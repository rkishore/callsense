"""SPIKE — throwaway.
Spike to answer if each provider returns a nested Pydantic model through with_structured_output?
Makes real API calls.
"""

import sys
import time

sys.path.insert(0, "/Users/kishore.iyer/Code/ik/agentic-ai/callsense")

from src.graph.state import QAScoreResult
from src.utils.config import VALID_PROVIDERS
from src.utils.llm_factory import get_llm

if __name__ == "__main__":
    TRANSCRIPT = """[00:03] Agent: Thanks for calling Metro Bank, this is Dana.
    [00:07] Customer: Hi, I'm looking at my statement and there's a charge I don't recognise.
    [00:13] Agent: Sure, let me pull that up. I see a $340 charge from an electronics store on the 4th.
    [00:21] Customer: That wasn't me. I've never shopped there.
    [00:25] Agent: Okay, I've gone ahead and reversed it. You should see it in two days.
    [00:32] Customer: Don't you need to check who I am first?
    [00:35] Agent: It's fine, I can see your details right here. Anything else?
    [00:39] Customer: No, that's it. Thanks."""

    SYSTEM_PROMPT = "Score this call on five dimensions"

    for p in VALID_PROVIDERS:
        try:
            t = time.time()
            llm = get_llm(provider=p)
            structured = llm.with_structured_output(QAScoreResult)
            result = structured.invoke([("system", SYSTEM_PROMPT), ("human", TRANSCRIPT)])
            elapsed = time.time() - t
        except Exception as e:
            print(f"{p:7} error {type(e).__name__}: {str(e)[:200]}")
        else:
            dim = result.compliance
            print(
                f"{p:7} ok    {elapsed:5.2f}s  overall={result.overall_score}  "
                f"compliance={dim.score}  flags={len(result.compliance_flags)}"
            )
            print(f"        call_id: {result.call_id}")
            print(f"        justification: {dim.justification[:60]}")
