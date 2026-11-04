import argparse
import contextlib
import hashlib
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from src.mcp_server import jev, server
from src.mcp_server.jev import INJECTION_QUESTION_VERSION, JEV_MODEL
from src.mcp_server.server import TAU_BLOCK, TAU_REVIEW, scan_text

HERE = Path(__file__).parent
CASES = HERE / "cases.jsonl"
RESULTS = HERE / "results"

# Must match the heading of LABELLING.md. Bump both together when the rule changes.
RULE_VERSION = "v0"

Label = Literal["injection", "benign", "ambiguous"]

# Closed on purpose: a typo such as "hard-negative" would otherwise become a
# category of its own and quietly skew the per-category recall. A new category
# is a one-line change here.
Category = Literal[
    "literal",
    "paraphrase",
    "reviewer",
    "extraction",
    "embedded",
    "benign",
    "hard_negative",
]

Source = Literal["hand", "whisper", "llm"]

# Dev is for tuning (thresholds, question wording); test is only scored to report.
# Changing this moves cases between the two, so treat it as fixed.
DEV_PERCENT = 70

Context = Literal["line", "call"]

# --context call puts each case inside one of these made-up calls, as one turn
# between the turns before and after. The pipeline scans a whole call's
# full_text, segments joined by spaces with no speaker labels, so that is the
# form used here. Benign, free of trigger words and PII. Changing them changes
# every call-context score, so treat them as fixed, like DEV_PERCENT.
CALLS = {
    "billing": (
        "Thanks for calling, this is Dana in billing. How can I help today? "
        "Hi Dana, my bill this month is a lot higher than usual. "
        "Let me take a look at that for you.",
        "Okay, I can see a one-time charge for the equipment upgrade. "
        "Oh, I didn't realise that was separate. "
        "It is, but I can split it over three months if that helps. "
        "Yes please, that would be great. Thanks for your help.",
    ),
    "tech_support": (
        "Hello, you're through to technical support, my name is Leo. "
        "Hi Leo, my internet keeps dropping out in the evenings. "
        "Sorry to hear that. Which lights are showing on the router right now?",
        "Thanks. I'm going to refresh the connection from our end. "
        "Okay, the light just went green. "
        "Great. If it drops again tonight, call back and we'll send an engineer. "
        "Will do, thanks.",
    ),
    "delivery": (
        "Good afternoon, deliveries team, Priya speaking. "
        "Hi, I'm calling about a parcel that was meant to arrive yesterday. "
        "I can check that. Do you have the order number to hand?",
        "Thank you. It looks like it was held at the depot overnight. "
        "So when will it come? "
        "It's out for delivery now and should reach you by six this evening. "
        "Perfect, thank you.",
    ),
}


def call_for(case_id: str) -> str:
    """Which made-up call a case goes into, the same on every run."""
    names = sorted(CALLS)
    return names[int(hashlib.sha256(f"call:{case_id}".encode()).hexdigest(), 16) % len(names)]


def in_call(case_id: str, text: str) -> str:
    """The case's text as one turn of its made-up call, as full_text would be."""
    before, after = CALLS[call_for(case_id)]
    return f"{before} {text} {after}"


class Case(BaseModel):
    """One row in cases.jsonl, validated"""

    # forbid: a misspelt field ("note") would otherwise be dropped silently.
    # frozen: nothing should change a case once it is loaded.
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z]+-\d{3}$", description="ID for the case, e.g. rev-003")
    text: str = Field(min_length=1, description="Text used for this case")
    label: Label = Field(description="Labelled by the rule in LABELLING.md")
    category: Category = Field(description="Category this case belongs to")
    source: Source = Field(description="Where the text came from")
    notes: str = Field(default="", description="Why the case is here, or why it is ambiguous")

    @model_validator(mode="after")
    def ambiguous_needs_a_note(self) -> "Case":
        """LABELLING.md: an ambiguous case must say which part of the rule failed."""
        if self.label == "ambiguous" and not self.notes.strip():
            raise ValueError(f"{self.id}: ambiguous cases need notes naming the part of the rule")
        return self


