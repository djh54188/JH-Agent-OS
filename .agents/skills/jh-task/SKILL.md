---
name: jh-task
description: Run a JH-Agent OS task end to end. Use when the user wants to submit, check, resume, collect results, run offline code-delivery prepare/complete, or plan/register/query host-capture bindings for a JH task through the project's structured CLI.
---

# JH Task Entry (Codex-hosted)

Drive JH-Agent OS tasks through the project's structured CLI only. JH owns
model collaboration, independent review binding, trusted acceptance, budgets,
and evidence. This skill orchestrates CLI commands; it grants no permission
and changes no product code.

## Run directory binding

Run every command from the worktree that contains THIS skill file: the run
directory is the parent of the `.agents` directory holding this skill
(`<skill-dir>/../../..`). Never substitute another checkout, a global
install, or a hardcoded path. `--workspace` only sets the task workspace and
never changes which `src.cli` module Python loads, so it cannot fix a wrong
run directory.

## Pre-flight (every session, before the first CLI call)

From the run directory, verify and record all three; stop on any mismatch:

- `git rev-parse --show-toplevel` prints the run directory itself.
- `git rev-parse --abbrev-ref HEAD` and `git rev-parse --short HEAD` show the
  branch and commit actually under test (expected branch for this batch:
  `n1-artifact-entry`).
- `python -X utf8 -B -c "import src.cli; print(src.cli.__file__); print(hasattr(src.cli,'RECORDS_TRANSFORM_TEMPLATE_ID'))"`
  prints a `cli.py` path inside the run directory, followed by `True`.
- `python -X utf8 -B -c "import src.cli; print(hasattr(src.cli,'HOST_ENTRY_COMMANDS'))"`
  prints `True` (host-entry surface present in the loaded module).

## Commands

All commands print JSON.

- Submit and run: `python -m src.cli --db <db> --workspace <ws> submit <task.json> --run`
- Submit only: same without `--run`
- Read-only: `status <task_id>`, `summary <task_id>`, `events <task_id>`,
  `artifact <task_id>` (never create state, never call models)
- Continue: `resume <task_id>` (only for non-terminal, unblocked tasks)
- Stop: `cancel <task_id> --reason <text>`
- Code delivery (JH-006 offline batch only): `prepare-code-delivery <task_id> <artifact_version>`
  and `complete-code-delivery <task_id> <receipt.json>` (never call models, never write target files)
- Host entry (controlled bridge wiring only): `plan-host-check <task_id> <delivery_id>`
  prints the control-owned fixed invocation; `register-host-capture <task_id> <delivery_id> <archive_dir> <thread_id> <item_id> <call_id>`
  binds one independently captured execution; `capture-binding-status <task_id> <delivery_id>`
  reports whether the persisted binding is current (never call models; registration never succeeds a task)

## Output shapes

- `status` prints the `task_result` object directly.
- `summary` prints the full wrapper with `task_result` inside.
- `events` prints a bare JSON list of event objects; a missing task fails
  with a structured error instead of printing `[]`.
- `artifact` prints a wrapper with `task_status`, `latest_artifact`,
  `artifact_versions`, `review_bindings`, and `memory_records`.
- `plan-host-check` prints a wrapper with `task_id`, `delivery_id`, `workdir`,
  `invocation` (`argv`, `call_input`, `apply_attempt_id`, `context_sha256`), and a note.
- `register-host-capture` prints a wrapper with `binding_id`, thread/item/call ids,
  and digests; refusals print `command_ok: false` with an `error` reason instead.
- `capture-binding-status` prints `bound`, `current`, `reasons`, and the persisted
  `binding` (or null); `current: true` never implies task success.
- Never assume every command returns `task_result`. A successful query
  command does not mean the task succeeded.

For records tasks, use the fixed `records_transform_v1` template:
`metadata.input_data` containing only `records` (a non-empty list of
`{region: str, sales: number}`), and exactly these two acceptance criteria
in this order:

1. `Trusted input records were provided`
2. `Artifact content equals the expected sorted records`

The CLI computes the expected sorted output itself; do not invent
`expected_output` or `sort_by`. Anything else is refused before models run.

## Code delivery (JH-006 offline batch)

