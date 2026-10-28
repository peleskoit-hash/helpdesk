const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const template = fs.readFileSync('api/app/templates/tickets.html', 'utf8')
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, '');
const styles = fs.readFileSync('api/app/static/styles.css', 'utf8');
const list = fs.readFileSync('api/app/static/list.js', 'utf8');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });

async function setup(t, { width = 1440, height = 900, theme = 'light' } = {}) {
  const page = await browser.newPage({ viewport: { width, height } });
  t.after(() => page.close());
  const errors = [], requests = [], writes = [];
  page.on('pageerror', error => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, []));
  const preferences = { color_enabled: true, task_panel_enabled: false };
  await page.route('**/*', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (!path.startsWith('/api/')) return route.fulfill({ contentType: 'text/html', body: template });
    requests.push({ path, method: request.method() });
    let body;
    if (path.endsWith('/preferences')) {
      if (request.method() === 'PATCH') {
        writes.push(request.postDataJSON());
        Object.assign(preferences, writes.at(-1));
      }
      body = preferences;
    } else if (path === '/api/v1/users/me') body = { full_name: 'Synthetic', availability_status: 'online' };
    else body = { items: [], total: 0, page: 1, pages: 1, limit: 20 };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto('http://helpdesk.test/tickets?tab=active&page=1');
  await page.addStyleTag({ content: styles });
  await page.addStyleTag({ content: fs.readFileSync('api/app/static/profile-settings-v2.css', 'utf8') });
  assert.equal(await page.locator('#colorEnabledInput').getAttribute('role'), 'switch');
  assert.equal(await page.locator('#colorEnabledInput').evaluate(el => getComputedStyle(el).width), '40px');
  await page.evaluate(theme => {
    document.body.dataset.sidebarReady = '1';
    document.body.dataset.theme = theme;
    const sidebar = { role: 'agent', userId: 'synthetic', fullName: 'Synthetic', agents: [], counts: {} };
    window.HelpdeskSidebar = {
      init: async () => {}, getState: () => sidebar, onChange: () => () => {}, refreshCounts: async () => {},
    };
    window.HelpdeskAppShell = {
      registerPage: (_, init) => { window.initList = init; }, getCurrentPage: () => 'tickets-list',
    };
  }, theme);
  await page.addScriptTag({ content: list });
  await page.evaluate(() => { window.listPage = window.initList(); });
  await page.waitForFunction(() => document.querySelector('#colorEnabledInput').checked);
  assert.equal(await page.locator('#errorModal').isVisible(), false);
  return { page, requests, writes };
}

async function metrics(page) {
  return page.evaluate(() => {
    const anchor = document.querySelector('#colorToggleText');
    const nodes = [document.documentElement, document.body, document.querySelector('#appContent'),
      document.querySelector('.toolbar'), document.querySelector('.display-settings'),
      document.querySelector('.content')];
    return {
      containers: nodes.map(node => ({
        scrollWidth: node.scrollWidth, scrollHeight: node.scrollHeight,
        width: node.clientWidth, height: node.clientHeight,
      })),
      anchor: anchor.getBoundingClientRect().toJSON(),
      table: document.querySelector('.content').getBoundingClientRect().toJSON(),
    };
  });
}

async function opened(page) {
  await page.waitForFunction(() => document.querySelector('#colorLegend').matches(':popover-open'));
}

async function closed(page) {
  await page.waitForFunction(() => !document.querySelector('#colorLegend').matches(':popover-open'));
}

