/* JH-020 run-history frontend behaviour harness (offline, no browser).
 *
 * Evaluates the REAL gui/components.js and gui/views.js against a DOM stub with
 * a stubbed JH.api, then asserts how the Agent/会话 view renders real run
 * history versus simulated registrations:
 *
 *   - history cards carry app/instance/role/model/thread/turn/state/time/usage;
 *   - the source notice states live=false and browser_supplied=false;
 *   - declared_complete is shown as an executor declaration, never as success;
 *   - simulated sessions still read "未连接（模拟登记）", distinct from history;
 *   - evidence states (verified / mismatch / missing) render their own text;
 *   - selecting a run uses the button's run id, not scraped card text;
 *   - a corrupt source renders its message instead of an empty list.
 *
 * No browser, no network, no model. Exits non-zero on any failed assertion.
 *
 *   node tests/js/jh020_run_history_harness.mjs [--json]
 */
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..");
// JH_GUI_DIR lets a mutation check run the same behaviours against a
// disposable copy of the GUI sources; default is the real gui/ directory.
const GUI = process.env.JH_GUI_DIR || path.join(REPO, "gui");

function makeNode(tag) {
  const node = {
    tagName: tag, children: [], dataset: {}, listeners: [],
    _text: "", className: "", placeholder: "", rows: 0, value: "",
    classList: { toggle() {}, add() {}, remove() {} },
    appendChild(child) { node.children.push(child); return child; },
    addEventListener(type, fn) { node.listeners.push({ type, fn }); },
    querySelectorAll() { return []; },
    querySelector() { return null; },
    closest() { return null; },
    get textContent() { return node._text + node.children.map((c) => c.textContent).join(""); },
    set textContent(value) { node._text = String(value); node.children = []; },
  };
  return node;
}

function walk(node, visit) {
  visit(node);
  node.children.forEach((child) => walk(child, visit));
}

function findAll(start, predicate) {
  const out = [];
  walk(start, (n) => { if (predicate(n)) out.push(n); });
  return out;
}

function makeDocument() {
  const byId = {};
  const doc = {
    listeners: [],
    createElement(tag) { return makeNode(tag); },
    getElementById(id) { return byId[id] || (byId[id] = makeNode("div")); },
    addEventListener(type, fn) { doc.listeners.push({ type, fn }); },
  };
  return doc;
}

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

function newWorld() {
  const document = makeDocument();
  const sandbox = { document, console, setTimeout, clearTimeout, JSON, Promise, Math,
                    Object, Array, String, Number, Boolean, RegExp, Error };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  for (const file of ["components.js", "views.js"]) {
    vm.runInContext(fs.readFileSync(path.join(GUI, file), "utf8"), context, { filename: file });
  }
  const pending = { capabilities: [], listTasks: [], listSessions: [], listDrafts: [],
                    taskDetail: [], reviewDelivery: [], saveGoalDraft: [],
                    listAgentRuns: [], agentRunDetail: [] };
  const api = {};
  for (const method of Object.keys(pending)) {
    api[method] = (...args) => {
      const d = deferred();
      d.args = args;
      pending[method].push(d);
      return d.promise;
    };
  }
  sandbox.JH.api = api;
  return { JH: sandbox.JH, document, pending, root: document.getElementById("view-root"),
           tick: () => new Promise((r) => setImmediate(r)) };
}

function clickTab(world, view) {
  const listener = world.document.getElementById("tabs").listeners
    .find((l) => l.type === "click");
  listener.fn({ target: { dataset: { view } } });
}

function clickRunButton(world, runId) {
  const buttons = findAll(world.root, (n) => n.className === "run-action");
  if (buttons.length !== 1) throw new Error("expected exactly one run-action button, got " + buttons.length);
  if (buttons[0].dataset.runId !== runId) throw new Error("run button carries the wrong run id");
  const list = findAll(world.root, (n) => n.className === "run-list")[0];
  if (!list) throw new Error("no run-list container");
  const listener = list.listeners.find((l) => l.type === "click");
  const button = buttons[0];
  listener.fn({ target: { closest: (sel) => (sel.includes("run-action") ? button : null) } });
}

const sessionsPayload = { ok: true, read_only: true, transport: "static-registry", count: 1,
  sessions: [{ session_id: "sim-1", app_kind: "zcode", instance_id: "i", role: "worker",
               model_id: null, connected: false, simulated: true,
               evidence: { a: "b" }, note: "未接入" }],
  note: "全部为静态登记：connected=false、simulated=true" };

