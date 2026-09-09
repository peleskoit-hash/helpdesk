const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

process.env.TZ = "UTC";

const source = fs.readFileSync("api/app/static/management_feedback.js", "utf8");

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
    this.classList = {
      toggle: (name, enabled) => {
        const names = new Set(this.className.split(" ").filter(Boolean));
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
  removeEventListener(key, handler) { this.handlers[key] = (this.handlers[key] || []).filter((item) => item !== handler); }
  fire(key, event = {}) { for (const handler of this.handlers[key] || []) handler({ target: this, ...event }); }
  checkValidity() { return Boolean(this.value && (!this.min || this.value >= this.min) && (!this.max || this.value <= this.max)); }
}

const item = {
  feedback_id: "feedback-1",
  submitted_at: "2026-09-06T10:00:00Z",
  client_name_snapshot: "<Client>",
  ticket_title_snapshot: "<Ticket>",
  text: "Первая строка\nВторая строка",
  ticket_available: true,
  ticket_id: "ticket-1",
  ticket_number: 557,
  workflow_type: "task",
};

function list(items = [item], overrides = {}) {
  return { items, total_count: items.length, min_feedback_date: null, page: 1, pages: 1, page_size: 30, ...overrides };
}

const tick = () => new Promise((resolve) => setImmediate(resolve));
async function settle() { await tick(); await tick(); }

async function load(handler = () => list(), { role = "admin", appTimezone = "UTC", now = "2026-09-07T12:00:00Z" } = {}) {
  const el = {};
  for (const key of ["Status", "Search", "DateFrom", "DateTo", "SearchBtn", "ResetBtn", "Summary",
    "TableBody", "Empty", "Pagination", "Prev", "Next", "PageSummary"]) el[key] = new Element();
  const root = { querySelector: (selector) => el[selector.slice("#managementFeedback".length)] };
  const requests = [];
  let factory;
  const window = {
    location: { href: "/management/feedback" },
    HelpdeskSidebar: { init: async () => ({ role, appTimezone }) },
    HelpdeskAppShell: { registerPage: (name, value) => { assert.equal(name, "management-feedback"); factory = value; } },
  };
  class FixedDate extends Date {
    constructor(...args) { super(...(args.length ? args : [now])); }
  }
  vm.runInNewContext(source, {
    window, document: { createElement: (tag) => new Element(tag) },
    Date: FixedDate, URLSearchParams, AbortController,
    fetch: async (url, options) => {
      requests.push({ url, options });
      const payload = await handler(url, options);
      return { ok: true, status: 200, json: async () => payload };
    },
  });
  const destroy = factory({ root });
  await settle();
  return { el, requests, destroy };
}

function descendants(node, predicate) {
  return node.children.flatMap((child) => [...(predicate(child) ? [child] : []), ...descendants(child, predicate)]);
}

function button(node, label) {
  return descendants(node, (child) => child.tagName === "button" && child.textContent === label)[0];
}

test("table renders date object client and escaped text data", async () => {
  const ui = await load();
  const row = ui.el.TableBody.children[0];
  assert.equal(row.children.length, 4);
  assert.equal(row.children[0].textContent, new Date(item.submitted_at).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "medium" }));
  assert.match(row.children[1].textContent, /\[Задача\] №557/);
  const link = descendants(row.children[1], (child) => child.tagName === "a")[0];
  assert.equal(link.href, "/tickets/557");
  assert.equal(link.target, "_blank");
  assert.equal(row.children[2].textContent, "<Client>");
  assert.match(row.children[3].textContent, /Первая строка\nВторая строка/);
  assert.equal(ui.el.Status.textContent, "");
  assert.equal(ui.el.Status.hidden, true);
});

test("missing object renders fallback without link", async () => {
  const ui = await load(() => list([{ ...item, ticket_available: false, ticket_number: null, workflow_type: null }]));
  const objectCell = ui.el.TableBody.children[0].children[1];
  assert.match(objectCell.textContent, /Объект недоступен/);
  assert.equal(descendants(objectCell, (child) => child.tagName === "a").length, 0);
});

test("feedback text with fewer than 200 characters has no toggle", async () => {
  const shortText = "Короткое предложение";
  const ui = await load(() => list([{ ...item, text: shortText }]));
  const cell = ui.el.TableBody.children[0].children[3];
  assert.equal(cell.children.length, 1);
  assert.equal(cell.children[0].textContent, shortText);
  assert.equal(button(cell, "Показать полностью"), undefined);
});

test("feedback text with exactly 200 characters has no toggle", async () => {
  const exactText = "а".repeat(200);
  const ui = await load(() => list([{ ...item, text: exactText }]));
  const cell = ui.el.TableBody.children[0].children[3];
  assert.equal(cell.children.length, 1);
  assert.equal(cell.children[0].textContent, exactText);
  assert.equal(button(cell, "Показать полностью"), undefined);
});

test("feedback text over 200 characters expands and collapses by hard limit", async () => {
  const longText = "б".repeat(200) + "в";
  const collapsedText = "б".repeat(200) + "…";
  const ui = await load(() => list([{ ...item, text: longText }]));
  const cell = ui.el.TableBody.children[0].children[3];
  const text = cell.children[0];
  const toggle = button(cell, "Показать полностью");
  assert.equal(cell.children.length, 2);
  assert.equal(text.textContent, collapsedText);
  assert.equal(toggle.hidden, false);
  toggle.fire("click");
  assert.equal(toggle.textContent, "Свернуть");
  assert.equal(text.textContent, longText);
  toggle.fire("click");
  assert.equal(toggle.textContent, "Показать полностью");
  assert.equal(text.textContent, collapsedText);
});

test("empty state search period and pagination use expected API query", async () => {
  const ui = await load((url) => list([], {
    page: Number(new URL(url, "http://local").searchParams.get("page")),
    pages: 3,
    total_count: 70,
  }));
  assert.equal(ui.el.Empty.hidden, false);
  assert.equal(ui.el.Empty.textContent, "Предложений и замечаний за выбранный период нет");
  ui.el.Search.value = "Мария";
  ui.el.SearchBtn.fire("click");
  await settle();
  let query = new URL(ui.requests.at(-1).url, "http://local").searchParams;
  assert.equal(query.get("query"), "Мария");
  assert.equal(query.get("date_from"), "2026-09-01");
  assert.equal(query.get("date_to"), "2026-09-07");
  assert.equal(query.get("page"), "1");
  ui.el.Next.fire("click");
  await settle();
  query = new URL(ui.requests.at(-1).url, "http://local").searchParams;
  assert.equal(query.get("page"), "2");
  ui.el.DateTo.value = "2026-08-31";
  ui.el.DateTo.fire("change");
  await settle();
  query = new URL(ui.requests.at(-1).url, "http://local").searchParams;
  assert.equal(query.get("date_from"), "2026-08-31");
  assert.equal(query.get("date_to"), "2026-08-31");
});

test("reset clears search and restores current month period", async () => {
  const ui = await load();
  ui.el.Search.value = "текст";
  ui.el.DateFrom.value = "2026-08-01";
  ui.el.ResetBtn.fire("click");
  await settle();
  const query = new URL(ui.requests.at(-1).url, "http://local").searchParams;
  assert.equal(query.has("query"), false);
  assert.equal(query.get("date_from"), "2026-09-01");
  assert.equal(query.get("date_to"), "2026-09-07");
});
