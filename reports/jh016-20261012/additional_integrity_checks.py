"""JH-016: isolated file-integrity checks; no APIs or shared-source changes."""
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from src import file_tools as ft
from src.acceptance import file_write_acceptance_evaluator
from src.mock_components import ScriptedReviewerAdapter, ScriptedWorkerAdapter
from src.orchestrator import Orchestrator
from src.storage import TaskStorage
from test_phase4_file_tools import make_file_task, stage_request

ORIGINAL = b'{"old":"UNCHANGED_ORIGINAL"}'
EXPECTED = json.dumps([{'region': 'a', 'sales': 1}], ensure_ascii=False)
CANDIDATE = EXPECTED.encode('utf-8')

def digest(value):
    return hashlib.sha256(value).hexdigest()

def run_case(base, name):
    ws = base / name
    (ws / 'out').mkdir(parents=True)
    target = ws / 'out/report.json'
    target.write_bytes(ORIGINAL)
    alias = ws / 'out/unapproved-neighbor.json'
    if name == 'hardlink_alias':
        os.link(target, alias)
    identity = (target.stat().st_dev, target.stat().st_ino)
    executor = ft.FileToolExecutor(ws)
    reviewer = ScriptedReviewerAdapter(mode='pass')
    tid = 'task_jh016_' + name
    worker = ScriptedWorkerAdapter(tool_requests_by_turn=[[
        stage_request(tid, 'out/report.json', EXPECTED)
    ]])
    storage = TaskStorage(':memory:')
    orch = Orchestrator(storage=storage, workspace_root=ws, tool_executor=executor,
                        worker_adapter=worker, reviewer_adapter=reviewer,
                        acceptance_evaluator=file_write_acceptance_evaluator(EXPECTED, 'out/report.json'),
                        enforce_target_files_scope=True)
    observations = {'injection_count': 0}
    real_write = os.write
    def check_write(fd, data):
        st = os.fstat(fd)
        is_target = (st.st_dev, st.st_ino) == identity and data == CANDIDATE
        if not is_target or observations['injection_count']:
            return real_write(fd, data)
        if name == 'short_write':
            observations['injection_count'] += 1
            written = real_write(fd, data[:4])
            observations['returned_write_count'] = written
            return written
        if name == 'write_error':
            observations['injection_count'] += 1
            observations['bytes_written_before_error'] = real_write(fd, data[:4])
            raise OSError(errno.ENOSPC, 'isolated fixture: write fails after four bytes')
        if name == 'late_edit':
            observations['injection_count'] += 1
            child_code = "import os,sys,json;f=os.open(sys.argv[1],os.O_RDWR|getattr(os,'O_BINARY',0));os.lseek(f,1,os.SEEK_SET);n=os.write(f,b'Z');os.fsync(f);os.close(f);print(json.dumps({'written':n}))"
            child = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', child_code, str(target)],
                                   capture_output=True, text=True, encoding='utf-8', timeout=10)
            observations['writer_exit_code'] = child.returncode
            observations['writer_stdout'] = child.stdout.strip()
            observations['writer_stderr'] = child.stderr.strip()
            previous_offset = os.lseek(fd, 0, os.SEEK_CUR)
            os.lseek(fd, 0, os.SEEK_SET)
            observations['target_after_external_edit'] = os.read(fd, 65536).decode('utf-8')
            os.lseek(fd, previous_offset, os.SEEK_SET)
        return real_write(fd, data)
    try:
        orch.submit_task(make_file_task(tid, ws, expected_content=EXPECTED))
        with patch.object(ft.os, 'write', check_write):
            final = orch.run_task(tid)
        after = target.read_bytes()
        tools = storage.list_tool_executions(tid)
        stages = [t for t in tools if t['tool_name'] == 'stage_write']
        sd = stages[-1]['result']['structured_data']
        recovery = executor.recover_apply(tid, target_path='out/report.json',
                                          staged_path=sd['staged_path'], content_sha256=sd['content_sha256'],
                                          target_pre_sha256=sd['target_pre_sha256'])
        backups = list((ws / '_staging' / tid).glob('backup_*'))
        record = {'case': name, 'status': final['status'], 'error_info': final.get('error_info'),
                  'original_text': ORIGINAL.decode(), 'expected_text': EXPECTED, 'target_after': after.decode('utf-8'),
                  'target_is_original': after == ORIGINAL, 'target_is_candidate': after == CANDIDATE,
                  'reviewer_calls': reviewer.call_count, 'recovery_state': recovery['state'],
                  'backup_count': len(backups), 'backups_preserve_original': bool(backups) and all(p.read_bytes() == ORIGINAL for p in backups),
                  'unconfirmed_side_effect': final.get('_runtime', {}).get('has_unconfirmed_side_effect'),
                  'tool_statuses': [{'tool': t['tool_name'], 'status': t['status']} for t in tools],
                  'observations': observations}
        if name == 'hardlink_alias':
            record['neighbor_after'] = alias.read_text(encoding='utf-8')
            record['neighbor_unchanged'] = alias.read_bytes() == ORIGINAL
            record['target_hardlink_count'] = target.stat().st_nlink
        return record
    finally:
        storage.close()

def main():
    base = (ROOT / '.test-tmp').resolve()
    base.mkdir(exist_ok=True)
    fixture = base / ('jh016-integrity-' + uuid.uuid4().hex)
    fixture.mkdir()
    original_hashes = {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in (ROOT / 'src').glob('*.py')}
    runs = []
    try:
        for number in range(2):
            group = []
            for case in ['control', 'short_write', 'write_error', 'hardlink_alias', 'late_edit']:
                try:
                    group.append(run_case(fixture / str(number), case))
                except Exception as exc:
                    group.append({'case': case, 'harness_error': type(exc).__name__ + ': ' + str(exc)})
            runs.append(group)
    finally:
        resolved = fixture.resolve()
        if resolved.parent != base or not resolved.name.startswith('jh016-integrity-'):
            raise RuntimeError('fixture cleanup boundary check failed')
        shutil.rmtree(resolved)
    after_hashes = {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in (ROOT / 'src').glob('*.py')}
    report = {'api_calls': 0, 'shared_source_unchanged': original_hashes == after_hashes,
              'fixture_cleaned': not fixture.exists(), 'runs': runs, 'repeat_observations_equal': runs[0] == runs[1]}
    out = Path(__file__).with_name('additional-integrity-checks.json')
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'report': str(out), 'repeat_observations_equal': report['repeat_observations_equal'],
                      'shared_source_unchanged': report['shared_source_unchanged'],
                      'cases': [{k: v for k, v in r.items() if k in ['case', 'status', 'target_is_original', 'target_is_candidate', 'neighbor_unchanged', 'recovery_state', 'harness_error']} for r in runs[0]]}, ensure_ascii=False))
    return 1 if any('harness_error' in r for group in runs for r in group) else 0

if __name__ == '__main__':
    raise SystemExit(main())
