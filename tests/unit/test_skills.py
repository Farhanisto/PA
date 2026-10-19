"""Unit tests: the assistant's skills are loaded and actually used."""

import pytest

from pa.services import skills as skills_mod


@pytest.fixture()
def skill_dir(tmp_path, monkeypatch):
    """A skills directory the test controls."""
    root = tmp_path / "skills"

    def write(name, front, body):
        d = root / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\n{front}\n---\n{body}", encoding="utf-8")

    write(
        "acme-crm",
        "name: acme-crm\ndescription: Work with Acme CRM.\napps: [acme]",
        "# Acme CRM\n\n"
        "## Tool families\n\n"
        "| Need | Tool |\n|---|---|\n| Deals | ACME_LIST_DEALS |\n\n"
        "## Flow\n\n"
        "1. Read before write.\n\n"
        "## Rules\n\n"
        "- Never write without approval.\n"
        "- Resolve ids to names first.\n",
    )
    write(
        "other-crm",
        "name: other-crm\ndescription: Any other CRM.\napps: [other]\nfallback: true",
        "# Other\n\n## Rules\n\n- Be careful.\n",
    )
    write(
        "link-apps",
        "name: link-apps\ndescription: Linking apps.\ntriggers: [connect, link]",
        "# Link\n\n## Rules\n\n- Never ask for a token.\n",
    )
    monkeypatch.setenv("PA_SKILLS_DIR", str(root))
    skills_mod.invalidate()
    yield root
    skills_mod.invalidate()


# --- loading ---------------------------------------------------------------


def test_skills_are_loaded_from_disk(skill_dir):
    loaded = skills_mod.load_skills()
    assert {s.name for s in loaded} == {"acme-crm", "other-crm", "link-apps"}


def test_the_frontmatter_is_parsed(skill_dir):
    acme = next(s for s in skills_mod.load_skills() if s.name == "acme-crm")
    assert acme.description == "Work with Acme CRM."
    assert acme.apps == ("acme",)
    assert acme.fallback is False
    assert skills_mod.load_skills()  # cached


def test_the_fallback_flag_is_parsed(skill_dir):
    other = next(s for s in skills_mod.load_skills() if s.name == "other-crm")
    assert other.fallback is True


def test_rules_and_guide_are_separated(skill_dir):
    acme = next(s for s in skills_mod.load_skills() if s.name == "acme-crm")
    assert "Never write without approval" in acme.rules
    assert "Resolve ids to names" in acme.rules
    assert "ACME_LIST_DEALS" not in acme.rules
    assert "ACME_LIST_DEALS" in acme.guide
    assert "Read before write" in acme.guide
    assert "Never write without approval" not in acme.guide


def test_a_missing_skills_directory_is_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setenv("PA_SKILLS_DIR", str(tmp_path / "nope"))
    skills_mod.invalidate()
    assert skills_mod.load_skills() == []
    assert skills_mod.select_skills("anything", "acme", ["acme"]) == []


def test_a_malformed_skill_is_skipped(skill_dir, monkeypatch):
    (skill_dir / "broken").mkdir()
    (skill_dir / "broken" / "SKILL.md").write_text("no frontmatter here", encoding="utf-8")
    skills_mod.invalidate()
    names = {s.name for s in skills_mod.load_skills()}
    assert "broken" not in names
    assert "acme-crm" in names


# --- selection -------------------------------------------------------------


def test_a_skill_is_selected_by_its_app(skill_dir):
    chosen = skills_mod.select_skills("show my acme deals", "acme", ["acme"])
    assert [s.name for s in chosen] == ["acme-crm"]


def test_a_dedicated_skill_beats_the_fallback(skill_dir):
    # "other" has only the fallback skill.
    chosen = skills_mod.select_skills("show my other deals", "other", ["other"])
    assert [s.name for s in chosen] == ["other-crm"]


def test_a_skill_is_selected_by_a_trigger(skill_dir):
    chosen = skills_mod.select_skills("how do I connect Slack?", None, ["gmail"])
    assert [s.name for s in chosen] == ["link-apps"]


def test_a_skill_for_another_app_is_not_selected(skill_dir):
    """A HubSpot skill must not leak into a Gmail request."""
    chosen = skills_mod.select_skills("show my emails", "gmail", ["gmail"])
    assert chosen == []


def test_at_most_two_skills_are_loaded(skill_dir):
    for i in range(5):
        (skill_dir / f"extra{i}").mkdir()
        (skill_dir / f"extra{i}" / "SKILL.md").write_text(
            f"---\nname: extra{i}\ntriggers: [connect]\n---\n\n## Rules\n\n- r{i}\n",
            encoding="utf-8",
        )
    skills_mod.invalidate()
    assert len(skills_mod.select_skills("connect", "acme", ["acme"])) <= 2


# --- prompt injection ------------------------------------------------------


def test_the_rules_and_guide_are_rendered_separately(skill_dir):
    chosen = skills_mod.select_skills("show my acme deals", "acme", ["acme"])
    rules = skills_mod.skill_text(chosen, "rules")
    guide = skills_mod.skill_text(chosen, "guide")
    assert "Never write without approval" in rules
    assert "ACME_LIST_DEALS" not in rules
    assert "ACME_LIST_DEALS" in guide
    assert "Never write without approval" not in guide


def test_the_rules_reach_the_prose_prompt(skill_dir):
    from pa.graph.nodes import _prompt_for

    prompt = _prompt_for(
        {
            "request": "show my acme deals",
            "target_app": "acme",
            "connected_apps": ["acme"],
        }
    )
    assert "Never write without approval" in prompt


def test_the_guide_reaches_the_tool_prompt(skill_dir):
    from pa.graph.nodes import plan_action
    from pa.services import tool_runner

    index = {
        "ACME_LIST_DEALS": {
            "app": "acme",
            "description": "List deals",
            "classification": "read",
            "required": [],
            "properties": {},
        }
    }
    tool_runner.tool_index = lambda user_id: dict(index)

    class _Msg:
        content = '{"action":"reply","text":"ok"}'

    class _Model:
        def __init__(self):
            self.prompts = []

        def invoke(self, prompt):
            self.prompts.append(prompt)
            return _Msg()

    model = _Model()
    plan_action(
        {
            "user_id": "u1",
            "request": "show my acme deals",
            "target_app": "acme",
            "connected_apps": ["acme"],
        },
        config={"configurable": {"model": model}},
    )
    assert "ACME_LIST_DEALS" in model.prompts[0]
    # The tool families table from the skill is what stops slug guessing.
    assert "Tool families" in model.prompts[0]


# --- the real skills -------------------------------------------------------


def test_the_repository_skills_load():
    skills_mod.invalidate()
    loaded = skills_mod.load_skills()
    names = {s.name for s in loaded}
    assert "hubspot-crm" in names
    assert "salesforce-crm" in names
    assert "crm-generic" in names
    assert "composio-connect" in names


def test_the_hubspot_skill_names_the_real_tools():
    skills_mod.invalidate()
    hubspot = next(s for s in skills_mod.load_skills() if s.name == "hubspot-crm")
    assert "HUBSPOT_RETRIEVE_PIPELINE_STAGES" in hubspot.guide
    assert "Never write without approval" in hubspot.rules


def test_a_hubspot_request_loads_the_hubspot_skill():
    skills_mod.invalidate()
    chosen = skills_mod.select_skills("show my hubspot tickets", "hubspot", ["hubspot"])
    assert [s.name for s in chosen] == ["hubspot-crm"]
