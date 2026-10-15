"""Behavior checks for the n2-code-sortfix fixture (not a unittest module).

Usage: python tests/fixtures/n2/code_sortfix/check_widget.py --target <widget.py>
Exit 0 when all pinned cases pass, 1 otherwise. Never touches real sources.
The file name intentionally does not match test_*.py so repo test discovery
never collects it.
"""
import argparse
import importlib.util


CASES = [
    ([{"region": "b", "sales": 2}, {"region": "a", "sales": 2},
      {"region": "c", "sales": 1}], 2, ["a", "b"]),
    ([{"region": "b", "sales": 2}, {"region": "a", "sales": 2},
      {"region": "c", "sales": 1}], 9, ["a", "b", "c"]),
    ([], 3, []),
    ([{"region": "a", "sales": 1}], 0, []),
]


def main():
    parser = argparse.ArgumentParser(description="Pinned behavior checks for widget.py")
    parser.add_argument("--target", required=True, help="Path to widget.py under test")
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("fixture_widget", args.target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    failures = 0
    for index, (records, n, expected) in enumerate(CASES):
        try:
            got = module.top_regions([dict(r) for r in records], n)
        except Exception as exc:  # noqa: BLE001 - fixture reports, never raises
            print(f"case {index}: ERROR {exc!r}")
            failures += 1
            continue
        if got != expected:
            print(f"case {index}: FAIL got={got!r} expected={expected!r}")
            failures += 1
        else:
            print(f"case {index}: ok")
    print("PASS" if failures == 0 else "FAIL")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
