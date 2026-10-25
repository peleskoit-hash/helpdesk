const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

process.env.TZ = "UTC";

const source = fs.readFileSync("api/app/static/management_statistics.js", "utf8");

class Element {
  constructor(dataset = {}, metrics = {}) {
    this.dataset = dataset;
    this.handlers = {};
    this.hidden = false;
    this.value = "";
    this.text = "";
    this.html = "";
    this.disabled = false;
    this.clientHeight = metrics.clientHeight || 0;
    this.offsetHeight = metrics.offsetHeight || this.clientHeight;
    this.rectTop = metrics.top || 0;
    this.rectHeight = metrics.height ?? this.clientHeight;
    this.rowHeight = metrics.rowHeight || 30;
    this.firstElementChild = null;
    this.className = dataset.statisticsTab === "work-time" ? "action active" : "action";
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
  set innerHTML(value) {
    this.html = String(value);
    this.firstElementChild = /<tr[\s>]/.test(this.html) ? new Element({}, { clientHeight: this.rowHeight }) : null;
  }
  get innerHTML() { return this.html || ""; }
  getBoundingClientRect() { return { top: this.rectTop, height: this.rectHeight || this.clientHeight || this.offsetHeight || 0 }; }
  querySelector(selector) { return selector === "tr" ? this.firstElementChild : null; }
  addEventListener(key, handler) { (this.handlers[key] ||= []).push(handler); }
  removeEventListener(key, handler) { this.handlers[key] = (this.handlers[key] || []).filter((item) => item !== handler); }
  fire(key, event = {}) { for (const handler of this.handlers[key] || []) handler({ target: this, ...event }); }
}

const users = [
  { id: "agent-a", full_name: "Agent A", role: "agent", is_active: true },
  { id: "trainee-a", full_name: "Trainee A", role: "trainee", is_active: true },
  { id: "inactive-agent", full_name: "Inactive Agent", role: "agent", is_active: false },
  { id: "inactive-trainee", full_name: "Inactive Trainee", role: "trainee", is_active: false },
  { id: "admin-a", full_name: "Admin A", role: "admin", is_active: true },
  { id: "chiki-a", full_name: "Chiki A", role: "chiki_piki", is_active: true },
];

const workTimePayload = {
  total: { tickets: 3, tasks: 2, objects: 9, average_seconds: 3660, median_seconds: 90 },
  items: [
    { user_id: "agent-a", full_name: "Agent A", is_active: true, tickets: 1, tasks: 1, objects: 5, average_seconds: 3600, median_seconds: 90 },
    { user_id: "inactive-agent", full_name: "Inactive Agent", is_active: false, tickets: 0, tasks: 1, objects: 1, average_seconds: 45, median_seconds: 45 },
    { user_id: "deleted-agent", full_name: null, is_active: null, tickets: 2, tasks: 0, objects: 3, average_seconds: 3660, median_seconds: 0 },
  ],
};

const statusesPayload = {
  total: { online_seconds: 90061, on_ticket_seconds: 3660, waiting_seconds: 90, lunch_seconds: 45, offline_seconds: 0, total_seconds: 180000 },
  items: [
    { user_id: "agent-a", full_name: "Agent A", is_active: true, online_seconds: 90, on_ticket_seconds: 3660, waiting_seconds: 45, lunch_seconds: 0, offline_seconds: 0, total_seconds: 9000 },
    { user_id: "inactive-agent", full_name: "Inactive Agent", is_active: false, online_seconds: 45, on_ticket_seconds: 0, waiting_seconds: 0, lunch_seconds: 0, offline_seconds: 0, total_seconds: 45 },
    { user_id: "deleted-agent", full_name: null, is_active: null, online_seconds: 0, on_ticket_seconds: 0, waiting_seconds: 90, lunch_seconds: 3660, offline_seconds: 90061, total_seconds: 180000 },
  ],
};

const transitionsPayload = {
  items: [
    { user_id: "agent-a", full_name: "Agent A", is_active: true, from_status: "online", to_status: "on_ticket", changed_at: "2026-09-02T10:10:05" },
    { user_id: "inactive-agent", full_name: "Inactive Agent", is_active: false, from_status: "waiting", to_status: "lunch", changed_at: "2026-09-02T09:00:00" },
    { user_id: "deleted-agent", full_name: null, is_active: null, from_status: "offline", to_status: "online", changed_at: "2026-09-02T08:00:00" },
  ],
  next_cursor: null,
  has_more: false,
};

const tick = () => new Promise((resolve) => setImmediate(resolve));
async function settle() { await tick(); await tick(); await new Promise((resolve) => setTimeout(resolve, 0)); }
async function waitForResize() { await new Promise((resolve) => setTimeout(resolve, 150)); await settle(); }

async function load({
  handler,
  role = "admin",
  appTimezone = "UTC",
  now = "2026-09-13T12:00:00Z",
  transitionViewportHeight = 304,
  transitionViewportTop = 0,
  transitionPaginationTop = null,
  transitionHeadHeight = 34,
  transitionRowHeight = 30,
} = {}) {
  const el = {};
  for (const key of ["Status", "Employee", "Period", "DateFrom", "DateTo", "Validation", "Summary", "TableBody", "Empty",
    "WorkTimeSummary", "WorkTimeTableBody", "WorkTimeEmpty",
    "StatusesSummary", "StatusesTableBody", "StatusesEmpty",
    "TransitionsEmpty", "TransitionsPrev", "TransitionsNext", "TransitionsPage"]) {
    el[key] = new Element();
  }
  el.TransitionsTableBody = new Element({}, { rowHeight: transitionRowHeight });
  el.TransitionsViewport = new Element({}, { clientHeight: transitionViewportHeight, height: transitionViewportHeight, top: transitionViewportTop });
  el.TransitionsHead = new Element({}, { clientHeight: transitionHeadHeight });
  el.TransitionsPagination = new Element({}, { top: transitionPaginationTop ?? transitionViewportHeight });
  el.MedianHelp = new Element();
  const tabs = [
    new Element({ statisticsTab: "work-time" }),
    new Element({ statisticsTab: "statuses" }),
  ];
  const panels = [
    new Element({ statisticsPanel: "work-time" }),
    new Element({ statisticsPanel: "statuses" }),
  ];
  const customFields = [new Element(), new Element()];
  el.Period.value = "30days";

  const root = {
    querySelector: (selector) => {
      if (selector === ".management-statistics-median-help") return el.MedianHelp;
      if (selector === ".management-statistics-transition-log .management-statistics-pagination") return el.TransitionsPagination;
      return el[selector.slice("#managementStatistics".length)];
    },
    querySelectorAll: (selector) => {
      if (selector === "[data-statistics-tab]") return tabs;
      if (selector === "[data-statistics-panel]") return panels;
      if (selector === ".management-statistics-custom") return customFields;
      return [];
    },
  };
  const requests = [];
  const resizeObservers = [];
  let factory;
  const window = {
    location: { href: "/management/statistics" },
    HelpdeskSidebar: { init: async () => ({ role, appTimezone }) },
    HelpdeskAppShell: { registerPage: (name, value) => { assert.equal(name, "management-statistics"); factory = value; } },
    ResizeObserver: class {
      constructor(callback) {
        this.callback = callback;
        this.targets = [];
        resizeObservers.push(this);
      }
      observe(target) { this.targets.push(target); }
      disconnect() { this.targets = []; }
      trigger() { this.callback(this.targets.map((target) => ({ target }))); }
    },
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
    setTimeout,
    clearTimeout,
    fetch: async (url, options) => {
      requests.push({ url, options });
      const payload = handler
        ? await handler(url, options)
        : url === "/api/v1/settings/users"
          ? users
          : url.startsWith("/api/v1/management/statistics/work-time")
            ? workTimePayload
            : url.startsWith("/api/v1/management/statistics/status-transitions")
              ? transitionsPayload
            : url.startsWith("/api/v1/management/statistics/statuses")
              ? statusesPayload
            : workTimePayload;
      if (payload instanceof Error) {
        return { ok: false, status: 500, text: async () => payload.message };
      }
      return { ok: true, status: 200, text: async () => JSON.stringify(payload) };
    },
  });
  const destroy = factory({ root });
  await settle();
  return { el, tabs, panels, customFields, requests, resizeObservers, destroy, hooks: window.ManagementStatisticsTestHooks };
}

