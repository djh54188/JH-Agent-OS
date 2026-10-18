"""Mutation guard verification for Phase 4 batch 1 file tools.

Each mutation breaks one file-tool security guarantee. The repository itself is
never modified: every run happens in a disposable copy of the checkout, in its
own Python process, so the mutated module, its consumers (orchestrator,
acceptance) and the test module share one import graph -- one class identity
for every exception and one set of module attributes for every patch target.

Per mutation three runs of the matching test in tests/test_phase4_file_tools.py:
  1. baseline  -- unmodified copy; must pass
  2. identity  -- anchor replaced by itself through the same write path; must pass
  3. mutant    -- real replacement; must fail with BEHAVIOURAL evidence
``detected`` requires the failing assertion's live operands to prove a behaviour
change (see the harness notes below). Errors, skips, load failures, timeouts,
a red identity run, wording-only differences and failures the child cannot
attribute ("undetermined") never count.

A red baseline is ``platform_blocked`` only with explicit platform evidence
(a skip naming the platform, or a missing Windows/POSIX-only module or
executable); any other red baseline is ``baseline_not_green`` and fails.

Exit codes: 0 every guard detected; 1 anything else that is not explained by
platform evidence (survivor, not detected, red baseline, harness problem);
2 every row is detected or platform_blocked and at least one is blocked.
Copies live under the platform temp directory unless JH_MUTATION_TMP is set.
"""

import ast
import json
from pathlib import Path
import sys

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


# Source proof for scalar operands (R3). A bool/int/float/None operand carries no
# text of its own, so the opaque replay cannot see a wording-only difference that
# the TEST computed away (good = msg.endswith(...); self.assertTrue(good)). Such
# an operand counts only when its expression is PROVEN to come from the system
# under test, not from test-side computation; anything unproven is undetermined.
# This is a deliberately small, local check, not a data-flow analyser:
#   - non-str constants;
#   - attribute/subscript reads (constant or name keys) whose base is a with-as
#     name, or a name bound once by a simple assignment from a proven call /
#     object construction / another read; test-built literals are not, and
#     neither is anything read off ``self`` (the test may have stored text
#     work there);
#   - calls whose callee (a pure name/attribute chain, resolved in the frame) is
#     defined under <root>/src, a class constructor, or one of the pathlib
#     predicates exists/is_file/is_dir/is_symlink on a Path;
#   - not / and / or / comparisons / arithmetic over proven parts with no str;
#   - a bare local name bound exactly once by a simple assignment of a proven
#     expression earlier in the same function; a module-level int/float/None.
# Test helpers (functions under <root>/tests), str methods, builtins such as
# len/bool/any, re.*, tuple-unpacking, loop targets and parameters are unproven.
_SRC_DIR = os.path.normcase(os.path.join(os.path.abspath(root), "src")) + os.sep
_PATH_PREDICATES = {"exists", "is_file", "is_dir", "is_symlink"}


def contains_scalar(value, depth=0):
    if value is None or isinstance(value, (bool, int, float)):
        return True
    if depth > 40:
        return True
    if isinstance(value, dict):
        return any(contains_scalar(v, depth + 1) for v in value.values())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(contains_scalar(v, depth + 1) for v in value)
    return False


def _function_node(frame):
    code = frame.f_code
    tree = _SOURCE.get(code.co_filename)
    if tree is None:
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == code.co_name:
            first = node.decorator_list[0].lineno if node.decorator_list else node.lineno
            if code.co_firstlineno in (first, node.lineno):
                return node
    return None


def _bindings(func, name):
    """('assign', value_node, lineno) / ('with', None, lineno) / ('other', ...)."""
    found = []
    args = func.args
    for a in args.posonlyargs + args.args + args.kwonlyargs + [args.vararg, args.kwarg]:
        if a is not None and a.arg == name:
            found.append(("self" if name == "self" and a is (args.posonlyargs + args.args)[0] else "other",
                          None, func.lineno))
    for node in ast.walk(func):
        if node is not func and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            if getattr(node, "name", None) == name:
                found.append(("other", None, node.lineno))
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    found.append(("assign", node.value, node.lineno))
                elif any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(t)):
                    found.append(("other", None, node.lineno))
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign, ast.NamedExpr)):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                found.append(("other", None, node.lineno))
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            if any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(node.target)):
                found.append(("other", None, getattr(node, "lineno", 0)))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                v = item.optional_vars
                if isinstance(v, ast.Name) and v.id == name:
                    found.append(("with", None, node.lineno))
                elif v is not None and any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(v)):
                    found.append(("other", None, node.lineno))
        elif isinstance(node, ast.ExceptHandler) and node.name == name:
            found.append(("other", None, node.lineno))
    return found


