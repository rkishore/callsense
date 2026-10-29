import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from src.mcp_server.jev import INJECTION_QUESTION_VERSION, JEV_MODEL
from src.mcp_server.server import scan_text

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


def run_case(case: Case, repeat: int) -> dict:
    """Run scan_text on one case and record what happened.

    Never raises: a failure becomes the row's error, so one bad case cannot end a run.
    Whether the verdict is right is for the scorer to decide, not this function.
    """
    result = {
        "id": case.id,
        "repeat": repeat,
        "label": case.label,
        "category": case.category,
        "verdict": None,
        "patterns_matched": None,
        "jev_p": None,
        "latency_ms": None,
        "error": None,
    }

    start = time.perf_counter()
    try:
        scan_text_result = scan_text(case.text)
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    else:
        result["verdict"] = scan_text_result.verdict
        result["patterns_matched"] = scan_text_result.injection.patterns_matched
        result["jev_p"] = scan_text_result.injection.jev_p

    result["latency_ms"] = round((time.perf_counter() - start) * 1000, 2)

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


def run_metadata(cases_path: Path, split: str, repeats: int) -> dict:
    """Facts about the whole run, written once as the first line of the results file."""
    sha, dirty = git_state()
    return {
        "started_at": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "git_sha": sha,
        "git_dirty": dirty,
        "cases_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        "rule_version": RULE_VERSION,
        "jev_model": JEV_MODEL,
        "question_version": INJECTION_QUESTION_VERSION,
        "split": split,
        "repeats": repeats,
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

    args = parser.parse_args()

    print(f"Processing {args.cases}...", file=sys.stderr)

    # Load cases
    cases = load_cases(args.cases)

    # Split/Filter
    sample_cases = [
        case for case in cases if (args.split == "all" or split_of(case.id) == args.split)
    ]

    # Metadata first: started_at should be when the run began, not when it ended.
    meta = run_metadata(args.cases, args.split, args.repeats)

    # Run cases
    rows = [run_case(case, repeat) for case in sample_cases for repeat in range(args.repeats)]

    # Write results: line 1 is the run's metadata, every line after it is a row.
    # "x" refuses to overwrite, so an existing results file is never lost.
    out = RESULTS / f"{meta['started_at']}_{meta['git_sha']}_{args.split}.jsonl"
    with open(out, "x", encoding="utf-8") as f:
        f.write(json.dumps(meta) + "\n")
        for row in rows:
            f.write(json.dumps(row) + "\n")

    print(f"Wrote {len(rows)} rows to {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
