"""Model provider factory with auto-detect.

Selection rule: GROQ_API_KEY set → Groq, otherwise Nebius (which raises a
clear error when unconfigured). When both providers are configured, Groq
takes precedence; unset GROQ_API_KEY to use Nebius. Only the provider name
is ever logged — never keys, models carry no secrets.
"""

import logging
import os

logger = logging.getLogger(__name__)


def resolve_provider() -> str:
    """Return 'groq' or 'nebius' without instantiating any client."""
    if os.getenv("GROQ_API_KEY"):
        return "groq"
    return "nebius"


def create_model():
    """Create the configured chat model client (app/worker scope only)."""
    provider = resolve_provider()
    logger.info("model provider resolved: %s", provider)
    if provider == "groq":
        from pa.models.groq import create_groq_model

        return create_groq_model()
    from pa.models.nebius import create_nebius_model

    return create_nebius_model()
