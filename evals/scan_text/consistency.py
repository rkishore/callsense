"""
Are Jev's repeats independent draws, or could a cache be answering some of them?

TypeSafe's consistency cookbook puts a random uid in every request's state "so
no cache along the way can hand back an earlier answer". run.py never did, so
an identical score across repeats could be a cached reply rather than the same
draw. Each case here is asked REPEATS times as scan_text asks it (no uid) and
REPEATS times with a fresh uid, interleaved so drift over the run hits both
alike.

If a cache were answering, the no-uid repeats would be identical where the uid
ones vary. The uid also changes the input, so a shift in the mean between the
two is the uid's effect on the question, not noise.

    .venv/bin/python evals/scan_text/consistency.py
"""

import json
import logging
import random
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

from typesafe_sdk import Noul

from evals.scan_text.run import CASES, git_state, load_cases
from src.mcp_server import jev, server
from src.security.pii_redactor import detect_and_redact_pii

RESULTS = Path(__file__).parent / "results"

# Chosen from the 2026-11-04 runs: four whose three repeats were identical, at
# both ends and in the middle (par-002), and four that varied, three of them
# within 0.05 of a threshold.
CASE_IDS = ["hn-006", "asr-014", "par-002", "asr-001", "asr-010", "hn-018", "ben-003", "par-006"]
REPEATS = 5


def ask(client, text: str, uid: str | None) -> float:
    """jev.ask_jev, with the uid added to the state when given."""
    state = {jev.STATE_KEY: text} if uid is None else {"uid": uid, jev.STATE_KEY: text}
    resp = client.system_one(
        state=state, questions={"is_injection": Noul(**jev.INJECTION_QUESTION)}
    )
    return resp.nouls["is_injection"].noul


def main():
    logging.getLogger("typesafe_sdk").setLevel(logging.WARNING)
    client = server.get_jev_client()
    if client is None:
        sys.exit("TYPESAFE_API_KEY is not set.")

    cases = {c.id: c for c in load_cases(CASES)}
    texts = {i: detect_and_redact_pii(cases[i].text).redacted_text for i in CASE_IDS}

    sha, dirty = git_state()
    started = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    meta = {
        "started_at": started,
        "git_sha": sha,
        "git_dirty": dirty,
        "jev_model": jev.JEV_MODEL,
        "question_version": jev.INJECTION_QUESTION_VERSION,
        "case_ids": CASE_IDS,
        "repeats": REPEATS,
    }

    out = RESULTS / f"{started}_{sha}_consistency.jsonl"
    with open(out, "x", encoding="utf-8") as f:
        f.write(json.dumps(meta) + "\n")
        for repeat in range(REPEATS):
            calls = [(i, use_uid) for i in CASE_IDS for use_uid in (False, True)]
            random.shuffle(calls)
            for case_id, use_uid in calls:
                uid = secrets.token_hex(4) if use_uid else None
                row = {"id": case_id, "repeat": repeat, "uid": uid, "jev_p": None, "error": None}
                try:
                    row["jev_p"] = ask(client, texts[case_id], uid)
                except Exception as e:
                    row["error"] = type(e).__name__
                f.write(json.dumps(row) + "\n")
    print(f"Wrote {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
