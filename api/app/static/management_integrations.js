(function () {
  const PAGE_SIZE = 50;
  const EMPTY = "\u2014";

  const state = {
    eventType: "",
    result: "",
    createdFrom: "",
    timeFrom: "",
    createdTo: "",
    timeTo: "",
    limit: PAGE_SIZE,
    offset: 0,
    total: 0,
  };

  const EVENT_LABELS = {
    task_create: "Создание задачи",
    telegram_send: "Telegram",
  };
  const RESULT_LABELS = {
    success: "Успех",
    duplicate: "Дубликат",
    error: "Ошибка",
  };

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function formatCell(value) {
    const text = String(value ?? "").trim();
    return text || EMPTY;
  }

  function formatDateTime(value) {
    if (!value) return EMPTY;
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return EMPTY;
    return date.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "medium" });
  }

  function nextLocalDayStart(value) {
    if (!value) return "";
    const date = new Date(`${value}T00:00:00`);
    if (Number.isNaN(date.getTime())) return "";
    date.setDate(date.getDate() + 1);
    return date.toISOString();
  }

  function localDateTime(value, timeValue = "") {
    if (!value) return "";
    const time = String(timeValue || "").trim() || "00:00";
    const date = new Date(`${value}T${time}`);
    if (Number.isNaN(date.getTime())) return "";
    return date.toISOString();
  }

  function localCreatedTo(value, timeValue = "") {
    if (!value) return "";
    if (String(timeValue || "").trim()) return localDateTime(value, timeValue);
    return nextLocalDayStart(value);
  }

  function buildLogsUrl(options = {}) {
    const params = new URLSearchParams();
    params.set("limit", String(options.limit || PAGE_SIZE));
    params.set("offset", String(options.offset || 0));
    if (options.eventType) params.set("event_type", options.eventType);
    if (options.result) params.set("result", options.result);
    const createdFrom = localDateTime(options.createdFrom || "", options.timeFrom || "");
    const createdTo = localCreatedTo(options.createdTo || "", options.timeTo || "");
    if (createdFrom) params.set("created_from", createdFrom);
    if (createdTo) params.set("created_to", createdTo);
    return `/api/v1/management/integrations/logs?${params.toString()}`;
  }

  function paginationSummary(offset, limit, total, itemCount) {
    const safeTotal = Math.max(0, Number(total || 0));
    if (safeTotal === 0 || itemCount === 0) {
      return "Показано 0–0 из 0";
    }
    const start = Number(offset || 0) + 1;
    const end = Math.min(Number(offset || 0) + Number(itemCount || limit || 0), safeTotal);
    return `Показано ${start}–${end} из ${safeTotal}`;
  }

  async function api(path) {
    const response = await fetch(path, {
      headers: { accept: "application/json" },
      credentials: "include",
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
      status: root.querySelector("#integrationLogStatus"),
      eventType: root.querySelector("#integrationLogEventType"),
      result: root.querySelector("#integrationLogResult"),
      createdFrom: root.querySelector("#integrationLogCreatedFrom"),
      timeFrom: root.querySelector("#integrationLogTimeFrom"),
      createdTo: root.querySelector("#integrationLogCreatedTo"),
      timeTo: root.querySelector("#integrationLogTimeTo"),
      resetBtn: root.querySelector("#integrationLogResetBtn"),
      tableBody: root.querySelector("#integrationLogTableBody"),
      empty: root.querySelector("#integrationLogEmpty"),
      pageSummary: root.querySelector("#integrationLogPageSummary"),
      prevBtn: root.querySelector("#integrationLogPrevBtn"),
      nextBtn: root.querySelector("#integrationLogNextBtn"),
    };
  }

  function setStatus(root, message) {
    const node = els(root).status;
    if (node) node.textContent = message;
  }

  function eventDataHtml(item) {
    const parts = [];
    if (item.event_type === "task_create") {
      if (item.deal_id) parts.push(`Сделка: ${item.deal_id}`);
      if (item.task_id) parts.push(`Task ID: ${item.task_id}`);
    } else if (item.event_type === "telegram_send") {
      if (item.bitrix_id) parts.push(`Bitrix ID: ${item.bitrix_id}`);
      if (item.call_time) parts.push(`call_time: ${formatDateTime(item.call_time)}`);
      if (item.telegram_message_id) parts.push(`Telegram message ID: ${item.telegram_message_id}`);
    }
    if (!parts.length) return EMPTY;
    return parts.map((part) => `<div>${escapeHtml(part)}</div>`).join("");
  }

  function descriptionHtml(item) {
    const description = `<div>${escapeHtml(formatCell(item.description))}</div>`;
    if (item.event_type !== "task_create" || !String(item.task_id || "").trim()) {
      return description;
    }
    const href = `/tickets/${encodeURIComponent(item.task_id)}`;
    return `${description}<a class="integration-log-task-link" href="${href}">Открыть задачу</a>`;
  }

  function tableRowHtml(item) {
    const eventLabel = EVENT_LABELS[item.event_type] || item.event_type || EMPTY;
    const resultLabel = RESULT_LABELS[item.result] || item.result || EMPTY;
    const resultClass = item.result ? `integration-log-result-${item.result}` : "";
    return `
      <tr>
        <td>${escapeHtml(formatDateTime(item.created_at))}</td>
        <td>${escapeHtml(eventLabel)}</td>
        <td><span class="status integration-log-badge ${escapeHtml(resultClass)}">${escapeHtml(resultLabel)}</span></td>
        <td class="integration-log-code">${escapeHtml(formatCell(item.code))}</td>
        <td class="integration-log-event-data">${eventDataHtml(item)}</td>
        <td>${descriptionHtml(item)}</td>
      </tr>
    `;
  }

  function renderLogs(root, payload) {
    const el = els(root);
    const items = Array.isArray(payload?.items) ? payload.items : [];
    state.total = Number(payload?.total || 0);
    state.limit = Number(payload?.limit || state.limit || PAGE_SIZE);
    state.offset = Number(payload?.offset || state.offset || 0);
    el.tableBody.innerHTML = items.map(tableRowHtml).join("");
    if (el.empty) {
      el.empty.classList.toggle("hidden", items.length > 0);
      el.empty.textContent = "События не найдены";
    }
    if (el.pageSummary) {
      el.pageSummary.textContent = paginationSummary(state.offset, state.limit, state.total, items.length);
    }
    if (el.prevBtn) {
      el.prevBtn.disabled = state.offset <= 0;
    }
    if (el.nextBtn) {
      el.nextBtn.disabled = state.offset + items.length >= state.total;
    }
  }

  function readFilters(root) {
    const el = els(root);
    state.eventType = el.eventType?.value || "";
    state.result = el.result?.value || "";
    state.createdFrom = el.createdFrom?.value || "";
    state.timeFrom = el.timeFrom?.value || "";
    state.createdTo = el.createdTo?.value || "";
    state.timeTo = el.timeTo?.value || "";
  }

  async function loadLogs(root) {
    const el = els(root);
    if (el.empty) {
      el.empty.classList.remove("hidden");
      el.empty.textContent = "Загрузка...";
    }
    setStatus(root, "Загрузка...");
    const payload = await api(buildLogsUrl(state));
    renderLogs(root, payload);
    setStatus(root, `Найдено: ${payload?.total ?? 0}`);
  }

  function resetFilters(root) {
    const el = els(root);
    if (el.eventType) el.eventType.value = "";
    if (el.result) el.result.value = "";
    if (el.createdFrom) el.createdFrom.value = "";
    if (el.timeFrom) el.timeFrom.value = "";
    if (el.createdTo) el.createdTo.value = "";
    if (el.timeTo) el.timeTo.value = "";
    readFilters(root);
    state.offset = 0;
    return loadLogs(root);
  }

  function bind(root) {
    const el = els(root);
    [el.eventType, el.result, el.createdFrom, el.timeFrom, el.createdTo, el.timeTo].forEach((control) => {
      control?.addEventListener("change", () => {
        readFilters(root);
        state.offset = 0;
        loadLogs(root).catch(() => {
          renderLogs(root, { items: [], total: 0, limit: state.limit, offset: state.offset });
          setStatus(root, "Не удалось загрузить Integration Log");
        });
      });
    });
    el.resetBtn?.addEventListener("click", () => {
      resetFilters(root).catch(() => {
        renderLogs(root, { items: [], total: 0, limit: state.limit, offset: state.offset });
        setStatus(root, "Не удалось загрузить Integration Log");
      });
    });
    el.prevBtn?.addEventListener("click", () => {
      if (state.offset <= 0) return;
      state.offset = Math.max(0, state.offset - state.limit);
      loadLogs(root).catch(() => setStatus(root, "Не удалось загрузить Integration Log"));
    });
    el.nextBtn?.addEventListener("click", () => {
      if (state.offset + state.limit >= state.total) return;
      state.offset += state.limit;
      loadLogs(root).catch(() => setStatus(root, "Не удалось загрузить Integration Log"));
    });
  }

  async function initManagementIntegrationsPage({ root } = {}) {
    if (!root) return;
    bind(root);
    try {
      const sidebar = await window.HelpdeskSidebar?.init?.();
      if (sidebar && sidebar.role !== "admin") {
        setStatus(root, "Доступ только для администратора");
        return;
      }
      readFilters(root);
      await loadLogs(root);
    } catch {
      renderLogs(root, { items: [], total: 0, limit: state.limit, offset: state.offset });
      setStatus(root, "Не удалось загрузить Integration Log");
    }
  }

  window.ManagementIntegrationsPage = {
    buildLogsUrl,
    descriptionHtml,
    eventDataHtml,
    formatDateTime,
    localCreatedTo,
    localDateTime,
    nextLocalDayStart,
    paginationSummary,
    tableRowHtml,
  };

  window.HelpdeskAppShell?.registerPage?.("management-integrations", initManagementIntegrationsPage);
})();
