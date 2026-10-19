"""Cross-record consistency of an agent-run registry history (read-only).

`parse_registry_text` validates each record on its own. This module checks what
no single record can show: that the records of one run, read in file append
order, describe one run the writer could actually have produced, and that a
non-null idempotency key inside one registry source binds exactly one run.

The rules are derived from the only writer, `CodexAgentSessionAdapter._persist`
and its call sites in `submit` / `collect` (src/agent_session_codex.py):

    reserved -> dispatch_failed                      (terminal)
    reserved -> dispatch_uncertain                   (terminal)
    reserved -> dispatched -> collect_timeout        (terminal)
    reserved -> dispatched -> collected              (terminal)

`[reserved]` and `[reserved, dispatched]` are legal unfinished prefixes. Runs may
interleave and timestamps may repeat; nothing here sorts, de-duplicates, repairs
or rewrites anything. The first violation is reported and the caller must treat
the WHOLE history as corrupt (no partial summary, no evidence read).

Pure function over already-parsed records: no file, process, model or session
access.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Fields fixed when the run is reserved (`RunBinding` + request + key). The
# writer copies them unchanged into every later record of the run.
BINDING_FIELDS = ("app_kind", "instance_id", "session_id", "task_id", "attempt",
                  "request_snapshot", "request_fingerprint", "idempotency_key")

EVENTS = ("reserved", "dispatch_failed", "dispatch_uncertain", "dispatched",
          "collect_timeout", "collected")

# Legal successor events, keyed by the previous event of the same run (None =
# the run has no record yet). An event missing from a key's set is illegal there;
# terminal events have no successors.
TRANSITIONS: Dict[Optional[str], frozenset] = {
    None: frozenset({"reserved"}),
    "reserved": frozenset({"dispatch_failed", "dispatch_uncertain", "dispatched"}),
    "dispatched": frozenset({"collect_timeout", "collected"}),
    "dispatch_failed": frozenset(),
    "dispatch_uncertain": frozenset(),
    "collect_timeout": frozenset(),
    "collected": frozenset(),
}

# (stage, native_state) pairs the writer emits for dispatch_failed, and whether
# the native thread id is already known at that point.
DISPATCH_FAILED_BRANCHES = {
    ("thread/start", "thread/start-refused"): False,
    ("thread/start", "thread/start-no-id"): False,
    ("thread/start", "model-not-honored"): True,
    ("turn/start", "turn/start-refused"): True,
    ("turn/start", "turn/start-no-id"): True,
}
# stage -> whether the native thread id is known, for dispatch_uncertain.
DISPATCH_UNCERTAIN_BRANCHES = {"thread/start": False, "turn/start": True}

NATIVE_SCALARS = ("native_session_id", "native_model")


class HistoryInconsistent(Exception):
    """A registry history whose records contradict each other."""


def _fail(run_id: Any, index: int, detail: str):
    raise HistoryInconsistent(
        f"registry history is inconsistent: run {run_id!r} (record {index}): {detail}")


def check_binding(first: Dict[str, Any], record: Dict[str, Any], run_id: Any, index: int) -> None:
    """Invariant 1: the run's binding, request and key never change."""
    for name in BINDING_FIELDS:
        if record.get(name) != first.get(name):
            _fail(run_id, index, f"{name} changed from {first.get(name)!r} to {record.get(name)!r}")


def check_native_evolution(prev: Dict[str, Any], record: Dict[str, Any], run_id: Any,
                           index: int) -> None:
    """Invariant 2: native identifiers may go from unknown to known, never back
    and never to a different known value; the turn list may only be extended."""
    for name in NATIVE_SCALARS:
        before, after = prev.get(name), record.get(name)
        if before is None:
            continue
        if after is None:
            _fail(run_id, index, f"{name} regressed from {before!r} to null")
        if after != before:
            _fail(run_id, index, f"{name} conflicts: {before!r} then {after!r}")
    before_turns = list(prev.get("native_turns") or [])
    after_turns = list(record.get("native_turns") or [])
    if after_turns[:len(before_turns)] != before_turns:
        _fail(run_id, index, f"native_turns regressed or changed: {before_turns!r} then {after_turns!r}")


def check_transition(previous_event: Optional[str], event: str, run_id: Any, index: int) -> None:
    """Invariant 4: only the writer's event order, per run, in append order."""
    if event not in EVENTS:
        _fail(run_id, index, f"unknown event {event!r}")
    allowed = TRANSITIONS[previous_event]
    if event not in allowed:
        if previous_event is None:
            _fail(run_id, index, f"run starts with {event!r}; the writer always reserves first")
        if not allowed:
            _fail(run_id, index, f"{event!r} appended after terminal event {previous_event!r}")
        _fail(run_id, index, f"{event!r} cannot follow {previous_event!r}")


def _require(cond: bool, run_id: Any, index: int, detail: str) -> None:
    if not cond:
        _fail(run_id, index, detail)


