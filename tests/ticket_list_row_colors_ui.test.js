const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const listSource = fs.readFileSync("api/app/static/list.js", "utf8");
const styles = fs.readFileSync("api/app/static/styles.css", "utf8");
const template = fs.readFileSync("api/app/templates/tickets.html", "utf8");
const appShellSource = fs.readFileSync("api/app/static/app_shell.js", "utf8");

function sourceBetween(source, start, end) {
  source = source.replaceAll("\r\n", "\n");
  const from = source.indexOf(start);
  assert.notEqual(from, -1, `start marker not found: ${start}`);
  const to = source.indexOf(end, from);
  assert.notEqual(to, -1, `end marker not found: ${end}`);
  return source.slice(from, to);
}

function loadColorClass() {
  const helperSource = [
    sourceBetween(listSource, "function ticketRowColorClass", "function tabKeyFromNode"),
    "globalThis.ticketRowColorClass = ticketRowColorClass;",
  ].join("\n");
  const context = vm.createContext({});
  vm.runInContext(helperSource, context);
  return context.ticketRowColorClass;
}

function loadTicketTypeInfo() {
  const helperSource = [
    sourceBetween(listSource, "function ticketTypeInfo", "function tabKeyFromNode"),
    "globalThis.ticketTypeInfo = ticketTypeInfo;",
  ].join("\n");
  const context = vm.createContext({});
  vm.runInContext(helperSource, context);
  return context.ticketTypeInfo;
}

test("ticket list row color classification follows entity type and status priority", () => {
  const colorClass = loadColorClass();

  assert.equal(colorClass({ workflow_type: "ticket", status: "new" }, "active"), "ticket-color-request");
  assert.equal(colorClass({ workflow_type: "ticket", status: "unassigned" }, "unassigned"), "ticket-color-request");
  assert.equal(colorClass({ workflow_type: "task", task_kind: "normal", status: "new" }, "active"), "ticket-color-task");
  assert.equal(colorClass({ workflow_type: "task", task_kind: "group_parent", is_internal_task: true, status: "new" }, "active"), "ticket-color-group-task");
  assert.equal(colorClass({ workflow_type: "task", task_kind: "group_child", status: "new" }, "active"), "ticket-color-group-task");
  assert.equal(colorClass({ workflow_type: "task", task_kind: "normal", is_internal_task: true, status: "new" }, "active"), "ticket-color-internal-task");
  assert.equal(colorClass({ workflow_type: "ticket", is_frozen: true, status: "new" }, "active"), "ticket-color-frozen");
  assert.equal(colorClass({ workflow_type: "task", status: "closed", is_frozen: true }, "active"), "ticket-color-closed");
  assert.equal(colorClass({ workflow_type: "task", task_kind: "group_child", is_special: true, status: "new" }, "special"), "ticket-color-group-task");
});

test("ticket list render uses api seen flag as a visual modifier only", () => {
  const rowClassSource = sourceBetween(listSource, 'const rowClasses = ["row-click", "ticket-row"];', "tr.className = rowClasses.join");

  assert.match(rowClassSource, /rowClasses\.push\(ticketRowColorClass\(t, state\.tab\)\)/);
  assert.match(rowClassSource, /t\.seen_by_agent/);
  assert.match(rowClassSource, /rowClasses\.push\("ticket-row-seen"\)/);
  assert.match(rowClassSource, /state\.role === "agent" \|\| state\.role === "trainee"/);
  assert.match(rowClassSource, /t\.seen_by_agent === false/);
  assert.match(rowClassSource, /rowClasses\.push\("ticket-row-unseen"\)/);
  assert.doesNotMatch(rowClassSource, /state\.tab === "unassigned"[\s\S]*ticket-row-unassigned/);
  assert.doesNotMatch(rowClassSource, /state\.tab === "active"[\s\S]*ticket-row-fresh/);
});

