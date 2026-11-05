const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

process.env.TZ = "UTC";

const source = fs.readFileSync("api/app/static/reviews.js", "utf8");

class Element {
  constructor(tag = "div") {
    this.tagName = tag;
    this.children = [];
    this.handlers = {};
    this.attributes = {};
    this.value = "";
    this.min = "";
    this.max = "";
    this.hidden = false;
    this.disabled = false;
    this.className = "";
    this.clientHeight = 75;
    this.scrollHeight = 75;
    this.classList = {
      toggle: (name, enabled) => {
        const names = new Set(this.className.split(" "));
        if (enabled) names.add(name); else names.delete(name);
        this.className = [...names].join(" ");
      },
    };
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; this.text = ""; }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return (this.text || "") + this.children.map((child) => child.textContent).join(""); }
  setAttribute(key, value) { this.attributes[key] = value; }
  removeAttribute(key) { this[key] = ""; delete this.attributes[key]; }
  addEventListener(key, handler) { (this.handlers[key] ||= []).push(handler); }
  removeEventListener(key, handler) { this.handlers[key] = this.handlers[key].filter((item) => item !== handler); }
  fire(key) { for (const handler of this.handlers[key] || []) handler({ target: this }); }
  checkValidity() { return Boolean(this.value && (!this.min || this.value >= this.min) && (!this.max || this.value <= this.max)); }
}

const current = {
  review_id: "review-1", ticket_id: "ticket-1", rated_at: "2026-09-06T10:00:00Z",
  client_name_snapshot: "<Client>", ticket_subject_snapshot: "<Ticket>",
  rating: 5, review_text: "Первая строка\nВторая строка", version_number: 3, has_history: true,
};
function list(items = [current], overrides = {}) {
  return { items, total_count: items.length, average_rating: items.length ? 4.666 : null,
    min_review_date: null, page: 1, pages: 1, page_size: 30, ...overrides };
}
const employees = [
  { employee_id: "old", display_name: "Z Old", is_active: false },
  { employee_id: "active", display_name: "A Active", is_active: true },
];
const tick = () => new Promise((resolve) => setImmediate(resolve));
async function settle() { await tick(); await tick(); }

async function load(role = "agent", handler = () => list(), {
  appTimezone = "UTC", now = "2026-09-07T12:00:00Z",
} = {}) {
  const el = {};
  for (const key of ["EmployeeField", "EmployeeHeader", "DateFrom", "DateTo", "Stats", "Count", "Average", "Status",
    "TableWrap", "TableBody", "Pagination", "Prev", "Next", "PageSummary"]) el[key] = new Element();
  el.DateFrom.disabled = el.DateTo.disabled = true;
  const root = { querySelector: (selector) => el[selector.slice("#review".length)] };
  const requests = [];
  let factory;
  let measure;
  const window = {
    location: { href: "/reviews" },
    HelpdeskSidebar: { init: async () => ({ role, appTimezone }) },
    HelpdeskAppShell: { registerPage: (name, value) => { assert.equal(name, "reviews"); factory = value; } },
  };
  class FixedDate extends Date {
    constructor(...args) { super(...(args.length ? args : [now])); }
  }
  vm.runInNewContext(source, {
    window, document: { createElement: (tag) => new Element(tag) },
    Date: FixedDate, URLSearchParams, AbortController,
    ResizeObserver: class {
      constructor(callback) { measure = callback; }
      observe() {}
      disconnect() {}
    },
    fetch: async (url, options) => {
      requests.push({ url, options });
      const payload = url.endsWith("/employees") ? employees : await handler(url, options);
      return { ok: true, status: 200, json: async () => payload };
    },
  });
  const destroy = factory({ root });
  await settle();
  return { el, window, requests, destroy, measure: () => measure() };
}
function descendants(node, predicate) {
  return node.children.flatMap((child) => [...(predicate(child) ? [child] : []), ...descendants(child, predicate)]);
}
function button(node, label) {
  return descendants(node, (child) => child.tagName === "button" && child.textContent === label)[0];
}
function employeeSelect(ui) {
  return descendants(ui.el.EmployeeField, (child) => child.tagName === "select")[0];
}

