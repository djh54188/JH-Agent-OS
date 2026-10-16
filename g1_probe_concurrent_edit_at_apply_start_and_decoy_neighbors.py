import argparse, json, shutil, sys, uuid
from pathlib import Path

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--repo', required=True)
    p.add_argument('--temp-base', required=True)
    args = p.parse_args()
    sys.path.insert(0, args.repo)
    from src.file_tools import FileToolExecutor, PathBoundaryError

    root = Path(args.temp_base) / f'probe_cedit_{uuid.uuid4().hex[:8]}'
    ws = root / 'ws'
    try:
        ws.mkdir(parents=True)
        target = ws / 'cfg.txt'
        target.write_text('CFG_V1', encoding='utf-8')
        decoy1 = ws / '.tmp_cfg.txt'
        decoy1.write_bytes(b'DECOY_1')

        def injector(phase: str):
            if phase == 'apply_start':
                target.write_text('CONCURRENT_V2', encoding='utf-8')

        ex = FileToolExecutor(ws, fault_injector=injector)
        st = ex.stage_write('t_edit', target_path='cfg.txt', content='STAGED_V3')
        decoy2 = ws / '_staging' / 't_edit' / 'backup_decoy_cfg.txt'
        decoy2.write_bytes(b'DECOY_2')

        refused = False
        try:
            ex.apply_staged('t_edit', target_path='cfg.txt', staged_path=st['staged_path'],
                            content_sha256=st['content_sha256'], target_pre_sha256=st['target_pre_sha256'])
        except PathBoundaryError:
            refused = True

        concurrent_kept = target.read_text(encoding='utf-8') == 'CONCURRENT_V2'
        decoys_ok = decoy1.read_bytes() == b'DECOY_1' and decoy2.read_bytes() == b'DECOY_2'
        checks = {'concurrent_edit_preserved': concurrent_kept, 'decoys_untouched': decoys_ok}
        print(json.dumps({
            'probe': 'probe_concurrent_edit_at_apply_start_and_decoy_neighbors',
            'invariant': 'I2',
            'refused': refused,
            'disk_checks': checks,
            'defect_reproduced': (not refused) or (not concurrent_kept) or (not decoys_ok)
        }))
    except Exception as e:
        print(json.dumps({'probe': 'probe_concurrent_edit_at_apply_start_and_decoy_neighbors', 'error': str(e)}))
    finally:
        if root.exists() and root.name.startswith('probe_cedit_'):
            shutil.rmtree(root, ignore_errors=True)

if __name__ == '__main__':
    main()
