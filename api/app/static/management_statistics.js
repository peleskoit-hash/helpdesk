(function () {
  const EMPTY = "\u2014";
  const DEFAULT_PERIOD = "30days";

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

  function initManagementStatisticsPage({ root }) {
    const el = {
      status: root.querySelector("#managementStatisticsStatus"),
      employee: root.querySelector("#managementStatisticsEmployee"),
      period: root.querySelector("#managementStatisticsPeriod"),
      dateFrom: root.querySelector("#managementStatisticsDateFrom"),
      dateTo: root.querySelector("#managementStatisticsDateTo"),
      validation: root.querySelector("#managementStatisticsValidation"),
      summary: root.querySelector("#managementStatisticsSummary"),
      tableBody: root.querySelector("#managementStatisticsTableBody"),
      empty: root.querySelector("#managementStatisticsEmpty"),
      tabs: [...root.querySelectorAll("[data-statistics-tab]")],
      panels: [...root.querySelectorAll("[data-statistics-panel]")],
      customFields: [...root.querySelectorAll(".management-statistics-custom")],
    };
    const state = {
      appTimezone: "UTC",
      tab: "completed",
      generation: 0,
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

    function clearCompleted(message) {
      state.generation += 1;
      state.controller.abort();
      state.controller = new AbortController();
      el.tableBody.innerHTML = "";
      el.empty.hidden = false;
      el.empty.textContent = message || "Загрузка...";
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

    function renderUsers(users) {
      const current = el.employee.value || "";
      const options = ['<option value="">Все сотрудники</option>'];
      for (const user of users) {
        const inactive = user.is_active === false ? " (неактивен)" : "";
        options.push(`<option value="${escapeHtml(user.id)}">${escapeHtml(userLabel(user) + inactive)}</option>`);
      }
      el.employee.innerHTML = options.join("");
      el.employee.value = users.some((user) => user.id === current) ? current : "";
    }

    function rowHtml(item) {
      const deleted = item.full_name == null;
      const inactive = item.is_active === false;
      const name = deleted ? "Удалённый сотрудник" : item.full_name;
      const meta = deleted
        ? item.user_id || EMPTY
        : inactive
          ? "Неактивен"
          : "";
      return `
        <tr>
          <td>
            <div class="management-statistics-user-name">${escapeHtml(name)}</div>
            ${meta ? `<div class="management-statistics-user-muted">${escapeHtml(meta)}</div>` : ""}
          </td>
          <td>${Number(item.tickets || 0)}</td>
          <td>${Number(item.tasks || 0)}</td>
          <td>${Number(item.total || 0)}</td>
        </tr>
      `;
    }

    function renderCompleted(payload) {
      const total = payload?.total || {};
      const items = Array.isArray(payload?.items) ? payload.items : [];
      el.summary.textContent = `Всего выполнено: ${Number(total.total || 0)} · Заявки: ${Number(total.tickets || 0)} · Задачи: ${Number(total.tasks || 0)}`;
      el.tableBody.innerHTML = items.map(rowHtml).join("");
      el.empty.hidden = items.length > 0;
      el.empty.textContent = "За выбранный период выполненных объектов нет";
      setStatus("");
    }

    function buildCompletedUrl() {
      const bounds = periodBounds(el.period.value, el.dateFrom.value, el.dateTo.value, new Date(), state.appTimezone);
      if (bounds.error) return { error: bounds.error };
      const query = new URLSearchParams();
      const userId = String(el.employee.value || "").trim();
      if (userId) query.set("user_id", userId);
      for (const [key, value] of Object.entries(bounds.params)) query.set(key, value);
      const suffix = query.toString();
      return { url: "/api/v1/management/statistics/completed" + (suffix ? "?" + suffix : "") };
    }

    async function loadCompleted() {
      if (state.tab !== "completed") return;
      const requestGeneration = clearCompleted("Загрузка...");
      setStatus("Загрузка...");
      const built = buildCompletedUrl();
      if (built.error) {
        el.empty.textContent = "";
        setStatus("");
        setValidation(built.error);
        return;
      }
      setValidation("");
      try {
        const payload = await api(built.url, state.controller.signal);
        if (state.destroyed || requestGeneration !== state.generation) return;
        renderCompleted(payload);
      } catch (error) {
        if (state.destroyed || requestGeneration !== state.generation || error.name === "AbortError") return;
        el.empty.textContent = "";
        setStatus("Не удалось загрузить статистику. Повторите попытку.");
      }
    }

    function selectTab(tab) {
      state.tab = tab;
      for (const button of el.tabs) button.classList.toggle("active", button.dataset.statisticsTab === tab);
      for (const panel of el.panels) panel.hidden = panel.dataset.statisticsPanel !== tab;
      if (tab === "completed") loadCompleted();
      else setStatus("");
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
        await loadCompleted();
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
        loadCompleted();
      });
    }

    start();

    return () => {
      state.destroyed = true;
      state.controller.abort();
      while (listeners.length) listeners.pop()();
    };
  }

  window.ManagementStatisticsTestHooks = {
    addCalendarDays,
    calendarDate,
    isoBoundaryInTimezone,
    periodBounds,
    initManagementStatisticsPage,
  };

  window.HelpdeskAppShell?.registerPage("management-statistics", initManagementStatisticsPage);
})();
