"""Builders for CONSTRUCTED Hark-派单-21 inputs (tests only).

Everything here is constructed: the export is real (bytes of the fixed commit),
the adapter and its registry/evidence are the real adapter code, but the "CLI"
is tests/fixtures/hark21/fake_cli_t01.py and every tool output and delivery
text is written by these builders. Nothing here is a real model's work and none
of it may be presented as a real-path positive control.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts", "gemini_task01"))

import export_source  # noqa: E402
from src import agent_session_gemini as gem  # noqa: E402
from src.agent_session import SessionAddress, SubmitRequest  # noqa: E402

PKG_DIR = os.path.join(ROOT, "scripts", "gemini_task01")
PACKAGE = os.path.join(PKG_DIR, "package.json")
FAKE = os.path.join(HERE, "fake_cli_t01.py")
TARGET = "src/agent_session_gemini.py"
MODEL = "constructed-model"


def package():
    with open(PACKAGE, "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def task_text():
    with open(os.path.join(PKG_DIR, "task.txt"), "rb") as fh:
        return fh.read().decode("utf-8")


def make_export(base):
    out = os.path.join(base, "target")
    manifest = os.path.join(base, "export-manifest.json")
    export_source.export(ROOT, out, manifest, package())
    return out, manifest


def lines_of(export, rel):
    with open(os.path.join(export, *rel.split("/")), "rb") as fh:
        return fh.read().decode("utf-8").split("\n")


def grep_output(export, pattern):
    """CLI 0.65.0 grep_search output shape (observed), matches only, all exported files."""
    rx = re.compile(pattern)
    blocks, count = [], 0
    for dirpath, dirs, files in os.walk(export):
        dirs.sort()
        for name in sorted(files):
            rel = os.path.relpath(os.path.join(dirpath, name), export).replace(os.sep, "/")
            hits = [(i + 1, l) for i, l in enumerate(lines_of(export, rel)) if rx.search(l)]
            if hits:
                count += len(hits)
                blocks.append(f"File: {rel}\n" + "\n".join(f"L{n}: {l}" for n, l in hits))
    word = "match" if count == 1 else "matches"
    return f'Found {count} {word} for pattern "{pattern}" in path ".":\n---\n' + "\n---\n".join(blocks) + "\n---"


def read_output(export, rel, a, b):
    src = lines_of(export, rel)
    return ("\nIMPORTANT: The file content has been truncated.\n"
            f"Status: Showing lines {a}-{b} of {len(src)} total lines.\n"
            "Action: To read more of the file, you can use the 'start_line' and 'end_line' parameters "
            f"in a subsequent 'read_file' call. For example, to read the next section of the file, "
            f"use start_line: {b + 1}.\n\n--- FILE CONTENT (truncated) ---\n" + "\n".join(src[a - 1:b])), \
        f"Read lines {a}-{b} of {len(src)} from {rel}"


def good_tools(export):
    g = grep_output(export, "_registry_problem")
    r, summary = read_output(export, TARGET, 1060, 1145)
    r2, summary2 = read_output(export, TARGET, 1261, 1333)
    return [
        {"id": "grep_search__1", "name": "grep_search", "params": {"pattern": "_registry_problem"},
         "chat_args": {"pattern": "_registry_problem", "context": 0}, "status": "success",
         "chat_output": g},
        {"id": "read_file__1", "name": "read_file",
         "params": {"file_path": "{WORKSPACE}/" + TARGET, "start_line": 1060, "end_line": 1145},
         "status": "success", "chat_output": r, "stream_output": summary},
        {"id": "read_file__2", "name": "read_file",
         "params": {"file_path": "{WORKSPACE}/" + TARGET, "start_line": 1261, "end_line": 1333},
         "status": "success", "chat_output": r2, "stream_output": summary2},
    ]


GOOD_DELIVERY = (
    "1. 粘滞拒绝（A）：GeminiCliAgentSessionAdapter._reload_locked 在登记前缀被改、文本不合法、"
    "不是合法历史或 runs/ 下有未登记目录时设置粘滞问题，见 src/agent_session_gemini.py:1069-1093，"
    "unaccounted_run_traces 见 src/agent_session_gemini.py:879-911。\n"
    "2. 重开后未收集的运行标为未知（C），不重发：src/agent_session_gemini.py:1134-1141。\n"
    "3. 幂等重放（B）：_replay_locked 同键同指纹返回已有状态，src/agent_session_gemini.py:1218-1233；"
    "submit 两次判定 src/agent_session_gemini.py:1237-1256。\n"
    "4. 启动前先写 reserved：src/agent_session_gemini.py:1292-1297，写不成则 RegistryUnavailable，零派发。\n"
    "5. dispatched 写失败：src/agent_session_gemini.py:1324-1330。当前运行仍为 running，进程不被终止；"
    "本对象之后的 submit 被拒；其他读者只见 reserved，按未知处理，不重发。\n")

# Same references, explanation reversed: mechanically identical locations.
REVERSED_DELIVERY = (
    "1. 登记出问题时会自动修复登记并重新派发：src/agent_session_gemini.py:1069-1093，"
    "src/agent_session_gemini.py:879-911。\n"
    "2. 重开后未收集的运行会被自动重试：src/agent_session_gemini.py:1134-1141。\n"
    "3. 同键请求总是重新派发一次：src/agent_session_gemini.py:1218-1233，src/agent_session_gemini.py:1237-1256。\n"
    "4. reserved 在进程结束后才写：src/agent_session_gemini.py:1292-1297。\n"
    "5. dispatched 写失败时会杀掉进程并补写登记后再派发：src/agent_session_gemini.py:1324-1330。\n")

# B7 location cited, but its meaning omitted (no three-layer distinction).
B7_OMITTED_DELIVERY = GOOD_DELIVERY.split("5. ")[0] + "5. 另见 src/agent_session_gemini.py:1324-1330（未展开）。\n"


def write_scenario(base, scenario):
    path = os.path.join(base, "scenario.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(scenario, fh, ensure_ascii=False)
    return path


def run_adapter(base, export, scenario, instructions=None, task_id=None, model=MODEL, key="t01-k1"):
    """Dispatch ONE constructed run through the real adapter; returns (evidence_root, run_id)."""
    pkg = package()
    sc_path = write_scenario(base, scenario)
    home = os.path.join(base, "cli-home")
    os.makedirs(home, exist_ok=True)
    evidence = os.path.join(base, "evidence")
    config = gem.GeminiCliConfig(argv_prefix=(sys.executable, FAKE), cli_home=home, evidence_root=evidence,
                                 extra_env=(("FAKE_T01_SCENARIO", sc_path),))
    adapter = gem.GeminiCliAgentSessionAdapter(config)
    address = SessionAddress(app_kind="gemini-cli", instance_id="gemini-cli/local", session_id="new",
                             model_id=model)
    request = SubmitRequest(task_id=task_id or pkg["task_id"], attempt=1, session=address,
                            instructions=task_text() if instructions is None else instructions,
                            workspace=export, idempotency_key=key)
    status = adapter.submit(request)
    adapter.collect(address, status.run_id, timeout=60)
    return evidence, status.run_id
