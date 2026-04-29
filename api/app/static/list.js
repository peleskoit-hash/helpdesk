const params = new URLSearchParams(window.location.search);
const tabFromQuery = params.get("tab");

const state = {
  tab: tabFromQuery || localStorage.getItem("hd_tab") || "active",
  sort: localStorage.getItem("hd_sort") || "created_at",
  order: localStorage.getItem("hd_order") || "desc",
  role: localStorage.getItem("hd_role") || "agent",
  userId: localStorage.getItem("hd_user_id") || "00000000-0000-0000-0000-000000000001",
  agents: [],
  adminAssigneeIds: JSON.parse(localStorage.getItem("hd_admin_assignee_ids") || "[]"),
  selectedTicketIds: new Set(),
  visibleTicketIds: [],
  tickets: [],
  page: 1,
  pages: 1,
  total: 0,
  limit: 20,
  isLoading: false,
};

const endpointMap = {
  active: "/api/v1/tickets/active",
  unassigned: "/api/v1/tickets/unassigned",
  frozen: "/api/v1/tickets/frozen",
  closed: "/api/v1/tickets/closed",
};

const el = {
  tabButtons: [...document.querySelectorAll(".tab-btn")],
  sortSelect: document.getElementById("sortSelect"),
  orderSelect: document.getElementById("orderSelect"),
  roleSelect: document.getElementById("roleSelect"),
  agentSelect: document.getElementById("agentSelect"),
  agentSelectWrap: document.getElementById("agentSelectWrap"),
  searchInput: document.getElementById("searchInput"),
  ticketsTableBody: document.getElementById("ticketsTableBody"),
  ticketCount: document.getElementById("ticketCount"),
  pageCurrent: document.getElementById("pageCurrent"),
  pageTotal: document.getElementById("pageTotal"),
  prevPageBtn: document.getElementById("prevPageBtn"),
  nextPageBtn: document.getElementById("nextPageBtn"),
  emptyText: document.getElementById("emptyText"),
  statusText: document.getElementById("statusText"),
  bulkAssignBtn: document.getElementById("bulkAssignBtn"),
  selectAllTicketsBtn: document.getElementById("selectAllTicketsBtn"),
  assignModal: document.getElementById("assignModal"),
  closeAssignModalBtn: document.getElementById("closeAssignModalBtn"),
  confirmBulkAssignBtn: document.getElementById("confirmBulkAssignBtn"),
  bulkAssigneeSelect: document.getElementById("bulkAssigneeSelect"),
  errorModal: document.getElementById("errorModal"),
  errorText: document.getElementById("errorText"),
  closeErrorModalBtn: document.getElementById("closeErrorModalBtn"),
  copyErrorBtn: document.getElementById("copyErrorBtn"),
  okErrorBtn: document.getElementById("okErrorBtn"),
  statusButtons: [...document.querySelectorAll("[data-availability-status]")],
  adminOnly: [...document.querySelectorAll(".admin-only")],
  adminAgentFilter: document.getElementById("adminAgentFilter"),
  adminAgentList: document.getElementById("adminAgentList"),
  adminAssigneeCols: [...document.querySelectorAll(".admin-assignee-col")],
  adminSelectCols: [...document.querySelectorAll(".admin-select-col")],
  agentAvatar: document.querySelector(".agent-avatar"),
};

function setStatus(text) { el.statusText.textContent = text; }

function showError(message) {
  el.errorText.value = message || "Неизвестная ошибка";
  el.errorModal.classList.remove("hidden");
}

function closeError() {
  el.errorModal.classList.add("hidden");
}

function headers() {
  return {
    "x-user-role": state.role,
    "x-user-id": state.userId,
    "accept": "application/json",
  };
}

async function api(path) {
  const response = await fetch(path, { headers: headers() });
  const payload = await response.json();
  if (!response.ok) {
    const detail = payload?.detail?.error?.message || "Ошибка запроса";
    throw new Error(detail);
  }
  return payload;
}

