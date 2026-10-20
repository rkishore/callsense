"""
MCP server — CallSense's guards, exposed as tools. Scope in docs/mcp-scope.md.

One tool so far: `scan_text`, the pipeline's stage 3 (injection detection) and
stage 4 (PII redaction) over arbitrary text. No Whisper, no LLM and no
database, so the server starts in well under a second and needs no keys.

stdio transport: the client launches this process and talks JSON-RPC over its
stdin/stdout. That is why get_logger writes to stderr — anything printed to
stdout here would be read as a malformed protocol message.

The package is `mcp_server` rather than `mcp` so it can never shadow the SDK.
"""

from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from src.security.injection_detector import detect_injection
from src.security.pii_redactor import detect_and_redact_pii

# Generous for a pasted transcript excerpt, small enough that the regexes stay
# fast. The pipeline has no such cap; its input is bounded by audio duration.
MAX_TEXT_CHARS = 20_000

Verdict = Literal["block", "review", "pass"]


class InjectionFindings(BaseModel):
    patterns_matched: list[str] = Field(description="Names of the regex patterns that matched")
    # Part of the contract now so it does not change when Jev lands. Null means
    # Jev was not asked: it is not wired yet, and once it is, a regex match
    # will still skip it.
    jev_p: float | None = Field(
        default=None, description="Jev's probability of injection, or null if Jev was not called"
    )


class PIIFindings(BaseModel):
    types: list[str] = Field(description="PII types found, e.g. CREDIT_CARD, SSN, EMAIL, PHONE")
    count: int = Field(description="Number of PII matches redacted")
    redacted_text: str = Field(description="The input with every match replaced by a placeholder")


class ScanTextResult(BaseModel):
    # "review" is unreachable until Jev's confidence gate exists. It is in the
    # schema now so clients see the full set of values from the start.
    verdict: Verdict = Field(description="block: injection found. pass: none found.")
    injection: InjectionFindings
    pii: PIIFindings


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
        # Pure local regex today. Flips to True when Jev, a third-party API, is called.
        openWorldHint=False,
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

    Returns verdict "block" if any of the 22 injection patterns match, otherwise "pass",
    together with the matched pattern names and the PII-redacted text.
    """
    # Same order as the pipeline: injection on the raw text, then redaction.
    injection = detect_injection(text)
    pii = detect_and_redact_pii(text)

    return ScanTextResult(
        verdict="block" if injection.injection_detected else "pass",
        injection=InjectionFindings(patterns_matched=injection.patterns_matched),
        pii=PIIFindings(types=pii.pii_types, count=pii.pii_count, redacted_text=pii.redacted_text),
    )


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
