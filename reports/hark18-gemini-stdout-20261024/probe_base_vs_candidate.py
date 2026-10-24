"""Hark-派单-18: same CONSTRUCTED input through the baseline (e3b2017) and candidate adapters.

Usage: python reports/hark18-gemini-stdout-20261024/probe_base_vs_candidate.py <base_worktree> <out.json>
Each adapter runs in its own process with its own src/ and tests/ (test_hark11 harness); the
process stand-in is this candidate's tests/fixtures/gemini_cli/fake_cli_h18.py for both, so the
stdout bytes are identical. Local temporary directories only; no real CLI, no model.
"""
import base64, json, subprocess, sys
from pathlib import Path

CAND = Path(__file__).resolve().parents[2]
FAKE = CAND / "tests" / "fixtures" / "gemini_cli" / "fake_cli_h18.py"
KNOWN = b"Rename failed with EPERM, retrying in 50ms (attempt 1/5)...\n"
DRIVER = r'''
import hashlib, json, os, sys
root, fake, scenario = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
sys.path.insert(0, root); sys.path.insert(0, os.path.join(root, "tests"))
from src import agent_session_gemini as gem
import test_hark11_gemini_session as h11
class H(h11._Harness):
    def config(self):
        return gem.GeminiCliConfig(argv_prefix=(sys.executable, fake), cli_home=self.home,
                                   evidence_root=self.evidence, extra_env=(("FAKE_GEMINI_SCENARIO", self.scenario_path),))
h = H(scenario)
try:
    a = h.adapter(); s = a.submit(h.request()); r = a.collect(h11.ADDRESS, s.run_id, timeout=60)
    out = next(p for p in r.raw_refs if str(p).endswith("stdout.jsonl"))
    raw = open(out, "rb").read()
    print(json.dumps({"state": a.status(h11.ADDRESS, s.run_id).state, "declared_status": r.declared_status,
                      "stdout_sha256": hashlib.sha256(raw).hexdigest(), "stdout_first_line": raw.split(b"\n")[0].decode("utf-8", "replace"),
                      "not_json_problems": [u for u in r.unverified if "not JSON" in u],
                      "host_diagnostics": r.artifacts[0].get("host_diagnostics") is not None}))
finally:
    h.close()
'''
cases = {"no_preamble": {}, "known_preamble": {"h18_prefix_b64": base64.b64encode(KNOWN).decode()},
         "known_preamble_exit1": {"h18_prefix_b64": base64.b64encode(KNOWN).decode(), "exit_code": 1}}
rows = []
for name, scen in cases.items():
    for label, root in (("base", Path(sys.argv[1]).resolve()), ("candidate", CAND)):
        done = subprocess.run([sys.executable, "-c", DRIVER, str(root), str(FAKE), json.dumps(scen)],
                              capture_output=True, text=True, timeout=300)
        row = {"case": name, "adapter": label, "exit": done.returncode}
        row.update(json.loads(done.stdout.strip().splitlines()[-1]) if done.returncode == 0 else {"stderr": done.stderr[-500:]})
        rows.append(row)
        print(name, label, row.get("state"), row.get("not_json_problems"), "diag:", row.get("host_diagnostics"))
Path(sys.argv[2]).write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
