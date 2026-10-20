/* Hark-派单-05 run-history selection & single-column placement harness.
 *
 * Offline, no browser, no network, no model. Evaluates the REAL
 * gui/components.js and gui/views.js against a DOM stub that additionally
 * records setAttribute/getAttribute, scrollIntoView calls and a controllable
 * window.matchMedia width, and asserts:
 *
 *   - no selection before any click; A->B leaves only B pressed/selected;
 *   - repeated clicks on the same run keep it the only selection;
 *   - late success / late error / wrong run_id / replies after leaving the
 *     panel never change the selection and never scroll;
 *   - loading / error / empty detail stay tied to the current selection and
 *     never show an older run's content;
 *   - at <=1100px (single column) an explicit click scrolls the detail pane to
 *     the top of the viewport exactly once; at >=1101px it never scrolls;
 *   - a click only issues the existing detail query (no other API call);
 *   - the JS breakpoint equals the CSS single-column media query and the new
 *     CSS selectors are scoped to the run-history panel.
 *
 * A failed check throws AssertFail (kind "assertion"); any other throw is
 * reported as kind "exception" and must never be counted as a detection.
 *
 *   node tests/js/hark05_run_selection_harness.mjs [--json]
 */
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..");
const GUI = process.env.JH_GUI_DIR || path.join(REPO, "gui");

class AssertFail extends Error {}
function check(cond, message) { if (!cond) throw new AssertFail(message); }

function makeNode(tag, world) {
  const node = {
    tagName: tag, children: [], dataset: {}, listeners: [], attributes: {},
    _text: "", className: "", placeholder: "", rows: 0, value: "",
    classList: { toggle() {}, add() {}, remove() {} },
    appendChild(child) { node.children.push(child); return child; },
    addEventListener(type, fn) { node.listeners.push({ type, fn }); },
    setAttribute(name, value) { node.attributes[name] = String(value); },
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(node.attributes, name) ? node.attributes[name] : null; },
    scrollIntoView(options) { world.scrolls.push({ node, options }); },
    querySelectorAll() { return []; },
    querySelector() { return null; },
    closest() { return null; },
    get textContent() { return node._text + node.children.map((c) => c.textContent).join(""); },
    set textContent(value) { node._text = String(value); node.children = []; },
  };
  return node;
}

function walk(node, visit) { visit(node); node.children.forEach((c) => walk(c, visit)); }
function findAll(start, predicate) { const out = []; walk(start, (n) => { if (predicate(n)) out.push(n); }); return out; }

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

function newWorld(width) {
  const world = { scrolls: [], width, mediaQueries: [] };
  const byId = {};
  const document = {
    listeners: [],
    createElement(tag) { return makeNode(tag, world); },
    getElementById(id) { return byId[id] || (byId[id] = makeNode("div", world)); },
    addEventListener(type, fn) { document.listeners.push({ type, fn }); },
  };
  const sandbox = { document, console, setTimeout, clearTimeout, JSON, Promise, Math,
                    Object, Array, String, Number, Boolean, RegExp, Error };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.matchMedia = function (query) {
    world.mediaQueries.push(query);
    const m = /^\(max-width:\s*(\d+)px\)$/.exec(query);
    if (!m) throw new Error("harness only models (max-width: Npx), got " + query);
    return { matches: world.width <= Number(m[1]), media: query };
  };
  const context = vm.createContext(sandbox);
  for (const file of ["components.js", "views.js"]) {
    vm.runInContext(fs.readFileSync(path.join(GUI, file), "utf8"), context, { filename: file });
  }
  const pending = { capabilities: [], listTasks: [], listSessions: [], listDrafts: [],
                    taskDetail: [], reviewDelivery: [], saveGoalDraft: [],
                    listAgentRuns: [], agentRunDetail: [] };
  const calls = {};
  const api = {};
  for (const method of Object.keys(pending)) {
    calls[method] = 0;
    api[method] = (...args) => {
      calls[method] += 1;
      const d = deferred();
      d.args = args;
      pending[method].push(d);
      return d.promise;
    };
  }
  sandbox.JH.api = api;
  return Object.assign(world, { JH: sandbox.JH, document, pending, calls,
    root: document.getElementById("view-root"),
    tick: () => new Promise((r) => setImmediate(r)) });
}

