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

    root = Path(args.temp_base) / f'probe_rfrace_{uuid.uuid4().hex[:8]}'
    ws, out = root / 'ws', root / 'outside'
    juncs = []
    try:
        (ws / 'sub').mkdir(parents=True)
        out.mkdir(parents=True)
        (ws / 'sub' / 'data.txt').write_text('INSIDE', encoding='utf-8')
        (out / 'data.txt').write_text('OUTSIDE_SECRET', encoding='utf-8')

        def swap_hook(resolved: Path):
            sub = ws / 'sub'
            if sub.exists():
                shutil.rmtree(sub)
                mk_junc(sub, out)
                juncs.append(sub)

        ex = FileToolExecutor(ws, race_hook=swap_hook)
        refused, leaked = False, False
        try:
            res = ex.read_file('sub/data.txt')
            if 'OUTSIDE_SECRET' in res:
                leaked = True
        except PathBoundaryError:
            refused = True

        intact = (out / 'data.txt').read_text(encoding='utf-8') == 'OUTSIDE_SECRET'
        checks = {'no_outside_leak': not leaked, 'outside_intact': intact}
        print(json.dumps({
            'probe': 'probe_read_file_race_junction_swap',
            'invariant': 'I1',
            'refused': refused,
            'disk_checks': checks,
            'defect_reproduced': leaked or (not refused) or (not intact)
        }))
    except Exception as e:
        print(json.dumps({'probe': 'probe_read_file_race_junction_swap', 'error': str(e)}))
    finally:
        for j in juncs:
            try: os.rmdir(j)
            except OSError: pass
        if root.exists() and root.name.startswith('probe_rfrace_'):
            shutil.rmtree(root, ignore_errors=True)

if __name__ == '__main__':
    main()
