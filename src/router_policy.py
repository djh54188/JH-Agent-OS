"""
JH-Agent OS Rule-Based Router & Risk Policy Engine v1.0.0
Enforces:
1. Per-call parameter & permission inspection before every tool invocation.
2. Level 3 ABSOLUTE_FORBIDDEN priority over any routing or model confidence score (zero downgrade).
3. Incremental re-authorization when plans or parameters change beyond approved scope.
4. Separation of Risk (permissions), Complexity (planning depth), and Uncertainty (review/repair budget).
"""

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

RISK_LEVEL_TO_NUMERIC: Dict[str, int] = {
    "read_only": 0,
    "workspace_write": 1,
    "system_sensitive": 2,
    "danger_external": 3,
}

NUMERIC_TO_LEVEL_NAME: Dict[int, str] = {
    0: "READ_ONLY",
    1: "WORKSPACE_WRITE",
    2: "REQUIRES_APPROVAL",
    3: "ABSOLUTE_FORBIDDEN",
}

ALLOWED_WRITE_EXTENSIONS: FrozenSet[str] = frozenset({
    ".py", ".json", ".md", ".toml", ".txt", ".yml", ".yaml", ".csv", ".sql", ".ini", ".cfg"
})

FORBIDDEN_WRITE_EXTENSIONS: FrozenSet[str] = frozenset({
    ".exe", ".dll", ".sys", ".bat", ".cmd", ".ps1", ".vbs", ".msi"
})

READ_ONLY_TOOLS: FrozenSet[str] = frozenset({
    "read_file", "list_dir", "git_status", "git_diff", "search_text", "inspect_schema"
})

WRITE_TOOLS: FrozenSet[str] = frozenset({
    "write_file", "edit_file", "create_file", "apply_patch"
})

LEVEL3_COMMAND_PATTERNS = [
    (r"(?i)\brmdir\s+/s", "L3-CMD-RMDIR-S"),
    (r"(?i)\bdel\s+/[fsq]", "L3-CMD-DEL-FORCE"),
    (r"(?i)\brm\s+-rf\s+(/|[a-zA-Z]:\\|\*)", "L3-CMD-RM-RF-ROOT"),
    (r"(?i)\bformat\s+[a-zA-Z]:", "L3-CMD-FORMAT-DRIVE"),
    (r"(?i)\breg\s+(add|delete)\b", "L3-CMD-REGISTRY"),
    (r"(?i)\bbcdedit\b", "L3-CMD-BCDEDIT"),
    (r"(?i)--dangerously-bypass-approvals-and-sandbox", "L3-SELF-PRIVILEGE-ESCALATION"),
    (r"(?i)(curl|invoke-webrequest|wget)\b.*(api_key|auth\.json|secret|token)", "L3-CREDENTIAL-EXFIL"),
]

LEVEL2_COMMAND_PATTERNS = [
    (r"(?i)\bgit\s+reset\s+--hard\b", "L2-GIT-RESET-HARD"),
    (r"(?i)\bgit\s+clean\s+-[a-zA-Z]*f", "L2-GIT-CLEAN"),
    (r"(?i)\bgit\s+push\s+.*--force\b", "L2-GIT-PUSH-FORCE"),
    (r"(?i)\b(pip\s+install|npm\s+install\s+-g)\b", "L2-EXTERNAL-PKG-INSTALL"),
    (r"(?i)\b(rm|del|remove-item)\b", "L2-FILE-DELETE"),
]


@dataclass
class PolicyDecision:
    level: int
    level_name: str
    action: str  # "allow" | "hold_for_approval" | "deny_and_fail"
    target_task_state: str  # "executing" | "awaiting_approval" | "failed"
    tool_result_status: str  # "success" | "denied_by_policy"
    reason: str
    violated_rule_id: Optional[str] = None


@dataclass
class RoutingDecision:
    worker_profile: str
    reviewer_profile: str
    use_mock_worker: bool
    planning_depth: str  # "shallow" | "standard" | "deep"
    max_repairs: int
    effective_risk_level: str
    requires_initial_approval: bool
    blocked_by_level3: bool
    routing_basis: str
    risk_basis: str


