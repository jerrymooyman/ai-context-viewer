# Understanding snapshot schema v1

Snapshots describe reported task understanding. They do not establish complete context coverage, current source residency or independent verification. The validator is [viewer/snapshots.py](../viewer/snapshots.py); the full valid example is [web/snapshot-example.json](../web/snapshot-example.json). The browser provides a copyable request with that example.

## Transport and limits

For automatic collection, ask Codex to emit JSON in an assistant message with this exact fence on its own line:

````text
```context-viewer-snapshot
{ ...schema v1 JSON... }
```
````

User messages and ordinary prose do not become snapshots. An invalid marked block produces a visible error, with the last valid understanding retained. The original message remains recorded evidence, subject to the displayed evidence-cache bounds.

For manual import, paste the JSON object without fences. `POST /api/contexts/{id}/imports` accepts that object directly as an `application/json` body. `POST /api/import-contexts` with `{}` creates an independent empty context when logs are unavailable; import into its returned `context_id`. Unsupported recorded versions can also accept imports without establishing log coverage.

All fields below are required; extra fields are rejected. JSON is limited to 64 KiB, rejects duplicate keys and nonfinite numbers, and allows at most 256 items per array. Nonempty text fields have at most 8192 characters. IDs contain 1–128 ASCII letters, digits, `_`, `.`, `:`, or `-`. No model-supplied observation timestamp is accepted.

## Exact fields

| Top-level field | Type and meaning |
|---|---|
| `schema_version` | Integer `1` (not a boolean) |
| `snapshot_id` | Unique ID within this context's history |
| `goal_id` | Stable ID for this context's goal |
| `scope` | Literal `goal`, or the ID of one existing subtask |
| `supersedes` | `null` for the first goal snapshot; otherwise the current snapshot ID for the updated scope |
| `goal` | Object below for goal scope; `null` for subtask scope |
| `subtasks` | Array of subtask objects below |
| `claims` | Array of claim objects below |
| `sources` | Array of reported source objects below |

| Object | Exact fields |
|---|---|
| Goal | `objective`: nonempty text; `constraints`: text array; `questions`: text array |
| Subtask | `id`: unique ID other than `goal`; `title`: nonempty text; `dependencies`: unique subtask ID array; `progress`: `planned`, `in_progress`, `blocked`, or `done` |
| Claim | `id`: unique ID; `scope`: `goal` or subtask ID; `statement`: nonempty text; `kind`: `assumption`, `constraint`, `decision`, or `question`; `source_ids`: ID array; `verification`: object below |
| Verification | `status`: `unknown`, `reported_pass`, or `reported_fail`; `evidence_keys`: text array |
| Source | `id`: unique ID; `title`: nonempty text; `evidence_keys`: text array |

Dependencies must reference existing subtasks and have no cycles. Missing source definitions or evidence references remain unresolved. A reported pass stays an agent report; neither a reference nor reported progress independently proves correctness. Sources match exact captured evidence conservatively; a path/title alone remains unresolved. Schema v1 has no acknowledgment field; generic prose never becomes automatic acknowledgment or behavior verification.

## Scoped replacement

```text
First goal s1
  goal head = s1; subtask a head = s1; subtask b head = s1

Update a with s2, supersedes s1
  goal head = s1; subtask a head = s2; subtask b head = s1

Update a again with s3
  supersedes must be s2
```

A goal update replaces the goal, its entire explicitly listed subtask collection, claims and sources. Subtasks omitted from it leave the current projection but remain in history. It supersedes the current **goal** head even when subtasks have separate newer heads.

A subtask update has `goal: null`, exactly one subtask matching `scope`, and only claims for that subtask. Shared constraints and unrelated subtasks/claims are preserved. Duplicate claim IDs across scopes are rejected. Source IDs cannot be redefined differently by a subtask update: use a new source ID or a goal update. Shared source definitions are preserved. Dependencies are revalidated against the resulting whole goal.

Cross-context supersession, unknown heads, stale ancestry, reused snapshot IDs and changing `goal_id` are rejected. To start a new goal independently, select or explicitly declare a fresh context.

## Provenance and storage

Log snapshots use the recorded message's timestamp/key and the label `agent-self-report`. Manual imports use local receipt time, no log evidence key and `user-imported`. The UI shows each selected scope's observation time separately from later activity. Unknown and future observation times stay explicit.

Valid history includes original payloads and metadata. Invalid attempts retain their raw JSON and validation error. Imports and independent import contexts are memory-only and disappear on restart. Replacing a detected log generation invalidates its current snapshot/import projection. Declaring a context boundary repartitions log snapshots by recorded position; imports remain tied to their explicitly selected context and generation.

Resource bounds: 128 accepted snapshots per context; 256 collected marked blocks per selected file generation; 128 import attempts per context/generation; 64 independent import contexts; 8 MiB of collected snapshot/import JSON across the service. Capacity errors retain the prior valid projection and are visible. The service does not discard valid history silently to make room. Restart reconstructs log snapshots incrementally within these bounds, but cannot restore imports.
