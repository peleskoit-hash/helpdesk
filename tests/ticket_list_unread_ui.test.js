const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { chromium } = require("playwright");

const source = fs.readFileSync("api/app/static/list.js", "utf8");
const cellSource = source.slice(source.indexOf("function ticketCellHtml("), source.indexOf("function renderTable()"));
const escapeHtml = value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;");
function cell(role, count, assignee = "agent", subject = "Тема заявки", taskKind = null) {
  const context = vm.createContext({ state: { role, userId: "agent" }, intakeBadge: () => "", escapeHtml });
  vm.runInContext(cellSource, context);
  const linked = (className, text, width, html) => `<td class="${className} ticket-linked-cell"><a class="ticket-row-link" href="/tickets/1">${html ? text : escapeHtml(text)}</a></td>`;
  return context.ticketCellHtml({ key: "subject", width: 240 }, { subject, assignee_id: assignee, unread_count: count, task_kind: taskKind }, "/tickets/1", linked);
}

test("unread count appears only for the responsible agent or trainee", () => {
  for (const role of ["agent", "trainee"]) {
    for (const count of [1, 5, 12]) assert.match(cell(role, count), new RegExp(`>\\+${count}</span>`));
    for (const count of [0, -1, undefined]) assert.doesNotMatch(cell(role, count), /ticket-unread-count/);
    assert.doesNotMatch(cell(role, 5, "other"), /ticket-unread-count/);
  }
  for (const role of ["admin", "chiki_piki"]) assert.doesNotMatch(cell(role, 12), /ticket-unread-count/);
  assert.match(cell("agent", 1, "agent", "<unsafe>"), /&lt;unsafe>/);
});

test("group parent keeps MAIN before its title and unread count", () => {
  for (const count of [0, 5, 12]) {
    const html = cell("agent", count, "agent", "Групповая задача", "group_parent");
    assert.match(html, /<span class="ticket-main-badge">MAIN<\/span><span class="ticket-main-subject">Групповая задача<\/span>/);
    if (count) assert.match(html, new RegExp(`Групповая задача</span><span class="ticket-unread-count"[^>]*>\\+${count}</span>`));
    else assert.doesNotMatch(html, /ticket-unread-count/);
  }
});

let browser;
test.before(async () => { browser = await chromium.launch({ channel: "msedge", headless: true }); });
test.after(async () => { await browser?.close(); });

for (const theme of ["light", "dark"]) {
  test(`badge geometry, contrast and long titles: ${theme}`, async t => {
    const page = await browser.newPage({ viewport: { width: 1000, height: 800 } });
    t.after(() => page.close());
    await page.route("**/*", route => {
      const p = new URL(route.request().url()).pathname;
      const file = path.resolve("api/app", p.slice(1));
      if (!p.startsWith("/static/") || !fs.existsSync(file)) return route.abort();
      return route.fulfill({ body: fs.readFileSync(file), contentType: p.endsWith(".css") ? "text/css" : "application/octet-stream" });
    });
    const colors = ["request", "task", "group-task", "internal-task", "frozen", "closed"];
    const rows = colors.map((color, i) => `<tr class="ticket-row ticket-color-${color} ${i % 2 ? "ticket-row-unseen" : ""}">${cell("agent", [1, 5, 12][i % 3], "agent", i ? "Длинный заголовок ".repeat(30) : "Короткий")}</tr>`).join("");
    await page.setContent(`<link rel="stylesheet" href="http://desk.test/static/styles.css"><body data-theme="${theme}" class="ticket-columns-fixed"><div class="layout"><main id="appContent" class="main"><div class="table-wrap"><table style="width:240px;table-layout:fixed;min-width:0"><tbody>${rows}</tbody></table></div></main></div></body>`);
    await page.waitForFunction(() => getComputedStyle(document.querySelector(".ticket-unread-count")).flexShrink === "0");
    for (const width of [240, 400]) {
      await page.locator("table").evaluate((e, width) => e.style.width = `${width}px`, width);
      const metrics = await page.locator("tr").evaluateAll(rows => rows.map(row => {
        const badge = row.querySelector(".ticket-unread-count"), title = row.querySelector(".ticket-unread-subject"), td = row.firstElementChild;
        const b = badge.getBoundingClientRect(), a = title.getBoundingClientRect(), c = td.getBoundingClientRect();
        const bs = getComputedStyle(badge), ts = getComputedStyle(title);
        return { gap: b.left - a.right, inside: b.right <= c.right && b.left >= c.left,
                 difference: parseFloat(bs.fontSize) - parseFloat(ts.fontSize),
                 weight: ts.fontWeight, long: title.scrollWidth > title.clientWidth,
                 foreground: bs.color, background: bs.backgroundColor };
      }));
      for (const [i, m] of metrics.entries()) {
        assert.equal(m.gap, 15);
        assert.equal(m.inside, true);
        assert.equal(m.difference, 2);
        assert.equal(m.weight, i % 2 ? "700" : "400");
        if (i) assert.equal(m.long, true);
        assert.equal(m.foreground, "rgb(255, 255, 255)");
        assert.equal(m.background, "rgb(67, 56, 202)");
      }
      assert.ok(Math.abs(await page.locator("table").evaluate(e => e.getBoundingClientRect().width) - width) < 1);
    }
  });
}