async function apiWrite(path, body) {
  const response = await fetch(path, {
    method: "PATCH",
    headers: {
      ...headers(),
      "content-type": "application/json",
    },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok) {
    const detail = payload?.detail?.error?.message || "Ошибка запроса";
    throw new Error(detail);
  }
  return payload;
}

async function apiPost(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      ...headers(),
      "content-type": "application/json",
    },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok) {
    const detail = payload?.detail?.error?.message || "Ошибка запроса";
    throw new Error(detail);
  }
  return payload;
}

function fmtDate(dateIso) {
  if (!dateIso) return "-";
  const d = new Date(dateIso);
  return d.toLocaleString("ru-RU", { hour12: false });
}

function ageText(dateIso) {
  const diffMs = Date.now() - new Date(dateIso).getTime();
  const min = Math.max(0, Math.floor(diffMs / 60000));
  if (min < 60) return `${min}м`;
  const h = Math.floor(min / 60);
  return `${h}ч ${min % 60}м`;
}

function applySearch(items) {
  const q = (el.searchInput.value || "").trim().toLowerCase();
  if (!q) return items;
  return items.filter((t) =>
    (t.subject || "").toLowerCase().includes(q) ||
    (t.client_name || "").toLowerCase().includes(q)
  );
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function lastReplyText(ticket) {
  if (!ticket.last_reply_at) return "-";
  const who = ticket.last_reply_by || "-";
  return `${who} (${fmtDate(ticket.last_reply_at)})`;
}

function intakeBadge(ticket) {
  if (ticket.workflow_type === "auto") return " [авто]";
  if (ticket.intake_state === "collecting") return " [сбор данных]";
  if (ticket.intake_state === "cancelled") return " [отменено]";
  return "";
}

function assigneeOptionsHtml(selectedId) {
  const options = [`<option value="" ${selectedId ? "" : "selected"}>нераспределён</option>`];
  for (const agent of state.agents) {
    options.push(
      `<option value="${escapeHtml(agent.id)}" ${agent.id === selectedId ? "selected" : ""}>${escapeHtml(agent.full_name)}</option>`
    );
  }
  return options.join("");
}

function renderBulkAssigneeOptions() {
  el.bulkAssigneeSelect.innerHTML = assigneeOptionsHtml("");
}

function updateBulkAssignUi() {
  const isAdmin = state.role === "admin";
  const selectedCount = state.selectedTicketIds.size;
  el.bulkAssignBtn.classList.toggle("hidden", !isAdmin || selectedCount === 0);
  if (selectedCount > 0) {
    el.bulkAssignBtn.textContent = `Назначить ответственно (${selectedCount})`;
  } else {
    el.bulkAssignBtn.textContent = "Назначить ответственно";
  }

  if (el.selectAllTicketsBtn) {
    const allVisibleSelected = state.visibleTicketIds.length > 0
      && state.visibleTicketIds.every((id) => state.selectedTicketIds.has(id));
    el.selectAllTicketsBtn.classList.toggle("active", allVisibleSelected);
  }
}

async function assignTickets(ticketIds, assigneeId) {
  if (!ticketIds.length) return;
  setStatus("Назначаю ответственного...");
  await apiPost("/api/v1/tickets/assign", {
    ticket_ids: ticketIds,
    assignee_id: assigneeId || null,
  });
  for (const ticketId of ticketIds) {
    state.selectedTicketIds.delete(ticketId);
  }
  await loadTickets();
  setStatus("Ответственный назначен");
}

function openAssignModal() {
  renderBulkAssigneeOptions();
  el.assignModal.classList.remove("hidden");
}

function closeAssignModal() {
  el.assignModal.classList.add("hidden");
}

function renderTable() {
  const filtered = applySearch(state.tickets);
  state.visibleTicketIds = filtered.map((t) => t.id);
  el.ticketsTableBody.innerHTML = "";
  el.ticketCount.textContent = String(state.total);
  el.pageCurrent.textContent = String(state.page);
  el.pageTotal.textContent = String(state.pages);
  el.prevPageBtn.disabled = state.page <= 1;
  el.nextPageBtn.disabled = state.page >= state.pages;
  el.emptyText.style.display = filtered.length ? "none" : "block";

  for (const t of filtered) {
    const tr = document.createElement("tr");
    const rowClasses = ["row-click", "ticket-row"];
    if (state.tab === "active" || t.status === "new") rowClasses.push("ticket-row-new");
    if (state.tab === "unassigned" || t.status === "unassigned") rowClasses.push("ticket-row-unassigned");
    if (state.tab === "active" && !t.seen_by_agent) rowClasses.push("ticket-row-fresh");
    if (t.is_frozen || state.tab === "frozen") rowClasses.push("ticket-row-frozen");
    if (t.status === "closed" || state.tab === "closed") rowClasses.push("ticket-row-closed");
    tr.className = rowClasses.join(" ");
    const selectCell = state.role === "admin"
      ? `<td class="ticket-check-cell"><input class="ticket-check" data-ticket-id="${escapeHtml(t.id)}" type="checkbox" ${state.selectedTicketIds.has(t.id) ? "checked" : ""} /></td>`
      : "";
    const assigneeCell = state.role === "admin"
      ? `<td><select class="row-assignee-select" data-ticket-id="${escapeHtml(t.id)}">${assigneeOptionsHtml(t.assignee_id)}</select></td>`
      : "";
    tr.innerHTML = `
      ${selectCell}
      <td>${escapeHtml((t.subject || "") + intakeBadge(t))}</td>
      <td>${escapeHtml(t.client_name || "")}</td>
      ${assigneeCell}
      <td>${lastReplyText(t)}</td>
      <td>${fmtDate(t.created_at)}</td>
      <td>${ageText(t.created_at)}</td>
      <td>${fmtDate(t.updated_at)}</td>
    `;
    tr.addEventListener("click", () => {
      const role = encodeURIComponent(state.role);
      const tab = encodeURIComponent(state.tab);
      const userId = encodeURIComponent(state.userId);
      window.location.href = `/tickets/${t.id}?role=${role}&tab=${tab}&user_id=${userId}`;
    });
    el.ticketsTableBody.appendChild(tr);
  }

  el.ticketsTableBody.querySelectorAll(".ticket-check").forEach((checkbox) => {
    checkbox.addEventListener("click", (event) => {
      event.stopPropagation();
      const ticketId = checkbox.dataset.ticketId;
      if (checkbox.checked) {
        state.selectedTicketIds.add(ticketId);
      } else {
        state.selectedTicketIds.delete(ticketId);
      }
      updateBulkAssignUi();
    });
  });

  el.ticketsTableBody.querySelectorAll(".row-assignee-select").forEach((select) => {
    select.addEventListener("click", (event) => event.stopPropagation());
    select.addEventListener("change", async (event) => {
      event.stopPropagation();
      try {
        await assignTickets([select.dataset.ticketId], select.value || null);
      } catch (e) {
        showError(e.message);
        await loadTickets();
      }
    });
  });
  updateBulkAssignUi();
}

function applyRoleUi() {
  const isAdmin = state.role === "admin";
  el.adminOnly.forEach((node) => {
    node.classList.toggle("hidden", !isAdmin);
  });
  el.adminAssigneeCols.forEach((node) => {
    node.classList.toggle("hidden", !isAdmin);
  });
  el.adminSelectCols.forEach((node) => {
    node.classList.toggle("hidden", !isAdmin);
  });
  if (el.agentSelectWrap) {
    el.agentSelectWrap.classList.toggle("hidden", false);
  }
  if (!isAdmin && state.tab === "unassigned") {
    state.tab = "active";
    localStorage.setItem("hd_tab", state.tab);
  }
  el.tabButtons.forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === state.tab);
  });
  if (!isAdmin) {
    state.selectedTicketIds.clear();
  }
  updateBulkAssignUi();
}