function clickTab(w, view) {
  w.document.getElementById("tabs").listeners.find((l) => l.type === "click")
    .fn({ target: { dataset: { view } } });
}

const sessionsPayload = { ok: true, read_only: true, transport: "static-registry", count: 1,
  sessions: [{ session_id: "sim-1", app_kind: "zcode", instance_id: "i", role: "worker",
               model_id: null, connected: false, simulated: true, evidence: { a: "b" }, note: "未接入" }],
  note: "全部为静态登记：connected=false、simulated=true" };

function runPayload(over = {}) {
  return Object.assign({
    run_id: "RUN-A", events: ["reserved", "dispatched", "collected"],
    app_kind: "codex", instance_id: "codex-app-server/local", session_id: "new",
    role: "worker", requested_model: "gpt-6.1-sol", native_model: "MODEL-OF-A",
    logical_task_id: "task_x", attempt: 1, request_fingerprint: "a".repeat(64),
    idempotency_key: "key-1", native_session_id: "thread-1", native_turns: ["turn-1"],
    record_state: "declared_complete", native_state: "completed",
    first_recorded_at: "t0", last_recorded_at: "t1",
    usage: { turn: { totalTokens: 1 }, thread_total: { totalTokens: 2 } },
    evidence: { state: "verified", detail: "d" }, unverified: ["u"],
    declared_status: "completed", dispatch_uncertain_reason: null, snapshot: {},
  }, over);
}

const RUN_IDS = ["RUN-A", "RUN-B", "RUN-C"];
const runsEnvelope = () => ({
  ok: true, read_only: true, live: false, state: "ok", count: RUN_IDS.length, message: "",
  source: { configured: true, path: "server-configured", browser_supplied: false },
  note: "历史记录，不代表实时在线；declared_complete 是执行方声明，不代表任务验收成功。",
  runs: RUN_IDS.map((id) => runPayload({ run_id: id, native_model: "MODEL-OF-" + id.slice(-1) })),
});
const detailOk = (id, over) => ({ ok: true, read_only: true, state: "ok", records: [],
  run: Object.assign(runPayload({ run_id: id, native_model: "DETAIL-OF-" + id,
    link: { checked: false, linked: false, note: "fixture" } }), over || {}) });

async function openSessions(w) {
  w.JH.views.start();
  w.pending.capabilities.shift().resolve({ ok: true, unsupported: ["dispatch"], note: "n" });
  await w.tick();
  clickTab(w, "sessions");
  await w.tick();
  w.pending.listSessions.shift().resolve(sessionsPayload);
  await w.tick();
  for (const d of w.pending.listAgentRuns.splice(0)) d.resolve(runsEnvelope());
  await w.tick();
}

const buttons = (w) => findAll(w.root, (n) => n.className === "run-action");
const cards = (w) => findAll(w.root, (n) => n.className === "card run-card");
const pane = (w) => findAll(w.root, (n) => n.className === "run-detail")[0];
const detailText = (w) => (pane(w) ? pane(w).textContent : "");
const pressed = (b) => b.getAttribute("aria-pressed");

function clickRun(w, id) {
  const list = findAll(w.root, (n) => n.className === "run-list")[0];
  const button = buttons(w).find((b) => b.dataset.runId === id);
  if (!list || !button) throw new Error("run button not found: " + id);
  list.listeners.find((l) => l.type === "click").fn({ target: { closest: () => button } });
}

