const state = {
  tab: "active",
  sort: "created_at",
  order: "desc",
  role: "agent",
  userId: "00000000-0000-0000-0000-000000000001",
  tickets: [],
  selectedTicketId: null,
};

const endpointMap = {
  active: "/api/v1/tickets/active",
  frozen: "/api/v1/tickets/frozen",
  closed: "/api/v1/tickets/closed",
};

const el = {
  tabButtons: [...document.querySelectorAll(".tab-btn")],
  sortSelect: document.getElementById("sortSelect"),
  orderSelect: document.getElementById("orderSelect"),
  roleSelect: document.getElementById("roleSelect"),
  searchInput: document.getElementById("searchInput"),
  ticketsTableBody: document.getElementById("ticketsTableBody"),
  ticketCount: document.getElementById("ticketCount"),
  emptyText: document.getElementById("emptyText"),
  cardEmpty: document.getElementById("cardEmpty"),
  cardWrap: document.getElementById("cardWrap"),
  ticketSubject: document.getElementById("ticketSubject"),
  ticketMeta: document.getElementById("ticketMeta"),
  messages: document.getElementById("messages"),
  messageInput: document.getElementById("messageInput"),
  freezeUntil: document.getElementById("freezeUntil"),
  sendReplyBtn: document.getElementById("sendReplyBtn"),
  sendCommentBtn: document.getElementById("sendCommentBtn"),
  greetingBtn: document.getElementById("greetingBtn"),
  freezeBtn: document.getElementById("freezeBtn"),
  unfreezeBtn: document.getElementById("unfreezeBtn"),
  closeBtn: document.getElementById("closeBtn"),
  statusText: document.getElementById("statusText"),
};

function headers() {
  return {
    "x-user-role": state.role,
    "x-user-id": state.userId,
    "accept": "application/json",
  };
}

function setStatus(text) {
  el.statusText.textContent = text;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      ...headers(),
      ...(options.headers || {}),
    },
  });

  const contentType = response.headers.get("content-type") || "";
  const isJson = contentType.includes("application/json");
  const payload = isJson ? await response.json() : await response.text();

  if (!response.ok) {
    const detail = payload?.detail?.error?.message || payload?.error?.message || response.statusText;
    throw new Error(detail || "Ошибка запроса");
  }
  return payload;
}

function ageText(dateIso) {
  const diffMs = Date.now() - new Date(dateIso).getTime();
  const min = Math.max(0, Math.floor(diffMs / 60000));
  if (min < 60) return `${min}м`;
  const h = Math.floor(min / 60);
  const rem = min % 60;
  return `${h}ч ${rem}м`;
}

function fmtDate(dateIso) {
  const d = new Date(dateIso);
  return d.toLocaleString("ru-RU", { hour12: false });
}

function applySearch(items) {
  const q = el.searchInput.value.trim().toLowerCase();
  if (!q) return items;
  return items.filter((t) =>
    (t.subject || "").toLowerCase().includes(q) ||
    (t.client_name || "").toLowerCase().includes(q)
  );
}

function renderTable() {
  const filtered = applySearch(state.tickets);
  el.ticketsTableBody.innerHTML = "";
  el.ticketCount.textContent = String(filtered.length);
  el.emptyText.classList.toggle("hidden", filtered.length > 0);

  for (const t of filtered) {
    const tr = document.createElement("tr");
    tr.className = "row-click";
    tr.innerHTML = `
      <td>${t.subject || ""}</td>
      <td>${t.client_name || ""}</td>
      <td>${fmtDate(t.created_at)}</td>
      <td>${ageText(t.created_at)}</td>
      <td>${fmtDate(t.updated_at)}</td>
    `;
    tr.addEventListener("click", () => openTicket(t.id));
    el.ticketsTableBody.appendChild(tr);
  }
}

function isGreetingAllowedNow() {
  const now = new Date();
  const hh = now.getHours();
  const mm = now.getMinutes();
  if (hh < 5) return false;
  if (hh > 19) return false;
  if (hh === 19 && mm > 30) return false;
  return true;
}

