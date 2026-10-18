"""Constructor residual false-positive probe (analysis only, baseline fe0476d).

Builds a throw-away synthetic repository in a temp directory, applies a
WORDING-ONLY mutation (exception message "value over limit" ->
"value beyond limit"; guard condition, raised type and every business result
unchanged) and runs 12 synthetic tests (bool/int/float/str x direct text check /
test helper / local variable) through evaluate_mutations of both verifiers:

  state "on"  -- the verifier exactly as committed at fe0476d (text_derived on);
  state "off" -- control: same source with the single line
                 ``if i < len(exprs) and text_derived(exprs[i]):`` -> ``if False:``
                 loaded from an in-memory copy (the repository file is untouched).

Two control rows confirm the mutation is wording-only: the business result
(consumer.allowed(11) is False, status == "blocked") still passes under the
mutant, so they must be "survived".

Every "detected" row among the 12 is a false positive by construction (only
wording changed). error / skip / red baseline / undetermined never count.
Usage:  python probe_constructor.py <repo_root_at_fe0476d>  > out.json
"""
import importlib.util, json, shutil, sys, tempfile, types
from pathlib import Path

GUARD = '''\
class Refused(Exception):
    pass


def limit():
    return 10


def check(value):
    if value > limit():
        raise Refused("value over limit")
    return value
'''
CONSUMER = '''\
from src import guard
from src.guard import Refused


def run(value):
    try:
        guard.check(value)
    except Refused as exc:
        return "blocked: " + str(exc)
    return "allowed"


def status(value):
    try:
        guard.check(value)
    except Refused:
        return "blocked"
    return "allowed"


def allowed(value):
    return status(value) == "allowed"
'''
TESTS = '''\
import unittest
from src import consumer


def _ends(text):
    return text.endswith("over limit")


def _hits(text):
    return text.count("over limit")


class TestCtor(unittest.TestCase):
    # bool
    def test_bool_direct(self):
        self.assertTrue(bool(consumer.run(11).endswith("over limit")))

    def test_bool_helper(self):
        self.assertTrue(bool(_ends(consumer.run(11))))

    def test_bool_local(self):
        hit = consumer.run(11).endswith("over limit")
        self.assertTrue(bool(hit))

    # int
    def test_int_direct(self):
        self.assertEqual(int(consumer.run(11).count("over limit")), 1)

    def test_int_helper(self):
        self.assertEqual(int(_hits(consumer.run(11))), 1)

    def test_int_local(self):
        hits = consumer.run(11).count("over limit")
        self.assertEqual(int(hits), 1)

    # float
    def test_float_direct(self):
        self.assertEqual(float(consumer.run(11).count("over limit")), 1.0)

    def test_float_helper(self):
        self.assertEqual(float(_hits(consumer.run(11))), 1.0)

    def test_float_local(self):
        hits = consumer.run(11).count("over limit")
        self.assertEqual(float(hits), 1.0)

    # str
    def test_str_direct(self):
        self.assertEqual(str(consumer.run(11).endswith("over limit")), "True")

    def test_str_helper(self):
        self.assertEqual(str(_ends(consumer.run(11))), "True")

    def test_str_local(self):
        hit = consumer.run(11).endswith("over limit")
        self.assertEqual(str(hit), "True")


class TestBusinessControl(unittest.TestCase):
    def test_still_refused(self):
        self.assertFalse(consumer.allowed(11))

    def test_status_still_blocked(self):
        self.assertEqual(consumer.status(11), "blocked")
'''
ORIGINAL = '        raise Refused("value over limit")'
WORDING = '        raise Refused("value beyond limit")'
CASES12 = ["bool_direct", "bool_helper", "bool_local", "int_direct", "int_helper", "int_local",
           "float_direct", "float_helper", "float_local", "str_direct", "str_helper", "str_local"]
CONTROLS = ["still_refused", "status_still_blocked"]
OFF_ANCHOR = "if i < len(exprs) and text_derived(exprs[i]):"


def make_repo(tmp):
    (tmp / "src").mkdir(); (tmp / "tests").mkdir()
    (tmp / "src" / "__init__.py").write_text("", encoding="utf-8")
    (tmp / "src" / "guard.py").write_text(GUARD, encoding="utf-8")
    (tmp / "src" / "consumer.py").write_text(CONSUMER, encoding="utf-8")
    (tmp / "tests" / "test_probe.py").write_text(TESTS, encoding="utf-8")


def load(path, off):
    src = path.read_text(encoding="utf-8")
    if off:
        assert src.count(OFF_ANCHOR) == 1, "off anchor not unique"
        src = src.replace(OFF_ANCHOR, "if False:", 1)
    mod = types.ModuleType(("off_" if off else "on_") + path.stem)
    mod.__file__ = str(path)
    exec(compile(src, str(path), "exec"), mod.__dict__)
    return mod


def cases():
    out = []
    for n in CASES12:
        out.append({"name": n, "path": "src/guard.py", "original": ORIGINAL, "replacement": WORDING,
                    "test": "test_probe.TestCtor.test_" + n})
    for n in CONTROLS:
        out.append({"name": "control_" + n, "path": "src/guard.py", "original": ORIGINAL,
                    "replacement": WORDING, "test": "test_probe.TestBusinessControl.test_" + n})
    return out


def main():
    root = Path(sys.argv[1]).resolve()
    result = {"baseline_commit": "fe0476d5807d6d562a1c290fe1b14623e53d1a46",
              "python": sys.version.split()[0], "platform": sys.platform,
              "mutation": {"path": "src/guard.py", "original": ORIGINAL, "replacement": WORDING},
              "runs": {}}
    for script in ("verify_phase4_guards", "verify_jh006_guards"):
        for state in ("on", "off"):
            mod = load(root / "scripts" / f"{script}.py", state == "off")
            tmp = Path(tempfile.mkdtemp(prefix="jh-ctor-probe-"))
            try:
                make_repo(tmp)
                before = {str(p.relative_to(tmp)): p.read_bytes() for p in tmp.rglob("*") if p.is_file()}
                rep = mod.evaluate_mutations(tmp, cases())
                after = {str(p.relative_to(tmp)): p.read_bytes() for p in tmp.rglob("*") if p.is_file()}
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            rows = rep["rows"]
            fp = [r["guard"] for r in rows if r["guard"] in CASES12 and r["verdict"] == "detected"]
            result["runs"][f"{script}:{state}"] = {
                "synthetic_repo_unchanged": before == after,
                "false_positives_in_sample": len(fp), "false_positive_cases": fp,
                "verdicts": {r["guard"]: r["verdict"] for r in rows},
                "rows": rows}
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