/* Exactly `id` is selected (or nothing when id is null), on button AND card. */
function expectSelection(w, id, label) {
  const bs = buttons(w);
  check(bs.length === RUN_IDS.length, label + ": expected " + RUN_IDS.length + " run buttons, got " + bs.length);
  for (const b of bs) {
    const want = b.dataset.runId === id ? "true" : "false";
    check(pressed(b) === want, label + ": aria-pressed of " + b.dataset.runId + " is " + pressed(b) + ", expected " + want);
  }
  for (const c of cards(w)) {
    const b = findAll(c, (n) => n.className === "run-action")[0];
    const want = b.dataset.runId === id ? "true" : "false";
    check(c.dataset.selected === want, label + ": card of " + b.dataset.runId + " data-selected=" + c.dataset.selected + ", expected " + want);
  }
}

const NARROW = 760;
const WIDE = 1440;

async function scenarioNoSelection() {
  const w = newWorld(NARROW);
  await openSessions(w);
  expectSelection(w, null, "before any click");
  check(w.scrolls.length === 0, "scrolled without a click");
  check(detailText(w) === "", "detail pane is not empty before a click");
  return { selected: null, scrolls: 0 };
}

async function scenarioAToB() {
  const w = newWorld(WIDE);
  await openSessions(w);
  clickRun(w, "RUN-A");
  expectSelection(w, "RUN-A", "after A");
  clickRun(w, "RUN-B");
  expectSelection(w, "RUN-B", "after A->B");
  w.pending.agentRunDetail.shift().resolve(detailOk("RUN-A"));
  w.pending.agentRunDetail.shift().resolve(detailOk("RUN-B"));
  await w.tick();
  expectSelection(w, "RUN-B", "after both replies");
  check(detailText(w).includes("DETAIL-OF-RUN-B"), "B's detail not shown");
  check(!detailText(w).includes("DETAIL-OF-RUN-A"), "A's detail shown after switching to B");
  return { selected: "RUN-B" };
}

async function scenarioRepeatSame() {
  const w = newWorld(NARROW);
  await openSessions(w);
  clickRun(w, "RUN-A");
  clickRun(w, "RUN-A");
  expectSelection(w, "RUN-A", "after A,A");
  check(w.calls.agentRunDetail === 2, "each explicit click issues one detail query");
  check(w.scrolls.length === 2, "each explicit narrow click positions once, got " + w.scrolls.length);
  for (const d of w.pending.agentRunDetail.splice(0)) d.resolve(detailOk("RUN-A"));
  await w.tick();
  expectSelection(w, "RUN-A", "after replies");
  check(w.scrolls.length === 2, "a reply scrolled");
  return { selected: "RUN-A", scrolls: 2 };
}

async function scenarioOutOfOrderSuccess() {
  const w = newWorld(NARROW);
  await openSessions(w);
  clickRun(w, "RUN-A");
  clickRun(w, "RUN-B");
  const a = w.pending.agentRunDetail.shift();
  const b = w.pending.agentRunDetail.shift();
  const scrollsAfterClicks = w.scrolls.length;
  b.resolve(detailOk("RUN-B"));
  await w.tick();
  a.resolve(detailOk("RUN-A"));
  await w.tick();
  expectSelection(w, "RUN-B", "after late A success");
  check(w.scrolls.length === scrollsAfterClicks, "a reply scrolled");
  check(detailText(w).includes("DETAIL-OF-RUN-B") && !detailText(w).includes("DETAIL-OF-RUN-A"),
        "late A success replaced B");
  return { selected: "RUN-B", extraScrolls: 0 };
}

async function scenarioLateError() {
  const w = newWorld(NARROW);
  await openSessions(w);
  clickRun(w, "RUN-A");
  clickRun(w, "RUN-B");
  const a = w.pending.agentRunDetail.shift();
  const b = w.pending.agentRunDetail.shift();
  const before = w.scrolls.length;
  b.resolve(detailOk("RUN-B"));
  await w.tick();
  a.resolve({ ok: false, error: { code: "late_a", message: "LATE-A-ERROR" } });
  await w.tick();
  expectSelection(w, "RUN-B", "after late A error");
  check(w.scrolls.length === before, "a late error scrolled");
  check(!detailText(w).includes("LATE-A-ERROR"), "late A error painted");
  return { selected: "RUN-B", extraScrolls: 0 };
}