for (const theme of ['light', 'dark']) {
  test(`hover tooltip does not add scrolling or shift layout: ${theme}`, async t => {
    const { page, requests } = await setup(t, { theme });
    const before = await metrics(page);
    const networkBefore = requests.length;
    await page.locator('#colorToggleText').hover();
    await opened(page);
    assert.deepEqual(await metrics(page), before);
    const appearance = await page.locator('#colorLegend').evaluate(legend => ({
      labels: [...legend.children].map(node => node.textContent),
      sizes: [...legend.children].map(node => [node.offsetWidth, node.offsetHeight]),
      colors: [...legend.children].map(node => getComputedStyle(node).backgroundColor),
      background: getComputedStyle(legend).backgroundColor,
      padding: getComputedStyle(legend).padding,
      radius: getComputedStyle(legend).borderRadius,
      visible: legend.getAttribute('aria-hidden'),
    }));
    assert.deepEqual(appearance.labels, ['Задача', 'Заявка', 'Увольнение', 'Внутренняя', 'Замороженная', 'Закрытая']);
    assert.deepEqual(appearance.sizes, Array(6).fill([104, 24]));
    assert.deepEqual(appearance.colors, ['rgba(197, 243, 203, 0.78)', 'rgba(255, 202, 158, 0.78)',
      'rgba(221, 206, 255, 0.8)', 'rgba(255, 239, 145, 0.82)', 'rgba(205, 231, 255, 0.84)', 'rgba(255, 207, 213, 0.86)']);
    assert.equal(appearance.background, theme === 'dark' ? 'rgb(18, 28, 42)' : 'rgb(255, 255, 255)');
    assert.equal(appearance.padding, '8px');
    assert.equal(appearance.radius, '8px');
    assert.equal(appearance.visible, 'false');
    assert.equal(requests.length, networkBefore);
    const bounds = await page.locator('#colorLegend').boundingBox();
    assert.ok(bounds.y > before.anchor.bottom);
    // The tooltip is actually hit-testable outside the toolbar's clipped area.
    assert.equal(await page.evaluate(({ x, y }) => document.elementFromPoint(x, y)?.closest('#colorLegend')?.id,
      { x: bounds.x + 5, y: bounds.y + 5 }), 'colorLegend');
    await page.locator('#colorLegend').hover();
    await page.waitForTimeout(160);
    assert.equal(await page.locator('#colorLegend').evaluate(node => node.matches(':popover-open')), true);
    await page.mouse.move(0, 899);
    await closed(page);
    assert.deepEqual(await metrics(page), before);
  });
}

test('keyboard focus, Escape and preference toggle keep working', async t => {
  const { page, writes } = await setup(t);
  await page.locator('#searchInput').focus();
  await page.keyboard.press('Tab');
  assert.equal(await page.locator('#colorEnabledInput').evaluate(node => node === document.activeElement), true);
  await opened(page);
  await page.keyboard.press('Tab');
  assert.equal(await page.locator('#colorToggleText').evaluate(node => node === document.activeElement), true);
  await opened(page);
  await page.keyboard.press('Escape');
  await closed(page);
  await page.keyboard.press('Shift+Tab');
  await opened(page);
  await page.keyboard.press('Space');
  await page.waitForFunction(() => document.body.classList.contains('ticket-colors-disabled'));
  await closed(page);
  assert.deepEqual(writes.at(-1), { color_enabled: false });
  await page.locator('#colorToggleText').hover();
  assert.equal(await page.locator('#colorLegend').evaluate(node => node.matches(':popover-open')), false);
  await page.locator('#colorEnabledInput').check();
  await page.waitForFunction(() => !document.body.classList.contains('ticket-colors-disabled'));
  assert.deepEqual(writes.at(-1), { color_enabled: true });
  await page.mouse.move(0, 899);
  await page.locator('#colorToggleText').hover();
  await opened(page);
  await page.locator('#themeToggleBtn').focus();
  await page.mouse.move(0, 899);
  await closed(page);
});

for (const side of ['left', 'right']) {
  test(`tooltip stays inside the viewport and flips above at bottom-${side}`, async t => {
    const { page } = await setup(t, { width: 800, height: 400 });
    await page.evaluate(side => {
      const toolbar = document.querySelector('.toolbar');
      Object.assign(toolbar.style, { position: 'fixed', bottom: '0', top: 'auto', width: '300px',
        left: side === 'left' ? '0' : 'auto', right: side === 'right' ? '0' : 'auto' });
      for (const node of toolbar.children) if (!node.matches('.display-settings')) node.style.display = 'none';
      document.querySelector('#themeToggleBtn').style.display = 'none';
    }, side);
    const before = await metrics(page);
    await page.locator('#colorToggleText').hover();
    await opened(page);
    const bounds = await page.locator('#colorLegend').boundingBox();
    assert.ok(bounds.x >= 8 && bounds.x + bounds.width <= 792);
    assert.ok(bounds.y >= 8 && bounds.y + bounds.height < before.anchor.top);
    assert.deepEqual(await metrics(page), before);
  });
}

