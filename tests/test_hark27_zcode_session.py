"""Hark-派单-27: ZCode AgentSessionAdapter thin bridge - offline checks with a fake host.

No real model, ZCode, Node, network or user configuration is touched: the "host" is
tests/fixtures/hark27/fake_zcode.py, launched exactly the way the bridge launches zcode.cjs.
Each guard has a weakening probe (WEAKENING27) showing the bad outcome comes back when the guard
is removed from a copy of the adapter source.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
FIX = ROOT / "tests" / "fixtures" / "hark27"
sys.path.insert(0, str(FIX))

import scenarios as S  # noqa: E402
from src import agent_session_zcode as Z  # noqa: E402
from src.agent_session import (  # noqa: E402
    AddressMismatch, AgentSessionAdapter, IdempotencyConflict, SessionAddress, SubmitRequest,
    UnsupportedCapability)

SECRET = "sk-hark27-SENTINEL-do-not-echo"
ADAPTER_SRC = (ROOT / "src" / "agent_session_zcode.py").read_text(encoding="utf-8")


def load_variant(replacements):
    """A copy of the adapter module with exact source replacements (weakening only)."""
    src = ADAPTER_SRC
    for old, new in replacements:
        assert src.count(old) == 1, old[:80]
        src = src.replace(old, new)
    name = f"agent_session_zcode_weakened_{abs(hash(src))}"
    mod = types.ModuleType(name)
    mod.__file__ = "<weakened>"
    sys.modules[name] = mod
    exec(compile(src, "<weakened agent_session_zcode>", "exec"), mod.__dict__)
    return mod


class Env:
    def __init__(self, mod=Z):
        self.mod = mod
        self.tmp = tempfile.mkdtemp(prefix="h27-")
        self.ws = os.path.join(self.tmp, "ws")
        shutil.copytree(FIX / "workspace", self.ws)
        self.evidence = os.path.join(self.tmp, "evidence")
        self.homes = os.path.join(self.tmp, "homes")
        os.makedirs(self.homes)
        self.host = os.path.join(self.tmp, "fake_zcode.py")
        shutil.copyfile(FIX / "fake_zcode.py", self.host)
        self.environ = {"PATH": os.environ.get("PATH", ""), "JH27_KEY": "fake-key-value",
                        "USER_SECRET_SHOULD_NOT_PASS": SECRET, "GEMINI_API_KEY": SECRET}

    def config(self, **kw):
        base = dict(instance_id="zcode-local", argv_prefix=(sys.executable, self.host), host_file=self.host,
                    host_sha256=Z.sha256_file(self.host), evidence_root=self.evidence,
                    run_home_root=self.homes, provider_id=S.PROVIDER, model_id=S.MODEL,
                    base_url="http://127.0.0.1:9/never-contacted", key_env="JH27_KEY")
        base.update(kw)
        return self.mod.ZCodeConfig(**base)

    def adapter(self, **kw):
        return self.mod.ZCodeAgentSessionAdapter(self.config(**kw), environ=self.environ)

    def scenario(self, scn):
        with open(os.path.join(self.homes, "scenario.json"), "w", encoding="utf-8") as fh:
            json.dump(scn, fh)

    def request(self, key="k-1", attempt=1, model=S.MODEL, session="new", scope=("read", "search"), text="find greet"):
        return SubmitRequest(task_id="h27-task", attempt=attempt,
                             session=SessionAddress("zcode", "zcode-local", session, model_id=model),
                             instructions=text, workspace=self.ws, allowed_scope=scope, idempotency_key=key)

    def address(self):
        return SessionAddress("zcode", "zcode-local", "new", model_id=S.MODEL)

    def launches(self):
        return sorted(n for n in os.listdir(self.homes) if n.startswith("spy-"))

    def spy(self, n=0):
        with open(os.path.join(self.homes, f"spy-{n}.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def run(self, scn, adapter=None, request=None):
        self.scenario(scn)
        ad = adapter or self.adapter()
        st = ad.submit(request or self.request())
        deadline = time.time() + 60
        while st.state == "running" and time.time() < deadline:
            time.sleep(0.05)
            st = ad.status(self.address(), st.run_id)
        return ad, st

    def events(self):
        with open(os.path.join(self.evidence, "registry.jsonl"), encoding="utf-8") as fh:
            return [json.loads(x)["event"] for x in fh]

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class Base(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.addCleanup(self.e.close)


class TestNormalControl(Base):
    def test_read_search_run_collects_native_evidence(self):
        ad, st = self.e.run(S.normal())
        self.assertEqual(st.state, "declared_complete", st)
        self.assertEqual(self.e.events(), ["reserved", "dispatched", "exited", "collected"])
        col = ad.collect(self.e.address(), st.run_id)
        self.assertEqual(col.declared_status, "host_completed")
        art = col.artifacts[0]
        self.assertEqual((art["native_session_id"], art["native_trace_id"], art["native_turn_id"]),
                         (S.SID, S.TID, S.UID))
        self.assertEqual([c["toolName"] for c in art["tool_calls"]], ["Grep", "Read"])
        self.assertTrue(all(c["results"] for c in art["tool_calls"]))
        self.assertEqual(set(map(tuple, art["observed_models"])), {(S.PROVIDER, S.MODEL)})
        self.assertEqual(art["mechanical_problems"], [])
        self.assertEqual(col.unverified, list(Z.UNVERIFIED_HOST))       # host facts stay unverified
        self.assertEqual(col.usage, {"inputTokens": 1000, "outputTokens": 234})
        self.assertNotEqual(col.declared_status, "succeeded")
        # native NDJSON kept byte for byte (no rewriting into another host's events)
        spy = self.e.spy()
        with open(os.path.join(st.evidence_ref, "stdout.jsonl"), "rb") as fh:
            self.assertEqual(fh.read().hex(), spy["stdout_hex"])

    def test_launch_shape_isolation_and_explicit_binding(self):
        self.e.run(S.normal())
        spy = self.e.spy()
        argv = spy["argv"]
        self.assertEqual(argv[argv.index("--mode") + 1], "plan")
        self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
        self.assertEqual(argv[argv.index("--cwd") + 1], os.path.realpath(self.e.ws))
        denied = argv[argv.index("--disallowed-tools") + 1].split()
        for name in ("Bash", "Write", "Edit", "ApplyPatch", "WebFetch", "Agent", "CreateWorkflow"):
            self.assertIn(name, denied)
        for name in Z.ALLOWED_TOOLS:
            self.assertNotIn(name, denied)
        self.assertNotIn("--model", argv)
        self.assertNotIn("yolo", argv)
        self.assertNotIn("USER_SECRET_SHOULD_NOT_PASS", spy["env_keys"])
        self.assertNotIn("GEMINI_API_KEY", spy["env_keys"])
        self.assertNotIn("JH27_KEY", spy["env_keys"])
        self.assertTrue(spy["home"].startswith(self.e.homes))
        prov = spy["provider"]["config"]
        self.assertEqual(prov["defaultModelSelection"], {"providerId": S.PROVIDER, "modelId": S.MODEL})
        rule = prov["providerConfigRules"]["providerRules"]
        self.assertEqual(len(rule), 1)
        self.assertEqual(rule[0]["config"]["api"]["type"], "anthropic-messages")
        self.assertEqual(rule[0]["config"]["personalModelIds"], [S.MODEL])
        self.assertEqual(rule[0]["config"]["access"]["apiKey"], "fake-key-value")
        for root, _, files in os.walk(self.e.evidence):                  # key never in evidence
            for f in files:
                with open(os.path.join(root, f), "rb") as fh:
                    self.assertNotIn(b"fake-key-value", fh.read())

    def test_capabilities_claim_only_the_four_and_nothing_verified(self):
        ad = self.e.adapter()
        self.assertIsInstance(ad, AgentSessionAdapter)
        cap = ad.capabilities()
        self.assertEqual({k for k, v in cap["capabilities"].items() if v["supported"]},
                         {"capabilities", "submit", "status", "collect"})
        self.assertEqual(cap["verified"], [])
        with self.assertRaises(UnsupportedCapability):
            ad.continue_run(self.e.address(), "zcode-run-000000000000", "more")
        with self.assertRaises(UnsupportedCapability):
            ad.cancel(self.e.address(), "zcode-run-000000000000")


class TestRegistry(Base):
    def test_same_key_same_request_replays_without_relaunch(self):
        ad, st = self.e.run(S.normal())
        again = ad.submit(self.e.request())
        self.assertEqual(again.run_id, st.run_id)
        self.assertIn("replay", again.note)
        fresh = self.e.adapter().submit(self.e.request())                # after reopen too
        self.assertEqual(fresh.run_id, st.run_id)
        self.assertEqual(len(self.e.launches()), 1)

    def test_conflicts_and_rekeying_are_refused(self):
        ad, st = self.e.run(S.normal())
        with self.assertRaises(IdempotencyConflict):
            ad.submit(self.e.request(text="something else"))
        with self.assertRaises(Z.RegistryUnusable):
            ad.submit(self.e.request(key="k-2"))
        with self.assertRaises(Z.RegistryUnusable):
            ad.submit(self.e.request(key="k-3", attempt=2))
        self.assertEqual(len(self.e.launches()), 1)

    def test_wrong_address_raises(self):
        ad, st = self.e.run(S.normal())
        with self.assertRaises(AddressMismatch):
            ad.status(SessionAddress("zcode", "other-instance", "new"), st.run_id)

    def _crash_after_reserve(self):
        reg = Z._Registry(self.e.evidence)
        with reg:
            reg.load()
            reg.append({"event": "reserved", "run_id": "zcode-run-aaaaaaaaaaaa", "idempotency_key": "k-1",
                        "fingerprint": self.e.request().fingerprint(), "snapshot": "{}",
                        "binding": {"app_kind": "zcode", "instance_id": "zcode-local", "session_id": "new",
                                    "task_id": "h27-task", "attempt": 1},
                        "workspace": os.path.realpath(self.e.ws), "provider_id": S.PROVIDER,
                        "model_id": S.MODEL, "api_type": "anthropic-messages", "host_sha256": "0" * 64})
        return "zcode-run-aaaaaaaaaaaa"

    def test_unknown_submit_outcome_is_frozen(self):
        rid = self._crash_after_reserve()
        self.e.scenario(S.normal())
        ad = self.e.adapter()
        st = ad.submit(self.e.request())
        self.assertEqual((st.run_id, st.state), (rid, "unknown"))
        self.assertIn("frozen", st.note)
        self.assertEqual(ad.status(self.e.address(), rid).state, "unknown")
        self.assertEqual(ad.collect(self.e.address(), rid).declared_status, "unreported")
        with self.assertRaises(Z.RegistryUnusable):
            ad.submit(self.e.request(key="k-new"))
        self.assertEqual(self.e.launches(), [])

    def test_dispatched_without_exit_is_unknown_after_reopen(self):
        scn = S.normal()
        self.e.scenario(scn)
        ad = self.e.adapter()
        st = ad.submit(self.e.request())
        ad._live[st.run_id].process.wait()
        reopened = self.e.adapter()                                       # live handle lost
        st2 = reopened.status(self.e.address(), st.run_id)
        self.assertEqual((st2.state, st2.native_state), ("unknown", "outcome-unknown-after-reopen"))
        self.assertEqual(reopened.collect(self.e.address(), st.run_id).declared_status, "unreported")
        self.assertEqual(reopened.submit(self.e.request()).state, "unknown")
        self.assertEqual(len(self.e.launches()), 1)

    def test_damaged_or_missing_registry_is_never_fresh(self):
        cases = {}
        for name in ("corrupt", "partial", "traces_without_registry", "lock_present", "unknown_shape"):
            e = Env()
            try:
                self.e_case = e
                ad, st = e.run(S.normal())
                reg = os.path.join(e.evidence, "registry.jsonl")
                if name == "corrupt":
                    with open(reg, "ab") as fh:
                        fh.write(b"{not json\n")
                elif name == "partial":
                    with open(reg, "ab") as fh:
                        fh.write(b'{"event": "exited"')
                elif name == "traces_without_registry":
                    os.remove(reg)
                elif name == "lock_present":
                    open(os.path.join(e.evidence, "registry.lock"), "w").close()
                else:
                    with open(reg, "ab") as fh:
                        fh.write(b'{"event": "teleported", "run_id": "zcode-run-bbbbbbbbbbbb"}\n')
                with self.assertRaises(Z.RegistryUnusable):
                    e.adapter().submit(e.request(key="k-9"))
                with self.assertRaises(Z.RegistryUnusable):
                    e.adapter().submit(e.request())
                cases[name] = len(e.launches())
            finally:
                e.close()
        self.assertEqual(cases, {k: 1 for k in cases})


class TestCorrelationAndScope(Base):
    def outcome(self, scn):
        ad, st = self.e.run(scn)
        col = ad.collect(self.e.address(), st.run_id)
        return st, col

    def assertFails(self, scn, needle):
        st, col = self.outcome(scn)
        self.assertEqual(st.state, "failed", (st, col.unverified))
        self.assertTrue(any(needle in u for u in col.artifacts[0]["mechanical_problems"]),
                        col.artifacts[0]["mechanical_problems"])
        self.assertNotEqual(col.declared_status, "succeeded")

    def test_event_from_another_session_or_trace(self):
        def other_session(lines):
            lines[4]["sessionId"] = "sess_other"
        self.assertFails(S.mutate(S.normal(), other_session), "not attributable")
        self.setUp()

        def other_trace(lines):
            lines[5]["traceId"] = "trace_other"
        self.assertFails(S.mutate(S.normal(), other_trace), "not attributable")

    def test_result_for_unrequested_call_or_other_turn(self):
        def stray(lines):
            lines[5]["payload"]["toolCallId"] = "call_never"
        self.assertFails(S.mutate(S.normal(), stray), "never requested")
        self.setUp()

        def turn(lines):
            lines[5]["turnId"] = "turn_other"
        self.assertFails(S.mutate(S.normal(), turn), "another turn")

    def test_permission_requests_are_never_approved(self):
        def ask(lines):
            lines.insert(3, S.ev("permission.requested", {"toolCallId": "c9", "toolName": "Bash",
                                                          "riskLevel": "high", "reason": "x", "input": {}}, 50))
        self.assertFails(S.mutate(S.normal(), ask), "asked permission")
        self.setUp()

        def allow(lines):
            lines.insert(3, S.ev("permission.resolved", {"toolCallId": "c9", "decision": "allow"}, 51))
        self.assertFails(S.mutate(S.normal(), allow), "resolved other than deny")

    def test_write_shell_unknown_and_side_effect_tools(self):
        for name, inp in (("Bash", {"command": "ls"}), ("Write", {"file_path": "{WS}/x"}),
                          ("Edit", {"file_path": "{WS}/src/greeter.py"}), ("FancyNewTool", {})):
            with self.subTest(tool=name):
                self.setUp()
                self.assertFails(S.mutate(S.normal(), lambda l: l.__setitem__(slice(3, 3), S.tool(60, "c_x", name, inp))),
                                 "outside read/search")
        self.setUp()

        def rw(lines):
            lines[4]["payload"]["readOnly"] = False
        self.assertFails(S.mutate(S.normal(), rw), "side effects")

    def test_reads_outside_the_workspace_are_detected(self):
        outside = os.path.join(self.e.tmp, "outside.txt")
        with open(outside, "w") as fh:
            fh.write("x")
        os.symlink(outside, os.path.join(self.e.ws, "link.txt"))
        for label, inp, name in (("absolute", {"file_path": outside}, "Read"),
                                 ("dotdot", {"file_path": "{WS}/../outside.txt"}, "Read"),
                                 ("symlink", {"file_path": "{WS}/link.txt"}, "Read"),
                                 ("grep_root", {"pattern": "x", "path": "/"}, "Grep"),
                                 ("glob_parent", {"pattern": "*", "path": "{WS}/.."}, "Glob")):
            with self.subTest(case=label):
                e = Env()
                try:
                    shutil.copyfile(outside, os.path.join(e.tmp, "outside.txt"))
                    os.symlink(os.path.join(e.tmp, "outside.txt"), os.path.join(e.ws, "link.txt"))
                    i = {k: (v.replace(self.e.tmp, e.tmp) if isinstance(v, str) else v) for k, v in inp.items()}
                    ad, st = e.run(S.mutate(S.normal(), lambda l: l.__setitem__(slice(3, 3), S.tool(60, "c_o", name, i))))
                    col = ad.collect(e.address(), st.run_id)
                    self.assertEqual(st.state, "failed")
                    self.assertTrue(any("outside the workspace" in p for p in col.artifacts[0]["mechanical_problems"]))
                finally:
                    e.close()

    def test_model_binding_fallback_and_missing_observation(self):
        self.assertFails(S.normal(model="gemini-2.0-other"), "other than the bound")
        self.setUp()
        self.assertFails(S.mutate(S.normal(), lambda l: l.insert(3, S.ev("session.updated", {
            "fromModelSelection": {"modelId": S.MODEL}, "toModelSelection": {"modelId": "x"}, "reason": "r"}, 52))),
            "fallback")
        self.setUp()

        def drop(lines):
            lines[:] = [x for x in lines if "providerId" not in x.get("payload", {})]
        st, col = self.outcome(S.mutate(S.normal(), drop))
        self.assertEqual(st.state, "unknown")
        self.assertTrue(any("binding unconfirmed" in u for u in col.unverified))

    def test_usage_absent_is_unknown_not_zero(self):
        st, col = self.outcome(S.normal(usage=False))
        self.assertEqual(st.state, "declared_complete")
        self.assertEqual(col.usage, {})
        self.assertIn("usage not reported by the host (unknown, not 0)", col.unverified)
        self.assertFalse(Z.public_summary(col)["usage_known"])

    def test_host_failure_shapes_stay_non_pass(self):
        scn = S.normal()
        scn["exit"] = 1
        st, col = self.outcome(scn)
        self.assertEqual((st.state, col.declared_status), ("failed", "host_not_completed"))
        self.setUp()
        st, col = self.outcome(S.mutate(S.normal(), lambda l: l.insert(2, "this is not json")))
        self.assertEqual(st.state, "unknown")
        self.setUp()
        st, col = self.outcome(S.mutate(S.normal(), lambda l: l.pop()))      # no result line
        self.assertIn(st.state, ("unknown", "failed"))
        self.assertTrue(any("exactly one result" in u for u in col.unverified))


class TestAdmission(Base):
    def refused(self, exc, **req):
        with self.assertRaises(exc):
            self.e.adapter().submit(self.e.request(**req))
        self.assertEqual(self.e.launches(), [])

    def test_request_binding_refusals(self):
        self.e.scenario(S.normal())
        self.refused(ValueError, model="some-default-model")
        self.refused(UnsupportedCapability, scope=("read", "write"))
        self.refused(UnsupportedCapability, scope=("shell",))
        self.refused(UnsupportedCapability, session="sess_existing")
        self.refused(ValueError, key=None)

    def test_env_file_and_overlap_refusals(self):
        self.e.scenario(S.normal())
        with open(os.path.join(self.e.tmp, ".env"), "w") as fh:
            fh.write("X=1\n")
        self.refused(ValueError)
        os.remove(os.path.join(self.e.tmp, ".env"))
        with self.assertRaises(ValueError):
            self.e.adapter(evidence_root=os.path.join(self.e.ws, "ev")).submit(self.e.request())

    def test_config_must_be_explicit(self):
        for kw in ({"model_id": ""}, {"provider_id": ""}, {"mode": "yolo"}, {"api_type": "gemini-native"},
                   {"argv_prefix": ("node", "zcode.cjs")}, {"run_home_root": self.e.evidence},
                   {"base_url": "localhost:8787"}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                self.e.adapter(**kw)

    def test_host_pin_and_missing_key_fail_before_launch(self):
        self.e.scenario(S.normal())
        st = self.e.adapter(host_sha256="0" * 64).submit(self.e.request())
        self.assertEqual((st.state, st.native_state), ("failed", "not-launched:host-pin"))
        e = Env()
        try:
            e.scenario(S.normal())
            e.environ.pop("JH27_KEY")
            st = e.adapter().submit(e.request())
            self.assertEqual((st.state, st.native_state), ("failed", "not-launched:key"))
            self.assertEqual(e.launches(), [])
        finally:
            e.close()
        self.assertEqual(self.e.launches(), [])


class TestPublicSummary(Base):
    def test_host_reflected_text_never_reaches_the_public_view(self):
        def reflect(lines):
            lines.insert(3, S.ev("permission.requested", {"toolCallId": "c9", "toolName": SECRET,
                                                          "riskLevel": "high", "reason": SECRET, "input": {}}, 53))
            lines[3:3] = S.tool(70, "c_" + SECRET, SECRET, {"file_path": SECRET}, SECRET)
            lines[-1]["response"] = SECRET
        scn = S.mutate(S.normal(), reflect)
        scn["stderr"] = SECRET
        ad, st = self.e.run(scn)
        col = ad.collect(self.e.address(), st.run_id)
        pub = json.dumps(Z.public_summary(col))
        self.assertNotIn(SECRET, pub)
        self.assertNotIn("SENTINEL", pub)
        self.assertEqual(json.loads(pub)["state"], "failed")
        self.assertIn(SECRET, json.dumps(col.artifacts[0]))            # private view keeps raw


# ------------------------------------------------------------------ weakening
def _settle(*adapters):
    for ad in adapters:
        for live in ad._live.values():
            live.process.wait(timeout=60)


def _probe_duplicate(mod):
    e = Env(mod)
    try:
        ad, st = e.run(S.normal())
        try:
            ad.submit(e.request())
        except Exception:
            pass
        _settle(ad)
        return {"launches": len(e.launches()), "bad": len(e.launches()) > 1}
    finally:
        e.close()


def _probe_rekey_after_unknown(mod):
    e = Env(mod)
    try:
        TestRegistry._crash_after_reserve(types.SimpleNamespace(e=e))
        e.scenario(S.normal())
        ad = e.adapter()
        try:
            ad.submit(e.request(key="k-new"))
        except Exception:
            pass
        _settle(ad)
        return {"launches": len(e.launches()), "bad": len(e.launches()) > 0}
    finally:
        e.close()


def _probe_traces(mod):
    e = Env(mod)
    try:
        e.run(S.normal())
        os.remove(os.path.join(e.evidence, "registry.jsonl"))
        ad = e.adapter()
        try:
            ad.submit(e.request(key="k-9"))
        except Exception:
            pass
        _settle(ad)
        return {"launches": len(e.launches()), "bad": len(e.launches()) > 1}
    finally:
        e.close()


def _probe_state(mod, scn_fn):
    e = Env(mod)
    try:
        ad, st = e.run(scn_fn(e))
        return {"state": st.state, "bad": st.state == "declared_complete"}
    finally:
        e.close()


def _misassoc(e):
    return S.mutate(S.normal(), lambda l: l[4].__setitem__("sessionId", "sess_other"))


def _ask(e):
    return S.mutate(S.normal(), lambda l: l.insert(3, S.ev("permission.requested", {
        "toolCallId": "c9", "toolName": "Bash", "riskLevel": "high", "reason": "x", "input": {}}, 50)))


def _outside(e):
    return S.mutate(S.normal(), lambda l: l.__setitem__(slice(3, 3), S.tool(60, "c_o", "Read", {"file_path": "/etc/hostname"})))


def _model(e):
    return S.normal(model="gemini-2.0-other")


def _probe_public(mod):
    e = Env(mod)
    try:
        ad, st = e.run(S.mutate(S.normal(), lambda l: l.__setitem__(slice(3, 3), S.tool(70, "c_s", SECRET, {}))))
        pub = json.dumps(mod.public_summary(ad.collect(e.address(), st.run_id)))
        return {"reflected": SECRET in pub, "bad": SECRET in pub}
    finally:
        e.close()


WEAKENING = [
    ("duplicate_dispatch", [('            same = [r for r in reserved if r.get("idempotency_key") == request.idempotency_key]\n',
                             '            same = []\n'),
                            ("            if reserved:\n                raise RegistryUnusable(",
                             "            if False:\n                raise RegistryUnusable(")], _probe_duplicate),
    ("rekey_after_unknown", [("            if reserved:\n                raise RegistryUnusable(",
                              "            if False:\n                raise RegistryUnusable(")], _probe_rekey_after_unknown),
    ("traces_without_registry", [('            if traces:\n                raise RegistryUnusable("evidence root has traces but no registry")\n', "")],
     _probe_traces),
    ("misassociation", [('        if session_id is None or ev.get("sessionId") != session_id or ev.get("traceId") != trace_id:\n',
                         '        if session_id is None:\n')], lambda m: _probe_state(m, _misassoc)),
    ("unknown_approval", [('            fail.append(f"host asked permission for {str(p.get(\'toolName\'))[:40]} (not approved by this bridge)")\n',
                           '            pass\n')], lambda m: _probe_state(m, _ask)),
    ("out_of_workspace", [('    real, root = os.path.realpath(full), os.path.realpath(workspace)\n    return real == root or real.startswith(root + os.sep)\n',
                           '    return True\n')], lambda m: _probe_state(m, _outside)),
    ("model_binding", [('        if (prov, mod) != (provider_id, model_id):\n', '        if False:\n')],
     lambda m: _probe_state(m, _model)),
    ("public_reflection", [('    names = [c.get("toolName") if c.get("toolName") in ALLOWED_TOOLS else "other" for c in tools]\n',
                            '    names = [c.get("toolName") for c in tools]\n')], _probe_public),
]


class TestGuardWeakening(unittest.TestCase):
    def test_each_guard_control_then_weakened(self):
        report = {}
        for name, repl, probe in WEAKENING:
            with self.subTest(guard=name):
                control = probe(Z)
                weakened = probe(load_variant(repl))
                report[name] = {"control": control, "weakened": weakened}
                self.assertFalse(control["bad"], control)
                self.assertTrue(weakened["bad"], weakened)
        print("WEAKENING27 " + json.dumps(report, sort_keys=True))


PWSH = os.environ.get("HARK_PWSH") or shutil.which("pwsh") or (
    "/workspace/work/pwsh/pwsh" if os.path.exists("/workspace/work/pwsh/pwsh") else None)
PS1 = ROOT / "scripts" / "win" / "hark27-zcode-prepare.ps1"


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True).stdout.strip()


@unittest.skipUnless(PWSH, "pwsh not available")
class TestPrepare(unittest.TestCase):
    """The Windows entry, run with official pwsh against a marked local fixture remote."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="h27ps-")
        cls.remote = os.path.join(cls.tmp, "remote")
        subprocess.run(["git", "clone", "-q", str(ROOT), cls.remote], check=True)
        for rel in ("src", "scripts/win", "tests/fixtures/hark27"):
            dst = os.path.join(cls.remote, rel)
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(ROOT / rel, dst, ignore=shutil.ignore_patterns("__pycache__"))
        _git(cls.remote, "add", "-A", "src", "scripts/win", "tests/fixtures/hark27")
        _git(cls.remote, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "fixture")
        cls.good = _git(cls.remote, "rev-parse", "HEAD")
        open(os.path.join(cls.remote, "hark27-test-fixture"), "w").close()
        with open(os.path.join(cls.remote, ".git", "info", "exclude"), "a") as fh:
            fh.write("\nhark27-test-fixture\n")
        drv = os.path.join(cls.remote, "scripts", "win", "hark27_zcode_replay.py")
        cls.probe = os.path.join(cls.tmp, "probe-ran.txt")
        body = open(drv, encoding="utf-8").read()
        with open(drv, "w", encoding="utf-8") as fh:
            fh.write(f"open({cls.probe!r}, 'a').write('ran')\n" + body)
        _git(cls.remote, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-am", "tampered driver")
        cls.bad = _git(cls.remote, "rev-parse", "HEAD")
        cls.cfg = os.path.join(cls.tmp, "test-config.json")
        with open(cls.cfg, "w") as fh:
            json.dump({"remote": cls.remote}, fh)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def ps(self, commit, parent=None, ps1=PS1, extra=()):
        if parent is None:
            parent = tempfile.mkdtemp(prefix="p-", dir=self.tmp)
            shutil.rmtree(parent)
        done = subprocess.run([PWSH, "-NoProfile", "-File", str(ps1), "-Commit", commit, "-Parent", parent,
                               "-Python", sys.executable, "-TestConfig", self.cfg, *extra],
                              capture_output=True, text=True, timeout=300,
                              env={"PATH": os.environ.get("PATH", ""), "HOME": self.tmp, "GEMINI_API_KEY": SECRET})
        return done.returncode, done.stdout, parent

    def test_pins_match_the_fixture_commit(self):
        text = PS1.read_text()
        self.assertIn(_git(self.remote, "rev-parse", self.good + ":src"), text)
        self.assertIn(_git(self.remote, "rev-parse", self.good + ":tests/fixtures/hark27"), text)
        self.assertIn(_git(self.remote, "rev-parse", self.good + ":scripts/win/hark27_zcode_replay.py"), text)

    def test_offline_replay_ok(self):
        code, out, parent = self.ps(self.good)
        self.assertEqual(code, 0, out)
        self.assertIn('"stage": "OFFLINE_REPLAY_OK"', out)
        self.assertIn('"real_model_calls": 0', out)
        self.assertIn("HARK27-PS exit 0", out)
        self.assertNotIn(SECRET, out)

    def test_refusals_and_setup_codes(self):
        code, out, _ = self.ps("abc")
        self.assertEqual(code, 2, out)
        nonempty = tempfile.mkdtemp(prefix="ne-", dir=self.tmp)
        open(os.path.join(nonempty, "x"), "w").close()
        done = subprocess.run([PWSH, "-NoProfile", "-File", str(PS1), "-Commit", self.good, "-Parent", nonempty,
                               "-Python", sys.executable, "-TestConfig", self.cfg], capture_output=True, text=True)
        self.assertEqual(done.returncode, 2, done.stdout)
        envdir = tempfile.mkdtemp(prefix="envroot-", dir=self.tmp)
        open(os.path.join(envdir, ".env"), "w").close()
        code, out, _ = self.ps(self.good, parent=os.path.join(envdir, "work"))
        self.assertEqual(code, 4, out)

    def test_host_pin_is_hash_only(self):
        fake = os.path.join(self.tmp, "zcode.cjs")
        with open(fake, "wb") as fh:
            fh.write(b"process.exit(7) // must never run\n")
        code, out, _ = self.ps(self.good, extra=("-ZcodeCjs", fake))
        self.assertEqual(code, 3, out)
        import hashlib
        digest = hashlib.sha256(open(fake, "rb").read()).hexdigest().upper()
        text = PS1.read_text().replace("A055234A6CA3DA0172616D3987CF3C617B6A26DEB1332D69035300C2A0AE0DE1", digest)
        text = text.replace("15243891", str(os.path.getsize(fake)))
        ps1 = os.path.join(self.tmp, "pinned-copy.ps1")
        with open(ps1, "w") as fh:
            fh.write(text)
        code, out, _ = self.ps(self.good, ps1=ps1, extra=("-ZcodeCjs", fake))
        self.assertEqual(code, 3, out)                       # the Python side keeps its own pin
        self.assertIn('"host_pin": "mismatch"', out)

    def test_tampered_source_never_runs_and_weakening_runs_it(self):
        if os.path.exists(self.probe):
            os.remove(self.probe)
        code, out, _ = self.ps(self.bad)
        self.assertEqual(code, 2, out)
        self.assertFalse(os.path.exists(self.probe))
        text = PS1.read_text()
        weak = text.replace("    Assert-Source $RepoDir $Commit\n", "")
        self.assertNotEqual(weak, text)
        ps1 = os.path.join(self.tmp, "weak.ps1")
        with open(ps1, "w") as fh:
            fh.write(weak)
        code, out, _ = self.ps(self.bad, ps1=ps1)
        ran = os.path.exists(self.probe)
        print("PSWEAKENING27 " + json.dumps({"source_identity": {"control": {"exit": 2, "probe_ran": False},
                                                               "weakened": {"exit": code, "probe_ran": ran}}}))
        self.assertTrue(ran)


class TestStatic(unittest.TestCase):
    def test_no_model_adapter_or_protocol_translation(self):
        src = ADAPTER_SRC
        for banned in ("anthropic_adapter", "AnthropicMessagesAdapter", "ModelAdapter(", "agent_session_gemini",
                       "agent_session_codex", "urllib", "http.client", "requests", "socket"):
            self.assertNotIn(banned, src)
        self.assertTrue(issubclass(Z.ZCodeAgentSessionAdapter, AgentSessionAdapter))


if __name__ == "__main__":
    unittest.main()
