"""
JH-Agent OS Task Lifecycle State Machine v1.0.0
Enforces strict 11-state transitions, terminal state immutability,
and the iron rule that Worker completion never directly enters 'succeeded' without 'reviewing'.
"""

from typing import Dict, FrozenSet, Optional, Set, Tuple

ALL_STATES: FrozenSet[str] = frozenset({
    "created",
    "classified",
    "planning",
    "awaiting_approval",
    "executing",
    "reviewing",
    "repairing",
    "succeeded",
    "failed",
    "cancelled",
    "needs_attention",
})

TERMINAL_STATES: FrozenSet[str] = frozenset({
    "succeeded",
    "failed",
    "cancelled",
})

# Legal transitions between states.
# Note: 'executing' CANNOT transition directly to 'succeeded' (Worker completion != task success).
ALLOWED_TRANSITIONS: Dict[str, FrozenSet[str]] = {
    "created": frozenset({"classified", "failed", "cancelled", "needs_attention"}),
    "classified": frozenset({"planning", "awaiting_approval", "failed", "cancelled", "needs_attention"}),
    "planning": frozenset({"awaiting_approval", "executing", "failed", "cancelled", "needs_attention"}),
    "awaiting_approval": frozenset({"planning", "executing", "failed", "cancelled", "needs_attention"}),
    "executing": frozenset({"reviewing", "awaiting_approval", "failed", "cancelled", "needs_attention"}),
    "reviewing": frozenset({"succeeded", "repairing", "failed", "cancelled", "needs_attention"}),
    "repairing": frozenset({"executing", "awaiting_approval", "failed", "cancelled", "needs_attention"}),
    "needs_attention": frozenset({"planning", "executing", "failed", "cancelled"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}


class InvalidStateTransitionError(Exception):
    """Raised when an illegal task state transition is attempted."""

    def __init__(self, current_state: str, target_state: str, reason: str = ""):
        self.current_state = current_state
        self.target_state = target_state
        self.reason = reason
        msg = f"Illegal state transition from '{current_state}' to '{target_state}'"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


def is_terminal_state(state: str) -> bool:
    return state in TERMINAL_STATES


def validate_transition(
    current_state: str,
    target_state: str,
    *,
    review_verdict: Optional[str] = None,
) -> None:
    """Validate whether transitioning from current_state to target_state is legal."""
    if current_state not in ALL_STATES:
        raise InvalidStateTransitionError(current_state, target_state, f"Unknown current state '{current_state}'")
    if target_state not in ALL_STATES:
        raise InvalidStateTransitionError(current_state, target_state, f"Unknown target state '{target_state}'")
    if current_state in TERMINAL_STATES:
        raise InvalidStateTransitionError(
            current_state,
            target_state,
            f"State '{current_state}' is terminal and cannot transition",
        )
    allowed = ALLOWED_TRANSITIONS.get(current_state, frozenset())
    if target_state not in allowed:
        raise InvalidStateTransitionError(
            current_state,
            target_state,
            f"Allowed targets from '{current_state}' are {sorted(allowed)}",
        )
    if target_state == "succeeded":
        if current_state != "reviewing":
            raise InvalidStateTransitionError(
                current_state,
                target_state,
                "Worker completion does not equal task success; must pass through 'reviewing'",
            )
        if review_verdict != "pass":
            raise InvalidStateTransitionError(
                current_state,
                target_state,
                f"Cannot enter 'succeeded' without Reviewer verdict 'pass' (got '{review_verdict}')",
            )


def resolve_review_transition(
    verdict: Optional[str],
    repair_count: int,
    max_repairs: int = 2,
) -> Tuple[str, str]:
    """
    Map a Reviewer verdict to the next task state and reason.
    Enforces:
    - pass -> succeeded
    - revise -> repairing (if repair_count < max_repairs) else failed
    - reject -> failed
    - needs_human -> needs_attention
    - Any missing/unknown verdict -> needs_attention (never auto-pass)
    """
    if verdict == "pass":
        return "succeeded", "Reviewer verdict is pass"
    if verdict == "revise":
        if repair_count < max_repairs:
            return "repairing", f"Reviewer requested revision (repair {repair_count + 1}/{max_repairs})"
        return "failed", f"Repair budget exhausted ({repair_count}/{max_repairs}) after revise verdict"
    if verdict == "reject":
        return "failed", "Reviewer rejected artifact (terminal failure)"
    if verdict == "needs_human":
        return "needs_attention", "Reviewer escalated task to human attention (needs_human)"
    return "needs_attention", f"Invalid or unavailable review verdict '{verdict}'; fail-closed to needs_attention"
