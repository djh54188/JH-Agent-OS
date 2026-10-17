/* JH-019 frontend request-generation harness (behavioural, offline).
 *
 * Runs the real gui/components.js + gui/views.js inside a tiny DOM stub with a
 * stubbed JH.api whose promises the harness resolves in a chosen order. It
 * asserts that a LATE response never paints over the page the user is on and
 * that a stale draft-list reply neither clears nor duplicates the list.
 *
 * No browser, no network, no model. Exits non-zero on any failed assertion.
 *
 *   node tests/js/jh019_generation_harness.mjs [--json]
 */
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..");
const GUI = path.join(REPO, "gui");

// ---------------------------------------------------------------- DOM stub --
function makeNode(tag) {
  const node = {
    tagName: tag,
    children: [],
    dataset: {},
    listeners: [],
    _text: "",
    className: "",
    placeholder: "",
    rows: 0,
    value: "",
    classList: { toggle() {}, add() {}, remove() {} },
    appendChild(child) { node.children.push(child); return child; },
    addEventListener(type, fn) { node.listeners.push({ type, fn }); },
    querySelectorAll() { return []; },
    querySelector() { return null; },
    get textContent() {
      return node._text + node.children.map((c) => c.textContent).join("");
    },
    set textContent(value) { node._text = String(value); node.children = []; },
  };
  return node;
}

function walk(node, visit) {
  visit(node);
  node.children.forEach((child) => walk(child, visit));
}

function findByText(start, text) {
  let found = null;
  walk(start, (n) => { if (!found && n._text === text) found = n; });
  return found;
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
    createElement: (tag) => makeNode(tag),
    getElementById(id) {
      if (!byId[id]) byId[id] = makeNode("div");
      return byId[id];
    },
    addEventListener(type, fn) { doc.listeners.push({ type, fn }); },
    _byId: byId,
  };
  return doc;
}

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

// Fresh world: fresh DOM + freshly evaluated components.js/views.js so module
// state (generation counters) never leaks between scenarios.
function newWorld() {
  const document = makeDocument();
  const sandbox = { document, console, setTimeout, clearTimeout, JSON, Promise, Math, Object, Array, String, Number, Boolean, RegExp, Error };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  for (const file of ["components.js", "views.js"]) {
    const code = fs.readFileSync(path.join(GUI, file), "utf8");
    vm.runInContext(code, context, { filename: file });
  }
  const pending = { capabilities: [], listTasks: [], listSessions: [], listDrafts: [], taskDetail: [], reviewDelivery: [], saveGoalDraft: [] };
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
  return {
    JH: sandbox.JH, document, pending,
    root: document.getElementById("view-root"),
    tabs: document.getElementById("tabs"),
    tick: () => new Promise((r) => setImmediate(r)),
  };
}

function clickTab(world, view) {
  const listener = world.tabs.listeners.find((l) => l.type === "click");
  listener.fn({ target: { dataset: { view } } });
}

function clickTaskAction(world, taskId, view) {
  const listener = world.document.listeners.find((l) => l.type === "click");
  const button = { dataset: { taskId, view } };
  listener.fn({ target: { closest: (selector) => (selector.includes("task-action") ? button : null) } });
}

const sessionsPayload = (ok = true) => ({
  ok, read_only: true, transport: "static-registry (JH-019: no adapter wired)",
  sessions: ok ? [{ session_id: "sim-1", app_kind: "zcode", instance_id: "i", role: "worker",
                    model_id: null, connected: false, simulated: true, evidence: { a: "b" }, note: "未接入" }] : [],
  count: ok ? 1 : 0, note: "全部为静态登记",
});

const tasksPayload = (tasks) => ({ ok: true, read_only: true, database_present: true, tasks, count: tasks.length });
const taskRow = (id) => ({ task_id: id, title: "任务 " + id, status: "created", review_verdict: null, updated_at: "2026-10-17T00:00:00Z" });

// ------------------------------------------------------------- scenarios ---
const results = {};

async function scenarioWorkbenchThenSessions() {
  const world = newWorld();
  world.JH.views.start();
  world.pending.capabilities.shift().resolve({ ok: true, unsupported: ["dispatch"], note: "n" });
  await world.tick();
  // workbench request is pending; user switches to the sessions view
  clickTab(world, "sessions");
  await world.tick();
  world.pending.listSessions.shift().resolve(sessionsPayload());
  await world.tick();
  const afterSessions = world.root.textContent;
  if (!afterSessions.includes("Agent / 会话")) throw new Error("sessions view did not render");
  if (!afterSessions.includes("未连接（模拟登记）")) throw new Error("sessions view missing disconnect notice");

  // the OLD workbench reply arrives late
  world.pending.listTasks.shift().resolve(tasksPayload([taskRow("task_late")]));
  await world.tick();
  const afterLate = world.root.textContent;
  if (!afterLate.includes("Agent / 会话")) throw new Error("late workbench reply overwrote the sessions view");
  if (afterLate.includes("目标草案")) throw new Error("late workbench reply painted the workbench panel");
  if (afterLate.includes("task_late")) throw new Error("late workbench data leaked into the DOM");
  return { rendered: afterSessions.includes("Agent / 会话"), lateIgnored: true };
}

