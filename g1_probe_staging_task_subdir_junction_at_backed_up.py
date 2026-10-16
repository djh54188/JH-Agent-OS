import argparse, json, os, shutil, subprocess, sys, uuid, _winapi
from pathlib import Path

def mk_junc(link: Path, target: Path):
    try:
        _winapi.CreateJunction(str(target), str(link))
    except Exception:
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], check=True, capture_output=True)

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--repo', required=True)
    p.add_argument('--temp-base', required=True)
    args = p.parse_args()
    sys.path.insert(0, args.repo)
    from src.file_tools import FileToolExecutor, PathBoundaryError

    root = Path(args.temp_base) / f'probe_stgj_{uuid.uuid4().hex[:8]}'
    ws, out = root / 'ws', root / 'outside_stg'
    juncs = []
    try:
        ws.mkdir(parents=True)
        out.mkdir(parents=True)
        (out / 'canary.txt').write_bytes(b'CANARY')
        target = ws / 'doc.txt'
        target.write_text('DOC_V1', encoding='utf-8')

        def injector(phase: str):
            if phase == 'backed_up':
                tdir = ws / '_staging' / 'task_99'
                shutil.rmtree(tdir)
                mk_junc(tdir, out)
                juncs.append(tdir)

        ex = FileToolExecutor(ws, fault_injector=injector)
        st = ex.stage_write('task_99', target_path='doc.txt', content='DOC_V2')
        refused = False
        try:
            ex.apply_staged('task_99', target_path='doc.txt', staged_path=st['staged_path'],
                            content_sha256=st['content_sha256'], target_pre_sha256=st['target_pre_sha256'])
        except PathBoundaryError:
            refused = True

        out_files = [f.name for f in out.iterdir()]
        no_temp_leaked = out_files == ['canary.txt'] and (out / 'canary.txt').read_bytes() == b'CANARY'
        target_unchanged = target.read_text(encoding='utf-8') == 'DOC_V1'
        checks = {'outside_clean': no_temp_leaked, 'target_unchanged': target_unchanged}
        print(json.dumps({
            'probe': 'probe_staging_task_subdir_junction_at_backed_up',
            'invariant': 'I5',
            'refused': refused,
            'disk_checks': checks,
            'defect_reproduced': (not refused) or (not no_temp_leaked) or (not target_unchanged)
        }))
    except Exception as e:
        print(json.dumps({'probe': 'probe_staging_task_subdir_junction_at_backed_up', 'error': str(e)}))
    finally:
        for j in juncs:
            try: os.rmdir(j)
            except OSError: pass
        if root.exists() and root.name.startswith('probe_stgj_'):
            shutil.rmtree(root, ignore_errors=True)

if __name__ == '__main__':
    main()
