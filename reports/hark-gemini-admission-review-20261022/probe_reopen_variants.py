"""Fresh reader after source loss: repeat direct cases and nonempty tail loss."""
import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("frozen", ROOT / "frozen_probe_boundaries.py")
frozen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(frozen)


def main():
    out = ROOT / "reopen-boundary-02"
    out.mkdir(exist_ok=False)
    rows = []
    for mode in ("fresh", "normal", "bad-json", "truncate-empty", "delete", "whitespace",
                 "reserved-only", "lost-last-run", "two-reserved-prefixes"):
        h = frozen.Harness(out / mode)
        owner = h.adapter()
        registry = h.evidence / "registry.jsonl"
        first = None
        request = h.request()
        if mode != "fresh":
            first = owner.submit(request)
            original = registry.read_bytes()
            if mode == "bad-json": registry.write_bytes(b"{bad\n")
            elif mode == "truncate-empty": registry.write_bytes(b"")
            elif mode == "delete": registry.unlink()
            elif mode == "whitespace": registry.write_bytes(b" \n\n")
            elif mode == "reserved-only": registry.write_bytes(original.splitlines(keepends=True)[0])
            elif mode in ("lost-last-run", "two-reserved-prefixes"):
                request = h.request("k2")
                first = owner.submit(request)
                if mode == "lost-last-run": registry.write_bytes(original)
                else:
                    records = [json.loads(l) for l in registry.read_bytes().splitlines()]
                    registry.write_bytes(b"".join(json.dumps(r).encode() + b"\n" for r in records if r["event"] == "reserved"))
        before = h.launches
        retained = list((h.evidence / "runs").iterdir()) if (h.evidence / "runs").exists() else []
        result = {"case": mode, "retained_run_dir_count": len(retained), "first_run": first.run_id if first else None}
        try:
            a = h.adapter()
            s = a.submit(request)
            result.update(run_id=s.run_id, state=s.state, same_run=(s.run_id == first.run_id) if first else None)
        except Exception as e: result.update(exception=type(e).__name__, message=str(e))
        result["new_launches"] = h.launches - before
        rows.append(result)
    report = {"external_processes": 0, "model_calls": 0, "cases": rows}
    (out / "results.json").write_bytes(json.dumps(report, indent=2).encode())
    print(json.dumps(report, indent=2))


if __name__ == "__main__": main()