async function scenarioTaskSwitch() {
  const world = newWorld();
  world.JH.views.start();
  world.pending.capabilities.shift().resolve({ ok: true, unsupported: [], note: "n" });
  await world.tick();
  world.pending.listTasks.shift().resolve(tasksPayload([taskRow("task_A"), taskRow("task_B")]));
  await world.tick();
  if (!world.root.textContent.includes("查看详情")) throw new Error("task actions not rendered");

  clickTaskAction(world, "task_A", "task");
  await world.tick();
  clickTaskAction(world, "task_B", "task");
  await world.tick();

  const detailB = world.pending.taskDetail.pop();          // B requested last
  detailB.resolve({ ok: true, read_only: true,
                    task_result: { task_id: detailB.args[0], title: "TITLE_B", status: "created",
                                   runtime_usage: {}, blockers: [] },
                    events_count: 1, artifact_versions: [], note: "n" });
  await world.tick();
  if (!world.root.textContent.includes("TITLE_B")) throw new Error("task B detail did not render");

  const detailA = world.pending.taskDetail.pop();          // A's reply is late
  detailA.resolve({ ok: true, read_only: true,
                    task_result: { task_id: detailA.args[0], title: "TITLE_A", status: "created",
                                   runtime_usage: {}, blockers: [] },
                    events_count: 1, artifact_versions: [], note: "n" });
  await world.tick();
  const text = world.root.textContent;
  if (!text.includes("TITLE_B")) throw new Error("late task A reply replaced task B");
  if (text.includes("TITLE_A")) throw new Error("late task A data leaked into the DOM");
  return { showedB: true, lateAIgnored: true };
}

async function scenarioDraftListOutOfOrder() {
  const world = newWorld();
  world.JH.views.start();
  world.pending.capabilities.shift().resolve({ ok: true, unsupported: [], note: "n" });
  await world.tick();
  world.pending.listTasks.shift().resolve(tasksPayload([]));
  await world.tick();

  // The first list request is still pending, so no draft is rendered yet.
  if (countDraftCards(world) !== 0) throw new Error("draft list should be empty while pending");
  const firstList = world.pending.listDrafts.shift();       // older list request (resolved last)

  const save = findByText(world.root, "保存草案");
  if (!save) throw new Error("save button not found");
  const titleInput = findAll(world.root, (n) => n.tagName === "input")[0];
  const goalInput = findAll(world.root, (n) => n.tagName === "textarea")[0];
  titleInput.value = "草案标题";
  goalInput.value = "草案目标";
  save.listeners.find((l) => l.type === "click").fn();
  await world.tick();

  const saveReq = world.pending.saveGoalDraft.shift();
  saveReq.resolve({ ok: true, read_only: false,
                    draft: { draft_id: "draft-XYZ", draft: true, execution: "not_triggered",
                             title: "草案标题", created_at: "2026-10-17T00:00:00Z" },
                    note: "已保存为草案；未触发任何执行" });
  await world.tick();                                       // handler calls refreshDrafts()
  const secondList = world.pending.listDrafts.shift();

  secondList.resolve({ ok: true, read_only: true, count: 1,
                       drafts: [{ draft_id: "draft-XYZ", title: "草案标题",
                                  execution: "not_triggered", created_at: "2026-10-17T00:00:00Z" }],
                       note: "n" });
  await world.tick();
  const cardsAfterSave = countDraftCards(world);
  if (cardsAfterSave !== 1) throw new Error("expected 1 draft card after save, got " + cardsAfterSave);

  firstList.resolve({ ok: true, read_only: true, drafts: [], count: 0, note: "n" });   // stale empty reply
  await world.tick();
  const text = world.root.textContent;
  if (countDraftCards(world) !== 1) throw new Error("stale list reply changed the card count");
  if (text.includes("还没有草案")) throw new Error("stale empty list overwrote the rendered draft");
  return { cardsAfterSave, cardsAfterStale: countDraftCards(world), staleIgnored: true };
}

