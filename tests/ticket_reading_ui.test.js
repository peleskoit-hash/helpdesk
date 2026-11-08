const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const { chromium } = require("playwright");
let browser;
test.before(async () => { browser = await chromium.launch({ channel: "msedge", headless: true }); });
test.after(async () => { await browser?.close(); });

async function setup(t, { total = 100, read = 0, canRead = true } = {}) {
  const page = await browser.newPage();
  t.after(() => page.close());
  await page.setContent('<style>#messages { height:320px; width:500px; overflow:auto; overflow-anchor:none } .message { box-sizing:border-box; margin:0 0 10px; } .message-body { overflow:hidden }</style><div id="messages"></div>');
  await page.addScriptTag({ content: fs.readFileSync("api/app/static/ticket_message_view.js", "utf8") });
  await page.evaluate(({ total, read, canRead }) => {
    window.calls = [];
    window.card = { id: "one", messages: Array.from({ length: total }, (_, i) => ({
      id: String(i + 1), message_type: "client", body: `Message ${i + 1}`, height: 50,
      created_at: new Date(Date.UTC(2026, 0, 1, 0, i)).toISOString(), attachments: [],
    })), unread_message_ids: Array.from({ length: total - read }, (_, i) => String(read + i + 1)) };
    const el = document.getElementById("messages");
    window.ctx = { state: { role: "agent", userId: "agent", card }, el: { messages: el },
      fmtMessageStamp: x => x, linkifyText: x => x, isStickerOnlyMessage: () => false,
      messageRenderSignature: x => JSON.stringify(x), cleanupAttachmentObjectUrls: () => {}, cleanupStickerResources: () => {},
      renderAttachments: (m, node) => { node.style.height = `${m.height}px`; } };
    window.controller = TicketMessageView.createReadingController(el, async payload => { calls.push(payload); });
    window.signature = null;
    window.render = (options = {}) => {
      signature = TicketMessageView.renderMessages(ctx, card, signature, null).signature;
      controller.update(card, { canRead, ...options });
    };
    render();
  }, { total, read, canRead });
  return page;
}

for (const scenario of [
  { name: "no unread messages opens at the bottom", total: 100, read: 100, bottom: true },
  { name: "first long visit starts at first unread", total: 100, read: 0, bottom: false },
  { name: "short first visit shows entire history", total: 3, read: 0, bottom: true },
  { name: "five new messages fit at the bottom", total: 100, read: 95, bottom: true },
  { name: "fifty new messages start at first unread", total: 100, read: 50, bottom: false },
]) {
  test(scenario.name, async t => {
    const page = await setup(t, scenario);
    const result = await page.evaluate(() => {
      const el = ctx.el.messages, first = el.querySelector(`[data-message-id="${card.unread_message_ids[0]}"]`);
      return { atBottom: Math.abs(el.scrollHeight - el.clientHeight - el.scrollTop) < 1,
        firstOffset: first ? first.getBoundingClientRect().top - el.getBoundingClientRect().top : null };
    });
    assert.equal(result.atBottom, scenario.bottom);
    if (!scenario.bottom) assert.ok(Math.abs(result.firstOffset) < 1);
    await page.waitForTimeout(240);
    const calls = await page.evaluate(() => window.calls);
    assert.equal(calls.length, scenario.total === scenario.read ? 0 : 1);
    if (scenario.total === scenario.read) return;
    if (scenario.bottom) assert.equal(calls[0].message_ids.length, scenario.total - scenario.read);
    else assert.ok(calls[0].message_ids.length < scenario.total - scenario.read);
  });
}

