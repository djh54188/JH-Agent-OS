"""Independent extraction of every number in reports/hark-gemini-admission-20261022.md.
Run from the repo root: python reports/hark-gemini-admission-20261022/check_h12.py"""
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
for n in ("postcommit-hark12-run1.txt", "postcommit-hark12-run2.txt", "postcommit-hark12-run3.txt",
          "postcommit-hark11-jh018.txt", "postcommit-weakening.txt"):
    print(n, *ran(n))
w = [l for l in read("postcommit-weakening-detail.txt").splitlines() if "\tcontrol=" in l]
print("weakenings", len(w), "control pass", sum("control=pass" in l for l in w),
      "detected", sum("weakened=detected" in l for l in w))
base = [json.loads(l) for l in read("repro-baseline.txt").splitlines()][:-1]
post = [json.loads(l) for l in read("repro-postcommit.txt").splitlines()][:-1]
assert [c["case"] for c in base] == [c["case"] for c in post]
print("repro cases", len(base))
for x, y in zip(base, post):
    print("-", x["case"], "| baseline:", json.dumps(x["observed"], sort_keys=True), "| candidate:",
          json.dumps(y["observed"], sort_keys=True))
hp = read("host-probe-postcommit.txt")
print("host-probe PASS", hp.count("\nPASS "), "FAIL", hp.count("\nFAIL "))
with open(os.path.join(E, "host-probe-postcommit", "synthetic-requests.jsonl"), encoding="utf-8") as fh:
    print("synthetic endpoint requests", sum(1 for l in fh if l.strip()), "(real model calls: 0)")
