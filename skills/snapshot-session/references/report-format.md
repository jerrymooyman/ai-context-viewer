# Semantic report input

Send exactly these fields to the helper; replace the example's content with the actual current task. Omit `snapshot_id`: code generates it. All reports use `scope: "goal"`, replacing the complete current subtask/claim/source collection.

```json
{
  "schema_version": 1,
  "goal_id": "goal-example",
  "scope": "goal",
  "supersedes": null,
  "goal": {"objective": "Describe the current task", "constraints": [], "questions": []},
  "subtasks": [],
  "claims": [],
  "sources": []
}
```

| Object | Exact fields |
|---|---|
| Subtask | `id`, `title`, `dependencies` (subtask ID array), `progress` (`planned`, `in_progress`, `blocked`, `done`) |
| Claim | `id`, `scope` (`goal` or subtask ID), `statement`, `kind` (`assumption`, `constraint`, `decision`, `question`), `source_ids` (array), `verification` |
| Verification | `status` (`unknown`, `reported_pass`, `reported_fail`), `evidence_keys` (array) |
| Source | `id`, `title`, `evidence_keys` (array; empty when unknown) |

IDs use 1–128 ASCII letters, digits, `_`, `.`, `:` or `-`. Nonempty text fields have at most 8192 characters; arrays at most 256 items. Payload cap is 64 KiB. Dependencies must reference listed subtasks without cycles. Claim scope must be `goal` or a listed subtask. Reuse source IDs only for the same source. Unknown references stay unresolved. Reported progress and check status never establish independent verification.

For updates use the inspected goal ID and exact head; include all content that should remain current. Omitted content leaves the current view but remains in history. [envelope-example.json](envelope-example.json) is a validator-tested storage example, not helper input; code creates that envelope.
