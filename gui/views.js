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

  /* Agent / 会话：全部为静态登记，必须显式显示未连接。 */
  function sessions() {
    const gen = beginRender();
    render([heading("Agent / 会话", "静态登记视图：未接入适配器，不存在真实连接"),
            C().loadingState("正在读取会话登记…")], gen);
    JH.api.listSessions().then(function (envelope) {
      if (!isCurrent(gen)) { return; }
      envelopeOrError("Agent / 会话", envelope, function () {
        const panel = C().el("section", "panel");
        panel.appendChild(C().note("传输来源：" + envelope.transport + "；" + envelope.note));
        envelope.sessions.forEach(function (session) {
          const card = C().el("div", "card");
          card.appendChild(C().el("div", "card-title", session.app_kind + " · " + session.session_id));
          card.appendChild(C().field("instance_id", session.instance_id));
          card.appendChild(C().field("role", session.role));
          card.appendChild(C().field("model_id", session.model_id));
          card.appendChild(C().connectionNotice(session));
          card.appendChild(C().el("h4", null, "接口证据（来自 JH-018 实测）"));
          card.appendChild(C().evidenceList(session.evidence));
          panel.appendChild(card);
        });
        render([heading("Agent / 会话", "静态登记视图：未接入适配器，不存在真实连接"), panel], gen);
      });
    });
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
           sessions: sessions, delivery: delivery };
})();