def load_cases(path: Path) -> list[Case]:
    """Read and validate every case in a JSONL file. Raises on the first bad line."""
    cases = []
    ids_seen_so_far = set()
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            if not line.strip():
                continue

            try:
                case = Case.model_validate_json(line)
            except ValidationError as e:
                raise ValueError(f"{path}:{lineno}: {e}") from e

            # Pydantic sees one row at a time, so only the loader can catch this.
            if case.id in ids_seen_so_far:
                raise ValueError(f"{path}:{lineno}: duplicate id: {case.id}")
            ids_seen_so_far.add(case.id)
            cases.append(case)

    return cases


def split_of(case_id: str) -> Literal["dev", "test"]:
    """Assign a case to dev or test by its id, the same way on every run.

    sha256, not hash(): Python salts hash() per process (PYTHONHASHSEED), so cases
    would move between dev and test from one run to the next.
    """
    bucket = int(hashlib.sha256(case_id.encode()).hexdigest(), 16) % 100
    return "dev" if bucket < DEV_PERCENT else "test"


def run_case(
    case: Case, repeat: int, jev_on_regex_hits: bool = False, context: Context = "line"
) -> dict:
    """Run scan_text on one case and record what happened.

    Never raises: a failure becomes the row's error, so one bad case cannot end a run.
    Whether the verdict is right is for the scorer to decide, not this function.

    context "call" scans the case inside its made-up call instead of alone. The
    label stays the case's: one injected turn makes the whole call an injection,
    as LABELLING.md says, and a benign turn in a benign call is benign.

    jev_on_regex_hits is an experiment, not a change to scan_text: when the regex
    blocked, Jev is asked anyway and its score kept in jev_p_shadow, so the
    "regex blocks first" ordering can be compared with "Jev decides". The verdict
    is still scan_text's.
    """
    result = {
        "id": case.id,
        "repeat": repeat,
        "label": case.label,
        "category": case.category,
        "verdict": None,
        "patterns_matched": None,
        "jev_p": None,
        "jev_p_shadow": None,
        "latency_ms": None,
        "error": None,
    }

    text = case.text if context == "line" else in_call(case.id, case.text)
    start = time.perf_counter()
    try:
        scan_text_result = scan_text(text)
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    else:
        result["verdict"] = scan_text_result.verdict
        result["patterns_matched"] = scan_text_result.injection.patterns_matched
        result["jev_p"] = scan_text_result.injection.jev_p

    result["latency_ms"] = round((time.perf_counter() - start) * 1000, 2)

    # After the timing, so latency stays scan_text's alone. Same redacted text
    # scan_text would have sent. A failure leaves the shadow score null rather
    # than marking the row an error: the verdict it records did not fail.
    if jev_on_regex_hits and result["patterns_matched"]:
        with contextlib.suppress(Exception):
            result["jev_p_shadow"] = jev.ask_jev(
                server.get_jev_client(), scan_text_result.pii.redacted_text
            )

    return result


def git_state() -> tuple[str, bool]:
    """The short SHA of HEAD, and whether tracked files have uncommitted changes.

    Untracked files are ignored: the audio recordings are never committed, so
    counting them would mark every run dirty.
    """

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=HERE, capture_output=True, text=True, check=True
        ).stdout.strip()

    return git("rev-parse", "--short", "HEAD"), bool(git("status", "--porcelain", "-uno"))


def call_controls(repeats: int) -> dict[str, list]:
    """Each made-up call scanned alone, with no case in it, `repeats` times.

    The baseline for --context call: what Jev scores the surrounding
    conversation. Kept in the metadata, not as rows, so the scorer never counts
    them as cases. A failed scan is recorded as its error type.
    """
    controls = {}
    for name in sorted(CALLS):
        scores = []
        for _ in range(repeats):
            try:
                result = scan_text(" ".join(CALLS[name]))
                scores.append({"verdict": result.verdict, "jev_p": result.injection.jev_p})
            except Exception as e:
                scores.append({"error": type(e).__name__})
        controls[name] = scores
    return controls


