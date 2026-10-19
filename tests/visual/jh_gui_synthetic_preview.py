"""Synthetic visual preview of the GUI (Hark-派单-01). NOT a test, NOT production.

Starts the REAL `src.gui_http.make_server` on loopback against a throw-away temp
directory that holds a SYNTHETIC run registry and synthetic raw-evidence files,
then drives a headless Chromium (Playwright) to capture screenshots of all four
views at a desktop and a narrow width, and to click one run's detail button.

Every screenshot carries an injected overlay banner "合成预览，非真实运行" that
exists only inside this preview page; nothing here is wired into any production
path, no model is called, no dispatch/collect happens, and the registry file is
deleted with the temp directory when the script exits.

    python tests/visual/jh_gui_synthetic_preview.py --out DIR [--gui-dir DIR] [--label after]

`--gui-dir` points the server at another copy of gui/ (used for the BEFORE shots,
taken from the baseline commit).  Requires: playwright + chromium.
"""
import argparse
import hashlib
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.gui_http import make_server  # noqa: E402

BANNER = "合成预览，非真实运行 · SYNTHETIC PREVIEW — NOT A REAL RUN"

THREAD_A = "01a1aaaa-1111-7000-8000-000000000001"
TURN_A = "01a1bbbb-2222-7000-8000-000000000002"
THREAD_LONG = "01a1ffff-9999-7000-8000-00000000000f"
LONG_RUN_ID = ("codex-run-SYNTH-very-long-identifier-"
               "0123456789abcdef0123456789abcdef0123456789abcdef-retry-02")
LONG_TURNS = [f"01a1eeee-{i:04d}-7000-8000-0000000000{i:02d}" for i in range(1, 5)]


def _snapshot(task_id, role="worker", model="gpt-6.1-sol"):
    return json.dumps({"schema": "jh018/submit/v1", "app_kind": "codex",
                       "instance_id": "codex-app-server/local", "session_id": "new",
                       "model_id": model, "role": role, "task_id": task_id, "attempt": 1,
                       "workspace": "SYNTH:/ws", "allowed_scope": [], "budget_tokens": None,
                       "instructions": "synthetic preview", "artifacts": []},
                      ensure_ascii=False)


def _base(run_id, task_id, key, fp_char, at):
    return {"run_id": run_id, "app_kind": "codex", "instance_id": "codex-app-server/local",
            "session_id": "new", "task_id": task_id, "attempt": 1,
            "request_fingerprint": fp_char * 64, "request_snapshot": _snapshot(task_id),
            "idempotency_key": key, "native_session_id": None, "native_turns": [],
            "native_model": None, "state": "queued", "native_state": "reserved", "at": at}


def _raw(tmp: Path, name: str, frames: int, tamper: bool = False):
    raw_dir = tmp / "raw"
    raw_dir.mkdir(exist_ok=True)
    payload = "".join(json.dumps({"synthetic": True, "frame": i}) + "\n"
                      for i in range(frames)).encode("utf-8")
    target = raw_dir / name
    target.write_bytes(payload)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if tamper:
        target.write_bytes(payload + b'{"tampered": true}\n')
    return {"path": f"raw/{name}", "sha256": digest, "frames": frames,
            "bytes": len(payload)}


def build_registry(tmp: Path) -> Path:
    records = []
    # 1. completed + verified evidence
    b = _base("codex-run-SYNTH-0001", "task_synth_a", "key-synth-a", "a",
              "2026-10-19T02:00:00Z")
    records += [dict(b, event="reserved"),
                dict(b, event="dispatched", native_session_id=THREAD_A, native_turns=[TURN_A],
                     native_model="gpt-6.1-sol", state="running", native_state="turn-started"),
                dict(b, event="collected", native_session_id=THREAD_A, native_turns=[TURN_A],
                     native_model="gpt-6.1-sol", state="declared_complete",
                     native_state="completed", declared_status="completed", artifacts=[],
                     raw_refs=[f"{THREAD_A}/{TURN_A}"],
                     unverified=["adapter observation only; review decides success"],
                     usage={"turn": {"totalTokens": 1840}, "thread_total": {"totalTokens": 5210},
                            "model_context_window": 475000,
                            "source": {"thread_id": THREAD_A, "turn_id": TURN_A}},
                     raw=_raw(tmp, "synth-0001.jsonl", 3))]
    # 2. completed + mismatching evidence, very long identifiers
    b = _base(LONG_RUN_ID, "task_synth_long_identifier_example", "key-synth-long-0123456789",
              "b", "2026-10-19T03:10:00Z")
    records += [dict(b, event="reserved"),
                dict(b, event="dispatched", native_session_id=THREAD_LONG,
                     native_turns=LONG_TURNS, native_model="gpt-6.1-sol", state="running",
                     native_state="turn-started"),
                dict(b, event="collected", native_session_id=THREAD_LONG,
                     native_turns=LONG_TURNS, native_model="gpt-6.1-sol",
                     state="declared_complete", native_state="completed",
                     declared_status="completed", artifacts=[], raw_refs=[],
                     unverified=["adapter observation only; review decides success",
                                 "frame count not re-checked by a reviewer"],
                     usage={"turn": {"totalTokens": 92311},
                            "thread_total": {"totalTokens": 410022}},
                     raw=_raw(tmp, "synth-long.jsonl", 2, tamper=True))]
    # 3. dispatch uncertain, no collected record -> evidence missing
    b = _base("codex-run-SYNTH-0003", "task_synth_c", None, "c", "2026-10-19T04:20:00Z")
    records += [dict(b, event="reserved"),
                dict(b, event="dispatch_uncertain", state="dispatch_uncertain",
                     native_state="unknown", stage="turn/start",
                     reason="turn/start 超时，无法确认是否已派发（合成）")]
    path = tmp / "runs.jsonl"
    path.write_bytes("".join(json.dumps(r, ensure_ascii=False) + "\n"
                             for r in records).encode("utf-8"))
    return path