function runPayload(over = {}) {
  return Object.assign({
    run_id: "codex-run-1-abc12345", events: ["reserved", "dispatched", "collected"],
    app_kind: "codex", instance_id: "codex-app-server/local", session_id: "new",
    role: "worker", requested_model: "gpt-6.1-sol", native_model: "gpt-6.1-sol",
    logical_task_id: "task_x", attempt: 1, request_fingerprint: "a".repeat(64),
    idempotency_key: "key-1",
    native_session_id: "01a1aaaa-1111-7000-8000-000000000001",
    native_turns: ["01a1bbbb-2222-7000-8000-000000000002"],
    record_state: "declared_complete", native_state: "completed",
    first_recorded_at: "2026-10-18T00:00:00Z", last_recorded_at: "2026-10-18T00:00:05Z",
    usage: { turn: { totalTokens: 120 }, thread_total: { totalTokens: 900 },
             model_context_window: 475000, source: { thread_id: "t", turn_id: "u" } },
    evidence: { state: "verified", detail: "digest and frame count match the file bytes",
                path: "server/path", sha256: "c".repeat(64), frames: 2, bytes: 10 },
    unverified: ["adapter observation only; review decides success"],
    declared_status: "completed", dispatch_uncertain_reason: null, snapshot: { parsed: true },
  }, over);
}

const runsEnvelope = (over = {}) => Object.assign({
  ok: true, read_only: true, live: false, state: "ok", count: 1, message: "",
  source: { configured: true, path: "server-configured", browser_supplied: false },
  note: "历史记录，不代表实时在线；declared_complete 是执行方声明，不代表任务验收成功。",
  runs: [runPayload()],
}, over);

async function openSessions(world, runsPayload) {
  world.JH.views.start();
  world.pending.capabilities.shift().resolve({ ok: true, unsupported: ["dispatch"], note: "n" });
  await world.tick();
  clickTab(world, "sessions");
  await world.tick();
  world.pending.listSessions.shift().resolve(sessionsPayload);
  await world.tick();
  for (const d of world.pending.listAgentRuns.splice(0)) d.resolve(runsPayload);
  await world.tick();
}

const results = {};

async function scenarioHistoryCardFields() {
  const world = newWorld();
  await openSessions(world, runsEnvelope());
  const text = world.root.textContent;
  for (const needle of ["codex-run-1-abc12345", "codex", "codex-app-server/local", "worker",
                        "gpt-6.1-sol", "01a1aaaa-1111-7000-8000-000000000001",
                        "01a1bbbb-2222-7000-8000-000000000002", "declared_complete",
                        "2026-10-18T00:00:05Z"]) {
    if (!text.includes(needle)) throw new Error("run card missing " + needle);
  }
  if (!text.includes("登记状态：ok")) throw new Error("source state not shown");
  if (!text.includes("live=false")) throw new Error("live=false not shown");
  if (!text.includes("browser_supplied=false")) throw new Error("browser_supplied not shown");
  if (!text.includes("本轮用量(turn)")) throw new Error("turn usage label missing");
  if (!text.includes("线程累计(thread_total)")) throw new Error("thread usage label missing");
  if (!text.includes("证据核验：verified")) throw new Error("evidence state not shown");
  return { hasFields: true };
}

async function scenarioDeclaredCompleteIsNotSuccess() {
  const world = newWorld();
  await openSessions(world, runsEnvelope());
  const text = world.root.textContent;
  if (!text.includes("不代表任务验收成功")) throw new Error("claim disclaimer missing");
  if (text.includes("succeeded")) throw new Error("a success literal leaked into the view");
  return { disclaimerShown: true };
}

async function scenarioSimulatedIsDistinctFromHistory() {
  const world = newWorld();
  await openSessions(world, runsEnvelope());
  const text = world.root.textContent;
  if (!text.includes("未连接（模拟登记）")) throw new Error("simulated notice missing");
  if (!text.includes("运行记录（历史，只读）")) throw new Error("history panel missing");
  return { distinct: true };
}

async function scenarioEvidenceStatesRenderDifferently() {
  const world = newWorld();
  await openSessions(world, runsEnvelope({ runs: [runPayload({ evidence: {
    state: "mismatch", detail: "raw evidence digest does not match the file's bytes" } })] }));
  const text = world.root.textContent;
  if (!text.includes("证据核验：mismatch")) throw new Error("mismatch state not shown");
  if (text.includes("证据核验：verified")) throw new Error("mismatch rendered as verified");
  return { mismatchShown: true };
}

