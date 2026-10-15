(function () {
  const EMPTY = "\u2014";
  const STATUS_LABELS = {
    new: "Новая",
    pending: "В работе",
    unassigned: "Нераспределена",
    closed: "Закрыта",
  };
  const RECURRING_STATE_LABELS = {
    active: "Активна",
    paused: "На паузе",
    finished: "Завершена",
  };
  const WEEKDAY_LABELS = {
    1: "Пн",
    2: "Вт",
    3: "Ср",
    4: "Чт",
    5: "Пт",
    6: "Сб",
    7: "Вс",
  };
  const state = {
    tab: "active",
    scope: "all",
    appTimezone: "UTC",
    options: null,
    overview: { active: [], scheduled: [], completed: [], recurring: [] },
    editingDefinitionId: "",
    editingKind: "",
    modalSnapshot: "",
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

  function formatLaunchDateTime(value) {
    const date = parseApiDateTime(value);
    if (!date) return EMPTY;
    const formatted = new Intl.DateTimeFormat("ru-RU", {
      timeZone: state.appTimezone || "UTC",
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
    return formatted.replace(",", " в");
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

  function isoWeekdayFromLocalInput(value) {
    const date = parseApiDateTime(localWallTimeToUtcIso(value, state.appTimezone || "UTC"));
    if (!date) return 1;
    const fields = timezoneFields(date, state.appTimezone || "UTC");
    const utcDay = new Date(Date.UTC(Number(fields.year), Number(fields.month) - 1, Number(fields.day))).getUTCDay();
    return utcDay === 0 ? 7 : utcDay;
  }

  function weekdayFromApiDateTime(value) {
    const local = localInputValue(value);
    return isoWeekdayFromLocalInput(local);
  }

  function recurrenceSummary(item) {
    const config = item.recurrence_config || {};
    if (item.recurrence_mode === "daily") return `Ежедневно: ${(config.times || []).join(", ")}`;
    if (item.recurrence_mode === "selected_weekdays") {
      const days = (config.weekdays || []).map((day) => WEEKDAY_LABELS[day] || day).join(", ");
      return `${days}: ${config.time || EMPTY}`;
    }
    if (item.recurrence_mode === "weekly") return `${WEEKDAY_LABELS[weekdayFromApiDateTime(item.cycle_start_at)] || EMPTY}: ${config.time || EMPTY}`;
    if (item.recurrence_mode === "monthly") {
      const day = config.day === "last" ? "последний день" : `${config.day} число`;
      return `${day}: ${config.time || EMPTY}`;
    }
    if (item.recurrence_mode === "every_n_days") return `Каждые ${config.interval_days || 1} дн.: ${config.time || EMPTY}`;
    return item.recurrence_mode || EMPTY;
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
      const neutralServerMessage = "Не удалось выполнить запрос. Попробуйте ещё раз.";
      const detailMessage = payload?.detail?.error?.message;
      const businessMessage = detailMessage || payload?.message || payload?.error?.message;
      const message = response.status >= 500
        ? (detailMessage && detailMessage !== "Internal Server Error" ? detailMessage : neutralServerMessage)
        : (businessMessage || raw || "Ошибка запроса");
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
      recurringBody: root.querySelector("#internalTasksRecurringBody"),
      recurringEmpty: root.querySelector("#internalTasksRecurringEmpty"),
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
      scheduleInfo: root.querySelector("#internalTaskScheduleInfo"),
      recurrenceWrap: root.querySelector("#internalTaskRecurrenceWrap"),
      recurrenceMode: root.querySelector("#internalTaskRecurrenceMode"),
      cycleStartAt: root.querySelector("#internalTaskCycleStartAt"),
      dailyTimesWrap: root.querySelector("#internalTaskDailyTimesWrap"),
      dailyTimes: root.querySelector("#internalTaskDailyTimes"),
      dailyTimesError: root.querySelector("#internalTaskDailyTimesError"),
      weekdaysWrap: root.querySelector("#internalTaskWeekdaysWrap"),
      weekdays: [...root.querySelectorAll("[data-internal-task-weekday]")],
      recurrenceWeekdayWrap: root.querySelector("#internalTaskRecurrenceWeekdayWrap"),
      recurrenceWeekday: root.querySelector("#internalTaskRecurrenceWeekday"),
      recurrenceTimeWrap: root.querySelector("#internalTaskRecurrenceTimeWrap"),
      recurrenceTime: root.querySelector("#internalTaskRecurrenceTime"),
      monthlyDayWrap: root.querySelector("#internalTaskMonthlyDayWrap"),
      monthlyDay: root.querySelector("#internalTaskMonthlyDay"),
      everyNDaysWrap: root.querySelector("#internalTaskEveryNDaysWrap"),
      everyNDays: root.querySelector("#internalTaskEveryNDays"),
      cycleEndMode: root.querySelector("#internalTaskCycleEndMode"),
      cycleEndAtWrap: root.querySelector("#internalTaskCycleEndAtWrap"),
      cycleEndAt: root.querySelector("#internalTaskCycleEndAt"),
      cycleMaxWrap: root.querySelector("#internalTaskCycleMaxWrap"),
      cycleMax: root.querySelector("#internalTaskCycleMax"),
      deadlineEnabled: root.querySelector("#internalTaskDeadlineEnabled"),
      deadlineAtWrap: root.querySelector("#internalTaskDeadlineAtWrap"),
      deadlineAt: root.querySelector("#internalTaskDeadlineAt"),
      formError: root.querySelector("#internalTaskFormError"),
      skipNextBtn: root.querySelector("#internalTaskSkipNextBtn"),
    };
  }

  function setStatus(root, message) {
    const el = els(root);
    if (el.status) {
      el.status.textContent = message || "";
      el.status.hidden = !message || message === "Готово";
    }
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

  function setDailyTimesError(root, message) {
    const el = els(root);
    if (!el.dailyTimesError) return;
    el.dailyTimesError.textContent = message || "";
    el.dailyTimesError.hidden = !message;
  }

  function findRecurring(definitionId) {
    return (state.overview.recurring || []).find((entry) => entry.definition_id === definitionId) || null;
  }

  function scheduleInfoText(item) {
    if (!item) return "";
    if (item.kind === "scheduled") return `Создание задачи: ${formatDateTime(item.scheduled_for)}`;
    if (item.kind === "recurring") {
      if (item.state === "paused") return "Расписание на паузе";
      if (item.next_occurrence_at) return `Следующее создание задачи: ${formatDateTime(item.next_occurrence_at)}`;
      return "Следующих запусков нет";
    }
    return "";
  }

  function updateScheduleInfo(root, item) {
    const el = els(root);
    const text = scheduleInfoText(item);
    if (el.scheduleInfo) {
      el.scheduleInfo.textContent = text;
      el.scheduleInfo.hidden = !text;
    }
    if (el.skipNextBtn) {
      el.skipNextBtn.hidden = !(item?.kind === "recurring" && item.state === "active" && item.next_occurrence_at);
      el.skipNextBtn.disabled = false;
    }
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
    if (el.monthlyDay && el.monthlyDay.options?.length !== 32) {
      el.monthlyDay.innerHTML = `<option value="last">Последний день</option>${Array.from({ length: 31 }, (_, index) => `<option value="${index + 1}">${index + 1}</option>`).join("")}`;
    }
  }

  function syncFormVisibility(root) {
    const el = els(root);
    const setHiddenDisabled = (wrapper, controls, hidden) => {
      if (wrapper) wrapper.hidden = hidden;
      controls.filter(Boolean).forEach((control) => {
        control.disabled = hidden;
      });
    };
    const direct = el.assignmentMode.value === "direct";
    setHiddenDisabled(el.assigneeWrap, [el.assignee], !direct);
    const scheduled = el.timing.value === "scheduled";
    const recurring = el.timing.value === "recurring";
    setHiddenDisabled(el.scheduledAtWrap, [el.scheduledAt], !scheduled);
    setHiddenDisabled(el.recurrenceWrap, [
      el.recurrenceMode,
      el.cycleStartAt,
      el.dailyTimes,
      ...el.weekdays,
      el.recurrenceWeekday,
      el.recurrenceTime,
      el.monthlyDay,
      el.everyNDays,
      el.cycleEndMode,
      el.cycleEndAt,
      el.cycleMax,
    ], !recurring);
    const mode = el.recurrenceMode.value;
    setHiddenDisabled(el.dailyTimesWrap, [el.dailyTimes], !recurring || mode !== "daily");
    setHiddenDisabled(el.weekdaysWrap, el.weekdays, !recurring || mode !== "selected_weekdays");
    setHiddenDisabled(el.recurrenceWeekdayWrap, [el.recurrenceWeekday], !recurring || mode !== "weekly");
    setHiddenDisabled(el.recurrenceTimeWrap, [el.recurrenceTime], !recurring || mode === "daily");
    setHiddenDisabled(el.monthlyDayWrap, [el.monthlyDay], !recurring || mode !== "monthly");
    setHiddenDisabled(el.everyNDaysWrap, [el.everyNDays], !recurring || mode !== "every_n_days");
    setHiddenDisabled(el.cycleEndAtWrap, [el.cycleEndAt], !recurring || el.cycleEndMode.value !== "at");
    setHiddenDisabled(el.cycleMaxWrap, [el.cycleMax], !recurring || el.cycleEndMode.value !== "count");
    const deadlineEnabled = Boolean(el.deadlineEnabled.checked);
    setHiddenDisabled(el.deadlineAtWrap, [el.deadlineAt], !deadlineEnabled);
  }

  function setRecurrenceFields(root, item = null) {
    const el = els(root);
    const config = item?.recurrence_config || {};
    el.recurrenceMode.value = item?.recurrence_mode || "daily";
    el.cycleStartAt.value = item?.cycle_start_at ? localInputValue(item.cycle_start_at) : fallbackFutureLocalInput();
    el.dailyTimes.value = (config.times || ["10:00"]).join(", ");
    el.weekdays.forEach((box) => {
      box.checked = (config.weekdays || [1]).includes(Number(box.value));
    });
    el.recurrenceWeekday.value = String(item?.recurrence_mode === "weekly" ? weekdayFromApiDateTime(item.cycle_start_at) : 1);
    el.recurrenceTime.value = config.time || "10:00";
    el.monthlyDay.value = String(config.day || "last");
    el.everyNDays.value = String(config.interval_days || 1);
    el.cycleEndMode.value = item?.cycle_end_mode || "never";
    el.cycleEndAt.value = item?.cycle_end_at ? localInputValue(item.cycle_end_at) : "";
    el.cycleMax.value = String(item?.cycle_max_occurrences || 1);
  }

  function openModal(root, item = null) {
    const el = els(root);
    state.editingDefinitionId = item?.definition_id || "";
    state.editingKind = item?.kind || "";
    el.modalTitle.textContent = item ? "Изменить задачу" : "Создать задачу";
    el.title.value = item?.title || "";
    el.description.value = item?.description || "";
    el.assignmentMode.value = item?.assignment_mode || "distribution";
    el.displayAuthor.checked = item ? Boolean(item.display_author) : true;
    el.notify.checked = item ? Boolean(item.notify_on_completion) : true;
    el.timing.value = item?.kind === "recurring" ? "recurring" : (item ? "scheduled" : "now");
    el.timing.disabled = Boolean(item);
    el.scheduledAt.value = item?.scheduled_for ? localInputValue(item.scheduled_for) : fallbackFutureLocalInput();
    el.deadlineEnabled.checked = Boolean(item?.deadline_at);
    el.deadlineAt.value = item?.deadline_at ? localInputValue(item.deadline_at) : "";
    fillOptions(root, {
      assigneeId: item?.assignee?.id || "",
      responsibleId: item?.responsible_admin?.id || "",
    });
    setRecurrenceFields(root, item?.kind === "recurring" ? item : null);
    setFormError(root, "");
    syncFormVisibility(root);
    updateScheduleInfo(root, item);
    state.modalSnapshot = modalSnapshot(root);
    el.modal.classList.remove("hidden");
  }

  function closeModal(root, options = {}) {
    if (!options.force && !shouldCloseModal(root)) return false;
    const el = els(root);
    state.editingDefinitionId = "";
    state.editingKind = "";
    state.modalSnapshot = "";
    el.timing.disabled = false;
    updateScheduleInfo(root, null);
    el.modal.classList.add("hidden");
    return true;
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
        <td>${taskLink(item.ticket_id)} <button class="action" type="button" data-delete-materialized="${escapeHtml(item.ticket_id)}">Удалить</button></td>
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
        <td><button class="action" type="button" data-edit-scheduled="${escapeHtml(item.definition_id)}">Изменить</button> <button class="action" type="button" data-delete-scheduled="${escapeHtml(item.definition_id)}">Удалить</button></td>
      </tr>
    `;
  }

  function recurringActions(item) {
    const edit = `<button class="action" type="button" data-edit-recurring="${escapeHtml(item.definition_id)}">Изменить</button>`;
    const remove = `<button class="action" type="button" data-delete-recurring="${escapeHtml(item.definition_id)}">Удалить</button>`;
    if (item.state === "active") {
      return `${edit} <button class="action" type="button" data-pause-recurring="${escapeHtml(item.definition_id)}">Пауза</button> ${remove}`;
    }
    if (item.state === "paused" || item.state === "finished") {
      return `${edit} <button class="action" type="button" data-resume-recurring="${escapeHtml(item.definition_id)}">Возобновить</button> ${remove}`;
    }
    return `${edit} ${remove}`;
  }

  function recurringRow(item) {
    return `
      <tr class="${item.state === "finished" ? "management-tasks-muted-row" : ""}">
        <td>${escapeHtml(item.title || EMPTY)}</td>
        <td>${escapeHtml(recurrenceSummary(item))}</td>
        <td>${escapeHtml(userLabel(item.responsible_admin))}</td>
        <td>${escapeHtml(formatDateTime(item.next_occurrence_at))}</td>
        <td>${escapeHtml(RECURRING_STATE_LABELS[item.state] || item.state || EMPTY)}</td>
        <td>${recurringActions(item)}</td>
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
        <td>${taskLink(item.ticket_id)} <button class="action" type="button" data-delete-materialized="${escapeHtml(item.ticket_id)}">Удалить</button></td>
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
    renderTable(el.recurringBody, el.recurringEmpty, state.overview.recurring || [], recurringRow, "Регулярных задач нет");
    renderTable(el.completedBody, el.completedEmpty, state.overview.completed || [], completedRow, "Завершённых задач нет");
  }

  function recurrenceConfig(root) {
    const el = els(root);
    const mode = el.recurrenceMode.value;
    if (mode === "daily") return { times: normalizedDailyTimes(root) };
    if (mode === "selected_weekdays") {
      return {
        weekdays: el.weekdays.filter((box) => box.checked).map((box) => Number(box.value)),
        time: el.recurrenceTime.value,
      };
    }
    if (mode === "weekly") return { weekday: Number(el.recurrenceWeekday.value), time: el.recurrenceTime.value };
    if (mode === "monthly") {
      const value = el.monthlyDay.value;
      return { day: value === "last" ? "last" : Number(value), time: el.recurrenceTime.value };
    }
    if (mode === "every_n_days") {
      return { interval_days: Number(el.everyNDays.value), time: el.recurrenceTime.value };
    }
    return {};
  }

  function parseDailyTimes(value) {
    const rawItems = String(value || "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
    const seen = new Set();
    const times = [];
    const invalid = [];
    rawItems.forEach((item) => {
      const normalized = item.replace(/\s+/g, "").replace(".", ":");
      const match = normalized.match(/^(\d{1,2}):(\d{2})$/);
      if (!match) {
        invalid.push(item);
        return;
      }
      const hour = Number(match[1]);
      const minute = Number(match[2]);
      if (hour > 23 || minute > 59) {
        invalid.push(item);
        return;
      }
      const time = `${pad(hour)}:${pad(minute)}`;
      if (!seen.has(time)) {
        seen.add(time);
        times.push(time);
      }
    });
    return { times, invalid };
  }

  function normalizedDailyTimes(root) {
    return parseDailyTimes(els(root).dailyTimes.value).times;
  }

  function validateDailyTimes(root) {
    const el = els(root);
    if (el.timing.value !== "recurring" || el.recurrenceMode.value !== "daily") {
      setDailyTimesError(root, "");
      return true;
    }
    const result = parseDailyTimes(el.dailyTimes.value);
    if (result.invalid.length) {
      const values = result.invalid.join(", ");
      setDailyTimesError(root, `Некорректное время: ${values}. Используйте формат ЧЧ:ММ, например 10:00.`);
      return false;
    }
    el.dailyTimes.value = result.times.join(", ");
    setDailyTimesError(root, "");
    return true;
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
      setStatus(root, "");
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
    if (el.timing.value === "recurring") {
      payload.recurrence_mode = el.recurrenceMode.value;
      payload.recurrence_config = recurrenceConfig(root);
      payload.cycle_start_at = el.cycleStartAt.value || null;
      payload.cycle_end_mode = el.cycleEndMode.value;
      payload.cycle_end_at = el.cycleEndMode.value === "at" ? (el.cycleEndAt.value || null) : null;
      payload.cycle_max_occurrences = el.cycleEndMode.value === "count" ? Number(el.cycleMax.value) : null;
    }
    return payload;
  }

  function modalSnapshot(root) {
    const el = els(root);
    return JSON.stringify({
      editingDefinitionId: state.editingDefinitionId,
      editingKind: state.editingKind,
      timing: el.timing.value,
      payload: collectPayload(root),
    });
  }

  function isModalDirty(root) {
    const el = els(root);
    if (el.modal?.classList?.contains("hidden")) return false;
    return Boolean(state.modalSnapshot) && modalSnapshot(root) !== state.modalSnapshot;
  }

  function shouldCloseModal(root) {
    if (!isModalDirty(root)) return true;
    if (typeof window.confirm !== "function") return true;
    return window.confirm("Вы уверены, что хотите закрыть окно?\nВсе несохранённые данные будут потеряны.");
  }

  async function saveTask(root) {
    const el = els(root);
    setFormError(root, "");
    if (!validateDailyTimes(root)) return;
    el.saveBtn.disabled = true;
    try {
      const payload = collectPayload(root);
      if (state.editingDefinitionId && state.editingKind === "recurring") {
        await api(`/api/v1/management/internal-tasks/recurring/${encodeURIComponent(state.editingDefinitionId)}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
      } else if (state.editingDefinitionId) {
        await api(`/api/v1/management/internal-tasks/scheduled/${encodeURIComponent(state.editingDefinitionId)}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
      } else if (el.timing.value === "recurring") {
        await api("/api/v1/management/internal-tasks/recurring", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        state.tab = "recurring";
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
      closeModal(root, { force: true });
      await loadData(root);
      setStatus(root, "Сохранено");
    } catch (error) {
      setFormError(root, error.message || "Ошибка сохранения");
    } finally {
      el.saveBtn.disabled = false;
    }
  }

  function confirmAction(message) {
    if (typeof window.confirm !== "function") return true;
    return window.confirm(message);
  }

  async function deleteMaterialized(root, ticketId) {
    if (!ticketId || !confirmAction("Удалить задачу? Это действие нельзя отменить.")) return;
    try {
      await api(`/api/v1/management/internal-tasks/materialized/${encodeURIComponent(ticketId)}`, { method: "DELETE" });
      setValidation(root, "");
      await loadData(root);
    } catch (error) {
      setValidation(root, error.message || "Ошибка удаления задачи");
    }
  }

  async function deleteScheduled(root, definitionId) {
    if (!definitionId || !confirmAction("Удалить задачу? Это действие нельзя отменить.")) return;
    try {
      await api(`/api/v1/management/internal-tasks/scheduled/${encodeURIComponent(definitionId)}`, { method: "DELETE" });
      setValidation(root, "");
      await loadData(root);
    } catch (error) {
      setValidation(root, error.message || "Ошибка удаления запланированной задачи");
    }
  }

  async function deleteRecurring(root, definitionId) {
    if (!definitionId || !confirmAction("Удалить регулярную задачу?\nНовые задачи по этому расписанию больше создаваться не будут.\nУже созданные задачи останутся.")) return;
    try {
      await api(`/api/v1/management/internal-tasks/recurring/${encodeURIComponent(definitionId)}`, { method: "DELETE" });
      setValidation(root, "");
      await loadData(root);
    } catch (error) {
      setValidation(root, error.message || "Ошибка удаления регулярной задачи");
    }
  }

  async function skipNextRecurring(root) {
    const definitionId = state.editingKind === "recurring" ? state.editingDefinitionId : "";
    const item = findRecurring(definitionId);
    const launchTime = formatLaunchDateTime(item?.next_occurrence_at);
    const message = `Пропустить ближайший запуск ${launchTime}?\nСледующие запуски продолжат работать по расписанию.`;
    if (!definitionId || !item?.next_occurrence_at || !confirmAction(message)) return;
    const el = els(root);
    if (el.skipNextBtn) el.skipNextBtn.disabled = true;
    try {
      await api(`/api/v1/management/internal-tasks/recurring/${encodeURIComponent(definitionId)}/skip-next`, { method: "POST" });
      setValidation(root, "");
      await loadData(root);
      const refreshed = findRecurring(definitionId);
      if (refreshed) openModal(root, refreshed);
    } catch (error) {
      setFormError(root, error.message || "Ошибка пропуска следующего запуска");
    } finally {
      if (el.skipNextBtn) el.skipNextBtn.disabled = false;
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
    listen(el.recurrenceMode, "change", () => syncFormVisibility(root));
    listen(el.cycleEndMode, "change", () => syncFormVisibility(root));
    listen(el.deadlineEnabled, "change", () => syncFormVisibility(root));
    listen(el.dailyTimes, "input", () => {
      if (el.dailyTimesError && !el.dailyTimesError.hidden) validateDailyTimes(root);
    });
    listen(el.saveBtn, "click", () => saveTask(root));
    listen(el.skipNextBtn, "click", () => skipNextRecurring(root));
    listen(el.activeBody, "click", (event) => {
      const button = event.target?.closest?.("[data-delete-materialized]");
      if (button) deleteMaterialized(root, button.dataset.deleteMaterialized);
    });
    listen(el.completedBody, "click", (event) => {
      const button = event.target?.closest?.("[data-delete-materialized]");
      if (button) deleteMaterialized(root, button.dataset.deleteMaterialized);
    });
    listen(el.scheduledBody, "click", (event) => {
      const editButton = event.target?.closest?.("[data-edit-scheduled]");
      const deleteButton = event.target?.closest?.("[data-delete-scheduled]");
      if (deleteButton) {
        deleteScheduled(root, deleteButton.dataset.deleteScheduled);
        return;
      }
      if (!editButton) return;
      const item = (state.overview.scheduled || []).find((entry) => entry.definition_id === editButton.dataset.editScheduled);
      if (item) openModal(root, item);
    });
    listen(el.recurringBody, "click", async (event) => {
      const editButton = event.target?.closest?.("[data-edit-recurring]");
      const pauseButton = event.target?.closest?.("[data-pause-recurring]");
      const resumeButton = event.target?.closest?.("[data-resume-recurring]");
      const deleteButton = event.target?.closest?.("[data-delete-recurring]");
      if (editButton) {
        const item = (state.overview.recurring || []).find((entry) => entry.definition_id === editButton.dataset.editRecurring);
        if (item) openModal(root, item);
        return;
      }
      if (deleteButton) {
        deleteRecurring(root, deleteButton.dataset.deleteRecurring);
        return;
      }
      const actionButton = pauseButton || resumeButton;
      if (!actionButton) return;
      const action = pauseButton ? "pause" : "resume";
      const definitionId = pauseButton?.dataset.pauseRecurring || resumeButton?.dataset.resumeRecurring;
      try {
        await api(`/api/v1/management/internal-tasks/recurring/${encodeURIComponent(definitionId)}/${action}`, { method: "POST" });
        setValidation(root, "");
        await loadData(root);
      } catch (error) {
        setValidation(root, error.message || "Ошибка изменения расписания");
      }
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
    fallbackFutureLocalInput,
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
