"""Hark-派单-18 process stand-in (SYNTHETIC; used only by tests).

Runs the existing fake_cli.py unchanged and then rewrites only its stdout/stderr
bytes, so the native stream stays the captured 0.65.0 template:
  h18_prefix_b64   raw bytes written before the native stream (CONSTRUCTED input)
  h18_insert       list of {"after_line": n, "b64": ...}: raw bytes after the n-th line
  h18_stderr_b64   raw bytes written to stderr
Everything else (exit code, session, edits, mutate, chat record) is fake_cli.py's.
"""
import base64
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fake_cli  # noqa: E402


def main() -> int:
    with open(os.environ["FAKE_GEMINI_SCENARIO"], encoding="utf-8") as fh:
        scenario = json.load(fh)
    real = sys.stdout
    buffer = io.BytesIO()
    wrapper = io.TextIOWrapper(buffer, encoding="utf-8", newline="")
    sys.stdout = wrapper
    try:
        code = fake_cli.main()
        wrapper.flush()
    finally:
        sys.stdout = real
        wrapper.detach()
    data = buffer.getvalue()
    if sys.argv[1:] != ["--version"]:
        lines = data.split(b"\n")[:-1]
        inserts = {i["after_line"]: base64.b64decode(i["b64"]) for i in scenario.get("h18_insert", [])}
        rebuilt = b"".join(l + b"\n" + inserts.get(n, b"") for n, l in enumerate(lines, start=1))
        data = base64.b64decode(scenario.get("h18_prefix_b64", "")) + rebuilt
        if scenario.get("h18_stderr_b64"):
            sys.stderr.buffer.write(base64.b64decode(scenario["h18_stderr_b64"]))
            sys.stderr.flush()
    sys.stdout.buffer.write(data)
    sys.stdout.flush()
    return code


if __name__ == "__main__":
    sys.exit(main())
