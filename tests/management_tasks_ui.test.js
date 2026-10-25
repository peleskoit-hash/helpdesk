const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("api/app/static/management_tasks.js", "utf8");
const sidebarTemplateFiles = [
  "api/app/templates/management.html",
  "api/app/templates/management_feedback.html",
  "api/app/templates/management_integrations.html",
  "api/app/templates/management_statistics.html",
  "api/app/templates/management_tasks.html",
  "api/app/templates/management_users.html",
  "api/app/templates/mass_notifications.html",
  "api/app/templates/profile.html",
  "api/app/templates/profile_notification_bot.html",
  "api/app/templates/reviews.html",
  "api/app/templates/settings.html",
  "api/app/templates/settings_automatic_messages.html",
  "api/app/templates/settings_bot_buttons.html",
  "api/app/templates/settings_client_card.html",
  "api/app/templates/settings_notification_bot_basic.html",
  "api/app/templates/settings_users.html",
  "api/app/templates/statuses.html",
  "api/app/templates/ticket.html",
  "api/app/templates/tickets.html",
];

class Element {
  constructor(dataset = {}) {
    this.dataset = dataset;
    this.handlers = {};
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.checked = false;
    this.html = "";
    this.text = "";
    this.className = dataset.internalTaskTab === "active" || dataset.internalTaskScope === "all" ? "action active" : "action";
    this.classList = {
      add: (name) => {
        const names = new Set(this.className.split(" ").filter(Boolean));
        names.add(name);
        this.className = [...names].join(" ");
      },
      remove: (name) => {
        const names = new Set(this.className.split(" ").filter(Boolean));
        names.delete(name);
        this.className = [...names].join(" ");
      },
      toggle: (name, enabled) => {
        const names = new Set(this.className.split(" ").filter(Boolean));
        if (enabled) names.add(name); else names.delete(name);
        this.className = [...names].join(" ");
      },
      contains: (name) => this.className.split(" ").includes(name),
    };
  }
  set textContent(value) { this.text = String(value); }
  get textContent() { return this.text || ""; }
  set innerHTML(value) { this.html = String(value); }
  get innerHTML() { return this.html || ""; }
  addEventListener(name, handler) { (this.handlers[name] ||= []).push(handler); }
  removeEventListener(name, handler) { this.handlers[name] = (this.handlers[name] || []).filter((item) => item !== handler); }
  fire(name, event = {}) { for (const handler of this.handlers[name] || []) handler({ target: this, ...event }); }
  querySelector() { return null; }
}

const tick = () => new Promise((resolve) => setImmediate(resolve));
async function settle() { await tick(); await tick(); await new Promise((resolve) => setTimeout(resolve, 0)); }