for (const scenario of [
  { appTimezone: "Asia/Tokyo", now: "2026-12-31T23:30:00Z", today: "2027-01-01", first: "2027-01-01" },
  { appTimezone: "America/Los_Angeles", now: "2027-01-01T00:30:00Z", today: "2026-12-31", first: "2026-12-01" },
]) {
  for (const role of ["agent", "trainee", "admin"]) {
    test("application calendar controls period and max: " + scenario.appTimezone + " / " + role, async () => {
      assert.equal(new Date(scenario.now).getTimezoneOffset(), 0);
      const ui = await load(role, () => list(), scenario);
      if (role === "admin") {
        assert.equal(ui.el.DateFrom.disabled, false);
        const select = employeeSelect(ui);
        select.value = "active";
        select.fire("change");
        await settle();
      }
      const query = new URL(ui.requests.at(-1).url, "http://local").searchParams;
      assert.equal(query.get("date_from"), scenario.first);
      assert.equal(query.get("date_to"), scenario.today);
      assert.equal(ui.el.DateFrom.max, scenario.today);
      assert.equal(ui.el.DateTo.max, scenario.today);
      assert.equal(ui.requests.some((request) => request.url.includes("/auth/session")), false);
    });
  }

  test("current and history timestamps use application timezone: " + scenario.appTimezone, async () => {
    const item = { ...current, rated_at: scenario.now };
    const ui = await load("agent", (url) => url.endsWith("/history")
      ? { items: [{ ...item, review_id: "old-review", version_number: 2 }] }
      : list([item]), scenario);
    const date = new Date(scenario.now);
    const expected = date.toLocaleString("ru-RU", {
      timeZone: scenario.appTimezone, dateStyle: "short", timeStyle: "medium",
    });
    assert.notEqual(expected, date.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "medium" }));
    assert.equal(ui.el.TableBody.children[0].children[0].textContent, expected);
    button(ui.el.TableBody, "История").fire("click");
    await settle();
    const historyMeta = ui.el.TableBody.children[1].children[0].children[0].children[0];
    assert.equal(historyMeta.children[1].textContent, expected);
  });
}

test("admin defaults to all employees, loads automatically and preserves API employee ordering", async () => {
  const ui = await load("admin");
  assert.equal(ui.requests[0].url, "/api/v1/reviews/employees");
  assert.equal(ui.requests.length, 2);
  assert.equal(new URL(ui.requests[1].url, "http://local").searchParams.has("employee_id"), false);
  assert.equal(ui.el.DateFrom.disabled, false);
  assert.equal(ui.el.DateTo.disabled, false);
  assert.equal(ui.el.Count.textContent, "1");
  const select = employeeSelect(ui);
  assert.equal(select.value, "");
  assert.deepEqual(select.children.map((item) => item.textContent),
    ["Все сотрудники", "Z Old (неактивен)", "A Active"]);
  select.value = "old";
  select.fire("change");
  await settle();
  const query = new URL(ui.requests.at(-1).url, "http://local").searchParams;
  assert.equal(query.get("employee_id"), "old");
  assert.equal(query.get("date_from"), "2026-09-01");
  assert.equal(query.get("date_to"), "2026-09-07");
  select.value = "";
  select.fire("change");
  assert.equal(ui.el.DateFrom.disabled, false);
  assert.equal(ui.el.DateTo.disabled, false);
  assert.equal(ui.el.Stats.hidden, true);
  assert.equal(ui.el.TableBody.children.length, 0);
  await settle();
  assert.equal(ui.requests.length, 4);
  assert.equal(new URL(ui.requests.at(-1).url, "http://local").searchParams.has("employee_id"), false);
  assert.equal(ui.el.Count.textContent, "1");
});

for (const role of ["agent", "trainee"]) {
  test(role + " loads own reviews without employee_id and renders snapshots safely", async () => {
    const ui = await load(role);
    assert.equal(ui.el.EmployeeField.children.length, 0);
    assert.equal(ui.requests.length, 1);
    const query = new URL(ui.requests[0].url, "http://local").searchParams;
    assert.equal(query.has("employee_id"), false);
    assert.equal(query.get("page_size"), "30");
    assert.equal(ui.el.Average.textContent, "4,67");
    const row = ui.el.TableBody.children[0];
    assert.equal(row.children.length, 5);
    assert.equal(row.children[0].textContent,
      new Date(current.rated_at).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "medium" }));
    assert.equal(row.children[1].textContent, "<Client>");
    const link = row.children[2].children[0];
    assert.equal(link.textContent, "<Ticket>");
    assert.equal(link.href, "/tickets/ticket-1");
    assert.equal(link.target, "_blank");
    assert.match(row.children[4].textContent, /Первая строка\nВторая строка/);
    assert.equal(row.textContent.includes("Версия"), false);
    assert.equal(ui.el.Pagination.hidden, true);
  });
}