test("polling and incoming client, admin, internal and own messages preserve position", async t => {
  const page = await setup(t);
  await page.evaluate(() => { ctx.el.messages.scrollTop = 135; });
  for (const [type, author] of [["client", null], ["agent", "admin"], ["internal", "admin"], ["agent", "agent"]]) {
    const result = await page.evaluate(({ type, author }) => {
      const before = ctx.el.messages.scrollTop;
      card.messages.push({ ...card.messages[0], id: String(card.messages.length + 1), message_type: type, author_user_id: author });
      render();
      render();
      return { before, after: ctx.el.messages.scrollTop };
    }, { type, author });
    assert.equal(result.after, result.before);
  }
  const changed = await page.evaluate(() => {
    const firstVisible = Array.from(ctx.el.messages.children).find(n => n.getBoundingClientRect().bottom > ctx.el.messages.getBoundingClientRect().top);
    const id = firstVisible.dataset.messageId, before = firstVisible.getBoundingClientRect().top;
    card.messages[0].height = 150;
    render();
    return { before, after: ctx.el.messages.querySelector(`[data-message-id="${id}"]`).getBoundingClientRect().top };
  });
  assert.equal(changed.after, changed.before);
});

test("only explicit local-send completion forces bottom", async t => {
  const page = await setup(t);
  await page.evaluate(() => { ctx.el.messages.scrollTop = 80; render({ forceBottom: true }); });
  assert.equal(await page.evaluate(() => ctx.el.messages.scrollTop === ctx.el.messages.scrollHeight - ctx.el.messages.clientHeight), true);
});

test("reading progresses only after actual views and does not repeat on polling", async t => {
  const page = await setup(t);
  await page.waitForTimeout(240);
  const initial = await page.evaluate(() => calls.length);
  await page.evaluate(() => { render(); render(); });
  await page.waitForTimeout(240);
  assert.equal(await page.evaluate(() => calls.length), initial);
  await page.evaluate(() => { ctx.el.messages.scrollTop = 200; ctx.el.messages.dispatchEvent(new Event("scroll")); });
  await page.waitForTimeout(240);
  assert.equal(await page.evaluate(() => calls.length), initial + 1);
  assert.ok(await page.evaluate(() => Number(calls.at(-1).message_id) < 100));
});

test("jumping past unseen history cannot advance the boundary", async t => {
  const page = await setup(t);
  await page.waitForTimeout(240);
  const initial = await page.evaluate(() => calls.length);
  await page.evaluate(() => { ctx.el.messages.scrollTop = ctx.el.messages.scrollHeight; ctx.el.messages.dispatchEvent(new Event("scroll")); });
  await page.waitForTimeout(240);
  assert.equal(await page.evaluate(() => calls.length), initial);
});

test("admin viewing never acknowledges, and another ticket gets initial positioning", async t => {
  const page = await setup(t, { canRead: false });
  await page.waitForTimeout(240);
  assert.equal(await page.evaluate(() => calls.length), 0);
  await page.evaluate(() => { card.id = "two"; render({ canRead: true }); });
  assert.equal(await page.evaluate(() => ctx.el.messages.scrollTop), 0);
});

test("a long message is acknowledged only after its bottom is reached", async t => {
  const page = await setup(t, { total: 1 });
  await page.evaluate(() => { card.id = "tall"; card.messages[0].height = 800; render(); });
  await page.waitForTimeout(240);
  assert.equal(await page.evaluate(() => calls.length), 0);
  await page.evaluate(() => { ctx.el.messages.scrollTop = ctx.el.messages.scrollHeight; ctx.el.messages.dispatchEvent(new Event("scroll")); });
  await page.waitForTimeout(240);
  assert.equal(await page.evaluate(() => calls.length), 1);
});


test("split task descriptions and attachments are read only after all parts were shown", async t => {
  const page = await setup(t, { total: 1 });
  await page.evaluate(() => {
    card.id = "split-task";
    card.workflow_type = "task";
    Object.assign(card.messages[0], { height: 180, is_initial_task_description: true,
      attachments: [{ id: "file-one" }, { id: "file-two" }] });
    render();
  });
  assert.equal(await page.locator('[data-message-id="1"]').count(), 3);
  await page.waitForTimeout(240);
  assert.equal(await page.evaluate(() => calls.length), 0);
  for (const top of [100, 260]) {
    await page.evaluate(top => { ctx.el.messages.scrollTop = top; ctx.el.messages.dispatchEvent(new Event("scroll")); }, top);
    await page.waitForTimeout(240);
  }
  assert.equal(await page.evaluate(() => calls.length), 1);
});