async function load(handler, dateConstructor = Date, options = {}) {
  const el = {};
  for (const key of [
    "Status", "Validation", "CreateBtn", "ActiveBody", "ActiveEmpty", "ScheduledBody", "ScheduledEmpty",
    "RecurringBody", "RecurringEmpty", "CompletedBody", "CompletedEmpty", "Modal", "ModalTitle", "CloseModalBtn", "CancelBtn", "SaveBtn",
    "Form", "Title", "Description", "AssignmentMode", "AssigneeWrap", "Assignee", "Responsible",
    "DisplayAuthor", "Notify", "Timing", "ScheduledAtWrap", "ScheduledAt", "DeadlineEnabled",
    "RecurrenceWrap", "RecurrenceMode", "CycleStartAt", "DailyTimesWrap", "DailyTimes",
    "WeekdaysWrap", "RecurrenceWeekdayWrap", "RecurrenceWeekday", "RecurrenceTimeWrap", "RecurrenceTime",
    "MonthlyDayWrap", "MonthlyDay", "EveryNDaysWrap", "EveryNDays", "CycleEndMode", "CycleEndAtWrap",
    "CycleEndAt", "CycleMaxWrap", "CycleMax", "DeadlineAtWrap", "DeadlineAt", "DailyTimesError", "FormError", "ScheduleInfo", "SkipNextBtn",
  ]) {
    el[key] = new Element();
  }
  el.Modal.className = "modal hidden";
  el.AssignmentMode.value = "distribution";
  el.Timing.value = "now";
  el.RecurrenceMode.value = "daily";
  el.RecurrenceTime.value = "10:00";
  el.MonthlyDay.value = "last";
  el.EveryNDays.value = "1";
  el.CycleEndMode.value = "never";
  el.CycleMax.value = "1";
  const weekdays = ["1", "2", "3", "4", "5", "6", "7"].map((value) => {
    const item = new Element();
    item.value = value;
    return item;
  });
  const tabs = ["active", "scheduled", "recurring", "completed"].map((name) => new Element({ internalTaskTab: name }));
  const scopes = ["all", "mine"].map((name) => new Element({ internalTaskScope: name }));
  const panels = ["active", "scheduled", "recurring", "completed"].map((name) => new Element({ internalTaskPanel: name }));
  const root = {
    querySelector: (selector) => {
      const key = selector.replace(/^#internalTasks?/, "").replace(/^#internalTask/, "");
      if (selector === "#internalTasksStatus") return el.Status;
      if (selector === "#internalTasksValidation") return el.Validation;
      return el[key] || null;
    },
    querySelectorAll: (selector) => {
      if (selector === "[data-internal-task-tab]") return tabs;
      if (selector === "[data-internal-task-scope]") return scopes;
      if (selector === "[data-internal-task-panel]") return panels;
      if (selector === "[data-internal-task-weekday]") return weekdays;
      return [];
    },
  };
  const requests = [];
  const confirms = [];
  let factory;
  const window = {
    HelpdeskSidebar: { init: async () => ({ role: "admin" }) },
    HelpdeskAppShell: { registerPage: (name, value) => { assert.equal(name, "management-tasks"); factory = value; } },
    confirm: (message) => {
      confirms.push(message);
      return options.confirm ? options.confirm(message) : true;
    },
  };
  vm.runInNewContext(source, {
    window,
    Date: dateConstructor,
    URLSearchParams,
    encodeURIComponent,
    setTimeout,
    clearTimeout,
    fetch: async (url, options = {}) => {
      requests.push({ url, options });
      const payload = handler ? handler(url, options) : defaultPayload(url);
      if (payload && Object.prototype.hasOwnProperty.call(payload, "ok") && Object.prototype.hasOwnProperty.call(payload, "text")) {
        return { ok: payload.ok, status: payload.status || 200, text: async () => payload.text };
      }
      if (payload && Object.prototype.hasOwnProperty.call(payload, "ok") && Object.prototype.hasOwnProperty.call(payload, "payload")) {
        return { ok: payload.ok, status: payload.status || 200, text: async () => JSON.stringify(payload.payload) };
      }
      return { ok: true, status: 200, text: async () => JSON.stringify(payload) };
    },
  });
  const lifecycle = factory({ root });
  await settle();
  return { el, tabs, scopes, panels, requests, confirms, lifecycle, hooks: window.ManagementTasksTestHooks, weekdays };
}

function defaultPayload(url) {
  if (url === "/api/v1/management/internal-tasks/options") {
    return {
      current_admin_id: "admin-1",
      app_timezone: "Europe/Moscow",
      assignees: [{ id: "agent-1", full_name: "Agent One", role: "agent" }],
      admins: [{ id: "admin-1", full_name: "Admin One", role: "admin" }],
    };
  }
  if (url.startsWith("/api/v1/management/internal-tasks/overview")) {
    return {
      active: [{ definition_id: "d1", occurrence_id: "o1", ticket_id: "t1", title: "Active", current_assignee: { full_name: "Agent One" }, responsible_admin: { full_name: "Admin One" }, ticket_status: "pending", materialized_at: "2026-09-16T10:00:00" }],
      scheduled: [{ kind: "scheduled", definition_id: "d2", title: "Scheduled", description: "Text", assignment_mode: "distribution", responsible_admin: { id: "admin-1", full_name: "Admin One" }, display_author: true, notify_on_completion: true, scheduled_for: "2026-09-17T10:00:00" }],
      completed: [{ definition_id: "d3", occurrence_id: "o3", ticket_id: "t3", title: "Done", current_assignee: { full_name: "Agent One" }, responsible_admin: { full_name: "Admin One" }, ticket_status: "closed", closed_at: "2026-09-16T11:00:00" }],
      recurring: [
        { kind: "recurring", definition_id: "r1", title: "Recurring", description: "Repeat", assignment_mode: "distribution", responsible_admin: { id: "admin-1", full_name: "Admin One" }, display_author: true, notify_on_completion: true, deadline_at: null, state: "active", recurrence_mode: "selected_weekdays", recurrence_config: { weekdays: [1, 3], time: "10:00" }, cycle_start_at: "2026-09-17T10:00:00", cycle_end_mode: "never", cycle_end_at: null, cycle_max_occurrences: null, next_occurrence_at: "2026-09-18T10:00:00" },
        { kind: "recurring", definition_id: "r2", title: "Finished recurring", description: "Done", assignment_mode: "distribution", responsible_admin: { id: "admin-1", full_name: "Admin One" }, display_author: true, notify_on_completion: true, deadline_at: null, state: "finished", recurrence_mode: "daily", recurrence_config: { times: ["10:00"] }, cycle_start_at: "2026-09-17T10:00:00", cycle_end_mode: "count", cycle_end_at: null, cycle_max_occurrences: 1, next_occurrence_at: null },
      ],
    };
  }
  return { ok: true };
}

function sidebarLinks(template) {
  return [...template.matchAll(/<a class="([^"]*settings-link[^"]*)" href="([^"]+)">([\s\S]*?)<\/a>/g)].map((match) => ({
    className: match[1],
    href: match[2],
    html: match[3],
  }));
}

