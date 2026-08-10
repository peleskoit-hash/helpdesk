const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const ticketPageSource = fs.readFileSync("api/app/static/ticket.js", "utf8");
const ticketControlsSource = fs.readFileSync("api/app/static/ticket_controls.js", "utf8");

function sourceBetween(source, start, end) {
  const from = source.indexOf(start);
  assert.notEqual(from, -1, `start marker not found: ${start}`);
  const to = source.indexOf(end, from);
  assert.notEqual(to, -1, `end marker not found: ${end}`);
  return source.slice(from, to);
}

function loadTicketControls(now = 0) {
  const window = {};
  const FakeDate = class extends Date {
    static now() {
      return now;
    }
  };
  const context = vm.createContext({ window, console, Date: FakeDate, Math });
  vm.runInContext(ticketControlsSource, context);
  return window.TicketControls;
}

function buttonStub() {
  return {
    textContent: "",
    disabled: false,
    classList: { toggle() {} },
    addEventListener(type, handler) {
      this.handlers = this.handlers || {};
      this.handlers[type] = handler;
    },
  };
}

function controlsRenderCtx({ now = 0, restoreCloseCooldownUntil = 0 } = {}) {
  const closeBtn = buttonStub();
  const restoreBtn = buttonStub();
  const freezeToggleBtn = buttonStub();
  return {
    controls: loadTicketControls(now),
    closeBtn,
    restoreBtn,
    freezeToggleBtn,
    ctx: {
      el: {
        greetingBtn: buttonStub(),
        wrongProblemBtn: buttonStub(),
        closeBtn,
        adminForceCloseBtn: buttonStub(),
        adminDeleteBtn: buttonStub(),
        freezeToggleBtn,
        restoreBtn,
        markSpecialBtn: buttonStub(),
        autoCloseBox: null,
        instructionBtn: buttonStub(),
        editClientBtn: buttonStub(),
        ticketAssigneeBox: null,
        ticketAssigneeSelect: null,
        autoCloseDisabledCheckbox: null,
        autoCloseDueText: null,
      },
      state: {
        role: "agent",
        userId: "agent-1",
        stateActionInFlight: null,
        restoreCloseCooldownUntil,
        supportAgents: [],
        card: {
          workflow_type: "ticket",
          status: "pending",
          messages: [{ message_type: "agent", body: "done" }],
          greeting_used: false,
          wrong_problem_used: false,
          task_kind: "normal",
          parent_ticket_id: null,
          is_special: false,
          onsite_triggered: false,
          is_frozen: false,
          auto_close_disabled: false,
          auto_close_due_at: null,
          assignee_id: null,
          closed_by_user_id: null,
          instruction_text: null,
          intake_state: "ready",
        },
      },
      escapeHtml: (value) => String(value || ""),
      setVisible() {},
      minutesLeftToDue() {
        return null;
      },
      greetingAllowed() {
        return false;
      },
    },
  };
}

test("state action guard blocks duplicate freeze request while first request is in flight", async () => {
  const helperSource = [
    'const STATE_ACTION_NAMES = new Set(["close", "restore", "freeze", "unfreeze"]);',
    "const RESTORE_CLOSE_COOLDOWN_MS = 10_000;",
    sourceBetween(ticketPageSource, "let restoreCloseCooldownTimer = null;", "function shellNavigateOrReload(urlLike) {"),
    sourceBetween(ticketPageSource, "async function action(name, body = null) {", "async function assignCurrentTicket(assigneeId) {"),
    "globalThis.__ticketActionTest = { action, state };",
  ].join("\n");

  let resolveApiPost;
  const apiCalls = [];
  const renderCalls = [];
  const errors = [];

  const context = vm.createContext({
    console,
    Date,
    Math,
    window: {
      setInterval() {
        return 1;
      },
      clearInterval() {},
    },
    state: {
      ticketId: "ticket-1",
      isSendingReply: false,
      pendingCloseAfterSend: false,
      stateActionInFlight: null,
      restoreCloseCooldownUntil: 0,
      card: { id: "ticket-1", status: "pending" },
    },
    destroyed: false,
    setStatus() {},
    showError(message) {
      errors.push(message);
    },
    apiPost(url, body) {
      apiCalls.push({ url, body });
      return new Promise((resolve) => {
        resolveApiPost = resolve;
      });
    },
    loadCard: async () => {},
    TicketControls: {
      renderTicketControlsView() {
        renderCalls.push("render");
      },
    },
    controlsCtx() {
      return {};
    },
  });

  vm.runInContext(helperSource, context);

  const first = context.__ticketActionTest.action("freeze", { freeze_until: null });
  const second = context.__ticketActionTest.action("freeze", { freeze_until: null });
  await Promise.resolve();

  assert.equal(apiCalls.length, 1);
  assert.equal(context.__ticketActionTest.state.stateActionInFlight, "freeze");

  resolveApiPost();
  await first;
  await second;

  assert.equal(context.__ticketActionTest.state.stateActionInFlight, null);
  assert.equal(errors.length, 0);
  assert.ok(renderCalls.length >= 2);
});

test("successful restore cooldown disables close button and shows countdown label", () => {
  const { controls, closeBtn, ctx } = controlsRenderCtx({
    now: 10_000,
    restoreCloseCooldownUntil: 20_000,
  });

  controls.renderTicketControlsView(ctx);

  assert.equal(closeBtn.disabled, true);
  assert.equal(closeBtn.textContent, "Закрыть через 10 с");
});

test("expired restore cooldown returns close button to normal active state", () => {
  const { controls, closeBtn, ctx } = controlsRenderCtx({
    now: 21_000,
    restoreCloseCooldownUntil: 20_000,
  });

  controls.renderTicketControlsView(ctx);

  assert.equal(closeBtn.disabled, false);
  assert.equal(closeBtn.textContent, "Закрыть заявку");
});
