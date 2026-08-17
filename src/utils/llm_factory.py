"""
LLM factory utils
"""

from typing import NamedTuple

from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI

from src.utils.config import (
    VALID_PROVIDERS,
    Config,
    load_config,
)


class _ProviderSpec(NamedTuple):
    """How to build one provider's client.

    Names rather than values: there is no Config at import time, so these are
    attributes to look up later.
    """

    cls: type[BaseChatModel]
    key_field: str  # Config attribute *and* constructor kwarg — same spelling
    model_field: str  # Config attribute; the kwarg is always plain "model"


_PROVIDERS = {
    "openai": _ProviderSpec(ChatOpenAI, "openai_api_key", "openai_model"),
    "gemini": _ProviderSpec(ChatGoogleGenerativeAI, "google_api_key", "gemini_model"),
    "groq": _ProviderSpec(ChatGroq, "groq_api_key", "groq_model"),
}


def get_llm(
    provider: str | None = None,
    model: str | None = None,
    timeout: float | None = None,
    config: Config | None = None,
) -> BaseChatModel:
    if config is None:
        config = load_config()

    provider = provider or config.llm_provider
    if provider not in _PROVIDERS:
        raise ValueError(
            f"provider = {provider} is unsupported. Choose one from {' | '.join(VALID_PROVIDERS)}."
        )

    spec = _PROVIDERS[provider]
    if model is None:
        model = getattr(config, spec.model_field)
    if timeout is None:
        timeout = config.llm_timeout_seconds

    return spec.cls(
        model=model,
        timeout=timeout,
        **{spec.key_field: getattr(config, spec.key_field)},
    )