for (const role of ["agent", "trainee", "admin"]) {
  test(`full list preserves row emphasis and uses server counts: ${role}`, async t => {
    const page = await browser.newPage({ viewport: { width: 1200, height: 800 } });
    page.setDefaultTimeout(5000);
    t.after(() => page.close());
    const errors = [], requests = [];
    page.on("pageerror", e => errors.push(e.message));
    const items = [1, 5, 12].map((count, i) => ({ id: String(i + 1), subject: `Заявка ${i + 1}`,
      assignee_id: "agent", seen_by_agent: i !== 0, unread_count: count,
      status: "new", workflow_type: "ticket", channel: "web", created_at: "2026-10-01T08:00:00Z" }));
    await page.route("http://desk.test/**", route => {
      const p = new URL(route.request().url()).pathname;
      if (p.startsWith("/static/")) {
        const f = path.resolve("api/app", p.slice(1));
        return route.fulfill({ body: fs.readFileSync(f), contentType: f.endsWith(".css") ? "text/css" : "application/octet-stream" });
      }
      if (!p.startsWith("/api/")) return route.fulfill({ contentType: "text/html", body: fs.readFileSync("api/app/templates/tickets.html", "utf8").replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, "") });
      requests.push(p);
      if (p.endsWith("/preferences")) return route.fulfill({ json: { task_panel_enabled: false, ticket_list_columns: [{ key: "subject", width: 320 }] } });
      if (p.endsWith("/users/me")) return route.fulfill({ json: { full_name: "Test" } });
      return route.fulfill({ json: { items, total: 3, page: 1, pages: 1, limit: 20 } });
    });
    await page.goto("http://desk.test/tickets?tab=active");
    await page.evaluate(role => {
      document.body.dataset.theme = "dark";
      document.body.dataset.sidebarReady = "1";
      const identity = { role, userId: role === "admin" ? "admin" : "agent", agents: [{ id: "agent", full_name: "Agent", role }], counts: {} };
      window.HelpdeskSidebar = { init: async () => {}, getState: () => identity, onChange: () => () => {}, refreshCounts: async () => {} };
      window.HelpdeskAppShell = { registerPage: (_, init) => { window.initList = init; }, getCurrentPage: () => "tickets-list" };
    }, role);
    for (const script of ["task_panel_layout.js", "ticket_column_auto_width.js", "list.js"]) {
      await page.addScriptTag({ content: fs.readFileSync(`api/app/static/${script}`, "utf8") });
    }
    await page.evaluate(() => { window.listPage = window.initList(); });
    await page.waitForFunction(() => document.querySelectorAll(".ticket-row").length === 3);
    const badges = await page.locator(".ticket-unread-count").allTextContents();
    assert.deepEqual(badges, role === "admin" ? [] : ["+1", "+5", "+12"]);
    assert.equal(await page.locator(".ticket-row-unseen").count(), role === "admin" ? 0 : 1);
    assert.equal(await page.locator(".ticket-row-seen").count(), 2);
    assert.equal(requests.some(p => /\/tickets\/[123]$/.test(p)), false);
    assert.deepEqual(errors, []);
  });
}
