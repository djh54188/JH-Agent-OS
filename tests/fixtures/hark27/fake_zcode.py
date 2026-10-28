"""Fake ZCode host (Hark-派单-27 tests and offline replay only; NO model, NO network).

Accepts the argv shape the bridge sends, reads its scenario from <run_home_root>/scenario.json
(run_home_root = parent of $HOME), writes the scenario's NDJSON to stdout byte for byte, and
records argv, environment KEY NAMES, the isolated provider file and the bytes it wrote into
<run_home_root>/spy-<n>.json. Placeholders in the scenario: {WS} the --cwd value, {HOME}."""
import json
import os
import sys

argv = sys.argv[1:]
home = os.environ.get("HOME", "")
root = os.path.dirname(home)
with open(os.path.join(root, "scenario.json"), encoding="utf-8") as fh:
    scenario = json.load(fh)
cwd = argv[argv.index("--cwd") + 1] if "--cwd" in argv else os.getcwd()


def fill(value):
    if isinstance(value, str):
        return value.replace("{WS}", cwd).replace("{HOME}", home)
    if isinstance(value, list):
        return [fill(v) for v in value]
    if isinstance(value, dict):
        return {k: fill(v) for k, v in value.items()}
    return value


out = b""
for item in scenario.get("lines", []):
    out += (item if isinstance(item, str) else json.dumps(fill(item), ensure_ascii=False)).encode("utf-8") + b"\n"
provider = None
pfile = os.environ.get("ZCODE_PERSONAL_PROVIDER_CONFIG_FILE")
if pfile and os.path.exists(pfile):
    with open(pfile, encoding="utf-8") as fh:
        provider = json.load(fh)
n = len([x for x in os.listdir(root) if x.startswith("spy-")])
with open(os.path.join(root, f"spy-{n}.json"), "w", encoding="utf-8") as fh:
    json.dump({"argv": argv, "env_keys": sorted(os.environ), "cwd": os.getcwd(), "provider": provider,
               "home": home, "stdout_hex": out.hex()}, fh)
sys.stdout.buffer.write(out)
sys.stdout.flush()
if scenario.get("stderr"):
    sys.stderr.write(scenario["stderr"])
sys.exit(int(scenario.get("exit", 0)))
