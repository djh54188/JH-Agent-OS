"""Offline boundary probe: settled layout changes BEFORE the deferred worker runs."""
import argparse
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
from unittest import mock

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    repo, out = args.repo.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    def mkdtemp(suffix=None, prefix=None, dir=None):
        parent = Path(dir) if dir else out / 'tmp'
        parent.mkdir(parents=True, exist_ok=True)
        result = parent / ((prefix or 'tmp') + uuid.uuid4().hex + (suffix or ''))
        result.mkdir()
        return str(result)
    if os.name == 'nt':
        tempfile.mkdtemp = mkdtemp
    sys.path[:0] = [str(repo), str(repo / 'tests')]
    import test_hark06_session_probe as fx
    from src.session_probe import CLAIM_NAME
    if os.name != 'nt':
        fx.codex_mod.is_special_path = fx._posix_evidence_seam
    rows = []
    def link_dir(link, target):
        if os.name == 'nt':
            result = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(link), str(target)],
                                    capture_output=True, timeout=10)
            if result.returncode:
                raise RuntimeError('junction creation failed: ' + str(result.returncode))
        else:
            link.symlink_to(target, target_is_directory=True)
    for kind in ('root', 'root_with_claim', 'ancestor', 'raw', 'ws'):
        base = out / kind
        probe_dir = base / 'parent' / 'probe' if kind == 'ancestor' else base / 'probe'
        rig = fx.Rig(base, probe_dir=probe_dir, runner='deferred')
        prepared = rig.api.prepare_session_probe({})
        started = rig.api.start_session_probe({'probe_id': prepared['probe_id'], 'confirm': True})
        target = base / 'outside'
        target.mkdir()
        if kind == 'root_with_claim':
            (target / CLAIM_NAME).write_bytes((probe_dir / CLAIM_NAME).read_bytes())
        if kind == 'ancestor':
            (target / 'probe').mkdir()
        if kind in ('root', 'root_with_claim', 'ancestor'):
            original = probe_dir if kind.startswith('root') else probe_dir.parent
            original.rename(base / 'parked-original')
            link_dir(original, target)
        else:
            link_dir(probe_dir / kind, target)
        before = sorted(str(p.relative_to(target)) for p in target.rglob('*'))
        before_claim = json.loads((target/CLAIM_NAME).read_bytes())['state'] if (target/CLAIM_NAME).exists() else None
        for job in rig.deferred:
            job()
        rows.append({'case': kind, 'accepted': started.get('accepted'),
                     'thread_starts': rig.thread_starts(),
                     'last_error': {k:v for k,v in (rig.api.session_probe_status()['last_error'] or {}).items()
                                    if k in ('type', 'code')},
                     'target_before': before,
                     'target_claim_before': before_claim,
                     'target_claim_after': json.loads((target/CLAIM_NAME).read_bytes())['state'] if (target/CLAIM_NAME).exists() else None,
                     'target_after': sorted(str(p.relative_to(target)) for p in target.rglob('*')),
                     'target_file_sizes': {str(p.relative_to(target)): p.stat().st_size
                                           for p in target.rglob('*') if p.is_file()}})
    spec = importlib.util.spec_from_file_location('reviewed_guard07', repo / 'scripts/verify_hark06_session_probe_guards.py')
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    classifications = []
    for active in (False, True):
        for state in ('passed', 'assertion', 'error', 'skipped', 'zero_tests'):
            verdict, counted, extra = guard.classify_python_row(
                'replay_check_removed', 'passed', state, active,
                {'thread_starts': 1, 'first_start_accepted': True, 'second_start': 'probe_not_prepared'})
            classifications.append({'active': active, 'mutated': state, 'verdict': verdict,
                                    'counted': counted, 'extra': extra})
    states = iter(['passed', 'assertion'])
    buf = io.StringIO()
    with mock.patch.object(guard, 'PY_MUTATIONS', [('replay_check_removed', contextlib.nullcontext, 'observed-outcomes')]), \
         mock.patch.object(guard, 'JS_MUTATIONS', []), mock.patch.object(guard, 'NODE', 'unused'), \
         mock.patch.object(guard, 'run_case', lambda name: next(states)), \
         mock.patch.object(guard, 'run_js', lambda path: {'scenarios': {}}), \
         mock.patch.object(guard, 'replay_diagnostic', lambda: {'thread_starts': 1,
             'first_start_accepted': True, 'second_start': 'probe_not_prepared'}), \
         contextlib.redirect_stdout(buf):
        code = guard.main()
    report = json.loads(buf.getvalue())
    result = {'platform': sys.platform, 'python': sys.version, 'model_calls': 0,
              'real_app_server_calls': 0, 'rows': rows, 'classifications': classifications,
              'inactive_assertion_main': {'exit_code': code, 'verdict': report['verdict'],
                                          'row': report['rows'][0]}}
    (out/'results.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))

if __name__ == '__main__':
    main()