OVERLAY_JS = """
(() => {
  // Two static (non-fixed) bands, top and bottom of the page, so every full-page
  // screenshot is marked. Injected by this preview only; never part of gui/.
  const band = (id) => {
    const b = document.createElement('div');
    b.id = id;
    b.textContent = %s;
    b.style.cssText = 'background:repeating-linear-gradient(45deg,#7c2d12,#7c2d12 12px,#9a3412 12px,#9a3412 24px);'
      + 'color:#fff;font:600 13px/1.6 sans-serif;text-align:center;padding:6px;letter-spacing:.5px';
    return b;
  };
  const add = () => {
    if (document.getElementById('__synthetic_banner_top')) return;
    document.body.insertBefore(band('__synthetic_banner_top'), document.body.firstChild);
    document.body.appendChild(band('__synthetic_banner_bottom'));
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add);
  else add();
})();
""" % json.dumps(BANNER, ensure_ascii=False)


def capture(base_url: str, out: Path, label: str, log: list):
    from playwright.sync_api import sync_playwright
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width, height, tag in ((1440, 1000, "desktop"), (760, 1100, "narrow")):
            page = browser.new_page(viewport={"width": width, "height": height})
            page.add_init_script(OVERLAY_JS)
            page.goto(base_url + "/")
            page.wait_for_load_state("networkidle")
            for view in ("workbench", "task", "sessions", "delivery"):
                page.click(f'button.tab[data-view="{view}"]')
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(250)
                name = f"{label}-{tag}-{view}.png"
                page.screenshot(path=str(out / name), full_page=True)
                log.append({"width": width, "action": f"click tab {view}", "shot": name})
            # sessions detail: pick the long-id run, via a real pointer click
            page.click('button.tab[data-view="sessions"]')
            page.wait_for_load_state("networkidle")
            btn = page.locator(f'button.run-action[data-run-id="{LONG_RUN_ID}"]')
            btn.scroll_into_view_if_needed()
            btn.click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(300)
            name = f"{label}-{tag}-sessions-detail.png"
            page.screenshot(path=str(out / name), full_page=True)
            # The detail pane may scroll inside a sticky box; a tall viewport shows
            # it whole for the record. Element shot only, same DOM, no style edits.
            page.set_viewport_size({"width": width, "height": 2600})
            page.wait_for_timeout(200)
            pane = f"{label}-{tag}-detail-pane.png"
            page.locator("div.run-detail").screenshot(path=str(out / pane))
            log.append({"width": width, "action": "element screenshot div.run-detail "
                        "(viewport height 2600)", "shot": pane})
            page.set_viewport_size({"width": width, "height": height})
            overflow = page.evaluate(
                "() => document.documentElement.scrollWidth > window.innerWidth")
            log.append({"width": width, "action": "pointer click run-action " + LONG_RUN_ID,
                        "shot": name, "horizontal_overflow": overflow})
            page.close()
        browser.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--gui-dir", default=None)
    ap.add_argument("--label", default="after")
    args = ap.parse_args(argv)
    out = Path(args.out)
    log = []
    with tempfile.TemporaryDirectory(prefix="jh-gui-synth-") as tmpname:
        tmp = Path(tmpname)
        runs = build_registry(tmp)
        httpd = make_server("127.0.0.1", 0, db_path=str(tmp / "absent.sqlite3"),
                            drafts_path=str(tmp / "drafts.json"), runs_path=str(runs),
                            gui_dir=Path(args.gui_dir) if args.gui_dir else None)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            capture(f"http://127.0.0.1:{httpd.server_address[1]}", out, args.label, log)
        finally:
            httpd.shutdown()
            httpd.server_close()
    record = {"label": args.label, "synthetic": True, "banner": BANNER,
              "gui_dir": args.gui_dir or "gui/", "model_calls": 0,
              "captured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "actions": log}
    (out / f"{args.label}-actions.json").write_bytes(
        json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8"))
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
