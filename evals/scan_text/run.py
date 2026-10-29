import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

Label = Literal["injection", "benign", "ambiguous"]

# Closed on purpose: a typo such as "hard-negative" would otherwise become a
# category of its own and quietly skew the per-category recall. A new category
# is a one-line change here.
Category = Literal[
    "literal",
    "paraphrase",
    "reviewer",
    "extraction",
    "embedded",
    "benign",
    "hard_negative",
]

Source = Literal["hand", "whisper", "llm"]

# Dev is for tuning (thresholds, question wording); test is only scored to report.
# Changing this moves cases between the two, so treat it as fixed.
DEV_PERCENT = 70


class Case(BaseModel):
    """One row in cases.jsonl, validated"""

    # forbid: a misspelt field ("note") would otherwise be dropped silently.
    # frozen: nothing should change a case once it is loaded.
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z]+-\d{3}$", description="ID for the case, e.g. rev-003")
    text: str = Field(min_length=1, description="Text used for this case")
    label: Label = Field(description="Labelled by the rule in LABELLING.md")
    category: Category = Field(description="Category this case belongs to")
    source: Source = Field(description="Where the text came from")
    notes: str = Field(default="", description="Why the case is here, or why it is ambiguous")

    @model_validator(mode="after")
    def ambiguous_needs_a_note(self) -> "Case":
        """LABELLING.md: an ambiguous case must say which part of the rule failed."""
        if self.label == "ambiguous" and not self.notes.strip():
            raise ValueError(f"{self.id}: ambiguous cases need notes naming the part of the rule")
        return self


def load_cases(path: Path) -> list[Case]:
    """Read and validate every case in a JSONL file. Raises on the first bad line."""
    cases = []
    ids_seen_so_far = set()
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            if not line.strip():
                continue

            try:
                case = Case.model_validate_json(line)
            except ValidationError as e:
                raise ValueError(f"{path}:{lineno}: {e}") from e

            # Pydantic sees one row at a time, so only the loader can catch this.
            if case.id in ids_seen_so_far:
                raise ValueError(f"{path}:{lineno}: duplicate id: {case.id}")
            ids_seen_so_far.add(case.id)
            cases.append(case)

    return cases


def split_of(case_id: str) -> Literal["dev", "test"]:
    """Assign a case to dev or test by its id, the same way on every run.

    sha256, not hash(): Python salts hash() per process (PYTHONHASHSEED), so cases
    would move between dev and test from one run to the next.
    """
    bucket = int(hashlib.sha256(case_id.encode()).hexdigest(), 16) % 100
    return "dev" if bucket < DEV_PERCENT else "test"
