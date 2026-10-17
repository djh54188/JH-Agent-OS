"""AgentSessionAdapter: minimal contract for external Agent sessions (JH-018).

DESIGN ONLY. Nothing in the production path imports this module; it defines the
interface a real adapter must satisfy and the value objects the collaboration
core and review modules exchange with it. Ordinary model calls stay on
ModelAdapter (docs/adapter-contracts.md) — an AgentSessionAdapter addresses an
existing agent application session, asks it to work, and observes what happened.

Rules encoded here (pinned by tests/test_jh018_agent_session_contract.py):

* Native differences live in the adapter, never in the contract. An unsupported
  operation raises UnsupportedCapability instead of being silently simulated.
* A session is addressed by application instance + native session id, never by a
  model name. A role is a registration attribute, not an address.
* A run is bound to application instance + session + logical task + attempt, and
  a *logical task* is distinct from a *native turn*: remediation adds a native
  turn while keeping the task, its history, its cumulative budget and its
  authorization scope.
* Idempotency and run lookup are separate indexes. A returned run_id always
  resolves to its own run; a mismatched address raises AddressMismatch instead
  of reading `unknown`, and a reused idempotency key with a different request
  raises IdempotencyConflict instead of hitting a cache.
* The idempotency fingerprint is a SHA-256 over a canonical JSON encoding of
  every field that changes execution semantics (address, task, attempt,
  workspace, authorization scope, budget, instructions, and artifact references
  with their version/content hash). A canonical encoding cannot be collided by
  separator characters in the inputs. Registration stores a request snapshot, so
  a caller mutating its own dict afterwards cannot alter the registered fact.
* Cancel is a REQUEST. Until native confirmation is observed the state is
  CANCEL_REQUESTED; `cancelled` is only reported after confirmation, and a run
  whose cancel is confirmed accepts no further remediation turn.
* Adding a remediation turn is idempotent per key: the same run + same key +
  same request appends exactly one native turn; the same key with a different
  request conflicts. The returned state always equals a following status query.
* There is no `succeeded` run state. Success belongs to the control layer, and
  `CollectedResult.unverified` lists what the adapter could not confirm.
"""
from __future__ import annotations

import hashlib
import json

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

CAPABILITIES = ("capabilities", "submit", "status", "collect", "continue", "cancel")

# Artifact reference keys that change execution semantics. Extra keys are kept
# and included in the fingerprint; these are the ones a caller must supply.
ARTIFACT_KEY_HINT = ("ref", "version", "sha256")


class UnsupportedCapability(RuntimeError):
    """Raised when an adapter is asked for something it does not support."""

    def __init__(self, app_kind: str, capability: str, detail: str = ""):
        self.app_kind = app_kind
        self.capability = capability
        self.detail = detail
        message = f"{app_kind} does not support {capability}"
        if detail:
            message += f": {detail}"
        super().__init__(message)


class AddressMismatch(RuntimeError):
    """The run does not belong to the address it was queried with."""

    def __init__(self, run_id: Optional[str], expected, actual):
        super().__init__(f"run {run_id!r} belongs to {expected}, queried as {actual}")
        self.run_id = run_id
        self.expected = expected
        self.actual = actual


class IdempotencyConflict(RuntimeError):
    """The same idempotency key was reused for a different request."""

    def __init__(self, key: str):
        super().__init__(f"idempotency key {key!r} already used for a different request")
        self.key = key


class RemediationRefused(RuntimeError):
    """The run is in a state that cannot accept another remediation turn."""

    def __init__(self, run_id: Optional[str], reason: str):
        super().__init__(f"run {run_id!r} cannot take a remediation turn: {reason}")
        self.run_id = run_id
        self.reason = reason


class RunState:
    """Observed run states. `declared_complete` is the executor's claim;
    `succeeded` is NOT a run state — that belongs to the control layer."""

    UNKNOWN = "unknown"
    QUEUED = "queued"
    ACCEPTED = "accepted"
    RUNNING = "running"
    DECLARED_COMPLETE = "declared_complete"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


