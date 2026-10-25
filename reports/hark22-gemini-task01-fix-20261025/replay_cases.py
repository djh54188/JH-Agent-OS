"""Hark-派单-22: replay every counter-example (control only, no weakening) and the three
semantic controls; print each case's overall result and its non-pass items.
CONSTRUCTED inputs only (fake CLI, no model/network). Usage:
    python reports/hark22-gemini-task01-fix-20261025/replay_cases.py"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
from tests import test_hark21_gemini_task01 as T  # noqa: E402


class Case(T._Base):
    def runTest(self):
        pass


def one(label, make):
    t = Case()
    t.setUp()
    try:
        t.sentinel = None
        kwargs = make(t)
        base = kwargs.pop("_base", set())
        T.check_run.WEAKEN.clear()
        T.check_run.WEAKEN.update(base)
        with T.Observe() as obs:
            r = t.check(**kwargs)
        T.check_run.WEAKEN.clear()
        bad = [f"{i['check']}={i['status']}" for i in r["mechanical"]["items"] if i["status"] != "pass"]
        line = {"case": label, "overall": r["mechanical"]["overall"], "base_weakened": sorted(base),
                "non_pass": bad, "semantic_review": r["semantic_review"]["status"],
                "task_outcome": r["task_outcome"]}
        if t.sentinel:
            line["sentinel_access"] = len(obs.touched(t.sentinel))
        return line
    finally:
        t.tearDown()


def semantic(delivery):
    def make(t):
        t.run_case(delivery=delivery)
        return {}
    return make


rows = [one(f"control.{n}", semantic(d)) for n, d in (("good", T.build.GOOD_DELIVERY),
                                                       ("reversed", T.build.REVERSED_DELIVERY),
                                                       ("b7_omitted", T.build.B7_OMITTED_DELIVERY))]
rows += [one(label, make) for label, _flag, make in T.WEAKENING if "." in label]
for row in rows:
    print(json.dumps(row, ensure_ascii=False, sort_keys=True))
