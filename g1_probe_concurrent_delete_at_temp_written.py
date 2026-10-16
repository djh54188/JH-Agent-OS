import argparse, json, shutil, sys, uuid
from pathlib import Path

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--repo', required=True)
    p.add_argument('--temp-base', required=True)
    args = p.parse_args()
    sys.path.insert(0, args.repo)
    from src.file_tools import FileToolExecutor, PathBoundaryError

    root = Path(args.temp_base) / f'probe_cdel_{uuid.uuid4().hex[:8]}'
    ws = root / 'ws'
    try:
        ws.mkdir(parents=True)
        target = ws / 'victim.txt'
        target.write_text('INITIAL_V1', encoding='utf-8')
        neighbor = ws / 'neighbor.txt'
        neighbor.write_bytes(b'NEIGHBOR_SAFE')

        def injector(phase: str):
            if phase == 'temp_written':
                target.unlink()

        ex = FileToolExecutor(ws, fault_injector=injector)
        st = ex.stage_write('t_del', target_path='victim.txt', content='STAGED_V2')
        refused = False
        try:
            ex.apply_staged('t_del', target_path='victim.txt', staged_path=st['staged_path'],
                            content_sha256=st['content_sha256'], target_pre_sha256=st['target_pre_sha256'])
        except PathBoundaryError:
            refused = True

        stay_deleted = not target.exists()
        neighbor_ok = neighbor.read_bytes() == b'NEIGHBOR_SAFE'
        checks = {'target_remained_deleted': stay_deleted, 'neighbor_intact': neighbor_ok}
        print(json.dumps({
            'probe': 'probe_concurrent_delete_at_temp_written',
            'invariant': 'I3',
            'refused': refused,
            'disk_checks': checks,
            'defect_reproduced': (not refused) or (not stay_deleted) or (not neighbor_ok)
        }))
    except Exception as e:
        print(json.dumps({'probe': 'probe_concurrent_delete_at_temp_written', 'error': str(e)}))
    finally:
        if root.exists() and root.name.startswith('probe_cdel_'):
            shutil.rmtree(root, ignore_errors=True)

if __name__ == '__main__':
    main()
