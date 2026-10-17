"""AgentSessionAdapter: minimal contract for external Agent sessions (JH-018).

DESIGN ONLY. Nothing in the production path imports this module; it defines the
interface a real adapter must satisfy and the value objects the collaboration
core and review modules exchange with it. Ordinary model calls stay on
ModelAdapter (docs/adapter-contracts.md) — an AgentSessionAdapter is a different
thing: it addresses an existing agent application session, asks it to work, and
observes what actually happened.

Design rules encoded here:

* Native differences live in the adapter, never in the contract. Capabilities
  are declared per adapter, and an unsupported operation raises
  UnsupportedCapability instead of being silently simulated.
* A session is addressed by application instance + native session id, not by a
  model name. The same application can hold several independently addressed
  sessions, and a role (worker/reviewer) is a registration attribute, not an
  address.
* Delivery is not success. `RunStatus` and `CollectedResult` carry what the
  adapter observed, plus explicit `unverified` facts; only the review and
  control layer decides whether a task succeeded.
* Messages that carry a next step come from the authorized flow: an adapter may
  not let an executor's own text redirect the route.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Capability names the contract understands (docs/modular-architecture.md §4).
CAPABILITIES = ("capabilities", "submit", "status", "collect", "continue", "cancel")


class UnsupportedCapability(RuntimeError):
    """Raised when an adapter is asked for something it does not support.

    Adapters must raise this rather than degrade silently into a different
    behaviour (for example pretending a delivered notification is a running
    session). Callers must surface it to the user as a reduced capability.
    """

    def __init__(self, app_kind: str, capability: str, detail: str = ""):
        self.app_kind = app_kind
        self.capability = capability
        self.detail = detail
        message = f"{app_kind} does not support {capability}"
        if detail:
            message += f": {detail}"
        super().__init__(message)


class RunState:
    """Observed run states. `declared_complete` is what the executor claims;
    `succeeded` is NOT a run state — it belongs to the control layer."""

    UNKNOWN = "unknown"
    QUEUED = "queued"
    ACCEPTED = "accepted"
    RUNNING = "running"
    DECLARED_COMPLETE = "declared_complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


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
    """One dispatch. `idempotency_key` lets the adapter deduplicate a retry of
    the same logical dispatch instead of starting a second run."""

    task_id: str
    attempt: int
    session: SessionAddress
    instructions: str
    workspace: str
    allowed_scope: List[str] = field(default_factory=list)
    idempotency_key: Optional[str] = None
    artifacts: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class RunStatus:
    run_id: Optional[str]
    state: str
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
        """Declare what this adapter can do. Must report each name in
        CAPABILITIES as supported/unsupported with a reason; it must not claim
        a capability it has not been observed to have."""

    @abstractmethod
    def submit(self, request: SubmitRequest) -> RunStatus:
        """Ask the addressed session to start work. Returns the observed run
        hand-off state, not a result."""

    @abstractmethod
    def status(self, session: SessionAddress, run_id: Optional[str]) -> RunStatus:
        """Current observed state of a run in the addressed session."""

    @abstractmethod
    def collect(self, session: SessionAddress, run_id: Optional[str]) -> CollectedResult:
        """Gather what the run produced. Never marks a task succeeded."""

    @abstractmethod
    def continue_run(self, session: SessionAddress, run_id: Optional[str],
                     instructions: str, idempotency_key: Optional[str] = None) -> RunStatus:
        """Send a bounded remediation to the SAME session and run. Must not
        reset the task budget, authorization scope, or attempt count."""

    @abstractmethod
    def cancel(self, session: SessionAddress, run_id: Optional[str]) -> RunStatus:
        """Request cancellation. Must distinguish request-sent from
        native-confirmed and from 'side effects unknown'."""


class MockAgentSessionAdapter(AgentSessionAdapter):
    """In-memory stand-in used to prove the core can host a second application
    without edits. It is a test double: it is NOT a second integrated app."""

    app_kind = "mock"

    def __init__(self, *, support=("capabilities", "submit", "status", "collect",
                                   "continue", "cancel")):
        self._support = set(support)
        self.runs: Dict[str, RunStatus] = {}
        self.collected: Dict[str, CollectedResult] = {}

    def _require(self, capability: str) -> None:
        if capability not in self._support:
            raise UnsupportedCapability(self.app_kind, capability, "not implemented by this adapter")

    def capabilities(self) -> Dict[str, Any]:
        return {name: {"supported": name in self._support} for name in CAPABILITIES}

    def submit(self, request: SubmitRequest) -> RunStatus:
        self._require("submit")
        if request.idempotency_key and request.idempotency_key in self.runs:
            return self.runs[request.idempotency_key]
        run_id = f"{self.app_kind}-run-{len(self.runs) + 1}"
        status = RunStatus(run_id=run_id, state=RunState.ACCEPTED, native_state="accepted")
        self.runs[request.idempotency_key or run_id] = status
        return status

    def status(self, session: SessionAddress, run_id: Optional[str]) -> RunStatus:
        self._require("status")
        return self.runs.get(run_id or "", RunStatus(run_id=run_id, state=RunState.UNKNOWN))

    def collect(self, session: SessionAddress, run_id: Optional[str]) -> CollectedResult:
        self._require("collect")
        return self.collected.get(
            run_id or "",
            CollectedResult(run_id=run_id, declared_status="unreported",
                            unverified=["collect: nothing observed yet"]))

    def continue_run(self, session: SessionAddress, run_id: Optional[str],
                     instructions: str, idempotency_key: Optional[str] = None) -> RunStatus:
        self._require("continue")
        return self.runs.get(run_id or "", RunStatus(run_id=run_id, state=RunState.UNKNOWN))

    def cancel(self, session: SessionAddress, run_id: Optional[str]) -> RunStatus:
        self._require("cancel")
        return RunStatus(run_id=run_id, state=RunState.CANCELLED,
                         note="request-sent (mock: no native confirmation)")
