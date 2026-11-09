# AI context viewer

## Start and resume

Treat the checkout directory as the project root; application files belong here, without a nested `context-viewer/` directory. Read `README.md` and `docs/user-guide.md` for the current behavior and usage.

At task intake, read `~/.agents/plan-workflow.md` and `~/.agents/ai-model-strategy.md`, then `docs/research/coding-agent-memory/00-progress.md`. Before implementation, read `docs/research/coding-agent-memory/04-handoff.md` and the linked design and implementation outline. Expand `~/` to the current user’s home directory when accessing these files. Local planning and research documents are excluded from source control; if required files are missing in a new checkout, report that rather than silently skipping them. Use the current request to establish execution scope; historical proposals are not approvals.

## Plans and delivery

Maintain progress checkboxes, dated findings/decisions, verification and complete resume notes in the active task documents throughout execution, review, PR/CI and handoff. Keep task handoffs in `docs/research/coding-agent-memory/04-handoff.md`, updated in place. Keep local planning artifacts out of public commits when they contain private task history.

Merging is strictly human-performed. Agents must never merge through Git, a hosting API/CLI, UI, auto-merge or merge queues. “Ship it” means commit, push and create a PR, run/report relevant checks and CI, then hand it back. It never authorizes merging, deployment or release publication.

## Execution and cost

Use native tools and one agent by default. Follow the shared model strategy: lowest-cost suitable model and modest reasoning when selection is available; honor explicit user choices. Explain a concrete blocker and obtain the user’s choice before premium escalation. Do not claim active-session switching or savings without measurement. Record material model/effort choices in the active plan.

Use `~/.codex/skills/focused-code-review/SKILL.md` for bounded code review; avoid duplicate review passes. Use exact searches and ordinary tools first. When available, follow `~/.agents/skills/jev-file-triage/SKILL.md` for bounded semantic triage only when it reduces expensive reads; verify selected implementation before editing and fall back to native tools on incomplete evidence or service failure. Use zsh for shell commands.

## Product boundaries

Keep Codex logs read-only. Distinguish recorded source exposure, agent self-report and verification; retain unknown coverage. Fresh contexts remain separate without automatic carryover. Preserve these distinctions in implementation and acceptance checks.