function statisticsRequests(ui, endpoint) {
  return ui.requests.filter((request) => request.url.startsWith("/api/v1/management/statistics/" + endpoint));
}

function lastQuery(ui, endpoint = "work-time") {
  const requests = statisticsRequests(ui, endpoint);
  return new URL(requests.at(-1).url, "http://local").searchParams;
}

test("management statistics route and navigation are registered", () => {
  assert.match(fs.readFileSync("api/app/main.py", "utf8"), /@app\.get\("\/management\/statistics"\)/);
  assert.match(fs.readFileSync("api/app/static/app_shell.js", "utf8"), /"\/management\/statistics".*"management-statistics"/);
  assert.match(fs.readFileSync("api/app/templates/management.html", "utf8"), /href="\/management\/statistics">Статистика/);
  const template = fs.readFileSync("api/app/templates/management_statistics.html", "utf8");
  assert.match(template, /data-shell-page="management-statistics"/);
  assert.doesNotMatch(template, /data-statistics-tab="completed"/);
  assert.doesNotMatch(template, /managementStatisticsTransitions|Лог смен|Общее время/);
  assert.match(template, /data-statistics-tab="work-time">Время выполнения Заявок\/Задач/);
  assert.match(template, /data-statistics-tab="statuses">Статусы сотрудников/);
  assert.match(template, /Медианное значение — время, которое находится посередине выборки/);
  assert.doesNotMatch(template, /<th scope="col">Всего<\/th>/);
  const styles = fs.readFileSync("api/app/static/styles.css", "utf8");
  const v2 = fs.readFileSync("api/app/static/admin-pages-v2.css", "utf8");
  assert.match(v2, /data-shell-page="management-statistics"/);
  assert.match(v2, /grid-template-rows:\s*auto minmax\(0,\s*1fr\)/);
  assert.match(v2, /padding:\s*14px 16px 16px/);
  assert.match(styles, /#managementStatisticsWorkTimePanel:not\(\[hidden\]\),\s*#managementStatisticsStatusesPanel:not\(\[hidden\]\)\s*{[^}]*grid-row:\s*4;[^}]*align-self:\s*stretch;/s);
  assert.match(styles, /\.management-statistics-work-time-table th:nth-child\(5\),[\s\S]*\.management-statistics-work-time-table th:nth-child\(7\),[\s\S]*width:\s*18\.16%;/);
});

test("defaults load all employees and work-time statistics for last 30 days", async () => {
  const ui = await load();
  assert.equal(ui.el.Employee.value, "");
  assert.equal(ui.el.Period.value, "30days");
  assert.match(ui.el.Employee.innerHTML, />Все сотрудники</);
  assert.match(ui.el.Employee.innerHTML, /Agent A/);
  assert.match(ui.el.Employee.innerHTML, /Trainee A/);
  assert.match(ui.el.Employee.innerHTML, /Inactive Agent \(неактивен\)/);
  assert.match(ui.el.Employee.innerHTML, /Inactive Trainee \(неактивен\)/);
  assert.doesNotMatch(ui.el.Employee.innerHTML, /Admin A/);
  assert.doesNotMatch(ui.el.Employee.innerHTML, /Chiki A/);
  const query = lastQuery(ui);
  assert.equal(query.has("user_id"), false);
  assert.equal(query.get("date_from"), "2026-08-15T00:00:00+00:00");
  assert.equal(query.get("date_to"), "2026-09-14T00:00:00+00:00");
  assert.equal(statisticsRequests(ui, "completed").length, 0);
  assert.equal(statisticsRequests(ui, "work-time").length, 1);
});

test("employee filter and all-time period update work-time API params", async () => {
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

test("duration formatter uses at most two significant units", async () => {
  const ui = await load();
  const format = ui.hooks.durationLabel;
  assert.equal(format(0), "0 сек");
  assert.equal(format(45), "45 сек");
  assert.equal(format(90), "1 мин 30 сек");
  assert.equal(format(3600), "1 ч");
  assert.equal(format(3660), "1 ч 1 мин");
  assert.equal(format(90061), "1 д 1 ч");
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

test("invalid custom period shows validation and does not call work-time API", async () => {
  const ui = await load();
  const before = statisticsRequests(ui, "work-time").length;
  ui.el.Period.value = "custom";
  ui.el.DateFrom.value = "2026-09-04";
  ui.el.DateTo.value = "2026-09-03";
  ui.el.Period.fire("change");
  await settle();
  assert.equal(statisticsRequests(ui, "work-time").length, before);
  assert.equal(ui.el.Validation.hidden, false);
  assert.match(ui.el.Validation.textContent, /Дата окончания/);
});

test("invalid custom period does not call statuses API", async () => {
  const ui = await load();
  ui.tabs[1].fire("click");
  await settle();
  const before = statisticsRequests(ui, "statuses").length;
  const transitionsBefore = statisticsRequests(ui, "status-transitions").length;
  ui.el.Period.value = "custom";
  ui.el.DateFrom.value = "2026-09-04";
  ui.el.DateTo.value = "2026-09-03";
  ui.el.Period.fire("change");
  await settle();
  assert.equal(statisticsRequests(ui, "statuses").length, before);
  assert.equal(statisticsRequests(ui, "status-transitions").length, transitionsBefore);
  assert.equal(ui.el.Validation.hidden, false);
});

test("work-time default tab uses shared period and employee filters", async () => {
  const ui = await load({ appTimezone: "Europe/Moscow", now: "2026-09-13T12:00:00Z" });
  const completedBefore = statisticsRequests(ui, "completed").length;
  ui.el.Employee.value = "agent-a";
  ui.el.Period.value = "today";
  ui.el.Period.fire("change");
  await settle();
  assert.equal(statisticsRequests(ui, "completed").length, completedBefore);
  assert.equal(statisticsRequests(ui, "statuses").length, 0);
  let query = lastQuery(ui, "work-time");
  assert.equal(query.get("user_id"), "agent-a");
  assert.equal(query.get("date_from"), "2026-09-13T00:00:00+03:00");
  assert.equal(query.get("date_to"), "2026-09-14T00:00:00+03:00");

  ui.el.Employee.value = "inactive-agent";
  ui.el.Employee.fire("change");
  await settle();
  query = lastQuery(ui, "work-time");
  assert.equal(query.get("user_id"), "inactive-agent");
  assert.equal(statisticsRequests(ui, "completed").length, completedBefore);

  ui.el.Period.value = "all";
  ui.el.Period.fire("change");
  await settle();
  query = lastQuery(ui, "work-time");
  assert.equal(query.get("user_id"), "inactive-agent");
  assert.equal(query.has("date_from"), false);
  assert.equal(query.has("date_to"), false);
});

test("work-time response renders counts durations inactive deleted and empty state", async () => {
  const ui = await load();
  assert.equal(ui.el.WorkTimeSummary.innerHTML, "<span>Объектов: 9</span><span>| Среднее: 1 ч 1 мин</span><span>| Медиана: 1 мин 30 сек</span><span>| Заявки: 3</span><span>| Задачи: 2</span>");
  assert.match(ui.el.WorkTimeTableBody.innerHTML, /Agent A/);
  assert.match(ui.el.WorkTimeTableBody.innerHTML, /<td>1<\/td>/);
  assert.match(ui.el.WorkTimeTableBody.innerHTML, /<td>5<\/td>/);
  assert.doesNotMatch(ui.el.WorkTimeTableBody.innerHTML, /2 ч 30 мин/);
  assert.match(ui.el.WorkTimeTableBody.innerHTML, /Неактивен/);
  assert.match(ui.el.WorkTimeTableBody.innerHTML, /Удалённый сотрудник/);
  assert.match(ui.el.WorkTimeTableBody.innerHTML, /deleted-agent/);
  assert.equal(ui.el.WorkTimeEmpty.hidden, true);

  const empty = await load({ handler: (url) => url === "/api/v1/settings/users" ? users : { total: { tickets: 0, tasks: 0, objects: 0, average_seconds: 0, median_seconds: 0 }, items: [] } });
  assert.equal(empty.el.WorkTimeSummary.innerHTML, "<span>Объектов: 0</span><span>| Среднее: 0 сек</span><span>| Медиана: 0 сек</span><span>| Заявки: 0</span><span>| Задачи: 0</span>");
  assert.equal(empty.el.WorkTimeEmpty.hidden, false);
  assert.equal(empty.el.WorkTimeEmpty.textContent, "За выбранный период времени работы нет");
});

test("median help is visible only on work-time tab", async () => {
  const ui = await load();
  assert.equal(ui.el.MedianHelp.hidden, false);

  ui.tabs[1].fire("click");
  await settle();
  assert.equal(ui.el.MedianHelp.hidden, true);

  ui.tabs[0].fire("click");
  await settle();
  assert.equal(ui.el.MedianHelp.hidden, false);
});

test("work-time API error shows page error", async () => {
  const ui = await load({ handler: (url) => {
    if (url === "/api/v1/settings/users") return users;
    if (url.startsWith("/api/v1/management/statistics/work-time")) return new Error("boom");
    return workTimePayload;
  } });
  assert.equal(ui.el.Status.textContent, "Не удалось загрузить статистику. Повторите попытку.");
  assert.equal(ui.el.WorkTimeEmpty.textContent, "");
});

test("statuses tab calls only statuses API with shared period and employee filters", async () => {
  const ui = await load({ appTimezone: "Europe/Moscow", now: "2026-09-13T12:00:00Z" });
  const completedBefore = statisticsRequests(ui, "completed").length;
  ui.el.Employee.value = "agent-a";
  ui.el.Period.value = "today";
  ui.tabs[1].fire("click");
  await settle();
  assert.equal(statisticsRequests(ui, "completed").length, completedBefore);
  assert.equal(statisticsRequests(ui, "work-time").length, 1);
  let query = lastQuery(ui, "statuses");
  assert.equal(query.get("user_id"), "agent-a");
  assert.equal(query.get("date_from"), "2026-09-13T00:00:00+03:00");
  assert.equal(query.get("date_to"), "2026-09-14T00:00:00+03:00");
  assert.equal(query.has("limit"), false);
  assert.equal(query.has("cursor"), false);

  ui.el.Employee.value = "inactive-agent";
  ui.el.Employee.fire("change");
  await settle();
  query = lastQuery(ui, "statuses");
  assert.equal(query.get("user_id"), "inactive-agent");
  assert.equal(statisticsRequests(ui, "completed").length, completedBefore);

  ui.el.Period.value = "all";
  ui.el.Period.fire("change");
  await settle();
  query = lastQuery(ui, "statuses");
  assert.equal(query.get("user_id"), "inactive-agent");
  assert.equal(query.has("date_from"), false);
  assert.equal(query.has("date_to"), false);
  assert.equal(query.has("limit"), false);
  assert.equal(query.has("cursor"), false);
});

test("statuses response renders durations inactive deleted and backend total", async () => {
  const ui = await load();
  ui.tabs[1].fire("click");
  await settle();
  assert.equal(ui.el.StatusesSummary.innerHTML, "<span>Учтено времени: 2 д 2 ч</span><span>| Не в сети: 0 сек</span><span>| В сети: 1 д 1 ч</span><span>| На заявке: 1 ч 1 мин</span><span>| В ожидании: 1 мин 30 сек</span><span>| Обед: 45 сек</span>");
  assert.match(ui.el.StatusesTableBody.innerHTML, /Agent A/);
  assert.match(ui.el.StatusesTableBody.innerHTML, /1 мин 30 сек/);
  assert.match(ui.el.StatusesTableBody.innerHTML, /1 ч 1 мин/);
  assert.doesNotMatch(ui.el.StatusesTableBody.innerHTML, /2 ч 30 мин/);
  assert.match(ui.el.StatusesTableBody.innerHTML, /Неактивен/);
  assert.match(ui.el.StatusesTableBody.innerHTML, /Удалённый сотрудник/);
  assert.match(ui.el.StatusesTableBody.innerHTML, /deleted-agent/);
  assert.equal(ui.el.StatusesEmpty.hidden, true);

  const totalCells = ui.el.StatusesTableBody.innerHTML.match(/<td>2 д 2 ч<\/td>/g) || [];
  assert.equal(totalCells.length, 0);
});












test("statuses empty response shows zero summary and empty state", async () => {
  const emptyPayload = {
    total: { offline_seconds: 0, online_seconds: 0, on_ticket_seconds: 0, waiting_seconds: 0, lunch_seconds: 0, total_seconds: 0 },
    items: [],
  };
  const ui = await load({ handler: (url) => {
    if (url === "/api/v1/settings/users") return users;
    if (url.startsWith("/api/v1/management/statistics/status-transitions")) return { items: [] };
    if (url.startsWith("/api/v1/management/statistics/statuses")) return emptyPayload;
    return workTimePayload;
  } });
  ui.tabs[1].fire("click");
  await settle();
  assert.equal(ui.el.StatusesSummary.innerHTML, "<span>Учтено времени: 0 сек</span><span>| Не в сети: 0 сек</span><span>| В сети: 0 сек</span><span>| На заявке: 0 сек</span><span>| В ожидании: 0 сек</span><span>| Обед: 0 сек</span>");
  assert.equal(ui.el.StatusesEmpty.hidden, false);
  assert.equal(ui.el.StatusesEmpty.textContent, "За выбранный период данных по статусам нет");
});

test("statuses API error shows page error", async () => {
  const ui = await load({ handler: (url) => {
    if (url === "/api/v1/settings/users") return users;
    if (url.startsWith("/api/v1/management/statistics/statuses")) return new Error("boom");
    return workTimePayload;
  } });
  ui.tabs[1].fire("click");
  await settle();
  assert.equal(ui.el.Status.textContent, "Не удалось загрузить статистику. Повторите попытку.");
  assert.equal(ui.el.StatusesEmpty.textContent, "");
});


test("stale statuses response does not render after switching tabs", async () => {
  let resolveStatuses;
  const delayedStatuses = new Promise((resolve) => { resolveStatuses = resolve; });
  const ui = await load({ handler: (url) => {
    if (url === "/api/v1/settings/users") return users;
    if (url.startsWith("/api/v1/management/statistics/statuses")) return delayedStatuses;
    return workTimePayload;
  } });
  ui.tabs[1].fire("click");
  await tick();
  ui.tabs[0].fire("click");
  resolveStatuses(statusesPayload);
  await settle();
  assert.equal(ui.el.StatusesTableBody.innerHTML, "");
  assert.equal(ui.el.WorkTimeSummary.innerHTML, "<span>Объектов: 9</span><span>| Среднее: 1 ч 1 мин</span><span>| Медиана: 1 мин 30 сек</span><span>| Заявки: 3</span><span>| Задачи: 2</span>");
});
