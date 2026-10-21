"""Independent offline observations of Hark09 capture input boundaries.

Uses the real default factory through the candidate's in-process transport
fixture. No process, app-server or model is started. Deliberately malformed
files belong exclusively to this probe's fresh output directory.
"""
import argparse
import hashlib
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
    sys.path[:0] = [str(repo), str(repo / 'tests')]
    import test_hark09_workspace_binding as fixture
    import src.agent_session_codex as codex
    seam = (mock.patch.object(codex, 'is_special_path', fixture.fx._posix_evidence_seam)
            if os.name != 'nt' else __import__('contextlib').nullcontext())
    with seam:
        world = fixture.World(out, str(out / 'probe'))
        initial = world.attempt()
        latest = initial['status']['latest']
        probe_id = initial['probe_id']
        thread_id = latest['native_session_id']
        capture = out / 'probe' / 'raw' / ('probe-native-' + probe_id + '.json')
        original = capture.read_bytes()
        valid = json.loads(original)
        cases = [('valid_object', original), ('invalid_json', b'{bad-json')]
        cases += [('top_' + name, json.dumps(value).encode('utf-8')) for name, value in
                  [('null', None), ('array_empty', []), ('array_object', [{}]),
                   ('string', 'native'), ('integer', 7), ('float', 1.5), ('boolean', True)]]
        cases += [('object_missing_keys', b'{}'),
                  ('facts_not_list', json.dumps({'probe_id': probe_id, 'facts': {}}).encode()),
                  ('fact_not_object', json.dumps({'probe_id': probe_id, 'facts': [7]}).encode()),
                  ('wrong_probe_id', json.dumps(dict(valid, probe_id='0' * 16)).encode()),
                  ('same_thread_invalid_type', json.dumps({'probe_id': probe_id, 'facts': [
                      {'source': 'thread/start.response', 'thread_id': thread_id, 'cwd': 7}]}).encode())]
        rows = []
        try:
            for name, payload in cases:
                capture.write_bytes(payload)
                calls = world.thread_starts()
                before = capture.read_bytes()
                try:
                    result = world.api.session_probe_status()['latest']
                    observation = {'workspace_state': result['workspace']['state'],
                                   'workspace_files': result['workspace_files'],
                                   'verified_empty': result['workspace']['verified_empty']}
                except Exception as exc:
                    observation = {'exception': type(exc).__name__, 'message': str(exc)}
                rows.append(dict(case=name, **observation,
                                 additional_thread_starts=world.thread_starts() - calls,
                                 capture_bytes_unchanged=capture.read_bytes() == before))
        finally:
            capture.write_bytes(original)
        result = {'model_calls': 0, 'real_app_server_calls': 0, 'platform': sys.platform,
                  'python': sys.version, 'initial_state': latest['workspace']['state'],
                  'rows': rows, 'probe_initial_capture_sha256': hashlib.sha256(original).hexdigest()}
        (out / 'results.json').write_bytes((json.dumps(result, indent=2) + '\n').encode('utf-8'))
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