for (const role of ["agent", "admin"]) {
  test(role + " min date clamps defaults and reloads the actual period; empty stats use a dash", async () => {
    const ui = await load(role, () => list([], { min_review_date: "2026-09-04" }));
    assert.equal(ui.requests.length, role === "admin" ? 3 : 2);
    assert.equal(ui.el.DateFrom.value, "2026-09-04");
    assert.equal(ui.el.DateFrom.min, "2026-09-04");
    assert.equal(ui.el.DateTo.max, "2026-09-07");
    assert.equal(ui.el.Count.textContent, "0");
    assert.equal(ui.el.Average.textContent, "—");
    assert.equal(ui.el.TableWrap.hidden, true);
    assert.equal(ui.el.Status.textContent, "За выбранный период отзывов нет");
  });
}

test("history loads lazily, preserves order and dates outside the filter, and is cached per row", async () => {
  const history = [
    { rated_at: "2026-08-20T10:00:00Z", rating: 4, review_text: null, version_number: 2 },
    { rated_at: "2026-07-20T10:00:00Z", rating: 3, review_text: "Ранее", version_number: 1 },
  ];
  const ui = await load("agent", (url) => url.endsWith("/history") ? { items: history } : list());
  assert.equal(ui.requests.length, 1);
  const toggle = button(ui.el.TableBody, "История");
  toggle.fire("click");
  await settle();
  assert.equal(ui.requests[1].url, "/api/v1/reviews/review-1/history");
  const content = ui.el.TableBody.children[1].children[0];
  assert.equal(content.children.length, 2);
  assert.match(content.children[0].textContent, /Версия 2/);
  assert.match(content.children[0].textContent, /—/);
  assert.match(content.children[1].textContent, /Версия 1/);
  toggle.fire("click");
  toggle.fire("click");
  await settle();
  assert.equal(ui.requests.length, 2);
  ui.el.DateFrom.value = "2026-09-02";
  ui.el.DateFrom.fire("change");
  assert.equal(ui.el.TableBody.children.length, 0);
  await settle();
  assert.equal(ui.el.TableBody.children[1].hidden, true);
  button(ui.el.TableBody, "История").fire("click");
  await settle();
  assert.equal(ui.requests.filter((request) => request.url.endsWith("/history")).length, 2);
});

test("text expansion uses measured overflow and preserves line breaks", async () => {
  const ui = await load();
  const cell = ui.el.TableBody.children[0].children[4];
  const text = cell.children[0];
  const toggle = button(cell, "Показать полностью");
  ui.measure();
  assert.equal(toggle.hidden, true);
  text.scrollHeight = 150;
  ui.measure();
  assert.equal(toggle.hidden, false);
  toggle.fire("click");
  assert.equal(toggle.textContent, "Свернуть");
  assert.equal(text.className.includes("reviews-text-clamped"), false);
  toggle.fire("click");
  assert.equal(text.className.includes("reviews-text-clamped"), true);
  assert.equal(text.textContent, current.review_text);
});

test("switching employees resets period, minimum date and history", async () => {
  const ui = await load("admin", (url) => url.endsWith("/history")
    ? { items: [{ rated_at: current.rated_at, rating: 4, version_number: 2, review_text: null }] }
    : list([current], { min_review_date: "2026-08-01" }));
  const select = employeeSelect(ui);
  select.value = "old";
  select.fire("change");
  await settle();
  ui.el.DateFrom.value = "2026-08-15";
  ui.el.DateFrom.fire("change");
  await settle();
  button(ui.el.TableBody, "История").fire("click");
  await settle();
  assert.equal(ui.el.TableBody.children[1].hidden, false);
  select.value = "active";
  select.fire("change");
  assert.equal(ui.el.DateFrom.min, "");
  assert.equal(ui.el.TableBody.children.length, 0);
  await settle();
  const query = new URL(ui.requests.at(-1).url, "http://local").searchParams;
  assert.equal(query.get("employee_id"), "active");
  assert.equal(query.get("date_from"), "2026-09-01");
  assert.equal(query.get("page"), "1");
  assert.equal(ui.el.TableBody.children[1].hidden, true);
});

