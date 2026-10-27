"""CONSTRUCTED `git` stand-in (Hark-派单-25 tests only). Placed first on PATH as `git`; records
the NAMES of the variables it received and whether any value contains the test sentinel (never
the values), then executes the real git. Its settings sit next to it (git-spy.json) because the
environment under test may be scrubbed."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "git-spy.json"), encoding="utf-8") as fh:
    cfg = json.load(fh)
sentinels = cfg["sentinels"]
with open(cfg["record"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"argv": sys.argv[1:], "env_names": sorted(os.environ),
                         "sentinel_in_env": any(s in v for v in os.environ.values() for s in sentinels)}) + "\n")
os.execv(cfg["real_git"], [cfg["real_git"]] + sys.argv[1:])
