const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");
let browser;
test.before(async () => { browser = await chromium.launch({ channel: "msedge", headless: true }); });
test.after(async () => { await browser?.close(); });

for (const theme of ["light", "dark"]) test(`messenger preview toggle and navigation: ${theme}`, async t => {
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  page.setDefaultTimeout(6000);
  t.after(() => page.close());
  const requests = [], errors = [], writes = [];
  page.on("pageerror", error => errors.push(error.message));
  const me = { id: "admin", role: "admin", login: "admin", full_name: "Admin" };
  const leaf = (label, key, type = "ticket") => ({ kind: "terminal", label, technical_key: key,
    workflow_type: type, full_path: key, children_rows: [], is_enabled: true });
  const sub = { kind: "submenu", label: "Раздел", technical_key: "sub", level_key: "sub-level", full_path: "Раздел",
    children_rows: [{ columns: 1, buttons: [leaf("Внутри подменю", "child")] }] };
  const rows = [{ columns: 1, buttons: [sub] }, { columns: 2, buttons: [
    leaf("Восстановить сделку", "primary"), leaf("Создать новую сделку", "success", "task")
  ] }, { columns: 3, buttons: [
    leaf("Убрать клиента из базы и всех связанных списков", "danger"),
    leaf("Добавить номер в сделку и сохранить дополнительные данные", "long"), leaf("Ещё", "short")
  ] }];
  const originalLabels = rows.flatMap(row => row.buttons.map(button => button.label));
  await page.route("**/*", route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    if (url.hostname !== "desk.test") return route.abort();
    if (p.startsWith("/static/")) {
      const file = path.resolve("api/app", p.slice(1));
      return route.fulfill({ body: fs.readFileSync(file), contentType: { ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml" }[path.extname(file)] || "application/octet-stream" });
    }
    if (!p.startsWith("/api/")) return route.fulfill({ contentType: "text/html", body: fs.readFileSync("api/app/templates/settings_bot_buttons.html", "utf8") });
    requests.push([req.method(), p]);
    if (req.method() !== "GET") writes.push({ method: req.method(), path: p, body: req.postDataJSON() });
    let data = {};
    if (p.endsWith("/appearance-session")) data = { writer_id: "00000000-0000-4000-8000-000000000001", preferences: { theme_preference: theme, appearance_revision: "0" } };
    else if (p.endsWith("/preferences")) data = { theme_preference: theme, appearance_revision: "0" };
    else if (p.endsWith("/auth/session")) data = { real_user: me, effective_user: me, is_real_admin: true };
    else if (p.endsWith("/users/me") || p.endsWith("/users/me/profile")) data = me;
    else if (p.endsWith("/menu-types/archived") || p.endsWith("/agents") || p.endsWith("/by-role") || p.endsWith("/assignees")) data = [];
    else if (p.endsWith("/destination-options")) data = { items: [], free: [] };
    else if (p.endsWith("/menu-types")) data = ["Test", "Other"].map(menu_name => ({ menu_name, display_name: menu_name, destinations: [], ok: true, buttons_count: 6, menus_count: 1 }));
    else if (p.endsWith("/structure")) data = { root_level_key: "root", tree_rows: rows, removed_items: [] };
    else if (p.endsWith("/item")) data = { button_style: ["primary", "success", "danger"].includes(url.searchParams.get("button_key")) ? url.searchParams.get("button_key") : null,
      button_key: url.searchParams.get("button_key"), currently_present: true, workflow_type: "ticket", files_effective: [], conditional_files_effective: [] };
    return route.fulfill({ json: data });
  });
  await page.goto("http://desk.test/settings/bot-buttons");
  await page.locator("[data-menu-name]").first().click();
  const toggle = page.locator("#messengerViewBtn"), list = page.locator(".bot-buttons-split-left > .bot-buttons-level-list");
  await toggle.waitFor();
  await page.waitForFunction(() => document.querySelector(".bot-button-color-danger"));
  await page.mouse.move(0, 0);
  await page.waitForTimeout(180);
  assert.deepEqual(await page.locator(".bot-menu-bindings-actions").evaluate(el => [...el.children].map(node => node.id)),
    ["addDestinationSelect", "addDestinationBtn", "messengerViewBtn", "deleteMenuTypeBtn"]);
  const metrics = () => list.evaluate(el => ({ width: el.getBoundingClientRect().width,
    buttons: [...el.querySelectorAll("button")].map(button => ({ text: button.firstElementChild.textContent,
      height: button.getBoundingClientRect().height, size: getComputedStyle(button).fontSize,
      background: getComputedStyle(button).backgroundColor, radius: getComputedStyle(button).borderRadius,
      tag: getComputedStyle(button.lastElementChild).display })) }));
  const off = await metrics();
  const offShadow = await toggle.evaluate(el => getComputedStyle(el).boxShadow);
  const offBackground = await toggle.evaluate(el => getComputedStyle(el).backgroundColor);
  assert.equal(await toggle.getAttribute("aria-pressed"), "false");
  assert.ok(!offShadow.includes("inset"));
  const requestCount = requests.length;
  await toggle.click();
  await page.mouse.move(0, 0);
  await page.waitForTimeout(180);
  assert.equal(requests.length, requestCount, "toggling makes no HTTP requests");
  assert.equal(await toggle.getAttribute("aria-pressed"), "true");
  assert.match(await toggle.evaluate(el => getComputedStyle(el).boxShadow), /inset/);
  assert.notEqual(await toggle.evaluate(el => getComputedStyle(el).backgroundColor), offBackground);
  assert.equal(await toggle.evaluate(el => new DOMMatrix(getComputedStyle(el).transform).m42), 1);
  const on = await metrics();
  assert.equal(on.width, 390);
  assert.deepEqual(on.buttons.map(button => button.text), originalLabels);
  assert.deepEqual(on.buttons.map(button => button.background), off.buttons.map(button => button.background));
  assert.ok(on.buttons.every(button => button.height === 45 && button.size === "16px" && button.tag === "none"));
  assert.equal(await list.locator(".bot-button-type-tag").count(), 6, "tags remain in the DOM");
  const geometry = await list.locator(".bot-buttons-row").evaluateAll(rows => rows.map(row => {
    const cells = [...row.children].map(el => el.getBoundingClientRect());
    return cells.every(cell => Math.abs(cell.top - cells[0].top) < 1 && Math.abs(cell.width - cells[0].width) < 1);
  }));
  assert.deepEqual(geometry, [true, true, true]);
  assert.equal(await list.locator('[data-button-color-key="primary"] > span').first().evaluate(el => el.scrollWidth <= el.clientWidth), true);
  assert.equal(await list.locator('[data-button-color-key="long"] > span').first().evaluate(el =>
    getComputedStyle(el).textOverflow === "ellipsis" && getComputedStyle(el).whiteSpace === "nowrap" && el.scrollWidth > el.clientWidth), true);
  await page.locator(".bot-button-submenu").click();
  assert.equal(await list.getAttribute("class").then(value => value.includes("bot-buttons-messenger-view")), true);
  await page.locator("#botButtonsBackBtn").click();
  assert.equal(await toggle.getAttribute("aria-pressed"), "true");
  await toggle.click();
  await page.mouse.move(0, 0);
  await page.waitForTimeout(180);
  assert.deepEqual(await metrics(), off, "OFF restores the accepted normal appearance");
  await toggle.click();
  await page.locator("#editLayoutBtn").click();
  assert.equal(await toggle.getAttribute("aria-pressed"), "false");
  assert.equal(await page.locator(".bot-buttons-messenger-view").count(), 0);
  assert.equal(await toggle.isDisabled(), true);
  assert.ok(await page.locator("[data-layout-drag-button]").first().evaluate(el => el.getBoundingClientRect().height >= 46 && el.getBoundingClientRect().height < 64));
  await page.locator("#cancelLayoutBtn").click();
  await toggle.click();
  await page.setViewportSize({ width: 768, height: 1080 });
  assert.equal(await list.evaluate(el => el.getBoundingClientRect().width <= el.parentElement.getBoundingClientRect().width && el.scrollWidth <= el.clientWidth), true);
  await page.emulateMedia({ reducedMotion: "reduce" });
  assert.equal(await toggle.evaluate(el => getComputedStyle(el).transitionDuration), "0s");
  await page.keyboard.press("Tab");
  await toggle.focus();
  assert.equal(await toggle.evaluate(el => getComputedStyle(el).outlineStyle), "solid");
  assert.ok(requests.every(([method]) => method === "GET"), "no data writes");

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.locator("#botButtonsBackBtn").click();
  await page.locator("[data-menu-name]").nth(1).click();
  assert.equal(await toggle.getAttribute("aria-pressed"), "false", "another menu resets preview");
  await page.locator("#botButtonsBackBtn").click();
  await page.locator("[data-menu-name]").first().click();
  assert.equal(await toggle.getAttribute("aria-pressed"), "false", "returning to the previous menu keeps preview OFF");
  for (let index = 0; index < 35; index++) rows.push({ columns: 1, buttons: [leaf(`Длинный список ${index}`, `extra-${index}`)] });
  await page.locator("#botButtonsBackBtn").click();
  await page.locator("[data-menu-name]").first().click();
  await page.locator("#editLayoutBtn").click();
  const dialog = page.locator(".bot-layout-dialog"), scroll = dialog.locator(".bot-layout-scroll-area");
  assert.equal(await dialog.evaluate(el => el.matches(":modal")), true);
  const size = await dialog.evaluate(el => {
    const r = el.getBoundingClientRect(), main = document.getElementById("appContent").getBoundingClientRect();
    return { ratio: r.width / (innerWidth - main.left - 24), height: r.height, viewport: innerHeight };
  });
  assert.ok(Math.abs(size.ratio - 2 / 3) < .01);
  assert.equal(size.height, size.viewport - 24);
  assert.equal(await page.evaluate(() => getComputedStyle(document.body).overflow), "hidden");
  const scrolling = await dialog.evaluate(el => {
    const area = el.querySelector('.bot-layout-scroll-area'), list = el.querySelector('.bot-buttons-level-list'), frame = el.querySelector('.bot-layout-scroll-frame');
    const a = area.getBoundingClientRect(), f = frame.getBoundingClientRect();
    return { maxHeight: getComputedStyle(list).maxHeight, overflow: getComputedStyle(list).overflowY,
      listScrolls: list.scrollHeight > list.clientHeight, areaScrolls: area.scrollHeight > area.clientHeight,
      topGap: a.top - f.top, bottomGap: f.bottom - a.bottom,
      scrollContainers: [...el.querySelectorAll('*')].filter(node => ['auto','scroll'].includes(getComputedStyle(node).overflowY) && node.scrollHeight > node.clientHeight).length };
  });
  assert.deepEqual(scrolling, { maxHeight: 'none', overflow: 'visible', listScrolls: false, areaScrolls: true, topGap: 0, bottomGap: 0, scrollContainers: 1 });
  assert.equal(await dialog.locator('.bot-layout-new-row-drop').first().evaluate(el => getComputedStyle(el).borderStyle), 'dashed');
  assert.equal(await dialog.locator('.bot-move-drop').first().evaluate(el => getComputedStyle(el).borderStyle), 'dashed');
  const headerTop = await dialog.locator("header").evaluate(el => el.getBoundingClientRect().top);
  await scroll.evaluate(el => { el.scrollTop = 300; });
  assert.equal(await dialog.locator("header").evaluate(el => el.getBoundingClientRect().top), headerTop);
  await scroll.evaluate(el => { el.scrollTop = 0; });
  const transfer = await page.evaluateHandle(() => new DataTransfer());
  const source = dialog.locator("[data-layout-drag-button]").first();
  await source.dispatchEvent("dragstart", { dataTransfer: transfer });
  const down = dialog.locator('[data-layout-scroll="1"]'), up = dialog.locator('[data-layout-scroll="-1"]');
  assert.equal(await up.isVisible(), false);
  assert.equal(await down.isVisible(), true);
  await down.dispatchEvent("dragover", { dataTransfer: transfer });
  await page.waitForTimeout(160);
  assert.ok(await scroll.evaluate(el => el.scrollTop > 20), "dragging over bottom zone scrolls down");
  await down.dispatchEvent("dragleave");
  const stopped = await scroll.evaluate(el => el.scrollTop);
  await page.waitForTimeout(80);
  assert.equal(await scroll.evaluate(el => el.scrollTop), stopped);
  await scroll.evaluate(el => { el.scrollTop = 400; el.dispatchEvent(new Event("scroll")); });
  assert.equal(await up.isVisible(), true);
  await up.dispatchEvent("dragover", { dataTransfer: transfer });
  await page.waitForTimeout(160);
  assert.ok(await scroll.evaluate(el => el.scrollTop < 380), "dragging over top zone scrolls up");
  await source.dispatchEvent("dragend", { dataTransfer: transfer });
  const ended = await scroll.evaluate(el => el.scrollTop);
  await page.waitForTimeout(80);
  assert.equal(await scroll.evaluate(el => el.scrollTop), ended);
  assert.equal(await down.isVisible(), false);
  await page.locator("#cancelLayoutBtn").click();
  assert.equal(await dialog.count(), 0);
  assert.equal(writes.length, 0, "cancel never writes the draft");
  await page.locator("#editLayoutBtn").click();
  await dialog.locator('[data-layout-drag-button="1:1"]').dragTo(dialog.locator('[data-layout-drag-button="1:0"]'));
  assert.deepEqual(await dialog.locator('[data-layout-drop-row="1"] button').evaluateAll(nodes=>nodes.map(n=>n.dataset.buttonColorKey)), ["success", "primary"]);
  await page.locator("#saveLayoutBtn").click();
  await page.waitForFunction(() => !document.querySelector(".bot-layout-dialog"));
  assert.equal(writes.length, 1);
  assert.equal(writes[0].method, "PUT");
  assert.deepEqual(writes[0].body.rows[1], { columns: 2, button_keys: ["success", "primary"] });
  await page.locator("#editLayoutBtn").click();
  await page.setViewportSize({ width: 900, height: 800 });
  assert.ok(await dialog.evaluate(el => Math.abs(el.getBoundingClientRect().width - (innerWidth - 24)) < 1));
  const oldScroll = await scroll.elementHandle();
  await dialog.locator("[data-layout-drag-button]").first().dispatchEvent("dragstart", { dataTransfer: transfer });
  await dialog.locator('[data-layout-scroll="1"]').dispatchEvent("dragover", { dataTransfer: transfer });
  await page.waitForTimeout(80);
  await page.keyboard.press("Escape");
  assert.equal(await dialog.count(), 0);
  const closedPosition = await oldScroll.evaluate(el => el.scrollTop);
  await page.waitForTimeout(80);
  assert.equal(await oldScroll.evaluate(el => el.scrollTop), closedPosition, "closing cancels the animation loop");
  await oldScroll.dispose();
  await transfer.dispose();
  assert.deepEqual(errors, []);
});
