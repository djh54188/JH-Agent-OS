"""Hark-派单-17: same-input comparison of 6cd4d51 (Hark16) and the Hark17 script for R5.

Usage (repo root; HARK14_PWSH or pwsh on PATH):
    python reports/hark17-reread-identity-20261024/probe_old_vs_new.py <out.json>
The old script receives the SAME test-only hook (Invoke-TestBetweenQueryAndFetch, copied verbatim from the
new script), called at the same point (after the ls-remote query, before the fetch). Its decision logic is
unchanged. Local temporary fixtures only; no network, no real GitHub targets.
"""
import json, re, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests import test_hark14_handoff_sync as T  # noqa: E402

OLD_SHA = "6cd4d51404faf129239076766b75c1e227cb8dcf"
new_text = T.SCRIPT.read_text(encoding="utf-8")
hook = re.search(r"function Invoke-TestBetweenQueryAndFetch \{.*?\n\}\n", new_text, re.S).group(0)
old = subprocess.run(["git", "show", f"{OLD_SHA}:scripts/win/hark14-handoff-sync.ps1"], cwd=ROOT,
                     capture_output=True, check=True).stdout.decode()
old = old.replace("function Test-RemoteBlob(", hook + "function Test-RemoteBlob(", 1)
anchor = "    foreach ($pair in @(@($ra, $Cfg.ref_a, 'refs/verify/a'), @($rb, $Cfg.ref_b, 'refs/verify/b'))) {"
assert old.count(anchor) == 1
old = old.replace(anchor, "    Invoke-TestBetweenQueryAndFetch\n" + anchor)
tmp = Path(tempfile.mkdtemp(prefix="hark17-probe-"))
OLD = tmp / "old-6cd4d51-with-same-hook.ps1"
OLD.write_text(old, encoding="utf-8")

cases = {
    "no_change": [],
    "a_ancestor": [{"ref": T.REF_A, "op": "ancestor"}],
    "a_fork": [{"ref": T.REF_A, "op": "fork"}],
    "a_descendant": [{"ref": T.REF_A, "op": "descendant"}],
    "b_descendant": [{"ref": T.REF_B, "op": "descendant"}],
    "a_and_b_descendant": [{"ref": T.REF_A, "op": "descendant"}, {"ref": T.REF_B, "op": "descendant"}],
    "b_deleted": [{"ref": T.REF_B, "op": "delete"}],
}
out = {"old_script": f"{OLD_SHA} + identical test hook", "new_script": "scripts/win/hark14-handoff-sync.ps1", "cases": []}
for name, inj in cases.items():
    for label, script in (("old", OLD), ("new", T.SCRIPT)):
        f = T.Fixture()
        try:
            f.write_config(inject_between_query_and_fetch=inj)
            code, text, result = f.run(publish=True, script=script)
            refs = f.remote_refs()
            ra = (result or {}).get("remote_after") or {}
            out["cases"].append({"case": name, "script": label, "exit": code, "verdict": (result or {}).get("verdict"),
                                 "reported_ref_a": ra.get("ref_a"), "actual_ref_a": refs.get(T.REF_A),
                                 "reported_ref_b": ra.get("ref_b"), "actual_ref_b": refs.get(T.REF_B),
                                 "reported_matches_actual": ra.get("ref_a") == refs.get(T.REF_A) and ra.get("ref_b") == refs.get(T.REF_B)})
        finally:
            f.close()
Path(sys.argv[1]).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
for r in out["cases"]:
    print(r["case"], r["script"], r["exit"], r["verdict"], "reported==actual:", r["reported_matches_actual"])