async function scenarioWrongRunId() {
  const w = newWorld(NARROW);
  await openSessions(w);
  clickRun(w, "RUN-B");
  const before = w.scrolls.length;
  w.pending.agentRunDetail.shift().resolve(detailOk("RUN-C"));
  await w.tick();
  expectSelection(w, "RUN-B", "after a reply carrying RUN-C");
  check(w.scrolls.length === before, "a wrong run_id reply scrolled");
  check(detailText(w).includes("unexpected_run_id"), "mismatch not reported");
  check(!detailText(w).includes("DETAIL-OF-RUN-C"), "unrequested run painted");
  return { selected: "RUN-B", extraScrolls: 0 };
}

async function scenarioLeaveReenter() {
  const w = newWorld(NARROW);
  await openSessions(w);
  clickRun(w, "RUN-A");
  const stale = w.pending.agentRunDetail.shift();
  const scrollsAfterClick = w.scrolls.length;
  clickTab(w, "workbench");
  await w.tick();
  clickTab(w, "sessions");
  await w.tick();
  w.pending.listSessions.shift().resolve(sessionsPayload);
  await w.tick();
  for (const d of w.pending.listAgentRuns.splice(0)) d.resolve(runsEnvelope());
  await w.tick();
  expectSelection(w, null, "re-entered panel");
  stale.resolve(detailOk("RUN-A"));
  await w.tick();
  expectSelection(w, null, "after the old panel's reply");
  check(w.scrolls.length === scrollsAfterClick, "the old panel's reply scrolled");
  check(detailText(w) === "", "the old panel's reply painted the new panel");
  clickRun(w, "RUN-C");
  expectSelection(w, "RUN-C", "first click in the new panel");
  check(w.scrolls.length === scrollsAfterClick + 1, "new panel click did not position once");
  return { reenteredSelection: null, extraScrolls: 0 };
}

async function scenarioLoadingErrorEmpty() {
  const w = newWorld(WIDE);
  await openSessions(w);
  clickRun(w, "RUN-A");
  w.pending.agentRunDetail.shift().resolve(detailOk("RUN-A"));
  await w.tick();
  check(detailText(w).includes("DETAIL-OF-RUN-A"), "A not shown");
  // loading for B: no A content, B selected
  clickRun(w, "RUN-B");
  expectSelection(w, "RUN-B", "B loading");
  check(detailText(w).includes("正在读取运行记录"), "loading state not shown for B");
  check(!detailText(w).includes("DETAIL-OF-RUN-A"), "A content visible while B loads");
  // error for B
  w.pending.agentRunDetail.shift().resolve({ ok: false, error: { code: "b_err", message: "B-ERROR" } });
  await w.tick();
  expectSelection(w, "RUN-B", "B error");
  check(detailText(w).includes("B-ERROR") && !detailText(w).includes("DETAIL-OF-RUN-A"), "B error not tied to B");
  // empty for C
  clickRun(w, "RUN-C");
  w.pending.agentRunDetail.shift().resolve({ ok: true, read_only: true, state: "absent", run: null,
                                             message: "C-EMPTY", records: [] });
  await w.tick();
  expectSelection(w, "RUN-C", "C empty");
  const t = detailText(w);
  check(t.includes("C-EMPTY") && !t.includes("B-ERROR") && !t.includes("DETAIL-OF-RUN-A"), "C empty not tied to C");
  check(w.scrolls.length === 0, "two-column layout scrolled");
  return { loading: "RUN-B", error: "RUN-B", empty: "RUN-C" };
}

