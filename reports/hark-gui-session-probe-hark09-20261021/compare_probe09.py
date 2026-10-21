"""Summarise frozen 49a7d1e probe_workspace_binding.py results: a/b identity (minus python,
output dir and random probe ids) and per-case protocol cwd / outcome / reported listing."""
import json, re, sys
from pathlib import Path
d = Path(__file__).resolve().parent / "frozen-probe"
def load(label, x):
    t = (d / f"probe09-{label}-{x}.json").read_text(encoding="utf-8")
    t = re.sub(r"workspace-review-fresh-[ab]", "OUT", t); t = re.sub(r"[0-9a-f]{16}", "<ID>", t)
    data = json.loads(t); data.pop("python"); return data
for label in ("baseline", "postcommit"):
    a, b = load(label, "a"), load(label, "b")
    print(label, "a==b:", a == b)
    for r in a["rows"]:
        proto = r.get("protocol") or []
        doubled = any(p["effective_native_cwd"] != r.get("expected_workspace") for p in proto)
        print(f"  {r['case']:26} refused={r.get('refused')} dispatches={r['offline_dispatches']} "
              f"supplied_abs={all(p['supplied_cwd'].startswith('/') or ':' in p['supplied_cwd'][:3] for p in proto) if proto else None} "
              f"effective_differs={doubled if proto else None} outcome={r.get('outcome')} files={r.get('reported_workspace_files')}")
