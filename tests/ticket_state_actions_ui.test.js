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
  const greetingBtn = buttonStub();
  const clarificationBtn = buttonStub();
  const onsiteBtn = buttonStub();
  return {
    controls: loadTicketControls(now),
    closeBtn,
    restoreBtn,
    freezeToggleBtn,
    greetingBtn,
    clarificationBtn,
    onsiteBtn,
    ctx: {
      el: {
        greetingBtn,
        clarificationBtn,
        onsiteBtn,
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
          quick_actions_used: {},
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

test("close button stays visible but disabled when agent cannot close yet", () => {
  const visible = [];
  const { controls, closeBtn, ctx } = controlsRenderCtx();
  ctx.state.card.messages = [];
  ctx.setVisible = (node, value) => {
    if (node === closeBtn) visible.push(value);
  };

  controls.renderTicketControlsView(ctx);

  assert.deepEqual(visible, [true]);
  assert.equal(closeBtn.disabled, true);
});

test("quick action sequence unlocks clarification and onsite after greeting button use", () => {
  const { controls, greetingBtn, clarificationBtn, onsiteBtn, ctx } = controlsRenderCtx();
  ctx.greetingAllowed = () => true;

  controls.renderTicketControlsView(ctx);

  assert.equal(greetingBtn.disabled, false);
  assert.equal(clarificationBtn.disabled, true);
  assert.equal(onsiteBtn.disabled, true);

  ctx.state.card.quick_actions_used = { greeting: true };
  ctx.state.card.greeting_used = true;
  ctx.state.card.messages = [{ message_type: "agent", body: "Добрый день" }];
  controls.renderTicketControlsView(ctx);

  assert.equal(greetingBtn.disabled, true);
  assert.equal(clarificationBtn.disabled, false);
  assert.equal(onsiteBtn.disabled, false);
});

test("internal task keeps all quick actions visible but disabled", () => {
  const visible = [];
  const { controls, greetingBtn, clarificationBtn, onsiteBtn, ctx } = controlsRenderCtx();
  ctx.state.card.workflow_type = "task";
  ctx.greetingAllowed = () => true;
  ctx.setVisible = (node, value) => {
    if ([greetingBtn, clarificationBtn, onsiteBtn].includes(node)) visible.push(value);
  };

  controls.renderTicketControlsView(ctx);

  assert.deepEqual(visible, [true, true, true]);
  assert.equal(greetingBtn.disabled, true);
  assert.equal(clarificationBtn.disabled, true);
  assert.equal(onsiteBtn.disabled, true);
});

test("quick action reload preserves current composer draft", () => {
  assert.match(ticketPageSource, /preserveComposerDraft/);
  assert.match(ticketControlsSource, /loadCard\(\{ preserveComposerDraft: true \}\)/);
});

test("quick message send error reload preserves composer draft", async () => {
  const { controls, clarificationBtn, ctx } = controlsRenderCtx();
  const loadCardCalls = [];
  const errors = [];
  ctx.apiPost = async () => {
    throw new Error("send failed");
  };
  ctx.apiPatch = async () => {};
  ctx.loadCard = async (options) => loadCardCalls.push(options || {});
  ctx.setStatus = () => {};
  ctx.showError = (message) => errors.push(message);
  ctx.action = () => {};
  ctx.openInstructionModal = () => {};
  ctx.closeInstructionModal = () => {};
  ctx.closeOnBackdropClick = () => {};
  ctx.assignCurrentTicket = async () => {};
  ctx.saveClientProfile = async () => {};
  ctx.openClientModal = () => {};
  ctx.closeClientModal = () => {};

  controls.bindTicketControlHandlers(ctx);
  await clarificationBtn.handlers.click();

  assert.deepEqual(errors, ["send failed"]);
  assert.equal(loadCardCalls.length, 1);
  assert.equal(loadCardCalls[0].silent, true);
  assert.equal(loadCardCalls[0].preserveComposerDraft, true);
});

test("successful greeting unlocks next quick actions before card reload finishes", async () => {
  const { controls, greetingBtn, clarificationBtn, onsiteBtn, ctx } = controlsRenderCtx();
  const apiCalls = [];
  let loadStarted = false;
  let loadFinished = false;
  ctx.greetingAllowed = () => true;
  ctx.state.ticketId = "ticket-1";
  ctx.apiPost = async (url, body) => {
    apiCalls.push({ url, body });
    return { ok: true };
  };
  ctx.apiPatch = async () => {};
  ctx.loadCard = async () => {
    loadStarted = true;
    await new Promise((resolve) => setTimeout(resolve, 20));
    loadFinished = true;
  };
  ctx.setStatus = () => {};
  ctx.showError = () => {};
  ctx.action = () => {};
  ctx.openInstructionModal = () => {};
  ctx.closeInstructionModal = () => {};
  ctx.closeOnBackdropClick = () => {};
  ctx.assignCurrentTicket = async () => {};
  ctx.saveClientProfile = async () => {};
  ctx.openClientModal = () => {};
  ctx.closeClientModal = () => {};

  controls.bindTicketControlHandlers(ctx);
  await greetingBtn.handlers.click();

  assert.equal(loadStarted, true);
  assert.equal(loadFinished, false);
  assert.equal(greetingBtn.disabled, true);
  assert.equal(clarificationBtn.disabled, false);
  assert.equal(onsiteBtn.disabled, false);

  const clarificationRun = clarificationBtn.handlers.click();
  await Promise.resolve();
  await Promise.resolve();

  assert.equal(apiCalls.length, 2);
  assert.equal(apiCalls[1].url, "/api/v1/tickets/ticket-1/reply");
  assert.equal(apiCalls[1].body.quick_action, "clarification");

  await clarificationRun;
});
