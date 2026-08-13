"""
Typed data contracts for the pipeline.

Every stage receives and returns one of these models, so they are the only thing
the stages know about each other. Two kinds live here:

- **Pydantic models** — validated contracts passed between stages. Field
  descriptions are not decoration: `SummaryResult` and `QAScoreResult` are used
  with `with_structured_output()`, so the LLM reads them as part of the schema.
- **`PipelineState`** — a `TypedDict(total=False)`, deliberately not a model.
  LangGraph merges a partial dict from each node into it.

This module must not import from anywhere else in the project.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TypedDict

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Base for all pipeline contracts. Unknown fields are an error."""

    model_config = ConfigDict(extra="forbid")


class AudioInput(StrictModel):
    """Represents the audio input for a call."""

    audio_data: bytes = Field(description="Raw audio file bytes")
    filename: str = Field(description="Name of the audio file")
    caller_id: str | None = Field(default=None, description="Optional caller ID")
    department: str | None = Field(default=None, description="Optional department")
    timestamp: str | None = Field(default=None, description="Optional timestamp of the call")


class AudioProperties(StrictModel):
    """Represents properties of the audio input."""

    duration_seconds: float = Field(description="Duration of the audio in seconds")
    size_bytes: int = Field(description="Size of the audio file in bytes")
    sample_rate: int = Field(description="Sample rate of the audio in Hz")
    channels: int = Field(description="Number of audio channels")
    format: str = Field(description="Audio format (e.g., 'wav', 'mp3')")


class PIIScanResult(StrictModel):
    """Represents the result of a PII scan on the audio input."""

    pii_detected: bool = Field(description="Whether PII was found in the audio")
    pii_types: list[str] = Field(description="List of PII types found")
    pii_count: int = Field(description="Count of PII instances found")


class IntakeResult(StrictModel):
    """Represents the result of the intake process for a call."""

    call_id: uuid.UUID = Field(description="Unique identifier for the call")
    validation_passed: bool = Field(description="Whether the intake validation passed")
    validation_error: str | None = Field(
        default=None,
        description="Error message if validation failed",
    )
    pii_scan: PIIScanResult = Field(description="Result of the PII scan")
    audio_properties: AudioProperties = Field(description="Properties of the audio input")
    temp_path: Path | None = Field(
        default=None,
        description="Temporary path where the audio file is stored",
    )


class SpeakerRole(StrEnum):
    """Enumeration of possible speaker roles."""

    AGENT = "agent"
    CUSTOMER = "customer"


class TranscriptionSegment(StrictModel):
    """Represents a segment of transcribed audio."""

    text: str = Field(description="Transcribed text of the segment")
    start_time: float = Field(description="Start time of the segment in seconds")
    end_time: float = Field(description="End time of the segment in seconds")
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence score of the transcription (0.0 to 1.0)"
    )
    speaker: SpeakerRole | None = Field(
        default=None, description="Optional speaker label for the segment"
    )


class TranscriptionResult(StrictModel):
    """Represents the result of the transcription process."""

    call_id: uuid.UUID = Field(description="Unique identifier for the call")
    full_text: str = Field(description="Full transcribed text of the call")
    segments: list[TranscriptionSegment] = Field(description="List of transcribed segments")

    low_confidence_ratio: float = Field(
        ge=0.0, le=1.0, description="Ratio of low-confidence segments (0.0 to 1.0)"
    )
    flagged_low_confidence: bool = Field(
        default=False,
        description="Transcript is unreliable; ratio exceeded the halt threshold",
    )


class ResolutionStatus(StrEnum):
    """Enumeration of possible resolution statuses for a call."""

    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    ESCALATED = "escalated"


class ActionItem(StrictModel):
    """Represents an action item extracted from the call."""

    description: str = Field(description="Description of the action item")
    owner: str | None = Field(default=None, description="Person assigned to the action item")
    deadline: str | None = Field(default=None, description="Due date for the action item")