function renderAgentSelect() {
  el.agentSelect.innerHTML = "";
  for (const agent of state.agents) {
    const option = document.createElement("option");
    option.value = agent.id;
    option.textContent = agent.full_name;
    el.agentSelect.appendChild(option);
  }
  if (!state.agents.some((agent) => agent.id === state.userId) && state.agents[0]) {
    state.userId = state.agents[0].id;
    localStorage.setItem("hd_user_id", state.userId);
  }
  el.agentSelect.value = state.userId;
}

function renderAdminAgentFilter() {
  el.adminAgentList.innerHTML = "";
  for (const agent of state.agents) {
    const label = document.createElement("label");
    const checked = state.adminAssigneeIds.includes(agent.id);
    label.className = `admin-agent-item${checked ? " active" : ""}`;
    label.innerHTML = `
      <input type="checkbox" value="${escapeHtml(agent.id)}" ${checked ? "checked" : ""} />
      <span>${escapeHtml(agent.full_name)}</span>
    `;
    label.addEventListener("click", async (event) => {
      event.preventDefault();
      const isSelected = state.adminAssigneeIds.includes(agent.id);
      if (isSelected) {
        state.adminAssigneeIds = state.adminAssigneeIds.filter((id) => id !== agent.id);
      } else {
        state.adminAssigneeIds = [...state.adminAssigneeIds, agent.id];
      }
      localStorage.setItem("hd_admin_assignee_ids", JSON.stringify(state.adminAssigneeIds));
      renderAdminAgentFilter();
      resetToFirstPage();
      await loadTickets();
    });
    el.adminAgentList.appendChild(label);
  }
}

