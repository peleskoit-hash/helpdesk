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

test("task close blocks on editor, server and message uploads and clears on ready or failed", () => {
  for (const role of ["agent", "admin"]) {
    for (const source of ["editor", "server", "message", "group"]) {
      for (const terminal of ["ready", "failed"]) {
        const { ctx, controls, closeBtn } = controlsRenderCtx();
        ctx.state.role = role;
        ctx.state.card.workflow_type = "task";
        const file = { name: "upload.pdf" };
        const preload = { stage: "browser_uploading", status: "uploading", id: "preload" };
        const attachment = { telegram_delivery_status: "uploading" };
        if (source === "editor") {
          ctx.state.selectedFiles = [file];
          ctx.state.filePreloads = new Map([[file, preload]]);
        } else if (source === "server") {
          ctx.state.card.active_attachment_preloads = [preload];
        } else if (source === "message") {
          ctx.state.card.messages[0].attachments = [attachment];
        } else {
          ctx.state.card.task_kind = "group_parent";
          ctx.state.card.has_active_attachment_uploads = true;
        }
        controls.renderTicketControlsView(ctx);
        assert.equal(closeBtn.disabled, true, `${role}/${source}`);
        assert.equal(ctx.el.adminForceCloseBtn.disabled, true);
        preload.stage = preload.status = terminal;
        attachment.telegram_delivery_status = terminal;
        ctx.state.card.has_active_attachment_uploads = false;
        controls.renderTicketControlsView(ctx);
        assert.equal(closeBtn.disabled, false, `${role}/${source}/${terminal}`);
        assert.equal(ctx.el.adminForceCloseBtn.disabled, false);
      }
    }
  }
});

test("task ignores removed uploads; ordinary ticket close ignores task upload guard", () => {
  const { ctx, controls, closeBtn } = controlsRenderCtx();
  const file = {};
  ctx.state.selectedFiles = [file];
  const preload = { stage: "uploading", removed: false };
  ctx.state.filePreloads = new Map([[file, preload]]);
  controls.renderTicketControlsView(ctx);
  assert.equal(closeBtn.disabled, false);
  ctx.state.card.workflow_type = "task";
  controls.renderTicketControlsView(ctx);
  assert.equal(closeBtn.disabled, true);
  preload.removed = true;
  controls.renderTicketControlsView(ctx);
  assert.equal(closeBtn.disabled, false);
});

test("task close stays blocked between saving the answer and refreshing its attachments", () => {
  const { ctx, controls, closeBtn } = controlsRenderCtx();
  ctx.state.card.workflow_type = "task";
  ctx.state.composeMode = "reply";
  ctx.state.isSendingReply = true;
  controls.renderTicketControlsView(ctx);
  assert.equal(closeBtn.disabled, true);
  ctx.state.isSendingReply = false;
  controls.renderTicketControlsView(ctx);
  assert.equal(closeBtn.disabled, false);
  ctx.state.composeMode = "comment";
  ctx.state.isSendingReply = true;
  controls.renderTicketControlsView(ctx);
  assert.equal(closeBtn.disabled, false);
});

test("deleted task messages do not block close while live messages and editor uploads do", () => {
  const { ctx, controls, closeBtn } = controlsRenderCtx();
  ctx.state.card.workflow_type = "task";
  ctx.state.card.messages[0].message_status = "deleted";
  ctx.state.card.messages[0].attachments = [{ telegram_delivery_status: "uploading" }];
  controls.renderTicketControlsView(ctx);
  assert.equal(closeBtn.disabled, false);
  ctx.state.card.messages.push({ message_type: "agent", message_status: "normal", attachments: [{ telegram_delivery_status: "uploading" }] });
  controls.renderTicketControlsView(ctx);
  assert.equal(closeBtn.disabled, true);
  ctx.state.card.messages[1].attachments[0].telegram_delivery_status = "ready";
  controls.renderTicketControlsView(ctx);
  assert.equal(closeBtn.disabled, false);
  ctx.state.card.active_attachment_preloads = [{ id: "editor", status: "uploading" }];
  controls.renderTicketControlsView(ctx);
  assert.equal(closeBtn.disabled, true);
});

test("existing card polling and composer updates refresh task controls without new timers", () => {
  const composerSource = fs.readFileSync("api/app/static/ticket_composer.js", "utf8");
  assert.match(sourceBetween(composerSource, "function renderSelectedFiles(ctx)", "function syncRestoredPreloads"), /refreshTaskUploadControls\?\.\(\)/);
  assert.match(sourceBetween(ticketPageSource, "function composerCtx()", "function controlsCtx()"), /refreshTaskUploadControls: renderTicketStateControls/);
  assert.match(sourceBetween(ticketPageSource, "function renderCard()", "async function loadCard"), /TicketControls\.renderTicketControlsView/);
});

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


test("move to special stays visible for admin and disables closed, special or unsupported workflows", () => {
  for (const workflow of ["ticket", "task", "auto", "unsupported"]) {
    for (const kind of ["normal", "group_parent", "group_child", "internal"]) {
      for (const role of ["admin", "agent", "trainee", "chiki_piki"]) {
        for (const status of ["unassigned", "new", "pending", "closed"]) {
          for (const special of [false, true]) {
            const { controls, ctx } = controlsRenderCtx();
            ctx.state.role = role;
            Object.assign(ctx.state.card, { workflow_type: workflow, task_kind: kind === "internal" ? "normal" : kind,
              is_internal_task: kind === "internal", parent_ticket_id: kind === "group_child" ? "parent" : null,
              status, is_special: special });
            let visible;
            ctx.setVisible = (element, show) => { if (element === ctx.el.markSpecialBtn) visible = show; };
            controls.renderTicketControlsView(ctx);
            assert.equal(visible, role === "admin",
              `${workflow}/${kind}/${role}/${status}/${special}`);
            assert.equal(ctx.el.markSpecialBtn.disabled, status === "closed" || special || !["ticket", "task"].includes(workflow),
              `${workflow}/${kind}/${role}/${status}/${special}`);
          }
        }
      }
    }
  }
});
