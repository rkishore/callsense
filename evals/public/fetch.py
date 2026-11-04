"""
Download the public datasets into data/ as JSON, one row per line.

    uv run --no-project --with pyarrow python evals/public/fetch.py

pyarrow is needed only to read the parquet files, so it is not a project
dependency: uv runs this in a throwaway environment. data/ is gitignored; the
datasets are fetched from Hugging Face and GitHub, not copied into the repo.
"""

import json
import subprocess
import urllib.request
from io import BytesIO
from pathlib import Path

DATA = Path(__file__).parent / "data"
HF = "https://huggingface.co/datasets"

DEEPSET = f"{HF}/deepset/prompt-injections/resolve/main/data"
NOTINJECT = f"{HF}/leolee99/NotInject/resolve/main/data"

SOURCES = {
    "deepset": [
        ("train", f"{DEEPSET}/train-00000-of-00001-9564e8b05b4757ab.parquet"),
        ("test", f"{DEEPSET}/test-00000-of-00001-701d16158af87368.parquet"),
    ],
    "notinject": [
        (f"NotInject_{n}", f"{NOTINJECT}/NotInject_{n}-00000-of-00001.parquet")
        for n in ("one", "two", "three")
    ],
}


# Gridspace-Stanford Harper Valley: simulated bank calls, CC-BY-4.0. Pinned to a
# commit, so the corpus cannot change under a results file. Transcripts and
# metadata only; the audio is not needed.
HARPER_VALLEY = "https://github.com/cricketclub/gridspace-stanford-harper-valley.git"
HARPER_VALLEY_COMMIT = "0bd721e877c4a85d8c13ff837e68661ea6200a98"


def harper_valley_calls(repo: Path) -> list[dict]:
    """One row per call: the machine transcript as the pipeline's full_text.

    The machine transcript, not the human one, because CallSense scans ASR
    output. Segments from both channels in time order, joined with a space and
    without speaker labels, as src/agents/transcription.py builds full_text.
    """
    calls = []
    for path in sorted((repo / "data" / "transcript").glob("*.json")):
        segments = sorted(json.loads(path.read_text()), key=lambda s: s["start_ms"])
        meta = json.loads((repo / "data" / "metadata" / path.name).read_text())
        calls.append(
            {
                "sid": meta["sid"],
                "tasks": [t.get("task_type") for t in meta.get("tasks", [])],
                "text": " ".join(s["transcript"] for s in segments if s["transcript"].strip()),
            }
        )
    return calls


def fetch_harper_valley() -> list[dict]:
    """Sparse-clone the pinned commit into data/harper-valley, then read its calls."""
    repo = DATA / "harper-valley"
    if not repo.exists():
        subprocess.run(
            ["git", "clone", "-q", "--filter=blob:none", "--sparse", HARPER_VALLEY, str(repo)],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "sparse-checkout", "set", "data/transcript", "data/metadata"],
            check=True,
        )
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", HARPER_VALLEY_COMMIT], check=True)
    return harper_valley_calls(repo)


def write_rows(name: str, rows: list[dict]) -> None:
    out = DATA / f"{name}.jsonl"
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    print(f"{name}: {len(rows)} rows -> {out}")


def main() -> None:
    # Here, not at the top, so tests can import this module without pyarrow.
    import pyarrow.parquet as pq

    DATA.mkdir(exist_ok=True)
    for name, files in SOURCES.items():
        rows = []
        for split, url in files:
            with urllib.request.urlopen(url) as resp:
                table = pq.read_table(BytesIO(resp.read()))
            rows += [{"split": split, "idx": i, **r} for i, r in enumerate(table.to_pylist())]
        write_rows(name, rows)
    write_rows("harper_valley", fetch_harper_valley())


if __name__ == "__main__":
    main()
