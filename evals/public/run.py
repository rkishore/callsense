"""
Run scan_text, as deployed, over the public datasets fetched by fetch.py.

    .venv/bin/python evals/public/run.py
    .venv/bin/python evals/public/run.py --set harper_valley --split dev

Writes results/<utc>_<sha>_<set>.jsonl: line 1 is the run's metadata, then one
row per dataset row. The public set: deepset in full, both labels and every
language; NotInject only where it is English, since scan_text expects English
speech. harper_valley and aixblock: whole calls, all benign, split into dev
and test as evals/scan_text splits its cases, so test is scored once.
aixblock rows are written without their text (its licence forbids
redistribution). deepset_in_calls: each deepset injection inside one Harper
Valley call, to compare with deepset scanned alone.
"""

import argparse
import hashlib
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from evals.scan_text.run import git_state, split_of
from src.mcp_server import server
from src.mcp_server.jev import INJECTION_QUESTION_VERSION, JEV_MODEL

HERE = Path(__file__).parent
DATA = HERE / "data"
RESULTS = HERE / "results"


def load_items() -> list[dict]:
    """Every row to scan, with what it is: source, label, and where it came from."""
    items = []
    for line in (DATA / "deepset.jsonl").read_text().splitlines():
        r = json.loads(line)
        label = "injection" if r["label"] else "benign"
        items.append(
            {
                "source": "deepset",
                "split": r["split"],
                "idx": r["idx"],
                "label": label,
                "category": None,
                "text": r["text"],
            }
        )
    for line in (DATA / "notinject.jsonl").read_text().splitlines():
        r = json.loads(line)
        if r["category"] == "Multilingual" or not r["prompt"].isascii():
            continue
        items.append(
            {
                "source": "notinject",
                "split": r["split"],
                "idx": r["idx"],
                "label": "benign",
                "category": r["category"],
                "text": r["prompt"],
            }
        )
    return items


# Call corpora: file, id field, and what goes in category. All benign: Harper
# Valley is simulated bank tasks, AIxBlock real customer-service calls.
CALL_SETS = {
    "harper_valley": ("harper_valley.jsonl", "sid", lambda r: ",".join(r["tasks"])),
    "aixblock": ("aixblock.jsonl", "id", lambda r: r["domain"]),
}

# Sets whose text may not be redistributed (AIxBlock: CC-BY-NC, no
# redistribution). Their results rows keep the id and scores, never the text.
NO_TEXT_IN_RESULTS = {"aixblock"}


def load_calls(name: str, split: str) -> list[dict]:
    """The calls of one call corpus in one split."""
    filename, id_field, category = CALL_SETS[name]
    items = []
    for line in (DATA / filename).read_text().splitlines():
        r = json.loads(line)
        if split_of(r[id_field]) != split:
            continue
        items.append(
            {
                "source": name,
                "split": split,
                "idx": r[id_field],
                "label": "benign",
                "category": category(r),
                "text": r["text"],
            }
        )
    return items


POSITIONS = ("start", "middle", "end")


def pick(key: str, n: int) -> int:
    """A fixed choice among n, by hash of key: the same on every run."""
    return int(hashlib.sha256(key.encode()).hexdigest(), 16) % n


def insert(text: str, line: str, position: str) -> str:
    """The call with the line added as one more stretch of speech."""
    if position == "start":
        return f"{line} {text}"
    if position == "end":
        return f"{text} {line}"
    words = text.split()
    half = len(words) // 2
    return " ".join([*words[:half], line, *words[half:]])


def load_deepset_in_calls() -> list[dict]:
    """Each deepset injection inside one Harper Valley call, at one position.

    The call and the position are picked by hash of the row, so the same
    injection lands in the same place on every run. Harper Valley calls score
    0.02 alone (2026-11-04), so a flag comes from the injection, not the call.
    """
    calls = [json.loads(line) for line in (DATA / "harper_valley.jsonl").read_text().splitlines()]
    items = []
    for line in (DATA / "deepset.jsonl").read_text().splitlines():
        r = json.loads(line)
        if not r["label"]:
            continue
        key = f"{r['split']}-{r['idx']}"
        call = calls[pick(f"call:{key}", len(calls))]
        position = POSITIONS[pick(f"position:{key}", len(POSITIONS))]
        items.append(
            {
                "source": "deepset_in_calls",
                "split": r["split"],
                "idx": r["idx"],
                "label": "injection",
                "category": position,
                "call": call["sid"],
                "text": insert(call["text"], r["text"], position),
            }
        )
    return items


def scan(item: dict) -> dict:
    """scan_text on one row. Never raises: a failure becomes the row's error."""
    row = {
        **item,
        "verdict": None,
        "jev_p": None,
        "patterns_matched": None,
        "latency_ms": None,
        "error": None,
    }
    start = time.perf_counter()
    try:
        result = server.scan_text(item["text"])
    except Exception as e:
        row["error"] = f"{type(e).__name__}: {e}"
    else:
        row["verdict"] = result.verdict
        row["jev_p"] = result.injection.jev_p
        row["patterns_matched"] = result.injection.patterns_matched
    row["latency_ms"] = round((time.perf_counter() - start) * 1000, 2)
    if item["source"] in NO_TEXT_IN_RESULTS:
        del row["text"]
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="Run scan_text over a public dataset.")
    parser.add_argument(
        "--set", choices=["public", "deepset_in_calls", *CALL_SETS], default="public"
    )
    parser.add_argument("--split", choices=["dev", "test"], help="Required for the call sets")
    args = parser.parse_args()
    if (args.set in CALL_SETS) != (args.split is not None):
        parser.error("--split is required for the call sets, and only for them")

    logging.getLogger("typesafe_sdk").setLevel(logging.WARNING)
    if server.get_jev_client() is None:
        sys.exit("TYPESAFE_API_KEY is not set: these runs measure scan_text with Jev.")

    sha, dirty = git_state()
    meta = {
        "started_at": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "git_sha": sha,
        "git_dirty": dirty,
        "set": args.set,
        "split": args.split,
        "data_sha256": {
            f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(DATA.glob("*.jsonl"))
        },
        "jev_model": JEV_MODEL,
        "question_version": INJECTION_QUESTION_VERSION,
        "tau_review": server.TAU_REVIEW,
        "tau_block": server.TAU_BLOCK,
    }
    if args.set == "public":
        items = load_items()
    elif args.set == "deepset_in_calls":
        items = load_deepset_in_calls()
    else:
        items = load_calls(args.set, args.split)
    name = args.set if args.split is None else f"{args.set}_{args.split}"

    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"{meta['started_at']}_{sha}_{name}.jsonl"
    # Opened before any row runs, so a name clash fails before Jev calls are spent.
    with open(out, "x", encoding="utf-8") as f:
        f.write(json.dumps(meta) + "\n")
        for item in items:
            f.write(json.dumps(scan(item), ensure_ascii=False) + "\n")
    print(f"Wrote {len(items)} rows to {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
