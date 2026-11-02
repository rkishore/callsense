"""
Run scan_text, as deployed, over the public datasets fetched by fetch.py.

    .venv/bin/python evals/public/run.py

Writes results/<utc>_<sha>_public.jsonl: line 1 is the run's metadata, then one
row per dataset row. deepset is run in full, both labels and every language;
NotInject only where it is English, since scan_text expects English speech.
"""

import hashlib
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from evals.scan_text.run import git_state
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
    return row


def main() -> None:
    logging.getLogger("typesafe_sdk").setLevel(logging.WARNING)
    if server.get_jev_client() is None:
        sys.exit("TYPESAFE_API_KEY is not set: these runs measure scan_text with Jev.")

    sha, dirty = git_state()
    meta = {
        "started_at": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "git_sha": sha,
        "git_dirty": dirty,
        "data_sha256": {
            f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(DATA.glob("*.jsonl"))
        },
        "jev_model": JEV_MODEL,
        "question_version": INJECTION_QUESTION_VERSION,
        "tau_review": server.TAU_REVIEW,
        "tau_block": server.TAU_BLOCK,
    }
    items = load_items()

    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"{meta['started_at']}_{sha}_public.jsonl"
    # Opened before any row runs, so a name clash fails before Jev calls are spent.
    with open(out, "x", encoding="utf-8") as f:
        f.write(json.dumps(meta) + "\n")
        for item in items:
            f.write(json.dumps(scan(item), ensure_ascii=False) + "\n")
    print(f"Wrote {len(items)} rows to {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
