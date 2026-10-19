/* JH-019 公共组件 (shared components).
 *
 * Pure rendering helpers. They take data and return DOM nodes built with
 * textContent — never innerHTML with data — and contain no fetch, no database
 * access and no success logic: whatever the backend recorded is what is shown.
 */
window.JH = window.JH || {};

JH.components = (function () {
  "use strict";

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  /* opts.mono renders the value in a monospace face that wraps anywhere
   * (long run/thread/turn ids); purely presentational. */
  function field(label, value, opts) {
    const row = el("div", "field");
    row.appendChild(el("span", "field-label", label));
    const empty = value === undefined || value === null || value === "";
    const valueNode = el("span", opts && opts.mono && !empty ? "field-value mono" : "field-value",
      empty ? "—" : String(value));
    if (!empty) valueNode.title = String(value);
    row.appendChild(valueNode);
    return row;
  }

  /* 类别标签：用文字区分「模拟登记 / 历史运行 / 证据 / 执行方声明」，颜色只做辅助。 */
  function kindTag(kind, text) {
    const tag = el("span", "kind-tag kind-" + kind, text);
    tag.dataset.kind = kind;
    return tag;
  }

  function stateIcon(symbol) {
    return el("span", "state-icon", symbol);
  }

  /* 图例：每一类都以文字 + 符号说明，不依赖颜色。 */
  function legend() {
    const box = el("div", "legend");
    [["sim", "◇ 模拟登记", "静态配置，未接入、非实时"],
     ["hist", "● 历史运行", "已登记的真实会话记录，只读"],
     ["evidence", "✓ / ✕ / ○ 证据状态", "原始证据与记录的核对结果"],
     ["claim", "✎ 执行方声明", "执行方自报，不等于任务验收成功"]].forEach(function (item) {
      const entry = el("span", "legend-item");
      entry.appendChild(kindTag(item[0], item[1]));
      entry.appendChild(el("span", null, item[2]));
      box.appendChild(entry);
    });
    return box;
  }

  /* 状态标记：只显示后端记录的字符串，不推断成功与否。 */
  function statusBadge(status) {
    const badge = el("span", "badge", status === undefined || status === null ? "未知" : status);
    badge.dataset.status = String(status);
    return badge;
  }

  /* 连接提示：未接入/模拟登记必须显式显示，不能伪装成已连接。 */
  function connectionNotice(session) {
    const box = el("div", session.connected ? "notice notice-ok" : "notice notice-sim");
    const title = el("strong");
    title.appendChild(stateIcon(session.connected ? "●" : "◇"));
    title.appendChild(el("span", null, session.connected ? "已连接" : "未连接（模拟登记）"));
    box.appendChild(title);
    box.appendChild(el("div", "muted", "simulated=" + String(session.simulated)
      + " · connected=" + String(session.connected)));
    if (session.note) box.appendChild(el("div", "muted", session.note));
    return box;
  }

  function evidenceList(evidence) {
    const list = el("ul", "evidence");
    Object.keys(evidence || {}).forEach(function (key) {
      const item = el("li");
      item.appendChild(el("span", "evidence-key", key + "："));
      item.appendChild(el("span", null, evidence[key]));
      list.appendChild(item);
    });
    return list;
  }

  function emptyState(message) {
    const box = el("div", "state state-empty");
    box.appendChild(el("strong", null, "空"));
    box.appendChild(el("div", "muted", message || "没有可显示的数据"));
    return box;
  }

  function errorState(error) {
    const box = el("div", "state state-error");
    box.appendChild(el("strong", null, "错误"));
    box.appendChild(el("div", "muted", "code=" + (error && error.code ? error.code : "unknown")));
    box.appendChild(el("div", "muted", (error && error.message) || "未提供错误信息"));
    return box;
  }

  function loadingState(message) {
    const box = el("div", "state state-loading");
    box.appendChild(el("strong", null, "加载中"));
    box.appendChild(el("div", "muted", message || "正在读取后端记录…"));
    return box;
  }

  function note(text) {
    return el("p", "note", text);
  }

  /* 运行记录来源状态：历史/缺失/空/损坏/不可读各自显式显示，不互相冒充。 */
  function runSourceNotice(envelope) {
    const state = (envelope && envelope.state) || "unknown";
    const kind = state === "ok" ? "notice-ok"
      : (state === "absent" || state === "empty") ? "notice"
      : "notice-warn";
    const box = el("div", "notice " + kind);
    box.dataset.state = state;
    const title = el("strong");
    title.appendChild(stateIcon(state === "ok" ? "✓" : (state === "absent" || state === "empty") ? "○" : "✕"));
    title.appendChild(el("span", null, "登记状态：" + state));
    box.appendChild(title);
    box.appendChild(el("div", "muted", "live=" + String(!!(envelope && envelope.live))
      + " · source=" + ((envelope && envelope.source && envelope.source.path) || "—")
      + " · browser_supplied=" + String(!!(envelope && envelope.source && envelope.source.browser_supplied))));
    if (envelope && envelope.message) box.appendChild(el("div", "muted", envelope.message));
    return box;
  }

  /* 证据核验状态：verified / missing / mismatch / unverifiable 四态分明。 */
  function evidenceNotice(evidence) {
    const state = (evidence && evidence.state) || "unknown";
    const kind = state === "verified" ? "notice-ok"
      : state === "mismatch" ? "notice-warn" : "notice";
    const box = el("div", "notice " + kind);
    box.dataset.state = state;
    const title = el("strong");
    title.appendChild(stateIcon(state === "verified" ? "✓"
      : (state === "missing" || state === "unverifiable") ? "○" : state === "unknown" ? "?" : "✕"));
    title.appendChild(el("span", null, "证据核验：" + state));
    box.appendChild(title);
    box.appendChild(el("div", "muted", (evidence && evidence.detail) || "—"));
    if (evidence && evidence.sha256) {
      box.appendChild(el("div", "muted mono", "sha256=" + evidence.sha256));
      box.appendChild(el("div", "muted", "frames=" + evidence.frames + " · bytes=" + evidence.bytes));
    }
    return box;
  }

  function bulletList(title, items) {
    const box = el("div", "subpanel");
    box.appendChild(el("h4", null, title));
    if (!items || !items.length) {
      box.appendChild(el("div", "muted", "—"));
      return box;
    }
    items.forEach(function (item) { box.appendChild(el("div", "muted", String(item))); });
    return box;
  }

  return { el: el, field: field, statusBadge: statusBadge,
           connectionNotice: connectionNotice, evidenceList: evidenceList,
           runSourceNotice: runSourceNotice, evidenceNotice: evidenceNotice,
           bulletList: bulletList, kindTag: kindTag, legend: legend,
           emptyState: emptyState, errorState: errorState,
           loadingState: loadingState, note: note };
})();
