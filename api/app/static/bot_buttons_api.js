(function initBotButtonsApi(global) {
  function escapeHtml(v) {
    return String(v ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { accept: "application/json", ...(options.headers || {}) },
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
    if (!response.ok) throw new Error(payload?.detail?.error?.message || "Ошибка запроса");
    return payload;
  }

  function yesNo(v) {
    return v ? "да" : "нет";
  }

  function safeText(v) {
    const text = String(v ?? "").trim();
    return text || "не задано";
  }

  function nodeKindLabel(kind, workflowType) {
    if (kind === "submenu" || kind === "menu_group") return "раздел";
    if (kind === "group_task_parent") return "group_task";
    if (workflowType === "ticket") return "заявка";
    if (workflowType === "task") return "задача";
    if (workflowType === "auto") return "auto";
    if (workflowType === "group_task_child") return "child task";
    return "раздел";
  }

  function renderInfoRow(label, value) {
    return `<div class="bot-info-row"><div class="bot-info-label">${escapeHtml(label)}</div><div class="bot-info-value">${escapeHtml(value)}</div></div>`;
  }

  global.BotButtonsApi = {
    api,
    escapeHtml,
    safeText,
    yesNo,
    nodeKindLabel,
    renderInfoRow,
  };
})(window);

