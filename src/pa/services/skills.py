"""Loading the assistant's skills.

A skill is a markdown file under ``skills/`` with YAML-ish frontmatter. It
carries the domain knowledge for one kind of work: which tools an app
exposes, how to use them in the right order, and the rules that keep the
assistant from doing damage. Without it the assistant knows *that* a
toolkit exists but not how to drive it.

Skills are injected into two places, because they answer two different
questions:

- the **rules** go into the prose prompt (what must never happen);
- the **guide** goes into the tool-selection prompt (which tool, in what
  order).

A skill is selected by the app the request is about, or by a trigger word
in the request. Only skills for the request's own app are loaded, so a
HubSpot skill never leaks into a Gmail request.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Skills are static files, but a change should not need a restart.
_CACHE_TTL_SECONDS = 300.0
_cache: dict[str, object] = {"at": 0.0, "skills": None}

# A skill's content is bounded: the prompt budget is small and shared.
MAX_SKILL_CHARS = 4000
MAX_SKILLS_PER_REQUEST = 2

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)


def skills_dir() -> Path | None:
    """Where the skills live.

    Checked in order: an explicit override, the repository root (development
    and tests), then the image's working directory (Docker).
    """
    override = os.getenv("PA_SKILLS_DIR")
    if override:
        path = Path(override)
        return path if path.is_dir() else None
    here = Path(__file__).resolve()
    # src/pa/services/skills.py -> repository root
    candidate = here.parents[3] / "skills"
    if candidate.is_dir():
        return candidate
    candidate = Path.cwd() / "skills"
    if candidate.is_dir():
        return candidate
    return None


def _parse_list(value: str) -> tuple[str, ...]:
    """Parse ``[a, b]`` or ``a, b`` into a tuple of strings."""
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return tuple(
        part.strip().strip("'\"")
        for part in value.split(",")
        if part.strip().strip("'\"")
    )


@dataclass(frozen=True)
class Skill:
    """One loaded skill."""

    name: str
    description: str
    apps: tuple[str, ...]
    triggers: tuple[str, ...]
    fallback: bool
    rules: str
    guide: str

    @property
    def text(self) -> str:
        return f"{self.rules}\n\n{self.guide}".strip()


def _split_sections(body: str) -> tuple[str, str]:
    """Split a skill into its rules and its guide.

    The ``Rules`` section is what must never be broken; the rest is how to
    do the work. They go to different prompts.
    """
    matches = list(_SECTION_RE.finditer(body))
    if not matches:
        return "", body.strip()
    rules: list[str] = []
    guide: list[str] = []
    preamble = body[: matches[0].start()].strip()
    if preamble:
        guide.append(preamble)
    for i, match in enumerate(matches):
        title = match.group(1).strip().lower()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        section = body[start:end].strip()
        # The heading is kept: it tells the model what kind of guidance it
        # is reading ("Tool families" is a list of tools, not prose).
        section = f"## {match.group(1).strip()}\n{section}".strip()
        if title.startswith("rule"):
            rules.append(section)
        else:
            guide.append(section)
    return "\n".join(rules).strip(), "\n".join(guide).strip()


def _parse_skill(path: Path) -> Skill | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("could not read skill %s: %s", path, type(exc).__name__)
        return None
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        logger.warning("skill %s has no frontmatter", path)
        return None
    front, body = match.group(1), match.group(2)

    meta: dict[str, str] = {}
    for line in front.splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip().lower()] = value.strip()

    name = meta.get("name") or path.parent.name
    rules, guide = _split_sections(body)
    return Skill(
        name=name,
        description=meta.get("description", ""),
        apps=tuple(a.lower() for a in _parse_list(meta.get("apps", ""))),
        triggers=tuple(t.lower() for t in _parse_list(meta.get("triggers", ""))),
        fallback=meta.get("fallback", "").strip().lower() in ("true", "yes", "1"),
        rules=rules[:MAX_SKILL_CHARS],
        guide=guide[:MAX_SKILL_CHARS],
    )


def load_skills() -> list[Skill]:
    """Every skill PA knows about. Never raises."""
    now = time.monotonic()
    cached = _cache.get("skills")
    if cached is not None and (now - float(_cache.get("at") or 0.0)) < _CACHE_TTL_SECONDS:
        return list(cached)  # type: ignore[arg-type]

    directory = skills_dir()
    if directory is None:
        logger.info("no skills directory found; the assistant runs without skills")
        _cache["at"] = now
        _cache["skills"] = []
        return []

    skills: list[Skill] = []
    try:
        for entry in sorted(directory.iterdir()):
            skill_file = entry / "SKILL.md" if entry.is_dir() else None
            if skill_file is None or not skill_file.is_file():
                continue
            skill = _parse_skill(skill_file)
            if skill is not None:
                skills.append(skill)
    except OSError as exc:
        logger.warning("could not read skills: %s", type(exc).__name__)

    _cache["at"] = now
    _cache["skills"] = skills
    return list(skills)


def invalidate() -> None:
    """Drop the cache (used by tests and after a skill is edited)."""
    _cache["at"] = 0.0
    _cache["skills"] = None


def select_skills(
    request: str,
    target_app: str | None = None,
    connected_apps: list[str] | None = None,
) -> list[Skill]:
    """The skills that apply to this request.

    A skill for the request's own app is chosen first; a generic fallback
    for that app is used only when no dedicated skill matched. Skills whose
    trigger words appear in the request are added regardless of app.
    """
    skills = load_skills()
    if not skills:
        return []

    chosen: list[Skill] = []
    app = (target_app or "").lower()

    if app:
        direct = [s for s in skills if app in s.apps and not s.fallback]
        if direct:
            chosen.extend(direct)
        else:
            chosen.extend(s for s in skills if app in s.apps and s.fallback)

    text = (request or "").lower()
    for skill in skills:
        if skill in chosen:
            continue
        if any(trigger and trigger in text for trigger in skill.triggers):
            chosen.append(skill)

    return chosen[:MAX_SKILLS_PER_REQUEST]


def skill_text(skills: list[Skill], part: str) -> str:
    """The rules or the guide of the chosen skills, as one block."""
    if not skills:
        return ""
    blocks = []
    for skill in skills:
        body = skill.rules if part == "rules" else skill.guide
        if not body:
            continue
        blocks.append(f"### Skill: {skill.name}\n{body}")
    return "\n\n".join(blocks)
