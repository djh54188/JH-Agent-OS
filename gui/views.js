/* JH-019 页面模块 (page modules).
 *
 * Four views: 工作台 / 任务详情 / Agent·会话 / 审查与交付. They only call
 * JH.api and JH.components. They never fetch directly, never touch a database,
 * never launch a tool and never derive "成功" themselves — the status shown is
 * the string the backend recorded.
 */
window.JH = window.JH || {};

JH.views = (function () {
  "use strict";

  const C = function () { return JH.components; };
  const root = function () { return document.getElementById("view-root"); };

  /* Request generations. Every view switch and every task selection starts a
   * new generation; a response may only paint when its generation is still the
   * current one, so a late reply can never overwrite the page the user is on.
   * The draft list keeps its own counter so an older list reply cannot回写 an
   * empty list or append twice. */
  let renderGeneration = 0;
  let currentTaskId = null;      // shared by the task/delivery views

  function beginRender() { renderGeneration += 1; return renderGeneration; }
  function isCurrent(gen) { return gen === renderGeneration; }

  function render(children, gen) {
    const node = root();
    if (gen !== undefined) { node.dataset.generation = String(gen); }
    node.textContent = "";
    children.forEach(function (child) { node.appendChild(child); });
  }

  function heading(text, sub) {
    const wrap = C().el("section", "panel");
    wrap.appendChild(C().el("h2", null, text));
    if (sub) wrap.appendChild(C().el("p", "muted", sub));
    return wrap;
  }

  function envelopeOrError(view, envelope, onOk) {
    if (!envelope || envelope.ok !== true) {
      render([heading(view), C().errorState((envelope && envelope.error) || {})]);
      return;
    }
    onOk();
  }

  /* 工作台：任务索引 + 草案入口。 */
  function workbench() {
    const gen = beginRender();
    render([heading("工作台", "只读任务索引；草案是明确的输入，不会自动执行"),
            C().loadingState("正在读取任务索引…")], gen);

    JH.api.listTasks().then(function (envelope) {
      if (!isCurrent(gen)) { return; }             // a newer view took over
      envelopeOrError("工作台", envelope, function () {
        const panel = C().el("section", "panel");
        if (envelope.database_present === false) {
          panel.appendChild(C().emptyState(
            "任务数据库不存在（尚无任务记录）：" + (envelope.note || "")));
        } else if (!envelope.tasks || envelope.tasks.length === 0) {
          panel.appendChild(C().emptyState("数据库存在，但没有任何任务"));
        } else {
          envelope.tasks.forEach(function (task) {
            const card = C().el("div", "card");
            card.appendChild(C().el("div", "card-title", task.title));
            card.appendChild(C().field("task_id", task.task_id));
            const badgeRow = C().el("div", "row");
            badgeRow.appendChild(C().statusBadge(task.status));
            badgeRow.appendChild(C().el("span", "muted", "review_verdict="
              + (task.review_verdict === null || task.review_verdict === undefined
                 ? "—" : task.review_verdict)));
            card.appendChild(badgeRow);
            card.appendChild(C().field("updated_at", task.updated_at));
            card.appendChild(C().el("div", "muted",
              "状态取自后端记录；本界面不判定成功"));
            const actions = C().el("div", "row");
            [["task", "查看详情"], ["delivery", "审查与交付"]].forEach(function (pair) {
              const button = C().el("button", "task-action", pair[1]);
              button.dataset.taskId = task.task_id;
              button.dataset.view = pair[0];
              actions.appendChild(button);
            });
            card.appendChild(actions);
            panel.appendChild(card);
          });
        }
        const child = [panel];
        child.push(draftPanel(gen));
        render([heading("工作台",
          "只读任务索引；草案是明确的输入，不会自动执行")].concat(child), gen);
      });
    });
  }

  function draftPanel(panelGeneration) {
    const panel = C().el("section", "panel");
    panel.appendChild(C().el("h3", null, "目标草案"));
    panel.appendChild(C().note("保存为显式标记的草案（draft=true、execution=not_triggered）；"
      + "本批不派发、不调用模型、不启动工具。"));

    const title = document.createElement("input");
    title.placeholder = "标题";
    const goal = document.createElement("textarea");
    goal.placeholder = "目标描述";
    goal.rows = 3;
    const button = C().el("button", null, "保存草案");
    const result = C().el("div", "draft-result");

    panel.appendChild(title);
    panel.appendChild(goal);
    panel.appendChild(button);
    panel.appendChild(result);

    const list = C().el("div", "draft-list");
    panel.appendChild(list);

    /* The list generation belongs to THIS panel, not to the module: a stale
     * panel's late reply must never invalidate the panel the user is looking
     * at. A save that resolves after the user navigated away therefore leaves
     * the current panel's in-flight list request untouched. */
    let panelDraftsGeneration = 0;

    function refreshDrafts() {
      if (panelGeneration !== renderGeneration) {
        return;                        // this panel is no longer on screen
      }
      panelDraftsGeneration += 1;
      const myGeneration = panelDraftsGeneration;
      list.textContent = "";
      JH.api.listDrafts().then(function (envelope) {
        // An older reply for this panel must not clear or append to its list,
        // and a replaced panel must not paint at all.
        if (myGeneration !== panelDraftsGeneration) { return; }
        if (panelGeneration !== renderGeneration) { return; }
        list.textContent = "";
        if (!envelope || envelope.ok !== true) {
          list.appendChild(C().errorState((envelope && envelope.error) || {}));
          return;
        }
        if (!envelope.drafts || envelope.drafts.length === 0) {
          list.appendChild(C().emptyState("还没有草案"));
          return;
        }
        envelope.drafts.forEach(function (draft) {
          const card = C().el("div", "card");
          card.appendChild(C().el("div", "card-title", draft.title));
          card.appendChild(C().field("draft_id", draft.draft_id));
          card.appendChild(C().field("execution", draft.execution));
          card.appendChild(C().field("created_at", draft.created_at));
          list.appendChild(card);
        });
      });
    }

    button.addEventListener("click", function () {
      result.textContent = "";
      JH.api.saveGoalDraft({ title: title.value, goal: goal.value }).then(function (envelope) {
        if (panelGeneration !== renderGeneration) { return; }   // panel replaced
        if (envelope && envelope.ok === true) {
          // One line of feedback; the stored facts are shown by the list card
          // below, so the same draft is not printed twice.
          result.appendChild(C().el("div", "notice notice-ok",
            "已保存草案 " + envelope.draft.draft_id + "（未触发任何执行）"));
          refreshDrafts();
        } else {
          result.appendChild(C().errorState((envelope && envelope.error) || {}));
        }
      });
    });

    refreshDrafts();
    return panel;
  }

  /* 任务详情。 */
  function taskDetail(taskId) {
    const gen = beginRender();
    const requested = taskId;
    render([heading("任务详情", taskId ? ("task_id " + taskId) : "请从工作台选择任务")], gen);
    if (!taskId) {
      render([heading("任务详情", "请从工作台选择任务"),
              C().emptyState("未选择任务")], gen);
      return;
    }
    JH.api.taskDetail(taskId).then(function (envelope) {
      // Ignore a reply for a task the user has already navigated away from.
      if (!isCurrent(gen) || currentTaskId !== requested) { return; }
      envelopeOrError("任务详情", envelope, function () {
        const task = envelope.task_result;
        const panel = C().el("section", "panel");
        panel.appendChild(C().el("h3", null, task.title || task.task_id));
        const row = C().el("div", "row");
        row.appendChild(C().statusBadge(task.status));
        panel.appendChild(row);
        panel.appendChild(C().note("status/is_terminal/审查结论均取自后端记录："
          + "本界面只显示，不判定成功。"));
        ["review_verdict", "assigned_worker", "assigned_reviewer", "error_info",
         "updated_at"].forEach(function (key) { panel.appendChild(C().field(key, task[key])); });

        const runtime = task.runtime_usage || {};
        const rt = C().el("div", "subpanel");
        rt.appendChild(C().el("h4", null, "运行用量"));
        Object.keys(runtime).forEach(function (key) {
          rt.appendChild(C().field(key, runtime[key]));
        });
        panel.appendChild(rt);

        const art = C().el("div", "subpanel");
        art.appendChild(C().el("h4", null, "产物版本"));
        if (!envelope.artifact_versions || envelope.artifact_versions.length === 0) {
          art.appendChild(C().emptyState("没有产物版本"));
        } else {
          envelope.artifact_versions.forEach(function (version) {
            art.appendChild(C().field("v" + version.version,
              (version.artifact_type || "?") + " " + (version.sha256 || "")));
          });
        }
        panel.appendChild(art);

        panel.appendChild(C().field("events_count", envelope.events_count));
        if (task.blockers && task.blockers.length) {
          panel.appendChild(C().el("h4", null, "阻塞"));
          task.blockers.forEach(function (b) { panel.appendChild(C().el("div", "muted", b)); });
        }
        render([heading("任务详情", "task_id " + taskId), panel], gen);
      });
    });
  }

  /* Agent / 会话：静态登记（模拟）+ 真实会话运行记录（历史，只读）。 */
  function sessions() {
    const gen = beginRender();
    render([heading("Agent / 会话", "静态登记为模拟；运行记录为历史，不代表实时在线"),
            C().loadingState("正在读取会话登记…")], gen);
    JH.api.listSessions().then(function (envelope) {
      if (!isCurrent(gen)) { return; }
      envelopeOrError("Agent / 会话", envelope, function () {
        const panel = C().el("section", "panel panel-simulated");
        const head = C().el("div", "section-head");
        head.appendChild(C().kindTag("sim", "◇ 模拟登记"));
        head.appendChild(C().el("h3", null, "静态会话登记（非实时）"));
        panel.appendChild(head);
        panel.appendChild(C().note("传输来源：" + envelope.transport + "；" + envelope.note));
        const grid = C().el("div", "sim-grid");
        envelope.sessions.forEach(function (session) {
          const card = C().el("div", "card card-simulated");
          const title = C().el("div", "card-title");
          title.appendChild(C().el("span", null, session.app_kind + " · "));
          title.appendChild(C().el("span", "mono", session.session_id));
          card.appendChild(title);
          card.appendChild(C().field("instance_id", session.instance_id, { mono: true }));
          card.appendChild(C().field("role", session.role));
          card.appendChild(C().field("model_id", session.model_id));
          card.appendChild(C().connectionNotice(session));
          card.appendChild(C().el("h4", null, "接口证据（来自 JH-018 实测）"));
          card.appendChild(C().evidenceList(session.evidence));
          grid.appendChild(card);
        });
        panel.appendChild(grid);
        const top = heading("Agent / 会话", "静态登记为模拟；运行记录为历史，不代表实时在线");
        top.appendChild(C().legend());
        render([top, panel, runHistoryPanel(gen)], gen);
      });
    });
  }

  /* 运行记录面板：来源状态、历史列表、以及按需展开的详情（不触发任何执行）。 */
  function runHistoryPanel(panelGeneration) {
    const panel = C().el("section", "panel panel-history");
    const head = C().el("div", "section-head");
    head.appendChild(C().kindTag("hist", "● 历史运行"));
    head.appendChild(C().el("h3", null, "运行记录（历史，只读）"));
    panel.appendChild(head);
    panel.appendChild(C().note("登记来源由服务端配置；本界面只读，不派发、不收集、"
      + "不启动 app-server、不发送任何会话消息。历史记录不代表实时在线，"
      + "declared_complete 不代表任务验收成功。"));
    const list = C().el("div", "run-list");
    const detail = C().el("div", "run-detail");
    const layout = C().el("div", "run-layout");      // presentation only: list | detail
    layout.appendChild(list);
    layout.appendChild(detail);
    panel.appendChild(layout);

    /* Detail-request generation and the run id the current pane is bound to;
     * both belong to THIS panel, so a replaced panel's reply is inert. */
    let panelDetailGeneration = 0;
    let currentDetailRunId = null;

    /* Hark-派单-05: current-selection cue and single-column detail placement.
     * Both are driven ONLY by an explicit click in this panel: no reply (late,
     * failed, empty or carrying another run id) ever changes the selection or
     * scrolls. The cue is carried by aria-pressed and data-selected, never by
     * colour alone. */
    let runButtons = [];                 // [{ runId, button, card }] of the current list

    function markSelected(runId) {
      runButtons.forEach(function (entry) {
        const on = entry.runId === runId;
        setPressed(entry.button, on);
        entry.card.dataset.selected = on ? "true" : "false";
      });
    }

    function revealDetail() {
      // Same breakpoint as the single-column rule in styles.css; the two-column
      // layout keeps the sticky pane in place and never scrolls.
      if (!isSingleColumn()) { return; }
      if (typeof detail.scrollIntoView === "function") {
        detail.scrollIntoView({ block: "start", inline: "nearest" });
      }
    }

    function renderRunCard(run) {
      const card = C().el("div", "card run-card");
      const title = C().el("div", "card-title");
      title.appendChild(C().el("span", null, run.app_kind + " · "));
      title.appendChild(C().el("span", "mono", run.run_id));
      card.appendChild(title);
      const row = C().el("div", "row");
      row.appendChild(C().statusBadge(run.record_state));
      row.appendChild(C().el("span", "muted", "native_state="
        + (run.native_state === null || run.native_state === undefined ? "—" : run.native_state)));
      card.appendChild(row);
      const grid = C().el("div", "field-grid");
      grid.appendChild(C().field("instance_id", run.instance_id, { mono: true }));
      grid.appendChild(C().field("role", run.role));
      grid.appendChild(C().field("requested_model", run.requested_model));
      grid.appendChild(C().field("native_model", run.native_model));
      grid.appendChild(C().field("last_recorded_at", run.last_recorded_at));
      const usage = run.usage || {};
      const turn = (usage.turn || {}).totalTokens;
      const total = (usage.thread_total || {}).totalTokens;
      grid.appendChild(C().field("本轮用量(turn)", turn === undefined ? "—" : turn));
      grid.appendChild(C().field("线程累计(thread_total)", total === undefined ? "—" : total));
      card.appendChild(grid);
      card.appendChild(C().field("native_session_id", run.native_session_id, { mono: true }));
      card.appendChild(C().field("native_turns", (run.native_turns || []).join(", "), { mono: true }));
      card.appendChild(C().evidenceNotice(run.evidence || {}));
      const declared = C().el("div", "declared");
      declared.appendChild(C().kindTag("claim", "✎ 执行方声明"));
      declared.appendChild(C().el("span", null, "declared_status="
        + (run.declared_status === null ? "—" : run.declared_status)
        + "（执行方声明，非任务验收成功）"));
      card.appendChild(declared);
      const foot = C().el("div", "run-foot");
      const button = C().el("button", "run-action", "查看记录详情");
      button.dataset.runId = run.run_id;
      setPressed(button, false);
      card.dataset.selected = "false";
      runButtons.push({ runId: run.run_id, button: button, card: card });
      foot.appendChild(button);
      card.appendChild(foot);
      return card;
    }

    function refresh() {
      if (panelGeneration !== renderGeneration) { return; }
      list.textContent = "";
      detail.textContent = "";
      runButtons = [];
      JH.api.listAgentRuns().then(function (envelope) {
        if (panelGeneration !== renderGeneration) { return; }
        list.textContent = "";
        runButtons = [];
        if (!envelope || envelope.ok !== true) {
          list.appendChild(C().errorState((envelope && envelope.error) || {}));
          return;
        }
        list.appendChild(C().runSourceNotice(envelope));
        if (envelope.state !== "ok") {
          list.appendChild(C().emptyState(envelope.message || "没有可显示的运行记录"));
          return;
        }
        envelope.runs.forEach(function (run) { list.appendChild(renderRunCard(run)); });
      });
    }

    /* Selecting a run uses an explicit button carrying the run id, not scraped
     * card text — the id is looked up in the history server-side. */
    function loadDetail(runId) {
      if (panelGeneration !== renderGeneration) { return; }
      /* The detail pane is bound to four things: this view/panel, the latest
       * detail request, the run id that was requested, and the run id the
       * reply carries. A late success, a late error or a late empty reply from
       * an older request must not clear or overwrite what is on screen. */
      panelDetailGeneration += 1;
      const myGeneration = panelDetailGeneration;
      const requestedRunId = runId;
      currentDetailRunId = requestedRunId;
      detail.textContent = "";
      detail.appendChild(C().loadingState("正在读取运行记录…"));
      JH.api.agentRunDetail(requestedRunId).then(function (envelope) {
        if (myGeneration !== panelDetailGeneration) { return; }   // a newer detail request exists
        if (panelGeneration !== renderGeneration) { return; }     // this panel was replaced
        if (currentDetailRunId !== requestedRunId) { return; }
        if (envelope && envelope.ok === true && envelope.run
            && envelope.run.run_id !== requestedRunId) {
          // Never paint a run the user did not ask for; do not echo its id.
          detail.textContent = "";
          detail.appendChild(C().errorState({ code: "unexpected_run_id",
            message: "响应与当前请求的 run 不一致，已丢弃" }));
          return;
        }
        detail.textContent = "";
        if (!envelope || envelope.ok !== true) {
          detail.appendChild(C().errorState((envelope && envelope.error) || {}));
          return;
        }
        if (!envelope.run) {
          detail.appendChild(C().emptyState(envelope.message || "该运行记录不可用"));
          return;
        }
        detail.appendChild(renderRunDetail(envelope.run));
      });
    }

    list.addEventListener("click", function (event) {
      const button = event.target.closest ? event.target.closest("button.run-action") : null;
      if (!button) { return; }
      markSelected(button.dataset.runId);
      loadDetail(button.dataset.runId);
      revealDetail();
    });

    refresh();
    return panel;
  }

  /* Single-column breakpoint shared with the `@media (max-width: 1100px)` rule
   * in styles.css. Without matchMedia the layout is treated as two-column, so
   * nothing scrolls. */
  const SINGLE_COLUMN_QUERY = "(max-width: 1100px)";

  function isSingleColumn() {
    return typeof window.matchMedia === "function"
      && window.matchMedia(SINGLE_COLUMN_QUERY).matches === true;
  }

  function setPressed(button, on) {
    const value = on ? "true" : "false";
    if (typeof button.setAttribute === "function") {
      button.setAttribute("aria-pressed", value);
    } else {
      button.ariaPressed = value;
    }
  }

  /* Detail layout groups the same recorded fields by meaning; no field is
   * added, dropped or derived. Long ids use the monospace wrap-anywhere face. */
  const DETAIL_GROUPS = [
    ["运行身份", ["app_kind", "instance_id", "session_id", "role"]],
    ["模型", ["requested_model", "native_model"]],
    ["请求与幂等", ["logical_task_id", "attempt", "request_fingerprint", "idempotency_key"]],
    ["原生会话与状态", ["native_session_id", "native_state", "record_state", "declared_status"]],
    ["时间", ["first_recorded_at", "last_recorded_at"]],
  ];
  const MONO_KEYS = { instance_id: 1, session_id: 1, request_fingerprint: 1,
                      idempotency_key: 1, native_session_id: 1, logical_task_id: 1 };

  function renderRunDetail(run) {
    const box = C().el("div", "subpanel");
    const title = C().el("h4", "detail-title");
    title.appendChild(C().kindTag("hist", "● 历史运行"));
    title.appendChild(C().el("span", null, "运行详情："));
    title.appendChild(C().el("span", "mono", run.run_id));
    box.appendChild(title);
    DETAIL_GROUPS.forEach(function (group) {
      const section = C().el("div", "detail-group");
      section.appendChild(C().el("h4", null, group[0]));
      const grid = C().el("div", "field-grid");
      group[1].forEach(function (key) {
        grid.appendChild(C().field(key, run[key], { mono: !!MONO_KEYS[key] }));
      });
      section.appendChild(grid);
      if (group[0] === "原生会话与状态") {
        section.appendChild(C().field("native_turns", (run.native_turns || []).join(", "),
                                      { mono: true }));
        section.appendChild(C().field("events", (run.events || []).join(" → ")));
      }
      box.appendChild(section);
    });

    const usage = run.usage || {};
    const usageBox = C().el("div", "subpanel");
    usageBox.appendChild(C().el("h4", null, "用量（本轮 / 线程累计）"));
    usageBox.appendChild(C().field("turn", JSON.stringify(usage.turn), { mono: true }));
    usageBox.appendChild(C().field("thread_total", JSON.stringify(usage.thread_total), { mono: true }));
    usageBox.appendChild(C().field("model_context_window", usage.model_context_window));
    usageBox.appendChild(C().field("source", JSON.stringify(usage.source), { mono: true }));
    box.appendChild(usageBox);

    box.appendChild(C().el("h4", null, "证据"));
    box.appendChild(C().evidenceNotice(run.evidence || {}));
    box.appendChild(C().bulletList("未验证事项", run.unverified));
    if (run.dispatch_uncertain_reason) {
      box.appendChild(C().bulletList("派发不确定原因", [run.dispatch_uncertain_reason]));
    }
    const link = run.link || {};
    box.appendChild(C().el("h4", null, "关联任务"));
    box.appendChild(C().field("linked", link.linked));
    box.appendChild(C().el("div", "muted", link.note || "未提供关联说明"));
    const claim = C().el("div", "declared");
    claim.appendChild(C().kindTag("claim", "✎ 执行方声明"));
    claim.appendChild(C().el("span", "muted",
      "历史记录不代表实时在线；declared_complete 不代表任务验收成功。"));
    box.appendChild(claim);
    return box;
  }

  /* 会话试运行（Hark-派单-06）：固定标识往返，默认关闭。
   * 打开与刷新只读状态；只有勾选确认并点击按钮才会先 prepare（签发一次性编号）
   * 再 start（只带 probe_id 与 confirm）。按钮在本面板内只受理一次点击，
   * 服务端另行保证至多派发一次。结果按文本显示，往返完成不等于任务验收成功。 */
  const PROBE_POLL_MS = 2000;
  const PROBE_OUTCOME = {
    round_trip_complete: ["✓", "试运行往返完成：固定标识已回显（不等于任务验收成功）"],
    round_trip_not_proven: ["✕", "往返未证实：回显、证据或工具检查未通过"],
    dispatch_failed: ["✕", "派发失败（已登记，不重试）"],
    unknown_manual: ["?", "结果未知：待人工处理，不会自动重发"],
    in_progress: ["…", "进行中：已受理，正在派发或收集"],
  };

  function probe() {
    const gen = beginRender();
    const title = "会话试运行";
    const sub = "固定标识往返（新建隔离只读 Codex 会话）；默认关闭，逐次确认，至多派发一次";
    render([heading(title, sub), C().loadingState("正在读取试运行状态…")], gen);
    let busy = false;                 // this panel accepts ONE confirmed click
    let polls = 0;

    function load() {
      JH.api.sessionProbeStatus().then(function (envelope) {
        if (!isCurrent(gen)) { return; }
        if (!envelope || envelope.ok !== true) {
          render([heading(title, sub), C().errorState((envelope && envelope.error) || {})], gen);
          return;
        }
        busy = false;
        render([heading(title, sub), probePanel(envelope)], gen);
        if (envelope.inflight) {
          const seconds = ((envelope.plan || {}).collect_timeout_seconds || 240) + 120;
          if (polls < Math.ceil(seconds * 1000 / PROBE_POLL_MS)) {
            polls += 1;
            setTimeout(function () { if (isCurrent(gen)) { load(); } }, PROBE_POLL_MS);
          }
        } else {
          polls = 0;
        }
      });
    }

    function probePanel(envelope) {
      const panel = C().el("section", "panel panel-probe");
      const source = C().el("div", envelope.adapter_source === "real_codex_adapter"
        ? "notice probe-source" : "notice notice-warn probe-source");
      source.dataset.source = String(envelope.adapter_source);
      source.appendChild(C().el("strong", null, envelope.adapter_source === "real_codex_adapter"
        ? "适配器：真实 Codex 会话适配器（会调用真实模型）"
        : "适配器：离线/合成注入（非真实模型结果）"));
      panel.appendChild(source);

      const avail = C().el("div", envelope.available ? "notice notice-ok" : "notice notice-warn");
      avail.dataset.state = envelope.available ? "available" : "blocked";
      avail.appendChild(C().el("strong", null, envelope.available
        ? "可以试运行：确认后发起一次真实会话" : "当前不能试运行"));
      (envelope.blockers || []).forEach(function (item) {
        avail.appendChild(C().el("div", "muted", item.code + "：" + item.message));
      });
      panel.appendChild(avail);

      const plan = envelope.plan || {};
      const planBox = C().el("div", "subpanel");
      planBox.appendChild(C().el("h4", null, "将调用什么（全部由服务端决定）"));
      [["app_kind", plan.app_kind], ["instance_id", plan.instance_id], ["session", plan.session],
       ["role", plan.role], ["model", plan.model], ["sandbox", plan.sandbox],
       ["network", plan.network], ["approval_policy", plan.approval_policy],
       ["指令模板", plan.instruction_template], ["隔离工作区", plan.workspace_root],
       ["登记", plan.registry], ["证据目录", plan.evidence_root], ["预算", plan.budget],
       ["收集等待上限(秒)", plan.collect_timeout_seconds]].forEach(function (pair) {
        planBox.appendChild(C().field(pair[0], pair[1], { mono: true }));
      });
      panel.appendChild(planBox);
      panel.appendChild(C().note("真实调用会使用模型额度；适配器不支持硬 token 预算。"
        + "试运行不发送任何既有、架构师或审查会话，不接受自由任务或路径。"));

      const confirmRow = C().el("label", "probe-confirm");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "probe-confirm-box";
      confirmRow.appendChild(checkbox);
      confirmRow.appendChild(C().el("span", null,
        "我确认发起一次真实 Codex 会话试运行，并知晓会使用模型额度"));
      panel.appendChild(confirmRow);

      const actions = C().el("div", "row probe-actions");
      const start = C().el("button", "probe-start", "确认并发起一次试运行");
      const refreshButton = C().el("button", "probe-refresh", "刷新状态（只读）");
      const canStart = envelope.available === true && !envelope.inflight;
      start.disabled = true;
      checkbox.disabled = !canStart;
      checkbox.addEventListener("change", function () {
        start.disabled = !(canStart && checkbox.checked === true && !busy);
      });
      const message = C().el("div", "probe-message");
      start.addEventListener("click", function () {
        if (busy || start.disabled || checkbox.checked !== true || !canStart) { return; }
        busy = true;
        start.disabled = true;
        checkbox.disabled = true;
        message.textContent = "正在签发一次性试运行编号…";
        JH.api.prepareSessionProbe().then(function (prepared) {
          if (!isCurrent(gen)) { return; }
          if (!prepared || prepared.ok !== true) {
            message.textContent = "";
            message.appendChild(C().errorState((prepared && prepared.error) || {}));
            busy = false;
            return;
          }
          message.textContent = "已确认，正在受理试运行 " + prepared.token + " …";
          JH.api.startSessionProbe(prepared.probe_id).then(function (started) {
            if (!isCurrent(gen)) { return; }
            if (!started || started.ok !== true) {
              message.textContent = "";
              message.appendChild(C().errorState((started && started.error) || {}));
            }
            load();                                  // the registry decides what happened
          });
        });
      });
      refreshButton.addEventListener("click", function () {
        if (busy) { return; }
        load();
      });
      actions.appendChild(start);
      actions.appendChild(refreshButton);
      panel.appendChild(actions);
      panel.appendChild(message);

      if (envelope.inflight) {
        panel.appendChild(C().note("试运行进行中（" + envelope.inflight.phase + "）；"
          + "本页仅只读刷新状态，不会重发。"));
      }
      if (envelope.last_error) {
        panel.appendChild(C().bulletList("最近一次执行异常（未重试）",
          [envelope.last_error.type + "：" + envelope.last_error.message]));
      }
      panel.appendChild(probeResult(envelope.latest, envelope.registry || {}));
      return panel;
    }

    load();
  }

  function probeResult(latest, registry) {
    const box = C().el("div", "subpanel probe-result");
    box.appendChild(C().el("h4", null, "最近一次试运行"));
    box.appendChild(C().field("登记状态", registry.state));
    if (!latest) {
      box.appendChild(C().emptyState(registry.message || "尚无试运行记录"));
      return box;
    }
    const outcome = PROBE_OUTCOME[latest.outcome] || ["?", "未知结果：" + latest.outcome];
    const head = C().el("div", latest.outcome === "round_trip_complete" ? "notice notice-ok" : "notice notice-warn");
    head.dataset.outcome = String(latest.outcome);
    const strong = C().el("strong");
    strong.appendChild(C().el("span", "state-icon", outcome[0]));
    strong.appendChild(C().el("span", null, outcome[1]));
    head.appendChild(strong);
    box.appendChild(head);
    [["run_id", latest.run_id], ["固定标识", latest.token], ["record_state", latest.record_state],
     ["native_state", latest.native_state], ["requested_model", latest.requested_model],
     ["native_model", latest.native_model], ["thread", latest.native_session_id],
     ["turn", latest.native_turn_id], ["回显核对", latest.echo],
     ["指令绑定本次编号", latest.instruction_bound],
     ["last_recorded_at", latest.last_recorded_at]].forEach(function (pair) {
      box.appendChild(C().field(pair[0], pair[1], { mono: true }));
    });
    box.appendChild(C().el("h4", null, "本次回复（按文本显示）"));
    if (!latest.reply_texts || latest.reply_texts.length === 0) {
      box.appendChild(C().el("div", "muted", "没有绑定本次 thread/turn 的回复"));
    } else {
      latest.reply_texts.forEach(function (text) {
        box.appendChild(C().el("pre", "probe-reply", text));
      });
    }
    box.appendChild(C().el("h4", null, "用量（仅采用绑定本次 thread/turn 的记录）"));
    if (!latest.usage) {
      box.appendChild(C().field("usage", "未知（" + latest.usage_state + "）；不按 0 计"));
    } else {
      box.appendChild(C().field("turn", JSON.stringify(latest.usage.turn), { mono: true }));
      box.appendChild(C().field("thread_total", JSON.stringify(latest.usage.thread_total), { mono: true }));
      box.appendChild(C().field("source", JSON.stringify(latest.usage.source), { mono: true }));
    }
    box.appendChild(C().el("h4", null, "原始证据"));
    box.appendChild(C().evidenceNotice(latest.evidence || {}));
    box.appendChild(C().field("path", (latest.evidence || {}).path, { mono: true }));
    box.appendChild(C().field("工具调用", latest.tool_items === null || latest.tool_items === undefined
      ? "未核验" : (latest.tool_items.length ? latest.tool_items.join(", ") : "无")));
    // Hark-派单-09: a listing speaks for the native workspace only when the
    // server verified plan == dispatch == native cwd; otherwise "未核实", never "无".
    const ws = latest.workspace || null;
    const wsVerified = !!(ws && ws.verified === true && ws.state === "verified" && Array.isArray(latest.workspace_files));
    box.appendChild(C().field("工作区核实", ws
      ? (wsVerified ? "已核实（计划、派发与原生目录一致）" : "未核实（" + ws.state + "）：" + (ws.reason || ""))
      : "未核实（无工作区观测）"));
    if (ws && ws.planned) box.appendChild(C().field("计划工作区", ws.planned, { mono: true }));
    box.appendChild(C().field("隔离工作区文件", !wsVerified
      ? "未核实：不对实际工作区内容作断言"
      : (latest.workspace_files.length ? latest.workspace_files.join(", ") : "无（已核实为空）")));
    box.appendChild(C().bulletList("未验证事项", latest.unverified));
    const claim = C().el("div", "declared");
    claim.appendChild(C().kindTag("claim", "✎ 执行方声明"));
    claim.appendChild(C().el("span", "muted", latest.note || ""));
    box.appendChild(claim);
    return box;
  }

  /* 审查与交付。 */
  function delivery(taskId) {
    const gen = beginRender();
    const requested = taskId;
    render([heading("审查与交付", taskId ? ("task_id " + taskId) : "请从工作台选择任务")], gen);
    if (!taskId) {
      render([heading("审查与交付", "请从工作台选择任务"),
              C().emptyState("未选择任务")], gen);
      return;
    }
    JH.api.reviewDelivery(taskId).then(function (envelope) {
      if (!isCurrent(gen) || currentTaskId !== requested) { return; }
      envelopeOrError("审查与交付", envelope, function () {
        const panel = C().el("section", "panel");
        const row = C().el("div", "row");
        row.appendChild(C().statusBadge(envelope.task_status));
        row.appendChild(C().el("span", "muted", "review_verdict="
          + (envelope.review_verdict === null ? "—" : envelope.review_verdict)));
        panel.appendChild(row);
        panel.appendChild(C().note("任务状态与审查结论来自记录事实；"
          + "交付是否完成以 delivery_scope 为准，界面不推断。"));

        const scope = envelope.delivery_scope || {};
        const scopeBox = C().el("div", "subpanel");
        scopeBox.appendChild(C().el("h4", null, "交付范围"));
        ["generated_artifact", "delivery_state", "delivery_id", "target_applied",
         "evidence_kind", "note"].forEach(function (key) {
          scopeBox.appendChild(C().field(key, scope[key]));
        });
        panel.appendChild(scopeBox);

        const acc = C().el("div", "subpanel");
        acc.appendChild(C().el("h4", null, "验收结果"));
        if (!envelope.acceptance_results || envelope.acceptance_results.length === 0) {
          acc.appendChild(C().emptyState("没有验收结果记录"));
        } else {
          envelope.acceptance_results.forEach(function (item) {
            acc.appendChild(C().field(item.criterion || item.id || "criterion",
              item.passed === undefined ? item.result : item.passed));
          });
        }
        panel.appendChild(acc);
        panel.appendChild(C().field("review_bindings_count", envelope.review_bindings_count));
        render([heading("审查与交付", "task_id " + taskId), panel], gen);
      });
    });
  }

  function start() {
    const tabs = document.getElementById("tabs");

    function show(view) {
      Array.prototype.forEach.call(tabs.querySelectorAll(".tab"), function (tab) {
        tab.classList.toggle("active", tab.dataset.view === view);
      });
      if (view === "workbench") workbench();
      else if (view === "sessions") sessions();
      else if (view === "task") taskDetail(currentTaskId);
      else if (view === "delivery") delivery(currentTaskId);
      else if (view === "probe") probe();
    }

    tabs.addEventListener("click", function (event) {
      if (event.target.dataset && event.target.dataset.view) {
        show(event.target.dataset.view);
      }
    });

    JH.api.capabilities().then(function (envelope) {
      const summary = document.getElementById("capability-summary");
      summary.textContent = "";
      if (!envelope || envelope.ok !== true) {
        summary.appendChild(C().el("span", "muted", "能力信息不可用"));
        return;
      }
      summary.appendChild(C().el("span", null, "不支持："));
      if (!envelope.unsupported || envelope.unsupported.length === 0) {
        summary.appendChild(C().el("span", "muted", "无"));
      } else {
        envelope.unsupported.forEach(function (name) {
          summary.appendChild(C().el("span", "badge badge-unsupported", name));
        });
      }
      summary.appendChild(C().el("span", "muted", " · " + envelope.note));
      const probeCap = (envelope.capabilities || {}).session_probe;
      if (probeCap && probeCap.supported === true) {
        // The static badge must not keep saying "never executes" once the
        // server has enabled the confirmed probe.
        document.getElementById("scope-badge").textContent =
          "只读 + 草案 · 会话试运行已启用（逐次确认后调用真实模型）";
      }
    });

    /* 任务卡片上的显式按钮负责选中任务并切换视图，不解析卡片正文。 */
    document.addEventListener("click", function (event) {
      const button = event.target.closest ? event.target.closest("button.task-action") : null;
      if (!button) return;
      currentTaskId = button.dataset.taskId;
      show(button.dataset.view);
    });

    show("workbench");
  }

  return { start: start, workbench: workbench, taskDetail: taskDetail,
           sessions: sessions, delivery: delivery, probe: probe };
})();
