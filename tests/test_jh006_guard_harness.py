"""Regression tests for the capture-guard mutation harness itself.

Every session log below is SYNTHETIC test data (thread ids prefixed
"test-bind-"); nothing here proves a real host executed anything. These
tests pin the mechanics the entry mutations rely on: import aliases
(e.g. cli_main) reach the mutated implementation, assembly failures
restore consumers, sys.modules entries, and parent attrs, and
function-local imports resolve to the mutated module while a case runs.
Production complete stays closed.
"""
import builtins
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import src.cli as real_cli
import test_jh006_code_delivery as fixtures
import test_jh006_host_entry as hostentry


def _load_harness():
    spec = importlib.util.spec_from_file_location(
        "verify_jh006_capture_guards",
        ROOT / "scripts" / "verify_jh006_capture_guards.py")
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    return harness


HARNESS = _load_harness()

ENTRY_OLD = '                    expected_argv=invocation["argv"],'
ENTRY_NEW = '                    expected_argv=[],'
ENTRY_TESTS = [(hostentry.TestHostEntryRegister,
                "test_register_persists_binding_and_event")]


class TestHarnessAliasBinding(unittest.TestCase):
    def test_entry_mutation_detected_and_local_import_hits_mutation(self):
        real_main = real_cli.main
        real_mod = sys.modules["src.cli"]
        seen = {}
        target_cls = hostentry.TestHostEntryRegister
        orig = target_cls.test_register_persists_binding_and_event

        def wrapper(self):
            import src.cli as loaded
            from src.cli import main as fresh_main
            import src as parent_pkg
            seen["loaded_main_is_mutation"] = loaded.main is not real_main
            seen["fresh_main_is_mutation"] = fresh_main is not real_main
            seen["parent_attr_is_mutation"] = parent_pkg.cli is not real_mod
            return orig(self)



        target_cls.test_register_persists_binding_and_event = wrapper
        try:
            result = HARNESS.run_case(
                "entry-register-plan-argv", HARNESS.climod,
                ENTRY_OLD, ENTRY_NEW, ENTRY_TESTS)
        finally:
            target_cls.test_register_persists_binding_and_event = orig
        self.assertTrue(seen.get("loaded_main_is_mutation"),
                        "function-local import missed the mutated cli")
        self.assertTrue(seen.get("fresh_main_is_mutation"),
                        "from-import missed the mutated cli")
        self.assertTrue(seen.get("parent_attr_is_mutation"),
                        "parent package attr missed the mutated cli")
        self.assertEqual(result["tests_run"], 1)
        self.assertEqual(result["errors"], 0, result["detail"])
        self.assertTrue(result["detected"])


class TestHarnessAssemblySafety(unittest.TestCase):
    def test_assembly_failure_restores_consumers_and_sys_modules(self):
        real_main = fixtures.cli_main
        real_mod = sys.modules.get("src.cli")

        def probe(*args, **kwargs):
            raise AssertionError("must never run")

        probe.__module__ = "src.cli"
        probe.__name__ = "no_such_cli_attr_xyz"
        fixtures._harness_probe_xyz = probe
        try:
            with self.assertRaisesRegex(RuntimeError, "alias anchor"):
                HARNESS.run_case(
                    "entry-register-plan-argv", HARNESS.climod,
                    ENTRY_OLD, ENTRY_NEW, ENTRY_TESTS)
        finally:
            del fixtures._harness_probe_xyz
        self.assertIs(fixtures.cli_main, real_main)
        self.assertIs(sys.modules.get("src.cli"), real_mod)


class TestHarnessPartialAssemblyRecovery(unittest.TestCase):
    def test_partial_assembly_failure_restores_everything(self):
        real_main = fixtures.cli_main
        real_mod = sys.modules.get("src.cli")
        real_parent_attr = getattr(sys.modules.get("src"), "cli", None)
        before_fixtures = dict(vars(fixtures))
        before_hostentry = dict(vars(hostentry))
        consumer_patches = []
        real_setattr = builtins.setattr

        def flaky_setattr(obj, name, value):
            if obj is fixtures or obj is hostentry:
                consumer_patches.append((obj, name, value))
                if len(consumer_patches) == 2:
                    first_obj, first_name, first_value = consumer_patches[0]
                    self.assertIs(
                        getattr(first_obj, first_name), first_value,
                        "no consumer replacement had landed before injection")
                    raise RuntimeError("injected assembly failure")
            return real_setattr(obj, name, value)

        with mock.patch.object(builtins, "setattr", side_effect=flaky_setattr):
            with self.assertRaisesRegex(RuntimeError, "injected assembly failure"):
                HARNESS.run_case(
                    "entry-register-plan-argv", HARNESS.climod,
                    ENTRY_OLD, ENTRY_NEW, ENTRY_TESTS)
        self.assertGreaterEqual(len(consumer_patches), 2)
        for key, val in before_fixtures.items():
            self.assertIs(vars(fixtures).get(key), val, key)
        self.assertEqual(set(vars(fixtures)), set(before_fixtures))
        for key, val in before_hostentry.items():
            self.assertIs(vars(hostentry).get(key), val, key)
        self.assertEqual(set(vars(hostentry)), set(before_hostentry))
        self.assertIs(fixtures.cli_main, real_main)
        self.assertIs(sys.modules.get("src.cli"), real_mod)
        self.assertIs(
            getattr(sys.modules.get("src"), "cli", None), real_parent_attr)


if __name__ == "__main__":
    unittest.main()
