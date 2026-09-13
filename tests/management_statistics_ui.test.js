const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

process.env.TZ = "UTC";

const source = fs.readFileSync("api/app/static/management_statistics.js", "utf8");

class Element {
  constructor(dataset = {}) {
    this.dataset = dataset;
    this.handlers = {};
    this.hidden = false;
    this.value = "";
    this.text = "";
    this.html = "";
    this.disabled = false;
    this.className = dataset.statisticsTab === "completed" ? "action active" : "action";
    this.classList = {
      toggle: (name, enabled) => {
        const names = new Set(this.className.split(" ").filter(Boolean));
        if (enabled) names.add(name); else names.delete(name);
        this.className = [...names].join(" ");
      },
    };
  }
  set textContent(value) { this.text = String(value); }
  get textContent() { return this.text || ""; }
  set innerHTML(value) { this.html = String(value); }
  get innerHTML() { return this.html || ""; }
  addEventListener(key, handler) { (this.handlers[key] ||= []).push(handler); }
  removeEventListener(key, handler) { this.handlers[key] = (this.handlers[key] || []).filter((item) => item !== handler); }
  fire(key, event = {}) { for (const handler of this.handlers[key] || []) handler({ target: this, ...event }); }
}

const users = [
  { id: "agent-a", full_name: "Agent A", is_active: true },
  { id: "inactive-agent", full_name: "Inactive Agent", is_active: false },
];

const completedPayload = {
  total: { total: 4, tickets: 2, tasks: 2 },
  items: [
    { user_id: "agent-a", full_name: "Agent A", is_active: true, tickets: 1, tasks: 1, total: 2 },
    { user_id: "inactive-agent", full_name: "Inactive Agent", is_active: false, tickets: 0, tasks: 1, total: 1 },
    { user_id: "deleted-agent", full_name: null, is_active: null, tickets: 1, tasks: 0, total: 1 },
  ],
};

const tick = () => new Promise((resolve) => setImmediate(resolve));
async function settle() { await tick(); await tick(); }

async function load({ handler, role = "admin", appTimezone = "UTC", now = "2026-09-13T12:00:00Z" } = {}) {
  const el = {};
  for (const key of ["Status", "Employee", "Period", "DateFrom", "DateTo", "Validation", "Summary", "TableBody", "Empty"]) {
    el[key] = new Element();
  }
  const tabs = [
    new Element({ statisticsTab: "completed" }),
    new Element({ statisticsTab: "work-time" }),
    new Element({ statisticsTab: "statuses" }),
  ];
  const panels = [
    new Element({ statisticsPanel: "completed" }),
    new Element({ statisticsPanel: "work-time" }),
    new Element({ statisticsPanel: "statuses" }),
  ];
  const customFields = [new Element(), new Element()];
  el.Period.value = "30days";

  const root = {
    querySelector: (selector) => el[selector.slice("#managementStatistics".length)],
    querySelectorAll: (selector) => {
      if (selector === "[data-statistics-tab]") return tabs;
      if (selector === "[data-statistics-panel]") return panels;
      if (selector === ".management-statistics-custom") return customFields;
      return [];
    },
  };
  const requests = [];
  let factory;
  const window = {
    location: { href: "/management/statistics" },
    HelpdeskSidebar: { init: async () => ({ role, appTimezone }) },
    HelpdeskAppShell: { registerPage: (name, value) => { assert.equal(name, "management-statistics"); factory = value; } },
  };
  class FixedDate extends Date {
    constructor(...args) { super(...(args.length ? args : [now])); }
  }
  vm.runInNewContext(source, {
    window,
    Date: FixedDate,
    Intl,
    URLSearchParams,
    AbortController,
    fetch: async (url, options) => {
      requests.push({ url, options });
      const payload = handler ? await handler(url, options) : (url === "/api/v1/settings/users" ? users : completedPayload);
      if (payload instanceof Error) {
        return { ok: false, status: 500, text: async () => payload.message };
      }
      return { ok: true, status: 200, text: async () => JSON.stringify(payload) };
    },
  });
  const destroy = factory({ root });
  await settle();
  return { el, tabs, panels, customFields, requests, destroy, hooks: window.ManagementStatisticsTestHooks };
}

function lastQuery(ui) {
  const completedRequests = ui.requests.filter((request) => request.url.startsWith("/api/v1/management/statistics/completed"));
  return new URL(completedRequests.at(-1).url, "http://local").searchParams;
}

test("management statistics route and navigation are registered", () => {
  assert.match(fs.readFileSync("api/app/main.py", "utf8"), /@app\.get\("\/management\/statistics"\)/);
  assert.match(fs.readFileSync("api/app/static/app_shell.js", "utf8"), /"\/management\/statistics".*"management-statistics"/);
  assert.match(fs.readFileSync("api/app/templates/management.html", "utf8"), /href="\/management\/statistics">Статистика/);
  const template = fs.readFileSync("api/app/templates/management_statistics.html", "utf8");
  assert.match(template, /data-shell-page="management-statistics"/);
  assert.match(template, /data-statistics-tab="completed">Выполнено/);
  assert.match(template, /data-statistics-tab="work-time">Время работы/);
  assert.match(template, /data-statistics-tab="statuses">Статусы сотрудников/);
});