def _resolve_chain(expr, frame):
    """Evaluate a pure Name/Attribute chain in the frame; (ok, object)."""
    parts = []
    while isinstance(expr, ast.Attribute):
        parts.append(expr.attr)
        expr = expr.value
    if not isinstance(expr, ast.Name):
        return False, None
    scope = [frame.f_locals, frame.f_globals, frame.f_builtins]
    for ns in scope:
        if expr.id in ns:
            obj = ns[expr.id]
            break
    else:
        return False, None
    try:
        for attr in reversed(parts):
            obj = getattr(obj, attr)
    except Exception:
        return False, None
    return True, obj


def _proven_call(node, frame):
    ok, fn = _resolve_chain(node.func, frame)
    if not ok:
        return False, "callee is not a plain name chain: " + ast.unparse(node.func)[:120]
    if isinstance(fn, type):
        return True, "constructor"
    receiver = getattr(fn, "__self__", None)
    name = getattr(fn, "__name__", "")
    if name in _PATH_PREDICATES and receiver is not None:
        import pathlib
        if isinstance(receiver, pathlib.PurePath):
            return True, "pathlib predicate"
    target = getattr(fn, "__func__", fn)
    code = getattr(target, "__code__", None)
    if code is None:
        return False, "callee has no source: " + ast.unparse(node.func)[:120]
    path = os.path.normcase(os.path.abspath(code.co_filename))
    if path.startswith(_SRC_DIR):
        return True, "system call"
    return False, "callee outside src: " + ast.unparse(node.func)[:120]


def _proven_base(expr, frame, func, line, depth):
    """Base of an attribute/subscript read: an object, not a test-built value."""
    if isinstance(expr, ast.Name):
        if expr.id == "self":
            # R4: an attribute of self holds whatever the test stored there
            # (self.good = text.endswith(...)); nothing proves its source.
            return False, "attribute of self: source not proven"
        if func is None:
            return False, "enclosing function not located"
        binds = _bindings(func, expr.id)
        if not binds:
            return False, "name not bound in the test function: " + expr.id
        if len(binds) == 1 and binds[0][0] == "with":
            return True, "with-as object"
        if len(binds) == 1 and binds[0][0] == "assign" and binds[0][2] < line:
            return _proven_base(binds[0][1], frame, func, binds[0][2], depth + 1)
        return False, "name not bound once by a simple assignment: " + expr.id
    if depth > 6:
        return False, "too deep"
    if isinstance(expr, (ast.Attribute, ast.Subscript)):
        return proven(expr, frame, func, line, depth + 1)
    if isinstance(expr, ast.Call):
        return _proven_call(expr, frame)
    return False, "unproven base: " + type(expr).__name__


