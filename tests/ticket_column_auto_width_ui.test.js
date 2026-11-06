const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });
const longName = 'Константинопольский Александр Александрович';
async function setup(t, theme = 'light', auto = true) {
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  t.after(() => page.close());
  const errors = [], writes = [];
  page.on('pageerror', e => errors.push(e.message));
  t.after(() => assert.deepEqual(errors, []));
  const prefs = { ticket_list_auto_width: auto, ticket_list_columns: [{ key: 'subject', width: 400 }, { key: 'assignee', width: 180 }] };
  await page.route('http://desk.test/**', route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    if (p.startsWith('/static/')) {
      const file = path.resolve('api/app', p.slice(1));
      return route.fulfill({ body: fs.readFileSync(file), contentType: { '.css': 'text/css', '.js': 'text/javascript', '.ttf': 'font/ttf', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream' });
    }
    if (!p.startsWith('/api/')) return route.fulfill({ contentType: 'text/html', body: fs.readFileSync('api/app/templates/tickets.html', 'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '') });
    if (req.method() === 'PATCH') { writes.push(req.postDataJSON()); return route.fulfill({ json: prefs }); }
    if (p.endsWith('/preferences')) return route.fulfill({ json: prefs });
    if (p.endsWith('/users/me')) return route.fulfill({ json: { full_name: 'Admin' } });
    const index = Number(url.searchParams.get('page') || 1);
    const items = Array.from({ length: 30 }, (_, i) => ({ id: `${index}-${i}`, subject: `Заявка ${i}`, workflow_type: 'ticket', status: 'new', assignee_id: index === 2 && i === 0 ? 'long' : 'short', created_at: '2026-11-06T09:00:00Z' }));
    return route.fulfill({ json: { items, total: 90, page: index, pages: 3, limit: 30 } });
  });
  await page.goto('http://desk.test/tickets?tab=active');
  await page.evaluate(({ theme, longName }) => {
    document.body.dataset.theme = theme;
    document.body.dataset.sidebarReady = '1';
    const sidebar = { role: 'admin', userId: 'admin', agents: [{ id: 'short', full_name: 'Иванов И.' }, { id: 'long', full_name: longName }, { id: 'unused', full_name: longName.repeat(3) }], counts: {} };
    window.HelpdeskSidebar = { init: async () => {}, getState: () => sidebar, onChange: () => () => {}, refreshCounts: async () => {} };
    window.HelpdeskAppShell = { registerPage: (_, init) => { window.initList = init; }, getCurrentPage: () => 'tickets-list' };
  }, { theme, longName });
  await page.addScriptTag({ path: 'api/app/static/ticket_column_auto_width.js' });
  await page.evaluate(() => {
    const original = window.HelpdeskTicketColumnAutoWidth.measure;
    window.measureCalls = [];
    window.HelpdeskTicketColumnAutoWidth.measure = (...args) => { const result = original(...args); window.measureCalls.push(result); return result; };
  });
  await page.addScriptTag({ path: 'api/app/static/list.js' });
  await page.evaluate(() => { window.listPage = window.initList(); });
  await page.waitForFunction(auto => document.querySelectorAll('.ticket-row').length === 30 && (!auto || window.measureCalls.length > 0), auto);
  return { page, writes };
}
async function geometry(page) {
  return page.evaluate(() => {
    const select = document.querySelector('.row-assignee-select'), cell = select.closest('td'), subject = document.querySelector('td[data-column-key="subject"]'), scroll = cell.closest('.table-scroll');
    const clone = select.cloneNode(true);
    const selected = clone.selectedOptions[0]; clone.replaceChildren(selected);
    clone.style.cssText = 'position:absolute;width:auto!important;max-width:none!important;min-width:0!important;visibility:hidden';
    cell.appendChild(clone);
    const native = clone.getBoundingClientRect().width; clone.remove();
    const style = getComputedStyle(cell);
    return { name: select.selectedOptions[0].textContent, native, select: select.getBoundingClientRect().width, cell: cell.getBoundingClientRect().width,
      padding: parseFloat(style.paddingLeft) + parseFloat(style.paddingRight) + parseFloat(style.borderLeftWidth) + parseFloat(style.borderRightWidth),
      subject: subject.getBoundingClientRect().width, scrollWidth: scroll.scrollWidth, clientWidth: scroll.clientWidth,
      cellStyle: cell.style.width, headerStyle: document.querySelector('th[data-column-key="assignee"]').style.width, measured: window.measureCalls.at(-1) };
  });
}
for (const theme of ['light', 'dark']) test(`native assignee sizing and pagination: ${theme}`, async t => {
  const { page, writes } = await setup(t, theme);
  const short = await geometry(page);
  await page.locator('#nextPageBtn').click();
  await page.waitForFunction(() => window.measureCalls.length === 2);
  const long = await geometry(page);
  assert.equal(long.name, longName);
  assert.ok(long.select >= long.native, JSON.stringify(long));
  assert.ok(long.cell >= long.native + long.padding, JSON.stringify(long));
  assert.ok(long.cell <= Math.ceil(long.native + long.padding) + 1, JSON.stringify(long));
  assert.ok(long.subject >= 180);
  assert.ok(long.cell > short.cell && long.subject < short.subject, 'spare subject width goes to the name');
  assert.ok(long.scrollWidth <= long.clientWidth, JSON.stringify(long));
  assert.equal(long.cellStyle, long.headerStyle);
  await page.locator('#nextPageBtn').click();
  await page.waitForFunction(() => window.measureCalls.length === 3);
  const again = await geometry(page);
  assert.ok(Math.abs(again.cell - short.cell) <= 1, JSON.stringify({ short, again }));
  assert.deepEqual(writes, [], 'automatic sizing does not overwrite saved preferences');
});
for (const theme of ['light', 'dark']) test(`sticky headers and unchanged footer: ${theme}`, async t => {
  const { page } = await setup(t, theme);
  const before = await page.locator('#ticketsTable thead').boundingBox();
  const footer = await page.locator('.table-pagination-footer').boundingBox();
  await page.locator('.table-scroll').evaluate(el => { el.scrollTop = 250; });
  const after = await page.locator('#ticketsTable thead').boundingBox();
  assert.deepEqual(after, before, 'the header must keep its original position and full visual height');
  await page.locator('.table-scroll').evaluate(el => { el.scrollTop = 500; });
  assert.deepEqual(await page.locator('#ticketsTable thead').boundingBox(), after);
  assert.deepEqual(await page.locator('.table-pagination-footer').boundingBox(), footer);
  const details = await page.evaluate(() => {
    const head = document.querySelector('#ticketsTable thead'), cells = [...head.querySelectorAll('th')], row = document.querySelector('.ticket-row');
    const rect = head.getBoundingClientRect();
    return { position: getComputedStyle(head).position, background: getComputedStyle(head).backgroundColor,
      aligned: cells.every((cell, i) => Math.abs(cell.getBoundingClientRect().left - row.cells[i].getBoundingClientRect().left) < 1),
      onTop: head.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2)) };
  });
  assert.equal(details.position, 'sticky');
  assert.equal(details.background, theme === 'dark' ? 'rgb(32, 37, 53)' : 'rgb(255, 254, 253)');
  assert.equal(details.aligned, true);
  assert.equal(details.onTop, true);
});

test('measure caps at twice the mean when there is no spare subject width', async t => {
  const { page } = await setup(t);
  const result = await page.evaluate(() => {
    const scroll = document.querySelector('.table-scroll');
    scroll.style.width = '300px';
    const table = document.createElement('table');
    table.innerHTML = '<thead><tr><th data-column-key="subject">Тема</th><th data-column-key="requester">Клиент</th></tr></thead><tbody></tbody>';
    scroll.appendChild(table);
    const lengths = [100, 2, 2, 2, 2, 2, 2, 2, 2, 2];
    for (const length of lengths) {
      const row = table.tBodies[0].insertRow();
      row.innerHTML = '<td data-column-key="subject">Тема</td><td data-column-key="requester">' + 'W'.repeat(length) + '</td>';
    }
    const widths = [...table.tBodies[0].rows].map(row => {
      const range = document.createRange(); range.selectNodeContents(row.cells[1]);
      return range.getBoundingClientRect().width;
    });
    const style = getComputedStyle(table.tBodies[0].rows[0].cells[1]);
    const padding = ['paddingLeft', 'paddingRight', 'borderLeftWidth', 'borderRightWidth'].reduce((sum, key) => sum + parseFloat(style[key]), 0);
    const expected = Math.ceil(widths.reduce((a,b) => a+b, 0) / widths.length * 2 + padding);
    const measured = window.HelpdeskTicketColumnAutoWidth.measure(table, [{ key: 'subject' }, { key: 'requester' }], [...table.tBodies[0].rows]);
    table.remove();
    return { expected, measured };
  });
  assert.equal(result.measured.find(c => c.key === 'requester').width, result.expected);
  assert.equal(result.measured.find(c => c.key === 'subject').width, 180);
});
test('manual column widths are preserved and no automatic measurement runs', async t => {
  const { page, writes } = await setup(t, 'light', false);
  assert.equal(await page.locator('td[data-column-key="assignee"]').first().evaluate(el => el.style.width), '180px');
  await page.locator('#nextPageBtn').click();
  await page.waitForFunction(() => document.querySelector('.ticket-row')?.querySelector('select')?.value === 'long');
  assert.equal(await page.locator('td[data-column-key="assignee"]').first().evaluate(el => el.style.width), '180px');
  assert.equal(await page.evaluate(() => window.measureCalls.length), 0);
  assert.deepEqual(writes, []);
});

for (const theme of ['light', 'dark']) test(`complete header surface stays pixel-identical through sticky transition: ${theme}`, async t => {
  const { page } = await setup(t, theme);
  const surface = () => page.evaluate(() => {
    const head = document.querySelector('#ticketsTable thead'), scroll = head.closest('.table-scroll');
    const h = head.getBoundingClientRect(), s = scroll.getBoundingClientRect();
    // Include the shadow from 7251292, clipped by the scrollport, not only the th boxes.
    const shadow = getComputedStyle(head).boxShadow;
    const [dx, dy, , spread] = shadow.replace(/rgba?\([^)]*\)/, '').match(/-?[\d.]+px/g).map(parseFloat);
    const left = Math.max(s.left, h.left + dx - spread), top = Math.max(s.top, h.top + dy - spread);
    const right = Math.min(s.left + scroll.clientWidth, h.right + dx + spread), bottom = Math.min(s.top + scroll.clientHeight, h.bottom + dy + spread);
    const panel = head.closest('.table-wrap'), p = panel.getBoundingClientRect(), style = getComputedStyle(panel);
    const border = parseFloat(style.borderLeftWidth);
    const roundedClip = { left: p.left + border, top: p.top + border, right: p.right - border,
      bottom: p.bottom - border, radius: parseFloat(style.borderTopLeftRadius) - border };
    return { header: { x: h.x, y: h.y, width: h.width, height: h.height }, shadow, roundedClip,
      clip: { x: Math.ceil(left), y: Math.ceil(top), width: Math.floor(right) - Math.ceil(left), height: Math.floor(bottom) - Math.ceil(top) },
      columns: [...head.querySelectorAll('th')].map(el => { const r = el.getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; }) };
  });
  const initial = await surface();
  const screenshot = await page.screenshot({ clip: initial.clip, animations: 'disabled' });
  const footer = await page.locator('.table-pagination-footer').boundingBox();
  const rowHeights = await page.locator('.ticket-row').evaluateAll(rows => rows.map(row => row.getBoundingClientRect().height));
  for (const offset of [1, 5, 6, 7, 50, 250, 500, 0]) {
    await page.locator('.table-scroll').evaluate((el, offset) => { el.scrollTop = offset; }, offset);
    assert.deepEqual(await surface(), initial, `full surface geometry at scrollTop=${offset}`);
    const current = await page.screenshot({ clip: initial.clip, animations: 'disabled' });
    const diff = await page.evaluate(async ([before, after, clip, roundedClip]) => {
      const pixels = async source => {
        const image = new Image(); image.src = 'data:image/png;base64,' + source; await image.decode();
        const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
        const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
        return { data: context.getImageData(0, 0, image.width, image.height).data, width: image.width };
      };
      const a = await pixels(before), b = await pixels(after), changed = [];
      // The rounded panel border is outside the header surface. Exclude its antialiasing,
      // which can blend scrolling rows; compare every pixel fully inside the panel clip.
      const inside = (x, y) => {
        const r = roundedClip.radius;
        const cx = Math.max(roundedClip.left + r, Math.min(x, roundedClip.right - r));
        const cy = Math.max(roundedClip.top + r, Math.min(y, roundedClip.bottom - r));
        return (x - cx) ** 2 + (y - cy) ** 2 <= r ** 2;
      };
      for (let i = 0; i < a.data.length; i += 4) {
        const x = clip.x + i / 4 % a.width, y = clip.y + Math.floor(i / 4 / a.width);
        if (![[x,y], [x+1,y], [x,y+1], [x+1,y+1]].every(([x,y]) => inside(x,y))) continue;
        if (a.data.slice(i, i + 4).some((value, channel) => value !== b.data[i + channel])) changed.push([i / 4 % a.width, Math.floor(i / 4 / a.width)]);
      }
      return { count: changed.length, first: changed.slice(0, 5), last: changed.slice(-5) };
    }, [screenshot.toString('base64'), current.toString('base64'), initial.clip, initial.roundedClip]);
    assert.equal(diff.count, 0, `full surface pixels at scrollTop=${offset}: ${JSON.stringify(diff)}`);
  }
  assert.deepEqual(await page.locator('.table-pagination-footer').boundingBox(), footer);
  assert.deepEqual(await page.locator('.ticket-row').evaluateAll(rows => rows.map(row => row.getBoundingClientRect().height)), rowHeights);
});