async function scenarioSaveAcrossViewSwitch() {
  /* The reported sequence: save pending -> switch to sessions -> back to the
   * workbench -> NEW list request pending -> OLD save completes -> the new list
   * returns the draft. The new list must render; a stale panel's callback must
   * not invalidate the current panel's request. */
  const world = newWorld();
  world.JH.views.start();
  world.pending.capabilities.shift().resolve({ ok: true, unsupported: [], note: "n" });
  await world.tick();
  world.pending.listTasks.shift().resolve(tasksPayload([]));
  await world.tick();                                        // panel A + list request L1

  await saveDraft(world, "跨面板草案", "保存请求挂起时切走再回来");   // save pending
  const save = world.pending.saveGoalDraft.shift();

  clickTab(world, "sessions");
  await world.tick();
  world.pending.listSessions.shift().resolve(sessionsPayload());
  await world.tick();
  if (!world.root.textContent.includes("Agent / 会话")) throw new Error("sessions view did not render");

  clickTab(world, "workbench");
  await world.tick();
  world.pending.listTasks.shift().resolve(tasksPayload([]));
  await world.tick();                                        // panel B + list request L2

  const listA = world.pending.listDrafts.shift();             // panel A's request
  const listB = world.pending.listDrafts.shift();             // panel B's request
  if (!listA || !listB) throw new Error("expected one list request per panel");

  save.resolve({ ok: true, read_only: false,
                 draft: { draft_id: "draft-SWITCH", draft: true, execution: "not_triggered",
                          title: "跨面板草案", created_at: "2026-10-17T00:00:00Z" },
                 note: "已保存为草案；未触发任何执行" });
  await world.tick();
  // The stale panel must NOT have issued a further request that could invalidate
  // the current panel's in-flight one.
  if (world.pending.listDrafts.length !== 0) {
    throw new Error("stale panel issued another draft-list request");
  }

  listB.resolve({ ok: true, read_only: true, count: 1,
                  drafts: [{ draft_id: "draft-SWITCH", title: "跨面板草案",
                             execution: "not_triggered", created_at: "2026-10-17T00:00:00Z" }],
                  note: "n" });
  await world.tick();
  const cardsAfterCurrentList = countDraftCards(world);
  if (cardsAfterCurrentList !== 1) {
    throw new Error("current panel list did not render the draft (cards=" + cardsAfterCurrentList + ")");
  }
  if (world.root.textContent.includes("还没有草案")) {
    throw new Error("current panel shows the empty state despite a stored draft");
  }

  listA.resolve({ ok: true, read_only: true, drafts: [], count: 0, note: "n" });   // stale, last
  await world.tick();
  if (countDraftCards(world) !== 1) throw new Error("stale panel reply changed the card count");
  return { cardsAfterCurrentList, cardsAfterStale: countDraftCards(world), stalePanelInert: true };
}

async function saveDraft(world, title, goal) {
  const save = findByText(world.root, "保存草案");
  if (!save) throw new Error("save button not found");
  const titleInput = findAll(world.root, (n) => n.tagName === "input")[0];
  const goalInput = findAll(world.root, (n) => n.tagName === "textarea")[0];
  titleInput.value = title;
  goalInput.value = goal;
  save.listeners.find((l) => l.type === "click").fn();
  await world.tick();
}

function countDraftCards(world) {
  let list = null;
  walk(world.root, (n) => { if (!list && n.className === "draft-list") list = n; });
  if (!list) return 0;
  return list.children.filter((c) => c.className === "card").length;
}

// ------------------------------------------------------------------ main ---
const scenarios = {
  workbench_then_sessions_late_workbench: scenarioWorkbenchThenSessions,
  task_switch_late_A_after_B: scenarioTaskSwitch,
  draft_list_stale_empty_after_save: scenarioDraftListOutOfOrder,
  save_pending_across_view_switch: scenarioSaveAcrossViewSwitch,
};

const report = { harness: "jh019-generation", scenarios: {}, ok: true };
for (const [name, fn] of Object.entries(scenarios)) {
  try {
    report.scenarios[name] = { ok: true, detail: await fn() };
  } catch (err) {
    report.ok = false;
    report.scenarios[name] = { ok: false, error: String(err.message || err) };
  }
}
const asJson = process.argv.includes("--json");
process.stdout.write(JSON.stringify(report, null, 2) + "\n");
if (!asJson) {
  for (const [name, r] of Object.entries(report.scenarios)) {
    process.stdout.write(`${r.ok ? "PASS" : "FAIL"} ${name}${r.ok ? "" : " :: " + r.error}\n`);
  }
}
process.exit(report.ok ? 0 : 1);
