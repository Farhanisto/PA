---
name: approval-gated-action
description: Propose, pause, and resume sensitive PA actions requiring explicit user approval. Use when a workflow needs a side effect (send, email, post, delete, purchase, pay) or a job is in waiting_for_approval.
---

# Approval-Gated Action

Sensitive side effects never execute without explicit, authorized approval.
The graph pauses via LangGraph `interrupt`; the worker exits (it must not
block waiting for a human); resume happens on the same `thread_id` after
approval.

## Flow

1. `propose_action` builds a structured proposal (`action`, `request`,
   `summary`) and sets `approval_status: pending`.
2. `approval_gate` calls `interrupt(...)`; the worker persists an approval
   record and marks the job `waiting_for_approval`.
3. User approves/rejects: `POST /api/v1/approvals/{id}/approve|reject`
   (same `X-User-Id` as the job owner).
4. Approve enqueues `resume_approved_workflow`; the graph resumes with
   `Command(resume={"decision": "approved"})` and re-checks policy before
   executing.

## Rules (enforced in code, never prompt-only)

- `requires_approval()` in `pa.security.policy` covers `send_message`,
  `send_email`, `post`, `publish`, `delete`, `purchase`, `pay`,
  `user_requested_action`.
- Approval must reference the **stored** proposal — reject any client-supplied
  replacement payload.
- Verify the approver owns the approval and job; reject duplicates/stale
  submissions (second approve → 409).
- Re-check policy and permissions immediately before the side effect.
- Use idempotency keys; if an action may have succeeded before a crash,
  check its outcome before retrying.
- Only allowlisted tools (`pa.tools.registry.ALLOWED_TOOLS`, currently
  `generate_briefing` and `approved_echo`) may execute. Validate all
  model-produced arguments; never run arbitrary shell/Python.
