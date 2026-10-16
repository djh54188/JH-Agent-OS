import argparse, json, shutil, sys, uuid
from pathlib import Path

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--repo', required=True)
    p.add_argument('--temp-base', required=True)
    args = p.parse_args()
    sys.path.insert(0, args.repo)
    from src.file_tools import FileToolExecutor, PathBoundaryError

    root = Path(args.temp_base) / f'probe_prefix_{uuid.uuid4().hex[:8]}'
    ws = root / 'workspace'
    evil = root / 'workspace_evil'
    try:
        ws.mkdir(parents=True)
        evil.mkdir(parents=True)
        outside_file = evil / 'secret.txt'
        outside_file.write_bytes(b'OUTSIDE_ORIGINAL')

        ex = FileToolExecutor(ws)
        refused_all = True
        variants = [
            str(outside_file),
            str(outside_file).upper(),
            '../workspace_evil/secret.txt',
            '..\\WORKSPACE_EVIL\\secret.txt'
        ]
        for v in variants:
            try:
                ex.read_file(v)
                refused_all = False
            except PathBoundaryError:
                pass
            try:
                ex.stage_write('t1', target_path=v, content='PWNED')
                refused_all = False
            except PathBoundaryError:
                pass

        intact = outside_file.read_bytes() == b'OUTSIDE_ORIGINAL'
        extra_files = list(evil.iterdir()) == [outside_file]
        checks = {'outside_bytes_intact': intact, 'no_extra_outside_files': extra_files}
        print(json.dumps({
            'probe': 'probe_prefix_and_case_collision',
            'invariant': 'I1',
            'refused': refused_all,
            'disk_checks': checks,
            'defect_reproduced': (not refused_all) or (not intact) or (not extra_files)
        }))
    except Exception as e:
        print(json.dumps({'probe': 'probe_prefix_and_case_collision', 'error': str(e)}))
    finally:
        if root.exists() and root.name.startswith('probe_prefix_'):
            shutil.rmtree(root, ignore_errors=True)

if __name__ == '__main__':
    main()
