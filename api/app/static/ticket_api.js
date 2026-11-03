(function initTicketApi(global) {
  function headers(extra = {}) {
    return { accept: "application/json", ...extra };
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: headers(options.headers || {}),
    });
    if (response.status === 401) {
      window.location.href = "/login";
      throw new Error("Требуется авторизация");
    }
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
      const detail = payload?.detail?.error?.message || raw || "Ошибка запроса";
      const error = new Error(detail);
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  async function apiPatch(path, body) {
    return api(path, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  async function apiPost(path, body = null) {
    const options = { method: "POST" };
    if (body !== null) {
      options.headers = { "content-type": "application/json" };
      options.body = JSON.stringify(body);
    }
    return api(path, options);
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function fmtDate(dateIso) {
    if (!dateIso) return "-";
    return new Date(dateIso).toLocaleString("ru-RU", { hour12: false });
  }

  function minutesLeftToDue(dueAtIso) {
    if (!dueAtIso) return null;
    const raw = String(dueAtIso).trim();
    const hasZone = /(?:Z|[+-]\d{2}:\d{2})$/i.test(raw);
    const normalized = hasZone ? raw : `${raw}Z`;
    const dueMs = new Date(normalized).getTime();
    if (!Number.isFinite(dueMs)) return null;
    const diffMs = dueMs - Date.now();
    if (diffMs <= 0) return 0;
    return Math.ceil(diffMs / 60000);
  }

  function fmtMessageStamp(dateIso) {
    if (!dateIso) return "";
    return new Date(dateIso).toLocaleString("ru-RU", {
      day: "2-digit", month: "2-digit", year: "numeric",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
    });
  }

  function formatFileSize(bytes) {
    const num = Number(bytes || 0);
    if (!Number.isFinite(num) || num <= 0) return "0 Б";
    if (num >= 1024 * 1024) return `${(num / (1024 * 1024)).toFixed(1)} МБ`;
    if (num >= 1024) return `${Math.round(num / 1024)} КБ`;
    return `${Math.round(num)} Б`;
  }

  function linkifyText(value) {
    const raw = String(value || "");
    const escaped = escapeHtml(raw);
    return escaped.replace(
      /(https?:\/\/[^\s<>"']+)/gi,
      '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>',
    );
  }

  function renderTextWithLinks(container, text) {
    if (!container) return;
    container.textContent = "";
    const source = String(text || "");
    const urlRegex = /(https?:\/\/[^\s<>"']+)/gi;
    let cursor = 0;
    let match;
    while ((match = urlRegex.exec(source)) !== null) {
      if (match.index > cursor) {
        container.appendChild(document.createTextNode(source.slice(cursor, match.index)));
      }
      const url = match[0];
      const link = document.createElement("a");
      link.href = url;
      link.textContent = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      container.appendChild(link);
      cursor = match.index + url.length;
    }
    if (cursor < source.length) {
      container.appendChild(document.createTextNode(source.slice(cursor)));
    }
  }

  global.TicketApi = {
    api,
    apiPatch,
    apiPost,
    headers,
    escapeHtml,
    fmtDate,
    fmtMessageStamp,
    formatFileSize,
    linkifyText,
    renderTextWithLinks,
    minutesLeftToDue,
  };
})(window);