function updateActionState(card) {
  const hasAgentMessage = card.messages.some((m) => m.message_type === "agent");
  const isClosed = card.status === "closed";
  const hasAnyMessage = card.messages.length > 0;

  el.sendReplyBtn.disabled = isClosed;
  el.sendCommentBtn.disabled = isClosed;
  el.greetingBtn.disabled = isClosed || !isGreetingAllowedNow();
  el.freezeBtn.disabled = isClosed || card.is_frozen;
  el.unfreezeBtn.disabled = isClosed || !card.is_frozen;
  el.closeBtn.disabled = isClosed || !hasAgentMessage || !hasAnyMessage;
}

function renderCard(card) {
  el.cardEmpty.classList.add("hidden");
  el.cardWrap.classList.remove("hidden");

  el.ticketSubject.textContent = card.subject;
  el.ticketMeta.textContent = `ID: ${card.id} | status: ${card.status} | frozen: ${card.is_frozen ? "yes" : "no"} | client: ${card.client_name}`;
  el.messages.innerHTML = "";

  for (const m of card.messages) {
    const div = document.createElement("div");
    const cls = m.message_type === "internal" ? "internal" : m.message_type === "system" ? "system" : "";
    div.className = `message ${cls}`.trim();
    div.innerHTML = `
      <div><strong>${m.message_type}</strong></div>
      <div>${m.body}</div>
      <div class="status">${fmtDate(m.created_at)}</div>
    `;
    el.messages.appendChild(div);
  }

  updateActionState(card);
}

async function loadTickets() {
  setStatus("Загружаю список...");
  const base = endpointMap[state.tab];
  const query = `?sort=${encodeURIComponent(state.sort)}&order=${encodeURIComponent(state.order)}`;
  state.tickets = await api(`${base}${query}`);
  renderTable();
  setStatus(`Загружено: ${state.tickets.length}`);
}

async function openTicket(ticketId) {
  state.selectedTicketId = ticketId;
  setStatus("Загружаю карточку...");
  const card = await api(`/api/v1/tickets/${ticketId}`);
  renderCard(card);
  setStatus("Карточка загружена");
}

async function ticketAction(action, body = null) {
  if (!state.selectedTicketId) return;
  const opts = { method: "POST" };
  if (body !== null) {
    opts.headers = { "content-type": "application/json" };
    opts.body = JSON.stringify(body);
  }
  try {
    setStatus("Выполняю действие...");
    await api(`/api/v1/tickets/${state.selectedTicketId}/${action}`, opts);
    await loadTickets();
    await openTicket(state.selectedTicketId);
    if (action === "reply" || action === "comment") el.messageInput.value = "";
    setStatus("Готово");
  } catch (e) {
    setStatus(`Ошибка: ${e.message}`);
  }
}

function initEvents() {
  el.tabButtons.forEach((btn) => {
    btn.addEventListener("click", async () => {
      el.tabButtons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.tab = btn.dataset.tab;
      state.selectedTicketId = null;
      el.cardWrap.classList.add("hidden");
      el.cardEmpty.classList.remove("hidden");
      await loadTickets();
    });
  });

  el.sortSelect.addEventListener("change", async () => {
    state.sort = el.sortSelect.value;
    await loadTickets();
  });

  el.orderSelect.addEventListener("change", async () => {
    state.order = el.orderSelect.value;
    await loadTickets();
  });

  el.roleSelect.addEventListener("change", async () => {
    state.role = el.roleSelect.value;
    await loadTickets();
  });

  el.searchInput.addEventListener("input", () => renderTable());

  el.sendReplyBtn.addEventListener("click", () => {
    const text = el.messageInput.value.trim();
    if (!text) return;
    ticketAction("reply", { text });
  });

  el.sendCommentBtn.addEventListener("click", () => {
    const text = el.messageInput.value.trim();
    if (!text) return;
    ticketAction("comment", { text });
  });

  el.greetingBtn.addEventListener("click", () => ticketAction("greeting"));

  el.freezeBtn.addEventListener("click", () => {
    const raw = el.freezeUntil.value;
    const freezeUntil = raw ? new Date(raw).toISOString() : null;
    ticketAction("freeze", { freeze_until: freezeUntil });
  });

  el.unfreezeBtn.addEventListener("click", () => ticketAction("unfreeze"));
  el.closeBtn.addEventListener("click", () => ticketAction("close"));
}

(async function main() {
  initEvents();
  await loadTickets();
})();
