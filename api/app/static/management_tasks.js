(function () {
  const EMPTY = "\u2014";
  const STATUS_LABELS = {
    new: "Новая",
    pending: "В работе",
    unassigned: "Нераспределена",
    closed: "Закрыта",
  };
  const state = {
    tab: "active",
    scope: "all",
    appTimezone: "UTC",
    options: null,
    overview: { active: [], scheduled: [], completed: [], recurring: [] },
    editingDefinitionId: "",
  };

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function userLabel(user) {
    const text = String(user?.full_name || user?.login || user?.id || "").trim();
    return text || EMPTY;
  }

  function pad(value) {
    return String(value).padStart(2, "0");
  }

  function hasExplicitTimezone(value) {
    return /(?:z|[+-]\d{2}:?\d{2})$/i.test(String(value || "").trim());
  }

  function parseApiDateTime(value) {
    if (!value) return null;
    const raw = String(value).trim();
    if (!raw) return null;
    const date = new Date(hasExplicitTimezone(raw) ? raw : `${raw}Z`);
    if (Number.isNaN(date.getTime())) return null;
    return date;
  }

  function timezoneFields(value, appTimezone) {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: appTimezone,
      hourCycle: "h23",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }).formatToParts(value);
    return Object.fromEntries(parts.map((part) => [part.type, part.value]));
  }

  function timezoneOffsetMinutes(value, appTimezone) {
    const fields = timezoneFields(value, appTimezone);
    const asUtc = Date.UTC(
      Number(fields.year),
      Number(fields.month) - 1,
      Number(fields.day),
      Number(fields.hour),
      Number(fields.minute),
      Number(fields.second),
    );
    return Math.round((asUtc - value.getTime()) / 60000);
  }

  function localWallTimeToUtcIso(value, appTimezone) {
    const match = String(value || "").match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/);
    if (!match) return value || null;
    const [, year, month, day, hour, minute] = match.map(Number);
    const wallTimeUtc = Date.UTC(year, month - 1, day, hour, minute, 0);
    let offset = timezoneOffsetMinutes(new Date(wallTimeUtc), appTimezone);
    offset = timezoneOffsetMinutes(new Date(wallTimeUtc - offset * 60000), appTimezone);
    return new Date(wallTimeUtc - offset * 60000).toISOString();
  }

  function formatDateTime(value) {
    const date = parseApiDateTime(value);
    if (!date) return EMPTY;
    return new Intl.DateTimeFormat("ru-RU", {
      timeZone: state.appTimezone || "UTC",
      dateStyle: "short",
      timeStyle: "short",
    }).format(date);
  }

  function localInputValue(value) {
    const date = parseApiDateTime(value);
    if (!date) return "";
    const fields = timezoneFields(date, state.appTimezone || "UTC");
    return `${fields.year}-${fields.month}-${fields.day}T${fields.hour}:${fields.minute}`;
  }

  function fallbackFutureLocalInput(minutes = 30) {
    const date = new Date(Date.now() + minutes * 60000);
    const fields = timezoneFields(date, state.appTimezone || "UTC");
    return `${fields.year}-${fields.month}-${fields.day}T${fields.hour}:${fields.minute}`;
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: {
        accept: "application/json",
        ...(options.body ? { "content-type": "application/json" } : {}),
        ...(options.headers || {}),
      },
      credentials: "include",
      cache: "no-store",
    });
    const raw = await response.text();
    let payload = null;
    if (raw) {
      try {
        payload = JSON.parse(raw);
      } catch {
        payload = null;
      }
    }
    if (!response.ok) {
      const message = payload?.detail?.error?.message || raw || "Ошибка запроса";
      const error = new Error(message);
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function els(root) {
    return {
      status: root.querySelector("#internalTasksStatus"),
      validation: root.querySelector("#internalTasksValidation"),
      createBtn: root.querySelector("#internalTaskCreateBtn"),
      tabs: [...root.querySelectorAll("[data-internal-task-tab]")],
      scopes: [...root.querySelectorAll("[data-internal-task-scope]")],
      panels: [...root.querySelectorAll("[data-internal-task-panel]")],
      activeBody: root.querySelector("#internalTasksActiveBody"),
      activeEmpty: root.querySelector("#internalTasksActiveEmpty"),
      scheduledBody: root.querySelector("#internalTasksScheduledBody"),
      scheduledEmpty: root.querySelector("#internalTasksScheduledEmpty"),
      completedBody: root.querySelector("#internalTasksCompletedBody"),
      completedEmpty: root.querySelector("#internalTasksCompletedEmpty"),
      modal: root.querySelector("#internalTaskModal"),
      modalTitle: root.querySelector("#internalTaskModalTitle"),
      closeModalBtn: root.querySelector("#internalTaskCloseModalBtn"),
      cancelBtn: root.querySelector("#internalTaskCancelBtn"),
      saveBtn: root.querySelector("#internalTaskSaveBtn"),
      form: root.querySelector("#internalTaskForm"),
      title: root.querySelector("#internalTaskTitle"),
      description: root.querySelector("#internalTaskDescription"),
      assignmentMode: root.querySelector("#internalTaskAssignmentMode"),
      assigneeWrap: root.querySelector("#internalTaskAssigneeWrap"),
      assignee: root.querySelector("#internalTaskAssignee"),
      responsible: root.querySelector("#internalTaskResponsible"),
      displayAuthor: root.querySelector("#internalTaskDisplayAuthor"),
      notify: root.querySelector("#internalTaskNotify"),
      timing: root.querySelector("#internalTaskTiming"),
      scheduledAtWrap: root.querySelector("#internalTaskScheduledAtWrap"),
      scheduledAt: root.querySelector("#internalTaskScheduledAt"),
      deadlineEnabled: root.querySelector("#internalTaskDeadlineEnabled"),
      deadlineAtWrap: root.querySelector("#internalTaskDeadlineAtWrap"),
      deadlineAt: root.querySelector("#internalTaskDeadlineAt"),
      formError: root.querySelector("#internalTaskFormError"),
    };
  }

  function setStatus(root, message) {
    const el = els(root);
    if (el.status) el.status.textContent = message || "";
  }

  function setValidation(root, message) {
    const el = els(root);
    if (!el.validation) return;
    el.validation.textContent = message || "";
    el.validation.hidden = !message;
  }

  function setFormError(root, message) {
    const el = els(root);
    if (!el.formError) return;
    el.formError.textContent = message || "";
    el.formError.hidden = !message;
  }

  function optionHtml(user, selectedId = "") {
    return `<option value="${escapeHtml(user.id)}" ${user.id === selectedId ? "selected" : ""}>${escapeHtml(userLabel(user))}</option>`;
  }

  function fillOptions(root, selected = {}) {
    const el = els(root);
    const assignees = state.options?.assignees || [];
    const admins = state.options?.admins || [];
    el.assignee.innerHTML = `<option value="">Выберите сотрудника</option>${assignees.map((item) => optionHtml(item, selected.assigneeId || "")).join("")}`;
    const responsibleId = selected.responsibleId || state.options?.current_admin_id || admins[0]?.id || "";
    el.responsible.innerHTML = admins.map((item) => optionHtml(item, responsibleId)).join("");
  }

  function syncFormVisibility(root) {
    const el = els(root);
    const direct = el.assignmentMode.value === "direct";
    el.assigneeWrap.hidden = !direct;
    el.assignee.disabled = !direct;
    const scheduled = el.timing.value === "scheduled";
    el.scheduledAtWrap.hidden = !scheduled;
    el.scheduledAt.disabled = !scheduled;
    const deadlineEnabled = Boolean(el.deadlineEnabled.checked);
    el.deadlineAtWrap.hidden = !deadlineEnabled;
    el.deadlineAt.disabled = !deadlineEnabled;
  }

  function openModal(root, item = null) {
    const el = els(root);
    state.editingDefinitionId = item?.definition_id || "";
    el.modalTitle.textContent = item ? "Изменить задачу" : "Создать задачу";
    el.title.value = item?.title || "";
    el.description.value = item?.description || "";
    el.assignmentMode.value = item?.assignment_mode || "distribution";
    el.displayAuthor.checked = item ? Boolean(item.display_author) : true;
    el.notify.checked = item ? Boolean(item.notify_on_completion) : true;
    el.timing.value = item ? "scheduled" : "now";
    el.timing.disabled = Boolean(item);
    el.scheduledAt.value = item?.scheduled_for ? localInputValue(item.scheduled_for) : fallbackFutureLocalInput();
    el.deadlineEnabled.checked = Boolean(item?.deadline_at);
    el.deadlineAt.value = item?.deadline_at ? localInputValue(item.deadline_at) : "";
    fillOptions(root, {
      assigneeId: item?.assignee?.id || "",
      responsibleId: item?.responsible_admin?.id || "",
    });
    setFormError(root, "");
    syncFormVisibility(root);
    el.modal.classList.remove("hidden");
  }

  function closeModal(root) {
    const el = els(root);
    state.editingDefinitionId = "";
    el.timing.disabled = false;
    el.modal.classList.add("hidden");
  }

  function assignmentLabel(item) {
    if (item.assignment_mode === "direct") return userLabel(item.assignee);
    return "Распределение";
  }

  function taskLink(ticketId) {
    if (!ticketId) return EMPTY;
    return `<a href="/tickets/${encodeURIComponent(ticketId)}">Открыть</a>`;
  }

  function activeRow(item) {
    const assignee = userLabel(item.current_assignee || item.effective_assignee);
    return `
      <tr>
        <td>${escapeHtml(item.title || EMPTY)}</td>
        <td>${escapeHtml(assignee)}</td>
        <td>${escapeHtml(userLabel(item.responsible_admin))}</td>
        <td>${escapeHtml(formatDateTime(item.materialized_at))}</td>
        <td>${escapeHtml(STATUS_LABELS[item.ticket_status] || item.ticket_status || EMPTY)}</td>
        <td>${escapeHtml(formatDateTime(item.deadline_at))}</td>
        <td>${taskLink(item.ticket_id)}</td>
      </tr>
    `;
  }

  function scheduledRow(item) {
    return `
      <tr>
        <td>${escapeHtml(item.title || EMPTY)}</td>
        <td>${escapeHtml(formatDateTime(item.scheduled_for))}</td>
        <td>${escapeHtml(assignmentLabel(item))}</td>
        <td>${escapeHtml(userLabel(item.responsible_admin))}</td>
        <td>${escapeHtml(formatDateTime(item.deadline_at))}</td>
        <td><button class="action" type="button" data-edit-scheduled="${escapeHtml(item.definition_id)}">Изменить</button></td>
      </tr>
    `;
  }

  function completedRow(item) {
    const assignee = userLabel(item.current_assignee || item.effective_assignee);
    return `
      <tr>
        <td>${escapeHtml(item.title || EMPTY)}</td>
        <td>${escapeHtml(assignee)}</td>
        <td>${escapeHtml(userLabel(item.responsible_admin))}</td>
        <td>${escapeHtml(formatDateTime(item.closed_at))}</td>
        <td>${taskLink(item.ticket_id)}</td>
      </tr>
    `;
  }

  function renderTable(body, empty, items, rowFn, emptyText) {
    body.innerHTML = items.map(rowFn).join("");
    empty.hidden = items.length > 0;
    empty.textContent = emptyText;
  }

  function render(root) {
    const el = els(root);
    el.tabs.forEach((button) => {
      button.classList.toggle("active", button.dataset.internalTaskTab === state.tab);
    });
    el.scopes.forEach((button) => {
      button.classList.toggle("active", button.dataset.internalTaskScope === state.scope);
    });
    el.panels.forEach((panel) => {
      panel.hidden = panel.dataset.internalTaskPanel !== state.tab;
    });
    renderTable(el.activeBody, el.activeEmpty, state.overview.active || [], activeRow, "Активных внутренних задач нет");
    renderTable(el.scheduledBody, el.scheduledEmpty, state.overview.scheduled || [], scheduledRow, "Запланированных задач нет");
    renderTable(el.completedBody, el.completedEmpty, state.overview.completed || [], completedRow, "Завершённых задач нет");
  }

  async function loadData(root) {
    setStatus(root, "Загрузка...");
    setValidation(root, "");
    try {
      const [options, overview] = await Promise.all([
        state.options ? Promise.resolve(state.options) : api("/api/v1/management/internal-tasks/options"),
        api(`/api/v1/management/internal-tasks/overview?scope=${encodeURIComponent(state.scope)}`),
      ]);
      state.options = options;
      state.appTimezone = options?.app_timezone || "UTC";
      state.overview = overview || state.overview;
      render(root);
      setStatus(root, "Готово");
    } catch (error) {
      setValidation(root, error.message || "Ошибка загрузки");
      setStatus(root, "Ошибка");
    }
  }

  function collectPayload(root) {
    const el = els(root);
    const payload = {
      title: el.title.value,
      description: el.description.value,
      assignment_mode: el.assignmentMode.value,
      assignee_user_id: el.assignmentMode.value === "direct" ? (el.assignee.value || null) : null,
      display_author: Boolean(el.displayAuthor.checked),
      responsible_admin_id: el.responsible.value || null,
      notify_on_completion: Boolean(el.notify.checked),
      deadline_enabled: Boolean(el.deadlineEnabled.checked),
      deadline_at: el.deadlineEnabled.checked ? localWallTimeToUtcIso(el.deadlineAt.value, state.appTimezone || "UTC") : null,
    };
    if (el.timing.value === "scheduled") {
      payload.scheduled_for = el.scheduledAt.value || null;
    }
    return payload;
  }

  async function saveTask(root) {
    const el = els(root);
    setFormError(root, "");
    el.saveBtn.disabled = true;
    try {
      const payload = collectPayload(root);
      if (state.editingDefinitionId) {
        await api(`/api/v1/management/internal-tasks/scheduled/${encodeURIComponent(state.editingDefinitionId)}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
      } else if (el.timing.value === "scheduled") {
        await api("/api/v1/management/internal-tasks/scheduled", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        state.tab = "scheduled";
      } else {
        await api("/api/v1/management/internal-tasks", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        state.tab = "active";
      }
      closeModal(root);
      await loadData(root);
      setStatus(root, "Сохранено");
    } catch (error) {
      setFormError(root, error.message || "Ошибка сохранения");
    } finally {
      el.saveBtn.disabled = false;
    }
  }

  function initManagementTasksPage({ root }) {
    const el = els(root);
    const listeners = [];
    function listen(target, event, handler) {
      if (!target) return;
      target.addEventListener(event, handler);
      listeners.push(() => target.removeEventListener(event, handler));
    }

    listen(el.createBtn, "click", () => openModal(root));
    listen(el.closeModalBtn, "click", () => closeModal(root));
    listen(el.cancelBtn, "click", () => closeModal(root));
    listen(el.assignmentMode, "change", () => syncFormVisibility(root));
    listen(el.timing, "change", () => syncFormVisibility(root));
    listen(el.deadlineEnabled, "change", () => syncFormVisibility(root));
    listen(el.saveBtn, "click", () => saveTask(root));
    listen(el.scheduledBody, "click", (event) => {
      const button = event.target?.closest?.("[data-edit-scheduled]");
      if (!button) return;
      const item = (state.overview.scheduled || []).find((entry) => entry.definition_id === button.dataset.editScheduled);
      if (item) openModal(root, item);
    });
    listen(el.modal, "pointerdown", (event) => {
      el.modal.dataset.pointerDownBackdrop = event.target === el.modal ? "1" : "0";
    });
    listen(el.modal, "click", (event) => {
      if (el.modal.dataset.pointerDownBackdrop === "1" && event.target === el.modal) closeModal(root);
      el.modal.dataset.pointerDownBackdrop = "0";
    });
    el.tabs.forEach((button) => listen(button, "click", () => {
      state.tab = button.dataset.internalTaskTab || "active";
      render(root);
    }));
    el.scopes.forEach((button) => listen(button, "click", async () => {
      state.scope = button.dataset.internalTaskScope || "all";
      await loadData(root);
    }));

    window.HelpdeskSidebar?.init?.().then((snapshot) => {
      if (snapshot?.role !== "admin") {
        setValidation(root, "Доступ только для администратора");
        setStatus(root, "Недостаточно прав");
        return;
      }
      loadData(root);
    }).catch(() => loadData(root));

    return {
      destroy() {
        listeners.forEach((remove) => remove());
      },
    };
  }

  window.ManagementTasksTestHooks = {
    localInputValue,
    collectPayload,
    assignmentLabel,
  };

  if (window.HelpdeskAppShell?.registerPage) {
    window.HelpdeskAppShell.registerPage("management-tasks", initManagementTasksPage);
  } else {
    window.addEventListener("DOMContentLoaded", () => {
      const root = document.getElementById("appContent");
      if (root?.dataset.shellPage === "management-tasks") initManagementTasksPage({ root });
    });
  }
})();
