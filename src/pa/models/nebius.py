"""Nebius (Nemotron) model adapter via OpenAI-compatible chat completions."""

import os


def create_nebius_model():
    """Factory returning a ChatOpenAI client for Nebius Token Factory.

    Raises RuntimeError with a clear message if required config is missing.
    Never instantiate globally at import time — call from app/worker scope.
    """
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("langchain-openai is not installed") from exc

    api_key = os.getenv("NEBIUS_API_KEY")
    if not api_key:
        raise RuntimeError("NEBIUS_API_KEY is not configured")

    model_name = os.getenv("NEBIUS_MODEL")
    if not model_name:
        raise RuntimeError("NEBIUS_MODEL is not configured")

    base_url = os.getenv("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/")
    timeout = float(os.getenv("NEBIUS_TIMEOUT", "60"))
    max_tokens = int(os.getenv("NEBIUS_MAX_TOKENS", "2048"))

    return ChatOpenAI(
        model=model_name,
        base_url=base_url,
        api_key=api_key,
        temperature=0.2,
        timeout=timeout,
        max_retries=2,
        max_tokens=max_tokens,
    )
