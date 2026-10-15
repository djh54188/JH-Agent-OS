---
name: jh-task
description: Run a JH-Agent OS task end to end. Use when the user wants to submit, check, resume, collect results, or run offline code-delivery prepare/complete for a JH task through the project's structured CLI.
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

## Output shapes

- `status` prints the `task_result` object directly.
- `summary` prints the full wrapper with `task_result` inside.
- `events` prints a bare JSON list of event objects; a missing task fails
  with a structured error instead of printing `[]`.
- `artifact` prints a wrapper with `task_status`, `latest_artifact`,
  `artifact_versions`, `review_bindings`, and `memory_records`.
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
assembly remains unimplemented and production complete still refuses.
A recorder string alone is never proof: offline complete requires an existing
fixture host record with host_call_id/apply_attempt_id and matching raw-output
digest. Only the explicit test harness can register synthetic fixture evidence;
never use that seam for production. `delivery_scope.evidence_kind` identifies
`offline_fixture`; its success does not prove real host execution.
Scope inventory supports only the small isolated fixture workspace (100 files,
2 MiB); links or unreadable files refuse, and no race-free snapshot is claimed.

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

## Result handoff

Return the run directory, branch/commit, and `src.cli` module path verified
in pre-flight, plus the task id, final task status, current artifact version
and SHA-256, reviewer verdict, per-criterion acceptance, usage totals, and
any blockers or unverified items. On failure, quote the structured `error`
field verbatim and stop; do not retry with wider permissions or a different
database.
