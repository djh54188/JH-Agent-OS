"""JH-006 offline source mutations; only behavioural assertion failures detect.

The repository is never modified. Every run happens in a disposable copy of the
checkout in its own Python process, so the mutated module, every consumer
(orchestrator, cli, code_delivery helpers) and the test modules share a single
import graph: one class identity for each exception and one module attribute
for each ``unittest.mock.patch`` target. The previous in-memory ``exec`` into a
dict namespace left test patches on the live module, so some mutants were
judged against unpatched helpers (V-2).

Per mutation three runs of the named regression:
  1. baseline  -- unmodified copy; must pass
  2. identity  -- anchor replaced by itself through the same write path; must pass
  3. mutant    -- real replacement; must fail with BEHAVIOURAL evidence
Errors, skips, load failures, timeouts, wording-only differences and failures
the child cannot attribute never count. A red baseline is ``platform_blocked``
only with explicit platform evidence; otherwise it fails.
Exit codes: 0 all detected; 1 anything else not explained by platform evidence;
2 every row detected or platform_blocked with at least one blocked.
Each test creates its own isolated workspace and SQLite records. No APIs.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Isolated mutation harness (identical copy in verify_phase4_guards.py and
# verify_jh006_guards.py; kept self-contained per script on purpose).
# ---------------------------------------------------------------------------
import json as _json
import os as _os
import re as _re
import shutil as _shutil
import subprocess as _subprocess
import tempfile as _tempfile

_COPY_IGNORE = _shutil.ignore_patterns(".git", "data", ".test-tmp", "__pycache__", "*.pyc",
                                       ".pytest_cache")
_RESULT_MARK = "@@MUTATION_RESULT@@"
# The child runs exactly one test in the copy and reports, for every failure,
# BEHAVIOURAL EVIDENCE read from the live operands of the assertion that
# failed (never from traceback text):
#   behavioral    -- the failing comparison still fails after every piece of
#                    free text in its operands is replaced by one opaque
#                    token, or an expected exception was not raised at all;
#   message_only  -- the failure disappears once text is made opaque (equal
#                    structures that differ only in wording, a text-in-text
#                    containment check, a regex on a message, an exception of
#                    the expected type whose message no longer matches);
#   undetermined  -- anything the child cannot attribute: bare ``assert``,
#                    ``self.fail``, an assertion on a value derived from text
#                    (``assertTrue(msg.startswith(...))``), unknown methods.
# "Text" is any str that contains whitespace, is empty, or contains a message
# of an exception raised during the baseline run or this run (provenance via
# sys.monitoring RAISE events, Python >= 3.12). Only ``behavioral`` counts.
_CHILD = r'''
import ast, io, json, linecache, os, re, sys, threading, unittest
import unittest.case as _case
root, name, known_path = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path[:0] = [root, os.path.join(root, "tests")]
KNOWN = set()
if known_path:
    with open(known_path, encoding="utf-8") as fh:
        KNOWN.update(json.load(fh))
RAISED = []
_root_norm = os.path.normcase(os.path.abspath(root))
PROVENANCE = False
_mon = getattr(sys, "monitoring", None)
if _mon is not None:
    for _tool in (4, 3, 2):
        try:
            _mon.use_tool_id(_tool, "jh-mutation-provenance")
        except ValueError:
            continue
        def _on_raise(code, offset, exc):
            if isinstance(exc, AssertionError) or len(RAISED) >= 5000:
                return
            if not os.path.normcase(os.path.abspath(code.co_filename)).startswith(_root_norm):
                return
            try:
                text = str(exc)
            except Exception:
                return
            if text:
                RAISED.append(text[:4000])
        _mon.register_callback(_tool, _mon.events.RAISE, _on_raise)
        _mon.set_events(_tool, _mon.events.RAISE)
        PROVENANCE = True
        break


def _messages():
    return [m for m in KNOWN.union(RAISED) if len(m) >= 4]


def is_text(value):
    if not isinstance(value, str):
        return False
    if value == "" or any(ch.isspace() for ch in value):
        return True
    return any(value == m or (len(m) >= 8 and m in value) for m in _messages())


class _Opaque:
    __slots__ = ()
    def __eq__(self, other):
        return isinstance(other, _Opaque)
    def __hash__(self):
        return 0x7e57
    def __lt__(self, other):
        return False
    def __repr__(self):
        return "<text>"
    def __contains__(self, item):
        return isinstance(item, _Opaque)


OPAQUE = _Opaque()


def blind(value, depth=0):
    if depth > 40:
        return value
    if is_text(value):
        return OPAQUE
    if isinstance(value, dict):
        return {blind(k, depth + 1): blind(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [blind(v, depth + 1) for v in value]
    if isinstance(value, tuple):
        return tuple(blind(v, depth + 1) for v in value)
    if isinstance(value, (set, frozenset)):
        return {blind(v, depth + 1) for v in value}
    return value


def has_text(value, depth=0):
    if depth > 40:
        return True
    if is_text(value):
        return True
    if isinstance(value, dict):
        return any(has_text(k, depth + 1) or has_text(v, depth + 1) for k, v in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(has_text(v, depth + 1) for v in value)
    return False


_TEXT_METHODS = {"startswith", "endswith", "find", "rfind", "index", "rindex", "count", "lower",
                 "upper", "casefold", "strip", "lstrip", "rstrip", "split", "rsplit", "splitlines",
                 "partition", "rpartition", "replace", "format", "join", "encode", "decode",
                 "search", "match", "fullmatch", "findall", "isdigit", "isalpha"}
_TEXT_NAMES = {"message", "msg", "error_info", "reason", "exception", "traceback", "stderr",
               "stdout", "output", "detail", "details", "text", "error", "err", "exc"}
_TEXT_CALLS = {"str", "repr", "format", "ascii", "len"}
_SOURCE = {}


def _call_node(frame):
    """The ast.Call of the assertion in the test frame, located by the exact
    instruction position of the call (multi-line calls included)."""
    try:
        positions = list(frame.f_code.co_positions())
        lineno, end_lineno, col, end_col = positions[frame.f_lasti // 2]
        filename = frame.f_code.co_filename
        if filename not in _SOURCE:
            _SOURCE[filename] = ast.parse("".join(linecache.getlines(filename)))
        for node in ast.walk(_SOURCE[filename]):
            if (isinstance(node, ast.Call) and node.lineno == lineno
                    and node.end_lineno == end_lineno and node.col_offset == col
                    and node.end_col_offset == end_col):
                return node
    except Exception:
        return None
    return None


def text_derived(expr):
    """True when the expression's value is computed from text (so a bool or
    int operand may hide a wording-only difference)."""
    for node in ast.walk(expr):
        if isinstance(node, ast.JoinedStr):
            return True
        if isinstance(node, ast.Compare):
            sides = [node.left] + list(node.comparators)
            for side in sides:
                if isinstance(side, ast.Constant) and isinstance(side.value, str):
                    if any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
                        return True
                    if is_text(side.value):
                        return True
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in _TEXT_METHODS:
                return True
            if isinstance(func, ast.Name) and func.id in _TEXT_CALLS:
                return True
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "re":
                return True
        if isinstance(node, ast.Name) and node.id in _TEXT_NAMES:
            return True
        if isinstance(node, ast.Attribute) and node.attr in _TEXT_NAMES:
            return True
        if (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
                and node.slice.value in _TEXT_NAMES):
            return True
    return False


EQUAL = {"assertEqual", "assertEquals", "assertDictEqual", "assertListEqual", "assertTupleEqual",
         "assertSetEqual", "assertSequenceEqual", "assertMultiLineEqual", "assertCountEqual"}
NEGATIVE = {"assertNotEqual", "assertNotEquals", "assertIs", "assertIsNot", "assertNotIn"}
SCALAR = {"assertTrue", "assertFalse", "assertIsNone", "assertIsNotNone", "assertGreater",
          "assertGreaterEqual", "assertLess", "assertLessEqual", "assertAlmostEqual",
          "assertNotAlmostEqual", "assertIsInstance", "assertNotIsInstance"}
TEXTUAL = {"assertRegex", "assertNotRegex", "assertRegexpMatches"}
WRAPPED = EQUAL | NEGATIVE | SCALAR | TEXTUAL | {"assertIn"}
_ORIG = {}
_state = threading.local()


def _short(value):
    try:
        text = repr(value)
    except Exception:
        text = "<unrepr>"
    return text[:300]


def judge(method, args, call):
    """(verdict, reason) for one failed assertion, from its live operands."""
    unary = {"assertTrue", "assertFalse", "assertIsNone", "assertIsNotNone"}
    operands = list(args[:1]) if method in unary else list(args[:2])
    exprs = list(call.args[:len(operands)]) if call is not None else None
    if method in TEXTUAL:
        return "message_only", "regex assertion on text"
    # A non-text operand whose expression is computed from text could hide a
    # wording change (e.g. assertTrue(err.startswith("x"))): undetermined.
    for i, value in enumerate(operands):
        if has_text(value):
            continue
        if exprs is None:
            if method in SCALAR or method in NEGATIVE:
                return "undetermined", "assertion call not located in source"
            continue
        if i < len(exprs) and text_derived(exprs[i]):
            return "undetermined", "operand derived from text: " + ast.unparse(exprs[i])[:200]
    if method in EQUAL:
        try:
            _ORIG[method](_state.case, blind(args[0]), blind(args[1]))
        except _state.case.failureException:
            return "behavioral", "values still differ with text made opaque"
        except Exception as exc:
            return "undetermined", "opaque replay raised " + type(exc).__name__
        return "message_only", "operands differ only in text"
    if method == "assertIn":
        member, container = args[0], args[1]
        if isinstance(container, str):
            if is_text(container) or is_text(member):
                return "message_only", "text containment check"
            return "undetermined", "substring check on a token"
        if is_text(member):
            return "message_only", "text member"
        try:
            _ORIG[method](_state.case, blind(member), blind(container))
        except _state.case.failureException:
            return "behavioral", "member absent with text made opaque"
        except Exception as exc:
            return "undetermined", "opaque replay raised " + type(exc).__name__
        return "message_only", "member differs only in text"
    if method in NEGATIVE or method in SCALAR:
        if any(has_text(v) for v in operands):
            return "undetermined", "operand is text"
        return "behavioral", "non-text operands"
    return "undetermined", "unhandled assertion " + method


def _wrap(method):
    original = getattr(_case.TestCase, method)
    _ORIG[method] = original

    def wrapper(self, *args, **kwargs):
        depth = getattr(_state, "depth", 0)
        _state.depth = depth + 1
        try:
            return original(self, *args, **kwargs)
        except self.failureException as exc:
            if depth == 0 and not hasattr(exc, "_jh_evidence"):
                _state.case = self
                call = _call_node(sys._getframe(1))
                _state.depth = 1  # replay must not record
                try:
                    verdict, reason = judge(method, args, call)
                except Exception as err:
                    verdict, reason = "undetermined", "judge raised " + type(err).__name__
                exc._jh_evidence = {"method": method, "verdict": verdict, "reason": reason,
                                    "operands": [_short(a) for a in args[:2]],
                                    "call": ast.unparse(call)[:400] if call is not None else None}
            raise
        finally:
            _state.depth = depth
    wrapper.__name__ = method
    setattr(_case.TestCase, method, wrapper)


for _m in sorted(WRAPPED):
    if hasattr(_case.TestCase, _m):
        _wrap(_m)

_orig_exit = _case._AssertRaisesContext.__exit__


def _raises_exit(self, exc_type, exc_value, tb):
    try:
        return _orig_exit(self, exc_type, exc_value, tb)
    except AssertionError as exc:
        if exc_type is None:
            ev = ("behavioral", "expected exception was not raised")
        else:
            ev = ("message_only", "expected exception raised; only its message differs")
        exc._jh_evidence = {"method": "assertRaises", "verdict": ev[0], "reason": ev[1],
                            "operands": [_short(getattr(self, "expected", None)), _short(exc_value)],
                            "call": None}
        raise


_case._AssertRaisesContext.__exit__ = _raises_exit


class Result(unittest.TextTestResult):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.evidence = []

    def _note(self, err):
        ev = getattr(err[1], "_jh_evidence", None)
        self.evidence.append(ev or {"method": None, "verdict": "undetermined",
                                    "reason": "failure not raised by a recognised assertion"})

    def addFailure(self, test, err):
        self._note(err)
        super().addFailure(test, err)

    def addSubTest(self, test, subtest, err):
        if err is not None and issubclass(err[0], test.failureException):
            self._note(err)
        super().addSubTest(test, subtest, err)


suite = unittest.defaultTestLoader.loadTestsFromName(name)
runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0, resultclass=Result)
result = runner.run(suite)
print("@@MUTATION_RESULT@@" + json.dumps({
    "tests_run": result.testsRun,
    "failures": [text for _, text in result.failures],
    "failure_evidence": result.evidence,
    "errors": [text for _, text in result.errors],
    "skipped": [reason for _, reason in result.skipped],
    "expected_failures": len(result.expectedFailures),
    "unexpected_successes": len(result.unexpectedSuccesses),
    "provenance": PROVENANCE,
    "raised_messages": sorted(set(RAISED))[:2000],
}))
'''
CHILD_TIMEOUT_SECONDS = 600

# Platform prerequisites. A red baseline is "platform_blocked" ONLY with explicit
# evidence that the test needs another platform: a skip whose reason names that
# platform, or an error whose final exception is the import of a module / the
# launch of an executable that exists only there. Everything else on a red
# baseline (assertion failure, other errors, load failure, timeout) is a failure.
_WINDOWS_ONLY_MODULES = ("_winapi", "winreg", "msvcrt", "_overlapped", "winsound", "_msi", "nt")
_POSIX_ONLY_MODULES = ("fcntl", "pwd", "grp", "termios", "resource", "posix")
_WINDOWS_ONLY_EXES = ("cmd", "cmd.exe", "mklink", "powershell", "powershell.exe")
_SKIP_NEEDS_WINDOWS = _re.compile(r"(?i)\b(?:requires?|needs?|only)\b[^\n]*\b(?:windows|win32|ntfs|junctions?)\b"
                                  r"|\b(?:windows|win32)[- ]only\b")
_SKIP_NEEDS_POSIX = _re.compile(r"(?i)\b(?:requires?|needs?|only)\b[^\n]*\b(?:posix|linux|unix|macos|darwin)\b"
                                r"|\b(?:posix|linux|unix)[- ]only\b")


def _final_exception_line(text):
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[-1].strip() if lines else ""


def platform_evidence(run, host=None):
    """Explicit evidence that a red run is due to a missing platform prerequisite, or None."""
    host = host or sys.platform
    on_windows = host == "win32"
    if "harness_error" in run or run.get("tests_run") != 1 or run.get("failures"):
        return None
    texts = []
    if run.get("skipped") and not run.get("errors"):
        for reason in run["skipped"]:
            if not on_windows and _SKIP_NEEDS_WINDOWS.search(reason):
                texts.append("skip: " + reason)
            elif on_windows and _SKIP_NEEDS_POSIX.search(reason):
                texts.append("skip: " + reason)
            else:
                return None
        return "; ".join(texts)
    if not run.get("errors"):
        return None
    for text in run["errors"]:
        last = _final_exception_line(text)
        mod = _re.match(r"ModuleNotFoundError: No module named '([^']+)'$", last)
        exe = _re.match(r"FileNotFoundError: \[(?:Errno 2|WinError 2)\][^\n]*: '([^']+)'$", last)
        if mod and not on_windows and mod.group(1) in _WINDOWS_ONLY_MODULES:
            texts.append(last)
        elif mod and on_windows and mod.group(1) in _POSIX_ONLY_MODULES:
            texts.append(last)
        elif exe and not on_windows and exe.group(1).lower() in _WINDOWS_ONLY_EXES:
            texts.append(last)
        else:
            return None
    return "; ".join(texts)


def _mutation_tmp_root():
    """Where disposable copies live. Default is the platform temp directory;
    JH_MUTATION_TMP overrides it (e.g. a short local path on Windows)."""
    return _os.environ.get("JH_MUTATION_TMP") or None


def _copy_checkout(root, dest):
    _shutil.copytree(str(root), str(dest), ignore=_COPY_IGNORE)


def _apply_anchor(repo, rel_path, original, replacement):
    """Rewrite one file in the disposable copy. Returns an error string or None."""
    target = repo / rel_path
    raw = target.read_bytes().decode("utf-8")
    crlf = "\r\n" in raw
    anchor = original.replace("\n", "\r\n") if crlf else original
    repl = replacement.replace("\n", "\r\n") if crlf else replacement
    count = raw.count(anchor)
    if count != 1:
        return f"anchor occurs {count} times in {rel_path}"
    target.write_bytes(raw.replace(anchor, repl, 1).encode("utf-8"))
    return None


def run_test_in_copy(repo, test_id, timeout=CHILD_TIMEOUT_SECONDS, known_messages=None):
    env = dict(_os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONPATH", None)
    known_path = ""
    if known_messages:
        known_path = str(Path(repo).parent / (Path(repo).name + ".known.json"))
        Path(known_path).write_text(_json.dumps(sorted(known_messages)), encoding="utf-8")
    try:
        proc = _subprocess.run([sys.executable, "-B", "-c", _CHILD, str(repo), test_id, known_path],
                               cwd=str(repo), env=env, capture_output=True, text=True,
                               timeout=timeout)
    except _subprocess.TimeoutExpired:
        return {"harness_error": f"timeout after {timeout}s"}
    for line in proc.stdout.splitlines()[::-1]:
        if line.startswith(_RESULT_MARK):
            return _json.loads(line[len(_RESULT_MARK):])
    return {"harness_error": f"no result (exit {proc.returncode}): {proc.stderr[-2000:]}"}


def outcome_of(run):
    """Collapse one child result into a single outcome word."""
    if "harness_error" in run:
        return "harness_error"
    if run["tests_run"] != 1:
        return "not_exactly_one_test"
    if run["errors"]:
        return "error"
    if run["skipped"]:
        return "skipped"
    if run["expected_failures"] or run["unexpected_successes"]:
        return "expected_failure_marker"
    if run["failures"]:
        evidence = run.get("failure_evidence") or []
        if len(evidence) != len(run["failures"]):
            return "undetermined_failure"
        verdicts = {ev.get("verdict") for ev in evidence}
        if "behavioral" in verdicts:
            return "behavioral_failure"
        if verdicts == {"message_only"}:
            return "message_only_failure"
        return "undetermined_failure"
    return "pass"


def classify(baseline, identity, mutant, platform_note=None):
    """Verdict for one mutation from its three outcome words."""
    if "harness_error" in (baseline, identity, mutant):
        return "harness_error"
    if baseline != "pass":
        return "platform_blocked" if platform_note else "baseline_not_green"
    if identity != "pass":
        return "identity_not_green"
    if mutant == "behavioral_failure":
        return "detected"
    if mutant == "pass":
        return "survived"
    return "not_detected_" + mutant


def summarize(rows):
    counts = {}
    for row in rows:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    detected = counts.get("detected", 0)
    blocked = counts.get("platform_blocked", 0)
    if rows and detected == len(rows):
        exit_code, verdict = 0, "pass"
    elif blocked and detected + blocked == len(rows):
        exit_code, verdict = 2, "platform_blocked"
    else:
        exit_code, verdict = 1, "fail"
    return counts, detected, exit_code, verdict


def evaluate_mutations(root, cases, timeout=CHILD_TIMEOUT_SECONDS):
    rows = []
    tmp_root = _mutation_tmp_root()
    with _tempfile.TemporaryDirectory(prefix="jh-mutation-", dir=tmp_root) as tmp:
        tmp = Path(tmp)
        base_repo = tmp / "baseline"
        _copy_checkout(root, base_repo)
        baseline_cache = {}
        for index, case in enumerate(cases):
            row = {"guard": case["name"], "module": case["path"], "test": case["test"]}
            check = (base_repo / case["path"]).read_bytes().decode("utf-8")
            occurrences = check.count(case["original"].replace("\n", "\r\n")
                                      if "\r\n" in check else case["original"])
            if occurrences != 1:
                row.update(verdict="harness_error",
                           reason=f"anchor occurs {occurrences} times in {case['path']}")
                rows.append(row)
                continue
            if case["test"] not in baseline_cache:
                baseline_cache[case["test"]] = run_test_in_copy(base_repo, case["test"], timeout)
            runs = {"baseline": baseline_cache[case["test"]]}
            known = runs["baseline"].get("raised_messages") or []
            for kind, replacement in (("identity", case["original"]), ("mutant", case["replacement"])):
                repo = tmp / f"{kind}-{index}"
                _copy_checkout(root, repo)
                err = _apply_anchor(repo, case["path"], case["original"], replacement)
                runs[kind] = ({"harness_error": err} if err
                              else run_test_in_copy(repo, case["test"], timeout, known))
                _shutil.rmtree(str(repo), ignore_errors=True)
            outcomes = {kind: outcome_of(run) for kind, run in runs.items()}
            note = platform_evidence(runs["baseline"]) if outcomes["baseline"] != "pass" else None
            row.update(outcomes=outcomes, verdict=classify(outcomes["baseline"], outcomes["identity"],
                                                           outcomes["mutant"], note))
            if note:
                row["platform_evidence"] = note
            if runs["mutant"].get("failure_evidence"):
                row["mutant_evidence"] = runs["mutant"]["failure_evidence"]
            row["provenance"] = {kind: run.get("provenance") for kind, run in runs.items()}
            if row["verdict"] != "detected":
                row["raw"] = {kind: (run.get("harness_error")
                                     or (run.get("errors") or run.get("failures")
                                         or run.get("skipped") or [""])[0][-1200:])
                              for kind, run in runs.items()}
            rows.append(row)
    counts, detected, exit_code, verdict = summarize(rows)
    return {"scope": "offline_isolated_copy", "api_calls": 0, "python": sys.version.split()[0],
            "platform": sys.platform, "tmp_root": tmp_root or _tempfile.gettempdir(),
            "mutations": len(rows), "detected": detected,
            "counts": counts, "verdict": verdict, "exit_code": exit_code, "rows": rows}

R = "test_jh006_review_regressions.TestReviewedDeliveryBoundaries."
F = "test_jh006_code_delivery."
# name, module, unique source anchor, replacement, observable regression
MUTATIONS = [
    ("payload_delivery_marker", "src.orchestrator",
     '        if "_delivery" in (task_payload.get("metadata") or {}):',
     '        if False:', R + "test_input_cannot_claim_applied_or_waiting_delivery"),
    ("cli_delivery_marker", "src.cli", '    if "_delivery" in meta:',
     '    if False:', R + "test_cli_rejects_delivery_marker_before_assembly"),
    ("unregistered_host_record", "src.orchestrator",
     '        if not fixture_host_evidence_matches(self.storage, task_id, norm):',
     '        if False:', R + "test_unregistered_receipt_cannot_succeed"),
    ("raw_output_digest", "src.code_delivery",
     '            and sha256_hex(output.encode("utf-8")) == receipt["tests"]["output_digest"])',
     '            and True)', R + "test_host_output_digest_must_match_persisted_raw_output"),
    ("strict_uncertainty_bool", "src.code_delivery",
     '    if type(receipt["uncertain"]) is not bool:',
     '    if receipt["uncertain"] not in (True, False):', R + "test_uncertainty_requires_bool_not_zero_or_one"),
    ("fixed_check_script", "src.orchestrator",
     '        if norm["tests"]["check_script"] != expected_script:',
     '        if False:', R + "test_wrong_script_path_is_not_a_verified_check"),
    ("sealed_check_command", "src.code_delivery",
     '        checks = json.loads(sealed_fixture_bytes(template, "checks_rel"))',
     '        checks = json.loads((_repo_root() / template["fixture_dir"] / "checks.json").read_text(encoding="utf-8"))',
     R + "test_unsealed_check_command_is_refused"),
    ("prepare_owner", "src.orchestrator",
     '    def prepare_code_delivery(self, task_id: str, artifact_version: int) -> Dict[str, Any]:\n        owner_id = uuid.uuid4().hex\n        if not self.storage.claim_task(task_id, owner_id):',
     '    def prepare_code_delivery(self, task_id: str, artifact_version: int) -> Dict[str, Any]:\n        owner_id = uuid.uuid4().hex\n        if False:',
     R + "test_prepare_respects_existing_owner"),
    ("pending_approval", "src.orchestrator",
     '        if self.storage.get_control(task_id).get("pending_json"):',
     '        if False:', R + "test_prepare_rechecks_pending_approval"),
    ("interrupted_tool", "src.orchestrator",
     '        if any(t.get("status") in uncertain for t in self.storage.list_tool_executions(task_id)):',
     '        if False:', R + "test_prepare_rechecks_interrupted_tool"),
    ("prepare_transaction", "src.orchestrator",
     '            with self.storage.transaction():\n                return self._prepare_code_delivery_locked(task_id, artifact_version)',
     '            with __import__("contextlib").nullcontext():\n                return self._prepare_code_delivery_locked(task_id, artifact_version)',
     R + "test_prepare_and_event_rollback_together"),
    ("authorized_workspace_scope", "src.orchestrator",
     '        if unauthorized or scope_after.get(tpl["authorized_target"]) != actual_sha:',
     '        if scope_after.get(tpl["authorized_target"]) != actual_sha:',
     R + "test_extra_file_change_never_satisfies_authorized_scope"),
    ("no_model_tools", "src.orchestrator",
     '            if proposed_requests and is_code_delivery_task(task):',
     '            if False:', R + "test_fixture_model_tools_never_execute"),
    ("generation_baseline_context", "src.orchestrator",
     '        if is_code_delivery_task(task):\n            tpl = template_for_task(task)\n            context["code_template"] = {',
     '        if False:\n            tpl = template_for_task(task)\n            context["code_template"] = {',
     R + "test_generation_receives_template_and_sealed_baseline"),
    ("review_deferred_scope", "src.orchestrator",
     '                if is_code_delivery_task(task):\n                    tpl = template_for_task(task)\n                    independent_review_context["code_review_scope"] = {',
     '                if False:\n                    tpl = template_for_task(task)\n                    independent_review_context["code_review_scope"] = {',
     R + "test_review_scope_defers_unexecuted_host_checks"),
    ("fresh_cancel_state", "src.orchestrator",
     '        if task["status"] != "reviewing":\n            raise CodeDeliveryRefused(f"complete requires reviewing, got',
     '        if False:\n            raise CodeDeliveryRefused(f"complete requires reviewing, got',
     R + "test_complete_reloads_cancel_after_owner_acquisition"),
    ("prepared_version_field", "src.orchestrator",
     '            if (pinned.get("version") == artifact_version',
     '            if (pinned.get("artifact_version") == artifact_version',
     F + "TestCliDeliveryCommands.test_cli_prepare_and_complete_roundtrip"),
    ("no_extra_model_turn", "src.orchestrator",
     '    def _complete_code_delivery_locked(self, task_id, receipt, tpl, delivery):\n        budget_failed = self._check_budget_or_fail(task_id, before_call=False)',
     '    def _complete_code_delivery_locked(self, task_id, receipt, tpl, delivery):\n        budget_failed = self._check_budget_or_fail(task_id)',
     R + "test_control_completion_does_not_reserve_an_extra_model_turn"),
    ("final_deadline_gate", "src.orchestrator",
     '        stopped = self._check_budget_or_fail(task_id, before_call=False)\n        if stopped is not None:\n            return stopped\n        criteria = task["acceptance_criteria"]',
     '        stopped = None\n        if stopped is not None:\n            return stopped\n        criteria = task["acceptance_criteria"]',
     R + "test_deadline_is_rechecked_after_verification_reads"),
]


def build_cases():
    cases = []
    for name, module_name, original, replacement, test_name in MUTATIONS:
        cases.append({"name": name, "path": module_name.replace(".", "/") + ".py",
                      "original": original, "replacement": replacement, "test": test_name})
    return cases


def main(argv=None):
    report = evaluate_mutations(ROOT, build_cases())
    print(json.dumps(report, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
