"""Summarise frozen-probe results.json per case and check a/b repeatability.
usage: compare_probe.py <results_a.json> [<results_b.json>]"""
import json, re, sys
RAND = re.compile(r"[0-9a-f]{16}|codex-run-\d+-[0-9a-f]{8}|[0-9a-f]{8}")
def norm(v):
    if isinstance(v, dict): return {k: norm(x) for k, x in v.items() if k not in ("probe_id",)}
    if isinstance(v, list): return [norm(x) for x in v]
    if isinstance(v, str): return RAND.sub("<id>", v)
    return v
def summary(path):
    d = json.load(open(path)); out = []
    for r in d["results"]:
        a = r.get("attempt") or r.get("second") or {}
        row = r.get("row") or {}
        out.append({"case": r["case"], "thread_starts": r.get("thread_starts"),
                    "first": "accepted" if (r.get("first") or {}).get("result") else (r.get("first") or {}).get("refused"),
                    "attempt": a.get("refused") or ("accepted" if a.get("result") else None),
                    "history_bytes_unchanged": r.get("history_bytes_unchanged"),
                    "files_in_outside": r.get("files_in_outside"), "dirs_in_outside": r.get("directories_in_outside"),
                    "verifier_exit": r.get("exit_code"), "verifier_verdict": r.get("verdict"), "row_verdict": row.get("verdict")})
    return d, out
d, a = summary(sys.argv[1])
for row in a: print(json.dumps({k: v for k, v in row.items() if v is not None}, ensure_ascii=False))
print("cases:", len(a), "platform:", d["platform"])
if len(sys.argv) > 2:
    _, b = summary(sys.argv[2])
    print("a_b_identical_after_normalisation:", norm(json.load(open(sys.argv[1]))["results"]) == norm(json.load(open(sys.argv[2]))["results"]))