test('tooltip follows resize and toolbar scroll without changing scroll ranges', async t => {
  const { page } = await setup(t, { width: 900, height: 600 });
  await page.locator('#colorToggleText').hover();
  await opened(page);
  await page.setViewportSize({ width: 760, height: 600 });
  await page.locator('#colorToggleText').scrollIntoViewIfNeeded();
  await page.locator('#colorToggleText').hover();
  await opened(page);
  const bounds = await page.locator('#colorLegend').boundingBox();
  assert.ok(bounds.x >= 8 && bounds.x + bounds.width <= 752);
  const before = await metrics(page);
  await page.mouse.move(0, 599);
  await closed(page);
  assert.deepEqual(await metrics(page), before);
  await page.locator('#colorToggleText').hover();
  await opened(page);
  await page.evaluate(() => {
    document.querySelector('.search-input-wrap').style.minWidth = '900px';
    const toolbar = document.querySelector('.toolbar');
    toolbar.scrollLeft = toolbar.scrollLeft === 0 ? 1 : 0;
  });
  await closed(page);
});

test('page cleanup hides the top-layer tooltip and removes handlers', async t => {
  const { page } = await setup(t);
  await page.locator('#colorToggleText').hover();
  await opened(page);
  await page.evaluate(() => window.listPage.destroy());
  await closed(page);
  assert.equal(await page.locator('#colorLegend').getAttribute('aria-hidden'), 'true');
  await page.mouse.move(0, 899);
  await page.locator('#colorToggleText').hover();
  assert.equal(await page.locator('#colorLegend').evaluate(node => node.matches(':popover-open')), false);
  assert.equal(await page.locator('#colorLegend').count(), 1);
});

for (const theme of ['light', 'dark']) test(`color switch reuses the standard V2 skin in both states: ${theme}`, async t => {
  const { page, writes } = await setup(t, { theme });
  await page.addStyleTag({ content: fs.readFileSync('api/app/static/desk-v2.css', 'utf8').replace(/@import[^;]+;/g, '') });
  const input = page.locator('#colorEnabledInput');
  assert.equal(await input.getAttribute('class'), 'desk-toggle');
  assert.doesNotMatch(fs.readFileSync('api/app/static/appearance-pattern.css', 'utf8'), /color-toggle|v2-switch/);
  const geometry = () => input.evaluate(el => { const r = el.getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; });
  const before = await geometry();
  for (const checked of [true, false]) {
    await input.setChecked(checked);
    await page.waitForFunction(checked => document.body.classList.contains('ticket-colors-disabled') === !checked, checked);
    await page.waitForTimeout(180);
    const result = await input.evaluate(el => {
      const reference = document.createElement('input'); reference.type = 'checkbox'; reference.className = 'desk-toggle'; reference.checked = el.checked;
      reference.style.transition = 'none'; document.body.appendChild(reference);
      const skin = node => { const s = getComputedStyle(node), knob = getComputedStyle(node, '::before');
        return [s.width, s.height, s.backgroundColor, s.borderRadius, knob.backgroundColor, knob.width, knob.transform]; };
      const result = [skin(el), skin(reference)]; reference.remove(); return result;
    });
    assert.deepEqual(result[0], result[1]);
    assert.deepEqual(result[0].slice(0,2), ['40px', '24px']);
    assert.equal(result[0][4], 'rgb(255, 255, 255)');
    assert.deepEqual(await geometry(), before);
    if (process.env.SWITCH_SCREENSHOT_DIR) await page.locator('.display-settings').screenshot({ path: `${process.env.SWITCH_SCREENSHOT_DIR}/color-switch-${theme}-${checked ? 'on' : 'off'}.png` });
  }
  assert.deepEqual(writes.at(-1), { color_enabled: false });
});
