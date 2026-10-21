"""Offline API observations for read failures, not syscall races.

Positive and failing reads use identical synthetic registry and raw bytes.
Only the named observation read or directory enumeration reports an OSError.
No real transport, model, native app-server or private history is used.
"""
import argparse
import contextlib
import inspect
import json
import os
from pathlib import Path
import sys
from unittest import mock


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    repo, out = args.repo.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    sys.path[:0] = [str(repo), str(repo / 'tests')]
    import test_hark09_workspace_binding as fixture
    import src.agent_session_codex as codex
    seam = (mock.patch.object(codex, 'is_special_path', fixture.fx._posix_evidence_seam)
            if os.name != 'nt' else contextlib.nullcontext())
    rows = []
    with seam:
        worlds = {}
        for label, native in [('conflicting_native', 'conflict'), ('nonempty_workspace', 'resolve')]:
            world = fixture.World(out / label, str(out / label / 'probe'), native=native)
            world.attempt()
            worlds[label] = world
        planned = Path(worlds['nonempty_workspace'].api.session_probe_status()['latest']['workspace']['planned'])
        (planned / 'nested').mkdir()
        (planned / 'nested' / 'exists.txt').write_bytes(b'present before every query')

        def observe(case, world, patch=contextlib.nullcontext()):
            raw_root = out / ('conflicting_native' if world is worlds['conflicting_native'] else 'nonempty_workspace') / 'probe'
            before = {str(p.relative_to(raw_root)): p.read_bytes() for p in raw_root.rglob('*') if p.is_file()}
            calls = world.thread_starts()
            with patch:
                try:
                    latest = world.api.session_probe_status()['latest']
                    ws = latest['workspace']
                    row = {'state': ws['state'], 'files': latest['workspace_files'],
                           'verified_empty': ws['verified_empty'], 'raw_evidence': latest['evidence']['state']}
                except Exception as exc:
                    row = {'exception': type(exc).__name__, 'message': str(exc)}
            after = {str(p.relative_to(raw_root)): p.read_bytes() for p in raw_root.rglob('*') if p.is_file()}
            rows.append(dict(case=case, **row, additional_thread_starts=world.thread_starts() - calls,
                             source_bytes_unchanged=before == after))

        observe('conflict_positive_control', worlds['conflicting_native'])
        original_read = Path.read_bytes
        for name, error in [('permission', PermissionError('injected observation read denied')),
                            ('io_error', OSError('injected observation read failure')),
                            ('decode_error', UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'injected decode failure'))]:
            def failed_read(path, _error=error):
                caller = inspect.currentframe().f_back.f_code.co_name
                if caller == '_workspace_observation':
                    raise _error
                return original_read(path)
            observe('native_observation_' + name, worlds['conflicting_native'],
                    mock.patch.object(Path, 'read_bytes', failed_read))

        observe('nonempty_positive_control', worlds['nonempty_workspace'])
        original_scandir = os.scandir
        for name, target, error in [('root_permission', planned, PermissionError('injected listing denied')),
                                    ('child_permission', planned / 'nested', PermissionError('injected child denied')),
                                    ('root_io_error', planned, OSError('injected listing failure'))]:
            def failed_scan(path, _target=target, _error=error):
                if not isinstance(path, int) and os.path.normcase(os.path.abspath(path)) == os.path.normcase(str(_target)):
                    raise _error
                return original_scandir(path)
            observe('listing_' + name, worlds['nonempty_workspace'], mock.patch.object(os, 'scandir', failed_scan))
    result = {'platform': sys.platform, 'python': sys.version, 'model_calls': 0,
              'real_app_server_calls': 0, 'rows': rows}
    (out / 'results.json').write_bytes((json.dumps(result, indent=2) + '\n').encode('utf-8'))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
