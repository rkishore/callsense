"""
Download the two public datasets into data/ as JSON, one row per line.

    uv run --no-project --with pyarrow python evals/public/fetch.py

pyarrow is needed only to read the parquet files, so it is not a project
dependency: uv runs this in a throwaway environment. data/ is gitignored; the
datasets are fetched from Hugging Face, not copied into the repo.
"""

import json
import urllib.request
from io import BytesIO
from pathlib import Path

import pyarrow.parquet as pq

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


def main() -> None:
    DATA.mkdir(exist_ok=True)
    for name, files in SOURCES.items():
        rows = []
        for split, url in files:
            with urllib.request.urlopen(url) as resp:
                table = pq.read_table(BytesIO(resp.read()))
            rows += [{"split": split, "idx": i, **r} for i, r in enumerate(table.to_pylist())]
        out = DATA / f"{name}.jsonl"
        out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        print(f"{name}: {len(rows)} rows -> {out}")


if __name__ == "__main__":
    main()
