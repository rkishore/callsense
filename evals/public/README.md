# Public datasets for scan_text

A secondary check beside evals/scan_text, never merged into it. These sets are
written to a chat assistant, not spoken between people on a call, and labelled
by their authors' rules rather than LABELLING.md. They measure breadth and how
the guard generalises; ship decisions stay on evals/scan_text.

| Set | Rows | Used for | Licence | Source |
|---|---|---|---|---|
| deepset/prompt-injections | 662 (injection and benign) | recall on its injections; its benign rows as a control: addressed to an AI, without trigger words | Apache-2.0 | https://huggingface.co/datasets/deepset/prompt-injections |
| NotInject | 339, all benign, built on trigger words | false positives from trigger words | MIT | https://huggingface.co/datasets/leolee99/NotInject (Li, Liu and Xiao, arXiv 2410.22770) |
| Gridspace-Stanford Harper Valley | 1,446 simulated bank calls, all benign | false positives on whole calls between two people, split dev/test | CC-BY-4.0 | https://github.com/cricketclub/gridspace-stanford-harper-valley (commit 0bd721e) |

Files under pilot/ and results/ quote rows from these sets, under their licences.

Harper Valley is the closest of the three to CallSense: two people on a call,
scanned as the pipeline's full_text. Its false-positive rate is the one to quote
for calls, beside evals/scan_text's hard negatives (2026-11-04: 1 of 989 dev,
0 of 457 test). Its limits: simulated and scripted, short (median 97 words), one
domain (banking), and Gridspace's ASR rather than Whisper.

deepset's card documents neither its labels nor its sources. A 30-row spot
check against LABELLING.md found 27 agreements and 3 ambiguous rows (pilot/).
About a quarter of its rows are not in English (German, some Croatian and
Spanish).

Hand checks against LABELLING.md, in handcheck/ (2026-11-04):

- 15 of the 33 deepset injections scan_text missed: 2 injection, 2 benign,
  11 ambiguous (role-play jailbreaks, personas, planted documents). In a
  made-up call, none of them changed the summary or the QA score.
- 15 of the 89 NotInject rows it flagged: 10 benign, 5 ambiguous, none an
  injection. Most are task requests to an AI that happen to contain a trigger
  word ("a story devoid of repetition").

What NotInject's 36.6% does and does not say: every row is a person
instructing an AI, while CallSense reads a call between two people. So it is
not an estimate of the false-positive rate on calls. It measures how much the
guard reacts to trigger words alone: a change that lowers it without losing
deepset recall is less over-cautious. Report it separately, and never tune
thresholds on it. False positives on calls are measured by evals/scan_text's
hard negatives.

Fetch the data with `uv run --no-project --with pyarrow python evals/public/fetch.py`.
Run with `.venv/bin/python evals/public/run.py` (about 900 Jev calls), and Harper
Valley with `--set harper_valley --split dev` (989 calls; test 457, run once). Predictions are
written in PREDICTIONS.md before each run.