test("ticket row color css applies one row-wide gradient from the left edge", () => {
  const ticketScope = sourceBetween(styles, "tr.ticket-row td.ticket-subject-col", "/* ===== ticket card/messages ===== */");

  for (const colorName of ["request", "task", "group-task", "internal-task", "frozen", "closed"]) {
    assert.match(ticketScope, new RegExp(`tr\\.ticket-color-${colorName} \\{[\\s\\S]*linear-gradient\\(90deg,[\\s\\S]*0%[\\s\\S]*50%`));
  }
  assert.match(styles, /tr\.ticket-row td \{[\s\S]*background: transparent;/);
  assert.match(ticketScope, /tr\.ticket-row td\.ticket-channel-col \{[\s\S]*background: transparent;/);
  assert.match(ticketScope, /tr\.ticket-row-seen\.ticket-color-request \{/);
  assert.match(ticketScope, /tr\.ticket-row-seen\.ticket-color-task \{/);
  assert.doesNotMatch(ticketScope, /tr\.ticket-color-[^{]+ td\.ticket-subject-col \{[\s\S]*linear-gradient/);
  assert.doesNotMatch(ticketScope, /tr\.ticket-color-[^{]+ td \{/);
});

test("ticket color preference is loaded, toggled, and persisted with a partial patch", () => {
  assert.match(template, /id="colorEnabledInput"[^>]*type="checkbox"/);
  assert.match(template, /id="colorToggleText"[^>]*>Цветные плашки<\/span>/);
  assert.match(template, /list\.js\?v=20261111-requester-name-v1/);
  assert.match(template, /styles\.css\?v=20261106-status-admin-v1/);

  const stateSource = sourceBetween(listSource, "const state = {", "};\n\nconst endpointMap");
  assert.match(stateSource, /colorEnabled: true/);

  const loadSource = sourceBetween(listSource, "async function loadPreferences", "async function updateColorPreference");
  assert.match(loadSource, /api\("\/api\/v1\/users\/me\/preferences"\)/);
  assert.match(loadSource, /preferences\?\.color_enabled !== false/);

  const updateSource = sourceBetween(listSource, "async function updateColorPreference", "function renderTabCounts");
  assert.match(updateSource, /apiWrite\("\/api\/v1\/users\/me\/preferences", \{ color_enabled: state\.colorEnabled \}\)/);
  assert.doesNotMatch(updateSource, /send_by_enter/);
  assert.match(updateSource, /state\.colorEnabled = previous/);
  assert.match(updateSource, /showError\(`Ошибка настройки цвета:/);

  assert.match(listSource, /await Promise\.all\(\[loadMe\(\), loadPreferences\(\)\]\)/);
});

test("ticket list admin bulk freeze and unfreeze use existing selection and confirmation modal", () => {
  assert.match(template, /id="bulkAssignBtn"[\s\S]*id="bulkFreezeBtn"[\s\S]*id="bulkUnfreezeBtn"[\s\S]*id="bulkSkipClientNotification"[\s\S]*id="bulkCloseBtn"[\s\S]*id="bulkDeleteBtn"/);
  assert.match(template, /id="bulkFreezeBtn" class="action bulk-freeze-btn admin-only hidden" type="button" disabled>Заморозить<\/button>/);
  assert.match(template, /id="bulkUnfreezeBtn" class="action bulk-freeze-btn admin-only hidden" type="button" disabled>Разморозить<\/button>/);

  const collectSource = sourceBetween(listSource, "function collectElements()", "const REQUIRED_LIST_ELEMENT_KEYS");
  assert.match(collectSource, /bulkFreezeBtn: byId\("bulkFreezeBtn"\)/);
  assert.match(collectSource, /bulkUnfreezeBtn: byId\("bulkUnfreezeBtn"\)/);

  const bulkUiSource = sourceBetween(listSource, "function updateBulkAssignUi()", "async function assignTickets");
  assert.match(bulkUiSource, /el\.bulkFreezeBtn[\s\S]*state\.tab !== "active"/);
  assert.match(bulkUiSource, /el\.bulkFreezeBtn[\s\S]*disabled = selectedCount === 0/);
  assert.match(bulkUiSource, /el\.bulkUnfreezeBtn[\s\S]*state\.tab !== "frozen"/);
  assert.match(bulkUiSource, /el\.bulkUnfreezeBtn[\s\S]*disabled = selectedCount === 0/);

  const modalSource = sourceBetween(listSource, "function openBulkActionModal", "function closeBulkActionModal");
  assert.match(modalSource, /const isFreeze = kind === "freeze"/);
  assert.match(modalSource, /const isUnfreeze = kind === "unfreeze"/);
  assert.match(modalSource, /"Заморозка заявок"/);
  assert.match(modalSource, /"Разморозка заявок"/);
  assert.match(modalSource, /"Заморозить выбранную заявку\?"/);
  assert.match(modalSource, /`Заморозить выбранные заявки \(\$\{selected\}\)\?`/);
  assert.match(modalSource, /"Разморозить выбранную заявку\?"/);
  assert.match(modalSource, /`Разморозить выбранные заявки \(\$\{selected\}\)\?`/);
  assert.match(modalSource, /textContent = isDelete \? "Удалить" : isFreeze \? "Заморозить" : isUnfreeze \? "Разморозить" : "Завершить"/);
  assert.match(modalSource, /classList\.toggle\("danger", isDelete\)/);
  assert.match(modalSource, /classList\.toggle\("primary", !isDelete\)/);

  const runSource = sourceBetween(listSource, "async function runBulkAction()", "function toggleAllVisibleTickets");
  assert.match(runSource, /pendingBulkAction === "freeze"/);
  assert.match(runSource, /apiPost\("\/api\/v1\/tickets\/admin\/freeze", \{ ticket_ids: ticketIds \}\)/);
  assert.match(runSource, /Number\(result\.already_frozen \|\| 0\) \+ Number\(result\.invalid \|\| 0\) \+ Number\(result\.closed \|\| 0\) \+ Number\(result\.not_found\?\.length \|\| 0\)/);
  assert.match(runSource, /if \(skipped\) \{[\s\S]*console\.info\("Bulk freeze skipped items", result\)/);
  assert.match(runSource, /pendingBulkAction === "unfreeze"/);
  assert.match(runSource, /apiPost\("\/api\/v1\/tickets\/admin\/unfreeze", \{ ticket_ids: ticketIds \}\)/);
  assert.match(runSource, /Number\(result\.already_unfrozen \|\| 0\) \+ Number\(result\.closed \|\| 0\) \+ Number\(result\.not_found\?\.length \|\| 0\)/);
  assert.match(runSource, /state\.selectedTicketIds\.clear\(\)/);

  const initSource = sourceBetween(listSource, "function init()", "async function runTicketsListPage");
  assert.match(initSource, /bulkFreezeBtn[\s\S]*openBulkActionModal\("freeze"\)/);
  assert.match(initSource, /bulkUnfreezeBtn[\s\S]*openBulkActionModal\("unfreeze"\)/);
  assert.match(initSource, /confirmBulkActionBtn\.disabled = true/);
  assert.match(initSource, /catch \(e\) \{[\s\S]*confirmBulkActionBtn\.disabled = false[\s\S]*showError\(e\.message\)/);

  assert.match(styles, /\.bulk-freeze-btn \{/);
  assert.match(styles, /body\[data-theme="dark"\] \.bulk-freeze-btn/);
});

test("ticket task panel preference control and lifecycle are wired into list settings", () => {
  assert.match(template, /id="taskPanel"/);
  assert.match(template, /id="taskPanelSlots"/);
  assert.match(template, /id="taskPanelMoreBtn"[\s\S]*…/);
  assert.match(template, /id="taskPanelDropdown"/);
  assert.match(template, /id="taskPanelEnabledInput" type="checkbox" class="desk-toggle"/);
  assert.match(listSource, /taskPanelToggle: root\?\.querySelector\("\.columns-task-panel-toggle"\) \|\| null/);
  assert.match(template, />Панель задач<\/span>/);

  const stateSource = sourceBetween(listSource, "const state = {", "};\n\nconst endpointMap");
  assert.match(stateSource, /taskPanelEnabled: false/);
  assert.match(stateSource, /taskPanelItems: \[\]/);
  assert.match(stateSource, /taskPanelDropdownOpen: false/);

  const loadSource = sourceBetween(listSource, "async function loadPreferences", "async function updateColorPreference");
  assert.match(loadSource, /state\.taskPanelEnabled = preferences\?\.task_panel_enabled === true/);
  assert.match(loadSource, /applyTaskPanelPreference\(\)/);

  const saveSource = sourceBetween(listSource, "async function saveColumnsModal", "function ticketCellHtml");
  assert.match(saveSource, /const payload = \{ ticket_list_columns: payloadColumns, ticket_list_auto_width: state\.draftTicketListAutoWidth \}/);
  assert.match(saveSource, /if \(canUseTaskPanel\(\)\) \{[\s\S]*payload\.task_panel_enabled = nextTaskPanelEnabled/);
  assert.match(saveSource, /apiWrite\("\/api\/v1\/users\/me\/preferences", payload\)/);
  assert.match(saveSource, /state\.taskPanelEnabled = preferences\?\.task_panel_enabled === true/);
  assert.match(saveSource, /await loadTaskPanel\(\)/);

  const loadTicketsSource = sourceBetween(listSource, "async function loadTickets", "async function loadCounts");
  assert.match(loadTicketsSource, /Promise\.all\(\[[\s\S]*api\(`\$\{base\}\?\$\{queryParams\.toString\(\)\}`\),[\s\S]*fetchTaskPanelItems\(\{ silent: true \}\)/);
  assert.ok(loadTicketsSource.indexOf("fetchTaskPanelItems({ silent: true })") < loadTicketsSource.indexOf("renderTable();"));
  assert.ok(loadTicketsSource.indexOf("renderTaskPanel();") < loadTicketsSource.indexOf("renderTable();"));
});

test("ticket list shell remount refreshes dom refs before binding listeners", () => {
  assert.match(appShellSource, /\["\/tickets", \{ page: "tickets-list", scripts: \["\/static\/desk_dialogs\.js\?v=20261027-dialog-prompts-v4", "\/static\/ticket_column_auto_width\.js\?v=20261106-column-auto-width-v1", "\/static\/task_panel_layout\.js\?v=20261106-inline-panel-v1", "\/static\/list\.js\?v=20261111-requester-name-v1"\] \}\]/);
  assert.match(template, /list\.js\?v=20261111-requester-name-v1/);

  const collectSource = sourceBetween(listSource, "function collectElements()", "const REQUIRED_LIST_ELEMENT_KEYS");
  const context = vm.createContext({});
  vm.runInContext(`${collectSource}\nglobalThis.collectElements = collectElements;`, context);

  function makeRoot(label) {
    const nodes = new Map();
    return {
      label,
      querySelector(selector) {
        if (!nodes.has(selector)) nodes.set(selector, { selector, owner: label });
        return nodes.get(selector);
      },
      querySelectorAll(selector) {
        return [{ selector, owner: label }];
      },
    };
  }

  const rootA = makeRoot("first");
  const rootB = makeRoot("second");
  const documentStub = {
    root: rootA,
    getElementById(id) {
      if (id === "appContent") return this.root;
      return { id, owner: "document" };
    },
    querySelector(selector) {
      return { selector, owner: "document" };
    },
    querySelectorAll(selector) {
      return [{ selector, owner: "document" }];
    },
  };
  context.document = documentStub;

  const first = context.collectElements();
  documentStub.root = rootB;
  const second = context.collectElements();
  assert.equal(first.bulkAssignBtn.owner, "first");
  assert.equal(second.bulkAssignBtn.owner, "second");
  assert.notEqual(first.bulkAssignBtn, second.bulkAssignBtn);
  assert.equal(second.bulkCloseBtn.owner, "second");
  assert.equal(second.bulkDeleteBtn.owner, "second");

  const initSource = sourceBetween(listSource, "function init()", "async function runTicketsListPage");
  assert.ok(initSource.indexOf("el = collectElements();") < initSource.indexOf("el.bulkAssignBtn.addEventListener"));
  assert.ok(initSource.indexOf("requireListElements();") < initSource.indexOf("el.bulkAssignBtn.addEventListener"));
  assert.match(initSource, /pageEventController\?\.abort\(\)/);
  assert.match(initSource, /const pageSignal = pageEventController\.signal/);
});

test("ticket task panel is role-gated without clearing saved preference", () => {
  const roleSource = sourceBetween(listSource, "function canShowAssigneeColumn", "function allowedTicketColumnKeys");
  const taskPanelRoleSource = sourceBetween(listSource, "function canUseTaskPanel", "function allowedTicketColumnKeys");
  assert.match(roleSource, /function canUseTaskPanel\(role = state\.role\)/);
  assert.match(taskPanelRoleSource, /return role === "agent" \|\| role === "trainee"/);
  assert.doesNotMatch(taskPanelRoleSource, /admin|chiki_piki/);
  const roleContext = vm.createContext({ state: { role: "agent" } });
  vm.runInContext(`${roleSource}\nglobalThis.canUseTaskPanel = canUseTaskPanel;`, roleContext);
  assert.equal(roleContext.canUseTaskPanel("agent"), true);
  assert.equal(roleContext.canUseTaskPanel("trainee"), true);
  assert.equal(roleContext.canUseTaskPanel("admin"), false);
  assert.equal(roleContext.canUseTaskPanel("chiki_piki"), false);

  const preferenceSource = sourceBetween(listSource, "function applyTaskPanelPreference", "async function loadPreferences");
  assert.match(preferenceSource, /const canUse = canUseTaskPanel\(\)/);
  assert.match(preferenceSource, /el\.taskPanelToggle\.classList\.toggle\("hidden", !canUse\)/);
  assert.match(preferenceSource, /el\.taskPanelEnabledInput\.checked = Boolean\(state\.taskPanelEnabled\)/);

  const renderSource = sourceBetween(listSource, "function renderTaskPanel", "async function loadTaskPanel");
  assert.match(renderSource, /const canUse = canUseTaskPanel\(\)/);
  assert.match(renderSource, /const items = canUse && state\.taskPanelEnabled \? \(state\.taskPanelItems \|\| \[\]\) : \[\]/);
  assert.match(renderSource, /const shouldShow = canUse && state\.taskPanelEnabled/);

  const loadPanelSource = sourceBetween(listSource, "async function loadTaskPanel", "function closeOnBackdropClick");
  assert.match(loadPanelSource, /await fetchTaskPanelItems\(options\)/);
  assert.match(loadPanelSource, /if \(!canUseTaskPanel\(\) \|\| !state\.taskPanelEnabled\)[\s\S]*return/);
  assert.match(loadPanelSource, /api\("\/api\/v1\/tickets\/task-panel"\)/);

  const saveSource = sourceBetween(listSource, "async function saveColumnsModal", "function ticketCellHtml");
  assert.match(saveSource, /const payload = \{ ticket_list_columns: payloadColumns, ticket_list_auto_width: state\.draftTicketListAutoWidth \}/);
  assert.match(saveSource, /if \(canUseTaskPanel\(\)\) \{[\s\S]*payload\.task_panel_enabled = nextTaskPanelEnabled[\s\S]*\}/);
  assert.doesNotMatch(saveSource, /\{ ticket_list_columns: payloadColumns, task_panel_enabled: nextTaskPanelEnabled \}/);

  const roleUiSource = sourceBetween(listSource, "function applyRoleUi", "function renderAgentSelect");
  assert.match(roleUiSource, /applyTaskPanelPreference\(\)/);
});

test("ticket task panel renders eight fixed slots, overflow menu, subjects only, and unread markers", () => {
  const panelSource = sourceBetween(listSource, "function ticketHref", "async function loadTaskPanel");
  const panelStyles = sourceBetween(styles, ".task-panel {", "#searchInput.search-active");
  assert.ok(panelSource.includes("return `/tickets/${ticketId}`;"));
  assert.doesNotMatch(panelSource, /return_url|ticketListReturnUrl|searchParams/);
  assert.match(panelSource, /const visibleItems = items\.slice\(0, 8\)/);
  assert.match(panelSource, /const overflowItems = items\.slice\(8\)/);
  assert.match(panelSource, /taskPanelItemHtml\(item, "task-panel-chip"\)/);
  assert.match(panelSource, /taskPanelItemHtml\(item, "task-panel-menu-item"\)/);
  assert.match(panelSource, /item\?\.has_unread \? '<span class="task-panel-unread"/);
  assert.match(panelSource, /<span class="task-panel-subject">\$\{escapeHtml\(item\.subject \|\| ""\)\}<\/span>/);
  assert.doesNotMatch(panelSource, /ticket_number|client_name|assignee|status|is_frozen|Замороженная|актив/);
  const loadPanelSource = sourceBetween(listSource, "async function loadTaskPanel", "function closeOnBackdropClick");
  assert.match(loadPanelSource, /if \(!canUseTaskPanel\(\) \|\| !state\.taskPanelEnabled\)[\s\S]*state\.taskPanelItems = \[\][\s\S]*return/);
  assert.match(loadPanelSource, /api\("\/api\/v1\/tickets\/task-panel"\)/);

  assert.match(styles, /\.task-panel-slots \{[\s\S]*grid-template-columns: repeat\(8, minmax\(0, 1fr\)\)/);
  assert.match(styles, /\.task-panel\.has-overflow \{[\s\S]*grid-template-columns: minmax\(0, 1fr\) 44px/);
  assert.match(styles, /\.task-panel-subject \{[\s\S]*text-overflow: ellipsis;[\s\S]*white-space: nowrap;/);
  assert.match(styles, /\.task-panel-unread \{[\s\S]*position: absolute;/);
  assert.doesNotMatch(panelStyles, /frozen|ticket-color-frozen/);
});

test("ticket colors disabled state removes semantic backgrounds but keeps unread bold", () => {
  assert.match(styles, /body\.ticket-colors-disabled tr\.ticket-color-request/);
  assert.match(styles, /body\.ticket-colors-disabled tr\.ticket-color-closed/);
  assert.match(styles, /body\[data-theme="dark"\]\.ticket-colors-disabled tr\.ticket-color-request/);
  assert.match(styles, /background: #fff;/);
  assert.match(styles, /tr\.ticket-row-unseen td,\s*tr\.ticket-row-unseen \.ticket-row-link,\s*tr\.ticket-row-unseen \.row-assignee-select \{[\s\S]*font-weight: 700;/);
});

test("ticket color legend is available only while colors are enabled and has exact swatches", () => {
  const legendSource = sourceBetween(template, '<span id="colorLegend"', "</span>\n          </label>");
  const labels = [...legendSource.matchAll(/color-legend-swatch[^"]*">([^<]+)<\/span>/g)].map((match) => match[1]);
  assert.deepEqual(labels, ["Задача", "Заявка", "Увольнение", "Внутренняя", "Замороженная", "Закрытая"]);

  for (const name of ["task", "request", "group-task", "internal-task", "frozen", "closed"]) {
    assert.match(styles, new RegExp(`\\.color-legend-${name} \\{[\\s\\S]*background: rgba\\(`));
  }
  assert.match(legendSource, /popover="manual"/);
  assert.match(styles, /\.color-legend:popover-open\s*\{\s*display: flex;/);
  assert.match(listSource, /el\.colorToggleLabel\.classList\.toggle\("color-toggle-disabled", !state\.colorEnabled\)/);
});

test("ticket list has a fixed system channel column before configured columns", () => {
  assert.match(template, /<tr id="ticketsHeaderRow"><\/tr>/);
  assert.match(listSource, /if \(state\.role === "admin"\) \{\s*cells\.push\('<th class="admin-select-col"/);
  assert.match(listSource, /cells\.push\('<th class="ticket-channel-col"><button id="columnsSettingsBtn"/);
  assert.match(listSource, /const channelCell = `<td class="ticket-channel-col">\$\{renderChannelIcon\(t\)\}<\/td>`/);
  assert.match(styles, /\.ticket-channel-col \{[\s\S]*width: 54px;[\s\S]*min-width: 54px;[\s\S]*max-width: 54px;/);
});

test("ticket channel abstraction renders internal sync and telegram icons with tooltips", () => {
  assert.match(listSource, /function ticketChannel\(ticket\)/);
  assert.match(listSource, /if \(ticket\?\.is_internal_task\) return "internal"/);
  assert.match(listSource, /return "telegram"/);
  assert.match(listSource, /function renderChannelIcon\(ticket\)/);
  assert.match(listSource, /channel === "telegram" \? "Telegram" : \(channel === "internal" \? "Внутренняя" : "Другой канал"\)/);
  assert.match(listSource, /ticket-channel-telegram/);
  assert.match(listSource, /ticket-channel-sync/);
  assert.match(listSource, /<svg viewBox="0 0 24 24"/);
});

test("ticket column preferences are normalized by role and restored from preferences api", () => {
  assert.match(listSource, /const DEFAULT_TICKET_COLUMNS = \[/);
  assert.match(listSource, /\{ key: "subject", width: 0 \}/);
  assert.match(listSource, /\{ key: "requester", width: 150 \}/);
  assert.match(listSource, /\{ key: "assignee", width: 170 \}/);
  const defaultColumnsSource = sourceBetween(listSource, "const DEFAULT_TICKET_COLUMNS = [", "];\n\nconst TICKET_COLUMN_DEFS");
  assert.doesNotMatch(defaultColumnsSource, /department|type|location/);
  assert.match(listSource, /department: \{ title: "Подразделение", className: "ticket-department-col", sortKey: "requester_department", defaultWidth: 160 \}/);
  assert.match(listSource, /type: \{ title: "Тип", className: "ticket-type-col", sortKey: "ticket_type", defaultWidth: 130 \}/);
  assert.match(listSource, /location: \{ title: "Расположение", className: "ticket-location-col", sortKey: "requester_location_type", defaultWidth: 130 \}/);
  assert.match(listSource, /function canShowAssigneeColumn\(role = state\.role\)/);
  assert.match(listSource, /return role === "admin" \|\| role === "chiki_piki"/);
  assert.match(listSource, /allowedTicketColumnKeys\(role = state\.role\)/);
  assert.match(listSource, /key !== "assignee" \|\| canShowAssigneeColumn\(role\)/);
  assert.match(listSource, /state\.ticketListColumns = normalizeTicketColumns\(preferences\?\.ticket_list_columns \|\| DEFAULT_TICKET_COLUMNS\)/);
});

test("ticket columns modal supports mandatory subject, add, trash delete, reorder, resize entry, and partial save", () => {
  assert.match(template, /id="columnsModal"/);
  assert.match(template, /Настройка списка заявок/);
  assert.match(template, /id="columnAddSelect"/);
  assert.match(template, /id="editColumnsWidthBtn"[\s\S]*Редактирование столбцов/);
  assert.match(listSource, /function openColumnsModal\(\)/);
  assert.match(listSource, /state\.draftTicketListColumns = cloneColumns\(normalizeTicketColumns\(state\.ticketListColumns\)\)/);
  assert.match(listSource, /def\.mandatory[\s\S]*columns-delete-spacer/);
  assert.match(listSource, /columns-delete-btn[\s\S]*<svg viewBox="0 0 24 24"/);
  assert.match(listSource, /function addDraftColumn\(key\)/);
  assert.match(listSource, /draft\.push\(\{ key, width: TICKET_COLUMN_DEFS\[key\]\.defaultWidth \}\)/);
  assert.match(listSource, /function deleteDraftColumn\(key\)/);
  assert.match(listSource, /function moveDraftColumn\(fromIndex, toIndex\)/);
  assert.doesNotMatch(listSource, /columns-width-input|data-width-step|columns-width-btn/);
  assert.match(listSource, /enterColumnResizeMode/);
  assert.match(listSource, /apiWrite\("\/api\/v1\/users\/me\/preferences", payload\)/);
  assert.doesNotMatch(sourceBetween(listSource, "async function saveColumnsModal", "function ticketCellHtml"), /send_by_enter|color_enabled/);
});

test("ticket column resize mode measures width zero, clamps drag, and does not patch backend", () => {
  assert.match(template, /id="resizeColumnsActions"/);
  assert.match(listSource, /function enterColumnResizeMode\(\)/);
  assert.match(listSource, /state\.columnResizePreviousDraft = cloneColumns\(normalizeTicketColumns\(state\.draftTicketListColumns \|\| state\.ticketListColumns\)\)/);
  assert.match(listSource, /state\.columnResizeColumns = measureRenderedColumnWidths\(\)/);
  assert.match(listSource, /function minResizeWidthForColumn\(key\)/);
  assert.match(listSource, /if \(key === "subject"\) return 160/);
  assert.match(listSource, /if \(key === "age"\) return 90/);
  assert.match(listSource, /return 100/);
  assert.match(listSource, /return Math\.max\(min, Number\.isFinite\(parsed\) \? parsed : min\)/);
  assert.doesNotMatch(listSource, /Math\.min\(600/);
  assert.match(listSource, /drag\.startWidth \+ event\.clientX - drag\.startX/);
  assert.match(listSource, /getBoundingClientRect\(\)\.width/);
  assert.match(listSource, /column\.key === drag\.key \? \{ \.\.\.column, width: nextWidth \} : column/);
  assert.match(listSource, /function saveColumnResizeDraft\(\)/);
  assert.match(listSource, /state\.draftTicketListColumns = cloneColumns\(activeTicketColumns\(\)\)/);
  assert.match(listSource, /state\.columnDraftPreviewActive = true/);
  assert.match(listSource, /function cancelColumnResizeDraft\(\)/);
  assert.match(listSource, /state\.draftTicketListColumns = cloneColumns\(normalizeTicketColumns\(state\.columnResizePreviousDraft/);
  assert.doesNotMatch(sourceBetween(listSource, "function saveColumnResizeDraft", "function cancelColumnResizeDraft"), /apiWrite|fetch/);
});

test("ticket resize UI weakens overlay, hides modal card, and blocks table actions", () => {
  assert.match(styles, /\.columns-resize-mode \{[\s\S]*background: rgba\(15, 23, 42, 0\.16\)/);
  assert.match(styles, /\.columns-resize-mode \.columns-dialog \{[\s\S]*display: none;/);
  assert.match(styles, /\.column-resize-handle \{[\s\S]*cursor: col-resize;/);
  assert.match(styles, /body\.ticket-column-resize-active th\[data-column-key\],[\s\S]*td\[data-column-key\] \{[\s\S]*border-right:/);
  assert.match(styles, /body\.ticket-column-resize-active \.table-sort-btn,[\s\S]*#columnsSettingsBtn \{[\s\S]*pointer-events: none;/);
  assert.match(listSource, /state\.columnResizeActive[\s\S]*column-resize-handle/);
  assert.match(listSource, /if \(state\.columnResizeActive\) return;/);
  assert.match(listSource, /document\.body\.classList\.toggle\("ticket-column-resize-active", state\.columnResizeActive\)/);
  assert.match(listSource, /const hasFixedColumns = activeColumns\.every\(\(column\) => column\.width > 0\)/);
  assert.match(listSource, /el\.ticketsTable\.style\.width = hasFixedColumns \? `\$\{activeTicketAutoWidth\(\) \?[\s\S]*fixedTableWidth\(activeColumns\)\}px` : ""/);
  assert.match(styles, /body\.ticket-columns-fixed \.table-wrap table \{[\s\S]*min-width: 0;[\s\S]*table-layout: fixed;/);
  assert.match(styles, /body\.ticket-columns-fixed \.table-wrap th\[data-column-key\],[\s\S]*td\[data-column-key\] \{[\s\S]*box-sizing: border-box;/);
  assert.doesNotMatch(sourceBetween(styles, "body.ticket-columns-fixed .table-wrap table", "th, td"), /width: auto/);
  assert.match(styles, /\.column-resize-handle \{[\s\S]*right: -1px;[\s\S]*transform: translate\(50%, -50%\);/);
  assert.match(styles, /\.resize-handle-icon::before \{[\s\S]*left: 6px;[\s\S]*width: 2px;/);
  assert.match(styles, /\.column-resize-handle:hover,[\s\S]*\.column-resize-handle:active \{[\s\S]*background: transparent;[\s\S]*border: 0;[\s\S]*box-shadow: none;/);
  assert.match(styles, /\.ticket-channel-col \.mini-icon-btn,[\s\S]*\.ticket-channel-col \.mini-icon-btn:active \{[\s\S]*background: transparent;[\s\S]*box-shadow: none;/);
});

test("configured columns drive render order, sorting, subject color, and admin assignee select", () => {
  assert.match(listSource, /columns\.forEach\(\(column\) => \{/);
  assert.match(listSource, /data-column-key="\$\{escapeHtml\(column\.key\)\}"/);
  assert.match(listSource, /data-sort-key="\$\{escapeHtml\(def\.sortKey\)\}"/);
  assert.match(listSource, /handleSortClick\(btn\)/);
  assert.match(listSource, /key === "subject"[\s\S]*linkedCell\("ticket-subject-col"/);
  assert.match(listSource, /key === "assignee"[\s\S]*state\.role === "admin"[\s\S]*row-assignee-select/);
  assert.match(listSource, /key === "department"[\s\S]*linkedCell\("ticket-department-col", ticket\.is_internal_task \? "Система" : \(ticket\.requester_department \|\| "—"\), column\.width\)/);
  assert.match(listSource, /key === "location"[\s\S]*linkedCell\("ticket-location-col", ticket\.is_internal_task \? "" : \(ticket\.requester_location_type \|\| ""\), column\.width\)/);
  assert.match(listSource, /key === "type"[\s\S]*ticket-type-badge ticket-type-\$\{escapeHtml\(typeInfo\.className\)\}/);
  assert.match(listSource, /linkedCell\("ticket-type-col", badge, column\.width, true\)/);
  assert.match(listSource, /const linkedCell = \(className, textValue, width = 0, isHtml = false\)/);
  assert.match(listSource, /requester_department/);
  assert.match(listSource, /requester_location_type/);
  assert.match(listSource, /ticket_type/);
  assert.match(listSource, /activeTicketColumns\(\)[\s\S]*\.map\(\(column\) => ticketCellHtml\(column, t, ticketHref, linkedCell\)\)/);
});

test("ticket type column uses semantic priority and colored badges independent of row colors toggle", () => {
  const typeInfo = loadTicketTypeInfo();
  assert.equal(typeInfo({ status: "closed", is_frozen: true, workflow_type: "ticket" }).label, "Закрытая");
  assert.equal(typeInfo({ status: "closed", is_frozen: true, workflow_type: "ticket" }).className, "closed");
  assert.equal(typeInfo({ status: "new", is_frozen: true, workflow_type: "ticket" }).label, "Замороженная");
  assert.equal(typeInfo({ status: "new", is_frozen: true, workflow_type: "ticket" }).className, "frozen");
  assert.equal(typeInfo({ status: "new", workflow_type: "ticket" }).label, "Заявка");
  assert.equal(typeInfo({ status: "new", workflow_type: "ticket" }).className, "request");
  assert.equal(typeInfo({ status: "new", workflow_type: "task", task_kind: "group_parent", is_internal_task: true }).label, "Увольнение");
  assert.equal(typeInfo({ status: "new", workflow_type: "task", task_kind: "group_parent", is_internal_task: true }).className, "group-task");
  assert.equal(typeInfo({ status: "new", workflow_type: "task", is_internal_task: true }).label, "Внутренняя");
  assert.equal(typeInfo({ status: "new", workflow_type: "task", is_internal_task: true }).className, "internal-task");
  assert.equal(typeInfo({ status: "new", workflow_type: "task" }).label, "Задача");
  assert.equal(typeInfo({ status: "new", workflow_type: "task" }).className, "task");

  for (const name of ["task", "request", "group-task", "internal-task", "frozen", "closed"]) {
    assert.match(styles, new RegExp(`\\.ticket-type-${name} \\{[\\s\\S]*background: rgba\\(`));
  }
  assert.doesNotMatch(sourceBetween(styles, "body.ticket-colors-disabled tr.ticket-color-request", ".columns-dialog"), /ticket-type-/);
  assert.match(styles, /td\.ticket-type-col \{[\s\S]*text-align: center;/);
});

test("ticket resize draft preview rolls back on close and persists on modal save", () => {
  const activeSource = sourceBetween(listSource, "function activeTicketColumns", "function fixedTableWidth");
  assert.match(activeSource, /state\.columnDraftPreviewActive && state\.draftTicketListColumns/);
  assert.match(activeSource, /state\.columnDraftPreviewActive && state\.draftTicketListColumns/);

  const closeSource = sourceBetween(listSource, "function closeColumnsModal", "function deleteDraftColumn");
  assert.match(closeSource, /const hadPreview = state\.columnDraftPreviewActive/);
  assert.match(closeSource, /state\.columnDraftPreviewActive = false/);
  assert.match(closeSource, /state\.draftTicketListColumns = null/);
  assert.match(closeSource, /if \(hadPreview\) renderTable\(\)/);

  const saveSource = sourceBetween(listSource, "async function saveColumnsModal", "function ticketCellHtml");
  assert.match(saveSource, /state\.ticketListColumns = normalizeTicketColumns\(preferences\?\.ticket_list_columns \|\| payloadColumns\)/);
  assert.match(saveSource, /state\.columnDraftPreviewActive = false/);
});


test("MAIN marks only group parents in subject independently of colors and type column", () => {
  const escapeHtml = text => String(text).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
  const context = vm.createContext({ escapeHtml, state: { tab: "active", colorEnabled: false } });
  vm.runInContext([
    sourceBetween(listSource, "function intakeBadge(", "function assigneeOptionsHtml("),
    sourceBetween(listSource, "function ticketCellHtml(", "function renderTable("),
  ].join("\n"), context);
  for (const colorEnabled of [false, true]) {
    context.state.colorEnabled = colorEnabled;
    for (const workflow_type of ["ticket", "task", "auto"]) {
      for (const task_kind of ["group_parent", "group_child", "normal", undefined]) {
        const ticket = { workflow_type, task_kind, subject: '<Original & "subject">', is_special: true };
        const before = JSON.stringify(ticket);
        const linkedCell = (className, text, width, isHtml) => `<td class="${className}"><a href="/tickets/42">${isHtml ? text : escapeHtml(text)}</a></td>`;
        const html = context.ticketCellHtml({ key: "subject", width: 150 }, ticket, "/tickets/42", linkedCell);
        assert.equal(html.includes('class="ticket-main-badge">MAIN</span>'), task_kind === "group_parent");
        assert.ok(html.includes(escapeHtml(ticket.subject)));
        assert.ok(html.includes(escapeHtml(context.intakeBadge(ticket))));
        assert.ok(html.includes('href="/tickets/42"'));
        assert.equal(JSON.stringify(ticket), before);
        if (task_kind === "group_parent") assert.ok(html.indexOf("MAIN</span>") < html.indexOf(escapeHtml(ticket.subject)));
        else assert.equal(html, linkedCell("ticket-subject-col", ticket.subject + context.intakeBadge(ticket), 150, false));
      }
    }
  }
});

test("WEB source uses channel independently of workflow; existing Telegram and internal icons stay intact", () => {
  const source = sourceBetween(listSource, 'function ticketChannel(ticket)', 'function lastReplyText(ticket)');
  const context = { escapeHtml: String };
  vm.createContext(context);
  vm.runInContext(source, context);
  for (const workflow_type of ['ticket', 'task', 'auto']) {
    const html = context.renderChannelIcon({ channel: 'web', workflow_type });
    assert.match(html, /ticket-channel-web/);
    assert.match(context.renderChannelIcon({ channel: "web", workflow_type, is_internal_task: true }), /ticket-channel-web/);
    assert.match(html, /src="\/static\/icons\/web-source.webp"/);
    assert.doesNotMatch(html, /<svg|ticket-channel-sync/);
    assert.match(context.renderChannelIcon({ channel: 'telegram', workflow_type }), /ticket-channel-telegram/);
  }
  assert.match(context.renderChannelIcon({ is_internal_task: true }), /ticket-channel-sync/);
  assert.match(context.renderChannelIcon({ channel: "telegram", is_internal_task: true }), /ticket-channel-sync/);
  assert.match(context.renderChannelIcon({}), /ticket-channel-telegram/);
});


test("unread emphasis excludes either closed indicator and preserves seen and frozen behavior", () => {
  const source = sourceBetween(listSource, 'const rowClasses = ["row-click", "ticket-row"];', 'tr.className = rowClasses.join');
  const classify = (ticket, role, tab) => {
    const before = JSON.stringify(ticket);
    const context = vm.createContext({ t: ticket, state: { role, tab }, ticketRowColorClass: loadColorClass() });
    const classes = vm.runInContext(source + '\nrowClasses;', context);
    assert.equal(JSON.stringify(ticket), before, 'read flags remain untouched');
    return classes;
  };
  for (const role of ['agent', 'trainee', 'admin']) for (const workflow_type of ['ticket', 'task']) {
    for (const assignee_id of ['self', 'other']) for (const seen_by_agent of [false, true, null, undefined]) {
      for (const [tab, status, is_frozen] of [['active', 'new', false], ['active', 'closed', false], ['closed', 'new', false], ['closed', 'closed', false], ['frozen', 'new', true], ['active', 'new', true]]) {
        const ticket = { workflow_type, assignee_id, seen_by_agent, status, is_frozen };
        const classes = classify(ticket, role, tab);
        const label = JSON.stringify({ role, tab, ...ticket });
        assert.equal(classes.includes('ticket-row-unseen'), role !== 'admin' && seen_by_agent === false && status !== 'closed' && tab !== 'closed', label);
        assert.equal(classes.includes('ticket-row-seen'), Boolean(seen_by_agent) && !is_frozen && tab !== 'frozen' && status !== 'closed' && tab !== 'closed', label);
      }
    }
    const ticket = { workflow_type, status: 'closed', seen_by_agent: false };
    assert.equal(classify(ticket, role, 'closed').includes('ticket-row-unseen'), false);
    ticket.status = 'new';
    assert.equal(classify(ticket, role, 'active').includes('ticket-row-unseen'), role !== 'admin');
  }
});