async function loadAgents() {
  const agents = await api("/api/v1/users/agents");
  state.agents = agents || [];
  renderAgentSelect();
  renderAdminAgentFilter();
}

function renderAvailabilityStatus(status) {
  el.statusButtons.forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.availabilityStatus === status);
  });
}

async function loadMe() {
  try {
    const me = await api("/api/v1/users/me");
    renderAvailabilityStatus(me.availability_status);
    if (el.agentAvatar) {
      const name = me.full_name || "A";
      el.agentAvatar.textContent = name.trim().slice(0, 1).toUpperCase();
      el.agentAvatar.title = name;
    }
  } catch (e) {
    showError(`Ошибка статуса: ${e.message}`);
  }
}

async function updateAvailabilityStatus(status) {
  try {
    renderAvailabilityStatus(status);
    const me = await apiWrite("/api/v1/users/me/status", { availability_status: status });
    renderAvailabilityStatus(me.availability_status);
    setStatus("Статус обновлён");
  } catch (e) {
    showError(`Ошибка статуса: ${e.message}`);
    await loadMe();
  }
}

async function loadTickets(options = {}) {
  if (state.isLoading) return;
  state.isLoading = true;
  const silent = Boolean(options.silent);
  if (!silent) setStatus("Загружаю список...");
  const base = endpointMap[state.tab];
  const queryParams = new URLSearchParams({
    sort: state.sort,
    order: state.order,
    page: String(state.page),
    limit: String(state.limit),
  });
  if (state.role === "admin" && state.tab !== "unassigned") {
    state.adminAssigneeIds.forEach((id) => queryParams.append("assignee_ids", id));
  }
  try {
    const payload = await api(`${base}?${queryParams.toString()}`);

    state.tickets = payload.items || [];
    state.total = payload.total || 0;
    state.pages = payload.pages || 1;
    state.page = payload.page || 1;

    renderTable();
    setStatus(`Загружено: ${state.tickets.length} из ${state.total}`);
  } catch (e) {
    if (!silent) setStatus(`Ошибка: ${e.message}`);
  } finally {
    state.isLoading = false;
  }
}

function resetToFirstPage() {
  state.page = 1;
}

