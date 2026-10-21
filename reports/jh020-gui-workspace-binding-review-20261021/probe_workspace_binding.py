"""Offline observation: distinguish server-relative config from app-server-relative cwd.

The default real adapter factory is exercised, with ONLY its stdio transport
replaced in-process. No Codex process or model is started. Effective cwd is a
resolver simulation, corroborated separately by the real trial native archive.
"""
import argparse
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
    os.chdir(repo)
    sys.path[:0] = [str(repo), str(repo/'tests')]
    import test_hark06_session_probe as fx
    import src.agent_session_codex as codex
    from src.app_api import AppInterface, ApiError
    from src.session_probe import ProbeConfig
    if os.name != 'nt':
        codex.is_special_path = fx._posix_evidence_seam
    rows = []
    for label in ('absolute', 'relative', 'relative_dot', 'relative_dotdot',
                  'absolute_space', 'relative_space', 'absolute_native_mismatch',
                  'absolute_native_missing', 'absolute_foreign_native',
                  'absolute_native_conflict'):
        intended = out/label/'probe'
        if label in ('absolute_space', 'relative_space'):
            intended = out/(label+' space \u7a7a\u683c')/'probe'
        spelling = str(intended)
        if label.startswith('relative'):
            spelling = os.path.relpath(intended, repo)
        if label == 'relative_dot':
            spelling = '.'+os.sep+spelling
        if label == 'relative_dotdot':
            middle = intended.parent/'middle'
            middle.mkdir(parents=True)
            spelling = os.path.relpath(middle/'..'/'probe', repo)
            # Keep the written '..' rather than pre-normalising the input.
            spelling = os.path.relpath(middle, repo)+os.sep+'..'+os.sep+'probe'
        expected_base = Path(os.path.abspath(spelling))
        observed = []
        class ResolvingEcho(fx.EchoTransport):
            def __init__(self, process_cwd):
                super().__init__()
                self.process_cwd = os.path.abspath(process_cwd)
                self.native_cwd = None
            def call(self, method, params, timeout=60):
                result = super().call(method, params, timeout)
                if method in ('thread/start', 'turn/start'):
                    supplied = params['cwd']
                    effective = os.path.abspath(os.path.join(self.process_cwd, supplied))
                    if label == 'absolute_native_mismatch':
                        effective = str(out/'different-native-directory')
                    self.native_cwd = effective
                    observed.append({'method':method, 'process_cwd':self.process_cwd,
                                     'supplied_cwd':supplied, 'effective_native_cwd':effective})
                    if method == 'thread/start':
                        result['result']['cwd'] = effective
                        result['result']['thread']['cwd'] = effective
                        if label in ('absolute_native_missing', 'absolute_foreign_native'):
                            result['result'].pop('cwd', None)
                            result['result']['thread'].pop('cwd', None)
                return result
            def notifications(self):
                notes = super().notifications()
                if label != 'absolute_native_missing':
                    thread = fx.fx.OTHER_THREAD if label == 'absolute_foreign_native' else fx.fx.THREAD
                    native = str(out/'foreign-native') if label == 'absolute_foreign_native' else self.native_cwd
                    if label == 'absolute_native_conflict':
                        native = str(out/'conflicting-native-directory')
                    notes.insert(0, {'method':'thread/started','params':{'thread':{'id':thread,'cwd':native}}})
                return notes
        transports = []
        def capture_transport(argv, cwd):
            transport = ResolvingEcho(cwd)
            transports.append(transport)
            return transport
        api = AppInterface(db_path=out/'absent.sqlite3', runs_path=out/'readonly-history.jsonl',
                           session_probe=ProbeConfig(enabled=True, probe_dir=spelling),
                           probe_runtime_check=lambda: None, probe_runner=lambda job: job())
        prepared = None
        try:
            with mock.patch.object(codex, 'CodexStdioTransport', capture_transport):
                prepared = api.prepare_session_probe({})
                api.start_session_probe({'probe_id':prepared['probe_id'], 'confirm':True})
            latest = api.session_probe_status()['latest']
            expected_workspace = str(expected_base/'ws'/prepared['probe_id'])
            matches = bool(observed) and all(os.path.normcase(r['effective_native_cwd']) == os.path.normcase(expected_workspace) for r in observed)
            raw = Path(latest['evidence']['path'])
            frames = [json.loads(line) for line in raw.read_text(encoding='utf-8').splitlines()]
            bound_cwds = [(frame.get('params') or {}).get('thread', {}).get('cwd')
                          for frame in frames if frame.get('method') == 'thread/started'
                          and ((frame.get('params') or {}).get('thread') or {}).get('id') == latest['native_session_id']]
            rows.append({'case':label,'input_is_absolute':os.path.isabs(spelling),
                         'expected_workspace':expected_workspace, 'protocol':observed,
                         'workspace_matches':matches, 'bound_native_cwd_notifications':bound_cwds,
                         'outcome':latest and latest['outcome'],
                         'reported_workspace_files':latest and latest['workspace_files'],
                         'offline_dispatches':len(transports)})
        except ApiError as exc:
            rows.append({'case':label,'refused':exc.code,'offline_dispatches':len(transports)})
    result={'platform':sys.platform,'python':sys.version,'transport':'in-process resolver simulation',
            'model_calls':0,'real_app_server_calls':0,'rows':rows}
    (out/'results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))

if __name__ == '__main__':
    main()
