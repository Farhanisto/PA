"""Groq model adapter via OpenAI-compatible chat completions."""

import os

DEFAULT_MODEL = "openai/gpt-oss-120b"
BASE_URL = "https://api.groq.com/openai/v1"


def create_groq_model():
    """Factory returning a ChatOpenAI client pointed at Groq.

    Raises RuntimeError with a clear message if required config is missing.
    Never instantiate globally at import time — call from app/worker scope.
    """
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("langchain-openai is not installed") from exc

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured")

    model_name = os.getenv("GROQ_MODEL", DEFAULT_MODEL)
    if not model_name:
        raise RuntimeError("GROQ_MODEL is not configured")

    base_url = os.getenv("GROQ_BASE_URL", BASE_URL)
    timeout = float(os.getenv("GROQ_TIMEOUT", "60"))
    max_tokens = int(os.getenv("GROQ_MAX_TOKENS", "2048"))

    return ChatOpenAI(
        model=model_name,
        base_url=base_url,
        api_key=api_key,
        temperature=0.2,
        timeout=timeout,
        max_retries=2,
        max_tokens=max_tokens,
    )
