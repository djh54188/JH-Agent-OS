"""Independent offline review probe. Product code is not modified; no model/process transport."""
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
    def inherited_mkdtemp(suffix=None, prefix=None, dir=None):
        parent = Path(dir) if dir else out / 'tmp'
        parent.mkdir(parents=True, exist_ok=True)
        result = parent / ((prefix or 'tmp') + uuid.uuid4().hex + (suffix or ''))
        result.mkdir()
        return str(result)
    if os.name == 'nt':
        tempfile.mkdtemp = inherited_mkdtemp
    sys.path[:0] = [str(repo), str(repo / 'tests')]
    import test_hark06_session_probe as fixtures
    from src.app_api import ApiError, AppInterface
    from src.session_probe import ProbeConfig
    if os.name != 'nt':
        # Same declared test-only POSIX seam, never active in Windows probes.
        fixtures.codex_mod.is_special_path = fixtures._posix_evidence_seam
    results = []
    def record(name, rig, **facts):
        results.append(dict(case=name, thread_starts=rig.thread_starts(), **facts))
    def start(api):
        prepared = api.prepare_session_probe({})
        return api.start_session_probe({'probe_id': prepared['probe_id'], 'confirm': True})
    def attempt(api):
        try:
            return {'result': start(api)}
        except ApiError as exc:
            return {'refused': exc.code}

    rig = fixtures.Rig(out / 'positive')
    start(rig.api)
    record('single_source_positive', rig, outcome=rig.api.session_probe_status()['latest']['outcome'])
    healthy_history = rig.registry().read_text(encoding='utf-8')

    rig = fixtures.Rig(out / 'one-object', runner='deferred',
                       transport_kw={'timeout_method': 'thread/start'})
    first = attempt(rig.api); second = attempt(rig.api)
    for job in rig.deferred: job()
    record('same_object_exclusion_control', rig, first=first, second=second)

    rig = fixtures.Rig(out / 'two-objects', runner='deferred',
                       transport_kw={'timeout_method': 'thread/start'})
    api2 = rig.make_api()
    first = attempt(rig.api); second = attempt(api2)
    rig.deferred[0]()
    blocked_before_second_worker = not api2.session_probe_status()['available']
    if len(rig.deferred) > 1: rig.deferred[1]()
    status = api2.session_probe_status()
    record('two_service_objects_one_source', rig, first=first, second=second,
           source_blocked_before_second_worker=blocked_before_second_worker,
           unresolved=len([b for b in status['blockers'] if b['code'] == 'probe_unresolved']),
           registry_state=status['registry']['state'])

    rig = fixtures.Rig(out / 'reopen-unresolved', transport_kw={'timeout_method': 'thread/start'})
    start(rig.api)
    record('reopen_unresolved_control', rig, second=attempt(rig.make_api()))

    rig = fixtures.Rig(out / 'same-file-control')
    exact = AppInterface(db_path=rig.tmp/'none.sqlite3', runs_path=rig.registry(),
                         session_probe=rig.config, probe_adapter_factory=rig.factory,
                         probe_runtime_check=lambda: None, probe_runner=rig.runner)
    record('exact_history_source_control', rig, attempt=attempt(exact))

    rig = fixtures.Rig(out / 'history-hardlink', history_text='')
    rig.probe_dir.mkdir()
    os.link(rig.history, rig.registry())
    before = rig.history.read_bytes()
    same_file = os.path.samefile(rig.history, rig.registry())
    result = attempt(rig.api)
    record('history_hardlink_alias', rig, same_file=same_file, attempt=result,
           history_bytes_unchanged=rig.history.read_bytes() == before,
           history_bytes_after=len(rig.history.read_bytes()))

    rig = fixtures.Rig(out / 'history-hardlink-healthy', history_text=healthy_history)
    rig.probe_dir.mkdir()
    os.link(rig.history, rig.registry())
    before = rig.history.read_bytes()
    result = attempt(rig.api)
    record('healthy_history_hardlink_alias', rig, attempt=result,
           history_bytes_unchanged=rig.history.read_bytes() == before,
           history_was_nonempty=bool(before))

    for kind in ('root', 'ancestor', 'raw', 'ws'):
        base = out / ('junction-' + kind)
        target = base / 'outside'
        target.mkdir(parents=True)
        if kind == 'root':
            link = base / 'probe'
            probe_dir = link
        elif kind == 'ancestor':
            link = base / 'alias'
            probe_dir = link / 'probe'
        else:
            probe_dir = base / 'probe'
            probe_dir.mkdir()
            link = probe_dir / kind
        try:
            if os.name == 'nt':
                made = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(link), str(target)],
                                      capture_output=True, timeout=10)
                if made.returncode:
                    results.append({'case': 'junction_' + kind, 'unverified': 'creation failed',
                                    'returncode': made.returncode})
                    continue
            else:
                link.symlink_to(target, target_is_directory=True)
            rig = fixtures.Rig(base, probe_dir=probe_dir)
            result = attempt(rig.api)
            record('junction_' + kind, rig, attempt=result, islink=os.path.islink(link),
                   isjunction=os.path.isjunction(link) if os.name == 'nt' else None,
                   files_in_outside=sorted(str(p.relative_to(target)) for p in target.rglob('*') if p.is_file()),
                   directories_in_outside=sorted(str(p.relative_to(target)) for p in target.rglob('*') if p.is_dir()))
        except Exception as exc:
            results.append({'case': 'junction_' + kind, 'probe_error': type(exc).__name__, 'detail': str(exc)})

    # Exercise the verifier's own result classification with a fixed diagnostic.
    spec = importlib.util.spec_from_file_location('reviewed_guard', repo / 'scripts/verify_hark06_session_probe_guards.py')
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    for state in ('passed', 'assertion', 'error', 'skipped', 'zero_tests'):
        states = iter(['passed', state])
        row = ('replay_check_removed', contextlib.nullcontext, 'synthetic-outcome')
        buf = io.StringIO()
        with mock.patch.object(guard, 'PY_MUTATIONS', [row]), \
             mock.patch.object(guard, 'JS_MUTATIONS', []), \
             mock.patch.object(guard, 'NODE', 'not-used'), \
             mock.patch.object(guard, 'run_case', lambda name: next(states)), \
             mock.patch.object(guard, 'run_js', lambda path: {'scenarios': {}}), \
             mock.patch.object(guard, 'replay_diagnostic', lambda: {'thread_starts': 1, 'second_start': 'probe_not_prepared'}), \
             contextlib.redirect_stdout(buf):
            exit_code = guard.main()
        verdict = json.loads(buf.getvalue())
        results.append({'case': 'redundant_' + state, 'exit_code': exit_code,
                        'verdict': verdict['verdict'], 'row': verdict['rows'][0]})
    payload = {'platform': sys.platform, 'python': sys.version, 'model_calls': 0,
               'transport': 'accepted fake only', 'product_modified': False,
               'results': results}
    (out / 'results.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
