"""Independent H13 fault-class probe. No Gemini, network, or model calls."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest import mock

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('frozen', ROOT / 'frozen_probe_boundaries.py')
frozen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(frozen)
gem = frozen.gem


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def attempt(h):
    before = h.launches
    try:
        a = h.adapter()
        s = a.submit(h.request())
        return {'state': s.state, 'new_launches': h.launches - before}
    except Exception as exc:
        return {'exception': type(exc).__name__, 'message': str(exc),
                'new_launches': h.launches - before}


def worker(root):
    cfg = gem.GeminiCliConfig(('NOT_EXECUTED',), str(root / 'home'), str(root / 'evidence'))
    count = 0
    def launch(*args, **kwargs):
        nonlocal count
        count += 1
        return frozen.Done()
    try:
        a = gem.GeminiCliAgentSessionAdapter(cfg, launcher=launch, version_probe=lambda _: '0.65.0')
        req = frozen.SubmitRequest('t1', 1, frozen.ADDRESS, 'Locate RunState, cite path:line.',
                                   str(root / 'ws'), idempotency_key='k1')
        s = a.submit(req)
        row = {'state': s.state, 'run_id': s.run_id}
    except Exception as exc:
        row = {'exception': type(exc).__name__, 'message': str(exc)}
    row['new_launches'] = count
    print(json.dumps(row))


def main():
    out = ROOT / 'admission-01'
    out.mkdir(exist_ok=False)
    rows = []
    for fault in ('lstat', 'scandir', 'entry_is_dir'):
        for layout in ('intact', 'missing'):
            h = frozen.Harness(out / (fault + '-' + layout))
            h.adapter().submit(h.request())
            if layout == 'missing':
                (h.evidence / 'registry.jsonl').unlink()
            before = snapshot(h.evidence)
            if fault == 'lstat':
                real = gem.os.lstat
                def bad(path, *args, **kwargs):
                    if os.fspath(path) == str(h.evidence / 'runs'):
                        raise PermissionError('independent lstat injection')
                    return real(path, *args, **kwargs)
                patch = mock.patch.object(gem.os, 'lstat', bad)
            elif fault == 'scandir':
                real = gem.os.scandir
                def bad(path):
                    if os.fspath(path) == str(h.evidence / 'runs'):
                        raise PermissionError('independent scandir injection')
                    return real(path)
                patch = mock.patch.object(gem.os, 'scandir', bad)
            else:
                class Entry:
                    name = 'gemini-run-0123456789ab'
                    def is_dir(self, **kwargs):
                        raise PermissionError('independent entry inspection injection')
                class Entries:
                    def __enter__(self): return iter([Entry()])
                    def __exit__(self, *args): return False
                real = gem.os.scandir
                def bad(path):
                    return Entries() if os.fspath(path) == str(h.evidence / 'runs') else real(path)
                patch = mock.patch.object(gem.os, 'scandir', bad)
            with patch:
                row = attempt(h)
            row.update(case=fault + '-' + layout, bytes_unchanged=snapshot(h.evidence) == before)
            rows.append(row)

    # A failed evidence write after mkdir must not silently let the same object
    # or a fresh one dispatch another key from an orphaned root.
    h = frozen.Harness(out / 'failed-reservation')
    a = h.adapter()
    with mock.patch.object(gem, 'write_bytes', side_effect=OSError('independent write failure')):
        try: a.submit(h.request())
        except Exception as exc: initial = type(exc).__name__
    before = snapshot(h.evidence)
    for who in ('owner', 'new-object'):
        b = a if who == 'owner' else h.adapter()
        try:
            b.submit(h.request('new-key'))
            row = {'exception': None}
        except Exception as exc:
            row = {'exception': type(exc).__name__}
        row.update(case='failed-reservation-' + who, initial_exception=initial,
                   total_launches=h.launches, bytes_unchanged=snapshot(h.evidence) == before)
        rows.append(row)

    # A fresh process uses no inherited adapter objects or interpreter memory.
    for layout in ('intact', 'delete', 'zero', 'blank', 'tail'):
        h = frozen.Harness(out / ('restart-' + layout))
        a = h.adapter()
        first = a.submit(h.request())
        reg = h.evidence / 'registry.jsonl'
        if layout == 'delete': reg.unlink()
        elif layout == 'zero': reg.write_bytes(b'')
        elif layout == 'blank': reg.write_bytes(b' \n\t\n')
        elif layout == 'tail':
            old = reg.read_bytes()
            a.submit(h.request('k2'))
            reg.write_bytes(old)
        before = snapshot(h.evidence)
        proc = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(Path(__file__).resolve()),
                               '--worker', str(h.root)], capture_output=True, text=True,
                              encoding='utf-8', timeout=20,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        (out / ('restart-' + layout + '.stdout.txt')).write_text(proc.stdout, encoding='utf-8')
        (out / ('restart-' + layout + '.stderr.txt')).write_text(proc.stderr, encoding='utf-8')
        row = json.loads(proc.stdout)
        row.update(case='restart-' + layout, exit_code=proc.returncode,
                   same_run=row.get('run_id') == first.run_id,
                   bytes_unchanged=snapshot(h.evidence) == before)
        rows.append(row)

    summary = {'cases': rows, 'python_child_processes': 5, 'real_gemini_processes': 0, 'model_calls': 0}
    (out / 'results.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))
    for row in rows:
        assert row['bytes_unchanged'], row
        if row['case'] == 'restart-intact':
            assert row['state'] == 'unknown' and row['same_run'] and row['new_launches'] == 0, row
        else:
            assert row['exception'] == 'RegistryUnavailable', row
            assert row.get('new_launches', row.get('total_launches')) == 0, row


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--worker': worker(Path(sys.argv[2]))
    else: main()
