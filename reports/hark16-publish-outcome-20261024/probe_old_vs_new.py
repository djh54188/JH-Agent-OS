"""Hark-派单-16: same-input comparison of the Hark15 candidate (f032a6a) and the Hark16 script for R4.

Usage (repo root; HARK14_PWSH or pwsh on PATH):
    python reports/hark16-publish-outcome-20261024/probe_old_vs_new.py <out.json>
The old script gets the SAME test-only receipt hook (Invoke-PublishPush, copied verbatim from the new
script) and the same "re-read unavailable" switch; nothing else in its decision logic is changed.
Only local temporary fixtures are used; no network, no real GitHub targets.
"""
import json, re, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests import test_hark14_handoff_sync as T  # noqa: E402

OLD_SHA = "f032a6a864ceb9c32e7620160f084e7800180b6f"
new_text = T.SCRIPT.read_text(encoding="utf-8")
hook = re.search(r"function Invoke-PublishPush\(.*?\n\}\n", new_text, re.S).group(0)
old = subprocess.run(["git", "show", f"{OLD_SHA}:scripts/win/hark14-handoff-sync.ps1"], cwd=ROOT,
                     capture_output=True, check=True).stdout.decode()
old = old.replace("function Test-RemoteBlob(", hook + "function Test-RemoteBlob(", 1)
push_line = "$push = Invoke-Git -GitArgs (@('push', '--atomic', '--porcelain') + (Get-CreateOnlyLeaseArgs $leaseRefs) + @('origin') + $specs) -AllowFail"
assert old.count(push_line) == 1
old = old.replace(push_line, "$push = Invoke-PublishPush (@('push', '--atomic', '--porcelain') + (Get-CreateOnlyLeaseArgs $leaseRefs) + @('origin') + $specs)")
add = "Invoke-Git -GitArgs @('remote', 'add', 'origin', $RemoteUrl) | Out-Null"
assert old.count(add) == 2
i = old.rindex(add)
old = old[:i] + ("$vu = $RemoteUrl; if ($IsTest -and $Cfg.ContainsKey('verify_unavailable') -and $Cfg.verify_unavailable) "
                 "{ $vu = Join-Path $RemoteUrl 'verify-unavailable-for-test' }\nInvoke-Git -GitArgs @('remote', 'add', 'origin', $vu) | Out-Null") + old[i + len(add):]
tmp = Path(tempfile.mkdtemp(prefix="hark16-probe-"))
OLD = tmp / "old-f032a6a-with-same-hooks.ps1"
OLD.write_text(old, encoding="utf-8")

HUNG = "fatal: the remote end hung up unexpectedly"
cases = {
    "committed_receipt_lost_reread_unavailable": dict(fake_push={"mode": "commit_hide", "exit": 128, "output": HUNG}, verify_unavailable=True),
    "not_committed_receipt_lost_reread_unavailable": dict(fake_push={"mode": "nocommit_hide", "exit": 128, "output": HUNG}, verify_unavailable=True),
    "committed_receipt_lost_reread_complete": dict(fake_push={"mode": "commit_hide", "exit": 128, "output": HUNG}),
    "complete_atomic_rejection": "inject",
}
out = {"old_script": f"{OLD_SHA} + identical test hooks", "new_script": "scripts/win/hark14-handoff-sync.ps1", "cases": []}
for name, cfg in cases.items():
    for label, script in (("old", OLD), ("new", T.SCRIPT)):
        f = T.Fixture()
        try:
            c = {"inject_before_push": [{"ref": T.REF_A, "sha": f.base}]} if cfg == "inject" else cfg
            f.write_config(**c)
            code, text, result = f.run(publish=True, script=script)
            refs = f.remote_refs()
            out["cases"].append({"case": name, "script": label, "exit": code, "verdict": (result or {}).get("verdict"),
                                 "claims_no_update": ("updated no remote ref" in text) or ("nothing was written" in text),
                                 "fixture_really_written": refs.get(T.REF_A) == f.evidence or (T.REF_B in refs and refs[T.REF_B] != f.base)})
        finally:
            f.close()
Path(sys.argv[1]).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
for r in out["cases"]:
    print(r)
