"""
MCP server — CallSense's guards, exposed as tools. Scope in docs/mcp-scope.md.

One tool so far: `scan_text`, the pipeline's stage 3 (injection detection) and
stage 4 (PII redaction) over arbitrary text, with Jev as a second injection
check on what the regexes let through. No Whisper, no pipeline LLM and no
database, so the server starts in well under a second. TYPESAFE_API_KEY is
optional: without it, scan_text runs regex-only.

stdio transport: the client launches this process and talks JSON-RPC over its
stdin/stdout. That is why get_logger writes to stderr — anything printed to
stdout here would be read as a malformed protocol message.

The package is `mcp_server` rather than `mcp` so it can never shadow the SDK.
"""

from functools import cache
from pathlib import Path
from typing import Annotated, Literal

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from src.mcp_server import jev
from src.security.injection_detector import detect_injection
from src.security.pii_redactor import detect_and_redact_pii
from src.utils.config import get_logger

logger = get_logger("mcp_server")

# The repo's .env, found from this file rather than the working directory. A
# host may launch the server from anywhere (Claude Desktop starts it in / or
# ~), and src/utils/config.py searches upward from the working directory, so
# from there it would find no .env and Jev would quietly be off. Variables
# already set in the host's environment win: load_dotenv does not override.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# Jev's score at or above which a text goes to a human, and at or above which it
# is blocked outright. Provisional, from the spike's five probes (benign peaked
# near 0.36, injection bottomed near 0.56), until the eval tunes them on dev.
TAU_REVIEW = 0.5
TAU_BLOCK = 0.8

# Generous for a pasted transcript excerpt, small enough that the regexes stay
# fast. The pipeline has no such cap; its input is bounded by audio duration.
MAX_TEXT_CHARS = 20_000

Verdict = Literal["block", "review", "pass"]


class InjectionFindings(BaseModel):
    patterns_matched: list[str] = Field(description="Names of the regex patterns that matched")
    # Null with verdict block: the regex matched, so Jev was not asked. Null with
    # review: Jev failed, and scan_text failed closed. Null with pass: no
    # TYPESAFE_API_KEY, so the scan was regex-only.
    jev_p: float | None = Field(
        default=None,
        description="Jev's injection score from 0 to 1, or null if Jev was not asked or failed",
    )


class PIIFindings(BaseModel):
    types: list[str] = Field(description="PII types found, e.g. CREDIT_CARD, SSN, EMAIL, PHONE")
    count: int = Field(description="Number of PII matches redacted")
    redacted_text: str = Field(description="The input with every match replaced by a placeholder")


class ScanTextResult(BaseModel):
    verdict: Verdict = Field(
        description=(
            "block: injection found. review: possible injection, or Jev could not be "
            "reached; a human should look. pass: none found."
        )
    )
    injection: InjectionFindings
    pii: PIIFindings


@cache
def get_jev_client() -> jev.TypeSafeClient | None:
    """The one Jev client for this process, made on first use.

    Not at import: tests and tools import this module without wanting a client.
    Cached, so the connection pool is reused across scan_text calls. Tests
    replace this function to hand scan_text a fake client.
    """
    client = jev.make_client()
    if client is None:
        logger.warning("TYPESAFE_API_KEY is not set: scan_text runs regex-only, without Jev")
    return client


def verdict_for(jev_p: float) -> Verdict:
    """Turn Jev's score into a verdict, using the two thresholds."""
    if jev_p >= TAU_BLOCK:
        return "block"
    if jev_p >= TAU_REVIEW:
        return "review"
    return "pass"


server = MCPServer(
    name="callsense",
    instructions=(
        "CallSense's input guards for call-centre text. scan_text checks text for prompt "
        "injection and redacts PII (SSN, credit card, email, phone) using the same rules "
        "the CallSense pipeline applies before any LLM sees a transcript."
    ),
)


@server.tool(
    annotations=ToolAnnotations(
        # No idempotentHint: the spec gives it meaning only when readOnlyHint is false.
        readOnlyHint=True,
        # Jev is a third-party API, reached for any text the regexes do not match.
        openWorldHint=True,
    ),
)
def scan_text(
    text: Annotated[
        str,
        Field(
            min_length=1,
            max_length=MAX_TEXT_CHARS,
            description="Text to scan, e.g. a transcript or an excerpt of one",
        ),
    ],
) -> ScanTextResult:
    """Check text for prompt injection and redact PII, as CallSense does before any LLM call.

    Returns verdict "block" if any of the 22 injection patterns match. Otherwise
    Jev scores the PII-redacted text: "block" or "review" above its thresholds,
    else "pass". If Jev cannot be reached the verdict is "review", never "pass".
    Also returns the matched pattern names, Jev's score and the redacted text.
    """
    # Same order as the pipeline: injection on the raw text, then redaction.
    injection = detect_injection(text)
    pii = detect_and_redact_pii(text)

    jev_p = None
    if injection.injection_detected:
        # Jev is not asked: the call would cost money and send attacker-controlled
        # text to a third party, only to confirm what the regex already found.
        verdict = "block"
    elif (client := get_jev_client()) is None:
        verdict = "pass"
    else:
        try:
            # Redacted text only: Jev is a provider, and no provider sees raw PII.
            jev_p = jev.ask_jev(client, pii.redacted_text)
        except Exception as e:
            # Fail closed. The type only: the message could quote the text.
            logger.warning("Jev failed (%s): verdict is review", type(e).__name__)
            verdict = "review"
        else:
            verdict = verdict_for(jev_p)

    return ScanTextResult(
        verdict=verdict,
        injection=InjectionFindings(patterns_matched=injection.patterns_matched, jev_p=jev_p),
        pii=PIIFindings(types=pii.pii_types, count=pii.pii_count, redacted_text=pii.redacted_text),
    )


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
