# Using the Codex context viewer

Use the viewer beside your existing Codex conversation to inspect what the agent reports about a task, follow claims to captured sources, and prepare corrections. The viewer reads logs without changing them. It makes no model calls and sends no messages to Codex.

The useful loop is:

```text
Ask Codex for a snapshot → Refresh the viewer → inspect a claim and its sources
  → edit/copy a clarification → send it in Codex → request an updated snapshot
  → Refresh and check the new report and supporting evidence
```

A snapshot is an agent report. Captured evidence can show that content was supplied or a narrowly supported read succeeded. Neither reveals everything currently in the model's context or independently proves the report correct.

## Start with your real sessions

From the project root, run:

```sh
cd /home/jerry/repos/private/ai-context-viewer
python3 -m viewer --state-file .local/state.json --port 8765
```

With the installed command, choose a port if needed:

```sh
ai-context-viewer --port 8002
```

This opens the viewer at <http://127.0.0.1:8002>. Omit `--port` to use 8765, or use `--port 0` to select an available port automatically. The Python invocation accepts the same option.

Open the printed URL, normally <http://127.0.0.1:8765>. Keep this terminal open; stop the service with Ctrl+C. No installation or daemon is needed, and there are no third-party runtime dependencies.

This command reads `$CODEX_HOME/sessions`, or `~/.codex/sessions` when `CODEX_HOME` is unset. To use another collection, add `--log-root /path/to/sessions`. The README's command with `--log-root tests/fixtures` opens synthetic examples rather than your real work.

The service exposes captured text locally while it runs. Choose a log root whose contents you intend to make accessible on this machine.

1. Find the recorded project directory under **Contexts** and choose the intended entry. Selection is always explicit.
2. Check the context label, recorded version and project before importing or correcting anything. Sessions sort by file modification time, newest first; that time does not show how fresh the agent's understanding is.
3. Sessions are grouped by local calendar date in expandable sections: file reports use the session’s latest export, and logs use file modification time. Today starts open; past days start collapsed. Expand a date to choose a session. Your toggles survive Refresh.
4. Press **Refresh** to read new activity and update the list. There is no background polling. Large logs can need several refreshes because each read is bounded.

Automatic evidence and marked-log snapshot collection currently supports recorded log version **0.167.0**. An unsupported version retains metadata and a warning; file reports or manual imports remain available without evidence coverage.

## Save reports with snapshot-session

