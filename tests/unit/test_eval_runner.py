"""
Unit tests for the scan_text eval runner in evals/scan_text/run.py.

Only the parts every score depends on: loading the cases, splitting them, and
running one without letting a failure end the run.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from evals.scan_text import run
from evals.scan_text.run import Case, load_cases, run_case, split_of

REPO = Path(__file__).resolve().parents[2]
CASES = REPO / "evals" / "scan_text" / "cases.jsonl"

ROW = '{"id": "lit-001", "text": "hi", "label": "benign", "category": "benign", "source": "hand"}'


def test_the_real_cases_file_loads():
    # Guards edits to cases.jsonl: one bad row fails here, not halfway through a run.
    assert load_cases(CASES)


def test_duplicate_id_raises_with_its_line_number(tmp_path):
    # The blank line is skipped but still counted, so the duplicate is on line 3.
    path = tmp_path / "cases.jsonl"
    path.write_text(f"{ROW}\n\n{ROW}\n")
    with pytest.raises(ValueError, match=r":3: duplicate id: lit-001"):
        load_cases(path)


def test_invalid_row_raises_with_its_line_number(tmp_path):
    path = tmp_path / "cases.jsonl"
    path.write_text(f"{ROW}\n{ROW.replace('benign', 'benigm', 1)}\n")
    # Pydantic names the bad field on the next line, hence (?s).
    with pytest.raises(ValueError, match=r"(?s):2: .*label"):
        load_cases(path)


def test_split_is_pinned():
    # If the hash or DEV_PERCENT changes, cases move between dev and test, and
    # anything tuned on dev may have seen today's test cases. Fail loudly instead.
    assert split_of("emb-001") == "test"
    assert split_of("rev-002") == "dev"


def test_split_is_the_same_under_different_hash_seeds():
    # Catches a switch to hash(), which Python salts per process. Fifty ids, so a
    # salted hash cannot agree across the two seeds by luck.
    code = (
        "from evals.scan_text.run import split_of; "
        "print([split_of(f'x-{i:03}') for i in range(50)])"
    )

    def splits(seed: str) -> str:
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=REPO,
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    assert splits("1") == splits("2")


def test_call_context_scans_the_case_inside_its_call(monkeypatch):
    # The same case must land in the same call every run, or line and call
    # scores could not be compared across runs.
    case = Case.model_validate_json(ROW)
    scanned = []

    def record(text):
        scanned.append(text)
        raise TimeoutError("stop here")

    monkeypatch.setattr(run, "scan_text", record)
    run_case(case, repeat=0, context="line")
    run_case(case, repeat=0, context="call")
    run_case(case, repeat=1, context="call")

    before, after = run.CALLS[run.call_for("lit-001")]
    assert scanned == ["hi", f"{before} hi {after}", f"{before} hi {after}"]


def test_a_failing_scan_becomes_an_error_row(monkeypatch):
    # The row must keep its shape, so the scorer never checks for missing keys.
    case = Case.model_validate_json(ROW)
    ok = run_case(case, repeat=0)

    def boom(text):
        raise TimeoutError("jev took too long")

    # Patched where run_case looks it up, not in server.py.
    monkeypatch.setattr(run, "scan_text", boom)
    failed = run_case(case, repeat=0)

    assert failed.keys() == ok.keys()
    assert failed["verdict"] is None
    assert failed["error"] == "TimeoutError: jev took too long"