async function scenarioSelectingARunUsesItsId() {
  const world = newWorld();
  await openSessions(world, runsEnvelope());
  clickRunButton(world, "codex-run-1-abc12345");
  await world.tick();
  const request = world.pending.agentRunDetail.shift();
  if (!request) throw new Error("no detail request was issued");
  if (request.args[0] !== "codex-run-1-abc12345") throw new Error("detail asked for the wrong run");
  request.resolve({ ok: true, read_only: true, state: "ok",
                    run: Object.assign(runPayload(), { link: { checked: true, linked: false,
                      note: "任务库中没有该 id 的任务；未关联（不创建任务记录）" } }),
                    records: [] });
  await world.tick();
  const text = world.root.textContent;
  if (!text.includes("关联任务")) throw new Error("task link section missing");
  if (!text.includes("linked")) throw new Error("link state missing");
  if (!text.includes("false")) throw new Error("link value missing");
  if (!text.includes("未关联")) throw new Error("unlinked note missing");
  if (!text.includes("declared_complete")) throw new Error("detail lost the record state");
  return { askedForTheRunId: true };
}

async function scenarioCorruptSourceShowsItsMessage() {
  const world = newWorld();
  await openSessions(world, runsEnvelope({ state: "corrupt", runs: [], count: 0,
    message: "registry history is corrupt: run_id must be a non-empty string",
    error: { code: "runs_corrupt", message: "registry history is corrupt" } }));
  const text = world.root.textContent;
  if (!text.includes("登记状态：corrupt")) throw new Error("corrupt state not shown");
  if (!text.includes("corrupt")) throw new Error("corrupt message not shown");
  if (text.includes("codex-run-1-abc12345")) throw new Error("a run card rendered for a corrupt source");
  return { corruptShown: true };
}

async function scenarioAbsentSourceIsNotAnError() {
  const world = newWorld();
  await openSessions(world, runsEnvelope({ state: "absent", runs: [], count: 0,
    message: "登记文件不存在：server-configured（尚无运行记录）" }));
  const text = world.root.textContent;
  if (!text.includes("登记状态：absent")) throw new Error("absent state not shown");
  if (!text.includes("登记文件不存在")) throw new Error("absent message not shown");
  return { absentShown: true };
}

function clickRun(world, id) {
  const list = findAll(world.root, (n) => n.className === "run-list")[0];
  const button = findAll(world.root, (n) => n.className === "run-action" && n.dataset.runId === id)[0];
  if (!list || !button) throw new Error("run button not found: " + id);
  list.listeners.find((l) => l.type === "click").fn({ target: { closest: () => button } });
}

function detailText(world) {
  const pane = findAll(world.root, (n) => n.className === "run-detail")[0];
  return pane ? pane.textContent : "";
}

const runDetailEnvelope = (id, over) => ({
  ok: true, read_only: true, state: "ok",
  run: Object.assign(runPayload({ run_id: id,
    link: { checked: false, linked: false, note: "review fixture" } }), over || {}),
  records: [],
});

async function scenarioDetailReverseReplies() {
  const w = newWorld();
  await openSessions(w, runsEnvelope({ count: 2,
    runs: [runPayload({ run_id: "RUN-A" }), runPayload({ run_id: "RUN-B" })] }));
  clickRun(w, "RUN-A");
  clickRun(w, "RUN-B");
  const a = w.pending.agentRunDetail.shift();
  const b = w.pending.agentRunDetail.shift();
  b.resolve(runDetailEnvelope("RUN-B"));
  await w.tick();
  const afterB = detailText(w);
  a.resolve(runDetailEnvelope("RUN-A"));
  await w.tick();
  const afterA = detailText(w);
  if (!afterB.includes("RUN-B")) throw new Error("B was not rendered");
  if (afterA.includes("RUN-A")) throw new Error("late A replaced B");
  return { latestKept: "RUN-B", staleDropped: true };
}

async function scenarioDetailSameIdRepeat() {
  const w = newWorld();
  await openSessions(w, runsEnvelope());
  clickRun(w, "codex-run-1-abc12345");
  clickRun(w, "codex-run-1-abc12345");
  const old = w.pending.agentRunDetail.shift();
  const fresh = w.pending.agentRunDetail.shift();
  fresh.resolve(runDetailEnvelope("codex-run-1-abc12345", { native_model: "NEW-MODEL" }));
  await w.tick();
  old.resolve(runDetailEnvelope("codex-run-1-abc12345", { native_model: "OLD-MODEL" }));
  await w.tick();
  const text = detailText(w);
  if (!text.includes("NEW-MODEL") || text.includes("OLD-MODEL")) {
    throw new Error("an older reply for the same id replaced the newer one");
  }
  return { newestWins: true };
}

