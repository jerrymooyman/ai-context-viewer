# Codex context viewer

A local browser companion for inspecting recorded Codex evidence. Logs are read-only; the service makes no inference calls and sends no messages.

Start with the [user guide](docs/user-guide.md) for setup, snapshots and the inspect-and-correct workflow.

Phases 1–4 implement context selection, incremental following, paged evidence, declared boundaries, validated goal/subtask snapshots, Sources, user concern flags and editable clarifications to copy. Same-session resets require a user declaration.

## Run

Python 3.14.8 and Node 26.8.2 were used during development. There are no third-party runtime dependencies. Playwright is a development-only dependency (`npm ci` installs the locked version). From the project root:

```sh
python3 -m viewer --project /sample/editor-config --log-root tests/fixtures --state-file .local/state.json --port 8765
```

Open <http://127.0.0.1:8765>, then choose a session. Expand individual records to inspect captured text. Stop the foreground service with Ctrl+C. Sessions are always grouped by their exact recorded project directory. Use Hide project / Show project to hide or restore its sessions; visibility is remembered in this browser. Hiding a project keeps the selected context open. Use Refresh to update both the list and selected context; there is no background polling. Use **Next evidence page** and **First evidence page** to browse history. Idle collection, unsupported input and unavailable logs have separate labels.

Without `--log-root`, logs come from `$CODEX_HOME/sessions`, or `~/.codex/sessions` if `CODEX_HOME` is unset. Only use log roots whose records you intend to make accessible locally. The default state location is `.local/state.json` relative to this project, and stores versioned boundary declarations, generation checkpoints and user concern flags. Writes are atomic; corrupt/unwritable state produces a visible error and prevents boundary/flag changes. Logs are never edited. Stop the service before backing up/removing state; deleting the sidecar explicitly removes all saved declarations and flags on next startup. No configuration or daemon is installed.

## Snapshot files and optional user-wide commands

Run the source launcher from the project you want to inspect, replacing `/path/to/ai-context-viewer` with this checkout’s location:

```sh
python3 /path/to/ai-context-viewer/scripts/ai-context-viewer
```

The caller directory selects the exact project; `--project /path/to/project` overrides it. The launcher locates this application's sources without changing cwd. Initial load and Refresh collect that project's saved reports. Choose task, session and context, then the **Files** or **Log/import** understanding stream. Files are the default when valid saved reports exist. File history survives restart through a separate SQLite ledger; imports remain temporary.

Repository-owned `skills/snapshot-session` and `scripts/install-user` are prepared but **not installed or live-client verified**. Complete the [disposable client identity check](docs/client-identity-check.md) first. Only after that gate, run:

```sh
python3 scripts/install-user --identity-verified
```

The flag records your attestation; it does not run identity checks. Installation writes an absolute-interpreter launcher to `~/.local/bin/ai-context-viewer` and a skill symlink to `~/.agents/skills/snapshot-session`. Add the bin directory to PATH if needed. The installer refuses unrelated/modified destinations and is idempotent for this checkout. Keep this checkout and Python interpreter in place. Remove only its unchanged entries with `python3 scripts/install-user --uninstall`; report files and state are retained. Temporary-root testing uses `--bin-dir` and `--skills-dir`.

After verified installation, explicitly invoke `$snapshot-session` in Codex, then Refresh the viewer. Native skill discovery still needs checking in the installed client; a bare `/snapshot-session` alias is not promised. The [file format](docs/snapshot-file-format.md) covers helper options, identity, full reports, replay, limits and backing up both stores.

## What the labels mean

| Display | What it establishes |
|---|---|
| Recorded supplied content | Captured base instructions, system/developer/user message text; not a complete startup inventory |
| Assistant message | Agent self-report; not a validated snapshot or independent verification |
| Tool call | Recorded request; no proof a read succeeded |
| Tool output | Captured result with call linkage where recorded; an exact supported match can establish recorded exposure, never independent verification |
| Unknown coverage | Completeness, current residency and causal influence are unverified |

The adapter targets recorded version **0.167.0**. Other versions retain metadata with an unsupported warning; explicit manual snapshot import remains available without claiming log coverage. Raw source/originator labels are displayed without treating them as verified client identity. The supplied synthetic fixture deliberately says `source: vscode` and `originator: codex-tui`.

