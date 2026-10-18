"""Meta-mutation of the R2 harness: each meta-mutant weakens one judging rule in
ONE verifier script inside a disposable copy; the regression suite must turn red."""
import json, os, shutil, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor
ROOT = sys.argv[1]
PY = sys.executable
META = [
 ("message_only_counted", 'if "behavioral" in verdicts:', 'if verdicts & {"behavioral", "message_only"}:'),
 ("undetermined_counted", '            return "message_only_failure"\n        return "undetermined_failure"', '            return "message_only_failure"\n        return "behavioral_failure"'),
 ("no_opaque_replay", "_ORIG[method](_state.case, blind(args[0]), blind(args[1]))", "_ORIG[method](_state.case, args[0], args[1])"),
 ("missing_evidence_counted", 'if len(evidence) != len(run["failures"]):\n            return "undetermined_failure"', 'if len(evidence) != len(run["failures"]):\n            return "behavioral_failure"'),
 ("provenance_off", "if _mon is not None:", "if False:"),
 ("text_derived_check_off", "if i < len(exprs) and text_derived(exprs[i]):", "if False:"),
 ("raises_regex_counted", 'ev = ("message_only", "expected exception raised; only its message differs")', 'ev = ("behavioral", "x")'),
 ("errors_counted", 'if run["errors"]:\n        return "error"', 'if run["errors"]:\n        return "behavioral_failure"'),
 ("identity_ignored", 'if identity != "pass":\n        return "identity_not_green"', 'if False:\n        return "identity_not_green"'),
 ("any_red_baseline_blocked", 'return "platform_blocked" if platform_note else "baseline_not_green"', 'return "platform_blocked"'),
 ("red_baseline_exit_2", 'elif blocked and detected + blocked == len(rows):', 'elif detected + blocked + counts.get("baseline_not_green", 0) == len(rows):'),
 ("platform_evidence_any_error", "        else:\n            return None\n    return \"; \".join(texts)", "        else:\n            texts.append(last)\n    return \"; \".join(texts)"),
]
SCRIPTS = ["verify_phase4_guards", "verify_jh006_guards"]
def run(job):
    name, old, new, script = job
    tmp = tempfile.mkdtemp(prefix="jh-meta-")
    try:
        repo = os.path.join(tmp, "repo")
        shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns(".git", "data", "__pycache__"))
        p = os.path.join(repo, "scripts", script + ".py")
        raw = open(p, "rb").read().decode()
        o, n = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
        c = raw.count(o)
        if c != 1:
            return (name, script, f"ANCHOR x{c}", "")
        open(p, "wb").write(raw.replace(o, n, 1).encode())
        r = subprocess.run([PY, "-B", "-m", "unittest", "tests.test_verifier_mutation_harness"],
                           cwd=repo, capture_output=True, text=True, timeout=1200)
        tail = r.stderr.strip().splitlines()[-1]
        failed = [l for l in r.stderr.splitlines() if l.startswith(("FAIL:", "ERROR:"))]
        return (name, script, f"exit {r.returncode} {tail}", "; ".join(sorted(set(failed)))[:600])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
jobs = [(n, o, w, s) for n, o, w in META for s in SCRIPTS]
with ThreadPoolExecutor(8) as ex:
    res = list(ex.map(run, jobs))
print("# Meta-mutation of the R2 harness (disposable copies; regression must turn red)")
print(f"# python {sys.version.split()[0]}")
for name, script, status, failed in res:
    print(f"{script:22} {name:28} {status}\n    {failed}")
print("ALL_RED" if all(s.startswith("exit 1") for _, _, s, _ in res) else "SOME_GREEN")