test("equal timestamp messages do not acknowledge unseen rows below the viewport", async t => {
  const page = await setup(t, { total: 10 });
  await page.evaluate(() => {
    card.id = "same-timestamp";
    card.messages.forEach(m => { m.created_at = card.messages[0].created_at; });
    render();
  });
  await page.waitForTimeout(240);
  assert.equal(await page.evaluate(() => calls.length), 0);
});


test("a hidden tab cannot acknowledge messages until it becomes visible", async t => {
  const page = await setup(t, { total: 3 });
  await page.evaluate(() => {
    window.testHidden = true;
    Object.defineProperty(document, "hidden", { configurable: true, get: () => window.testHidden });
  });
  await page.waitForTimeout(240);
  assert.equal(await page.evaluate(() => calls.length), 0);
  await page.evaluate(() => { window.testHidden = false; document.dispatchEvent(new Event("visibilitychange")); });
  await page.waitForTimeout(240);
  assert.equal(await page.evaluate(() => calls.length), 1);
});


test("pending image previews are not read before their actual size is visible", async t => {
  const page = await setup(t, { total: 2 });
  await page.evaluate(() => {
    window.imagesReady = false;
    ctx.renderAttachments = (message, node) => {
      node.style.height = "100px";
      const image = document.createElement("img");
      image.setAttribute("src", "data:image/png;base64,");
      Object.defineProperty(image, "complete", { get: () => window.imagesReady });
      node.appendChild(image);
    };
    card.id = "image-task";
    signature = null;
    render();
  });
  await page.waitForTimeout(240);
  assert.equal(await page.evaluate(() => calls.length), 0);
  await page.evaluate(() => {
    window.imagesReady = true;
    Array.from(ctx.el.messages.children).forEach(node => { node.style.height = "220px"; });
    ctx.el.messages.querySelector("img").dispatchEvent(new Event("load"));
  });
  await page.waitForTimeout(240);
  assert.equal(await page.evaluate(() => ctx.el.messages.scrollTop), 0);
  assert.deepEqual(await page.evaluate(() => calls.at(-1).message_ids), ["1"]);
});


test("initial image positioning survives polling before the image loads", async t => {
  const page = await setup(t, { total: 4, read: 2 });
  await page.evaluate(() => {
    window.imagesReady = false;
    ctx.renderAttachments = (message, node) => {
      node.style.height = "100px";
      const image = document.createElement("img");
      Object.defineProperty(image, "complete", { get: () => window.imagesReady });
      image.setAttribute("src", "data:image/png;base64,");
      node.appendChild(image);
    };
    card.id = "slow-image-task";
    signature = null;
    render();
  });
  await page.waitForTimeout(50);
  const before = await page.evaluate(() => ctx.el.messages.scrollTop);
  assert.ok(before > 0, "the small initial unread block fits at the bottom");
  await page.evaluate(() => { render(); render(); });
  assert.equal(await page.evaluate(() => ctx.el.messages.scrollTop), before);
  await page.evaluate(() => {
    window.imagesReady = true;
    Array.from(ctx.el.messages.children).forEach(node => { node.style.height = "220px"; });
    ctx.el.messages.querySelector("img").dispatchEvent(new Event("load"));
  });
  const offset = await page.locator('[data-message-id="3"]').evaluate(node =>
    node.getBoundingClientRect().top - node.parentElement.getBoundingClientRect().top);
  assert.ok(Math.abs(offset) < 1, "the expanded unread block starts at the first unread message");
});