Only complete JSONL records are displayed. Partial tails wait for a future read; malformed complete lines get positioned warnings. Metadata and individual records are bounded to 1 MiB. Each read consumes at most 4 MiB; further evidence waits for later refreshes. Oversized records are omitted with warnings while collection continues. Pages contain at most 100 raw records. The selected-log cache retains at most 5000 records / 8 MiB; earlier evicted evidence is visibly marked as omitted. Switching logs releases cached bodies and replays the selected log incrementally. File identity, size and first/last 4096 consumed-byte fingerprints detect replacements/truncation, including tested truncate-and-regrow. Arbitrary rewrites outside these checkpoints can be missed. File modification time in the list is filesystem metadata; captured activity time comes from retained records in the selected context, independent of the current page. Activity never establishes a fresh understanding snapshot.

The service binds only `127.0.0.1`, enforces local Host and same-origin requests, serves no external assets and uses a strict content security policy. Requests are bounded and arbitrary paths are not exposed. This is an explicit local-access tool, not a hardened multi-user server: local processes and people able to use this machine may access the service while it runs. Captured text may contain private content. Routine service logs omit request paths and bodies.

## Verify

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/web/*.test.mjs
```

HTTP tests bind disposable loopback ports and use temporary synthetic logs. A sandbox socket denial is a blocked integration run, not a passing test. `node tests/web/model.test.mjs` gives individual test names on runtimes that report only the file under `--test`.

## Declared context boundaries

Expand a captured record and choose **Start new context before this record**. Select the resulting entry in the context list. Earlier evidence stays in the preceding context; the boundary record and later evidence belong to the new one. Declarations show their user origin and timestamp. To correct a declaration, explicitly remove it with **Remove this declared boundary**, then declare the intended boundary. Removing a boundary joins its evidence back into the preceding context. Initial contexts cannot be removed.

Declarations are tied to file generation. Detected replacement invalidates cached evidence and excludes old declarations, with a stale-declaration warning. Activity or a compaction event alone never declares a fresh context. Separate session files remain separate even when recorded IDs match.

For a disposable Phase 2 preview (keep original fixtures unchanged):

```sh
mkdir -p /tmp/viewer-preview
cp tests/fixtures/supported.jsonl /tmp/viewer-preview/sample.jsonl
python3 -m viewer --log-root /tmp/viewer-preview --state-file /tmp/viewer-preview-state.json --port 8766
```

In another terminal, append a synthetic complete event:

```sh
python3 -c 'from pathlib import Path; import json; from datetime import datetime, timezone; p=Path("/tmp/viewer-preview/sample.jsonl"); f=p.open("a"); f.write(json.dumps({"type":"event_msg","timestamp":datetime.now(timezone.utc).isoformat(),"payload":{"type":"synthetic","message":"manual append"}})+"\n"); f.close()'
```

Choose the session and confirm the event appears without reselection. Declare a boundary before it, switch entries, then restart the service with the same state file and confirm separation persists. Partial JSONL tails should wait silently for completion; removal of the disposable log should show unavailable input.

The original viewer implementation was accepted. Snapshot-session packaging is a subsequent change; its live identity and installation gates remain pending. The [implementation outline](docs/research/coding-agent-memory/05-structure-outline-context-viewer.md) tracks evidence and remaining phases. Final focused code review is scheduled for Phase 5.


## Understanding snapshots and imports

Select a context, then expand **Snapshot request and schema v1 example**. Copy the request into the existing Codex conversation; the viewer sends nothing. Ask Codex for a new marked snapshot at task start or on demand. The browser renders the goal, shared constraints, explicit dependencies and selectable subtasks. Reported progress stays separate from verification. Snapshot age uses the selected scope's observation time; later activity never refreshes it.

**Import snapshot JSON** accepts the same schema without fences. Imports show local receipt time and unknown log provenance. **New import context** works without a session log. All imports and independent import contexts disappear when the service restarts; boundaries remain in the sidecar. Unsupported log versions can display imports while retaining their unsupported coverage warning.

The [exact schema and update rules](docs/snapshot-schema.md) and [valid example](web/snapshot-example.json) cover required fields, allowed values, supersession and resource limits. First goal snapshots use `supersedes: null`. Goal updates replace the whole listed subtask/claim/source collection; subtask updates preserve other scopes and shared constraints. History shows superseded/removed content. Invalid newer attempts show errors alongside the last valid understanding. Sources links use exact captured evidence where supported. Missing references remain unresolved; no agent report automatically becomes independently verified.


## Sources, user concerns and scoped clarifications

Choose the shared goal or a subtask. **Inspect source** follows a claim to captured evidence. Sources show explicit claim links, record keys, observation time, captured excerpts, coverage and paired calls where available. **Unassigned sources** keeps supplied records and tool outputs accessible without inventing task relevance. Skills catalogs are labeled as catalogs; an entry alone does not establish recorded skill content.

Matching is conservative: a supplied-record key links to that captured record. A file read requires an exact source title/path, one literal `cat <path>` call, one subsequent output with the same call ID and a recorded success marker. Batches, other commands, missing/duplicate calls, failed reads and missing evidence stay unresolved. Original captured output remains inspectable. The viewer never opens the current source file as a substitute. Version and full-file coverage remain unknown unless recorded evidence establishes them. Source excerpts are limited to 8192 characters each and 256 KiB combined per response, with visible excerpt notices; raw evidence pages retain the existing cache bounds.

Expand a source, enter **Your concern**, then **Save concern**. The sidecar stores user attribution, reason, creation time and context/generation/source or evidence identity. Flags survive restart for recorded contexts, never carry into a fresh context/generation and do not change agent reports or verification status. Flags tied to temporary imports cannot restore those imports or their temporary contexts after restart. No automatic relevance ranking or source editing occurs.

**Clarify claim** or **Clarify this source** creates editable text with context, goal/subtask, claim/source IDs, disputed statement, captured excerpt and a requested check. **Copy clarification** copies the current edited draft; clipboard failure selects the text for manual copying. Manual refresh preserves edits unless the log generation changes. A new context clears the correction editor. Copying establishes only copied text: sending, acknowledgment and behavior verification remain unknown. Schema v1 has no automatic acknowledgment field; inspect original snapshots/evidence and request a fresh scoped report in the existing Codex conversation.

## Operating and checking the complete workflow

Keep one service instance per sidecar. To back up state, stop the service, copy
`.local/state.json` to a separate `.json` file, and keep it with the same log
collection. To restore, stop the service and replace the sidecar with the backup.
Deleting it discards user boundaries and concern flags; it does not delete logs.
A changed/replaced log generation can invalidate saved declarations. Imports and
independent import contexts are memory-only and cannot be restored from state.

Use **Snapshot request and schema v1 example** to copy the instruction into Codex.
Only an explicit marked assistant snapshot updates the understanding view. For
unsupported versions or unavailable logs, use **New import context**, then
**Import snapshot JSON**. Imports have local receipt times and unknown log
provenance. See [the exact schema](docs/snapshot-schema.md).

For a same-session fresh context, expand the first record of the new context and
choose **Start new context before this record**. The declaration divides existing
evidence at that position; it does not clear or control Codex. Check the split
before continuing. **Remove this declared boundary** explicitly rejoins the
views. Separate session entries are never automatically grouped or inherited.

A useful acceptance journey is:

```text
choose context → inspect snapshot/subtask → inspect captured source
  → save a concern → edit/copy clarification → send it yourself in Codex
  → inspect later acknowledgment and check evidence separately
```

Compaction, interruption and unknown events remain recorded activity. They do not
prove which instructions survived or that a correction changed behavior. Generic
acknowledgment prose never changes snapshot verification. Stop with Ctrl+C; the
HTTP socket closes and file reads are scoped to each request (no persistent log
reader or background collector). Restart with the same arguments to restore
saved flags/boundaries and replay log snapshots. No daemon is installed.

`tests/test_workflow.py` exercises sanitized explorer assumptions, accepted sync
tradeoffs, procedural sources and stale handoff status through real loopback
HTTP. It also checks acknowledgment/compaction uncertainty, second-context-only
handoffs, restart behavior and unchanged source hashes. It uses temporary files,
not private transcripts. Loopback tests and browser launches need permission in
sandboxes that disable sockets/processes; a denied run is not a passing check.

Snapshot import API clients must send `X-Context-Generation` from the inspected
context response (`unavailable-log` for import-only/unsupported contexts). Missing
or stale generations are rejected before retaining an import. This transport
precondition leaves schema v1 unchanged. The browser clears import/clarification
drafts when it observes a changed generation; ordinary activity preserves edits.
Only a recognized execution wrapper with a single successful status before
`Output:` can establish a literal file read; success-looking captured text cannot.
