# AI context viewer

## Start and resume

At task intake, read `/home/jerry/.agents/plan-workflow.md` and `/home/jerry/.agents/ai-model-strategy.md`, then `_flow/current.md` and its referenced progress document. Read `docs/research/coding-agent-memory/04-handoff.md` and the linked design and implementation outline before implementation. The project root is `/home/jerry/repos/private/ai-context-viewer/`; application files belong here, without a nested `context-viewer/` directory.

This project contains transferred research and mockups; implementation has not started. The user intends to request implementation in a new session. The outline records approved scope and remaining proposed defaults. Use the new session’s request to establish execution scope; do not treat historical proposals as approvals.

## Plans and delivery

Maintain progress checkboxes, dated findings/decisions, verification and complete resume notes in the active task documents throughout execution, review, PR/CI and handoff. `_flow/current.md` is pointer only and stays ignored. Keep task handoffs in `docs/research/coding-agent-memory/04-handoff.md`, updated in place. Continue editing the project copies; the ideas-generation copies are historical.

Merging is strictly human-performed. Agents must never merge through Git, a hosting API/CLI, UI, auto-merge or merge queues. “Ship it” means commit, push and create a PR, run/report relevant checks and CI, then hand it back. It never authorizes merging, deployment or release publication.

## Execution and cost

Use native tools and one agent by default. Follow the shared model strategy: lowest-cost suitable model and modest reasoning when selection is available; honor explicit user choices. Explain a concrete blocker and obtain the user’s choice before premium escalation. Do not claim active-session switching or savings without measurement. Record material model/effort choices in the active plan.

Use `/home/jerry/.codex/skills/focused-code-review/SKILL.md` for bounded code review; avoid duplicate review passes. Use exact searches and ordinary tools first. When available, follow `/home/jerry/.agents/skills/jev-file-triage/SKILL.md` for bounded semantic triage only when it reduces expensive reads; verify selected implementation before editing and fall back to native tools on incomplete evidence or service failure. Use zsh for shell commands.

## Product boundaries

Keep Codex logs read-only. Distinguish recorded source exposure, agent self-report and verification; retain unknown coverage. Fresh contexts remain separate without automatic carryover. The approved scope and acceptance cases are in the design/outline; preserve these distinctions during implementation.