def proven(expr, frame, func, line, depth=0):
    """(ok, reason): expression value provably comes from the system, not test text work."""
    if depth > 6:
        return False, "too deep"
    if isinstance(expr, ast.Constant):
        return (not isinstance(expr.value, (str, bytes))), "constant"
    if isinstance(expr, (ast.Attribute, ast.Subscript)):
        node = expr
        while isinstance(node, (ast.Attribute, ast.Subscript)):
            if isinstance(node, ast.Subscript) and not isinstance(node.slice, (ast.Constant, ast.Name)):
                return False, "computed subscript"
            node = node.value
        if isinstance(node, ast.Call):
            return _proven_call(node, frame)
        return _proven_base(node, frame, func, line, depth)
    if isinstance(expr, ast.Call):
        return _proven_call(expr, frame)
    if isinstance(expr, ast.Name):
        if func is None:
            return False, "enclosing function not located"
        binds = _bindings(func, expr.id)
        if not binds:
            value = frame.f_globals.get(expr.id, frame)
            if value is None or (isinstance(value, (int, float)) and not isinstance(value, bool)):
                return True, "module constant"
            return False, "name not bound in the test function: " + expr.id
        if len(binds) == 1 and binds[0][0] == "assign" and binds[0][2] < line:
            return proven(binds[0][1], frame, func, binds[0][2], depth + 1)
        return False, "local name not bound once by a simple assignment: " + expr.id
    if isinstance(expr, ast.UnaryOp):
        return proven(expr.operand, frame, func, line, depth + 1)
    if isinstance(expr, (ast.BoolOp, ast.Compare, ast.BinOp)):
        parts = (expr.values if isinstance(expr, ast.BoolOp)
                 else [expr.left] + list(expr.comparators) if isinstance(expr, ast.Compare)
                 else [expr.left, expr.right])
        for part in parts:
            ok, why = proven(part, frame, func, line, depth + 1)
            if not ok:
                return False, why
        return True, "combination of proven parts"
    return False, "unproven expression: " + type(expr).__name__


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


def judge(method, args, call, frame=None):
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
            if method in SCALAR or method in NEGATIVE or contains_scalar(value):
                return "undetermined", "assertion call not located in source"
            continue
        if i < len(exprs) and text_derived(exprs[i]):
            return "undetermined", "operand derived from text: " + ast.unparse(exprs[i])[:200]
        if contains_scalar(value):
            if i >= len(exprs) or frame is None:
                return "undetermined", "scalar operand without a located expression"
            ok, why = proven(exprs[i], frame, _function_node(frame), call.lineno)
            if not ok:
                return "undetermined", "scalar operand source not proven: " + why
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
        return "behavioral", "non-text operands from proven sources"
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
                frame = sys._getframe(1)
                call = _call_node(frame)
                _state.depth = 1  # replay must not record
                try:
                    verdict, reason = judge(method, args, call, frame)
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

TEST_FILE = "tests/test_phase4_file_tools.py"