def run_metadata(
    cases_path: Path,
    split: str,
    repeats: int,
    jev_enabled: bool,
    jev_on_regex_hits: bool,
    context: Context = "line",
) -> dict:
    """Facts about the whole run, written once as the first line of the results file."""
    sha, dirty = git_state()
    return {
        "started_at": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "git_sha": sha,
        "git_dirty": dirty,
        "cases_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        "rule_version": RULE_VERSION,
        # Recorded either way, but they describe the run only when jev_enabled.
        "jev_enabled": jev_enabled,
        "jev_on_regex_hits": jev_on_regex_hits,
        "jev_model": JEV_MODEL,
        "question_version": INJECTION_QUESTION_VERSION,
        "tau_review": TAU_REVIEW,
        "tau_block": TAU_BLOCK,
        "split": split,
        "repeats": repeats,
        "context": context,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Run the scan_text eval cases and write one row per case per repeat."
    )

    parser.add_argument(
        "--cases", type=Path, default=CASES, help="Path to input jsonl file with cases to run"
    )

    parser.add_argument(
        "--split",
        choices=["dev", "test", "all"],
        default="dev",
        help="Split to use when running evals",
    )

    parser.add_argument("--repeats", type=int, default=1, help="Number of repeat runs")

    parser.add_argument(
        "--no-jev",
        action="store_true",
        help="Run regex-only, as the baseline was, even when TYPESAFE_API_KEY is set",
    )

    parser.add_argument(
        "--jev-on-regex-hits",
        action="store_true",
        help="Experiment: also ask Jev about texts the regex blocked (jev_p_shadow)",
    )

    parser.add_argument(
        "--context",
        choices=["line", "call"],
        default="line",
        help="Scan each case alone (line) or as one turn inside a made-up call (call)",
    )

    args = parser.parse_args()
    if args.no_jev and args.jev_on_regex_hits:
        parser.error("--jev-on-regex-hits needs Jev; it cannot be combined with --no-jev.")

    # The SDK logs every request at INFO, three lines each on stderr. Failures
    # are still recorded in the rows, so only its warnings are worth seeing.
    logging.getLogger("typesafe_sdk").setLevel(logging.WARNING)

    # Jev is on unless asked off, and a run that expected it but found no key
    # stops here. Otherwise it would quietly run regex-only under Jev's name.
    if args.no_jev:
        server.get_jev_client = lambda: None
    elif server.get_jev_client() is None:
        parser.error("TYPESAFE_API_KEY is not set. Set it in .env, or pass --no-jev.")

    print(f"Processing {args.cases}...", file=sys.stderr)

    # Load cases
    cases = load_cases(args.cases)

    # Split/Filter
    sample_cases = [
        case for case in cases if (args.split == "all" or split_of(case.id) == args.split)
    ]

    # Metadata first: started_at should be when the run began, not when it ended.
    meta = run_metadata(
        args.cases,
        args.split,
        args.repeats,
        jev_enabled=not args.no_jev,
        jev_on_regex_hits=args.jev_on_regex_hits,
        context=args.context,
    )

    # Line 1 is the run's metadata, every line after it is a row. "x" refuses to
    # overwrite, so an existing results file is never lost. The file is opened
    # before any case runs: a clash must fail before Jev calls are spent, not after.
    mode = "regex" if not meta["jev_enabled"] else "shadow" if args.jev_on_regex_hits else "jev"
    if args.context == "call":
        mode += "_call"
    out = RESULTS / f"{meta['started_at']}_{meta['git_sha']}_{args.split}_{mode}.jsonl"
    with open(out, "x", encoding="utf-8") as f:
        if args.context == "call":
            meta["call_controls"] = call_controls(args.repeats)
        f.write(json.dumps(meta) + "\n")
        rows = [
            run_case(case, repeat, args.jev_on_regex_hits, args.context)
            for case in sample_cases
            for repeat in range(args.repeats)
        ]
        for row in rows:
            f.write(json.dumps(row) + "\n")

    print(f"Wrote {len(rows)} rows to {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
