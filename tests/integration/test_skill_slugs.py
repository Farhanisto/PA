"""The skills must name tools that actually exist.

A skill is injected into the assistant's prompt, so a slug it names is a slug
the model will try to call. These were written from memory and several were
wrong (``HUBSPOT_GET_PIPELINE_STAGES`` is really
``HUBSPOT_RETRIEVE_PIPELINE_STAGES``), which is exactly the kind of drift
this test exists to catch.

It reaches the Composio catalog, so it is skipped when the catalog is not
reachable — the rest of the suite must still run offline.
"""

import glob
import re

import pytest

# A tool slug is an UPPER_SNAKE identifier with a toolkit prefix.
_SLUG_RE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")

# Slugs that are not Composio tools: HTTP paths and PA's own endpoints.
_NOT_TOOLS = re.compile(r"^(GET|POST|PUT|DELETE|PATCH|PA)_[A-Z_]*$")


@pytest.fixture()
def real_catalog(monkeypatch):
    """Use the real Composio catalog, not the test double.

    The test environment replaces ``COMPOSIO_API_KEY`` with a fake, and
    environment variables take precedence over the file, so the file has to
    be read directly.
    """
    from pa.config import reset_settings_cache
    from pa.integrations import composio_client

    real_key = _real_composio_key()
    if not real_key:
        pytest.skip("no real Composio key is available")

    factory = composio_client._client_factory
    composio_client.clear_client_factory()
    composio_client.reset_client()
    # The key is read through the cached settings object, so the cache has
    # to be dropped for the real key to take effect.
    monkeypatch.setenv("COMPOSIO_API_KEY", real_key)
    reset_settings_cache()
    try:
        yield
    finally:
        if factory is not None:
            composio_client.set_client_factory(factory)
        composio_client.reset_client()
        reset_settings_cache()


def _real_composio_key() -> str:
    """The real key, read from ``.env``."""
    from pathlib import Path

    env_file = Path(".env")
    if not env_file.is_file():
        return ""
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("COMPOSIO_API_KEY="):
            return line.split("=", 1)[1].strip().strip("'\"")
    return ""


def _catalog_tools(toolkit: str) -> tuple[set[str] | None, str]:
    """The toolkit's real tool slugs, and why not if they are unavailable."""
    from pa.integrations import catalog

    try:
        tools = catalog.list_tools(toolkit, limit=400, use_cache=False)
    except Exception as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:200]}"
    if not tools:
        return None, "the catalog returned no tools"
    return {t["slug"] for t in tools}, ""


def _skill_slugs(path: str, toolkit: str) -> set[str]:
    text = open(path, encoding="utf-8").read()
    prefix = toolkit.upper() + "_"
    found = set()
    for slug in _SLUG_RE.findall(text):
        if slug.startswith(prefix) and not _NOT_TOOLS.match(slug):
            found.add(slug)
    return found


def test_every_tool_slug_in_a_skill_exists(real_catalog):
    available, reason = _catalog_tools("hubspot")
    if available is None:
        pytest.skip(f"the Composio catalog is not reachable ({reason})")

    named = _skill_slugs("skills/hubspot-crm/SKILL.md", "hubspot")
    assert named, "the skill should name the tools it uses"

    unknown = sorted(s for s in named if s not in available)
    assert not unknown, (
        "skills/hubspot-crm/SKILL.md names tools that do not exist in the hubspot "
        "toolkit: " + ", ".join(unknown)
    )


def test_every_salesforce_tool_slug_in_a_skill_exists(real_catalog):
    available, reason = _catalog_tools("salesforce")
    if available is None:
        pytest.skip(f"the Composio catalog is not reachable ({reason})")

    named = _skill_slugs("skills/salesforce-crm/SKILL.md", "salesforce")
    assert named, "the skill should name the tools it uses"

    unknown = sorted(s for s in named if s not in available)
    assert not unknown, (
        "skills/salesforce-crm/SKILL.md names tools that do not exist in the "
        "salesforce toolkit: " + ", ".join(unknown)
    )


def test_the_hubspot_skill_names_the_stage_lookup():
    """The enrichment depends on this one being right."""
    from pathlib import Path

    text = Path("skills/hubspot-crm/SKILL.md").read_text(encoding="utf-8")
    assert "HUBSPOT_RETRIEVE_PIPELINE_STAGES" in text
    assert "HUBSPOT_GET_PIPELINE_STAGES" not in text


def test_the_enrichment_finds_the_stage_tool_by_pattern():
    """A renamed slug must still resolve, via the pattern fallback."""
    from pa.services.app_enrichment import ENRICHMENTS, _pattern_candidates

    spec = next(s for s in ENRICHMENTS["hubspot"] if s.kind == "stage")
    assert spec.patterns, "the stage lookup needs a pattern fallback"

    index = {
        "HUBSPOT_RETRIEVE_PIPELINE_STAGES": {"app": "hubspot", "classification": "read"},
        "HUBSPOT_RETRIEVE_PIPELINE_STAGE_BY_ID": {
            "app": "hubspot",
            "classification": "read",
        },
        "HUBSPOT_UPDATE_PIPELINE_STAGE": {"app": "hubspot", "classification": "write"},
    }
    candidates = _pattern_candidates(index, spec.patterns)
    # The listing tool wins over the by-id one, and writes are excluded.
    assert candidates[0] == "HUBSPOT_RETRIEVE_PIPELINE_STAGES"
    assert "HUBSPOT_UPDATE_PIPELINE_STAGE" not in candidates
