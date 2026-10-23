"""Independent extraction of every number in reports/hark-gemini-registry-loss-20261023.md.
Run from the repo root: python reports/hark-gemini-registry-loss-20261023/check_h13.py"""
import json, os, re
E = os.path.dirname(os.path.abspath(__file__))
L = os.path.join(E, "logs")

def read(n):
    with open(os.path.join(L, n), encoding="utf-8") as fh:
        return fh.read()

def ran(n):
    t = read(n)
    return re.findall(r"^Ran (\d+) tests?", t, re.M)[-1], re.findall(r"^(OK.*|FAILED.*)$", t, re.M)[-1]

for n in ("baseline-suite.txt", "postcommit-suite.txt"):
    print(n, *ran(n))
b = set(read("baseline-failset.txt").splitlines()); p = set(read("postcommit-failset.txt").splitlines())
print("failset baseline", len(b), "postcommit", len(p), "identical", b == p)
for n in ("hark13-run1.txt", "hark13-run2.txt", "hark13-run3.txt", "h12-h11-jh018.txt"):
    print(n, *ran(n))
base = [json.loads(l) for l in read("repro-baseline.txt").splitlines()][:-1]
post = [json.loads(l) for l in read("repro-postcommit.txt").splitlines()][:-1]
assert [c["case"] for c in base] == [c["case"] for c in post]
print("repro cases", len(base))
for x, y in zip(base, post):
    print("-", x["case"], "| baseline launches", x["observed"].get("new_launches"),
          x["observed"].get("outcome"), "| candidate launches", y["observed"].get("new_launches"),
          y["observed"].get("outcome"), y["observed"].get("type", y["observed"].get("state", "")))
r12a = [json.loads(l) for l in open(os.path.join(E, "..", "hark-gemini-admission-20261022", "logs", "repro-postcommit.txt"), encoding="utf-8")][:-1]
r12b = [json.loads(l) for l in read("repro12-candidate.txt").splitlines()][:-1]
strip = lambda o: {k: v for k, v in o.items() if k not in ("results", "run_id")}
print("hark12 repro cases", len(r12b), "differing from d9cbbf7 log", sum(strip(x["observed"]) != strip(y["observed"]) for x, y in zip(r12a, r12b)))
hp = read("host-probe-postcommit.txt")
print("host-probe PASS", hp.count("\nPASS "), "FAIL", hp.count("\nFAIL "))
with open(os.path.join(E, "host-probe-postcommit", "synthetic-requests.jsonl"), encoding="utf-8") as fh:
    print("synthetic endpoint requests", sum(1 for l in fh if l.strip()), "(real model calls: 0)")
