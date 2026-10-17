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

  function field(label, value) {
    const row = el("div", "field");
    row.appendChild(el("span", "field-label", label));
    row.appendChild(el("span", "field-value",
      value === undefined || value === null || value === "" ? "—" : String(value)));
    return row;
  }

  /* 状态标记：只显示后端记录的字符串，不推断成功与否。 */
  function statusBadge(status) {
    const badge = el("span", "badge", status === undefined || status === null ? "未知" : status);
    badge.dataset.status = String(status);
    return badge;
  }

  /* 连接提示：未接入/模拟登记必须显式显示，不能伪装成已连接。 */
  function connectionNotice(session) {
    const box = el("div", session.connected ? "notice notice-ok" : "notice notice-warn");
    box.appendChild(el("strong", null,
      session.connected ? "已连接" : "未连接（模拟登记）"));
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

  return { el: el, field: field, statusBadge: statusBadge,
           connectionNotice: connectionNotice, evidenceList: evidenceList,
           emptyState: emptyState, errorState: errorState,
           loadingState: loadingState, note: note };
})();