test("management tasks route template and navigation are registered", () => {
  assert.match(fs.readFileSync("api/app/main.py", "utf8"), /@app\.get\("\/management\/tasks"\)/);
  assert.match(fs.readFileSync("api/app/static/app_shell.js", "utf8"), /"\/management\/tasks".*"management-tasks"/);
  assert.match(fs.readFileSync("api/app/templates/management.html", "utf8"), /href="\/management\/tasks"[\s\S]*Задачи и Регулярки/);
  const template = fs.readFileSync("api/app/templates/management_tasks.html", "utf8");
  assert.match(template, /data-shell-page="management-tasks"/);
  assert.match(template, /class="settings-title">Задачи и Регулярки<\/div>/);
  assert.match(template, /data-internal-task-tab="active"/);
  assert.match(template, /data-internal-task-tab="scheduled"/);
  assert.match(template, /data-internal-task-tab="recurring"/);
  assert.match(template, />Регулярные</);
  assert.match(template, /<option value="recurring">Регулярка<\/option>/);
  assert.match(template, />Начало расписания/);
  assert.match(template, />Пропустить следующий запуск</);
  assert.match(template, /data-internal-task-tab="completed"/);
  assert.match(template, /id="internalTaskAssignmentMode"/);
  assert.match(template, /id="internalTaskDeadlineEnabled"/);
  assert.match(template, /id="internalTaskRecurrenceMode"/);
  assert.match(template, /id="internalTasksRecurringBody"/);
  const styles = fs.readFileSync("api/app/static/styles.css", "utf8");
  assert.match(styles, /\.management-tasks-tabs \.action/);
  assert.match(styles, /\.management-tasks-page \[hidden\]/);
  assert.match(styles, /\.management-tasks-modal \[hidden\]/);
  assert.match(styles, /\.management-tasks-assignment-grid[\s\S]*grid-template-areas/);
  assert.match(styles, /\.management-tasks-form \.field-help/);
  assert.doesNotMatch(styles, /\.management-tasks-page[\s\S]*width: min\(100%, 1280px\)/);
  assert.match(template, /management-tasks-assignment-mode/);
  assert.match(template, /management-tasks-assignee-field/);
  assert.match(template, /management-tasks-responsible-field/);
  assert.match(template, /management-tasks-checkbox-row"><input id="internalTaskDisplayAuthor"/);
  assert.match(template, /management-tasks-checkbox-row"><input id="internalTaskNotify"/);
  assert.match(template, /management-tasks-checkbox-row">\s*<input id="internalTaskDeadlineEnabled"/);
  assert.match(template, /Можно указать несколько значений через запятую: 10:00, 11:30, 16:35\. Допустим также формат 10\.00\./);
  assert.match(template, /id="internalTaskDailyTimesError" class="field-error" hidden/);
});

test("admin sidebar structure is consistent across main template surfaces", () => {
  for (const file of sidebarTemplateFiles) {
    const template = fs.readFileSync(file, "utf8");
    const links = sidebarLinks(template);
    assert.deepEqual(
      links.map((link) => link.href),
      file.endsWith("/settings.html") ? ["/settings", "/statuses", "/management/tasks"] : ["/settings", "/management", "/statuses", "/management/tasks"],
      file,
    );
    assert.match(template, /sidebar-admin-separator/, file);
    assert.doesNotMatch(links.map((link) => link.href).join(" "), /mass-notifications/, file);
    assert.match(links[0].html, /settings-gear\.svg/, file);
    assert.doesNotMatch(links[1].html, /settings-gear\.svg/, file);
  }

  const expectedActive = new Map([
    ["api/app/templates/settings.html", "/settings"],
    ["api/app/templates/management.html", "/management"],
    ["api/app/templates/statuses.html", "/statuses"],
    ["api/app/templates/management_tasks.html", "/management/tasks"],
  ]);
  for (const file of expectedActive.keys()) {
    const active = sidebarLinks(fs.readFileSync(file, "utf8"))
      .filter((link) => link.className.includes("active"))
      .map((link) => link.href);
    assert.deepEqual(active, [expectedActive.get(file)], file);
  }
});

test("management tasks loads options overview and renders tabs", async () => {
  const ui = await load();
  assert.equal(ui.requests.some((item) => item.url === "/api/v1/management/internal-tasks/options"), true);
  assert.equal(ui.requests.some((item) => item.url === "/api/v1/management/internal-tasks/overview?scope=all"), true);
  assert.match(ui.el.ActiveBody.html, /Active/);
  assert.match(ui.el.ActiveBody.html, /13:00/);
  assert.match(ui.el.ScheduledBody.html, /Scheduled/);
  assert.match(ui.el.RecurringBody.html, /Recurring/);
  assert.match(ui.el.RecurringBody.html, /Finished recurring/);
  assert.match(ui.el.RecurringBody.html, /Завершена/);
  assert.match(ui.el.CompletedBody.html, /Done/);
  assert.equal(ui.el.Status.text, "");
  assert.equal(ui.el.Status.hidden, true);
  ui.tabs[1].fire("click");
  assert.equal(ui.panels[1].hidden, false);
  assert.equal(ui.panels[0].hidden, true);
  ui.tabs[2].fire("click");
  assert.equal(ui.panels[2].hidden, false);
});

test("scope switch requests mine filter", async () => {
  const ui = await load();
  ui.scopes[1].fire("click");
  await settle();
  assert.equal(ui.requests.some((item) => item.url === "/api/v1/management/internal-tasks/overview?scope=mine"), true);
});

test("create form sends immediate scheduled and edit payloads to expected endpoints", async () => {
  const ui = await load();
  ui.el.CreateBtn.fire("click");
  ui.el.Title.value = "Title";
  ui.el.Description.value = "Description";
  ui.el.AssignmentMode.value = "direct";
  ui.el.Assignee.value = "agent-1";
  ui.el.Responsible.value = "admin-1";
  ui.el.DisplayAuthor.checked = true;
  ui.el.Notify.checked = true;
  ui.el.DeadlineEnabled.checked = true;
  ui.el.DeadlineAt.value = "2026-09-18T12:00";
  ui.el.SaveBtn.fire("click");
  await settle();
  const immediate = ui.requests.find((item) => item.url === "/api/v1/management/internal-tasks" && item.options.method === "POST");
  assert.ok(immediate);
  assert.equal(JSON.parse(immediate.options.body).assignee_user_id, "agent-1");
  assert.equal(JSON.parse(immediate.options.body).deadline_at, "2026-09-18T09:00:00.000Z");

  ui.el.CreateBtn.fire("click");
  ui.el.Title.value = "Later";
  ui.el.Description.value = "Description";
  ui.el.AssignmentMode.value = "distribution";
  ui.el.Timing.value = "scheduled";
  ui.el.ScheduledAt.value = "2026-09-19T09:30";
  ui.el.SaveBtn.fire("click");
  await settle();
  const scheduled = ui.requests.find((item) => item.url === "/api/v1/management/internal-tasks/scheduled" && item.options.method === "POST");
  assert.ok(scheduled);
  assert.equal(JSON.parse(scheduled.options.body).scheduled_for, "2026-09-19T09:30");

  ui.el.ScheduledBody.fire("click", { target: { closest: (selector) => (selector === "[data-edit-scheduled]" ? { dataset: { editScheduled: "d2" } } : null) } });
  assert.equal(ui.el.ScheduledAt.value, "2026-09-17T13:00");
  assert.match(ui.el.ScheduleInfo.textContent, /Создание задачи:/);
  assert.match(ui.el.ScheduleInfo.textContent, /13:00/);
  ui.el.Title.value = "Edited";
  ui.el.SaveBtn.fire("click");
  await settle();
  const edit = ui.requests.find((item) => item.url === "/api/v1/management/internal-tasks/scheduled/d2" && item.options.method === "PATCH");
  assert.ok(edit);
  assert.equal(JSON.parse(edit.options.body).scheduled_for, "2026-09-17T13:00");
});

test("create form fallback scheduled time uses app timezone", async () => {
  class FixedDate extends Date {
    static now() {
      return Date.parse("2026-09-17T09:30:00Z");
    }
  }

  const ui = await load(null, FixedDate);
  ui.el.CreateBtn.fire("click");
  assert.equal(ui.el.ScheduledAt.value, "2026-09-17T13:00");
  assert.equal(ui.hooks.fallbackFutureLocalInput(), "2026-09-17T13:00");
});

test("dirty task modal confirms destructive close attempts and keeps clean close silent", async () => {
  const rejected = await load(null, Date, { confirm: () => false });
  rejected.el.CreateBtn.fire("click");
  rejected.el.CancelBtn.fire("click");
  assert.equal(rejected.el.Modal.classList.contains("hidden"), true);
  assert.equal(rejected.confirms.length, 0);

  rejected.el.CreateBtn.fire("click");
  rejected.el.Title.value = "Draft";
  rejected.el.CancelBtn.fire("click");
  assert.equal(rejected.el.Modal.classList.contains("hidden"), false);
  assert.equal(rejected.confirms.at(-1), "Вы уверены, что хотите закрыть окно?\nВсе несохранённые данные будут потеряны.");

  rejected.el.CloseModalBtn.fire("click");
  assert.equal(rejected.el.Modal.classList.contains("hidden"), false);
  assert.equal(rejected.confirms.length, 2);

  const accepted = await load(null, Date, { confirm: () => true });
  accepted.el.ScheduledBody.fire("click", { target: { closest: (selector) => (selector === "[data-edit-scheduled]" ? { dataset: { editScheduled: "d2" } } : null) } });
  accepted.el.Title.value = "Changed";
  accepted.el.Modal.fire("pointerdown", { target: accepted.el.Modal });
  accepted.el.Modal.fire("click", { target: accepted.el.Modal });
  assert.equal(accepted.el.Modal.classList.contains("hidden"), true);
  assert.equal(accepted.confirms.at(-1), "Вы уверены, что хотите закрыть окно?\nВсе несохранённые данные будут потеряны.");
});

test("successful save closes dirty modal without unsaved changes confirmation", async () => {
  const ui = await load(null, Date, { confirm: () => false });
  ui.el.CreateBtn.fire("click");
  ui.el.Title.value = "Title";
  ui.el.Description.value = "Description";
  ui.el.Responsible.value = "admin-1";
  ui.el.SaveBtn.fire("click");
  await settle();

  assert.equal(ui.el.Modal.classList.contains("hidden"), true);
  assert.deepEqual(ui.confirms, []);
  assert.equal(ui.requests.some((item) => item.url === "/api/v1/management/internal-tasks" && item.options.method === "POST"), true);
});

test("recurring form renders conditional fields disables inactive branches and sends create payload", async () => {
  const ui = await load();
  ui.el.CreateBtn.fire("click");
  assert.equal(ui.el.ScheduledAtWrap.hidden, true);
  assert.equal(ui.el.ScheduledAt.disabled, true);
  assert.equal(ui.el.RecurrenceWrap.hidden, true);
  assert.equal(ui.el.RecurrenceMode.disabled, true);

  ui.el.Timing.value = "scheduled";
  ui.el.Timing.fire("change");
  assert.equal(ui.el.ScheduledAtWrap.hidden, false);
  assert.equal(ui.el.ScheduledAt.disabled, false);
  assert.equal(ui.el.RecurrenceWrap.hidden, true);
  assert.equal(ui.el.RecurrenceMode.disabled, true);

  ui.el.Timing.value = "recurring";
  ui.el.Timing.fire("change");
  assert.equal(ui.el.RecurrenceWrap.hidden, false);
  assert.equal(ui.el.RecurrenceMode.disabled, false);
  assert.equal(ui.el.DailyTimesWrap.hidden, false);
  assert.equal(ui.el.DailyTimes.disabled, false);
  assert.equal(ui.el.RecurrenceTimeWrap.hidden, true);
  assert.equal(ui.el.RecurrenceTime.disabled, true);

  ui.el.RecurrenceMode.value = "selected_weekdays";
  ui.el.RecurrenceMode.fire("change");
  assert.equal(ui.el.WeekdaysWrap.hidden, false);
  assert.equal(ui.weekdays[0].disabled, false);
  assert.equal(ui.el.DailyTimesWrap.hidden, true);
  assert.equal(ui.el.DailyTimes.disabled, true);
  assert.equal(ui.el.RecurrenceTimeWrap.hidden, false);
  assert.equal(ui.el.RecurrenceTime.disabled, false);

  ui.el.RecurrenceMode.value = "weekly";
  ui.el.RecurrenceMode.fire("change");
  assert.equal(ui.el.RecurrenceWeekdayWrap.hidden, false);
  assert.equal(ui.el.RecurrenceWeekday.disabled, false);
  assert.equal(ui.el.WeekdaysWrap.hidden, true);
  assert.equal(ui.weekdays[0].disabled, true);

  ui.el.RecurrenceMode.value = "monthly";
  ui.el.RecurrenceMode.fire("change");
  assert.equal(ui.el.MonthlyDayWrap.hidden, false);
  assert.equal(ui.el.MonthlyDay.disabled, false);
  assert.equal(ui.el.RecurrenceWeekdayWrap.hidden, true);
  assert.equal(ui.el.RecurrenceWeekday.disabled, true);

  ui.el.RecurrenceMode.value = "every_n_days";
  ui.el.RecurrenceMode.fire("change");
  assert.equal(ui.el.EveryNDaysWrap.hidden, false);
  assert.equal(ui.el.EveryNDays.disabled, false);
  assert.equal(ui.el.MonthlyDayWrap.hidden, true);
  assert.equal(ui.el.MonthlyDay.disabled, true);

  ui.el.RecurrenceMode.value = "selected_weekdays";
  ui.el.RecurrenceMode.fire("change");
  ui.el.CycleEndMode.value = "at";
  ui.el.CycleEndMode.fire("change");
  assert.equal(ui.el.CycleEndAtWrap.hidden, false);
  assert.equal(ui.el.CycleEndAt.disabled, false);
  assert.equal(ui.el.CycleMaxWrap.hidden, true);
  assert.equal(ui.el.CycleMax.disabled, true);

  ui.el.CycleEndMode.value = "count";
  ui.el.CycleEndMode.fire("change");
  assert.equal(ui.el.CycleEndAtWrap.hidden, true);
  assert.equal(ui.el.CycleEndAt.disabled, true);
  assert.equal(ui.el.CycleMaxWrap.hidden, false);
  assert.equal(ui.el.CycleMax.disabled, false);

  ui.el.Title.value = "Recurring create";
  ui.el.Description.value = "Description";
  ui.el.Responsible.value = "admin-1";
  ui.el.CycleStartAt.value = "2026-09-17T13:00";
  ui.weekdays[0].checked = true;
  ui.weekdays[2].checked = true;
  ui.el.RecurrenceTime.value = "10:00";
  ui.el.CycleEndMode.value = "count";
  ui.el.CycleEndMode.fire("change");
  ui.el.CycleMax.value = "3";
  ui.el.SaveBtn.fire("click");
  await settle();

  const request = ui.requests.find((item) => item.url === "/api/v1/management/internal-tasks/recurring" && item.options.method === "POST");
  assert.ok(request);
  const body = JSON.parse(request.options.body);
  assert.equal(body.recurrence_mode, "selected_weekdays");
  assert.deepEqual(body.recurrence_config, { weekdays: [1, 3], time: "10:00" });
  assert.equal(body.cycle_start_at, "2026-09-17T13:00");
  assert.equal(body.cycle_end_mode, "count");
  assert.equal(body.cycle_max_occurrences, 3);
});

test("daily recurrence validates invalid times inline and clears after correction", async () => {
  const ui = await load();
  ui.el.CreateBtn.fire("click");
  ui.el.Timing.value = "recurring";
  ui.el.Timing.fire("change");
  ui.el.RecurrenceMode.value = "daily";
  ui.el.RecurrenceMode.fire("change");
  ui.el.Title.value = "Recurring create";
  ui.el.Description.value = "Description";
  ui.el.Responsible.value = "admin-1";
  ui.el.CycleStartAt.value = "2026-09-17T13:00";
  ui.el.DailyTimes.value = "10:00, 25:90, 16.30";

  ui.el.SaveBtn.fire("click");
  await settle();

  assert.equal(ui.el.DailyTimesError.hidden, false);
  assert.match(ui.el.DailyTimesError.textContent, /Некорректное время: 25:90/);
  assert.equal(ui.requests.some((item) => item.url === "/api/v1/management/internal-tasks/recurring" && item.options.method === "POST"), false);

  ui.el.DailyTimes.value = "10:00, 16.30, 10:00";
  ui.el.DailyTimes.fire("input");
  assert.equal(ui.el.DailyTimesError.hidden, true);
  ui.el.SaveBtn.fire("click");
  await settle();

  const request = ui.requests.find((item) => item.url === "/api/v1/management/internal-tasks/recurring" && item.options.method === "POST");
  assert.ok(request);
  assert.deepEqual(JSON.parse(request.options.body).recurrence_config, { times: ["10:00", "16:30"] });
});

test("recurring edit pause and resume use recurring endpoints without changing scope", async () => {
  const ui = await load();
  ui.scopes[1].fire("click");
  await settle();
  ui.el.RecurringBody.fire("click", { target: { closest: (selector) => (selector === "[data-edit-recurring]" ? { dataset: { editRecurring: "r1" } } : null) } });
  assert.equal(ui.el.Timing.value, "recurring");
  assert.equal(ui.el.RecurrenceMode.value, "selected_weekdays");
  assert.equal(ui.el.CycleStartAt.value, "2026-09-17T13:00");
  assert.equal(ui.el.SkipNextBtn.hidden, false);
  assert.match(ui.el.ScheduleInfo.textContent, /Следующее создание задачи:/);
  assert.match(ui.el.ScheduleInfo.textContent, /13:00/);
  ui.el.SkipNextBtn.fire("click");
  await settle();
  assert.equal(ui.requests.some((item) => item.url === "/api/v1/management/internal-tasks/recurring/r1/skip-next" && item.options.method === "POST"), true);
  assert.match(ui.confirms.at(-1), /Пропустить ближайший запуск 18\.09\.2026 в 13:00\?/);
  assert.match(ui.confirms.at(-1), /Следующие запуски продолжат работать по расписанию\./);
  assert.equal(ui.weekdays[0].checked, true);
  ui.el.Title.value = "Recurring edit";
  ui.el.SaveBtn.fire("click");
  await settle();

  const edit = ui.requests.find((item) => item.url === "/api/v1/management/internal-tasks/recurring/r1" && item.options.method === "PATCH");
  assert.ok(edit);
  assert.equal(JSON.parse(edit.options.body).title, "Recurring edit");
  assert.equal(ui.requests.some((item) => item.url === "/api/v1/management/internal-tasks/overview?scope=mine"), true);

  ui.el.RecurringBody.fire("click", { target: { closest: (selector) => (selector === "[data-pause-recurring]" ? { dataset: { pauseRecurring: "r1" } } : null) } });
  await settle();
  assert.equal(ui.requests.some((item) => item.url === "/api/v1/management/internal-tasks/recurring/r1/pause" && item.options.method === "POST"), true);

  ui.el.RecurringBody.fire("click", { target: { closest: (selector) => (selector === "[data-resume-recurring]" ? { dataset: { resumeRecurring: "r2" } } : null) } });
  await settle();
  assert.equal(ui.requests.some((item) => item.url === "/api/v1/management/internal-tasks/recurring/r2/resume" && item.options.method === "POST"), true);
});

test("delete actions use local internal task endpoints", async () => {
  const ui = await load();

  ui.el.ActiveBody.fire("click", { target: { closest: (selector) => (selector === "[data-delete-materialized]" ? { dataset: { deleteMaterialized: "t1" } } : null) } });
  await settle();
  assert.equal(ui.requests.some((item) => item.url === "/api/v1/management/internal-tasks/materialized/t1" && item.options.method === "DELETE"), true);
  assert.equal(ui.confirms.at(-1), "Удалить задачу? Это действие нельзя отменить.");

  ui.el.CompletedBody.fire("click", { target: { closest: (selector) => (selector === "[data-delete-materialized]" ? { dataset: { deleteMaterialized: "t3" } } : null) } });
  await settle();
  assert.equal(ui.requests.some((item) => item.url === "/api/v1/management/internal-tasks/materialized/t3" && item.options.method === "DELETE"), true);

  ui.el.ScheduledBody.fire("click", { target: { closest: (selector) => (selector === "[data-delete-scheduled]" ? { dataset: { deleteScheduled: "d2" } } : null) } });
  await settle();
  assert.equal(ui.requests.some((item) => item.url === "/api/v1/management/internal-tasks/scheduled/d2" && item.options.method === "DELETE"), true);
  assert.equal(ui.confirms.at(-1), "Удалить задачу? Это действие нельзя отменить.");

  ui.el.RecurringBody.fire("click", { target: { closest: (selector) => (selector === "[data-delete-recurring]" ? { dataset: { deleteRecurring: "r1" } } : null) } });
  await settle();
  assert.equal(ui.requests.some((item) => item.url === "/api/v1/management/internal-tasks/recurring/r1" && item.options.method === "DELETE"), true);
  assert.equal(ui.confirms.at(-1), "Удалить регулярную задачу?\nНовые задачи по этому расписанию больше создаваться не будут.\nУже созданные задачи останутся.");
});

test("recurring pause or resume error is shown in page validation", async () => {
  const ui = await load((url) => {
    if (url === "/api/v1/management/internal-tasks/recurring/r1/pause") {
      return {
        ok: false,
        status: 409,
        payload: { detail: { error: { message: "Лимит повторений уже исчерпан" } } },
      };
    }
    return defaultPayload(url);
  });

  ui.el.RecurringBody.fire("click", { target: { closest: (selector) => (selector === "[data-pause-recurring]" ? { dataset: { pauseRecurring: "r1" } } : null) } });
  await settle();

  assert.equal(ui.el.Validation.hidden, false);
  assert.match(ui.el.Validation.textContent, /Лимит повторений уже исчерпан/);
});

test("task overview errors remain visible while successful ready status stays hidden", async () => {
  const ui = await load((url) => {
    if (url.startsWith("/api/v1/management/internal-tasks/overview")) {
      return { ok: false, status: 503, text: "Internal Server Error" };
    }
    return defaultPayload(url);
  });
  assert.equal(ui.el.Status.text, "Ошибка");
  assert.equal(ui.el.Status.hidden, false);
  assert.equal(ui.el.Validation.hidden, false);
});

test("server errors show neutral text while structured business messages remain visible", async () => {
  const business = await load((url) => {
    if (url === "/api/v1/management/internal-tasks/recurring/r1/pause") {
      return {
        ok: false,
        status: 409,
        payload: { detail: { error: { message: "Цикл уже завершён" } } },
      };
    }
    return defaultPayload(url);
  });

  business.el.RecurringBody.fire("click", { target: { closest: (selector) => (selector === "[data-pause-recurring]" ? { dataset: { pauseRecurring: "r1" } } : null) } });
  await settle();
  assert.match(business.el.Validation.textContent, /Цикл уже завершён/);

  const server = await load((url) => {
    if (url === "/api/v1/management/internal-tasks/recurring/r1/pause") {
      return {
        ok: false,
        status: 500,
        text: "Internal Server Error",
      };
    }
    return defaultPayload(url);
  });

  server.el.RecurringBody.fire("click", { target: { closest: (selector) => (selector === "[data-pause-recurring]" ? { dataset: { pauseRecurring: "r1" } } : null) } });
  await settle();
  assert.doesNotMatch(server.el.Validation.textContent, /Internal Server Error/);
  assert.match(server.el.Validation.textContent, /Не удалось выполнить запрос\. Попробуйте ещё раз\./);

  const jsonServer = await load((url) => {
    if (url === "/api/v1/management/internal-tasks/recurring/r1/pause") {
      return {
        ok: false,
        status: 500,
        payload: { message: "Internal Server Error" },
      };
    }
    return defaultPayload(url);
  });

  jsonServer.el.RecurringBody.fire("click", { target: { closest: (selector) => (selector === "[data-pause-recurring]" ? { dataset: { pauseRecurring: "r1" } } : null) } });
  await settle();
  assert.doesNotMatch(jsonServer.el.Validation.textContent, /Internal Server Error/);
  assert.match(jsonServer.el.Validation.textContent, /Не удалось выполнить запрос\. Попробуйте ещё раз\./);
});