function init() {
  if (!endpointMap[state.tab]) state.tab = "active";
  applyRoleUi();
  localStorage.setItem("hd_tab", state.tab);

  el.sortSelect.value = state.sort;
  el.orderSelect.value = state.order;
  el.roleSelect.value = state.role;
  el.agentSelect.value = state.userId;

  el.tabButtons.forEach((btn) => {
    if (btn.dataset.tab === state.tab) btn.classList.add("active");
    else btn.classList.remove("active");

    btn.addEventListener("click", async () => {
      if (btn.classList.contains("hidden")) return;
      el.tabButtons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.tab = btn.dataset.tab;
      localStorage.setItem("hd_tab", state.tab);
      state.selectedTicketIds.clear();
      resetToFirstPage();
      await loadTickets();
    });
  });

  el.sortSelect.addEventListener("change", async () => {
    state.sort = el.sortSelect.value;
    localStorage.setItem("hd_sort", state.sort);
    resetToFirstPage();
    await loadTickets();
  });

  el.orderSelect.addEventListener("change", async () => {
    state.order = el.orderSelect.value;
    localStorage.setItem("hd_order", state.order);
    resetToFirstPage();
    await loadTickets();
  });

  el.roleSelect.addEventListener("change", async () => {
    state.role = el.roleSelect.value;
    localStorage.setItem("hd_role", state.role);
    applyRoleUi();
    state.selectedTicketIds.clear();
    resetToFirstPage();
    await loadMe();
    await loadTickets();
  });

  el.agentSelect.addEventListener("change", async () => {
    state.userId = el.agentSelect.value;
    localStorage.setItem("hd_user_id", state.userId);
    state.selectedTicketIds.clear();
    resetToFirstPage();
    await loadMe();
    await loadTickets();
  });

  el.statusButtons.forEach((btn) => {
    btn.addEventListener("click", async () => {
      await updateAvailabilityStatus(btn.dataset.availabilityStatus);
    });
  });

  el.selectAllTicketsBtn.addEventListener("click", () => {
    const allVisibleSelected = state.visibleTicketIds.length > 0
      && state.visibleTicketIds.every((id) => state.selectedTicketIds.has(id));
    for (const ticketId of state.visibleTicketIds) {
      if (allVisibleSelected) state.selectedTicketIds.delete(ticketId);
      else state.selectedTicketIds.add(ticketId);
    }
    renderTable();
  });

  el.bulkAssignBtn.addEventListener("click", openAssignModal);
  el.closeAssignModalBtn.addEventListener("click", closeAssignModal);
  el.assignModal.addEventListener("click", (event) => {
    if (event.target === el.assignModal) closeAssignModal();
  });
  el.confirmBulkAssignBtn.addEventListener("click", async () => {
    try {
      const ticketIds = [...state.selectedTicketIds];
      await assignTickets(ticketIds, el.bulkAssigneeSelect.value || null);
      closeAssignModal();
    } catch (e) {
      showError(e.message);
    }
  });

  el.closeErrorModalBtn.addEventListener("click", closeError);
  el.okErrorBtn.addEventListener("click", closeError);
  el.errorModal.addEventListener("click", (event) => {
    if (event.target === el.errorModal) closeError();
  });
  el.copyErrorBtn.addEventListener("click", async () => {
    el.errorText.select();
    try {
      await navigator.clipboard.writeText(el.errorText.value);
    } catch {
      document.execCommand("copy");
    }
    setStatus("Текст ошибки скопирован");
  });

  el.searchInput.addEventListener("input", renderTable);

  el.prevPageBtn.addEventListener("click", async () => {
    if (state.page <= 1) return;
    state.page -= 1;
    await loadTickets();
  });

  el.nextPageBtn.addEventListener("click", async () => {
    if (state.page >= state.pages) return;
    state.page += 1;
    await loadTickets();
  });

  window.setInterval(() => {
    if (document.hidden) return;
    loadTickets({ silent: true });
  }, 5000);
}

(async function run() {
  init();
  await loadAgents();
  await loadMe();
  await loadTickets();
})();