test("defaults load all employees and completed statistics for last 30 days", async () => {
  const ui = await load();
  assert.equal(ui.el.Employee.value, "");
  assert.equal(ui.el.Period.value, "30days");
  assert.match(ui.el.Employee.innerHTML, />Все сотрудники</);
  assert.match(ui.el.Employee.innerHTML, /Inactive Agent \(неактивен\)/);
  const query = lastQuery(ui);
  assert.equal(query.has("user_id"), false);
  assert.equal(query.get("date_from"), "2026-08-15T00:00:00+00:00");
  assert.equal(query.get("date_to"), "2026-09-14T00:00:00+00:00");
});

test("employee filter and all-time period update completed API params", async () => {
  const ui = await load();
  ui.el.Employee.value = "agent-a";
  ui.el.Employee.fire("change");
  await settle();
  assert.equal(lastQuery(ui).get("user_id"), "agent-a");

  ui.el.Period.value = "all";
  ui.el.Period.fire("change");
  await settle();
  const query = lastQuery(ui);
  assert.equal(query.get("user_id"), "agent-a");
  assert.equal(query.has("date_from"), false);
  assert.equal(query.has("date_to"), false);
});

test("custom period sends inclusive end date as the next day boundary", async () => {
  const ui = await load();
  ui.el.Period.value = "custom";
  ui.el.DateFrom.value = "2026-09-02";
  ui.el.DateTo.value = "2026-09-03";
  ui.el.Period.fire("change");
  await settle();
  const query = lastQuery(ui);
  assert.equal(query.get("date_from"), "2026-09-02T00:00:00+00:00");
  assert.equal(query.get("date_to"), "2026-09-04T00:00:00+00:00");
  assert.equal(ui.el.Validation.hidden, true);
});

test("period bounds use application timezone offset instead of runtime timezone", async () => {
  assert.equal(new Date("2026-09-13T12:00:00Z").getTimezoneOffset(), 0);
  const ui = await load({ appTimezone: "Europe/Moscow", now: "2026-09-13T12:00:00Z" });
  ui.el.Period.value = "today";
  ui.el.Period.fire("change");
  await settle();
  let query = lastQuery(ui);
  assert.equal(query.get("date_from"), "2026-09-13T00:00:00+03:00");
  assert.equal(query.get("date_to"), "2026-09-14T00:00:00+03:00");

  ui.el.Period.value = "custom";
  ui.el.DateFrom.value = "2026-09-02";
  ui.el.DateTo.value = "2026-09-03";
  ui.el.Period.fire("change");
  await settle();
  query = lastQuery(ui);
  assert.equal(query.get("date_from"), "2026-09-02T00:00:00+03:00");
  assert.equal(query.get("date_to"), "2026-09-04T00:00:00+03:00");
});

test("timezone boundary helper uses date-specific DST offset", async () => {
  const ui = await load();
  const hooks = ui.hooks;
  assert.equal(hooks.isoBoundaryInTimezone("2026-03-29", "Europe/Berlin"), "2026-03-29T00:00:00+01:00");
  assert.equal(hooks.isoBoundaryInTimezone("2026-03-30", "Europe/Berlin"), "2026-03-30T00:00:00+02:00");
});

test("invalid custom period shows validation and does not call completed API", async () => {
  const ui = await load();
  const before = ui.requests.length;
  ui.el.Period.value = "custom";
  ui.el.DateFrom.value = "2026-09-04";
  ui.el.DateTo.value = "2026-09-03";
  ui.el.Period.fire("change");
  await settle();
  assert.equal(ui.requests.length, before);
  assert.equal(ui.el.Validation.hidden, false);
  assert.match(ui.el.Validation.textContent, /Дата окончания/);
});

test("completed response renders totals inactive users deleted fallback and empty state", async () => {
  const ui = await load();
  assert.equal(ui.el.Summary.textContent, "Всего выполнено: 4 · Заявки: 2 · Задачи: 2");
  assert.match(ui.el.TableBody.innerHTML, /Agent A/);
  assert.match(ui.el.TableBody.innerHTML, /Неактивен/);
  assert.match(ui.el.TableBody.innerHTML, /Удалённый сотрудник/);
  assert.match(ui.el.TableBody.innerHTML, /deleted-agent/);
  assert.equal(ui.el.Empty.hidden, true);

  const empty = await load({ handler: (url) => url === "/api/v1/settings/users" ? users : { total: { total: 0, tickets: 0, tasks: 0 }, items: [] } });
  assert.equal(empty.el.Empty.hidden, false);
  assert.equal(empty.el.Empty.textContent, "За выбранный период выполненных объектов нет");
  assert.equal(empty.el.Summary.textContent, "Всего выполнено: 0 · Заявки: 0 · Задачи: 0");
});

test("completed API error shows page error", async () => {
  const ui = await load({ handler: (url) => url === "/api/v1/settings/users" ? users : new Error("boom") });
  assert.equal(ui.el.Status.textContent, "Не удалось загрузить статистику. Повторите попытку.");
  assert.equal(ui.el.Empty.textContent, "");
});

test("future tabs do not call work-time statuses or completed endpoints on filter changes", async () => {
  const ui = await load();
  const before = ui.requests.length;
  ui.tabs[1].fire("click");
  await settle();
  ui.el.Employee.value = "agent-a";
  ui.el.Employee.fire("change");
  ui.el.Period.value = "all";
  ui.el.Period.fire("change");
  await settle();
  assert.equal(ui.requests.length, before);
  assert.equal(ui.requests.some((request) => request.url.includes("/work-time")), false);
  assert.equal(ui.requests.some((request) => request.url.includes("/statuses")), false);
});