async function scenarioDetailLateError() {
  const w = newWorld();
  await openSessions(w, runsEnvelope({ count: 2,
    runs: [runPayload({ run_id: "RUN-A" }), runPayload({ run_id: "RUN-B" })] }));
  clickRun(w, "RUN-A");
  clickRun(w, "RUN-B");
  const a = w.pending.agentRunDetail.shift();
  const b = w.pending.agentRunDetail.shift();
  b.resolve(runDetailEnvelope("RUN-B"));
  await w.tick();
  a.resolve({ ok: false, error: { code: "late_A_error", message: "LATE-A-ERROR" } });
  await w.tick();
  const text = detailText(w);
  if (text.includes("LATE-A-ERROR")) throw new Error("a late error erased the current detail");
  if (!text.includes("RUN-B")) throw new Error("the current detail was cleared");
  return { lateErrorIgnored: true };
}

async function scenarioDetailUnrequestedRunId() {
  const w = newWorld();
  await openSessions(w, runsEnvelope());
  clickRun(w, "codex-run-1-abc12345");
  w.pending.agentRunDetail.shift().resolve(runDetailEnvelope("UNREQUESTED-RUN"));
  await w.tick();
  const text = w.root.textContent;
  if (text.includes("UNREQUESTED-RUN")) throw new Error("a run that was never requested was rendered");
  if (!text.includes("unexpected_run_id")) throw new Error("the mismatch was not reported");
  return { unrequestedRejected: true };
}

async function scenarioDetailLeaveView() {
  const w = newWorld();
  await openSessions(w, runsEnvelope());
  clickRun(w, "codex-run-1-abc12345");
  const stale = w.pending.agentRunDetail.shift();
  clickTab(w, "workbench");
  await w.tick();
  stale.resolve(runDetailEnvelope("LATE-OLD-RUN"));
  await w.tick();
  if (w.root.textContent.includes("LATE-OLD-RUN")) throw new Error("a late reply painted another view");
  return { crossViewInert: true };
}

async function scenarioDetailReenterView() {
  const w = newWorld();
  await openSessions(w, runsEnvelope());
  clickRun(w, "codex-run-1-abc12345");
  const stale = w.pending.agentRunDetail.shift();
  clickTab(w, "workbench");
  await w.tick();
  clickTab(w, "sessions");
  await w.tick();
  w.pending.listSessions.shift().resolve(sessionsPayload);
  await w.tick();
  for (const d of w.pending.listAgentRuns.splice(0)) d.resolve(runsEnvelope());
  await w.tick();
  stale.resolve(runDetailEnvelope("OLD-PANEL-RUN"));
  await w.tick();
  if (w.root.textContent.includes("OLD-PANEL-RUN")) throw new Error("a replaced panel's reply painted");
  return { oldPanelInert: true };
}

const SCENARIOS = [
  ["history_card_fields", scenarioHistoryCardFields],
  ["declared_complete_is_not_success", scenarioDeclaredCompleteIsNotSuccess],
  ["simulated_distinct_from_history", scenarioSimulatedIsDistinctFromHistory],
  ["evidence_states_render_differently", scenarioEvidenceStatesRenderDifferently],
  ["selecting_a_run_uses_its_id", scenarioSelectingARunUsesItsId],
  ["corrupt_source_shows_its_message", scenarioCorruptSourceShowsItsMessage],
  ["absent_source_is_not_an_error", scenarioAbsentSourceIsNotAnError],
  ["detail_reverse_replies", scenarioDetailReverseReplies],
  ["detail_same_id_repeat", scenarioDetailSameIdRepeat],
  ["detail_late_error", scenarioDetailLateError],
  ["detail_unrequested_run_id", scenarioDetailUnrequestedRunId],
  ["detail_leave_view", scenarioDetailLeaveView],
  ["detail_reenter_view", scenarioDetailReenterView],
];

async function main() {
  let ok = true;
  for (const [name, fn] of SCENARIOS) {
    try {
      results[name] = { ok: true, detail: await fn() };
    } catch (err) {
      ok = false;
      results[name] = { ok: false, error: String(err && err.message || err) };
    }
  }
  const report = { harness: "jh020-run-history", ok, scenarios: results };
  if (process.argv.includes("--json")) {
    process.stdout.write(JSON.stringify(report, null, 2) + "\n");
  } else {
    for (const [name, r] of Object.entries(results)) {
      process.stdout.write((r.ok ? "ok   " : "FAIL ") + name + (r.error ? " :: " + r.error : "") + "\n");
    }
  }
  process.exit(ok ? 0 : 1);
}

main();
