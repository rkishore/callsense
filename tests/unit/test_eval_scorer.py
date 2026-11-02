"""
Unit tests for the scan_text eval scorer in evals/scan_text/score.py.

Rows are made by hand, because the regex alone never produces a review, an
error or a hard negative it flags. Each test puts rows in known cells of the
confusion matrix and checks the rates that come out.
"""

import pytest

from evals.scan_text.score import read_results, score


def row(**overrides) -> dict:
    """A results row as run.py writes it: a benign case that passed."""
    return {
        "id": "ben-001",
        "repeat": 0,
        "label": "benign",
        "category": "benign",
        "verdict": "pass",
        "patterns_matched": [],
        "jev_p": None,
        "latency_ms": 0.1,
        "error": None,
        **overrides,
    }


def test_each_rate_counts_only_its_own_group():
    rows = [
        row(id="rev-001", label="injection", category="reviewer", verdict="block"),
        row(id="rev-002", label="injection", category="reviewer", verdict="review"),
        row(id="rev-003", label="injection", category="reviewer", verdict="pass"),
        row(id="ben-001", verdict="pass"),
        row(id="hn-001", category="hard_negative", verdict="review"),
    ]
    result = score(rows)

    # review counts as a catch, so two of the three injections were caught.
    assert result["recall"] == pytest.approx(2 / 3)
    # The flagged hard negative must not leak into the benign rate.
    assert result["fpr_benign"] == 0.0
    assert result["fpr_hard_negative"] == 1.0


def test_error_and_ambiguous_rows_are_left_out_and_listed_once():
    rows = [
        row(id="rev-001", label="injection", category="reviewer", verdict="block"),
        # Without the exclusion, this errored injection would count as a miss.
        row(id="rev-002", label="injection", verdict=None, error="TimeoutError: x"),
        row(id="rev-002", repeat=1, label="injection", verdict=None, error="TimeoutError: x"),
        row(id="amb-001", label="ambiguous", verdict="block"),
    ]
    result = score(rows)

    assert result["recall"] == 1.0
    assert result["fpr_benign"] is None
    assert result["error_ids"] == ["rev-002"]
    assert result["ambiguous_ids"] == ["amb-001"]


def test_a_group_with_no_rows_has_no_rate():
    result = score([])
    assert (result["recall"], result["fpr_benign"], result["fpr_hard_negative"]) == (None,) * 3


def test_a_file_without_run_metadata_is_refused(tmp_path):
    # e.g. cases.jsonl passed by mistake: line 1 is a case, not a run.
    path = tmp_path / "cases.jsonl"
    path.write_text('{"id": "lit-001", "text": "hi"}\n')
    with pytest.raises(ValueError, match="git_sha"):
        read_results(path)


def test_repeats_are_reported_per_case():
    # par-001 missed every time, par-002 caught two times in three, ben-003
    # flagged once in three; rev-001 flips between block and review, which
    # moves no score.
    rows = [
        *(row(id="par-001", label="injection", category="paraphrase", repeat=r) for r in range(3)),
        *(
            row(id="par-002", label="injection", category="paraphrase", repeat=r, verdict=v)
            for r, v in enumerate(["review", "pass", "review"])
        ),
        *(
            row(id="ben-003", repeat=r, verdict=v, jev_p=0.5)
            for r, v in enumerate(["review", "pass", "pass"])
        ),
        *(
            row(id="rev-001", label="injection", category="reviewer", repeat=r, verdict=v)
            for r, v in enumerate(["block", "review", "block"])
        ),
    ]
    result = score(rows)

    # A case caught only sometimes is still a miss: some scans let it through.
    assert result["misses"] == [
        {"id": "par-001", "positives": 0, "rows": 3},
        {"id": "par-002", "positives": 2, "rows": 3},
    ]
    assert result["false_positives"] == [{"id": "ben-003", "positives": 1, "rows": 3}]
    assert result["counts"]["benign"] == {
        "positives": 1,
        "rows": 3,
        "always": 0,
        "sometimes": 1,
        "never": 0,
    }
    assert result["recall_by_category"]["paraphrase"] == {
        "positives": 2,
        "rows": 6,
        "always": 0,
        "sometimes": 1,
        "never": 1,
    }
    assert result["recall_by_category"]["reviewer"]["always"] == 1
    assert [(c["id"], c["matters"]) for c in result["verdict_changes"]] == [
        ("ben-003", True),
        ("par-002", True),
        ("rev-001", False),
    ]
    assert result["positive_verdicts"] == {"block": 2, "review": 4}


def test_a_review_jev_never_answered_is_listed_as_jev_failed():
    rows = [
        row(id="rev-001", label="injection", category="reviewer", verdict="review", jev_p=None),
        row(id="rev-002", label="injection", category="reviewer", verdict="review", jev_p=0.7),
    ]
    result = score(rows)

    # Both count as catches, which is why the outage has to be visible beside them.
    assert result["recall"] == 1.0
    assert result["jev_failed_ids"] == ["rev-001"]
