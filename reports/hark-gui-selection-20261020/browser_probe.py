"""Hark-派单-05 real-pointer browser check on the synthetic preview (Linux only).

Starts preview_server.py for a GUI directory, drives headless Chromium with
real mouse clicks at 1440 / 760 / 480 px and records, per width:
selection state (aria-pressed / data-selected), scroll position before and
after each click, the detail title's viewport rectangle, horizontal overflow,
and annotated screenshots. Labelled "Linux合成预览，未作Windows验收"; this is
not a Windows result and the records are synthetic.

    python browser_probe.py --gui <gui dir> --out <new dir> [--label candidate]
"""
import argparse
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
WIDTHS = [1440, 1101, 1100, 760, 480]   # 1440/760/480 required; 1101/1100 breakpoint pair added
HEIGHT = 900
LABEL = "Linux合成预览，未作Windows验收"


def state(page):
    return page.evaluate("""() => {
      const de = document.documentElement;
      const buttons = [...document.querySelectorAll('.panel-history button.run-action')];
      const title = document.querySelector('.panel-history .run-detail .detail-title');
      const r = title ? title.getBoundingClientRect() : null;
      return {
        scrollY: window.scrollY, innerHeight: window.innerHeight, innerWidth: window.innerWidth,
        overflowX: de.scrollWidth - de.clientWidth,
        pressed: buttons.map(b => [b.dataset.runId, b.getAttribute('aria-pressed'),
                                   b.closest('.run-card').dataset.selected]),
        layoutColumns: getComputedStyle(document.querySelector('.panel-history .run-layout')).gridTemplateColumns.split(' ').length,
        title: title ? { text: title.textContent, top: r.top, bottom: r.bottom,
                         visible: r.top >= 0 && r.bottom <= window.innerHeight } : null,
        selectedCardStyle: (() => { const c = document.querySelector('.panel-history .run-card[data-selected="true"]');
          if (!c) return null; const s = getComputedStyle(c);
          return { borderColor: s.borderColor, boxShadow: s.boxShadow,
                   marker: getComputedStyle(c.querySelector('.run-foot'), '::before').content }; })(),
        unselectedCardStyle: (() => { const c = document.querySelector('.panel-history .run-card[data-selected="false"]');
          if (!c) return null; const s = getComputedStyle(c);
          return { borderColor: s.borderColor, boxShadow: s.boxShadow,
                   marker: getComputedStyle(c.querySelector('.run-foot'), '::before').content }; })(),
      };
    }""")


def click_run(page, run_id):
    button = page.locator('.panel-history button.run-action[data-run-id="%s"]' % run_id)
    button.scroll_into_view_if_needed()            # make the button visible FIRST
    page.wait_for_timeout(150)
    before = state(page)
    box = button.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.up()
    page.wait_for_selector('.panel-history .run-detail .detail-title:has-text("%s")' % run_id)
    page.wait_for_timeout(400)
    after = state(page)
    return {"run_id": run_id, "before": before, "after": after}


def label(page, on):
    page.evaluate("""([on, text]) => {
      let n = document.getElementById('hark05-label');
      if (!on) { if (n) n.remove(); return; }
      n = document.createElement('div'); n.id = 'hark05-label'; n.textContent = text;
      n.style.cssText = 'position:fixed;right:8px;bottom:8px;z-index:99999;pointer-events:none;'
        + 'background:#a61b1b;color:#fff;font:600 13px sans-serif;padding:4px 10px;border-radius:4px;opacity:.92';
      document.body.appendChild(n);
    }""", [on, LABEL])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gui", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--label", default="candidate")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    server = subprocess.Popen([sys.executable, str(HERE / "preview_server.py"), "--gui", args.gui],
                              stdout=subprocess.PIPE, text=True)
    port = int(server.stdout.readline())
    results = {"label": args.label, "notice": LABEL, "synthetic": True, "model_calls": 0,
               "platform": platform.platform(), "python": sys.version.split()[0], "widths": {}}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            results["browser"] = "chromium " + browser.version
            for width in WIDTHS:
                page = browser.new_page(viewport={"width": width, "height": HEIGHT})
                requests = []
                page.on("request", lambda r: requests.append(r.method + " " + r.url.split(str(port), 1)[1]))
                page.goto("http://127.0.0.1:%d/" % port)
                page.click('#tabs button[data-view="sessions"]')
                page.wait_for_selector(".panel-history button.run-action")
                page.wait_for_timeout(300)
                initial = state(page)
                n_requests = len(requests)
                clicks = [click_run(page, "SYNTHETIC-RUN-A"), click_run(page, "SYNTHETIC-RUN-B")]
                click_requests = requests[n_requests:]
                label(page, True)
                page.screenshot(path=str(out / ("%s-%d-viewport.png" % (args.label, width))))
                page.screenshot(path=str(out / ("%s-%d-full.png" % (args.label, width))), full_page=True)
                page.locator('.panel-history button.run-action[data-run-id="SYNTHETIC-RUN-B"]').evaluate(
                    "b => b.closest('.run-card').scrollIntoView({block: 'center'})")
                page.wait_for_timeout(150)
                page.screenshot(path=str(out / ("%s-%d-selection.png" % (args.label, width))))
                label(page, False)
                results["widths"][str(width)] = {"initial": initial, "clicks": clicks,
                                                 "click_requests": click_requests}
                page.close()
            browser.close()
    finally:
        server.terminate()
    (out / "browser-results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"browser": results.get("browser"), "out": str(out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
