"""CONSTRUCTED probe (Hark-派单-25 tests only): if it ever runs, it writes a harmless sentinel file
named by probe.json next to the repo directory. A refusal must happen before this file runs."""
import json
import os

here = os.path.dirname(os.path.realpath(__file__))
repo = os.path.dirname(os.path.dirname(here))
with open(os.path.join(os.path.dirname(repo), "probe.json"), encoding="utf-8") as fh:
    cfg = json.load(fh)
with open(cfg["sentinel_file"], "a", encoding="utf-8") as fh:
    fh.write("probe executed\n")
