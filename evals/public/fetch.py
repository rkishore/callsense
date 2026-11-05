"""
Download the public datasets into data/ as JSON, one row per line.

    uv run --no-project --with pyarrow python evals/public/fetch.py

pyarrow is needed only to read the parquet files, so it is not a project
dependency: uv runs this in a throwaway environment. data/ is gitignored; the
datasets are fetched from Hugging Face and GitHub, not copied into the repo.
"""

import hashlib
import json
import subprocess
import urllib.parse
import urllib.request
import zipfile
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


# AIxBlock 92k real-world call-centre transcripts: CC-BY-NC-4.0, research only,
# no redistribution. Only ids, domains and scores are committed, never text.
# Pinned to a revision. About 1.4 GB of zips, kept in data/aixblock.
AIXBLOCK = "https://huggingface.co/datasets/AIxBlock/92k-real-world-call-center-scripts-english"
AIXBLOCK_REVISION = "9d40ca13f3e173ad815c89bb8fff144be3ae3146"
# Zip -> domain. The two re-uploads repeat calls from the zips they replace, so
# they share a domain and duplicate texts are dropped.
AIXBLOCK_ZIPS = {
    "(re-uploaded)PII_Redacted_Transcripts_aixblock-automotive-stereo-inbound-104h.zip": (
        "automotive_inbound"
    ),
    "(reupload)PII_redacted_auto_insurance_script.zip": "auto_insurance_inbound",
    "auto_insurance_customer_service_inbound.zip": "auto_insurance_inbound",
    "automotive_and_healthcare_insurance_inbound.zip": "auto_and_health_insurance_inbound",
    "automotive_inbound.zip": "automotive_inbound",
    "customer_service_general_inbound.zip": "customer_service_general_inbound",
    "home_ervice_inbound&telecom _outbound.zip": "home_service_inbound_and_telecom_outbound",
    "home_service_inbound.zip": "home_service_inbound",
    "insurance_outbound.zip": "insurance_outbound",
    "medical_equipment_outbound.zip": "medical_equipment_outbound",
    "medicare_inbound.zip": "medicare_inbound",
}
# Calls drawn per domain. Medicare is 64% of the corpus; an equal draw keeps it
# from setting the rate alone. Rates are also reported weighted by domain size.
AIXBLOCK_PER_DOMAIN = 225


def aixblock_id(zip_name: str, member: str) -> str:
    """A call's id: a hash of where it is stored. File names hold what look like
    phone numbers, so they never leave data/."""
    return hashlib.sha256(f"{zip_name}/{member}".encode()).hexdigest()[:16]


def aixblock_calls(folder: Path) -> tuple[list[dict], dict[str, int]]:
    """The sample, and how many distinct calls each domain has.

    A call's text is AIxBlock's "text" field: the whole call, no speaker labels,
    already PII-redacted by them. Duplicates (same text) and empty or unreadable
    files are dropped. Within a domain, calls are ordered by id, a hash, and the
    first AIXBLOCK_PER_DOMAIN taken: a fixed draw, not a convenient one.
    """
    by_domain: dict[str, list[dict]] = {}
    seen: set[str] = set()
    for zip_name, domain in AIXBLOCK_ZIPS.items():
        with zipfile.ZipFile(folder / zip_name) as zf:
            for member in sorted(zf.namelist()):
                if not member.endswith(".json") or member.startswith("__MACOSX") or "/._" in member:
                    continue
                try:
                    text = json.loads(zf.read(member)).get("text") or ""
                except (ValueError, AttributeError):
                    continue
                digest = hashlib.sha256(text.encode()).hexdigest()
                if not text.strip() or digest in seen:
                    continue
                seen.add(digest)
                by_domain.setdefault(domain, []).append(
                    {"id": aixblock_id(zip_name, member), "domain": domain, "text": text}
                )
    sample = [
        call
        for calls in by_domain.values()
        for call in sorted(calls, key=lambda c: c["id"])[:AIXBLOCK_PER_DOMAIN]
    ]
    return sample, {domain: len(calls) for domain, calls in sorted(by_domain.items())}


def fetch_aixblock() -> list[dict]:
    """Download the zips not already in data/aixblock, then draw the sample."""
    folder = DATA / "aixblock"
    folder.mkdir(exist_ok=True)
    for zip_name in AIXBLOCK_ZIPS:
        if not (folder / zip_name).exists():
            url = f"{AIXBLOCK}/resolve/{AIXBLOCK_REVISION}/{urllib.parse.quote(zip_name)}"
            urllib.request.urlretrieve(url, folder / zip_name)
    sample, sizes = aixblock_calls(folder)
    (DATA / "aixblock_domains.json").write_text(json.dumps(sizes, indent=1) + "\n")
    return sample


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
    write_rows("aixblock", fetch_aixblock())


if __name__ == "__main__":
    main()
