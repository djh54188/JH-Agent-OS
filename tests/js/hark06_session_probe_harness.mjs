/* Hark-派单-06 GUI session-probe behaviour harness (offline, no browser).
 *
 * Evaluates the REAL gui/components.js + gui/views.js against a DOM stub with a
 * stubbed JH.api and controllable timers, plus the REAL gui/data_access.js
 * against a stubbed fetch, and asserts:
 *
 *   - opening / refreshing the probe view only reads status (zero prepare/start);
 *   - disabled, blocked or unconfirmed states never prepare or start;
 *   - one confirmed click prepares once and starts once, even when clicked twice;
 *   - leaving the view before prepare resolves never sends start; late replies
 *     after leaving never paint;
 *   - the reply is rendered as text; unknown is shown as 待人工处理; a completed
 *     round trip is never shown as task success; unknown usage is not zero;
 *   - in-flight polling is bounded, read-only and stops when the view is left;
 *   - an injected/offline adapter is labelled as not a real model result;
 *   - the data-access start body carries exactly {probe_id, confirm:true}.
 *
 * A failed check throws AssertFail (kind "assertion"); anything else is
 * "exception" and must never be counted as a detection.
 *
 *   node tests/js/hark06_session_probe_harness.mjs [--json]
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

function makeNode(tag) {
  const node = {
    tagName: tag, children: [], dataset: {}, listeners: [], attributes: {},
    _text: "", className: "", type: "", checked: false, disabled: false, title: "",
    classList: { toggle() {}, add() {}, remove() {} },
    appendChild(child) { node.children.push(child); return child; },
    addEventListener(type, fn) { node.listeners.push({ type, fn }); },
    setAttribute(name, value) { node.attributes[name] = String(value); },
    getAttribute(name) { return name in node.attributes ? node.attributes[name] : null; },
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
function deferred() { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; }

function newWorld() {
  const byId = {};
  const timers = [];
  const document = {
    listeners: [],
    createElement(tag) { return makeNode(tag); },
    getElementById(id) { return byId[id] || (byId[id] = makeNode("div")); },
    addEventListener(type, fn) { document.listeners.push({ type, fn }); },
  };
  const sandbox = { document, console, JSON, Promise, Math, Object, Array, String, Number,
                    Boolean, RegExp, Error, clearTimeout() {},
                    setTimeout(fn, ms) { timers.push({ fn, ms }); return timers.length; } };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  for (const file of ["components.js", "views.js"]) {
    vm.runInContext(fs.readFileSync(path.join(GUI, file), "utf8"), context, { filename: file });
  }
  const methods = ["capabilities", "listTasks", "listSessions", "listDrafts", "taskDetail",
                   "reviewDelivery", "saveGoalDraft", "listAgentRuns", "agentRunDetail",
                   "sessionProbeStatus", "prepareSessionProbe", "startSessionProbe"];
  const pending = {};
  const api = {};
  for (const m of methods) {
    pending[m] = [];
    api[m] = (...args) => { const d = deferred(); d.args = args; pending[m].push(d); return d.promise; };
  }
  sandbox.JH.api = api;
  return { JH: sandbox.JH, document, pending, timers, root: document.getElementById("view-root"),
           tick: () => new Promise((r) => setImmediate(r)) };
}

function clickTab(w, view) {
  w.document.getElementById("tabs").listeners.find((l) => l.type === "click")
    .fn({ target: { dataset: { view } } });
}
function fire(node, type) { node.listeners.filter((l) => l.type === type).forEach((l) => l.fn({ target: node })); }
const one = (w, cls) => findAll(w.root, (n) => n.className === cls)[0];
const calls = (w, m) => w.pending[m].length;

function statusPayload(over = {}) {
  return Object.assign({
    ok: true, read_only: true, enabled: true, available: true, adapter_source: "real_codex_adapter",
    blockers: [], inflight: null, last_error: null, latest: null,
    registry: { state: "absent", message: "尚无试运行记录" },
    plan: { app_kind: "codex", instance_id: "codex-app-server/local", session: "new", role: "worker",
            model: "gpt-6.1-sol", sandbox: "read-only", network: "off", approval_policy: "never",
            instruction_template: "Reply with exactly this token", workspace_root: "ws",
            registry: "probe-runs.jsonl", evidence_root: "raw", budget: "适配器不支持硬 token 预算",
            collect_timeout_seconds: 10 },
  }, over);
}

function latest(over = {}) {
  return Object.assign({
    run_id: "codex-run-1-abc", probe_id: "0123456789abcdef", token: "JH-PROBE-0123456789ABCDEF",
    events: ["reserved", "dispatched", "collected"], record_state: "declared_complete",
    native_state: "completed", native_model: "gpt-6.1-sol", requested_model: "gpt-6.1-sol",
    native_session_id: "thread-1", native_turn_id: "turn-1", instruction_bound: true,
    reply_texts: ["JH-PROBE-0123456789ABCDEF"], echo: "matched",
    usage: { turn: { totalTokens: 120 }, thread_total: { totalTokens: 900 },
             source: { thread_id: "thread-1", turn_id: "turn-1" } }, usage_state: "bound",
    evidence: { state: "verified", detail: "digest ok", path: "raw/x.jsonl", sha256: "a".repeat(64) },
    tool_items: [], workspace_files: [], unverified: ["adapter observation only"],
    outcome: "round_trip_complete",
    note: "试运行往返完成只表示固定标识往返；不授予任何任务成功（Task succeeded）",
  }, over);
}

async function openProbe(w, payload) {
  w.JH.views.start();
  w.pending.capabilities.shift().resolve({ ok: true, unsupported: [], note: "n" });
  await w.tick();
  clickTab(w, "probe");
  await w.tick();
  w.pending.sessionProbeStatus.shift().resolve(payload);
  await w.tick();
}

function confirmBox(w) { const box = one(w, "probe-confirm-box"); box.checked = true; fire(box, "change"); }

async function scenarioOpenIsReadOnly() {
  const w = newWorld();
  await openProbe(w, statusPayload());
  fire(one(w, "probe-refresh"), "click");
  await w.tick();
  check(calls(w, "sessionProbeStatus") === 1, "refresh did not issue exactly one status read");
  w.pending.sessionProbeStatus.shift().resolve(statusPayload());
  await w.tick();
  check(calls(w, "prepareSessionProbe") === 0 && calls(w, "startSessionProbe") === 0, "opening/refreshing prepared or started");
  check(one(w, "probe-start").disabled === true, "start enabled before confirmation");
  return { prepare: 0, start: 0 };
}

async function scenarioBlockedNeverPrepares() {
  const w = newWorld();
  await openProbe(w, statusPayload({ enabled: false, available: false,
    blockers: [{ code: "probe_disabled", message: "服务端未启用会话试运行（默认关闭）" }] }));
  const box = one(w, "probe-confirm-box");
  check(box.disabled === true, "confirm box enabled while blocked");
  box.checked = true; fire(box, "change");
  const start = one(w, "probe-start");
  check(start.disabled === true, "start enabled while blocked");
  start.disabled = false;                      // even a forced click must do nothing
  fire(start, "click");
  await w.tick();
  check(calls(w, "prepareSessionProbe") === 0, "blocked view prepared a probe");
  check(w.root.textContent.includes("probe_disabled"), "blocker not shown");
  return { prepare: 0 };
}

async function scenarioUnconfirmedClickDoesNothing() {
  const w = newWorld();
  await openProbe(w, statusPayload());
  const start = one(w, "probe-start");
  start.disabled = false;                      // forced, but the box is unchecked
  fire(start, "click");
  await w.tick();
  check(calls(w, "prepareSessionProbe") === 0, "unconfirmed click prepared a probe");
  return { prepare: 0 };
}

async function scenarioConfirmedClickOnce() {
  const w = newWorld();
  await openProbe(w, statusPayload());
  confirmBox(w);
  const start = one(w, "probe-start");
  check(start.disabled === false, "start stays disabled after confirmation");
  fire(start, "click");
  fire(start, "click");                        // double click
  await w.tick();
  check(calls(w, "prepareSessionProbe") === 1, "double click prepared " + calls(w, "prepareSessionProbe") + " times");
  w.pending.prepareSessionProbe[0].resolve({ ok: true, probe_id: "0123456789abcdef", token: "JH-PROBE-0123456789ABCDEF" });
  await w.tick();
  check(calls(w, "startSessionProbe") === 1, "start sent " + calls(w, "startSessionProbe") + " times");
  check(w.pending.startSessionProbe[0].args.length === 1 && w.pending.startSessionProbe[0].args[0] === "0123456789abcdef",
        "start carried something other than the prepared probe id");
  fire(start, "click");
  await w.tick();
  check(calls(w, "prepareSessionProbe") === 1, "a click after start prepared again");
  w.pending.startSessionProbe[0].resolve({ ok: true, accepted: true });
  await w.tick();
  check(calls(w, "sessionProbeStatus") === 1, "status not re-read after start");
  return { prepare: 1, start: 1 };
}

async function scenarioLeaveBeforeStart() {
  const w = newWorld();
  await openProbe(w, statusPayload());
  confirmBox(w);
  fire(one(w, "probe-start"), "click");
  await w.tick();
  clickTab(w, "workbench");
  await w.tick();
  w.pending.prepareSessionProbe[0].resolve({ ok: true, probe_id: "0123456789abcdef", token: "T" });
  await w.tick();
  check(calls(w, "startSessionProbe") === 0, "start was sent after the view was left");
  return { start: 0 };
}

async function scenarioLateStatusAfterLeave() {
  const w = newWorld();
  w.JH.views.start();
  w.pending.capabilities.shift().resolve({ ok: true, unsupported: [], note: "n" });
  await w.tick();
  clickTab(w, "probe");
  await w.tick();
  const late = w.pending.sessionProbeStatus.shift();
  clickTab(w, "workbench");
  await w.tick();
  late.resolve(statusPayload({ latest: latest({ reply_texts: ["LATE-PROBE-REPLY"] }) }));
  await w.tick();
  check(!w.root.textContent.includes("LATE-PROBE-REPLY"), "a late probe status painted another view");
  return { inert: true };
}

async function scenarioReplyIsText() {
  const w = newWorld();
  const html = "<img src=x onerror=alert(1)>";
  await openProbe(w, statusPayload({ latest: latest({ reply_texts: [html], echo: "mismatch",
    outcome: "round_trip_not_proven" }) }));
  const pre = findAll(w.root, (n) => n.className === "probe-reply");
  check(pre.length === 1 && pre[0]._text === html && pre[0].children.length === 0, "reply not rendered as a text node");
  return { text: true };
}

async function scenarioUnknownIsManual() {
  const w = newWorld();
  await openProbe(w, statusPayload({ available: false,
    blockers: [{ code: "probe_unresolved", message: "存在未解决的试运行" }],
    latest: latest({ events: ["reserved", "dispatch_uncertain"], outcome: "unknown_manual",
                     reply_texts: [], echo: "unknown", usage: null, usage_state: "unknown",
                     record_state: "unknown" }) }));
  const text = w.root.textContent;
  check(text.includes("待人工处理") && text.includes("不会自动重发"), "unknown not shown as manual handling");
  check(one(w, "probe-start").disabled === true, "start enabled with an unresolved probe");
  return { manual: true };
}

async function scenarioCompleteIsNotTaskSuccess() {
  const w = newWorld();
  await openProbe(w, statusPayload({ latest: latest() }));
  const text = w.root.textContent;
  check(text.includes("不等于任务验收成功"), "round trip shown without the task-success disclaimer");
  check(text.includes("JH-PROBE-0123456789ABCDEF"), "reply token missing");
  check(text.includes("证据核验：verified"), "evidence state missing");
  return { disclaimer: true };
}

// Hark-派单-09: workspace observation is shown with its source and never as "empty" unverified.
async function scenarioWorkspaceUnverifiedNotEmpty() {
  const w = newWorld();
  await openProbe(w, statusPayload({ latest: latest({ workspace_files: null,
    workspace: { state: "native_mismatch", verified: false, reason: "原生目录与计划工作区不一致",
                 planned: "/p/ws/0123456789abcdef", files: null, verified_empty: null } }) }));
  const text = w.root.textContent;
  check(text.includes("未核实（native_mismatch）"), "unverified workspace state not shown");
  check(text.includes("未核实：不对实际工作区内容作断言"), "unverified workspace not marked as no assertion");
  check(!text.includes("无（已核实为空）"), "unverified workspace shown as verified empty");
  check(text.includes("不等于任务验收成功"), "echo outcome disclaimer lost");
  const legacy = newWorld();
  await openProbe(legacy, statusPayload({ latest: latest({ workspace_files: [] }) }));
  check(legacy.root.textContent.includes("未核实（无工作区观测）"), "payload without observation shown as verified");
  check(!legacy.root.textContent.includes("无（已核实为空）"), "payload without observation shown as empty");
  return { unverified: true };
}

async function scenarioWorkspaceVerifiedListing() {
  const w = newWorld();
  await openProbe(w, statusPayload({ latest: latest({ workspace_files: [],
    workspace: { state: "verified", verified: true, reason: "一致", planned: "/p/ws/0123456789abcdef",
                 files: [], verified_empty: true } }) }));
  const text = w.root.textContent;
  check(text.includes("已核实（计划、派发与原生目录一致）") && text.includes("无（已核实为空）"),
        "verified empty workspace not shown");
  check(text.includes("/p/ws/0123456789abcdef"), "planned workspace path missing");
  return { verified: true };
}

async function scenarioUsageUnknownNotZero() {
  const w = newWorld();
  await openProbe(w, statusPayload({ latest: latest({ usage: null, usage_state: "unknown" }) }));
  const text = w.root.textContent;
  check(text.includes("未知（unknown）；不按 0 计"), "unknown usage not shown as unknown");
  return { unknown: true };
}

async function scenarioInflightPollingBounded() {
  const w = newWorld();
  const inflight = statusPayload({ available: false, inflight: { probe_id: "p", phase: "collecting" },
    blockers: [{ code: "probe_in_flight", message: "进行中" }] });
  await openProbe(w, inflight);
  let polls = 0;
  for (let i = 0; i < 400 && w.timers.length; i += 1) {
    const timer = w.timers.shift();
    check(timer.ms === 2000, "poll interval changed");
    timer.fn();
    await w.tick();
    if (w.pending.sessionProbeStatus.length) {
      polls += 1;
      w.pending.sessionProbeStatus.shift().resolve(inflight);
      await w.tick();
    }
  }
  check(w.timers.length === 0, "polling never stopped");
  check(polls > 0 && polls <= 70, "poll count " + polls + " not bounded by the plan's wait");
  check(calls(w, "prepareSessionProbe") === 0 && calls(w, "startSessionProbe") === 0, "polling prepared or started");
  // leaving the view stops further polling
  const w2 = newWorld();
  await openProbe(w2, inflight);
  clickTab(w2, "workbench");
  await w2.tick();
  w2.timers.splice(0).forEach((t) => t.fn());
  await w2.tick();
  check(calls(w2, "sessionProbeStatus") === 0, "a left view kept polling");
  return { polls };
}

async function scenarioOfflineSourceLabelled() {
  const w = newWorld();
  await openProbe(w, statusPayload({ adapter_source: "injected_offline_adapter" }));
  check(w.root.textContent.includes("离线/合成注入（非真实模型结果）"), "offline adapter not labelled");
  return { labelled: true };
}

async function scenarioDataAccessBody() {
  const seen = [];
  const sandbox = { console, JSON, Promise,
    fetch: async (url, options) => { seen.push({ url, options }); return { status: 200, text: async () => "{\"ok\":true}" }; } };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(GUI, "data_access.js"), "utf8"), sandbox, { filename: "data_access.js" });
  await sandbox.JH.api.sessionProbeStatus();
  await sandbox.JH.api.prepareSessionProbe();
  await sandbox.JH.api.startSessionProbe("0123456789abcdef");
  check(seen[0].url === "/api/session-probe" && seen[0].options.method === "GET" && !seen[0].options.body, "status is not a bodyless GET");
  check(seen[1].url === "/api/session-probe/prepare" && seen[1].options.body === "{}", "prepare carries a body");
  const body = JSON.parse(seen[2].options.body);
  check(JSON.stringify(Object.keys(body).sort()) === JSON.stringify(["confirm", "probe_id"]), "start body keys " + Object.keys(body));
  check(body.confirm === true && body.probe_id === "0123456789abcdef", "start body values");
  check(seen[2].options.headers["Content-Type"] === "application/json", "start is not JSON");
  return { keys: Object.keys(body).sort() };
}

const SCENARIOS = [
  ["open_is_read_only", scenarioOpenIsReadOnly],
  ["blocked_never_prepares", scenarioBlockedNeverPrepares],
  ["unconfirmed_click_does_nothing", scenarioUnconfirmedClickDoesNothing],
  ["confirmed_click_once", scenarioConfirmedClickOnce],
  ["leave_before_start", scenarioLeaveBeforeStart],
  ["late_status_after_leave", scenarioLateStatusAfterLeave],
  ["reply_is_text", scenarioReplyIsText],
  ["unknown_is_manual", scenarioUnknownIsManual],
  ["complete_is_not_task_success", scenarioCompleteIsNotTaskSuccess],
  ["usage_unknown_not_zero", scenarioUsageUnknownNotZero],
  ["workspace_unverified_not_empty", scenarioWorkspaceUnverifiedNotEmpty],
  ["workspace_verified_listing", scenarioWorkspaceVerifiedListing],
  ["inflight_polling_bounded", scenarioInflightPollingBounded],
  ["offline_source_labelled", scenarioOfflineSourceLabelled],
  ["data_access_start_body", scenarioDataAccessBody],
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
  const report = { harness: "hark06-session-probe", ok, count: SCENARIOS.length, scenarios: results };
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
