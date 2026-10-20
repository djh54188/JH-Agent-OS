"""Hark-派单-06 synthetic GUI preview of the session probe (Linux, offline).

Runs the REAL src.gui_http server and the REAL gui/ assets with the probe
ENABLED, but the Codex app-server process is replaced by the offline fake
transport from tests/test_hark06_session_probe.py. NO real model is called and
NO real Codex path is exercised: the page itself shows "离线/合成注入（非真实模型
结果）". Headless Chromium is driven with real mouse input.

Also applies the declared TEST-ONLY POSIX evidence seam (see the test module),
because the accepted evidence policy refuses every "/..." path on Linux.

Scenarios (each with a fresh probe directory):
  open_only     - open the tab, refresh; record every request (expect GET only)
  unconfirmed   - click the disabled start button by coordinates (expect no POST)
  confirmed     - tick the box, double-click start; expect 1 prepare, 1 start,
                  1 thread/start, outcome round_trip_complete
  disabled      - server switch off; blocked notice, no POST
  unknown       - thread/start timeout; outcome unknown_manual, then blocked

    python synthetic_preview.py --out <new dir>
"""
import argparse
import json
import platform
import sys
import tempfile
import threading
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from playwright.sync_api import sync_playwright  # noqa: E402

import test_hark06_session_probe as probe_tests  # noqa: E402
from src.gui_http import make_server  # noqa: E402
from src.session_probe import ProbeConfig  # noqa: E402

LABEL = "Linux合成预览（离线假传输，未调用真实模型），未作Windows验收"


def serve(tmp, enabled=True, transport_kw=None):
    rig = probe_tests.Rig(tmp, enabled=enabled, transport_kw=transport_kw)
    server = make_server("127.0.0.1", 0, db_path=str(rig.tmp / "no.sqlite3"),
                         drafts_path=str(rig.tmp / "drafts.json"), runs_path=str(rig.history),
                         session_probe=ProbeConfig(enabled=enabled, probe_dir=str(rig.probe_dir)),
                         probe_adapter_factory=rig.factory, probe_runtime_check=lambda: None,
                         probe_runner=rig.runner)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return rig, server


def annotate(page, on):
    page.evaluate("""([on, text]) => {
      let n = document.getElementById('hark06-label');
      if (!on) { if (n) n.remove(); return; }
      n = document.createElement('div'); n.id = 'hark06-label'; n.textContent = text;
      n.style.cssText = 'position:fixed;right:8px;bottom:8px;z-index:99999;pointer-events:none;'
        + 'background:#a61b1b;color:#fff;font:600 13px sans-serif;padding:4px 10px;border-radius:4px;opacity:.92';
      document.body.appendChild(n);
    }""", [on, LABEL])


def facts(page):
    return page.evaluate("""() => {
      const q = (s) => document.querySelector(s);
      const start = q('.panel-probe button.probe-start');
      const box = q('.panel-probe input.probe-confirm-box');
      const outcome = q('.probe-result [data-outcome]');
      const de = document.documentElement;
      return { start_disabled: start ? start.disabled : null, box_disabled: box ? box.disabled : null,
               outcome: outcome ? outcome.dataset.outcome : null,
               source: (q('.probe-source') || {}).dataset ? q('.probe-source').dataset.source : null,
               replies: [...document.querySelectorAll('.probe-reply')].map(n => n.textContent),
               overflowX: de.scrollWidth - de.clientWidth,
               text_has_disclaimer: document.body.innerText.includes('不等于任务验收成功') };
    }""")


def open_tab(page, port):
    page.goto(f"http://127.0.0.1:{port}/")
    page.click('#tabs button[data-view="probe"]')
    page.wait_for_selector(".panel-probe button.probe-start")
    page.wait_for_timeout(300)


def scenario(browser, out, name, width, enabled=True, transport_kw=None, act=None):
    with tempfile.TemporaryDirectory() as tmp:
        rig, server = serve(tmp, enabled=enabled, transport_kw=transport_kw)
        port = server.server_address[1]
        page = browser.new_page(viewport={"width": width, "height": 900})
        requests = []
        page.on("request", lambda r: requests.append(r.method + " " + r.url.split(str(port), 1)[1])
                if "/api/" in r.url else None)
        try:
            open_tab(page, port)
            before = facts(page)
            extra = act(page) if act else {}
            page.wait_for_timeout(600)
            after = facts(page)
            annotate(page, True)
            page.screenshot(path=str(out / f"{name}-{width}.png"), full_page=True)
            annotate(page, False)
        finally:
            page.close()
            server.shutdown()
            server.server_close()
        posts = [r for r in requests if r.startswith("POST")]
        return {"scenario": name, "width": width, "requests": requests, "posts": posts,
                "thread_starts": rig.thread_starts(), "before": before, "after": after,
                "registry_exists": rig.registry().exists(), **(extra or {})}


def act_refresh(page):
    page.click(".panel-probe button.probe-refresh")
    page.wait_for_timeout(400)
    return {}


def act_click_disabled(page):
    box = page.locator(".panel-probe button.probe-start").bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.wait_for_timeout(400)
    return {}


def act_confirm_and_double_click(page):
    page.locator(".panel-probe input.probe-confirm-box").click()
    box = page.locator(".panel-probe button.probe-start").bounding_box()
    page.mouse.dblclick(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.wait_for_selector(".probe-result [data-outcome]", timeout=15000)
    page.wait_for_timeout(800)
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    results = {"notice": LABEL, "synthetic": True, "real_model_calls": 0,
               "platform": platform.platform(), "python": sys.version.split()[0], "runs": []}
    with mock.patch.object(probe_tests.codex_mod, "is_special_path", probe_tests._posix_evidence_seam):
        with sync_playwright() as p:
            browser = p.chromium.launch()
            results["browser"] = "chromium " + browser.version
            for width in (1440, 760):
                results["runs"].append(scenario(browser, out, "open_only", width, act=act_refresh))
                results["runs"].append(scenario(browser, out, "unconfirmed", width, act=act_click_disabled))
                results["runs"].append(scenario(browser, out, "confirmed", width, act=act_confirm_and_double_click))
            results["runs"].append(scenario(browser, out, "disabled", 1440, enabled=False, act=act_click_disabled))
            results["runs"].append(scenario(browser, out, "unknown", 1440,
                                            transport_kw={"timeout_method": "thread/start"},
                                            act=act_confirm_and_double_click))
            browser.close()
    (out / "preview-results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    for run in results["runs"]:
        print(json.dumps({k: run[k] for k in ("scenario", "width", "posts", "thread_starts")}
                         | {"outcome": run["after"]["outcome"], "start_disabled": run["after"]["start_disabled"],
                            "overflowX": run["after"]["overflowX"], "source": run["after"]["source"]},
                         ensure_ascii=False))
    print(results["browser"])


if __name__ == "__main__":
    main()
