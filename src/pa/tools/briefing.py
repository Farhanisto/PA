"""Local briefing helper — no third-party integrations in MVP."""

from collections.abc import Sequence


def build_briefing(
    tasks: Sequence[str] | None = None,
    notes: Sequence[str] | None = None,
    preferences: Sequence[str] | None = None,
) -> str:
    tasks = list(tasks or [])
    notes = list(notes or [])
    preferences = list(preferences or [])

    lines = ["Morning Briefing", "================"]
    if tasks:
        lines.append("\nTasks:")
        for i, t in enumerate(tasks, 1):
            lines.append(f"  {i}. {t}")
    else:
        lines.append("\nTasks: none provided.")

    if notes:
        lines.append("\nNotes:")
        for n in notes:
            lines.append(f"  - {n}")

    if preferences:
        lines.append("\nPreferences considered:")
        for p in preferences:
            lines.append(f"  - {p}")

    if not tasks and not notes:
        lines.append("\nNo tasks or notes provided. Add tasks to get a useful briefing.")

    lines.append("\nNext: pick the top 1-3 tasks and timebox them.")
    return "\n".join(lines)
