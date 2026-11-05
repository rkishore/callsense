"""
Does a subtle injection get lost in a long call? (A-011)

    .venv/bin/python evals/public/length.py

Puts each of evals/scan_text's eight injections that score lowest inside a
short call into three real AIxBlock calls (about 500, 2,000 and 10,000 words,
from dev), at the start, the middle or the end, and scans the result with
scan_text, 3 repeats. Each call is also scanned alone. 225 Jev calls.

Writes results/<utc>_<sha>_length.jsonl: ids, lengths, positions and scores,
never text (AIxBlock's licence forbids redistribution).
"""

import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from evals.scan_text.run import CASES, git_state, load_cases, split_of
from src.mcp_server import server
from src.mcp_server.jev import INJECTION_QUESTION_VERSION, JEV_MODEL

HERE = Path(__file__).parent
DATA = HERE / "data"
RESULTS = HERE / "results"

# Lowest mean score inside a made-up call, 2026-11-04 option 2 runs
# (evals/scan_text/results/20261104T2340*, 20261104T2341*): 0.56 to 0.83.
CASE_IDS = ["ext-002", "par-001", "rev-003", "par-008", "par-012", "par-009", "asr-001", "lit-001"]
TARGET_WORDS = [500, 2000, 10000]
POSITIONS = ["start", "middle", "end"]
REPEATS = 3


def pick_calls() -> list[dict]:
    """For each target length, the dev call closest to it (ties by id)."""
    calls = [json.loads(line) for line in (DATA / "aixblock.jsonl").read_text().splitlines()]
    calls = [c for c in calls if split_of(c["id"]) == "dev"]
    return [
        min(calls, key=lambda c, t=target: (abs(len(c["text"].split()) - t), c["id"]))
        for target in TARGET_WORDS
    ]


def insert(text: str, line: str, position: str) -> str:
    """The call with the line added as one more stretch of speech."""
    if position == "start":
        return f"{line} {text}"
    if position == "end":
        return f"{text} {line}"
    words = text.split()
    half = len(words) // 2
    return " ".join([*words[:half], line, *words[half:]])


def scan(text: str) -> dict:
    """scan_text's verdict and score. Never raises: a failure becomes the row's error."""
    try:
        result = server.scan_text(text)
    except Exception as e:
        return {"verdict": None, "jev_p": None, "error": type(e).__name__}
    return {"verdict": result.verdict, "jev_p": result.injection.jev_p, "error": None}


def main() -> None:
    logging.getLogger("typesafe_sdk").setLevel(logging.WARNING)
    if server.get_jev_client() is None:
        sys.exit("TYPESAFE_API_KEY is not set: this run measures scan_text with Jev.")

    cases = {c.id: c for c in load_cases(CASES)}
    calls = pick_calls()
    sha, dirty = git_state()
    meta = {
        "started_at": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "git_sha": sha,
        "git_dirty": dirty,
        "aixblock_sha256": hashlib.sha256((DATA / "aixblock.jsonl").read_bytes()).hexdigest(),
        "cases_sha256": hashlib.sha256(CASES.read_bytes()).hexdigest(),
        "jev_model": JEV_MODEL,
        "question_version": INJECTION_QUESTION_VERSION,
        "tau_review": server.TAU_REVIEW,
        "tau_block": server.TAU_BLOCK,
        "calls": [{"id": c["id"], "words": len(c["text"].split())} for c in calls],
    }

    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"{meta['started_at']}_{sha}_length.jsonl"
    with open(out, "x", encoding="utf-8") as f:
        f.write(json.dumps(meta) + "\n")
        for call in calls:
            words = len(call["text"].split())
            for repeat in range(REPEATS):
                row = {"case": None, "call": call["id"], "words": words, "position": None}
                f.write(json.dumps({**row, "repeat": repeat, **scan(call["text"])}) + "\n")
            for case_id in CASE_IDS:
                for position in POSITIONS:
                    text = insert(call["text"], cases[case_id].text, position)
                    for repeat in range(REPEATS):
                        row = {"case": case_id, "call": call["id"], "words": words}
                        row |= {"position": position, "repeat": repeat}
                        f.write(json.dumps({**row, **scan(text)}) + "\n")
    print(f"Wrote {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
