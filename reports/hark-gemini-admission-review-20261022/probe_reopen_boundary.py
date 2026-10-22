"""Independent durable-source loss variants; all launches are memory substitutes."""
import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("frozen", ROOT / "frozen_probe_boundaries.py")
frozen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(frozen)


def main():
    out = ROOT / "reopen-boundary-01"
    out.mkdir(exist_ok=False)
    rows = []
    for mode in ("normal", "bad-json", "truncate-empty", "delete", "whitespace", "reserved-only"):
        h = frozen.Harness(out / mode)
        owner = h.adapter()
        first = owner.submit(h.request())
        registry = h.evidence / "registry.jsonl"
        original = registry.read_bytes()
        if mode == "bad-json": registry.write_bytes(b"{bad\n")
        elif mode == "truncate-empty": registry.write_bytes(b"")
        elif mode == "delete": registry.unlink()
        elif mode == "whitespace": registry.write_bytes(b" \n\n")
        elif mode == "reserved-only": registry.write_bytes(original.splitlines(keepends=True)[0])
        # Evidence run dir still exists; only the authoritative registry is lost.
        retained_runs = [p.name for p in (h.evidence / "runs").iterdir() if p.is_dir()]
        before = h.launches
        result = {"case": mode, "first_run_id": first.run_id, "retained_run_dirs": retained_runs}
        try:
            reopened = h.adapter()
            again = reopened.submit(h.request())
            result.update(second_run_id=again.run_id, same_run=again.run_id == first.run_id, state=again.state)
        except Exception as e:
            result.update(exception=type(e).__name__, message=str(e))
        result["new_launches_after_reopen"] = h.launches - before
        rows.append(result)
    report = {"real_processes": 0, "model_calls": 0, "cases": rows}
    (out / "results.json").write_bytes(json.dumps(report, indent=2).encode())
    print(json.dumps(report, indent=2))


if __name__ == "__main__": main()