def _canonical_json(value: Any) -> str:
    """Deterministic encoding: sorted keys, compact separators, no reliance on
    a separator character that an input could contain."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def _canonical_artifacts(artifacts) -> List[Dict[str, Any]]:
    """Snapshot artifact references into plain, sorted, JSON-safe dicts so a
    later mutation of the caller's object cannot change the registered fact."""
    out: List[Dict[str, Any]] = []
    for item in artifacts or ():
        if isinstance(item, dict):
            out.append({str(k): item[k] for k in sorted(item)})
        else:
            out.append({"raw": item})
    out.sort(key=lambda d: _canonical_json(d))
    return out


def canonical_request(request: "SubmitRequest") -> str:
    """The registered snapshot AND the fingerprint pre-image of a dispatch."""
    return _canonical_json({
        "schema": "jh018/submit/v1",
        "app_kind": request.session.app_kind,
        "instance_id": request.session.instance_id,
        "session_id": request.session.session_id,
        "model_id": request.session.model_id,
        "role": request.session.role,
        "task_id": request.task_id,
        "attempt": int(request.attempt),
        "workspace": request.workspace,
        "allowed_scope": list(request.allowed_scope),
        "budget_tokens": request.budget_tokens,
        "instructions": request.instructions,
        "artifacts": _canonical_artifacts(request.artifacts),
    })


def canonical_remediation(run_id: str, instructions: str) -> str:
    return _canonical_json({"schema": "jh018/remediate/v1", "run_id": run_id,
                            "instructions": instructions})


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SessionAddress:
    """Application instance + native session. Never a model name alone."""

    app_kind: str
    instance_id: str
    session_id: str
    model_id: Optional[str] = None
    role: Optional[str] = None


@dataclass(frozen=True)
class SubmitRequest:
    """One dispatch of one logical task attempt.

    `idempotency_key` deduplicates a retry of the *same* request; reusing it for
    a different request must conflict rather than return the earlier run.
    `artifacts` carries the references/versions/hashes of what the run is given,
    and they are part of the fingerprint.
    """

    task_id: str
    attempt: int
    session: SessionAddress
    instructions: str
    workspace: str
    allowed_scope: Tuple[str, ...] = ()
    idempotency_key: Optional[str] = None
    artifacts: Tuple[Dict[str, Any], ...] = ()
    budget_tokens: Optional[int] = None

    def snapshot(self) -> str:
        """Canonical, immutable record of this request as submitted."""
        return canonical_request(self)

    def fingerprint(self) -> str:
        """SHA-256 over the canonical snapshot: unambiguous, collision-safe."""
        return _digest(self.snapshot())


@dataclass(frozen=True)
class RunBinding:
    """What a run is bound to. Lookups must match all of it."""

    app_kind: str
    instance_id: str
    session_id: str
    task_id: str
    attempt: int

    @staticmethod
    def of(request: SubmitRequest) -> "RunBinding":
        return RunBinding(request.session.app_kind, request.session.instance_id,
                          request.session.session_id, request.task_id, request.attempt)

    def matches(self, request: SubmitRequest) -> bool:
        return (self.app_kind == request.session.app_kind
                and self.instance_id == request.session.instance_id
                and self.session_id == request.session.session_id
                and self.task_id == request.task_id
                and self.attempt == request.attempt)


@dataclass(frozen=True)
class NativeTurn:
    """The native unit of work inside the external application. One logical
    task attempt may span several native turns (initial + remediation)."""

    native_turn_id: str
    native_session_id: str
    purpose: str = "initial"


@dataclass
class RunStatus:
    run_id: Optional[str]
    state: str
    binding: Optional[RunBinding] = None
    native_turn: Optional[NativeTurn] = None
    native_state: Optional[str] = None
    evidence_ref: Optional[str] = None
    note: str = ""