class Entity(StrictModel):
    """Represents an entity extracted from the call."""

    name: str = Field(description="Name of the entity")
    type: str = Field(description="Type of the entity (e.g., person, organization)")


class SummaryResult(StrictModel):
    """Represents the summary of the call."""

    call_id: uuid.UUID = Field(description="Unique identifier for the call")
    call_purpose: str = Field(description="Purpose of the call")
    key_discussion_points: list[str] = Field(
        description="List of key discussion points from the call",
    )
    action_items: list[ActionItem] = Field(
        description="List of action items extracted from the call",
    )
    resolution_status: ResolutionStatus = Field(description="Overall resolution status of the call")
    sentiment_trajectory: str = Field(
        description="How sentiment moved across the call, e.g. 'Frustrated -> Satisfied'",
    )
    entities: list[Entity] = Field(description="List of entities extracted from the call")


class QADimensionScore(StrictModel):
    """Represents a score for a specific QA dimension."""

    score: int = Field(ge=1, le=5, description="Score for the QA dimension (1 to 5)")
    justification: str = Field(description="Justification for the score")


class SeverityLevel(StrEnum):
    """Enumeration of severity levels for compliance violations."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ComplianceFlag(StrictModel):
    """Represents a compliance violation flag."""

    violation_description: str = Field(description="Description of the compliance violation")
    severity: SeverityLevel = Field(
        description="Severity level of the violation (e.g., 'low', 'medium', 'high', 'critical')",
    )
    transcript_timestamp: float = Field(
        description="Timestamp in the transcript where the violation occurred",
    )


class QAScoreResult(StrictModel):
    """Represents the overall QA score result for a call."""

    call_id: uuid.UUID = Field(description="Unique identifier for the call")
    professionalism: QADimensionScore = Field(description="Score for professionalism dimension")
    empathy: QADimensionScore = Field(description="Score for empathy dimension")
    problem_resolution: QADimensionScore = Field(
        description="Score for problem resolution dimension",
    )
    compliance: QADimensionScore = Field(description="Score for compliance dimension")
    communication_clarity: QADimensionScore = Field(
        description="Score for communication clarity dimension",
    )
    overall_score: float = Field(
        ge=1.0, le=5.0, description="Weighted overall QA score (1.0 to 5.0)"
    )
    compliance_flags: list[ComplianceFlag] = Field(description="List of compliance violation flags")


class CallStatus(StrEnum):
    """Enumeration of possible statuses for a call."""

    COMPLETED = "completed"
    FAILED = "failed"
    FLAGGED_FOR_REVIEW = "flagged_for_review"


class CallReport(StrictModel):
    """Represents the final report for a call, including summary and QA scores."""

    call_id: uuid.UUID = Field(description="Unique identifier for the call")
    audio_filename: str = Field(description="Name of the audio file")
    transcription_result: TranscriptionResult = Field(
        description="Result of the transcription process",
    )
    summary: SummaryResult = Field(description="Summary of the call")
    qa_scores: QAScoreResult = Field(description="QA scores for the call")
    status: CallStatus = Field(
        description="Status of the report generation",
    )
    processed_at: datetime = Field(description="Timestamp when the report was processed")
    trace_id: str | None = Field(default=None, description="Optional trace ID for debugging")


class InjectionScanResult(StrictModel):
    """Represents the injection scan result for the full text from a transcript"""

    injection_detected: bool = Field(description="Was an injection pattern detected?")
    patterns_matched: list[str] = Field(description="Injection patterns detected")


class PipelineState(TypedDict, total=False):
    """Top-level state that flows through the entire graph."""

    # Audio input
    audio_input: AudioInput

    # Intake
    intake: IntakeResult

    # Transcription
    transcription: TranscriptionResult

    # Summary
    summary: SummaryResult

    # QA Scoring
    qa_scores: QAScoreResult

    # Final report
    report: CallReport

    # Error handling
    error: str

    # Status
    status: CallStatus

    # Injection pattern detection
    injection_scan: InjectionScanResult
