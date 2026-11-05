"""
Unit tests for evals/public/run.py: what a results row keeps.
"""

from evals.public import run


def test_a_row_from_a_no_redistribution_set_keeps_no_text(monkeypatch):
    def fail(text):
        raise TimeoutError("stop here")

    monkeypatch.setattr(run.server, "scan_text", fail)
    item = {"source": "aixblock", "split": "dev", "idx": "ab12", "label": "benign"}
    row = run.scan({**item, "category": "medicare_inbound", "text": "a real call"})

    assert "text" not in row
    assert row["idx"] == "ab12"
