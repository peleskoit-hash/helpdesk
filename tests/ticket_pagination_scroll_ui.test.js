const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

test("ticket pagination resets vertical scroll only after applying navigation", async t => {
  const browser = await chromium.launch({ channel: "msedge", headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1000, height: 700 } });
  page.setDefaultTimeout(5000);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const me = { id: "agent", role: "agent", full_name: "Agent" };
  let loadCount = 0;
  let intercept = null;

  // Drive the actual polling callback without waiting five seconds or racing it.
  await page.addInitScript(() => {
    const setInterval = window.setInterval.bind(window);
    window.setInterval = (callback, delay, ...args) => {
      if (delay !== 5000) return setInterval(callback, delay, ...args);
      window.pollTickets = callback;
      return 0;
    };
  });
  await page.route("http://desk.test/**", async route => {
    const url = new URL(route.request().url());
    const pathname = url.pathname;
    if (pathname.startsWith("/static/")) {
      const file = path.resolve("api/app", pathname.slice(1));
      if (!fs.existsSync(file)) return route.fulfill({ status: 404 });
      const type = { ".css": "text/css", ".js": "text/javascript" }[path.extname(file)];
      return route.fulfill({ body: fs.readFileSync(file), contentType: type || "application/octet-stream" });
    }
    if (pathname === "/tickets") {
      return route.fulfill({ contentType: "text/html", body: fs.readFileSync("api/app/templates/tickets.html", "utf8") });
    }
    if (pathname === "/api/v1/tickets/active") {
      const number = Number(url.searchParams.get("page"));
      const response = ++loadCount;
      const status = (await intercept?.(number)) || 200;
      const items = Array.from({ length: 20 }, (_, index) => ({
        id: `${number}-${index}`, subject: `Page ${number}, load ${response}`,
        assignee_id: me.id, status: "new", created_at: "2026-10-01T08:00:00Z",
      }));
      return route.fulfill({ status, json: { items, total: 60, page: number, pages: 3 } });
    }
    let data = {};
    if (pathname === "/api/v1/auth/session") data = { real_user: me, effective_user: me, session_login_ts: 1 };
    if (pathname === "/api/v1/users/me" || pathname.endsWith("/me/profile")) data = me;
    if (pathname.endsWith("/preferences")) {
      data = { task_panel_enabled: false, ticket_list_columns: [{ key: "subject", width: 1200 }] };
    }
    if (pathname.endsWith("/agents") || pathname.endsWith("/by-role")) data = [me];
    return route.fulfill({ json: data });
  });

  const tableScroll = page.locator("#appContent .table-scroll");
  const position = () => tableScroll.evaluate(el => [el.scrollTop, el.scrollLeft]);
  async function scrollTable() {
    await tableScroll.evaluate(el => { el.scrollTop = 200; el.scrollLeft = 80; });
    assert.deepEqual(await position(), [200, 80]);
    assert.equal(await page.locator("#appContent").evaluate(el => el.scrollTop), 0);
  }
  async function rendered(number, load) {
    await page.waitForFunction(({ number, load }) => {
      const subject = document.querySelector("#ticketsTableBody .ticket-subject-col");
      return subject?.textContent.includes(`Page ${number}, load ${load}`);
    }, { number, load });
  }
  const poll = () => page.evaluate(() => window.pollTickets());
  const numbered = number => page.getByRole("button", { name: String(number), exact: true });
  const history = direction => page.evaluate(direction => window.history[direction](), direction);

  await page.goto("http://desk.test/tickets?tab=active&page=1");
  await rendered(1, 1);
  await scrollTable();
  await numbered(2).click();
  await rendered(2, 2);
  assert.deepEqual(await position(), [0, 80], "numbered pagination");

  await scrollTable();
  await poll();
  await rendered(2, 3);
  assert.deepEqual(await position(), [200, 80], "same-page polling");

  await page.locator("#nextPageBtn").click();
  await rendered(3, 4);
  assert.deepEqual(await position(), [0, 80], "next button");
  await scrollTable();
  await page.locator("#prevPageBtn").click();
  await rendered(2, 5);
  assert.deepEqual(await position(), [0, 80], "previous button");

  await scrollTable();
  await history("back");
  await rendered(3, 6);
  assert.deepEqual(await position(), [0, 80], "browser back");
  await scrollTable();
  await history("forward");
  await rendered(2, 7);
  assert.deepEqual(await position(), [0, 80], "browser forward");

  await scrollTable();
  const oldRequested = Promise.withResolvers();
  const oldResponse = Promise.withResolvers();
  const nextRequested = Promise.withResolvers();
  const nextResponse = Promise.withResolvers();
  intercept = async number => {
    if (number === 2) {
      oldRequested.resolve();
      await oldResponse.promise;
    } else {
      nextRequested.resolve();
      await nextResponse.promise;
    }
  };
  await poll();
  await oldRequested.promise;
  await numbered(1).click();
  await poll();
  assert.deepEqual(await position(), [200, 80], "navigation waiting in queue");
  oldResponse.resolve();
  await nextRequested.promise;
  assert.deepEqual(await position(), [200, 80], "stale response discarded");
  nextResponse.resolve();
  await rendered(1, 9);
  assert.deepEqual(await position(), [0, 80], "queued navigation applied");

  await scrollTable();
  intercept = () => 503;
  await numbered(2).click();
  await page.locator("#errorModal").waitFor({ state: "visible" });
  assert.deepEqual(await position(), [200, 80], "failed request");
  assert.deepEqual(errors, []);
});