for (const role of ["agent", "admin"]) {
  test(role + " pagination preserves filters, clears history, and date changes reset page and fix reversed dates", async () => {
    const ui = await load(role, (url) => list([current], {
      page: Number(new URL(url, "http://local").searchParams.get("page")), pages: 3, total_count: 80,
    }));
    ui.el.Next.fire("click");
    await settle();
    let query = new URL(ui.requests.at(-1).url, "http://local").searchParams;
    assert.equal(query.get("page"), "2");
    assert.equal(query.get("date_from"), "2026-09-01");
    assert.equal(ui.el.PageSummary.textContent, "Страница 2 из 3");
    ui.el.DateTo.value = "2026-08-31";
    ui.el.DateTo.fire("change");
    await settle();
    query = new URL(ui.requests.at(-1).url, "http://local").searchParams;
    assert.equal(query.get("page"), "1");
    assert.equal(query.get("date_from"), "2026-08-31");
    assert.equal(query.get("date_to"), "2026-08-31");
    assert.equal(ui.el.TableBody.children[1].hidden, true);
  });
}

test("late list responses cannot replace a newer period; destroy cancels active requests", async () => {
  const pending = [];
  const ui = await load("agent", () => new Promise((resolve) => pending.push(resolve)));
  ui.el.DateFrom.value = "2026-09-02";
  ui.el.DateFrom.fire("change");
  assert.equal(ui.requests[0].options.signal.aborted, true);
  pending[1](list([], { total_count: 0 }));
  await settle();
  pending[0](list([current]));
  await settle();
  assert.equal(ui.el.Count.textContent, "0");
  assert.equal(ui.el.TableBody.children.length, 0);
  ui.destroy();
  assert.equal(ui.requests[1].options.signal.aborted, true);
});

test("history arriving after a filter change is discarded and errors do not retain old results", async () => {
  let resolveHistory;
  let failList = false;
  const ui = await load("agent", (url) => {
    if (url.endsWith("/history")) return new Promise((resolve) => { resolveHistory = resolve; });
    if (failList) throw new Error("Internal stack must never reach UI");
    return list();
  });
  button(ui.el.TableBody, "История").fire("click");
  failList = true;
  ui.el.DateFrom.value = "2026-09-02";
  ui.el.DateFrom.fire("change");
  resolveHistory({ items: [{ version_number: 1, rating: 1, review_text: "Old" }] });
  await settle();
  assert.equal(ui.el.TableBody.children.length, 0);
  assert.equal(ui.el.Stats.hidden, true);
  assert.equal(ui.el.Status.textContent, "Не удалось загрузить отзывы. Повторите попытку.");
});

test("late all-employee and employee responses cannot replace a newer filter", async () => {
  const pending = [];
  const ui = await load("admin", () => new Promise((resolve) => pending.push(resolve)));
  const select = employeeSelect(ui);
  select.value = "old";
  select.fire("change");
  select.value = "";
  select.fire("change");
  ui.el.DateFrom.value = "2026-09-02";
  ui.el.DateFrom.fire("change");
  pending[3](list([], { total_count: 0 }));
  await settle();
  for (const resolve of pending.slice(0, 3)) resolve(list([current]));
  await settle();
  assert.equal(ui.el.Count.textContent, "0");
  assert.equal(ui.el.TableBody.children.length, 0);
  assert.equal(ui.el.Status.textContent, "За выбранный период отзывов нет");
  assert.ok(ui.requests.slice(1, -1).every(request => request.options.signal.aborted));
  const query = new URL(ui.requests.at(-1).url, "http://local").searchParams;
  assert.equal(query.has("employee_id"), false);
  assert.equal(query.get("date_from"), "2026-09-02");
  assert.equal(query.get("page"), "1");
});

for (const role of ["admin", "agent", "trainee"]) {
  test(role + " employee name column and history width", async () => {
    const name = '<img src=x onerror=alert(1)>';
    const ui = await load(role, () => list([
      { ...current, agent_full_name: name },
      { ...current, review_id: "review-2", agent_full_name: null },
    ]));
    const rows = ui.el.TableBody.children;
    assert.equal(ui.el.EmployeeHeader.hidden, role !== "admin");
    assert.equal(rows[0].children.length, role === "admin" ? 6 : 5);
    assert.equal(rows[1].children[0].colSpan, role === "admin" ? 6 : 5);
    if (role === "admin") {
      assert.equal(rows[0].children[0].textContent, name);
      assert.equal(rows[0].children[0].children.length, 0);
      assert.equal(rows[2].children[0].textContent, "—");
    }
  });
}
