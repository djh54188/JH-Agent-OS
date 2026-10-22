"""Inspect accepted test changes and run only the two relevant mutation targets."""
import ast
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest import mock

ROOT = Path(__file__).resolve().parent


def methods(path):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    return {c.name + '.' + f.name: ast.dump(f, include_attributes=False)
            for c in tree.body if isinstance(c, ast.ClassDef)
            for f in c.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}


def main():
    old = methods(ROOT.parent / 'hark12-independent-review-20261022/candidate/tests/test_hark12_gemini_admission.py')
    new = methods(ROOT / 'candidate/tests/test_hark12_gemini_admission.py')
    assert not (set(old) - set(new))
    changed = [k for k in old if old[k] != new[k]]
    assert changed == [], changed
    added = sorted(set(new) - set(old))
    assert added == ['TestR1RegistryState.test_tail_cut_registry_refuses_reader'], added
    temp = ROOT / 'mutation-controlled-temp'
    temp.mkdir(exist_ok=False)
    def ordinary_temp(suffix=None, prefix=None, dir=None):
        p = Path(dir or temp) / ((prefix or 'tmp') + uuid.uuid4().hex + (suffix or ''))
        p.mkdir()
        return str(p)
    rows = []
    log = io.StringIO()
    for name, filename, guard in [('h12', 'test_hark12_gemini_admission.py', 'registry_prefix_intact'),
                                   ('h13', 'test_hark13_registry_loss.py', 'unaccounted_run_traces')]:
        spec = importlib.util.spec_from_file_location(name, ROOT / 'candidate/tests' / filename)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        weak, target = mod.WEAKENINGS[guard]
        cls, method = target.split('.')
        outcomes = []
        with mock.patch.object(tempfile, 'mkdtemp', ordinary_temp):
            for mode in ('control', 'weakened'):
                runner = unittest.TextTestRunner(stream=log, verbosity=2)
                case = getattr(mod, cls)(method)
                if mode == 'weakened':
                    with mock.patch.object(mod.gem, guard, weak): result = runner.run(case)
                else: result = runner.run(case)
                outcomes.append({'mode': mode, 'run': result.testsRun,
                                 'failures': len(result.failures), 'errors': len(result.errors),
                                 'skipped': len(result.skipped)})
        assert outcomes[0] == {'mode': 'control', 'run': 1, 'failures': 0, 'errors': 0, 'skipped': 0}, outcomes
        assert outcomes[1]['failures'] > 0 and outcomes[1]['errors'] == 0, outcomes
        rows.append({'guard': guard, 'target': target, 'outcomes': outcomes})
    report = {'accepted_test_methods_changed': changed, 'accepted_test_methods_removed': [],
              'added_methods': added, 'cases': rows,
              'temp_workaround': 'review runner ordinary mkdir; original candidate source/test bytes unchanged',
              'real_gemini_processes': 0, 'model_calls': 0}
    (ROOT / 'mutation-targets.txt').write_text(log.getvalue(), encoding='utf-8')
    (ROOT / 'mutation-targets.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__': main()
