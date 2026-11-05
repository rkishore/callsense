"""
Unit tests for evals/public/run.py: what a results row keeps.
"""

import pytest

from evals.public import run


def test_a_row_from_a_no_redistribution_set_keeps_no_text(monkeypatch):
    def fail(text):
        raise TimeoutError("stop here")

    monkeypatch.setattr(run.server, "scan_text", fail)
    item = {"source": "aixblock", "split": "dev", "idx": "ab12", "label": "benign"}
    row = run.scan({**item, "category": "medicare_inbound", "text": "a real call"})

    assert "text" not in row
    assert row["idx"] == "ab12"


@pytest.mark.skipif(
    not (run.DATA / "deepset.jsonl").exists() or not (run.DATA / "harper_valley.jsonl").exists(),
    reason="needs evals/public/fetch.py run first; data/ is gitignored",
)
def test_each_deepset_injection_lands_in_the_same_call_and_place_every_run():
    first, second = run.load_deepset_in_calls(), run.load_deepset_in_calls()

    assert first == second
    assert len(first) == 263
    assert {item["category"] for item in first} == set(run.POSITIONS)


def test_insert_puts_the_line_at_a_word_boundary():
    assert run.insert("a b c d", "X", "start") == "X a b c d"
    assert run.insert("a b c d", "X", "middle") == "a b X c d"
    assert run.insert("a b c d", "X", "end") == "a b c d X"