The packaged skill is explicit-only and awaits the [live client identity gate](client-identity-check.md) before user-wide installation. The [README](../README.md#snapshot-files-and-optional-user-wide-commands) gives installation/removal commands. Until then, existing marked replies and manual imports below remain available.

With the verified skill installed:

1. Start `ai-context-viewer` from the exact project root where Codex runs, or pass `--project`. Open the printed URL. Use the same log root and sidecar for the viewer and exporter if overriding defaults.
2. Explicitly invoke `$snapshot-session` in the current Codex chat. Supply the current client's explicit ID if it is not already verifiably available. The skill finds the task's progress-document reference through the known handoff; missing reference becomes **Unassigned task**. It saves a full current goal report without sending a marked copy into the log.
3. Press **Refresh**, then choose task → session → context. Select **Files** understanding, the default when accepted files exist. **Log/import** remains a separate stream. Refresh collects reports; it never creates them.
4. Inspect export/collection times, attachment, current report and paged history. Missing or unsupported evidence stays unknown. Sources are available only through a uniquely matched supported context; an unresolved file is not attached by guesswork.
5. Invoke the skill again and Refresh to advance this history. A new chat has a separate history under the same explicit task reference, without inherited understanding. A manual viewer boundary instead needs its copyable current context target.

A stale-head error asks for a fresh report; the helper never silently rewrites ancestry. Invalid/conflicting reports preserve the last accepted state with diagnostics. Switching task, stream or context clears drafts; ordinary Refresh preserves valid drafts. Older history pages do not change the current head or its age.

Saved exports live in `.session-snapshots/`. Accepted bytes and conflict decisions also live in the ledger beside the sidecar (`state.json` → `state.snapshots.sqlite3`). Stop the viewer before backing up **both**. Keep the ledger to retain accepted content when original exports are missing/changed. Deleting it starts a fresh reconstruction from remaining unambiguous exports; no automatic pruning or repair occurs. See [file format and recovery](snapshot-file-format.md).

## Inspect preparation before execution

Ask for a preparation turn before the work itself. For a focused code review, send:

```text
Prepare for a focused code review of <files, diff or branch> using
$focused-code-review. Read the applicable project instructions, active plan,
review skill and enough of the diff to establish scope. Do not perform the
review yet.

Save a $snapshot-session report of the planned review scope, constraints,
sources read, checks planned, assumptions and unresolved questions. Use my
current explicit session ID <ID> if required. Then stop and wait for me to
approve the review.
```

Refresh the viewer, select the matching task/session/context and **Files** stream,
then inspect the goal, constraints, sources and unknowns. Send corrections in
Codex and request another report if needed. When satisfied, say:

```text
Proceed with the focused review using the scope and constraints I just checked.
If preparation reveals a material scope change, stop and report it first.
```

This checkpoint shows reported preparation and available recorded evidence. It
cannot preview the exact prompt or all context a future model call will receive.
The snapshot request is itself a model turn; the review remains paused until your
next instruction. Refresh only collects reports, so wait for a successful save.
Without the installed skill, use the marked snapshot request/import flow below
and include the same instruction to stop before reviewing.

## Get the first understanding snapshot

An ordinary conversation does not fill the understanding panel. Ask Codex to produce a structured snapshot explicitly.

1. Select the context that belongs to the Codex conversation you are using.
2. Expand **Snapshot request and schema v1 example**, then click **Copy snapshot request**.
3. Paste and send the request in that Codex conversation. The included example describes the format; Codex should replace it with the actual objective, constraints, subtasks, claims and sources.
4. Wait for Codex's response, then press **Refresh** in the viewer.
5. Check **Understanding snapshot**, its observation time and any validation errors.

Copying the request sends nothing. For automatic collection, Codex must respond in an assistant message with this exact opening fence on its own line:

````text
```context-viewer-snapshot
{ ...schema v1 JSON... }
```
````

The placeholder above is not a valid snapshot. Use the complete example in the viewer or [the JSON example](../web/snapshot-example.json). A user message containing the request/example and an ordinary prose response are recorded activity, not understanding snapshots.

### Use manual import when needed

If the log version is unsupported or automatic collection is unavailable:

1. Select the intended context. If no usable session exists, click **New import context** first.
2. Ask Codex for the same schema v1 snapshot using the copied request.
3. Expand **Import snapshot JSON** and paste only the JSON object, without its code fences.
4. Click **Import snapshot** and check the displayed result or error.

Imports show local receipt time and unknown log provenance, even when imported into a recorded context. All imported snapshots and independent import contexts disappear when the service restarts. Save important JSON elsewhere if you need it again.

## Read goals, subtasks and sources

Start at **Goal overview**. Review the objective, shared constraints, unresolved questions, subtask dependencies and reported progress. Then choose a subtask to inspect its claims and linked sources; shared constraints still apply.

| Display | How to use it |
|---|---|
| Reported progress, including `done` | The agent says work reached this state. Check verification separately. |
| `unknown`, `reported_pass`, `reported_fail` | A claim's reported check status. A reported pass is not independent verification. |
| Snapshot observation and age | When this scope's snapshot was observed in the log, or received as an import. |
| Last captured activity | Recent retained activity. Later activity does not refresh understanding. |
| Snapshot history and superseded content | Original JSON, older reports and the current snapshot ID for each scope. |
| Coverage and uncertainty | Limits on what was collected or can be established. Treat missing/truncated evidence as unknown. |

### Inspect a claim's origin

Click **Inspect source** beside a claim, or open a disclosure under **Sources**. Check the linked claims, agent-reported relevance, captured record keys, times, excerpts, coverage and paired tool call where available.

| Source result | What it establishes |
|---|---|
| `recorded-supplied` | The referenced content was captured as supplied material. |
| `recorded-read` | A narrowly supported exact file-read match was captured. |
| Unresolved reference | The viewer cannot establish the claimed exposure from retained evidence. |

A path or title alone cannot prove a read. File matching currently requires the exact source title/path, a single literal `cat <path>` command, one linked output in order and a recognized successful execution wrapper. Other tools and batched commands may remain unresolved even when a real read occurred. A skills catalog entry does not establish that the skill's content was read.

Use **Open source file in new tab** to read the referenced current local file as plain text, including Markdown. The page labels it as the current file; it may differ from what the agent saw. Paths must identify an existing UTF-8 text file no larger than 1 MiB within the project or agent instruction directories (`~/.agents` and `~/.codex/skills`); missing, descriptive or unsupported references show an error. **Open captured source in new tab** remains available for historical excerpts and provenance. Current files never replace captured evidence or establish a recorded read. Excerpts and retained evidence are bounded, with notices when content is omitted. **Unassigned sources — relevance unknown** keeps supplied records and tool outputs accessible without guessing their relevance to a task. Claim verification evidence references remain unresolved references; inspect the actual check evidence separately.

For raw details, expand records under **Recorded supplied content** or **Recorded activity**. Use **Next evidence page** and **First evidence page** to browse retained records. The displayed page is not the whole snapshot history or a complete prompt inventory.

## Correct a mistaken claim or source

1. Choose the affected goal or subtask and inspect the disputed statement and captured source.
2. Optionally enter **Your concern (user assessment)** under the source and click **Save concern**. This saves your annotation; it does not change Codex's report or verification status.
3. Click **Clarify claim** or **Clarify this source**.
4. In **Clarification to copy**, replace the correction placeholder with the concrete correction, why it matters and the check you want. Review the context, scope and excerpts before copying.
5. Click **Copy clarification**, then paste and send the text in the existing Codex conversation. If clipboard access fails, copy the selected draft manually.
6. Inspect Codex's response and any check results, request a new scoped snapshot, then press **Refresh**.

For example, put this kind of instruction in the editable correction:

```text
The task should use the existing local configuration, rather than assume the
sample configuration describes this machine. Check the actual configuration
before proceeding. Report what you checked and any remaining uncertainty.
```

Use facts and a check appropriate to your task. Copying, sending, acknowledgment and changed behavior are separate steps. Schema v1 has no acknowledgment field: an acknowledgment in prose, compaction or interruption does not change snapshot verification. Check both the updated report and evidence that the requested behavior/check occurred.

### Request a valid update

Open **Snapshot history and superseded content** and find the current ID for the scope you want to update. The copied snapshot request is generic; it does not insert these IDs for you. Add this instruction after it, replacing every placeholder:

```text
Update the existing snapshot for goal_id <goal-id> and scope <goal-or-subtask-id>.
Use a new unique snapshot_id and supersedes <current-snapshot-id-for-that-scope>.
Keep the existing goal_id. Use schema v1 and report the actual current task.
```

| Update | What Codex must include |
|---|---|
| First snapshot | Goal scope with `supersedes: null`. A subtask-only first snapshot is invalid. |
| Goal update | The entire goal, subtask list, claims and sources that should remain current. Omitted content leaves the current view but remains in history. |
| Subtask update | `goal: null`, exactly the existing subtask named by `scope`, and claims only for that subtask. Shared constraints and other scopes remain. |

Use the current **goal** head for a goal update, even if a subtask has a newer head. Each later subtask update must name that subtask's current head. Reused IDs, stale ancestry and a changed `goal_id` are rejected. Invalid attempts leave the last valid understanding in place. See [the exact schema and replacement rules](snapshot-schema.md) for required fields and limits.

## Keep fresh contexts separate

Separate session files always remain separate. Starting a new goal independently requires a fresh context rather than changing the existing goal ID.

If you genuinely start a fresh context inside the same recorded session, expand its first captured record and choose **Start new context before this record**. Select the resulting context entry and check the split. Earlier records stay in the preceding context; the selected record and later records belong to the new one.

This declaration only divides the viewer. It does not reset Codex, send a handoff or carry earlier understanding or concern flags into the new context. Compaction alone never declares a boundary. To correct a split, use **Remove this declared boundary**, then declare the intended position; removal rejoins evidence with the preceding context. The initial context cannot be removed.

Detected log replacement/truncation can invalidate saved boundaries and current projections. Inspect warnings and the refreshed context before importing again or relying on earlier snapshot ancestry.

## Know what survives

| State | Browser reload | Service restart |
|---|---|---|
| Selection and unsaved drafts | Lost | Not durable; copy important text first |
| Saved boundaries and recorded-context concerns | Retained by the running service | Restored with the same sidecar and matching logs/generation |
| Snapshots captured in supported logs | Available or recollected | Replayed incrementally from logs |
| Imported snapshots and independent import contexts | Remain in the running service | Lost |
| Accepted file reports | Available in Files stream | Restored with the same SQLite ledger, including retained history |

Ordinary **Refresh** preserves import and clarification drafts. Explicitly choosing an entry, even the same one, clears them. A detected generation change also clears drafts. Saved concerns for temporary imports cannot restore those imports after restart; flags never carry into another context/generation.

Use one service per state file. To back up or restore `.local/state.json`, stop the service first and keep the backup with the matching log collection. Deleting the sidecar removes saved boundaries and concerns, not Codex logs. Corrupt or unwritable state prevents boundary/concern changes and shows an error. Restart with the same command to restore durable state and replay supported log snapshots.

## Troubleshooting

| Symptom | Next step |
|---|---|
| No sessions listed | Check the configured project and log root for Codex `.jsonl` files, then **Refresh**. Expand the session’s date section. |
| Unsupported recorded version | Use **Import snapshot JSON**, or **New import context**. Automatic log coverage remains unavailable. |
| Activity changes but understanding stays old | Ask Codex for a new marked snapshot, then **Refresh**. Ordinary messages do not update understanding. |
| New snapshot is missing | Check the selected context, assistant role, exact fence, supported version, validation errors and coverage warnings. A large backlog may need repeated refreshes. |
| Import is rejected | Paste JSON only; check required fields, unique IDs and the current scope head in history. Maximum JSON size is 64 KiB. The last valid report remains current. |
| Context generation changed | Inspect the refreshed context and warnings before retrying. Prior imports or ancestry may no longer apply. |
| Source is unresolved | Inspect available records and missing/ambiguous references. Unresolved means insufficient matching evidence, not proof the source was never read. |
| “Local same-origin requests only” | Open the exact printed loopback URL. If using an old server with the fixed Firefox issue, preserve text, stop/restart the service and reload the page before retrying. Restart loses imports. |
| Context unavailable | **Refresh** the list or select again. The log may have been deleted/replaced or its old boundary may be stale. |
| Capacity or omitted-evidence warning | Preserve important imported JSON. Inspect the stated limit; restart loses imports and does not remove limits inherent in replaying a log's history. |

The viewer cannot guarantee complete prompt coverage, live Codex emission timing, current model residency or that a correction changed behavior. Use the report to choose what to inspect and check, keeping those unknowns visible.

For the evidence behind this guide, see [the workflow research](research/coding-agent-memory/08-research-user-guide.md).
