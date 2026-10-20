"""Independent Windows junction audit with exact before/after byte snapshots."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

def snapshot(root):
    return {p.relative_to(root).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest()
            if p.is_file() else '<directory>') for p in sorted(root.rglob('*'))}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    repo, out = args.repo.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    sys.path[:0] = [str(repo), str(repo/'tests')]
    import test_hark06_session_probe as fx
    import src.session_probe as service
    if os.name != 'nt':
        raise RuntimeError('This audit requires native Windows junctions')
    rows = []
    for case in ('root_empty', 'root_claim', 'root_claim_lock', 'ancestor', 'raw', 'ws'):
        base = out/case
        root = base/'parent/probe' if case == 'ancestor' else base/'probe'
        rig = fx.Rig(base, probe_dir=root, runner='deferred')
        prepared = rig.api.prepare_session_probe({})
        rig.api.start_session_probe({'probe_id': prepared['probe_id'], 'confirm': True})
        claim_before = (root/service.CLAIM_NAME).read_bytes()
        target = base/'outside'
        target.mkdir()
        if case.startswith('root') or case == 'ancestor':
            original = root.parent if case == 'ancestor' else root
            parked = base/'parked'
            assert original.resolve().is_relative_to(out) and parked.resolve().is_relative_to(out)
            original.rename(parked)
            link = original
            source_claim = parked/'probe'/service.CLAIM_NAME if case == 'ancestor' else parked/service.CLAIM_NAME
            if case == 'ancestor':
                (target/'probe').mkdir()
            if case in ('root_claim','root_claim_lock'):
                (target/service.CLAIM_NAME).write_bytes(claim_before)
            if case == 'root_claim_lock':
                (target/service.LOCK_NAME).write_bytes(b'foreign-lock-do-not-touch')
        else:
            link = root/case
            source_claim = root/service.CLAIM_NAME
        made = subprocess.run(['cmd.exe','/c','mklink','/J',str(link),str(target)],
            capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        assert made.returncode == 0, made.stderr.decode(errors='replace')
        before = snapshot(target)
        for job in rig.deferred:
            job()
        after = snapshot(target)
        status = rig.api.session_probe_status()
        source_state = json.loads(source_claim.read_bytes())['state']
        expected_state = 'finished' if case in ('raw','ws') else 'accepted'
        row = {'case': case, 'target_bytes_and_entries_unchanged': before == after,
               'before': before, 'after': after, 'thread_starts': rig.thread_starts(),
               'source_claim_state': source_state, 'expected_source_claim_state': expected_state,
               'parked_source_bytes_unchanged': source_claim.read_bytes() == claim_before if case not in ('raw','ws') else None,
               'cleanup_refused': bool((status['last_error'] or {}).get('cleanup_refused'))}
        row['ok'] = (before == after and row['thread_starts'] == 0 and source_state == expected_state
                     and (case in ('raw','ws') or row['parked_source_bytes_unchanged']))
        rows.append(row)
    result = {'platform': sys.platform, 'python': sys.version, 'model_calls': 0,
              'real_app_server_calls': 0, 'all_expected': all(r['ok'] for r in rows), 'rows': rows}
    (out/'results.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
    return 0 if result['all_expected'] else 1

if __name__ == '__main__':
    sys.exit(main())