@dataclass
class CollectedResult:
    """What the adapter could actually observe. Anything it cannot confirm goes
    in `unverified` — it is never promoted to trusted evidence here."""

    run_id: Optional[str]
    declared_status: str
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    usage: Dict[str, Any] = field(default_factory=dict)
    raw_refs: List[str] = field(default_factory=list)
    unverified: List[str] = field(default_factory=list)


class AgentSessionAdapter(ABC):
    """Minimal external-agent session adapter.

    `app_kind` is the native application family (e.g. "zcode", "codex"). A
    second application is integrated by adding an adapter, not by editing the
    collaboration core.
    """

    app_kind: str = ""

    @abstractmethod
    def capabilities(self) -> Dict[str, Any]:
        """Declare what this adapter can do. Must report every name in
        CAPABILITIES as supported/unsupported with a reason; it must never claim
        a capability that has not been observed."""

    @abstractmethod
    def submit(self, request: SubmitRequest) -> RunStatus:
        """Ask the addressed session to start work. Returns the run hand-off,
        not a result."""

    @abstractmethod
    def status(self, session: SessionAddress, run_id: Optional[str]) -> RunStatus:
        """Observed state of a run. A run that exists under another address
        raises AddressMismatch rather than reporting `unknown`."""

    @abstractmethod
    def collect(self, session: SessionAddress, run_id: Optional[str]) -> CollectedResult:
        """Gather what the run produced. Never marks a task succeeded."""

    @abstractmethod
    def continue_run(self, session: SessionAddress, run_id: Optional[str],
                     instructions: str, idempotency_key: Optional[str] = None) -> RunStatus:
        """Add one bounded remediation turn to the SAME run. Keeps the logical
        task, its turn history, its cumulative budget and its authorization
        scope. Idempotent per key; refused once a cancel is confirmed."""

    @abstractmethod
    def cancel(self, session: SessionAddress, run_id: Optional[str]) -> RunStatus:
        """Request cancellation. Returns CANCEL_REQUESTED until native
        confirmation is observed; `cancelled` only after confirmation."""


# --------------------------------------------------------------------------
# Two independent mock implementations. They exist to prove the core can host
# a second application without edits. Neither is a second integrated app.
# --------------------------------------------------------------------------

@dataclass
class _MockRun:
    run_id: str
    binding: RunBinding
    state: str
    request_snapshot: str
    request_fingerprint: str
    turns: List[NativeTurn] = field(default_factory=list)
    scope: Tuple[str, ...] = ()
    budget_tokens: Optional[int] = None
    budget_spent: int = 0
    cancel_confirmed: bool = False
    native_state: Optional[str] = None
    remediation_keys: Dict[str, str] = field(default_factory=dict)