def _normalize_path(raw_path: str, workspace_root: str) -> Path:
    ws = Path(workspace_root).resolve()
    p = Path(raw_path)
    if not p.is_absolute():
        p = (ws / p).resolve()
    else:
        p = p.resolve()
    return p


def _is_within_workspace(target: Path, workspace_root: str) -> bool:
    ws = Path(workspace_root).resolve()
    try:
        target.relative_to(ws)
        return True
    except ValueError:
        return False


def _is_level3_system_path(raw_path: str, resolved_path: Path) -> Optional[str]:
    norm_str = str(resolved_path).replace("/", "\\").lower()
    raw_lower = raw_path.replace("/", "\\").lower().strip()
    if raw_lower in {"c:\\", "c:", "/", "\\"}:
        return "L3-PATH-SYSTEM-ROOT"
    forbidden_prefixes = [
        "c:\\windows",
        "c:\\program files",
        "c:\\program files (x86)",
        "c:\\system32",
    ]
    for prefix in forbidden_prefixes:
        if norm_str == prefix or norm_str.startswith(prefix + "\\"):
            return "L3-PATH-WINDOWS-SYSTEM"
    if ".codex\\auth.json" in norm_str:
        return "L3-PATH-CODEX-AUTH"
    if norm_str.endswith("docs\\danger-action-policy.md"):
        return "L3-PATH-SELF-POLICY-MODIFICATION"
    return None


