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

    root = Path(args.temp_base) / f'probe_deepj_{uuid.uuid4().hex[:8]}'
    ws, out = root / 'ws', root / 'outside'
    juncs = []
    try:
        (ws / 'a' / 'b' / 'c').mkdir(parents=True)
        (out / 'b' / 'c').mkdir(parents=True)
        out_target = out / 'b' / 'c' / 'target.txt'
        out_target.write_bytes(b'UNTOUCHED')
        in_target = ws / 'a' / 'b' / 'c' / 'target.txt'
        in_target.write_text('OLD_INSIDE', encoding='utf-8')

        def injector(phase: str):
            if phase == 'apply_start':
                shutil.rmtree(ws / 'a')
                mk_junc(ws / 'a', out)
                juncs.append(ws / 'a')

        ex = FileToolExecutor(ws, fault_injector=injector)
        st = ex.stage_write('t1', target_path='a/b/c/target.txt', content='NEW_PAYLOAD')
        refused = False
        try:
            ex.apply_staged('t1', target_path='a/b/c/target.txt', staged_path=st['staged_path'],
                            content_sha256=st['content_sha256'], target_pre_sha256=st['target_pre_sha256'])
        except PathBoundaryError:
            refused = True

        intact = out_target.read_bytes() == b'UNTOUCHED'
        checks = {'outside_target_untouched': intact}
        print(json.dumps({
            'probe': 'probe_deep_ancestor_junction_at_apply_start',
            'invariant': 'I4',
            'refused': refused,
            'disk_checks': checks,
            'defect_reproduced': (not refused) or (not intact)
        }))
    except Exception as e:
        print(json.dumps({'probe': 'probe_deep_ancestor_junction_at_apply_start', 'error': str(e)}))
    finally:
        for j in juncs:
            try: os.rmdir(j)
            except OSError: pass
        if root.exists() and root.name.startswith('probe_deepj_'):
            shutil.rmtree(root, ignore_errors=True)

if __name__ == '__main__':
    main()
