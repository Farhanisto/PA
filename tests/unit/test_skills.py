"""Skills validation: every skill has discoverable frontmatter + instructions."""

from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"
EXPECTED = {
    "assistant-request",
    "daily-briefing",
    "approval-gated-action",
    "persistent-memory",
    "job-recovery",
    "gmail-assistant",
    "whatsapp-assistant",
    "zoom-assistant",
}


def _load():
    skills = {}
    for path in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---"), f"{path} missing frontmatter"
        _, front, body = text.split("---", 2)
        meta = {}
        for line in front.strip().splitlines():
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
        skills[path.parent.name] = (meta, body.strip())
    return skills


def test_expected_skills_exist():
    skills = _load()
    assert EXPECTED <= set(skills), f"missing: {EXPECTED - set(skills)}"


def test_frontmatter_valid():
    skills = _load()
    names = []
    for dirname, (meta, body) in skills.items():
        assert meta.get("name"), f"{dirname}: frontmatter needs a name"
        assert meta["name"] == dirname, f"{dirname}: name must match directory"
        assert len(meta.get("description", "")) >= 20, f"{dirname}: description too short for discovery"
        assert len(body) > 200, f"{dirname}: instructions body too short"
        names.append(meta["name"])
    assert len(names) == len(set(names)), "skill names must be unique"


def test_skills_index_covers_all():
    index = (SKILLS_DIR / "README.md").read_text(encoding="utf-8")
    for name in EXPECTED:
        assert name in index, f"index missing {name}"
