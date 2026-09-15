(function () {
  const EMPTY = "\u2014";
  const DEFAULT_PERIOD = "30days";
  const EMPLOYEE_FILTER_ROLES = new Set(["agent", "trainee"]);
  const TRANSITION_PAGE_SIZE_MIN = 1;
  const TRANSITION_PAGE_SIZE_MAX = 100;
  const TRANSITION_ROW_HEIGHT_FALLBACK = 30;
  const TRANSITION_PAGE_SIZE_SAFETY_PX = 2;
  const TRANSITION_RESIZE_DEBOUNCE_MS = 120;
  const STATUS_LABELS = {
    offline: "Не в сети",
    online: "В сети",
    on_ticket: "На заявке",
    waiting: "В ожидании",
    lunch: "Обед",
  };

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function pad(value) {
    return String(value).padStart(2, "0");
  }

  function calendarDate(value, appTimezone) {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: appTimezone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(value);
    const fields = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    return [fields.year, fields.month, fields.day].join("-");
  }

  function dateParts(value) {
    const [year, month, day] = String(value).split("-").map(Number);
    return { year, month, day };
  }

  function formatCalendarDate(year, month, day) {
    return [year, pad(month), pad(day)].join("-");
  }

  function addCalendarDays(value, days) {
    const { year, month, day } = dateParts(value);
    const result = new Date(Date.UTC(year, month - 1, day + days, 12, 0, 0));
    return formatCalendarDate(result.getUTCFullYear(), result.getUTCMonth() + 1, result.getUTCDate());
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

  function isoBoundaryInTimezone(value, appTimezone) {
    const { year, month, day } = dateParts(value);
    const wallTimeUtc = Date.UTC(year, month - 1, day, 0, 0, 0);
    let offset = timezoneOffsetMinutes(new Date(wallTimeUtc), appTimezone);
    offset = timezoneOffsetMinutes(new Date(wallTimeUtc - offset * 60000), appTimezone);
    const sign = offset >= 0 ? "+" : "-";
    const abs = Math.abs(offset);
    return [
      year,
      "-",
      pad(month),
      "-",
      pad(day),
      "T",
      "00",
      ":",
      "00",
      ":",
      "00",
      sign,
      pad(Math.floor(abs / 60)),
      ":",
      pad(abs % 60),
    ].join("");
  }

  function periodBounds(period, fromValue, toValue, now, appTimezone) {
    if (period === "all") return { params: {} };
    const todayValue = calendarDate(now, appTimezone);
    let from = null;
    let to = null;
    if (period === "today") {
      from = todayValue;
      to = addCalendarDays(todayValue, 1);
    } else if (period === "yesterday") {
      from = addCalendarDays(todayValue, -1);
      to = todayValue;
    } else if (period === "week") {
      from = addCalendarDays(todayValue, -6);
      to = addCalendarDays(todayValue, 1);
    } else if (period === DEFAULT_PERIOD) {
      from = addCalendarDays(todayValue, -29);
      to = addCalendarDays(todayValue, 1);
    } else if (period === "month") {
      const { year, month } = dateParts(todayValue);
      from = formatCalendarDate(year, month, 1);
      to = month === 12 ? formatCalendarDate(year + 1, 1, 1) : formatCalendarDate(year, month + 1, 1);
    } else if (period === "year") {
      const { year } = dateParts(todayValue);
      from = formatCalendarDate(year, 1, 1);
      to = formatCalendarDate(year + 1, 1, 1);
    } else if (period === "custom") {
      if (!fromValue || !toValue) return { error: "Укажите даты начала и окончания периода." };
      if (toValue < fromValue) return { error: "Дата окончания не должна быть раньше даты начала." };
      from = fromValue;
      to = addCalendarDays(toValue, 1);
    } else {
      return { error: "Выберите корректный период." };
    }
    return { params: { date_from: isoBoundaryInTimezone(from, appTimezone), date_to: isoBoundaryInTimezone(to, appTimezone) } };
  }

  function userLabel(user) {
    const name = String(user.full_name || user.login || user.email || user.id || "").trim();
    return name || EMPTY;
  }

  function durationLabel(value) {
    let seconds = Math.max(0, Math.trunc(Number(value || 0)));
    if (seconds === 0) return "0 сек";
    const units = [
      ["д", 86400],
      ["ч", 3600],
      ["мин", 60],
      ["сек", 1],
    ];
    const parts = [];
    for (const [label, size] of units) {
      const amount = Math.floor(seconds / size);
      if (!amount) continue;
      parts.push(amount + " " + label);
      seconds -= amount * size;
      if (parts.length === 2) break;
    }
    return parts.join(" ");
  }

  function initManagementStatisticsPage({ root }) {
    const el = {
      status: root.querySelector("#managementStatisticsStatus"),
      employee: root.querySelector("#managementStatisticsEmployee"),
      period: root.querySelector("#managementStatisticsPeriod"),
      dateFrom: root.querySelector("#managementStatisticsDateFrom"),
      dateTo: root.querySelector("#managementStatisticsDateTo"),
      validation: root.querySelector("#managementStatisticsValidation"),
      workTimeSummary: root.querySelector("#managementStatisticsWorkTimeSummary"),
      workTimeTableBody: root.querySelector("#managementStatisticsWorkTimeTableBody"),
      workTimeEmpty: root.querySelector("#managementStatisticsWorkTimeEmpty"),
      statusesSummary: root.querySelector("#managementStatisticsStatusesSummary"),
      statusesTableBody: root.querySelector("#managementStatisticsStatusesTableBody"),
      statusesEmpty: root.querySelector("#managementStatisticsStatusesEmpty"),
      transitionsTableBody: root.querySelector("#managementStatisticsTransitionsTableBody"),
      transitionsEmpty: root.querySelector("#managementStatisticsTransitionsEmpty"),
      transitionsViewport: root.querySelector("#managementStatisticsTransitionsViewport"),
      transitionsHead: root.querySelector("#managementStatisticsTransitionsHead"),
      transitionsPrev: root.querySelector("#managementStatisticsTransitionsPrev"),
      transitionsNext: root.querySelector("#managementStatisticsTransitionsNext"),
      transitionsPage: root.querySelector("#managementStatisticsTransitionsPage"),
      transitionsPagination: root.querySelector(".management-statistics-transition-log .management-statistics-pagination"),
      medianHelp: root.querySelector(".management-statistics-median-help"),
      tabs: [...root.querySelectorAll("[data-statistics-tab]")],
      panels: [...root.querySelectorAll("[data-statistics-panel]")],
      customFields: [...root.querySelectorAll(".management-statistics-custom")],
    };
    const state = {
      appTimezone: "UTC",
      tab: "work-time",
      generation: 0,
      transitionGeneration: 0,
      transitionPage: 1,
      transitionCursors: [null],
      transitionHasMore: false,
      transitionPageSize: null,
      transitionResizeTimer: null,
      transitionResizeObserver: null,
      controller: new AbortController(),
      destroyed: false,
    };
    const listeners = [];

    function listen(target, event, handler) {
      target.addEventListener(event, handler);
      listeners.push(() => target.removeEventListener(event, handler));
    }

    function setStatus(message) {
      el.status.textContent = message;
      el.status.hidden = !message;
    }

    function setValidation(message) {
      el.validation.textContent = message;
      el.validation.hidden = !message;
    }

    function panelElements(tab) {
      if (tab === "work-time") {
        return { tableBody: el.workTimeTableBody, empty: el.workTimeEmpty };
      }
      if (tab === "statuses") {
        return { tableBody: el.statusesTableBody, empty: el.statusesEmpty };
      }
      return { tableBody: el.workTimeTableBody, empty: el.workTimeEmpty };
    }

    function clearActiveResults(message) {
      state.generation += 1;
      state.controller.abort();
      state.controller = new AbortController();
      const active = panelElements(state.tab);
      active.tableBody.innerHTML = "";
      active.empty.hidden = false;
      active.empty.textContent = message || "Загрузка...";
      if (state.tab === "statuses") {
        el.transitionsTableBody.innerHTML = "";
        el.transitionsEmpty.hidden = false;
        el.transitionsEmpty.textContent = message || "Загрузка...";
      }
      return state.generation;
    }

    async function api(path, signal) {
      const response = await fetch(path, {
        headers: { accept: "application/json" },
        credentials: "include",
        cache: "no-store",
        signal,
      });
      if (response.status === 401) window.location.href = "/login";
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
        const message = payload?.detail?.error?.message || payload?.detail || raw || "Ошибка запроса";
        throw new Error(message);
      }
      return payload;
    }

    function updateCustomVisibility() {
      const show = el.period.value === "custom";
      for (const field of el.customFields) field.hidden = !show;
    }

    function resetCustomDates() {
      const today = calendarDate(new Date(), state.appTimezone);
      el.dateFrom.value = addCalendarDays(today, -29);
      el.dateTo.value = today;
    }

    function updateTransitionPagination() {
      if (!el.transitionsPrev || !el.transitionsNext || !el.transitionsPage) return;
      el.transitionsPage.textContent = `Страница ${state.transitionPage}`;
      el.transitionsPrev.disabled = state.transitionPage <= 1;
      el.transitionsNext.disabled = !state.transitionHasMore;
    }

    function resetTransitionPagination() {
      state.transitionGeneration += 1;
      state.transitionPage = 1;
      state.transitionCursors = [null];
      state.transitionHasMore = false;
      updateTransitionPagination();
    }

    function clampTransitionPageSize(value) {
      const normalized = Number.isFinite(value) ? Math.floor(value) : TRANSITION_PAGE_SIZE_MIN;
      return Math.max(TRANSITION_PAGE_SIZE_MIN, Math.min(TRANSITION_PAGE_SIZE_MAX, normalized));
    }

    function elementHeight(element) {
      if (!element) return 0;
      const rectHeight = typeof element.getBoundingClientRect === "function" ? element.getBoundingClientRect().height : 0;
      return rectHeight || element.clientHeight || element.offsetHeight || 0;
    }

    function transitionViewportHeight() {
      if (el.transitionsViewport && el.transitionsPagination && typeof el.transitionsViewport.getBoundingClientRect === "function" && typeof el.transitionsPagination.getBoundingClientRect === "function") {
        const viewportRect = el.transitionsViewport.getBoundingClientRect();
        const paginationRect = el.transitionsPagination.getBoundingClientRect();
        const geometryHeight = paginationRect.top - viewportRect.top;
        if (Number.isFinite(geometryHeight) && geometryHeight > 0) return geometryHeight;
      }
      return elementHeight(el.transitionsViewport);
    }

    function measuredTransitionRowHeight() {
      const row = el.transitionsTableBody?.querySelector?.("tr") || el.transitionsTableBody?.firstElementChild;
      return elementHeight(row) || TRANSITION_ROW_HEIGHT_FALLBACK;
    }

    function calculateTransitionPageSize() {
      const viewportHeight = transitionViewportHeight();
      const headerHeight = elementHeight(el.transitionsHead);
      const rowHeight = measuredTransitionRowHeight();
      const availableRowsHeight = Math.max(0, viewportHeight - headerHeight - TRANSITION_PAGE_SIZE_SAFETY_PX);
      return clampTransitionPageSize(availableRowsHeight / rowHeight);
    }

    function currentTransitionPageSize() {
      const pageSize = calculateTransitionPageSize();
      state.transitionPageSize = pageSize;
      return pageSize;
    }

    function scheduleTransitionPageSizeRefresh() {
      if (state.destroyed || state.tab !== "statuses") return;
      if (state.transitionResizeTimer != null) clearTimeout(state.transitionResizeTimer);
      state.transitionResizeTimer = setTimeout(() => {
        state.transitionResizeTimer = null;
        if (state.destroyed || state.tab !== "statuses") return;
        const nextPageSize = calculateTransitionPageSize();
        if (nextPageSize === state.transitionPageSize) return;
        state.transitionPageSize = nextPageSize;
        resetTransitionPagination();
        loadTransitionsPage();
      }, TRANSITION_RESIZE_DEBOUNCE_MS);
    }

    function renderUsers(users) {
      const current = el.employee.value || "";
      const options = ['<option value="">Все сотрудники</option>'];
      const employees = users.filter((user) => EMPLOYEE_FILTER_ROLES.has(user.role));
      for (const user of employees) {
        const inactive = user.is_active === false ? " (неактивен)" : "";
        options.push(`<option value="${escapeHtml(user.id)}">${escapeHtml(userLabel(user) + inactive)}</option>`);
      }
      el.employee.innerHTML = options.join("");
      el.employee.value = employees.some((user) => user.id === current) ? current : "";
    }

    function userCellHtml(item) {
      const deleted = item.full_name == null;
      const inactive = item.is_active === false;
      const name = deleted ? "Удалённый сотрудник" : item.full_name;
      const meta = deleted
        ? item.user_id || EMPTY
        : inactive
          ? "Неактивен"
          : "";
      return `
        <td>
          <div class="management-statistics-user-name">${escapeHtml(name)}</div>
          ${meta ? `<div class="management-statistics-user-muted">${escapeHtml(meta)}</div>` : ""}
        </td>
      `;
    }

    function statusLabel(status) {
      return STATUS_LABELS[status] || status || EMPTY;
    }

    function parseApiDateTime(value) {
      if (typeof value !== "string") return new Date(value);
      const normalized = value.trim();
      const hasTimezone = /(?:Z|[+-]\d{2}:\d{2})$/i.test(normalized);
      const shouldTreatAsUtc = /^\d{4}-\d{2}-\d{2}T/.test(normalized) && !hasTimezone;
      return new Date(shouldTreatAsUtc ? `${normalized}Z` : normalized);
    }

    function formatDateTime(value) {
      const date = parseApiDateTime(value);
      if (Number.isNaN(date.getTime())) return value || EMPTY;
      const parts = new Intl.DateTimeFormat("ru-RU", {
        timeZone: state.appTimezone,
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hourCycle: "h23",
      }).formatToParts(date);
      const fields = Object.fromEntries(parts.map((part) => [part.type, part.value]));
      return `${fields.day}.${fields.month}.${fields.year} ${fields.hour}:${fields.minute}:${fields.second}`;
    }

    function workTimeRowHtml(item) {
      return `
        <tr>
          ${userCellHtml(item)}
          <td>${Number(item.tickets || 0)}</td>
          <td>${Number(item.tasks || 0)}</td>
          <td>${Number(item.objects || 0)}</td>
          <td>${escapeHtml(durationLabel(item.total_seconds))}</td>
          <td>${escapeHtml(durationLabel(item.average_seconds))}</td>
          <td>${escapeHtml(durationLabel(item.median_seconds))}</td>
        </tr>
      `;
    }

    function statusRowHtml(item) {
      return `
        <tr>
          ${userCellHtml(item)}
          <td>${escapeHtml(durationLabel(item.offline_seconds))}</td>
          <td>${escapeHtml(durationLabel(item.online_seconds))}</td>
          <td>${escapeHtml(durationLabel(item.on_ticket_seconds))}</td>
          <td>${escapeHtml(durationLabel(item.waiting_seconds))}</td>
          <td>${escapeHtml(durationLabel(item.lunch_seconds))}</td>
        </tr>
      `;
    }

    function transitionRowHtml(item) {
      return `
        <tr>
          ${userCellHtml(item)}
          <td>${escapeHtml(statusLabel(item.from_status))}</td>
          <td>${escapeHtml(statusLabel(item.to_status))}</td>
          <td>${escapeHtml(formatDateTime(item.changed_at))}</td>
        </tr>
      `;
    }

    function summaryHtml(parts) {
      return parts.map((part, index) => `<span>${index > 0 ? "| " : ""}${escapeHtml(part)}</span>`).join("");
    }

    function renderWorkTime(payload) {
      const total = payload?.total || {};
      const items = Array.isArray(payload?.items) ? payload.items : [];
      el.workTimeSummary.innerHTML = summaryHtml([
        `Общее время: ${durationLabel(total.total_seconds)}`,
        `Объектов: ${Number(total.objects || 0)}`,
        `Среднее: ${durationLabel(total.average_seconds)}`,
        `Медиана: ${durationLabel(total.median_seconds)}`,
        `Заявки: ${Number(total.tickets || 0)}`,
        `Задачи: ${Number(total.tasks || 0)}`,
      ]);
      el.workTimeTableBody.innerHTML = items.map(workTimeRowHtml).join("");
      el.workTimeEmpty.hidden = items.length > 0;
      el.workTimeEmpty.textContent = "За выбранный период времени работы нет";
      setStatus("");
    }

    function renderStatuses(payload) {
      const total = payload?.total || {};
      const items = Array.isArray(payload?.items) ? payload.items : [];
      el.statusesSummary.innerHTML = summaryHtml([
        `Учтено времени: ${durationLabel(total.total_seconds)}`,
        `Не в сети: ${durationLabel(total.offline_seconds)}`,
        `В сети: ${durationLabel(total.online_seconds)}`,
        `На заявке: ${durationLabel(total.on_ticket_seconds)}`,
        `В ожидании: ${durationLabel(total.waiting_seconds)}`,
        `Обед: ${durationLabel(total.lunch_seconds)}`,
      ]);
      el.statusesTableBody.innerHTML = items.map(statusRowHtml).join("");
      el.statusesEmpty.hidden = items.length > 0;
      el.statusesEmpty.textContent = "За выбранный период данных по статусам нет";
      setStatus("");
    }

    function renderTransitions(payload) {
      const items = Array.isArray(payload?.items) ? payload.items : [];
      el.transitionsTableBody.innerHTML = items.map(transitionRowHtml).join("");
      el.transitionsEmpty.hidden = items.length > 0;
      el.transitionsEmpty.textContent = "За выбранный период смен статусов нет";
      state.transitionHasMore = Boolean(payload?.has_more);
      if (payload?.next_cursor) state.transitionCursors[state.transitionPage] = payload.next_cursor;
      updateTransitionPagination();
    }

    function buildStatisticsUrl(endpoint, extraParams = {}) {
      const bounds = periodBounds(el.period.value, el.dateFrom.value, el.dateTo.value, new Date(), state.appTimezone);
      if (bounds.error) return { error: bounds.error };
      const query = new URLSearchParams();
      const userId = String(el.employee.value || "").trim();
      if (userId) query.set("user_id", userId);
      for (const [key, value] of Object.entries(bounds.params)) query.set(key, value);
      for (const [key, value] of Object.entries(extraParams)) {
        if (value != null && value !== "") query.set(key, value);
      }
      const suffix = query.toString();
      return { url: "/api/v1/management/statistics/" + endpoint + (suffix ? "?" + suffix : "") };
    }

    async function loadTransitionsPage() {
      const activeTab = state.tab;
      const requestGeneration = state.generation;
      const transitionGeneration = ++state.transitionGeneration;
      const pageSize = currentTransitionPageSize();
      el.transitionsTableBody.innerHTML = "";
      el.transitionsEmpty.hidden = false;
      el.transitionsEmpty.textContent = "Загрузка...";
      updateTransitionPagination();
      const built = buildStatisticsUrl("status-transitions", {
        limit: pageSize,
        cursor: state.transitionCursors[state.transitionPage - 1],
      });
      if (built.error) {
        el.transitionsEmpty.textContent = "";
        setValidation(built.error);
        return;
      }
      try {
        const payload = await api(built.url, state.controller.signal);
        if (
          state.destroyed
          || requestGeneration !== state.generation
          || transitionGeneration !== state.transitionGeneration
          || state.tab !== activeTab
        ) return;
        renderTransitions(payload);
        const measuredPageSize = calculateTransitionPageSize();
        if (measuredPageSize !== pageSize) {
          state.transitionPageSize = measuredPageSize;
          resetTransitionPagination();
          loadTransitionsPage();
        }
      } catch (error) {
        if (state.destroyed || requestGeneration !== state.generation || transitionGeneration !== state.transitionGeneration || state.tab !== activeTab || error.name === "AbortError") return;
        el.transitionsTableBody.innerHTML = "";
        el.transitionsEmpty.hidden = false;
        el.transitionsEmpty.textContent = "Не удалось загрузить лог смен статусов. Повторите попытку.";
      }
    }

    async function loadActiveTab() {
      const activeTab = state.tab;
      const endpoint = activeTab === "statuses" ? "statuses" : "work-time";
      const requestGeneration = clearActiveResults("Загрузка...");
      setStatus("Загрузка...");
      const built = buildStatisticsUrl(endpoint);
      if (activeTab === "statuses") resetTransitionPagination();
      if (built.error) {
        panelElements(activeTab).empty.textContent = "";
        if (activeTab === "statuses") el.transitionsEmpty.textContent = "";
        setStatus("");
        setValidation(built.error);
        return;
      }
      setValidation("");
      try {
        const transitionsPromise = activeTab === "statuses" ? loadTransitionsPage() : null;
        const payload = await api(built.url, state.controller.signal);
        if (state.destroyed || requestGeneration !== state.generation || state.tab !== activeTab) return;
        if (activeTab === "work-time") renderWorkTime(payload);
        else {
          renderStatuses(payload);
          await transitionsPromise;
        }
      } catch (error) {
        if (state.destroyed || requestGeneration !== state.generation || state.tab !== activeTab || error.name === "AbortError") return;
        panelElements(activeTab).empty.textContent = "";
        if (activeTab === "statuses") el.transitionsEmpty.textContent = "";
        setStatus("Не удалось загрузить статистику. Повторите попытку.");
      }
    }

    function selectTab(tab) {
      state.tab = tab;
      if (tab === "statuses") resetTransitionPagination();
      for (const button of el.tabs) button.classList.toggle("active", button.dataset.statisticsTab === tab);
      for (const panel of el.panels) panel.hidden = panel.dataset.statisticsPanel !== tab;
      if (el.medianHelp) el.medianHelp.hidden = tab !== "work-time";
      loadActiveTab();
    }

    async function start() {
      try {
        const sidebar = await window.HelpdeskSidebar.init();
        if (state.destroyed) return;
        if (sidebar.role !== "admin") {
          setStatus("Доступ только для администратора");
          return;
        }
        state.appTimezone = sidebar.appTimezone || "UTC";
        el.period.value = DEFAULT_PERIOD;
        resetCustomDates();
        updateCustomVisibility();
        setStatus("Загрузка...");
        const users = await api("/api/v1/settings/users", state.controller.signal);
        if (state.destroyed) return;
        renderUsers(Array.isArray(users) ? users : []);
        await loadActiveTab();
      } catch (error) {
        if (state.destroyed || error.name === "AbortError") return;
        setStatus("Не удалось загрузить страницу. Обновите страницу.");
      }
    }

    for (const button of el.tabs) {
      listen(button, "click", () => selectTab(button.dataset.statisticsTab));
    }
    for (const control of [el.employee, el.period, el.dateFrom, el.dateTo]) {
      listen(control, "change", () => {
        updateCustomVisibility();
        resetTransitionPagination();
        loadActiveTab();
      });
    }
    if (el.transitionsPrev) {
      listen(el.transitionsPrev, "click", () => {
        if (state.transitionPage <= 1) return;
        state.transitionPage -= 1;
        state.transitionHasMore = true;
        loadTransitionsPage();
      });
    }
    if (el.transitionsNext) {
      listen(el.transitionsNext, "click", () => {
        if (!state.transitionHasMore) return;
        state.transitionPage += 1;
        state.transitionHasMore = false;
        loadTransitionsPage();
      });
    }
    if (el.transitionsViewport && window.ResizeObserver) {
      state.transitionResizeObserver = new window.ResizeObserver(scheduleTransitionPageSizeRefresh);
      state.transitionResizeObserver.observe(el.transitionsViewport);
    }

    start();

    return () => {
      state.destroyed = true;
      state.controller.abort();
      while (listeners.length) listeners.pop()();
      if (state.transitionResizeTimer != null) clearTimeout(state.transitionResizeTimer);
      state.transitionResizeObserver?.disconnect?.();
    };
  }

  window.ManagementStatisticsTestHooks = {
    addCalendarDays,
    calendarDate,
    durationLabel,
    isoBoundaryInTimezone,
    periodBounds,
    initManagementStatisticsPage,
  };

  window.HelpdeskAppShell?.registerPage("management-statistics", initManagementStatisticsPage);
})();
