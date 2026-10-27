"""Run the entry in-process with an audit hook that records every file it opens (Hark-派单-25
tests only). argv: <out.json> <entry.py> <entry args...>"""
import json
import os
import runpy
import sys

out_path, entry = sys.argv[1], sys.argv[2]
opened = []


def hook(event, args):
    if event == "open" and args and isinstance(args[0], (str, bytes)):
        opened.append(os.fsdecode(args[0]))


sys.addaudithook(hook)
sys.argv = [entry] + sys.argv[3:]
code = 0
try:
    runpy.run_path(entry, run_name="__main__")
except SystemExit as exc:
    code = exc.code if isinstance(exc.code, int) else 1
finally:
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"opened": opened, "exit": code}, fh)
sys.exit(code)