class RiskPolicyEngine:
    """
    Deterministic code-rule risk policy engine.
    Evaluates every tool call's actual parameters and permissions immediately prior to execution.
    """

    def __init__(self, workspace_root: str):
        self.workspace_root = str(Path(workspace_root).resolve())

    def evaluate_tool_request(
        self,
        tool_request: Dict[str, Any],
        *,
        approved_request_ids: Optional[Set[str]] = None,
        authorized_files: Optional[Set[str]] = None,
    ) -> PolicyDecision:
        """
        Inspect actual parameters and permissions for a single ToolRequest.
        Order of precedence: Level 3 (Forbidden) -> Level 2 (Approval) -> Level 1 (Write) -> Level 0 (Read).
        Model confidence or low declared risk_level NEVER downgrades a detected higher-level risk.
        """
        approved_ids = approved_request_ids or set()
        req_id = tool_request.get("request_id", "")
        tool_name = tool_request.get("tool_name", "")
        params = tool_request.get("parameters") or {}
        declared_risk = tool_request.get("risk_level", "read_only")
        declared_level = RISK_LEVEL_TO_NUMERIC.get(declared_risk, 3)

        # Extract candidate paths and commands from actual parameters
        raw_paths: List[str] = []
        for k in ("path", "file_path", "target_path", "destination", "source"):
            val = params.get(k)
            if isinstance(val, str) and val.strip():
                raw_paths.append(val.strip())

        command_str = ""
        for k in ("command", "cmd", "script", "shell_command"):
            val = params.get(k)
            if isinstance(val, str) and val.strip():
                command_str += " " + val.strip()

        # ---------------------------------------------------------
        # 1. Level 3 Check (ABSOLUTE_FORBIDDEN - Highest Priority)
        # ---------------------------------------------------------
        if declared_level >= 3:
            return PolicyDecision(
                level=3,
                level_name="ABSOLUTE_FORBIDDEN",
                action="deny_and_fail",
                target_task_state="failed",
                tool_result_status="denied_by_policy",
                reason="ToolRequest declared danger_external (Level 3)",
                violated_rule_id="L3-DECLARED-DANGER-EXTERNAL",
            )

        for rp in raw_paths:
            resolved = _normalize_path(rp, self.workspace_root)
            l3_rule = _is_level3_system_path(rp, resolved)
            if l3_rule:
                if l3_rule == "L3-PATH-SELF-POLICY-MODIFICATION" and tool_name in READ_ONLY_TOOLS:
                    continue
                return PolicyDecision(
                    level=3,
                    level_name="ABSOLUTE_FORBIDDEN",
                    action="deny_and_fail",
                    target_task_state="failed",
                    tool_result_status="denied_by_policy",
                    reason=f"Target path '{rp}' violates Level 3 system protection ({l3_rule})",
                    violated_rule_id=l3_rule,
                )
            # Check c:\ drive destructive operation
            if str(resolved).lower().startswith("c:\\") and tool_name not in READ_ONLY_TOOLS:
                return PolicyDecision(
                    level=3,
                    level_name="ABSOLUTE_FORBIDDEN",
                    action="deny_and_fail",
                    target_task_state="failed",
                    tool_result_status="denied_by_policy",
                    reason=f"Write/modify operation targeting C: drive path '{rp}' is forbidden",
                    violated_rule_id="L3-PATH-C-DRIVE-WRITE",
                )

        if command_str:
            for pattern, rule_id in LEVEL3_COMMAND_PATTERNS:
                if re.search(pattern, command_str):
                    return PolicyDecision(
                        level=3,
                        level_name="ABSOLUTE_FORBIDDEN",
                        action="deny_and_fail",
                        target_task_state="failed",
                        tool_result_status="denied_by_policy",
                        reason=f"Command matches forbidden Level 3 pattern ({rule_id})",
                        violated_rule_id=rule_id,
                    )

        # ---------------------------------------------------------
        # 2. Level 2 Check (REQUIRES_APPROVAL)
        # ---------------------------------------------------------
        level2_reasons: List[Tuple[str, str]] = []

        if declared_level == 2 or tool_request.get("requires_human_approval") is True:
            level2_reasons.append(("L2-DECLARED-SENSITIVE", "ToolRequest requires human approval or system_sensitive"))

        for rp in raw_paths:
            if ".." in Path(rp).parts:
                level2_reasons.append(("L2-PATH-TRAVERSAL", f"Path '{rp}' contains parent traversal '..'"))
            resolved = _normalize_path(rp, self.workspace_root)
            if not _is_within_workspace(resolved, self.workspace_root):
                level2_reasons.append(("L2-CROSS-WORKSPACE", f"Path '{rp}' is outside workspace '{self.workspace_root}'"))
            if tool_name in WRITE_TOOLS or declared_level >= 1:
                ext = resolved.suffix.lower()
                if ext in FORBIDDEN_WRITE_EXTENSIONS or (ext and ext not in ALLOWED_WRITE_EXTENSIONS):
                    level2_reasons.append(("L2-UNAPPROVED-EXTENSION", f"Writing file with extension '{ext}' requires approval"))
                # Incremental authorization check: if authorized_files scope is strictly enforced
                if authorized_files is not None and len(authorized_files) > 0:
                    rel_or_abs = {str(resolved), rp.replace("\\", "/")}
                    try:
                        rel_str = str(resolved.relative_to(Path(self.workspace_root).resolve())).replace("\\", "/")
                        rel_or_abs.add(rel_str)
                    except ValueError:
                        pass
                    normalized_auth = {f.replace("\\", "/") for f in authorized_files}
                    if not (rel_or_abs & normalized_auth):
                        level2_reasons.append((
                            "L2-INCREMENTAL-SCOPE-EXPANSION",
                            f"Target file '{rp}' is outside initially authorized target_files scope",
                        ))

        if command_str:
            for pattern, rule_id in LEVEL2_COMMAND_PATTERNS:
                if re.search(pattern, command_str):
                    level2_reasons.append((rule_id, f"Command matches Level 2 approval pattern ({rule_id})"))

        if tool_name in {"delete_file", "execute_external_binary"}:
            level2_reasons.append(("L2-SENSITIVE-TOOL", f"Tool '{tool_name}' requires explicit human approval"))

        if level2_reasons:
            if req_id and req_id in approved_ids:
                return PolicyDecision(
                    level=2,
                    level_name="REQUIRES_APPROVAL",
                    action="allow",
                    target_task_state="executing",
                    tool_result_status="success",
                    reason=f"Level 2 request '{req_id}' explicitly approved by human/Meta Planner",
                    violated_rule_id=None,
                )
            rule_id, reason_text = level2_reasons[0]
            return PolicyDecision(
                level=2,
                level_name="REQUIRES_APPROVAL",
                action="hold_for_approval",
                target_task_state="awaiting_approval",
                tool_result_status="denied_by_policy",
                reason=reason_text,
                violated_rule_id=rule_id,
            )

        # ---------------------------------------------------------
        # 3. Level 1 & Level 0 Check (Workspace Write / Read Only)
        # ---------------------------------------------------------
        if tool_name in WRITE_TOOLS or declared_level == 1:
            return PolicyDecision(
                level=1,
                level_name="WORKSPACE_WRITE",
                action="allow",
                target_task_state="executing",
                tool_result_status="success",
                reason="Allowed workspace write operation within policy bounds",
            )

        return PolicyDecision(
            level=0,
            level_name="READ_ONLY",
            action="allow",
            target_task_state="executing",
            tool_result_status="success",
            reason="Allowed read-only operation within workspace",
        )


