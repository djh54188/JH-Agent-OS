"""CONSTRUCTED stand-in for `node` (Hark-派单-24 tests only; no Node, no CLI, no model).

The test copies this file next to a fake-node.json and makes it executable. Called as
`node --version` it prints the configured version. Called as `node <cli.js> ...` it records
what the host process would have received (variable NAMES, whether the key variable was
present and its length - never its value - and the endpoint variable), optionally sleeps,
and then executes the CONSTRUCTED CLI tests/fixtures/hark21/fake_cli_t01.py.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    with open(os.path.join(HERE, "fake-node.json"), encoding="utf-8") as fh:
        cfg = json.load(fh)
    if sys.argv[1:] == ["--version"]:
        sys.stdout.write(cfg.get("version", "v22.23.4") + "\n")
        return 0
    key = os.environ.get("GEMINI_API_KEY")
    with open(cfg["record"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"argv_tail": sys.argv[2:], "env_names": sorted(os.environ),
                             "key_present": key is not None, "key_len": len(key or ""),
                             "endpoint": os.environ.get("GOOGLE_GEMINI_BASE_URL"),
                             "pid": os.getpid()}) + "\n")
    if "--version" in sys.argv[2:] and cfg.get("unexecutable_after_cli_version_calls"):
        counter = cfg["record"] + ".versions"
        n = (int(open(counter).read()) if os.path.exists(counter) else 0) + 1
        with open(counter, "w") as fh:
            fh.write(str(n))
        if n >= cfg["unexecutable_after_cli_version_calls"]:
            os.chmod(os.path.abspath(sys.argv[0]), 0o644)      # the next launch of this "node" fails
    if "--version" not in sys.argv[2:]:
        time.sleep(float(cfg.get("sleep", 0)))
    os.environ["FAKE_T01_SCENARIO"] = cfg["scenario"]
    os.execv(sys.executable, [sys.executable] + sys.argv[1:])
    return 1


if __name__ == "__main__":
    sys.exit(main())