class _MockBase(AgentSessionAdapter):
    """Shared behaviour for the two mocks; the storage strategy differs."""

    def __init__(self, *, support=CAPABILITIES):
        self._support = set(support)

    def _require(self, capability: str) -> None:
        if capability not in self._support:
            raise UnsupportedCapability(self.app_kind, capability, "not implemented by this adapter")

    def capabilities(self) -> Dict[str, Any]:
        return {name: {"supported": name in self._support} for name in CAPABILITIES}

    # --- storage hooks the two subclasses implement differently -------------
    def _store_run(self, run: _MockRun) -> None: raise NotImplementedError
    def _by_run_id(self, run_id: str) -> Optional[_MockRun]: raise NotImplementedError
    def _by_idem(self, key: str) -> Optional[Tuple[str, str]]: raise NotImplementedError
    def _put_idem(self, key: str, fingerprint: str, run_id: str) -> None: raise NotImplementedError
    def _next_run_id(self) -> str: raise NotImplementedError
    def _next_turn_id(self, run_id: str) -> str: raise NotImplementedError

    # --- contract ----------------------------------------------------------
    def submit(self, request: SubmitRequest) -> RunStatus:
        self._require("submit")
        snapshot = request.snapshot()
        fingerprint = _digest(snapshot)
        if request.idempotency_key:
            known = self._by_idem(request.idempotency_key)
            if known is not None:
                stored_fingerprint, run_id = known
                if stored_fingerprint != fingerprint:
                    raise IdempotencyConflict(request.idempotency_key)
                run = self._by_run_id(run_id)
                return RunStatus(run_id=run_id, state=run.state, binding=run.binding,
                                 native_turn=run.turns[-1] if run.turns else None,
                                 native_state=run.native_state,
                                 note="idempotent replay of the same request")
        run_id = self._next_run_id()
        turn = NativeTurn(self._next_turn_id(run_id), request.session.session_id, "initial")
        run = _MockRun(run_id=run_id, binding=RunBinding.of(request),
                       state=RunState.ACCEPTED, request_snapshot=snapshot,
                       request_fingerprint=fingerprint, turns=[turn],
                       scope=tuple(request.allowed_scope),
                       budget_tokens=request.budget_tokens, native_state="accepted")
        self._store_run(run)
        if request.idempotency_key:
            self._put_idem(request.idempotency_key, fingerprint, run_id)
        return RunStatus(run_id=run_id, state=run.state, binding=run.binding,
                         native_turn=turn, native_state="accepted")

    def _resolve(self, session: SessionAddress, run_id: Optional[str]) -> Optional[_MockRun]:
        run = self._by_run_id(run_id or "")
        if run is None:
            return None
        wanted = RunBinding(session.app_kind, session.instance_id, session.session_id,
                            run.binding.task_id, run.binding.attempt)
        if run.binding != wanted:
            raise AddressMismatch(run_id, run.binding, wanted)
        return run

    def status(self, session: SessionAddress, run_id: Optional[str]) -> RunStatus:
        self._require("status")
        run = self._resolve(session, run_id)
        if run is None:
            return RunStatus(run_id=run_id, state=RunState.UNKNOWN,
                             note="no run with this id")
        return RunStatus(run_id=run.run_id, state=run.state, binding=run.binding,
                         native_turn=run.turns[-1] if run.turns else None,
                         native_state=run.native_state)

    def collect(self, session: SessionAddress, run_id: Optional[str]) -> CollectedResult:
        self._require("collect")
        run = self._resolve(session, run_id)
        if run is None:
            return CollectedResult(run_id=run_id, declared_status="unreported",
                                   unverified=["collect: no run with this id"])
        return CollectedResult(
            run_id=run.run_id, declared_status=run.native_state or "unreported",
            usage={"turns": len(run.turns), "budget_spent": run.budget_spent,
                   "budget_tokens": run.budget_tokens},
            raw_refs=[f"{t.native_turn_id}:{t.purpose}" for t in run.turns],
            unverified=["adapter observation only; review decides success"])

    def continue_run(self, session: SessionAddress, run_id: Optional[str],
                     instructions: str, idempotency_key: Optional[str] = None) -> RunStatus:
        self._require("continue")
        run = self._resolve(session, run_id)
        if run is None:
            return RunStatus(run_id=run_id, state=RunState.UNKNOWN, note="no run with this id")
        if run.cancel_confirmed or run.state == RunState.CANCELLED:
            raise RemediationRefused(run_id, "cancel confirmed; no further turns")

        pre_image = canonical_remediation(run.run_id, instructions)
        fingerprint = _digest(pre_image)
        if idempotency_key:
            known = run.remediation_keys.get(idempotency_key)
            if known is not None:
                if known != fingerprint:
                    raise IdempotencyConflict(idempotency_key)
                # Same run + same key + same request: exactly one turn, replayed.
                return RunStatus(run_id=run.run_id, state=run.state, binding=run.binding,
                                 native_turn=run.turns[-1], native_state=run.native_state,
                                 note="idempotent replay of the same remediation request")

        turn = NativeTurn(self._next_turn_id(run.run_id), session.session_id, "remediation")
        run.turns.append(turn)                      # history preserved
        run.state = RunState.RUNNING
        run.native_state = "remediation-requested"
        # scope and budget are deliberately untouched: remediation adds no
        # authorization, does not restart the attempt and does not reset budget.
        if idempotency_key:
            run.remediation_keys[idempotency_key] = fingerprint
        return RunStatus(run_id=run.run_id, state=run.state, binding=run.binding,
                         native_turn=turn, native_state=run.native_state,
                         note="remediation turn added; attempt, budget and scope preserved")

    def cancel(self, session: SessionAddress, run_id: Optional[str]) -> RunStatus:
        self._require("cancel")
        run = self._resolve(session, run_id)
        if run is None:
            return RunStatus(run_id=run_id, state=RunState.UNKNOWN, note="no run with this id")
        # Request only. Native confirmation is a separate, observable step.
        run.state = RunState.CANCEL_REQUESTED
        run.native_state = "cancel-requested"
        return RunStatus(run_id=run.run_id, state=RunState.CANCEL_REQUESTED,
                         binding=run.binding, native_state=run.native_state,
                         note="request sent; native confirmation not observed")

    def confirm_cancel(self, session: SessionAddress, run_id: str) -> RunStatus:
        """Test/observation seam: record that the native side confirmed."""
        run = self._resolve(session, run_id)
        if run is None:
            return RunStatus(run_id=run_id, state=RunState.UNKNOWN)
        run.state = RunState.CANCELLED
        run.cancel_confirmed = True
        run.native_state = "cancelled"
        return RunStatus(run_id=run_id, state=RunState.CANCELLED,
                         binding=run.binding, native_state="cancelled")

    def inspect(self, run_id: str) -> Optional[_MockRun]:
        return self._by_run_id(run_id)


