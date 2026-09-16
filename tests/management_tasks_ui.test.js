const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("api/app/static/management_tasks.js", "utf8");

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

async function load(handler, dateConstructor = Date) {
  const el = {};
  for (const key of [
    "Status", "Validation", "CreateBtn", "ActiveBody", "ActiveEmpty", "ScheduledBody", "ScheduledEmpty",
    "CompletedBody", "CompletedEmpty", "Modal", "ModalTitle", "CloseModalBtn", "CancelBtn", "SaveBtn",
    "Form", "Title", "Description", "AssignmentMode", "AssigneeWrap", "Assignee", "Responsible",
    "DisplayAuthor", "Notify", "Timing", "ScheduledAtWrap", "ScheduledAt", "DeadlineEnabled",
    "DeadlineAtWrap", "DeadlineAt", "FormError",
  ]) {
    el[key] = new Element();
  }
  el.Modal.className = "modal hidden";
  el.AssignmentMode.value = "distribution";
  el.Timing.value = "now";
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
      return [];
    },
  };
  const requests = [];
  let factory;
  const window = {
    HelpdeskSidebar: { init: async () => ({ role: "admin" }) },
    HelpdeskAppShell: { registerPage: (name, value) => { assert.equal(name, "management-tasks"); factory = value; } },
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
      return { ok: true, status: 200, text: async () => JSON.stringify(payload) };
    },
  });
  const lifecycle = factory({ root });
  await settle();
  return { el, tabs, scopes, panels, requests, lifecycle, hooks: window.ManagementTasksTestHooks };
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
      scheduled: [{ definition_id: "d2", title: "Scheduled", description: "Text", assignment_mode: "distribution", responsible_admin: { id: "admin-1", full_name: "Admin One" }, display_author: true, notify_on_completion: true, scheduled_for: "2026-09-17T10:00:00" }],
      completed: [{ definition_id: "d3", occurrence_id: "o3", ticket_id: "t3", title: "Done", current_assignee: { full_name: "Agent One" }, responsible_admin: { full_name: "Admin One" }, ticket_status: "closed", closed_at: "2026-09-16T11:00:00" }],
      recurring: [],
    };
  }
  return { ok: true };
}

test("management tasks route template and navigation are registered", () => {
  assert.match(fs.readFileSync("api/app/main.py", "utf8"), /@app\.get\("\/management\/tasks"\)/);
  assert.match(fs.readFileSync("api/app/static/app_shell.js", "utf8"), /"\/management\/tasks".*"management-tasks"/);
  assert.match(fs.readFileSync("api/app/templates/management.html", "utf8"), /href="\/management\/tasks">Задачи/);
  const template = fs.readFileSync("api/app/templates/management_tasks.html", "utf8");
  assert.match(template, /data-shell-page="management-tasks"/);
  assert.match(template, /data-internal-task-tab="active"/);
  assert.match(template, /data-internal-task-tab="scheduled"/);
  assert.match(template, /data-internal-task-tab="recurring"/);
  assert.match(template, /data-internal-task-tab="completed"/);
  assert.match(template, /id="internalTaskAssignmentMode"/);
  assert.match(template, /id="internalTaskDeadlineEnabled"/);
});

test("management tasks loads options overview and renders tabs", async () => {
  const ui = await load();
  assert.equal(ui.requests.some((item) => item.url === "/api/v1/management/internal-tasks/options"), true);
  assert.equal(ui.requests.some((item) => item.url === "/api/v1/management/internal-tasks/overview?scope=all"), true);
  assert.match(ui.el.ActiveBody.html, /Active/);
  assert.match(ui.el.ActiveBody.html, /13:00/);
  assert.match(ui.el.ScheduledBody.html, /Scheduled/);
  assert.match(ui.el.CompletedBody.html, /Done/);
  ui.tabs[1].fire("click");
  assert.equal(ui.panels[1].hidden, false);
  assert.equal(ui.panels[0].hidden, true);
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

  ui.el.ScheduledBody.fire("click", { target: { closest: () => ({ dataset: { editScheduled: "d2" } }) } });
  assert.equal(ui.el.ScheduledAt.value, "2026-09-17T13:00");
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
});