async function placementAt(width) {
  const w = newWorld(width);
  await openSessions(w);
  clickRun(w, "RUN-B");
  const scrolls = w.scrolls.slice();
  w.pending.agentRunDetail.shift().resolve(detailOk("RUN-B"));
  await w.tick();
  check(w.scrolls.length === scrolls.length, width + "px: the reply scrolled");
  return { w, scrolls };
}

async function scenarioBreakpoints() {
  const out = {};
  for (const width of [1440, 1101]) {
    const { scrolls } = await placementAt(width);
    check(scrolls.length === 0, width + "px two-column click scrolled (" + scrolls.length + ")");
    out[width] = 0;
  }
  for (const width of [1100, 760, 480]) {
    const { w, scrolls } = await placementAt(width);
    check(scrolls.length === 1, width + "px single-column click positioned " + scrolls.length + " times");
    check(scrolls[0].node === pane(w), width + "px: positioned something other than the detail pane");
    check(scrolls[0].options && scrolls[0].options.block === "start", width + "px: detail not aligned to the top");
    out[width] = 1;
  }
  return out;
}

async function scenarioClickOnlyQueriesDetail() {
  const w = newWorld(NARROW);
  await openSessions(w);
  const before = Object.assign({}, w.calls);
  clickRun(w, "RUN-A");
  clickRun(w, "RUN-B");
  await w.tick();
  for (const [method, count] of Object.entries(w.calls)) {
    const want = before[method] + (method === "agentRunDetail" ? 2 : 0);
    check(count === want, method + " called " + (count - before[method]) + " extra times by clicks");
  }
  check(w.pending.agentRunDetail[1].args[0] === "RUN-B", "detail asked for the wrong run");
  return { onlyDetailQueries: true };
}

async function scenarioCssScope() {
  const css = fs.readFileSync(path.join(GUI, "styles.css"), "utf8");
  const views = fs.readFileSync(path.join(GUI, "views.js"), "utf8");
  check(css.includes("@media (max-width: 1100px)"), "single-column media query missing from styles.css");
  check(views.includes('"(max-width: 1100px)"'), "views.js breakpoint differs from styles.css");
  const rules = css.split("\n").filter((l) => l.includes("{") && /run-|data-selected|aria-pressed/.test(l)
    && /data-selected|aria-pressed|focus-visible|scroll-margin/.test(l));
  check(rules.length > 0, "no selection rules found");
  for (const line of rules) check(line.trim().startsWith(".panel-history "), "unscoped selection rule: " + line.trim());
  return { scopedRules: rules.length };
}

const SCENARIOS = [
  ["no_selection", scenarioNoSelection],
  ["a_to_b", scenarioAToB],
  ["repeat_same", scenarioRepeatSame],
  ["out_of_order_success", scenarioOutOfOrderSuccess],
  ["late_error", scenarioLateError],
  ["wrong_run_id", scenarioWrongRunId],
  ["leave_reenter", scenarioLeaveReenter],
  ["loading_error_empty", scenarioLoadingErrorEmpty],
  ["breakpoints_1100_1101", scenarioBreakpoints],
  ["click_only_queries_detail", scenarioClickOnlyQueriesDetail],
  ["css_breakpoint_and_scope", scenarioCssScope],
];

async function main() {
  const results = {};
  let ok = true;
  for (const [name, fn] of SCENARIOS) {
    try {
      results[name] = { ok: true, detail: await fn() };
    } catch (err) {
      ok = false;
      results[name] = { ok: false, kind: err instanceof AssertFail ? "assertion" : "exception",
                        error: String((err && err.message) || err) };
    }
  }
  const report = { harness: "hark05-run-selection", ok, count: SCENARIOS.length, scenarios: results };
  if (process.argv.includes("--json")) {
    process.stdout.write(JSON.stringify(report, null, 2) + "\n");
  } else {
    for (const [name, r] of Object.entries(results)) {
      process.stdout.write((r.ok ? "ok   " : "FAIL ") + name + (r.error ? " :: [" + r.kind + "] " + r.error : "") + "\n");
    }
  }
  process.exit(ok ? 0 : 1);
}

main();