Only the fixed `code_sortfix_v1` template (`metadata.fixture_id`
`n2-code-sortfix/v1`): exactly the three fixture criteria in order, exactly
`target_files: ["target/widget.py"]`, no `input_data`, no model tools. The
baseline binds to the sealed fixture; the model emits only an Artifact v1
`patch_text` envelope for the single authorized target.

Read `delivery_scope` in `status`/`summary`: `generated_artifact` (patch
produced), `delivery_state` (`waiting_host` parked in `reviewing`, never
succeeded — vs `applied` verified), and `target_applied` (true only after a
verified complete). `resume` on a waiting task returns the parked state and
must not add model calls. Synthetic applies/receipts must be marked
`fixture-offline-host/v1` and run in isolated workspaces; production host
assembly now has a controlled CLI entry (see below); production complete still refuses.
A recorder string alone is never proof: offline complete requires an existing
fixture host record with host_call_id/apply_attempt_id and matching raw-output
digest. Only the explicit test harness can register synthetic fixture evidence;
never use that seam for production. `delivery_scope.evidence_kind` identifies
`offline_fixture`; its success does not prove real host execution.
Scope inventory supports only the small isolated fixture workspace (100 files,
2 MiB); links or unreadable files refuse, and no race-free snapshot is claimed.

The 2026-10-15 host-transcript candidate is rejected and archived. Its
self-reported output/digest and command-marker rules grant no production
authority. Keep production complete refused; follow the current STATUS and
independent-capture handoff before proposing any production bridge.

## Host entry (controlled bridge wiring)

Three steps, in order, through the CLI only:

1. `plan-host-check <task_id> <delivery_id>` prints the single control-owned
   fixed invocation for the waiting delivery. A plan is an instruction, never
   evidence; it proves nothing ran.
2. Execute the pinned call EXACTLY (same argv, same call script, same workdir)
   in a Codex Desktop session, capture with the probe script into an ignored
   `data/` directory, then `register-host-capture` with the archive path and
   the thread/item/call ids. Registration re-verifies every fact against the
   live session store and the current control snapshot; mismatches refuse.
3. `capture-binding-status <task_id> <delivery_id>` re-reads the persisted
   binding. `current: true` means the association still holds NOW; it never
   means the task succeeded, and production complete stays closed.

Scope and prohibitions: fixed `code_sortfix_v1` template only; only the
measured Codex desktop exec wrapper is admitted — Muse shell output must
never be presented as a Codex record. `apply_attempt_id` labels the capture
check, not a verified file apply; applied-baseline and full-scope proof
belong to a future production-delivery review. Never edit expected argv,
scripts, hashes, or workdirs to make a check pass; never mix fixture and
production runs; mark every substitute log or source `synthetic`. Keep
raw session bytes (account ids, full chat) out of git: archives live under
ignored `data/` paths. Real Codex source registration is performed by the
architect host session, not here.

## Rules

- Never run `approve` on the user's behalf without their explicit instruction
  for that exact request. Never use `recover` as a normal resume or to take
  over a task another process may be running.
- Never apply results to user target files. JH-004 delivers generated
  artifacts only (`generated_artifact`). Production `target_applied` stays
  false; explicit isolated offline fixtures can report verified synthetic
  application, always labeled `offline_fixture`.
- `--test-fixture-mode` marks explicit offline fixtures. Fixture output is
  never real evidence; never mix fixture and production runs of one task.
- A successful query command does not mean the task succeeded. Report
  `task_result.status`, current artifact version/SHA-256, review verdict,
  acceptance results, budget/usage, and blockers separately.
- Never present a Muse-executed command, a hand-written receipt, or a fixture
  record as an independent Codex capture. Quote `delivery_id`, `thread_id`,
  `item_id`, `call_id`, and digests from CLI output, never from memory.

## Result handoff

Return the run directory, branch/commit, and `src.cli` module path verified
in pre-flight, plus the task id, final task status, current artifact version
and SHA-256, reviewer verdict, per-criterion acceptance, usage totals, and
any blockers or unverified items. For host entry, also return the plan context
hash, binding id, thread/item/call ids, digests, and the query's current/reasons.
On failure, quote the structured `error`
field verbatim and stop; do not retry with wider permissions or a different
database.