def check_event_shape(event: str, record: Dict[str, Any], dispatched: Optional[Dict[str, Any]],
                      run_id: Any, index: int) -> None:
    """Invariant 4 (branches): the state and native fields each writer call site emits."""
    state, native_state = record.get("state"), record.get("native_state")
    session, model = record.get("native_session_id"), record.get("native_model")
    turns = record.get("native_turns") or []
    if event == "reserved":
        _require(state == "queued" and native_state == "reserved", run_id, index,
                 f"reserved must be queued/reserved, got {state!r}/{native_state!r}")
        _require(session is None and model is None and not turns, run_id, index,
                 "reserved must not carry native identifiers")
    elif event == "dispatch_failed":
        branch = (record.get("stage"), native_state)
        _require(state == "failed", run_id, index, f"dispatch_failed must be failed, got {state!r}")
        _require(branch in DISPATCH_FAILED_BRANCHES, run_id, index,
                 f"dispatch_failed branch {branch!r} is not one the writer emits")
        _require((session is not None) == DISPATCH_FAILED_BRANCHES[branch], run_id, index,
                 f"dispatch_failed {branch!r} native_session_id presence contradicts the branch")
        _require(not turns, run_id, index, "dispatch_failed must not carry native turns")
        if branch[0] == "thread/start":
            _require(model is None, run_id, index, "native_model recorded before the model was accepted")
    elif event == "dispatch_uncertain":
        stage = record.get("stage")
        _require(state == "unknown" and native_state == "dispatch-uncertain", run_id, index,
                 f"dispatch_uncertain must be unknown/dispatch-uncertain, got {state!r}/{native_state!r}")
        _require(stage in DISPATCH_UNCERTAIN_BRANCHES, run_id, index,
                 f"dispatch_uncertain stage {stage!r} is not one the writer emits")
        _require((session is not None) == DISPATCH_UNCERTAIN_BRANCHES[stage], run_id, index,
                 f"dispatch_uncertain {stage!r} native_session_id presence contradicts the stage")
        _require(not turns, run_id, index, "dispatch_uncertain must not carry native turns")
        if stage == "thread/start":
            _require(model is None, run_id, index, "native_model recorded before thread/start answered")
    elif event == "dispatched":
        _require(state == "running" and native_state == "turn-started", run_id, index,
                 f"dispatched must be running/turn-started, got {state!r}/{native_state!r}")
        _require(session is not None and len(turns) == 1, run_id, index,
                 "dispatched must carry the native thread and exactly one turn")
        if "thread_id" in record:
            _require(record["thread_id"] == session, run_id, index,
                     "dispatched thread_id contradicts native_session_id")
        if "turn_id" in record:
            _require(record["turn_id"] == turns[-1], run_id, index,
                     "dispatched turn_id contradicts native_turns")
    elif event in ("collect_timeout", "collected"):
        # check_transition guarantees a dispatched record; an empty one keeps
        # this check total (no exception) if it is ever called on its own.
        dispatched = dispatched or {}
        if event == "collect_timeout":
            _require(state == "unknown", run_id, index, f"collect_timeout must be unknown, got {state!r}")
            _require(native_state == dispatched.get("native_state"), run_id, index,
                     "collect_timeout changed native_state; the writer leaves it as dispatched")
        else:
            _require(state in ("declared_complete", "failed"), run_id, index,
                     f"collected must be declared_complete or failed, got {state!r}")
        # Collection copies the run's native identifiers; it never learns new
        # ones (conflicts and regressions are check_native_evolution's job).
        for name in NATIVE_SCALARS:
            _require(not (dispatched.get(name) is None and record.get(name) is not None),
                     run_id, index, f"{event} introduced {name}; collection never rebinds the run")
        _require(len(turns) == len(dispatched.get("native_turns") or []), run_id, index,
                 f"{event} changed the number of native turns; collection never adds a turn")


def check_idempotency(index_by_key: Dict[str, Any], record: Dict[str, Any], run_id: Any,
                      index: int) -> None:
    """Invariant 3: inside ONE registry source a non-null key binds one run.

    Same scope as the adapter's `_idem` index (one per registry path). A legal
    null may repeat. A second run on the same key is a contradiction whatever
    its fingerprint (same fingerprint = the writer would have replayed;
    different fingerprint = the writer would have refused).
    """
    key = record.get("idempotency_key")
    if key is None:
        return
    owner = index_by_key.setdefault(key, run_id)
    if owner != run_id:
        _fail(run_id, index, f"idempotency_key {key!r} already bound to run {owner!r}")


def check_history(records: List[Dict[str, Any]]) -> None:
    """Raise HistoryInconsistent on the first contradiction; return None if consistent.

    `records` must already be validated by `parse_registry_text`; they are read
    in file order and never mutated.
    """
    first_by_run: Dict[Any, Dict[str, Any]] = {}
    last_by_run: Dict[Any, Dict[str, Any]] = {}
    dispatched_by_run: Dict[Any, Dict[str, Any]] = {}
    index_by_key: Dict[str, Any] = {}
    for index, record in enumerate(records):
        run_id = record.get("run_id")
        event = record.get("event")
        previous = last_by_run.get(run_id)
        check_transition(previous.get("event") if previous else None, event, run_id, index)
        if previous is None:
            first_by_run[run_id] = record
        else:
            check_binding(first_by_run[run_id], record, run_id, index)
            check_native_evolution(previous, record, run_id, index)
        check_event_shape(event, record, dispatched_by_run.get(run_id), run_id, index)
        check_idempotency(index_by_key, record, run_id, index)
        if event == "dispatched":
            dispatched_by_run[run_id] = record
        last_by_run[run_id] = record
