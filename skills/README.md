# PA Agent Skills

One skill per agent workflow. Each folder holds a `SKILL.md` with frontmatter
(`name` + `description` for discovery) followed by operating instructions.
An agent handling a request should load the skill whose description matches
the task.

| Skill | When to use |
|---|---|
| [assistant-request](assistant-request/SKILL.md) | General assistant task: submit, poll, return result |
| [daily-briefing](daily-briefing/SKILL.md) | Morning briefing / digest from seeded tasks and notes |
| [approval-gated-action](approval-gated-action/SKILL.md) | Sensitive side effects; jobs in `waiting_for_approval` |
| [persistent-memory](persistent-memory/SKILL.md) | Remember/forget user-approved preferences and facts |
| [job-recovery](job-recovery/SKILL.md) | Stuck jobs, post-restart requeue, queue health |
| [gmail-assistant](gmail-assistant/SKILL.md) | Inbox triage and approval-gated email sends |
| [whatsapp-assistant](whatsapp-assistant/SKILL.md) | Approval-gated WhatsApp messages |
| [zoom-assistant](zoom-assistant/SKILL.md) | Upcoming meetings and approval-gated scheduling |

Cross-cutting rules (apply to every skill): scope all data by owner, never
store or log secrets, validate all tool arguments against the allowlist in
`src/pa/tools/registry.py`, and record audit events for side effects.