MUTATIONS = [
    # Workspace containment (drive/prefix) removed -> out-of-bounds write succeeds.
    ("workspace_containment_removed", "src/file_tools.py",
     '                raise PathBoundaryError(f"path escapes workspace: \'{raw}\'")',
     "                pass",
     "test_out_of_bounds_paths_refused"),
    # Ancestor reparse (junction) check removed -> junction escape succeeds.
    ("junction_ancestor_check_removed", "src/file_tools.py",
     '            if _is_reparse_point(step):\n                raise PathBoundaryError(f"ancestor component is a link/junction: \'{step}\' (raw: \'{raw}\')")',
     "            pass",
     "test_real_junction_escape_refused"),
    # NOTE: the post-open realpath containment re-check is a compensating control
    # for the sub-millisecond check-to-open race window; it is NOT mutation-tested
    # because the pre-open final-component reparse check already deterministically
    # refuses the planted-junction scenario (see test_race_planted_junction_refused).
    # Apply target-version confirmation removed -> concurrent edit overwritten.
    ("apply_target_version_check_removed", "src/file_tools.py",
     '        if current_sha != target_pre_sha256:\n            raise PathBoundaryError(\n                f"apply refused: target changed since review (current={current_sha}, expected={target_pre_sha256})")',
     "        if False:\n            pass",
     "test_target_changed_between_review_and_apply"),
    # Apply staged-vs-reviewed hash confirmation removed -> tampered content applied.
    ("apply_staged_hash_check_removed", "src/file_tools.py",
     '        if staged_sha != content_sha256:\n            raise PathBoundaryError(\n                f"apply refused: staged content sha {staged_sha[:12]}... != reviewed {content_sha256[:12]}...")',
     "        if False:\n            pass",
     "test_apply_refuses_tampered_staged_content"),
    # Orchestrator apply gate removed -> revise verdict also applies the write.
    ("apply_without_pass_verdict", "src/orchestrator.py",
     '                if review_json["verdict"] == "pass":',
     '                if True:',
     "test_review_rejection_blocks_apply"),
    # ARCH-01: staging directory reparse check removed -> outside victim truncated/modified.
    ("arch01_staging_dir_reparse_removed", "src/file_tools.py",
     '                if _is_reparse_point(step):\n                    raise PathBoundaryError(f"ancestor component is a link/junction: \'{step}\'")',
     "                pass",
     "test_arch01_staging_junction_does_not_truncate_outside_file"),
    # ARCH-03: locked-handle pre_sha re-verification removed -> concurrent edit overwritten.
    ("arch03_locked_handle_version_check_removed", "src/file_tools.py",
     '                        if live_sha != target_pre_sha256:\n                            raise PathBoundaryError(\n                                f"apply refused: target changed since review "\n                                f"(current={live_sha}, expected={target_pre_sha256})")',
     "                        if False:\n                            pass",
     "test_arch03_concurrent_target_edit_during_backed_up_preserved"),
    # ARCH-05: staged snapshot vs stage_write record consistency check removed.
    ("arch05_staged_snapshot_binding_removed", "src/orchestrator.py",
     '                self._verify_staged_snapshots_match_tool_records(task_id, snapshots)',
     '                pass',
     "test_arch05_reviewed_content_mismatch_blocked_at_snapshot_and_apply"),
    # R1: workspace constructor reparse rejection removed -> junction workspace accepted.
    ("workspace_ctor_reparse_removed", "src/file_tools.py",
     '        if _is_reparse_point(raw_ws):\n            raise PathBoundaryError(f"workspace root is a link/junction: \'{raw_ws}\'")',
     "        pass",
     "test_junction_workspace_root_rejected"),
    # H1: unique staged naming removed -> same-basename second stage overwrites first.
    ("staged_unique_name_removed", "src/file_tools.py",
     '        staged_path = staging_root / f"{uuid.uuid4().hex}_{Path(target_path).name}"\n        fd = self._open_verified(staged_path, mode="create_exclusive")',
     '        staged_path = staging_root / Path(target_path).name\n        fd = self._open_verified(staged_path, mode="write")',
     "test_same_basename_targets_get_distinct_staged_files"),
    # NOTE: the no-fallback snapshot-entry check in _apply_staged_writes is a
    # second-layer defence: the pre-review _verify_staged_snapshots_match check
    # already guarantees every reviewed ref has a matching snapshot, so no
    # reachable single-point mutation there can turn a test red. It is covered
    # by code review (G2-F2) rather than a mutation guard.
    # G2-F2b: recovered 'applied' hash vs reviewed snapshot comparison removed.
    ("recovered_applied_hash_check_removed", "src/orchestrator.py",
     '                    if recovery["target_sha256"] != reviewed_sha:\n                        raise ApplyBlockedError(\n                            f"recovered target hash mismatch (disk={recovery[\'target_sha256\']}, "\n                            f"reviewed={reviewed_sha})")',
     '                    if False:\n                        pass',
     "test_recovered_applied_hash_must_match_reviewed_snapshot"),
    # Acceptance must judge the CURRENT reviewed stage record, not the first in history.
    ("acceptance_current_turn_judgment_removed", "src/acceptance.py",
     '        for t in reversed(matching):\n            staged_path_rel = (t["result"]["structured_data"] or {}).get("staged_path")\n            if staged_path_rel in reviewed_refs:\n                judged = t\n                break',
     '        judged = matching[0]',
     "test_stale_turn_staged_write_does_not_block_current_apply"),
]


def _test_ids():
    """Map each test method name to its unittest id via the test module's AST."""
    tree = ast.parse((ROOT / TEST_FILE).read_text(encoding="utf-8"))
    ids = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name.startswith("test_"):
                    ids.setdefault(item.name, f"test_phase4_file_tools.{node.name}.{item.name}")
    return ids


def build_cases():
    ids = _test_ids()
    cases = []
    for name, rel_path, original, replacement, test_name in MUTATIONS:
        cases.append({"name": name, "path": rel_path, "original": original,
                      "replacement": replacement, "test": ids.get(test_name, test_name)})
    return cases


def main(argv=None):
    report = evaluate_mutations(ROOT, build_cases())
    print(json.dumps(report, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
