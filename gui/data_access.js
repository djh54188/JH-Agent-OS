/* JH-019 数据访问层 (data access layer).
 *
 * The ONLY module that talks to the backend. Pages call these functions and
 * receive plain envelopes; they never use fetch, never see a database, never
 * launch a tool and never decide whether a task succeeded.
 *
 * Every call resolves to the backend envelope, including error envelopes, so a
 * page can render a real error state instead of guessing.
 */
window.JH = window.JH || {};

JH.api = (function () {
  "use strict";

  async function request(method, path, body) {
    const options = { method: method, headers: { "Accept": "application/json" } };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    try {
      const response = await fetch(path, options);
      const text = await response.text();
      let payload;
      try {
        payload = JSON.parse(text);
      } catch (err) {
        return { ok: false, error: { code: "bad_response",
          message: "后端返回的不是 JSON（HTTP " + response.status + "）" } };
      }
      return payload;
    } catch (err) {
      return { ok: false, error: { code: "network_error",
        message: "无法连接本地接口：" + err.message } };
    }
  }

  return {
    capabilities: function () { return request("GET", "/api/capabilities"); },
    listTasks: function () { return request("GET", "/api/tasks"); },
    taskDetail: function (taskId) {
      return request("GET", "/api/tasks/" + encodeURIComponent(taskId));
    },
    reviewDelivery: function (taskId) {
      return request("GET", "/api/tasks/" + encodeURIComponent(taskId) + "/delivery");
    },
    listSessions: function () { return request("GET", "/api/sessions"); },
    listAgentRuns: function () { return request("GET", "/api/agent-runs"); },
    agentRunDetail: function (runId) {
      return request("GET", "/api/agent-runs/" + encodeURIComponent(runId));
    },
    listDrafts: function () { return request("GET", "/api/drafts"); },
    /* Hark-派单-06 会话试运行：状态只读；prepare 只签发一次性编号；
     * start 只携带 probe_id 与 confirm，其余全部由服务端决定。 */
    sessionProbeStatus: function () { return request("GET", "/api/session-probe"); },
    prepareSessionProbe: function () { return request("POST", "/api/session-probe/prepare", {}); },
    startSessionProbe: function (probeId) {
      return request("POST", "/api/session-probe/start", { probe_id: probeId, confirm: true });
    },
    saveGoalDraft: function (draft) { return request("POST", "/api/drafts", draft); }
  };
})();
