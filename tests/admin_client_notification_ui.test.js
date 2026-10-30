const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const listSource = fs.readFileSync("api/app/static/list.js", "utf8");
const controlsSource = fs.readFileSync("api/app/static/ticket_controls.js", "utf8");
function node() {
  const classes = new Set();
  return { checked: false, disabled: false, textContent: "", setAttribute() {}, classList: {
    toggle(c, on) { if (on) classes.add(c); else classes.delete(c); },
    add(c) { classes.add(c); }, remove(c) { classes.delete(c); }, contains(c) { return classes.has(c); }
  }};
}
function list() {
  const el = Object.fromEntries(["bulkAssignBtn", "bulkCloseBtn", "bulkFreezeBtn", "bulkUnfreezeBtn", "bulkDeleteBtn", "bulkAdminActions", "bulkSkipClientNotification", "selectAllTicketsBtn", "bulkActionModal", "bulkActionModalTitle", "bulkActionModalText", "confirmBulkActionBtn"].map(k => [k, node()]));
  const state = { role: "admin", tab: "active", selectedTicketIds: new Set(["1"]), visibleTicketIds: ["1"] };
  const ctx = vm.createContext({ el, state, pendingBulkAction: null, assigneeOptionsHtml: () => "", setStatus() {}, showError() {}, loadTickets: async () => {}, loadCounts: async () => {}, console, apiPost: async () => ({}) });
  const start = listSource.indexOf("function updateBulkAssignUi()");
  const end = listSource.indexOf("function toggleAllVisibleTickets()", start);
  vm.runInContext(listSource.slice(start, end), ctx);
  return { el, state, ctx, open: kind => ctx.openBulkActionModal(kind), cancel: () => ctx.closeBulkActionModal(), run: () => ctx.runBulkAction() };
}
function card() {
  const window = { HelpdeskAppShell: { navigate: async () => {} } };
  vm.runInNewContext(controlsSource, { window, console });
  const el = Object.fromEntries(["adminSkipClientNotification", "adminActionModalTitle", "adminActionModalText", "confirmAdminActionBtn", "adminActionModal"].map(k => [k, node()]));
  const ctx = { el, state: { role: "admin", tab: "active", card: { id: "1", workflow_type: "ticket" } }, apiPost: async () => ({}), loadCard: async () => {}, loadTabCounts: async () => {}, setStatus() {}, showError() {} };
  return { el, ctx, checkbox: el.adminSkipClientNotification, open: k => window.TicketControls.openAdminActionModal(ctx, k), cancel: () => window.TicketControls.closeAdminActionModal(ctx), run: () => window.TicketControls.runAdminAction(ctx) };
}
test("zero selection keeps actions visible and disabled; 0/1/9/10/20 use one label format", () => {
  const p = list();
  for (const count of [0, 1, 9, 10, 20]) {
    p.state.selectedTicketIds = new Set(Array.from({length: count}, (_, i) => String(i)));
    p.ctx.updateBulkAssignUi();
    for (const key of ["bulkAssignBtn", "bulkCloseBtn", "bulkFreezeBtn", "bulkDeleteBtn", "bulkAdminActions"]) assert.equal(p.el[key].classList.contains("hidden"), false);
    for (const key of ["bulkAssignBtn", "bulkCloseBtn", "bulkFreezeBtn", "bulkDeleteBtn"]) assert.equal(p.el[key].disabled, count === 0);
    assert.ok(p.el.bulkAssignBtn.innerHTML.includes(`Назначить ответственного (${count})</option>`));
    assert.equal(p.el.bulkAssignBtn.value, "__placeholder__");
  }
  for (const tab of ["closed", "unassigned", "special", "frozen"]) {
    p.state.tab = tab; p.ctx.updateBulkAssignUi();
    assert.equal(p.el.bulkFreezeBtn.classList.contains("hidden"), tab === "frozen");
    assert.equal(p.el.bulkFreezeBtn.disabled, true);
    assert.equal(p.el.bulkUnfreezeBtn.classList.contains("hidden"), tab !== "frozen");
  }
  p.state.role = "agent"; p.ctx.updateBulkAssignUi();
  assert.equal(p.el.bulkAdminActions.classList.contains("hidden"), true);
});
for (const panel of ["list", "card"]) {
  for (const action of ["close", "delete"]) {
    for (const skip of [false, true]) {
      test(`${panel} ${action}: snapshot=${skip}, reset on success/error and cancel`, async () => {
        for (const fail of [false, true]) {
          const p = panel === "list" ? list() : card();
          const checkbox = panel === "list" ? p.el.bulkSkipClientNotification : p.checkbox;
          let sent;
          checkbox.checked = skip;
          p.open(action);
          p.ctx.apiPost = async (url, payload) => { sent = { url, payload }; checkbox.checked = !skip; if (fail) throw Error("offline"); return {}; };
          if (fail) await assert.rejects(p.run(), /offline/); else await p.run();
          assert.equal(sent.url, `/api/v1/tickets/admin/${action}`);
          assert.equal(sent.payload.skip_client_notification, skip);
          assert.equal(checkbox.checked, false);
          checkbox.checked = true; p.open(action); p.cancel();
          assert.equal(checkbox.checked, false);
        }
      });
    }
  }
}
test("independent checkboxes and freeze never consumes/sends the flag", async () => {
  const a = list(), b = card();
  a.el.bulkSkipClientNotification.checked = true;
  assert.equal(b.checkbox.checked, false);
  b.checkbox.checked = true; b.open("close"); b.cancel();
  assert.equal(a.el.bulkSkipClientNotification.checked, true);
  let payload;
  a.ctx.apiPost = async (url, data) => { payload = data; return {}; };
  a.open("freeze"); await a.run(); a.cancel();
  assert.equal("skip_client_notification" in payload, false);
  assert.equal(a.el.bulkSkipClientNotification.checked, true);
});