class RuleBasedRouter:
    """
    Routes tasks based on 3D assessment (risk_level, complexity, uncertainty) and credential availability.
    Danger actions (Level 3) always override routing results and cannot be downgraded.
    """

    def __init__(self, policy_engine: RiskPolicyEngine, has_gemini_credential_fn=None):
        self.policy_engine = policy_engine
        self._has_gemini_credential_fn = has_gemini_credential_fn

    def route_task(self, task: Dict[str, Any]) -> RoutingDecision:
        risk_assessment = task.get("risk_assessment") or {}
        risk_level = risk_assessment.get("risk_level", "read_only")
        complexity = risk_assessment.get("complexity", "medium")
        uncertainty = risk_assessment.get("uncertainty", "medium")
        description = f"{task.get('title', '')} {task.get('description', '')}"

        # Check if task description or target_files contain Level 3 forbidden actions
        context = task.get("context") or {}
        target_files = context.get("target_files") or []
        synthetic_req = {
            "request_id": "treq_route_probe",
            "tool_name": "write_file" if risk_level != "read_only" else "read_file",
            "parameters": {
                "path": target_files[0] if target_files else "",
                "command": description,
            },
            "risk_level": risk_level,
        }
        probe_decision = self.policy_engine.evaluate_tool_request(synthetic_req)

        # Level 3 ALWAYS overrides any routing, complexity, or confidence!
        if probe_decision.level == 3 or risk_level == "danger_external":
            return RoutingDecision(
                worker_profile="none_blocked_l3",
                reviewer_profile="none_blocked_l3",
                use_mock_worker=True,
                planning_depth="shallow",
                max_repairs=0,
                effective_risk_level="danger_external",
                requires_initial_approval=False,
                blocked_by_level3=True,
                routing_basis="Blocked immediately: Level 3 ABSOLUTE_FORBIDDEN overrides all routing",
                risk_basis=probe_decision.reason,
            )

        # Planning depth determined by Complexity & Uncertainty (separate from Risk)
        if complexity in {"high", "expert"} or uncertainty in {"high", "exploratory"}:
            planning_depth = "deep"
            max_repairs = 2
        elif complexity == "medium" or uncertainty == "medium":
            planning_depth = "standard"
            max_repairs = 2
        else:
            planning_depth = "shallow"
            max_repairs = 1

        # Worker selection: check Gemini credential availability
        has_gemini = False
        if self._has_gemini_credential_fn is not None:
            has_gemini = bool(self._has_gemini_credential_fn())
        else:
            has_gemini = bool(
                os.environ.get("GEMINI_API_KEY", "").strip()
                or os.environ.get("GOOGLE_API_KEY", "").strip()
            )

        if has_gemini:
            worker_profile = task.get("assigned_worker") or "gemini_worker"
            use_mock_worker = False
            worker_note = f"routed to live worker '{worker_profile}'"
        else:
            worker_profile = "mock_worker"
            use_mock_worker = True
            worker_note = "GEMINI_API_KEY absent; routed to Mock Worker per Phase 2 ruling"

        reviewer_profile = task.get("assigned_reviewer") or "muse_reviewer"
        requires_approval = (probe_decision.level == 2 or risk_level == "system_sensitive")
        effective_risk = "system_sensitive" if requires_approval else risk_level

        return RoutingDecision(
            worker_profile=worker_profile,
            reviewer_profile=reviewer_profile,
            use_mock_worker=use_mock_worker,
            planning_depth=planning_depth,
            max_repairs=max_repairs,
            effective_risk_level=effective_risk,
            requires_initial_approval=requires_approval,
            blocked_by_level3=False,
            routing_basis=f"complexity={complexity}, uncertainty={uncertainty} -> depth={planning_depth}; {worker_note}; reviewer={reviewer_profile}",
            risk_basis=probe_decision.reason,
        )
