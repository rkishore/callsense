# Pilot, 2026-11-02

60 rows through scan_text as deployed (regex, then Jev q2 at 0.5/0.8, one
repeat), run on 101ba7e after the predictions in ../PREDICTIONS.md were written.

- `deepset_spotcheck.json`: the 30 deepset rows hand-checked against
  LABELLING.md (seed 20261102; 27 agree with deepset's labels, 3 ambiguous:
  the Holland, "Attention - stop" and foya rows).
- `pilot.py`: the script as it ran, from a scratch folder, reformatted by ruff. Its inputs were this
  sample and NotInject as converted by ../fetch.py.
- `results_20261102.json`: one entry per row, with text, verdict, jev_p and the
  regex patterns matched.