class MockAgentSessionAdapter(_MockBase):
    """Dict-backed mock (run_id index and idempotency index kept apart)."""

    app_kind = "mock-dict"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._runs: Dict[str, _MockRun] = {}
        self._idem: Dict[str, Tuple[str, str]] = {}
        self._counter = 0

    def _store_run(self, run: _MockRun) -> None:
        self._runs[run.run_id] = run

    def _by_run_id(self, run_id: str) -> Optional[_MockRun]:
        return self._runs.get(run_id)

    def _by_idem(self, key: str) -> Optional[Tuple[str, str]]:
        return self._idem.get(key)

    def _put_idem(self, key: str, fingerprint: str, run_id: str) -> None:
        self._idem[key] = (fingerprint, run_id)

    def _next_run_id(self) -> str:
        self._counter += 1
        return f"mock-run-{self._counter}"

    def _next_turn_id(self, run_id: str) -> str:
        run = self._runs.get(run_id)
        return f"{run_id}-turn-{len(run.turns) + 1 if run else 1}"


class SecondMockAgentSessionAdapter(_MockBase):
    """A genuinely different implementation: list-backed runs, linear scan,
    different id scheme. It exists to prove one call site serves both."""

    app_kind = "mock-list"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._runs: List[_MockRun] = []
        self._idem: List[Tuple[str, str, str]] = []

    def _store_run(self, run: _MockRun) -> None:
        self._runs.append(run)

    def _by_run_id(self, run_id: str) -> Optional[_MockRun]:
        for run in self._runs:
            if run.run_id == run_id:
                return run
        return None

    def _by_idem(self, key: str) -> Optional[Tuple[str, str]]:
        for existing_key, fingerprint, run_id in self._idem:
            if existing_key == key:
                return (fingerprint, run_id)
        return None

    def _put_idem(self, key: str, fingerprint: str, run_id: str) -> None:
        self._idem.append((key, fingerprint, run_id))

    def _next_run_id(self) -> str:
        return f"second#{len(self._runs) + 1}"

    def _next_turn_id(self, run_id: str) -> str:
        run = self._by_run_id(run_id)
        return f"turn::{run_id}::{len(run.turns) + 1 if run else 1}"
