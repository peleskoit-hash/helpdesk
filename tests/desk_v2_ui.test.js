// Run with PLAYWRIGHT_MODULE pointing at an installed Playwright, if not local.
// Real templates, styles and scripts; synthetic API keeps local databases untouched.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });

for (const theme of ['light', 'dark']) test(`agent pastel background and all message text remain readable: ${theme}`, async t => {
  const { page } = await setup(t, { width: 1366, height: 900, theme, pageName: 'ticket',
    cardPatch: { messages: card.messages.map(message => message.message_type === 'agent'
      ? { ...message, message_status: 'edited', status_note: 'Сообщение исправлено' } : message) } });
  const agent = page.locator('.message.agent').first();
  const style = await agent.evaluate(el => {
    const rgb = color => color.match(/\d+/g).slice(0, 3).map(Number);
    const luminance = color => rgb(color).map(v => { v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; })
      .reduce((sum, value, i) => sum + value * [.2126, .7152, .0722][i], 0);
    const background = getComputedStyle(el).backgroundColor, b = luminance(background);
    return { background, texts: ['.message-header strong', '.message-body', '.message-date', '.message-status-note'].map(selector => {
      const node = el.querySelector(selector), s = getComputedStyle(node), a = luminance(s.color);
      return { selector, color: s.color, opacity: s.opacity, contrast: (Math.max(a, b) + .05) / (Math.min(a, b) + .05) };
    }) };
  });
  assert.equal(style.background, theme === 'dark' ? 'rgb(200, 175, 246)' : 'rgb(201, 177, 244)');
  for (const text of style.texts) {
    assert.equal(text.color, 'rgb(48, 44, 64)'); assert.equal(text.opacity, '1');
    assert.ok(text.contrast >= 4.5, JSON.stringify(text));
  }
  const hsl = values => {
    const [r, g, b] = values.map(value => value / 255), max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min, l = (max + min) / 2;
    return { h: ((r - g) / d + 4) * 60, s: d / (1 - Math.abs(2 * l - 1)), l };
  };
  const light = hsl([201, 177, 244]), dark = hsl([200, 175, 246]);
  assert.ok(Math.abs(dark.l - light.l) < .001 && Math.abs(dark.h - light.h) < 1);
  // Allow one-channel quantization when the +7% HSL result is stored as 8-bit RGB.
  assert.ok(Math.abs(dark.s / light.s - 1.07) < .015);
});

for (const theme of ['light', 'dark']) test(`quick reply accordion V2 populated rows, stable hover and actions: ${theme}`, async t => {
  const replies = [0, 1].map(i => ({ id: `reply-${i}`, short_title: `Useful ${i}`, body: `Saved answer ${i}`, can_edit: true, saved_files: [] }));
  const { page, requests } = await setup(t, { width: 1440, height: 1000, theme, role: 'agent', pageName: 'ticket', quickReplies: replies });
  const agent = await page.locator('.message.agent').first().evaluate(el => ({ background: getComputedStyle(el).backgroundColor,
    frozen: getComputedStyle(document.body).getPropertyValue('--desk-blue-bg').trim() }));
  assert.equal(agent.background, theme === 'dark' ? 'rgb(200, 175, 246)' : 'rgb(201, 177, 244)');
  assert.notEqual(agent.background, agent.frozen);
  await page.locator('#ticketMessageBody').fill('Start end');
  await page.locator('#ticketMessageBody').evaluate(el => el.setSelectionRange(6, 6));
  await page.locator('#quickRepliesBtn').click();
  const panel = page.locator('#quickRepliesPanel'), rows = panel.locator('[data-quick-reply-id]');
  await page.waitForFunction(() => document.querySelectorAll('#quickRepliesPanel [data-quick-reply-id]').length === 2);
  assert.equal(await panel.locator('.quick-replies-panel-placeholder').count(), 0);
  const shortHeight = (await panel.boundingBox()).height;
  const preview = rows.first().locator('.quick-replies-panel-preview');
  const metrics = () => preview.evaluate(el => { const s = getComputedStyle(el), r = el.getBoundingClientRect(); return [s.fontWeight, s.fontSize, s.lineHeight, r.width, r.height]; });
  const normal = await metrics();
  await rows.first().hover(); assert.deepEqual(await metrics(), normal);
  assert.equal(normal[0], '400');
  const colors = await panel.evaluate(el => {
    const search = el.querySelector('input'), row = el.querySelector('[data-quick-reply-id]');
    return { panel: getComputedStyle(el).backgroundColor, search: getComputedStyle(search).backgroundColor,
      text: getComputedStyle(search).color, row: getComputedStyle(row).backgroundColor,
      surface: getComputedStyle(document.body).getPropertyValue('--desk-surface').trim(),
      soft: getComputedStyle(document.body).getPropertyValue('--desk-soft').trim() };
  });
  assert.equal(colors.panel, colors.surface); assert.equal(colors.search, colors.soft);
  assert.notEqual(colors.text, colors.search);
  await rows.first().click();
  await page.waitForFunction(() => document.getElementById('ticketMessageBody').value === 'Start Saved answer 0end');
  assert.equal(await panel.isVisible(), false);
  assert.equal(requests.filter(r => r.path.endsWith('/reply-0/use')).length, 1);
  await page.locator('#quickRepliesBtn').click();
  async function search(query, count) {
    await page.locator('#quickRepliesSearchInput').fill(query);
    await page.waitForResponse(response => response.url().includes('/quick-replies/search?') && new URL(response.url()).searchParams.get('q') === query);
    await page.waitForFunction(count => document.querySelectorAll('#quickRepliesPanel [data-quick-reply-id]').length === count, count);
    assert.equal(await panel.locator('.quick-replies-panel-placeholder').count(), 0);
  }
  await search('not found', 0);
  assert.equal(await page.locator('#quickRepliesEditorRow').isVisible(), true);
  await search('Useful 1', 1);
  replies.push(...[2, 3, 4, 5].map(i => ({ id: `reply-${i}`, short_title: `Other ${i}`, body: `Saved answer ${i}`, can_edit: true, saved_files: [] })));
  await search('', 5);
  assert.ok((await panel.boundingBox()).height > shortHeight);
  await page.keyboard.press('Escape');
  await page.keyboard.press('Control+q');
  await page.locator('#quickRepliesSearchInput').waitFor({ state: 'visible' });
  await page.keyboard.press('ArrowDown');
  assert.equal(await rows.first().evaluate(el => el.classList.contains('highlighted')), true);
  assert.deepEqual(await preview.evaluate(el => [getComputedStyle(el).fontWeight]), ['400']);
  await page.locator('#quickRepliesEditorRow').click();
  await page.waitForURL(/\/quick-replies\?ticket_id=demo-0/);
  assert.equal(await page.locator('#quickRepliesList').isVisible(), true);
  await page.goBack(); await page.waitForSelector('.message');
  await page.locator('#quickRepliesBtn').click();
  await page.waitForFunction(() => document.querySelectorAll('#quickRepliesPanel [data-quick-reply-id]').length === 5);
  assert.equal(await panel.locator('.quick-replies-panel-placeholder').count(), 0);
});

for (const theme of ['light', 'dark']) {
  test(`visual controls stay painted across theme and settings changes: ${theme}`, async t => {
    const { page, requests } = await setup(t, { width: 1366, height: 900, theme, role: 'agent', pageName: 'tickets' });
    const palette = { online: 'rgb(169, 227, 195)', on_ticket: 'rgb(201, 177, 244)', waiting: 'rgb(245, 212, 165)',
      lunch: 'rgb(175, 216, 249)', offline: 'rgb(245, 186, 202)' };
    const textPalette = { online: 'rgb(21, 91, 62)', on_ticket: 'rgb(86, 50, 146)', waiting: 'rgb(135, 83, 21)',
      lunch: 'rgb(35, 91, 138)', offline: 'rgb(145, 61, 89)' };
    for (const [status, color] of Object.entries(palette)) {
      const button = page.locator(`.agent-status-btn[data-availability-status="${status}"]`);
      assert.equal(await button.evaluate(el => getComputedStyle(el).backgroundColor), color);
      assert.equal(await button.evaluate(el => getComputedStyle(el).color), textPalette[status]);
      await button.click();
      assert.equal(await button.evaluate(el => el.classList.contains('active')), true);
      assert.notEqual(await button.evaluate(el => getComputedStyle(el).boxShadow), 'none');
    }
    assert.ok(requests.filter(r => r.path.endsWith('/users/me/status') && r.method === 'PATCH').length >= 5);
    await page.locator('#themeToggleBtn').click();
    async function assertTracksPainted() {
      await page.locator('#appearanceMenu').waitFor({ state: 'visible' });
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const rects = await page.locator('#appearanceMenu input[type="range"]').evaluateAll(nodes => nodes.map(el => {
        const r = el.getBoundingClientRect();
        return { id: el.id, x: r.x, y: r.y, width: r.width, height: r.height, appearance: getComputedStyle(el).appearance };
      }));
      const png = await page.screenshot();
      const samples = await page.evaluate(async ({ base64, rects }) => {
        const image = new Image(); image.src = `data:image/png;base64,${base64}`; await image.decode();
        const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
        const ctx = canvas.getContext('2d'); ctx.drawImage(image, 0, 0);
        return rects.map(r => {
          const pixels = ctx.getImageData(Math.ceil(r.x) + 12, Math.floor(r.y + r.height / 2), Math.floor(r.width) - 24, 1).data;
          const colors = new Set(); let chromatic = 0;
          for (let i = 0; i < pixels.length; i += 4) {
            colors.add([pixels[i], pixels[i + 1], pixels[i + 2]].map(v => Math.round(v / 8)).join(','));
            if (Math.max(pixels[i], pixels[i + 1], pixels[i + 2]) - Math.min(pixels[i], pixels[i + 1], pixels[i + 2]) > 30) chromatic++;
          }
          return { id: r.id, appearance: r.appearance, colors: colors.size, chromatic };
        });
      }, { base64: png.toString('base64'), rects });
      for (const sample of samples) {
        assert.equal(sample.appearance, 'none');
        assert.ok(sample.colors > 12 && sample.chromatic > 20, JSON.stringify(sample));
      }
    }
    await assertTracksPainted();
    for (const mode of [theme === 'light' ? 'dark' : 'light', theme]) {
      await page.locator(`[data-appearance-theme="${mode}"]`).click();
      await page.locator('#appearance-interface-hue').fill('184');
      await page.locator('#appearance-accent-hue').fill('29');
      await page.locator('#appearance-interface-intensity').fill('0');
      await page.locator('#appearance-accent-intensity').fill('100');
      await page.waitForFunction(() => !saveInFlight);
      await assertTracksPainted();
      for (const [status, color] of Object.entries(palette)) {
        assert.equal(await page.locator(`.agent-status-btn[data-availability-status="${status}"]`).evaluate(el => getComputedStyle(el).backgroundColor), color);
        assert.equal(await page.locator(`.agent-status-btn[data-availability-status="${status}"]`).evaluate(el => getComputedStyle(el).color), textPalette[status]);
      }
    }
    await page.reload(); await page.waitForSelector('.ticket-row');
    await page.locator('#themeToggleBtn').click(); await assertTracksPainted();
    await page.keyboard.press('Escape');
    await page.locator('.ticket-row-link').first().click(); await page.waitForSelector('.message');
    await page.locator('#themeToggleBtn').click(); await assertTracksPainted();
  });
}

for (const height of [768, 900]) for (const theme of ['light', 'dark']) {
  test(`list arrows, header separator and hanging logo geometry: ${theme}, ${height}`, async t => {
    const { page } = await setup(t, { width: 1366, height, theme, pageName: 'tickets' });
    const geometry = await page.evaluate(() => {
      const brand = document.querySelector('.sidebar-brand'), bs = getComputedStyle(brand), br = brand.getBoundingClientRect();
      const scroll = document.querySelector('.table-scroll'), sr = scroll.getBoundingClientRect(), head = document.querySelector('thead').getBoundingClientRect();
      return { brand: { x: br.x + parseFloat(bs.paddingLeft), y: br.y, before: getComputedStyle(brand, "::before").content, background: getComputedStyle(brand.closest('.sidebar')).backgroundColor, color: bs.color, text: brand.textContent, size: bs.fontSize, family: bs.fontFamily },
        line: { y: Math.floor(head.bottom) - 1, left: sr.left + 1, right: sr.left + scroll.clientWidth - 2, color: getComputedStyle(document.querySelector('th')).borderBottomColor },
        buttons: ['prevPageBtn', 'nextPageBtn'].map(id => {
          const el = document.getElementById(id), r = el.getBoundingClientRect(), range = document.createRange(); range.selectNodeContents(el);
          const text = range.getBoundingClientRect();
          return { width: r.width, height: r.height, dx: (text.left + text.right - r.left - r.right) / 2, dy: (text.top + text.bottom - r.top - r.bottom) / 2 };
        }) };
    });
    for (const button of geometry.buttons) {
      assert.ok(Math.abs(button.width - 34.609375) < .5); assert.equal(button.height, 30);
      assert.ok(Math.abs(button.dx) < .5 && Math.abs(button.dy) < 1, JSON.stringify(button));
    }
    assert.equal(geometry.brand.text, 'ES DESK'); assert.equal(geometry.brand.size, '34px');
    assert.match(geometry.brand.family, /ESHelpdeskLogo/);
    assert.equal(geometry.brand.before, 'none');
    assert.equal(geometry.brand.x, 28);
    assert.equal(geometry.brand.y, 4);
    const png = await page.screenshot();
    const painted = await page.evaluate(async ({ base64, geometry }) => {
      const image = new Image(); image.src = `data:image/png;base64,${base64}`; await image.decode();
      const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
      const ctx = canvas.getContext('2d'); ctx.drawImage(image, 0, 0);
      const rgb = value => value.match(/\d+/g).slice(0, 3).map(Number);
      const distance = (p, c) => Math.max(...c.map((v, i) => Math.abs(p[i] - v)));
      const pixel = (x, y) => ctx.getImageData(Math.floor(x), Math.floor(y), 1, 1).data;
      let threads = 0, previous = false;
      for (let x = Math.floor(geometry.brand.x); x < geometry.brand.x + 130; x++) {
        const hit = distance(pixel(x, 1), rgb(geometry.brand.background)) > 25;
        if (hit && !previous) threads++; previous = hit;
      }
      return { threads, line: [geometry.line.left, geometry.line.right].map(x => distance(pixel(x, geometry.line.y), rgb(geometry.line.color))) };
    }, { base64: png.toString('base64'), geometry });
    assert.equal(painted.threads, 6);
    assert.ok(painted.line.every(distance => distance < 10), JSON.stringify(painted));
  });
}

for (const theme of ['light', 'dark']) for (const role of ['admin', 'agent']) {
  test(`list row rounded hover and icon overflow: ${theme}, ${role}`, async t => {
    const { page } = await setup(t, { width: 1366, height: 900, theme, role, pageName: 'tickets' });
    const row = page.locator('tr.ticket-row').first();
    const appearance = () => row.evaluate(el => ({
      size: [el.getBoundingClientRect().width, el.getBoundingClientRect().height],
      background: getComputedStyle(el).background,
      cells: [...el.cells].map(td => {
        const s = getComputedStyle(td);
        return [td.clientWidth, td.clientHeight, s.padding, s.background, s.borderTopWidth, s.borderBottomWidth];
      }),
      icons: [...el.querySelectorAll('.ticket-channel-icon, .ticket-channel-icon svg, input, select')].map(icon => {
        const s = getComputedStyle(icon), box = icon.getBoundingClientRect(), cell = icon.closest('td').getBoundingClientRect();
        return [icon.outerHTML, box.x - cell.x, box.y - cell.y, box.width, box.height, s.pointerEvents];
      }),
    }));
    await page.mouse.move(0, 0);
    const before = await appearance();
    await row.locator('.ticket-row-link').first().hover();
    assert.deepEqual(await appearance(), before);
    const border = await row.evaluate(el => {
      const probe = document.createElement('span');
      probe.style.color = 'var(--desk-accent)'; el.firstElementChild.appendChild(probe);
      const accent = getComputedStyle(probe).color; probe.remove();
      return { accent, outline: getComputedStyle(el).outlineStyle,
        cells: [...el.cells].map(td => [getComputedStyle(td).borderTopColor, getComputedStyle(td).borderBottomColor]),
        left: getComputedStyle(el.firstElementChild).borderLeftColor,
        right: getComputedStyle(el.lastElementChild).borderRightColor,
        radii: [getComputedStyle(el.firstElementChild).borderTopLeftRadius, getComputedStyle(el.lastElementChild).borderTopRightRadius] };
    });
    assert.equal(border.outline, 'none');
    assert.ok(border.cells.every(colors => colors.every(color => color === border.accent)));
    assert.equal(border.left, border.accent); assert.equal(border.right, border.accent);
    assert.deepEqual(border.radii, ['14px', '14px']);
    for (const selector of ['td.ticket-channel-col', ...(role === 'admin' ? ['td.ticket-check-cell'] : [])]) {
      assert.equal(await row.locator(selector).evaluate(el => getComputedStyle(el).textOverflow), 'clip');
    }
    assert.equal(await row.locator('td.ticket-linked-cell').first().evaluate(el => getComputedStyle(el).textOverflow), 'ellipsis');
    assert.deepEqual(await page.locator('.ticket-channel-icon svg').evaluateAll(nodes => [...new Set(nodes.map(el => {
      const box = el.getBoundingClientRect(); return `${box.width}x${box.height}`;
    }))]), ['17x17']);

    // Sample the painted border itself: top edge, curved corner and outer corner.
    for (const side of ['left', 'right']) {
      if (side === 'right') await page.locator('.table-scroll').evaluate(el => { el.scrollLeft = el.scrollWidth; });
      const cell = side === 'left' ? row.locator('td').first() : row.locator('td').last();
      const png = await cell.screenshot();
      const pixels = await page.evaluate(async ({ base64, side }) => {
        const image = new Image(); image.src = `data:image/png;base64,${base64}`; await image.decode();
        const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
        const ctx = canvas.getContext('2d'); ctx.drawImage(image, 0, 0);
        const pixel = (x, y) => [...ctx.getImageData(side === 'left' ? x : image.width - 1 - x, y, 1, 1).data].slice(0, 3);
        const curve = [];
        for (let x = 3; x <= 10; x++) for (let y = 3; y <= 10; y++) curve.push(pixel(x, y));
        return { top: pixel(18, 0), curve, outside: pixel(0, 0) };
      }, { base64: png.toString('base64'), side });
      const accent = border.accent.match(/\d+/g).slice(0, 3).map(Number);
      const distance = pixel => Math.max(...pixel.map((value, index) => Math.abs(value - accent[index])));
      assert.ok(distance(pixels.top) < 8, `${side} top border: ${pixels.top}`);
      assert.ok(pixels.curve.some(pixel => distance(pixel) < 40), `${side} curved border is painted`);
      assert.ok(distance(pixels.outside) > 20, `${side} outer corner stays rounded`);
    }
    await page.locator('.table-scroll').evaluate(el => { el.scrollLeft = 0; });
    if (role === 'admin') { await row.locator('.ticket-check').check(); assert.equal(await row.locator('.ticket-check').isChecked(), true); }
    await page.locator('#columnsSettingsBtn').click();
    assert.equal(await page.locator('#columnsModal').isVisible(), true);
    await page.locator('#closeColumnsModalBtn').click();
    assert.equal(await page.locator('link[href="/static/styles.css?v=20261027-agent-pastel-v2"]').count(), 1);
    await row.locator('.ticket-row-link').first().click();
    await page.waitForSelector('.message');
    await page.locator('#backToListBtn').click(); await page.waitForSelector('.ticket-row');
    await page.locator('.ticket-row-link').first().hover();
    assert.equal(await page.locator('link[href="/static/styles.css?v=20261027-agent-pastel-v2"]').count(), 1);
    assert.equal(await page.locator('td.ticket-channel-col').first().evaluate(el => getComputedStyle(el).textOverflow), 'clip');
  });
}

for (const theme of ['light', 'dark']) {
  test(`shell styles do not affect matching classes in settings content: ${theme}`, async t => {
    const page = await browser.newPage({ viewport: { width: 1366, height: 768 } });
    t.after(() => page.close());
    await page.setContent(fs.readFileSync('api/app/templates/settings_client_card.html', 'utf8')
      .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, ''));
    await page.addStyleTag({ content: fs.readFileSync('api/app/static/styles.css', 'utf8').replace(/^@import[^;]+;/, '') });
    await page.evaluate(theme => {
      document.body.dataset.theme = theme;
      document.body.dataset.sidebarReady = '1';
      const content = document.querySelector('#appContent .content');
      const probe = document.createElement('div');
      probe.id = 'shellScopeProbe';
      // Keep available content width constant while shared sidebar geometry changes.
      probe.style.width = '600px';
      for (const cls of ['tab-btn', 'tab-btn active', 'settings-link', 'settings-link active', 'admin-agent-item',
        'admin-agent-item active', 'tab-btn-count', 'tab-list', 'sidebar-brand', 'sidebar-title', 'admin-agent-filter',
        'admin-agent-list', 'admin-agent-header', 'admin-agent-header pressed', 'admin-agent-count-active',
        'admin-agent-count-divider', 'admin-agent-count-frozen', 'admin-agent-count-divider-frozen',
        'sidebar-user-name', 'sidebar-user-menu-item', 'sidebar-user-menu-item-danger', 'sidebar-bottom',
        'agent-avatar', 'agent-status-btn', 'agent-status-btn active', 'display-settings', 'bulk-admin-actions']) {
        const el = document.createElement('div');
        el.className = cls;
        el.textContent = 'Контроль стилей';
        probe.appendChild(el);
      }
      content.appendChild(probe);
    }, theme);
    const appearance = () => page.locator('#shellScopeProbe > *').evaluateAll(nodes => nodes.map(el => {
      const style = getComputedStyle(el);
      return Object.fromEntries(['color', 'background', 'border', 'borderRadius', 'padding', 'margin',
        'font', 'boxShadow', 'backdropFilter', 'width', 'height', 'minWidth', 'maxHeight', 'gap', 'overflowY']
        .map(key => [key, style[key]]));
    }));
    const before = await appearance();
    await page.addStyleTag({ content: fs.readFileSync('api/app/static/desk-v2.css', 'utf8') });
    assert.deepEqual(await appearance(), before);
  });
}
const stamp = '2026-10-23T10:20:00Z';
const user = { id: 'agent-1', role: 'admin', login: 'demo', full_name: 'Александр Сергеевич Иванов', availability_status: 'online' };
const columns = ['subject', 'requester', 'assignee', 'department', 'type', 'location', 'age', 'created_at', 'last_reply'];
const subjects = ['Не подключается рабочая почта', 'Настройка доступа к Bitrix', 'Проверить подключение принтера', 'Новый сотрудник: доступы и оборудование'];
const rows = Array.from({ length: 28 }, (_, i) => ({ id: `demo-${i}`, subject: `${subjects[i % 4]} · ${1200 + i}`,
  workflow_type: i % 3 ? 'task' : 'ticket', status: 'new', assignee_id: user.id, assignee_name: user.full_name,
  requester_display_name: 'Мария Смирнова', requester_department: 'Отдел Продаж', requester_location_type: 'Офисный',
  created_at: stamp, last_reply_at: stamp, channel: 'telegram', seen_by_agent: i % 2 === 0,
  task_kind: i % 4 === 1 ? 'group_child' : 'normal', is_internal_task: i % 4 === 2,
}));
const card = { ...rows[0], client_name: 'Мария Смирнова', instruction_text: 'Проверьте настройки почтового клиента.\n'.repeat(80),
  client_profile: { last_name: 'Смирнова', contact_name: 'Мария', telegram_display_name: 'Мария Смирнова',
    telegram_username: 'maria_demo', destination: 'Отдел Продаж', sales_group: 'группа Власова', location_type: 'Офисный',
    internal_number: '204', bitrix_login: 'm.smirnova', bitrix_id: '1204', is_verified: true },
  messages: Array.from({ length: 32 }, (_, i) => ({ id: `m-${i}`, message_type: ['client', 'agent', 'internal', 'system'][i % 4],
    body: ['Добрый день! Не получается войти в рабочую почту. После ввода пароля появляется сообщение об ошибке подключения.',
      'Мария, добрый день! Проверю настройки подключения. Подскажите, пожалуйста, работает ли почта через браузер?',
      'Проверить параметры сервера и доступ из офисной сети. Ожидаем ответ клиента.',
      'Заявка назначена ответственному: Александр Иванов'][i % 4], author_name: user.full_name, created_at: stamp, attachments: [] })),
};

for (const theme of ['light', 'dark']) for (const width of [1366, 1920]) {
  test(`sidebar selection keeps adaptive width stable: ${theme}, ${width}`, async t => {
    const { page } = await setup(t, { width, height: 900, theme, pageName: 'tickets', fullName: 'Тест' });
    await page.locator('.admin-agent-name').evaluateAll(nodes => {
      nodes[0].textContent = 'Ли Бо';
      nodes[1].textContent = 'Иван Александрович Соколов';
    });
    const sidebar = page.locator('.sidebar');
    const initial = (await sidebar.boundingBox()).width;
    for (const index of [0, 1, 0, 1]) {
      await page.locator('.admin-agent-item').nth(index).click();
      assert.equal((await sidebar.boundingBox()).width, initial);
    }
    assert.deepEqual(await page.locator('.tab-list').evaluate(el => {
      const s = getComputedStyle(el); return [s.borderRadius, s.borderTopWidth, s.padding];
    }), ['16px', '1px', '8px']);
    assert.equal(await sidebar.evaluate(el => [el, ...el.querySelectorAll('.admin-agent-item, .admin-agent-name, .admin-agent-counts')].some(n => n.scrollWidth > n.clientWidth + 1)), false);
  });
}

for (const theme of ['light', 'dark']) for (const role of ['admin', 'agent']) {
  test(`ticket action panel V2: ${role}, ${theme}`, async t => {
    const cardPatch = { status: 'pending', wrong_problem_used: false, auto_close_disabled: false };
    const { page, requests } = await setup(t, { width: 1366, height: 768, theme, role, pageName: 'ticket', cardPatch });
    const close = page.locator('#closeBtn'), restore = page.locator('#restoreBtn'), wrong = page.locator('#wrongProblemBtn');
    assert.equal(await restore.isVisible(), true);
    assert.equal(await restore.isDisabled(), true);
    assert.equal(await wrong.isVisible(), true);
    assert.equal(await wrong.isDisabled(), false);
    assert.equal(await close.evaluate(el => getComputedStyle(el).fontSize), '19px');
    const block = await page.locator('.close-wrap').evaluate(el => {
      const s = getComputedStyle(el), c = el.querySelector('#closeBtn').getBoundingClientRect(), r = el.querySelector('#restoreBtn').getBoundingClientRect();
      return { radius: s.borderRadius, border: s.borderTopWidth, overlap: r.bottom > c.top, inside: c.bottom <= el.getBoundingClientRect().bottom };
    });
    assert.deepEqual(block, { radius: '16px', border: '1px', overlap: false, inside: true });
    assert.equal(await page.locator('.freeze-box').evaluate(el => {
      const s = getComputedStyle(el), button = el.querySelector('#freezeToggleBtn');
      return button.getBoundingClientRect().width === el.clientWidth - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight);
    }), true);
    assert.equal(await page.locator('.freeze-box').innerText(), 'Заморозить\nАвторазморозка');
    const timerGeometry = () => page.locator('.ticket-control-wrap').evaluate(root => {
      const top = root.getBoundingClientRect().top - root.scrollTop;
      return ['#autoCloseBox', '.freeze-box', '.wrong-problem-wrap', '.ticket-quick-actions', '.close-wrap'].map(selector => {
        const box = root.querySelector(selector).getBoundingClientRect(); return [box.top - top, box.height];
      });
    });
    const timerReference = await timerGeometry();
    const future = new Date(Date.now() + 15 * 60 * 1000).toISOString();
    for (const patch of [
      { auto_close_due_at: future, auto_close_disabled: false },
      { auto_close_due_at: null, auto_close_disabled: false },
      { auto_close_due_at: future, auto_close_disabled: true },
      { intake_state: 'collecting' },
      { intake_state: 'ready', is_special: true },
      { is_special: false, status: 'closed' },
    ]) {
      Object.assign(cardPatch, patch);
      await page.reload();
      await page.waitForSelector('.message');
      assert.deepEqual(await timerGeometry(), timerReference);
      const available = cardPatch.status !== 'closed' && !cardPatch.is_special && cardPatch.intake_state !== 'collecting';
      const due = await page.locator('#autoCloseDueText').innerText();
      if (available && cardPatch.auto_close_due_at && !cardPatch.auto_close_disabled) assert.match(due, /^До закрытия: \d+ м\.$/);
      else assert.equal(due, 'До закрытия: --');
      assert.equal(await page.locator('#autoCloseDisabledCheckbox').isDisabled(), !available);
    }
    Object.assign(cardPatch, { status: 'pending', auto_close_due_at: null, auto_close_disabled: false, is_special: false, intake_state: 'ready' });
    await page.reload();
    await page.waitForSelector('.message');
    const timer = page.locator('#autoCloseDisabledCheckbox');
    assert.deepEqual(await timer.evaluate(el => { const s = getComputedStyle(el); return [s.width, s.height, s.appearance]; }), ['40px', '24px', 'none']);
    await timer.focus();
    await page.keyboard.press('Space');
    await page.waitForResponse(r => r.url().endsWith('/auto-close') && r.request().method() === 'PATCH');
    if (role === 'admin') {
      assert.equal(await page.locator('#ticketAssigneeBox + .admin-actions-box #adminForceCloseBtn').count(), 1);
      assert.equal(await page.locator('#ticketAssigneeBox + .admin-actions-box #adminDeleteBtn').count(), 1);
      assert.equal(await page.locator('#ticketAssigneeBox + .admin-actions-box #adminSkipClientNotification').count(), 1);
      assert.equal(await page.locator('#ticketAssigneeBox + .admin-actions-box > :first-child #adminSkipClientNotification').count(), 1);
      assert.equal(await page.locator('#ticketAssigneeBox + .admin-actions-box + .admin-actions-box #markSpecialBtn').count(), 1);
      const skip = page.locator('#adminSkipClientNotification');
      assert.equal(await skip.evaluate(el => getComputedStyle(el).width), '40px');
      await skip.check();
      assert.equal(await skip.isChecked(), true);
      await page.locator('#adminForceCloseBtn').click();
      await modalVisible(page, '#adminActionModal');
      await page.locator('#cancelAdminActionBtn').click();
      assert.equal(await skip.isChecked(), false);
      const spacing = await page.locator('.sidebar').evaluate(el => {
        const filter = el.querySelector('.admin-agent-filter').getBoundingClientRect();
        const links = ['/settings', '/statuses', '/management/tasks'].map(p => el.querySelector(`a[href="${p}"]`).getBoundingClientRect());
        return { above: links[0].top - filter.bottom, gaps: [links[1].top - links[0].bottom, links[2].top - links[1].bottom],
          separator: getComputedStyle(el.querySelector('.sidebar-admin-separator')).display };
      });
      assert.equal(spacing.separator, 'none');
      assert.ok(spacing.above >= 14);
      assert.equal(spacing.gaps[0], spacing.gaps[1]);
    } else {
      assert.equal(await page.locator('#adminForceCloseBtn').isVisible(), false);
      assert.equal(await page.locator('#adminDeleteBtn').isVisible(), false);
    }
    for (const owner of ['agent-1', 'another-agent']) {
      Object.assign(cardPatch, { status: 'closed', closed_by_user_id: owner });
      await page.reload();
      await page.waitForSelector('.message');
      assert.equal(await restore.isVisible(), true);
      assert.equal(await restore.isDisabled(), role !== 'admin' && owner !== 'agent-1');
      assert.equal(await wrong.isVisible(), true);
      assert.equal(await wrong.isDisabled(), true);
      assert.equal(await close.isDisabled(), true);
      assert.equal(await page.locator('#freezeToggleBtn').isVisible(), true);
      assert.equal(await page.locator('#freezeToggleBtn').isDisabled(), true);
      if (role === 'admin') {
        assert.equal(await page.locator('#adminForceCloseBtn').isVisible(), true);
        assert.equal(await page.locator('#adminForceCloseBtn').isDisabled(), true);
      }
    }
    Object.assign(cardPatch, { status: 'pending', wrong_problem_used: true });
    await page.reload();
    await page.waitForSelector('.message');
    assert.equal(await wrong.isVisible(), true);
    assert.equal(await wrong.isDisabled(), true);
    assert.equal(requests.filter(r => r.method === 'POST' || r.method === 'DELETE').length, 0);
    if (process.env.TICKET_ACTIONS_SCREENSHOT_DIR) {
      fs.mkdirSync(process.env.TICKET_ACTIONS_SCREENSHOT_DIR, { recursive: true });
      await page.locator('.ticket-control-wrap').evaluate(el => el.scrollTop = el.scrollHeight);
      await page.screenshot({ path: path.join(process.env.TICKET_ACTIONS_SCREENSHOT_DIR, `${role}-${theme}.png`) });
    }
  });
}

async function setup(t, { width, height, theme, pageName, role = 'admin', workflowType = 'ticket', clientAvatar = false, cardPatch = {}, fullName = user.full_name, quickReplies = [] }) {
  const page = await browser.newPage({ viewport: { width, height } });
  t.after(() => page.close());
  const errors = [], requests = [];
  page.on('pageerror', error => errors.push(error.message));
  const me = { ...user, role, full_name: fullName };
  const preferences = { appearance_revision: '0', theme_preference: theme, color_enabled: true, task_panel_enabled: true,
    ticket_list_columns: columns.map(key => ({ key, width: key === 'subject' ? 340 : 160 })) };
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    if (url.hostname !== 'desk.test') return route.abort();
    if (p.startsWith('/static/')) {
      const file = path.resolve('api/app', p.slice(1));
      if (!fs.existsSync(file)) return route.fulfill({ status: 404, body: '' });
      const ext = path.extname(file);
      return route.fulfill({ body: fs.readFileSync(file), contentType: { '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml', '.ttf': 'font/ttf' }[ext] || 'application/octet-stream' });
    }
    if (!p.startsWith('/api/')) {
      const template = p === '/quick-replies' ? 'quick_replies' : p === '/profile/notification-bot' ? 'profile_notification_bot' : p === '/tickets' ? 'tickets' : p.startsWith('/tickets/') ? 'ticket' : 'profile';
      return route.fulfill({ contentType: 'text/html', body: fs.readFileSync(`api/app/templates/${template}.html`, 'utf8') });
    }
    requests.push({ path: p, method: req.method(), query: url.search });
    if (p.endsWith('/appearance-session')) {
      Object.assign(preferences, req.postDataJSON()?.preferences);
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify({
        writer_id: '00000000-0000-4000-8000-000000000001', preferences,
      }) });
    }
    if (p.endsWith('/client-avatar/thumbnail')) return route.fulfill({ contentType: 'image/svg+xml',
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="92" height="92"><rect width="92" height="92" fill="#98b4cd"/><circle cx="46" cy="34" r="17" fill="#304860"/><path d="M15 92V76a31 31 0 0 1 62 0v16" fill="#304860"/></svg>' });
    let result = {};
    if (p.endsWith('/preferences')) {
      if (req.method() === 'PATCH') {
        const { appearance_write, ...patch } = req.postDataJSON();
        Object.assign(preferences, patch);
      }
      result = preferences;
    } else if (p === '/api/v1/me/notification-bot') result = { role, use_base_settings: false, delivery: {}, telegram_binding: { active: true },
      items: [{ type: role === 'admin' ? 'internal_task_completion' : 'new_assignment', title: 'Заявка', enabled: true, template: 'Текст', tag_items: [] }] };
    else if (p === '/api/v1/auth/session') result = { real_user: me, effective_user: me, is_real_admin: role === 'admin', session_login_ts: 1, app_timezone: 'Europe/Moscow' };
    else if (p === '/api/v1/users/me/status' && req.method() === 'PATCH') { Object.assign(me, req.postDataJSON()); result = me; }
    else if (p === '/api/v1/users/me' || p === '/api/v1/users/me/profile') result = me;
    else if (p === '/api/v1/users/agents' || p === '/api/v1/users/by-role') result = [me, { ...me, id: 'agent-2', full_name: 'Елена Александровна Волкова' }];
    else if (p.endsWith('/counts')) result = { active: 28, unassigned: 3, frozen: 4, closed: 158, special: 1,
      by_assignee: { 'agent-1': { active: 123, frozen: 12 }, 'agent-2': { active: 3, frozen: 456 } } };
    else if (p.endsWith('/task-panel')) result = rows.slice(0, 10);
    else if (/tickets\/(active|closed|frozen|special|unassigned)$/.test(p)) result = { items: rows, total: 56, pages: 2, page: Number(url.searchParams.get('page') || 1), limit: 28 };
    else if (p.endsWith('/client-card-view-config')) result = null;
    else if (p.endsWith('/problem-menu')) result = { rows: [{ buttons: [{ id: 'p1', kind: 'submenu', label: 'Почта', children: [{ buttons: [{ id: 'p2', label: 'Доступ к почте', kind: 'final' }] }] }] }] };
    else if (p === '/api/v1/tickets/demo-0') result = { ...card, workflow_type: workflowType,
      client_profile: { ...card.client_profile, telegram_avatar_present: clientAvatar }, ...cardPatch };
    else if (p.endsWith('/client-profile')) result = { ...card.client_profile, telegram_avatar_present: clientAvatar };
    else if (p === '/api/v1/quick-replies/search') result = quickReplies.filter(reply => `${reply.short_title} ${reply.body}`.toLowerCase().includes((url.searchParams.get('q') || '').toLowerCase())).slice(0, 5);
    else if (/\/api\/v1\/quick-replies\/[^/]+\/use$/.test(p)) result = { ...quickReplies.find(reply => p.includes(`/${reply.id}/use`)), saved_files: [] };
    else if (p === '/api/v1/quick-replies') result = quickReplies;
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(result) });
  });
  await page.goto(`http://desk.test/${pageName === 'ticket' ? 'tickets/demo-0' : 'tickets'}`);
  await page.waitForFunction(() => document.body.dataset.sidebarReady === '1');
  await page.waitForSelector(pageName === 'ticket' ? '.message' : '.ticket-row');
  await page.evaluate(() => document.fonts.ready);
  assert.equal(await page.locator('#errorModal').isVisible(), false);
  t.after(() => assert.deepEqual(errors, []));
  return { page, requests, preferences };
}

async function geometry(page) {
  return page.evaluate(() => {
    const box = selector => {
      const el = document.querySelector(selector), r = el.getBoundingClientRect();
      return { x: r.x, y: r.y, width: r.width, height: r.height, bottom: r.bottom };
    };
    return { sidebar: box('.sidebar'), toolbar: box('.toolbar'), content: box('.content'),
      overflow: document.documentElement.scrollWidth > innerWidth,
      font: getComputedStyle(document.querySelector('#appContent')).fontFamily,
      background: getComputedStyle(document.querySelector('.panel')).backgroundColor,
      radius: getComputedStyle(document.querySelector('.panel')).borderRadius };
  });
}

async function modalVisible(page, selector) {
  await page.locator(selector).waitFor({ state: 'visible' });
  const r = await page.locator(`${selector} .modal-dialog`).boundingBox(), vp = page.viewportSize();
  assert.ok(r.x >= 0 && r.y >= 0 && r.x + r.width <= vp.width + 1 && r.y + r.height <= vp.height + 1, JSON.stringify(r));
}

function contrastRatio(foreground, background) {
  const lum = color => color.match(/[\d.]+/g).slice(0, 3).map(Number)
    .map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : Math.pow((v + .055) / 1.055, 2.4))
    .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
  const a = lum(foreground), b = lum(background);
  return (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
}

for (const theme of ['light', 'dark']) {
  test(`client view scrollbar uses V2 tokens: ${theme}`, async t => {
    const { page } = await setup(t, { width: 1366, height: 768, theme, pageName: 'ticket' });
    const scroll = await page.locator('#clientDynamicCard').evaluate(el => {
      el.scrollTop = 25;
      const s = getComputedStyle(el);
      return { top: el.scrollTop, width: s.scrollbarWidth, color: s.scrollbarColor };
    });
    assert.ok(scroll.top > 0, 'the view card itself is the scroll owner');
    assert.equal(scroll.width, 'thin'); assert.notEqual(scroll.color, 'auto');
    assert.equal(await page.locator('#clientModal').isVisible(), false);
  });
  test(`ticket client V2 scrollbars, border and boolean save: ${theme}`, async t => {
    const { page } = await setup(t, { width: 1366, height: 768, theme, pageName: 'ticket' });
    const panelBox = () => page.locator('.ticket-client-side, .ticket-card, .ticket-client-side > .card-head').evaluateAll(nodes => nodes.map(el => {
      const r = el.getBoundingClientRect(); return [r.x, r.y, r.width, r.height];
    }));
    const view = await panelBox();
    await page.route('**/api/v1/tickets/demo-0/client-card-view-config', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ blocks: [{ key: 'checks', title: 'Проверки', is_visible: true, is_visible_in_editor: true, fields: [
      { key: 'is_verified', label: 'Верификация', field_type: 'checkbox', is_system: true, is_visible: true, is_visible_in_editor: true, is_editable: true },
      { key: 'custom_temporary_rop', label: 'Временный РОП', field_type: 'checkbox', is_visible: true, is_visible_in_editor: true, is_editable: true },
      ...Array.from({ length: 6 }, (_, i) => ({ key: `custom_note_${i}`, label: 'Примечание', field_type: 'textarea', is_visible: true, is_visible_in_editor: true, is_editable: true })),
    ] }] }) }));
    // Reload to load the configured system and custom boolean fields.
    await page.reload();
    await page.waitForSelector('.message');
    await page.evaluate(() => document.fonts.ready);
    await page.addStyleTag({ content: '#clientModal * { transition: none !important; }' });
    await page.locator('#editClientBtn').click();
    await page.locator('#clientModal .desk-toggle').first().waitFor();
    assert.deepEqual(await panelBox(), view, 'panel and header geometry is unchanged');
    const details = await page.evaluate(() => {
      const panel = document.querySelector('.ticket-client-side').getBoundingClientRect(), rect = document.querySelector('.client-edit-border rect').getBoundingClientRect();
      const style = getComputedStyle(document.querySelector('.client-edit-border rect'));
      return { insets: [rect.left - panel.left, rect.top - panel.top, panel.right - rect.right, panel.bottom - rect.bottom], rx: style.rx, ry: style.ry,
        scrollbars: ['#messages', '#clientModal .modal-body'].map(selector => { const el = document.querySelector(selector), s = getComputedStyle(el); el.scrollTop = 20; return { width: s.scrollbarWidth, color: s.scrollbarColor, scrolled: el.scrollTop }; }),
        rows: [...document.querySelectorAll('#clientModal .desk-toggle')].map(el => { const a = el.getBoundingClientRect(), b = el.previousElementSibling.getBoundingClientRect(); return { type: el.type, appearance: getComputedStyle(el).appearance, alignment: Math.abs(a.y + a.height / 2 - b.y - b.height / 2) }; }) };
    });
    assert.deepEqual(details.insets, [2, 2, 2, 2]);
    assert.equal(details.rx, '22px'); assert.equal(details.ry, '22px');
    for (const s of details.scrollbars) { assert.equal(s.width, 'thin'); assert.notEqual(s.color, 'auto'); }
    for (const s of details.scrollbars) assert.ok(s.scrolled > 0);
    assert.equal(details.rows.length, 2);
    for (const row of details.rows) { assert.equal(row.type, 'checkbox'); assert.equal(row.appearance, 'none'); assert.ok(row.alignment < 1); }
    const verified = page.locator('#fIsVerified'), custom = page.locator('[data-custom-field-key="custom_temporary_rop"]');
    await verified.uncheck();
    await custom.focus(); await page.keyboard.press('Space');
    assert.equal(await custom.isChecked(), true);
    await page.evaluate(() => document.querySelector('#appContent').style.setProperty('--desk-accent', 'rgb(180, 90, 120)'));
    assert.equal(await custom.evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(180, 90, 120)', 'toggle follows the active accent token');
    await custom.evaluate(el => { el.disabled = true; });
    assert.equal(await custom.evaluate(el => getComputedStyle(el).cursor), 'not-allowed');
    await custom.evaluate(el => { el.disabled = false; });
    let saved;
    await page.route('**/api/v1/tickets/demo-0/client-profile', route => {
      if (route.request().method() === 'PATCH') saved = route.request().postDataJSON();
      if (!saved) return route.fallback();
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ...card.client_profile, ...saved }) });
    });
    await page.locator('#saveClientBtn').click();
    await page.locator('#clientModal').waitFor({ state: 'hidden' });
    assert.equal(saved.is_verified, false); assert.equal(saved.custom_fields.custom_temporary_rop, true);
    assert.deepEqual(await panelBox(), view);
    await page.locator('#editClientBtn').click();
    await page.locator('#fIsVerified').waitFor();
    assert.equal(await verified.isChecked(), false); assert.equal(await custom.isChecked(), true);
    await page.locator('#closeClientModalBtn').click();
  });
  test(`client editor text contrast and unchanged geometry: ${theme}`, async t => {
    const { page } = await setup(t, { width: 1366, height: 768, theme, pageName: 'ticket' });
    await page.locator('#editClientBtn').click();
    await page.locator('#fContactName').waitFor({ state: 'visible' });
    await page.locator('#clientModal .modal-field-section').first().evaluate(section => {
      const probe = document.createElement('div');
      probe.className = 'client-card-field-readonly-rule';
      probe.innerHTML = '<small class="status">Подсказка</small><label>Только чтение<input readonly value="Значение"></label><textarea placeholder="Подсказка ввода"></textarea>';
      section.appendChild(probe);
    });
    const dimensions = () => page.locator('#clientModal, #clientModal .modal-dialog, #clientModal .modal-body, #clientModal label, #clientModal input, #clientModal select, #clientModal textarea').evaluateAll(nodes => nodes.map(el => {
      const { x, y, width, height } = el.getBoundingClientRect(); return { x, y, width, height };
    }));
    const initialGeometry = await dimensions();
    const samples = await page.locator('#clientModal').evaluate(root => {
      function background(el) {
        const layers = [];
        for (let node = el; node; node = node.parentElement) {
          const rgba = getComputedStyle(node).backgroundColor.match(/[\d.]+/g).map(Number);
          layers.push([...rgba.slice(0, 3), rgba[3] ?? 1]);
        }
        return `rgb(${layers.reverse().reduce((bg, [r, g, b, a]) => [r, g, b].map((v, i) => v * a + bg[i] * (1 - a)), [255, 255, 255]).join(', ')})`;
      }
      const nodes = [...root.querySelectorAll('label, .modal-label-title, .custom-field-editor > span, .client-card-block-title, .client-card-field-label, .client-card-field-value, .status, small, input:not([type="checkbox"]), select, textarea, button')]
        .filter(el => el.getClientRects().length && !el.disabled);
      return nodes.flatMap(el => {
        const style = getComputedStyle(el);
        const sample = { node: el.id || el.className || el.tagName, color: style.color, background: background(el) };
        return el.matches('input, textarea') ? [sample, { ...sample, node: `${sample.node}::placeholder`, color: getComputedStyle(el, '::placeholder').color }] : [sample];
      });
    });
    assert.ok(samples.length > 10);
    for (const sample of samples) assert.ok(contrastRatio(sample.color, sample.background) >= 4.5, JSON.stringify(sample));
    await page.evaluate(theme => HelpdeskTheme.applyTheme(theme === 'dark' ? 'light' : 'dark'), theme);
    assert.deepEqual(await dimensions(), initialGeometry);
  });
}

test('appearance write failure is visible and restores the saved choice', async t => {
  const { page } = await setup(t, { width: 1366, height: 768, theme: 'light', pageName: 'tickets' });
  await page.locator('#themeToggleBtn').click();
  const failWrite = route => ['PATCH','POST'].includes(route.request().method())
    ? route.fulfill({ status: 503, contentType: 'application/json', body: '{}' }) : route.fallback();
  await page.route('**/api/v1/users/me/preferences**', failWrite);
  await page.locator('#appearance-accent-hue').fill('350');
  await page.locator('.appearance-status').filter({ hasText: 'Не удалось сохранить' }).waitFor();
  assert.equal(await page.locator('#appearance-accent-hue').inputValue(), '259');
  assert.equal(await page.locator('#appearanceMenu').isVisible(), true);
  await page.unroute('**/api/v1/users/me/preferences**', failWrite);
  await page.locator('#appearance-accent-hue').fill('153');
  await page.waitForFunction(() => !saveInFlight);
  assert.equal(await page.locator('.appearance-status').textContent(), '');
  assert.equal(await page.locator('#appearance-accent-hue').inputValue(), '153');
});

for (const [width, height] of [[1366, 768], [1920, 1080]]) {
  for (const theme of ['light', 'dark']) {
    test(`appearance colors, intensity, contrast and geometry: ${theme}, ${width}`, async t => {
      const { page, preferences } = await setup(t, { width, height, theme, pageName: 'ticket' });
      const before = await geometry(page);
      await page.locator('#themeToggleBtn').click();
      const menu = page.locator('#appearanceMenu');
      await menu.waitFor({ state: 'visible' });
      const menuBox = await menu.boundingBox(), triggerBox = await page.locator('#themeToggleBtn').boundingBox();
      assert.ok(menuBox.y >= triggerBox.y + triggerBox.height);
      assert.ok(menuBox.x >= 0 && menuBox.x + menuBox.width <= width);
      assert.ok(menuBox.y + menuBox.height <= height);
      if (process.env.DESK_V2_SCREENSHOTS) {
        fs.mkdirSync(process.env.DESK_V2_SCREENSHOTS, { recursive: true });
        await page.screenshot({ path: path.join(process.env.DESK_V2_SCREENSHOTS, `appearance-${theme}-${width}.png`) });
      }
      assert.ok(menuBox.width >= 700 && menuBox.width <= 722);
      assert.equal(await menu.locator('[data-appearance-accent]').count(), 0);
      assert.equal(await menu.locator('input[type="range"]').count(), 4);
      assert.equal(await menu.locator('output').count(), 0);
      assert.ok((await page.locator('#appearance-interface-hue').evaluate(el => getComputedStyle(el).backgroundImage)).includes('linear-gradient'));
      const range = page.locator('#appearance-accent-intensity');
      const semanticsBackgrounds = () => page.locator('.ticket-status-badge, .message.internal, .message.agent, .action.danger')
        .evaluateAll(nodes => nodes.map(el => getComputedStyle(el).backgroundColor));
      const savedSemanticBackgrounds = await semanticsBackgrounds();
      for (const hue of [0, 60, 90, 120, 180, 240, 300, 360]) {
        for (const intensity of [0, 50, 100]) {
          await page.locator('#appearance-interface-hue').fill(String(hue));
          await page.locator('#appearance-interface-intensity').fill(String(intensity));
          await page.locator('#appearance-accent-hue').fill(String(hue === 90 ? 60 : 360 - hue));
          await range.fill(String(intensity));
          await page.waitForFunction(() => !saveInFlight);
          assert.equal(preferences[`interface_hue_${theme}`], hue);
          assert.equal(preferences[`accent_hue_${theme}`], hue === 90 ? 60 : 360 - hue);
          assert.equal(preferences[`interface_intensity_${theme}`], intensity);
          assert.equal(preferences[`accent_strength_${theme}`], intensity);
          assert.equal(await menu.isVisible(), true);
          const styles = await page.evaluate(() => {
            const background = el => {
              const layers = [];
              for (let node = el; node; node = node.parentElement) {
                const rgba = getComputedStyle(node).backgroundColor.match(/[\d.]+/g).map(Number);
                layers.push([...rgba.slice(0, 3), rgba[3] ?? 1]);
              }
              return `rgb(${layers.reverse().reduce((bg, [r,g,b,a]) => [r,g,b].map((v,i) => v*a+bg[i]*(1-a)), [255,255,255]).join(', ')})`;
            };
            const pairs = [...document.querySelectorAll('.sidebar .tab-btn.active, .sidebar-user-name, #sendNowBtn, .message, .message .status, .client-card-field-label, .client-card-field-value:not(.verified-ok):not(.verified-no), .client-card-field-value a, .client-card-block-title, .toolbar .action, .appearance-menu label, .appearance-menu legend, [data-appearance-theme]')]
              .filter(el => el.getBoundingClientRect().width).map(el => {
                const s = getComputedStyle(el); return [s.color, background(el), el.className || el.tagName];
              });
            return { pairs, tokens: ['bg','surface','soft','accent','text','border'].map(key => document.body.style.getPropertyValue(`--desk-${key}`)) };
          });
          assert.ok(styles.pairs.length >= 10);
          for (const [fg,bg,node] of styles.pairs) assert.ok(contrastRatio(fg,bg) >= 4.5, `${theme}/${hue}/${intensity}/${node}: ${fg} / ${bg}`);
          if (intensity === 0) for (const token of styles.tokens) {
            const [r,g,b] = token.match(/[\d.]+/g).map(Number);
            assert.equal(r,g); assert.equal(g,b);
          }
          assert.deepEqual(await semanticsBackgrounds(), savedSemanticBackgrounds);
          const after = await geometry(page);
          for (const key of ['sidebar','toolbar','content']) assert.deepEqual(after[key], before[key]);
        }
      }
      await page.locator('#appearance-interface-hue').fill('111');
      await page.locator('#appearance-interface-intensity').fill('23');
      await page.locator('#appearance-accent-hue').fill('222');
      await range.fill('41');
      await page.waitForFunction(() => !saveInFlight);
      const otherTheme = theme === 'light' ? 'dark' : 'light';
      await page.locator(`[data-appearance-theme="${otherTheme}"]`).click();
      assert.equal(await range.inputValue(), '50');
      assert.equal(await page.locator('#appearance-interface-hue').inputValue(), otherTheme === 'dark' ? '225' : '259');
      await range.fill('78');
      await page.waitForFunction(() => !saveInFlight);
      await page.locator(`[data-appearance-theme="${theme}"]`).click();
      assert.equal(await range.inputValue(), '41');
      assert.equal(await page.locator('#appearance-interface-hue').inputValue(), '111');
      assert.equal(await page.locator('#appearance-interface-intensity').inputValue(), '23');
      assert.equal(await page.locator('#appearance-accent-hue').inputValue(), '222');
      await page.keyboard.press('Escape');
      assert.equal(await menu.isVisible(), false);
      assert.equal(await page.locator('#themeToggleBtn').evaluate(el => el === document.activeElement), true);
      await page.keyboard.press('Enter');
      await menu.waitFor({ state: 'visible' });
      await page.keyboard.press('Tab');
      assert.equal(await page.evaluate(() => document.activeElement.dataset.appearanceTheme), 'light');
      await page.keyboard.press('Tab');
      await page.keyboard.press('Space');
      assert.equal(await page.locator('body').getAttribute('data-theme'), 'dark');
      await range.focus();
      await page.keyboard.press('Home');
      assert.equal(await range.inputValue(), '0');
      await page.keyboard.press('End');
      assert.equal(await range.inputValue(), '100');
      assert.equal(await menu.isVisible(), true);
      await page.locator('#themeToggleBtn').click();
      assert.equal(await menu.isVisible(), false);
      await page.locator('#themeToggleBtn').click();
      await page.mouse.click(8, height - 8);
      assert.equal(await menu.isVisible(), false);
      await page.waitForFunction(() => !saveInFlight);
      await page.reload();
      await page.waitForFunction(() => document.body.dataset.sidebarReady === '1');
      await page.locator('#themeToggleBtn').click();
      assert.equal(await page.locator('#appearance-accent-intensity').inputValue(), '100');
    });
  }
}

for (const [width, height] of [[1366, 768], [1920, 1080]]) {
  for (const theme of ['light', 'dark']) {
    for (const pageName of ['ticket', 'tickets']) {
      test(`${pageName}: ${theme}, ${width}px — real assets, scrolling and controls`, async t => {
        const { page, requests } = await setup(t, { width, height, theme, pageName, clientAvatar: pageName === 'ticket' });
        const before = await geometry(page);
        assert.equal(before.overflow, false);
        assert.match(before.font, /Manrope/);
        assert.equal(before.radius, '24px');
        assert.equal(before.background, theme === 'dark' ? 'rgb(32, 37, 53)' : 'rgb(245, 243, 249)');
        assert.ok(before.content.bottom <= height + 1);
        const sidebarChecks = await page.locator('.sidebar').evaluate(sidebar => ({
          width: sidebar.getBoundingClientRect().width,
          overflow: [sidebar, ...sidebar.querySelectorAll('.admin-agent-filter, .admin-agent-list, .admin-agent-item, .admin-agent-name')]
            .some(el => el.scrollWidth > el.clientWidth + 1),
        }));
        assert.ok(sidebarChecks.width >= 246 && sidebarChecks.width <= 420);
        assert.equal(sidebarChecks.overflow, false);
        const indicators = await page.locator('.admin-agent-counts').evaluateAll(nodes => nodes.map(el => ({
          dividers: [...el.querySelectorAll('.admin-agent-count-divider')].map(node => {
            const r = node.getBoundingClientRect(), s = getComputedStyle(node);
            return { x: r.x, y: r.y, height: r.height, width: r.width, color: s.color };
          }),
          centers: [...el.children].map(node => { const r = node.getBoundingClientRect(); return r.y + r.height / 2; }),
          frozenColor: getComputedStyle(el.querySelector('.admin-agent-count-frozen')).color,
          zones: [...el.querySelectorAll('.admin-agent-count')].map(node => ({ width: node.getBoundingClientRect().width, align: getComputedStyle(node).textAlign })),
          gaps: [el.children[1].getBoundingClientRect().left - el.children[0].getBoundingClientRect().right,
            el.children[3].getBoundingClientRect().left - el.children[2].getBoundingClientRect().right],
        })));
        for (const indicator of indicators) {
          assert.equal(indicator.dividers.length, 2);
          assert.ok(indicator.dividers.every(bar => bar.width === 2 && bar.height === 16));
          assert.ok(Math.max(...indicator.centers) - Math.min(...indicator.centers) < 1);
          assert.equal(indicator.dividers[1].color, theme === 'dark' ? 'rgb(142, 203, 255)' : 'rgb(22, 103, 200)');
          assert.equal(indicator.frozenColor, indicator.dividers[1].color);
          assert.deepEqual(indicator.zones, [{ width: 28, align: 'right' }, { width: 28, align: 'left' }]);
          assert.equal(indicator.dividers[1].x - indicator.dividers[0].x, 6);
          assert.deepEqual(indicator.gaps, [4, 4]);
        }
        assert.deepEqual(indicators[0].dividers.map(bar => bar.x), indicators[1].dividers.map(bar => bar.x));
        assert.equal(await page.evaluate(() => document.fonts.check('15px Manrope', 'Заявка переписка')), true);
        const employeeAvatar = await page.locator('.agent-avatar').boundingBox();
        assert.equal(employeeAvatar.width, 88);
        assert.equal(employeeAvatar.height, 88);
        assert.ok(employeeAvatar.y + employeeAvatar.height <= height - 8, JSON.stringify(employeeAvatar));
        const name = page.locator('.sidebar-user-name');
        const originalName = await name.textContent();
        await name.evaluate(el => { el.textContent = 'Александра Константиновна Рождественская'; });
        const nameStyle = await name.evaluate(el => {
          const s = getComputedStyle(el), r = el.getBoundingClientRect(), panel = el.closest('.sidebar-user-panel').getBoundingClientRect();
          return { whitespace: s.whiteSpace, clamp: s.webkitLineClamp, lines: (r.height - parseFloat(s.paddingTop) - parseFloat(s.paddingBottom) - 2) / parseFloat(s.lineHeight),
            right: r.right, panelRight: panel.right };
        });
        assert.equal(nameStyle.whitespace, 'normal');
        assert.equal(nameStyle.clamp, '2');
        assert.ok(Math.abs(nameStyle.lines - 2) < .1);
        assert.ok(nameStyle.right <= nameStyle.panelRight + 1);
        await name.evaluate((el, text) => { el.textContent = text; }, originalName);
        await page.locator('.agent-avatar').click();
        await page.locator('.sidebar-user-menu').waitFor({ state: 'visible' });
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('.sidebar-user-menu').isVisible(), false);
        if (pageName === 'ticket') {
          const avatar = page.locator('.client-card-telegram-avatar');
          await avatar.waitFor({ state: 'visible' });
          assert.deepEqual(await avatar.evaluate(el => {
            const r = el.getBoundingClientRect();
            return { width: r.width, height: r.height, radius: getComputedStyle(el).borderRadius,
              imageRadius: getComputedStyle(el.querySelector('img')).borderRadius, loaded: el.querySelector('img').naturalWidth > 0 };
          }), { width: 92, height: 92, radius: '16px', imageRadius: '16px', loaded: true });
          const density = await page.locator('.ticket-layout').evaluate(el => {
            const s = getComputedStyle(el); return { top: s.paddingTop, side: s.paddingLeft, gap: s.gap };
          });
          assert.deepEqual(density, width === 1366 ? { top: '11px', side: '11px', gap: '8px' } : { top: '14px', side: '16px', gap: '12px' });
          assert.equal(await page.locator('.message').count(), 32);
          assert.equal(await page.locator('.message.client').first().evaluate(el => getComputedStyle(el).fontSize), '18px');
          const internal = page.locator('.message.internal').first();
          assert.equal(await internal.evaluate(el => getComputedStyle(el).borderTopStyle), 'dashed');
          assert.equal(await internal.evaluate(el => getComputedStyle(el, '::before').content), '"Комментарий"');
          const system = await page.locator('.message.system').first().evaluate(el => {
            const s = getComputedStyle(el);
            return { color: s.color, background: s.backgroundColor, border: s.borderTopStyle, font: s.fontSize };
          });
          assert.equal(system.border, 'solid');
          assert.equal(system.font, '15px');
          assert.notEqual(system.background, 'rgba(0, 0, 0, 0)');
          assert.equal(system.color, theme === 'dark' ? 'rgb(237, 234, 245)' : 'rgb(48, 44, 64)');
          assert.equal(await page.locator('.message.agent').first().evaluate(el => getComputedStyle(el).backgroundColor), theme === 'dark' ? 'rgb(200, 175, 246)' : 'rgb(201, 177, 244)');
          const badgeColors = await page.evaluate(() => {
            const wrap = document.createElement('div');
            document.querySelector('#appContent').appendChild(wrap);
            const colors = ['type-ticket', 'type-task', 'status-special', 'status-unassigned', 'status-active', 'status-closed', 'frozen'].map(kind => {
              const badge = document.createElement('span');
              badge.className = `ticket-status-badge ticket-badge-${kind}`;
              wrap.appendChild(badge);
              return getComputedStyle(badge).backgroundColor;
            });
            wrap.remove(); return colors;
          });
          assert.equal(new Set(badgeColors).size, 7);
          assert.equal(badgeColors.at(-1), theme === 'dark' ? 'rgb(42, 53, 78)' : 'rgb(234, 240, 250)');
          assert.ok((await page.locator('.ticket-client-side > .card-head').boundingBox()).height <= 56);
          const headline = await page.locator('.ticket-head-title-wrap').evaluate(el => {
            const title = el.querySelector('.ticket-head-title').getBoundingClientRect();
            const badges = el.querySelector('.ticket-status-badges').getBoundingClientRect();
            return { direction: getComputedStyle(el).flexDirection, titleRight: title.right, badgesLeft: badges.left,
              titleCenter: title.y + title.height / 2, badgesCenter: badges.y + badges.height / 2 };
          });
          assert.equal(headline.direction, 'row');
          assert.ok(headline.badgesLeft >= headline.titleRight);
          assert.ok(Math.abs(headline.titleCenter - headline.badgesCenter) < 1);
          assert.ok((await page.locator('.ticket-card > .ticket-card-wrap > .card-head').boundingBox()).height <= 102);
          assert.equal(await page.locator('.toolbar #instructionBtn').count(), 0);
          assert.equal(await page.locator('.ticket-head-main > #instructionBtn').count(), 1);
          const instruction = await page.locator('#instructionBtn').boundingBox(), title = await page.locator('.ticket-head-title-wrap').boundingBox();
          assert.ok(instruction.x >= title.x + title.width);
          assert.ok(Math.abs(instruction.y + instruction.height / 2 - (title.y + title.height / 2)) < 1);
          assert.equal(await page.locator('.toolbar > #backToListBtn').count(), 1);
          const composer = await page.locator('.card-actions').boundingBox();
          assert.ok(composer.y + composer.height <= height);
          await page.locator('#messages').evaluate(el => { el.scrollTop = 0; });
          assert.equal(await page.locator('#messages').evaluate(el => el.scrollTop), 0);
          assert.deepEqual(await page.locator('.card-actions').boundingBox(), composer);
          for (const selector of ['#clientDynamicCard', '.ticket-control-wrap']) {
            const scrolled = await page.locator(selector).evaluate(el => { el.scrollTop = el.scrollHeight; return el.scrollHeight <= el.clientHeight || el.scrollTop > 0; });
            assert.ok(scrolled, selector);
          }
          await page.locator('#editClientBtn').click();
          await page.locator('#fContactName').waitFor({ state: 'visible' });
          assert.equal(await page.locator('#fContactName').inputValue(), 'Мария');
          await page.locator('#closeClientModalBtn').click();
          await page.locator('#instructionBtn').click();
          await modalVisible(page, '#instructionModal');
          await page.locator('#closeInstructionModalBtn').click();
          await page.locator('#wrongProblemBtn').click();
          await modalVisible(page, '#wrongProblemModal');
          await page.locator('.wrong-problem-item.submenu').click();
          assert.equal(await page.locator('.wrong-problem-children .wrong-problem-item').isVisible(), true);
          await page.locator('#closeWrongProblemModalBtn').click();
          await page.locator('#adminDeleteBtn').click();
          await modalVisible(page, '#adminActionModal');
          await page.locator('#cancelAdminActionBtn').click();
          assert.equal(requests.some(r => r.method === 'DELETE'), false);
        } else {
          assert.equal(await page.locator('th[data-column-key]').count(), 9);
          assert.equal(await page.locator('.ticket-row').count(), 28);
          const selectAll = page.locator('#selectAllTicketsBtn');
          const checkAlignment = await selectAll.evaluate(el => {
            const rect = el.getBoundingClientRect();
            const range = document.createRange();
            range.selectNodeContents(el);
            const glyph = range.getBoundingClientRect();
            return { padding: getComputedStyle(el).padding,
              inside: glyph.left >= rect.left && glyph.right <= rect.right && glyph.top >= rect.top && glyph.bottom <= rect.bottom,
              horizontalOffset: Math.abs((glyph.left + glyph.right - rect.left - rect.right) / 2) };
          });
          assert.equal(checkAlignment.padding, '0px');
          assert.equal(checkAlignment.inside, true);
          assert.ok(checkAlignment.horizontalOffset < 1);
          await selectAll.click();
          assert.equal(await page.locator('.ticket-check:checked').count(), 28);
          await selectAll.click();
          assert.equal(await page.locator('.ticket-check:checked').count(), 0);
          const rowHeights = await page.locator('.ticket-row').evaluateAll(rows => rows.map(row => row.getBoundingClientRect().height));
          assert.ok(rowHeights.every(height => height >= 58 && height <= 62), JSON.stringify(rowHeights));
          assert.equal(await page.locator('.ticket-row td[data-column-key="requester"]').first().evaluate(el => getComputedStyle(el).fontSize), '16px');
          await page.locator('.table-scroll').evaluate(el => { el.scrollLeft = el.scrollWidth; el.scrollTop = el.scrollHeight; });
          assert.ok(await page.locator('.table-scroll').evaluate(el => el.scrollLeft > 0 && el.scrollTop > 0));
          await page.locator('.table-scroll').evaluate(el => { el.scrollLeft = 0; el.scrollTop = 0; });
          await page.locator('#columnsSettingsBtn').click();
          await modalVisible(page, '#columnsModal');
          assert.equal(await page.locator('.columns-draft-row').count(), 9);
          await page.locator('#closeColumnsModalBtn').click();
          await page.locator('.ticket-check').first().check();
          await page.locator('#bulkAssignBtn').click();
          await modalVisible(page, '#assignModal');
          await page.locator('#closeAssignModalBtn').click();
          await page.locator('.ticket-check').first().uncheck();
          await page.locator('.table-sort-btn[data-sort-key="subject"]').click();
          assert.equal(await page.evaluate(() => localStorage.getItem('hd_table_sort_key')), 'subject');
          await page.locator('.table-sort-btn[data-sort-key="subject"]').click();
          assert.equal(await page.evaluate(() => localStorage.getItem('hd_table_sort_dir')), 'desc');
          await page.locator('#colorEnabledInput').uncheck();
          assert.equal(await page.locator('tr.ticket-row').first().evaluate(el => getComputedStyle(el).backgroundImage), 'none');
          await page.locator('#colorEnabledInput').check();
          await page.locator('#nextPageBtn').click();
          await page.waitForURL(/page=2/);
          await page.locator('#searchInput').fill('почта');
          await page.locator('#searchInput').press('Enter');
          await page.waitForURL(/q=/);
          assert.ok(requests.some(r => /query=/.test(r.query)));
        }
        // Theme geometry must be identical, including header wrapping.
        await page.locator('#themeToggleBtn').click();
        await page.locator(`[data-appearance-theme="${theme === 'light' ? 'dark' : 'light'}"]`).click();
        await page.waitForFunction(theme => document.body.dataset.theme !== theme, theme);
        const after = await geometry(page);
        for (const key of ['sidebar', 'toolbar', 'content']) assert.deepEqual(after[key], before[key]);
        await page.locator(`[data-appearance-theme="${theme}"]`).click();
        await page.locator('#themeToggleBtn').click();
        await page.waitForFunction(theme => document.body.dataset.theme === theme, theme);
        if (process.env.DESK_V2_SCREENSHOTS) {
          await page.evaluate(() => {
            for (const selector of ['.sidebar', '#clientDynamicCard', '.ticket-control-wrap', '#messages', '.table-scroll']) {
              const el = document.querySelector(selector);
              if (el) { el.scrollTop = 0; el.scrollLeft = 0; }
            }
          });
          fs.mkdirSync(process.env.DESK_V2_SCREENSHOTS, { recursive: true });
          await page.screenshot({ path: path.join(process.env.DESK_V2_SCREENSHOTS, `${pageName}-${theme}-${width}.png`) });
          if (pageName === 'ticket') {
            await page.locator('#messages').evaluate(el => {
              el.scrollTop += el.querySelector('.message.internal').getBoundingClientRect().top - el.getBoundingClientRect().top - 16;
            });
            await page.screenshot({ path: path.join(process.env.DESK_V2_SCREENSHOTS, `messages-${theme}-${width}.png`) });
          }
        }
      });
    }
  }
}

for (const width of [1366, 1920]) {
  for (const theme of ['light', 'dark']) {
    for (const pageName of ['ticket', 'tickets']) {
      test(`sidebar adapts only to a visible agent block: ${pageName}, ${theme}, ${width}`, async t => {
        const { page } = await setup(t, { width, height: 900, theme, pageName, role: 'agent' });
        assert.equal((await page.locator('.sidebar').boundingBox()).width, 246);
        const dimensions = () => page.locator('.sidebar').evaluate(sidebar => ({
          width: sidebar.getBoundingClientRect().width,
          overflow: [sidebar, ...sidebar.querySelectorAll('#adminAgentFilter, .admin-agent-list, .admin-agent-item, .admin-agent-name')]
            .some(el => el.scrollWidth > el.clientWidth + 1),
        }));
        // Simulate the same DOM block that Sidebar renders after a role/session refresh.
        await page.evaluate(() => {
          const filter = document.querySelector('#adminAgentFilter');
          filter.classList.remove('hidden');
          const list = filter.querySelector('.admin-agent-list');
          const item = document.createElement('button');
          item.className = 'admin-agent-item';
          item.innerHTML = '<span class="admin-agent-name">Александр Иванов</span><span class="admin-agent-counts"><span class="admin-agent-count admin-agent-count-active">123</span><span class="admin-agent-count-divider">|</span><span class="admin-agent-count-divider admin-agent-count-divider-frozen">|</span><span class="admin-agent-count admin-agent-count-frozen">12</span></span>';
          list.replaceChildren(item);
        });
        const short = await dimensions();
        await page.locator('.admin-agent-name').evaluate(el => { el.textContent = 'Александра Константиновна Рождественская'; });
        const long = await dimensions();
        assert.ok(long.width > short.width, `${JSON.stringify(short)} → ${JSON.stringify(long)}`);
        assert.ok(long.width <= 420);
        assert.equal(long.overflow, false);
        const counters = await page.locator('.admin-agent-counts > *').evaluateAll(nodes => nodes.every(el => el.scrollWidth <= el.clientWidth + 1));
        assert.equal(counters, true);
        await page.locator('.admin-agent-name').evaluate(el => { el.textContent = 'ОченьДлинноеСоставноеИмяБезПробелов'.repeat(3); });
        assert.equal((await dimensions()).overflow, false);
        await page.locator('#adminAgentFilter').evaluate(el => el.classList.add('hidden'));
        assert.equal((await dimensions()).width, 246);
        assert.equal((await geometry(page)).overflow, false);
      });
    }
  }
}

test('AppShell list → card → list preserves shell and new stylesheet', async t => {
  const { page } = await setup(t, { width: 1366, height: 768, theme: 'dark', pageName: 'tickets' });
  await page.locator('#themeToggleBtn').click();
  await page.locator('#appearance-accent-hue').fill('184');
  await page.locator('#appearance-accent-intensity').fill('73');
  await page.waitForFunction(() => !saveInFlight);
  await page.keyboard.press('Escape');
  const accentBeforeNavigation = await page.evaluate(() => document.body.style.getPropertyValue('--desk-accent'));
  await page.evaluate(() => { window.originalSidebar = document.querySelector('.sidebar'); });
  await page.locator('.ticket-row-link').first().click();
  await page.waitForSelector('.message');
  assert.equal(await page.evaluate(() => document.body.style.getPropertyValue('--desk-accent')), accentBeforeNavigation);
  await page.locator('#themeToggleBtn').click();
  assert.equal(await page.locator('#appearanceMenu').count(), 1);
  assert.equal(await page.locator('#appearance-accent-intensity').inputValue(), '73');
  await page.keyboard.press('Escape');
  assert.equal(await page.evaluate(() => window.originalSidebar === document.querySelector('.sidebar')), true);
  assert.equal((await geometry(page)).radius, '24px');
  await page.locator('#backToListBtn').click();
  await page.waitForSelector('.ticket-row');
  assert.equal(await page.evaluate(() => document.body.style.getPropertyValue('--desk-accent')), accentBeforeNavigation);
  await page.evaluate(() => { HelpdeskTheme.initThemeToggle(); HelpdeskTheme.initThemeToggle(); });
  assert.equal(await page.locator('#appearanceMenu').count(), 1);
  assert.equal(await page.evaluate(() => window.originalSidebar === document.querySelector('.sidebar')), true);
  assert.equal((await geometry(page)).radius, '24px');
});

for (const width of [1366, 1920]) {
  for (const theme of ['light', 'dark']) {
    test(`task close button keeps ready and disabled states: ${theme}, ${width}`, async t => {
      const { page } = await setup(t, { width, height: width === 1366 ? 768 : 1080, theme, pageName: 'ticket', workflowType: 'task' });
      const close = page.locator('#closeBtn');
      assert.equal(await close.textContent(), 'Закрыть задачу');
      assert.equal(await close.isEnabled(), true);
      assert.equal(await close.evaluate(el => el.classList.contains('close-action-ready')), true);
      const ready = await close.evaluate(el => {
        const s = getComputedStyle(el);
        return { font: s.fontSize, height: el.getBoundingClientRect().height, padding: s.padding, opacity: s.opacity, background: s.backgroundColor };
      });
      assert.equal(ready.font, '18px');
      assert.ok(ready.height >= 60);
      assert.equal(ready.padding, '14px 18px');
      assert.equal(ready.opacity, '1');
      await close.scrollIntoViewIfNeeded();
      if (process.env.DESK_V2_SCREENSHOTS) await page.screenshot({ path: path.join(process.env.DESK_V2_SCREENSHOTS, `task-${theme}-${width}.png`) });
      // Exercise the CSS state without issuing a close request to the API.
      await close.evaluate(el => { el.disabled = true; el.classList.remove('close-action-ready'); });
      await page.waitForFunction(background => getComputedStyle(document.querySelector('#closeBtn')).backgroundColor === background,
        theme === 'dark' ? 'rgb(38, 43, 61)' : 'rgb(240, 238, 246)');
      const disabled = await close.evaluate(el => ({ opacity: getComputedStyle(el).opacity, background: getComputedStyle(el).backgroundColor }));
      assert.equal(await close.isEnabled(), false);
      assert.equal(disabled.opacity, '0.45');
      assert.notEqual(disabled.background, ready.background);
    });
  }
}

for (const width of [1024, 390]) {
  for (const pageName of ['ticket', 'tickets']) {
    test(`responsive ${pageName}: ${width}px retains accessible content`, async t => {
      const { page } = await setup(t, { width, height: 844, theme: 'dark', pageName, role: 'agent' });
      assert.equal((await geometry(page)).overflow, false);
      if (pageName === 'ticket') {
        await page.locator('#ticketMessageBody').scrollIntoViewIfNeeded();
        assert.ok(await page.locator('#ticketMessageBody').isVisible());
        await page.locator('#editClientBtn').click();
        await page.locator('#fContactName').waitFor({ state: 'visible' });
      } else {
        await page.locator('#columnsSettingsBtn').click();
        await modalVisible(page, '#columnsModal');
      }
    });
  }
}


for (const width of [1366, 1920]) for (const theme of ['light', 'dark']) {
  test(`client and agent bubbles fit two thirds: ${theme}, ${width}`, async t => {
    const { page } = await setup(t, { width, height: 1080, theme, pageName: 'ticket' });
    const metrics = await page.evaluate(() => {
      const messages = document.querySelector('#messages');
      const style = getComputedStyle(messages);
      const available = messages.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
      const result = {};
      for (const kind of ['client', 'agent']) {
        const original = messages.querySelector(`.message.${kind}`);
        result[kind] = {};
        for (const [sample, text] of [['short', 'Ок'], ['long', 'Проверяем доступ к рабочей почте и настройки подключения. '.repeat(30)], ['continuous', 'НепрерывнаяСтрока'.repeat(100)]]) {
          const bubble = original.cloneNode(true);
          bubble.querySelector('.message-header strong').textContent = kind === 'client' ? 'Клиент' : 'Агент';
          bubble.querySelector('.message-date').textContent = '12:00';
          bubble.querySelector('.message-body').textContent = text;
          messages.appendChild(bubble);
          const rect = bubble.getBoundingClientRect(), computed = getComputedStyle(bubble);
          result[kind][sample] = { width: rect.width, overflow: bubble.scrollWidth > bubble.clientWidth + 1,
            align: computed.alignSelf, color: computed.backgroundColor, maxWidth: computed.maxWidth };
          bubble.remove();
        }
      }
      return { available, result };
    });
    for (const kind of ['client', 'agent']) {
      const samples = metrics.result[kind];
      for (const item of Object.values(samples)) {
        assert.equal(item.maxWidth, '66.67%');
        assert.ok(item.width <= metrics.available * .6667 + 1);
        assert.equal(item.overflow, false);
        assert.equal(item.align, kind === 'client' ? 'flex-start' : 'flex-end');
        assert.equal(item.color, samples.short.color);
      }
      assert.ok(samples.short.width < samples.long.width);
      assert.ok(Math.abs(samples.long.width - metrics.available * .6667) < 1);
      assert.ok(Math.abs(samples.continuous.width - metrics.available * .6667) < 1);
    }
  });
}


for (const width of [1366, 1920]) for (const theme of ['light', 'dark']) {
  test(`sidebar counter geometry for 0, 1, 12, 595: ${theme}, ${width}`, async t => {
    const { page } = await setup(t, { width, height: 1080, theme, pageName: 'tickets' });
    const metrics = await page.evaluate(() => {
      const glyph = el => {
        const range = document.createRange(); range.selectNodeContents(el);
        return range.getBoundingClientRect();
      };
      const samples = [];
      for (const value of ['0', '1', '12', '595']) {
        for (const counts of document.querySelectorAll('.sidebar .admin-agent-counts')) {
          const [left, barOne, barTwo, right] = counts.children;
          left.textContent = right.textContent = value;
          const l = left.getBoundingClientRect(), r = right.getBoundingClientRect();
          const a = barOne.getBoundingClientRect(), b = barTwo.getBoundingClientRect();
          samples.push({ kind: 'agent', value, leftWidth: l.width, rightWidth: r.width,
            leftGap: a.left - glyph(left).right, rightGap: glyph(right).left - b.right,
            barWidth: [a.width, b.width], barHeight: [a.height, b.height], barGap: b.left - a.right,
            numeric: getComputedStyle(counts).fontVariantNumeric,
            overflow: counts.scrollWidth > counts.clientWidth + 1 });
        }
        for (const badge of document.querySelectorAll('.sidebar .tab-btn-count')) {
          badge.textContent = value;
          const box = badge.getBoundingClientRect(), text = glyph(badge);
          samples.push({ kind: 'category', value, tab: badge.dataset.tabCount,
            width: box.width, height: box.height,
            dx: (text.left + text.right - box.left - box.right) / 2,
            dy: (text.top + text.bottom - box.top - box.bottom) / 2,
            radius: getComputedStyle(badge).borderRadius,
            overflow: badge.scrollWidth > badge.clientWidth + 1 });
        }
      }
      return { samples, overflow: [...document.querySelectorAll('.sidebar, .admin-agent-list, .admin-agent-item')]
        .some(el => el.scrollWidth > el.clientWidth + 1) };
    });
    assert.equal(metrics.overflow, false);
    assert.ok(metrics.samples.some(sample => sample.tab === 'frozen'));
    for (const sample of metrics.samples) {
      assert.equal(sample.overflow, false, JSON.stringify(sample));
      if (sample.kind === 'agent') {
        assert.equal(sample.leftWidth, 28);
        assert.equal(sample.rightWidth, 28);
        assert.ok(Math.abs(sample.leftGap - 4) < .1, JSON.stringify(sample));
        assert.ok(Math.abs(sample.rightGap - 4) < .1, JSON.stringify(sample));
        assert.deepEqual(sample.barWidth, [2, 2]);
        assert.deepEqual(sample.barHeight, [16, 16]);
        assert.equal(sample.barGap, 4);
        assert.equal(sample.numeric, 'tabular-nums');
      } else {
        assert.ok(Math.abs(sample.dx) <= .5, JSON.stringify(sample));
        assert.ok(Math.abs(sample.dy) <= 1, JSON.stringify(sample));
        assert.equal(sample.height, 24);
        assert.equal(sample.radius, '999px');
        if (sample.value === '595') assert.ok(sample.width > sample.height);
      }
    }
  });
}


for (const theme of ['light','dark']) test(`appearance narrow window and keyboard hue: ${theme}`, async t => {
  const {page} = await setup(t, {width:390, height:844, theme, pageName:'tickets'});
  await page.locator('#themeToggleBtn').click();
  const menu = page.locator('#appearanceMenu'), rect = await menu.boundingBox();
  assert.ok(rect.x >= 0 && rect.x+rect.width <= 390);
  assert.ok(rect.y >= 0 && rect.y+rect.height <= 844);
  assert.equal(await menu.evaluate(el => el.scrollWidth <= el.clientWidth), true);
  assert.equal(await menu.locator('.appearance-controls').evaluate(el => getComputedStyle(el).gridTemplateColumns.split(' ').length), 1);
  const hue = page.locator('#appearance-interface-hue');
  await hue.focus(); await page.keyboard.press('Home');
  assert.equal(await hue.inputValue(),'0');
  await page.keyboard.press('End');
  assert.equal(await hue.inputValue(),'360');
  await page.waitForFunction(() => !saveInFlight);
  assert.equal(await menu.isVisible(),true);
  await page.keyboard.press('Escape');
  assert.equal(await menu.isVisible(),false);
});


for (const theme of ['light','dark']) test(`main hue reaches large surfaces without changing accents or geometry: ${theme}`, async t => {
  const {page} = await setup(t, {width:1366,height:768,theme,pageName:'ticket'});
  const before = await geometry(page);
  await page.locator('#themeToggleBtn').click();
  const stable = () => page.evaluate(() => ({
    accent: ['accent','primary','on-accent'].map(key => document.body.style.getPropertyValue(`--desk-${key}`)),
    semantic: ['danger','success','warning-text','blue-text'].map(key => getComputedStyle(document.body).getPropertyValue(`--desk-${key}`)),
  }));
  // Keep a non-default accent to exercise surface and accent controls independently.
  await page.locator('#appearance-accent-hue').fill('215');
  await page.waitForFunction(() => !saveInFlight);
  const fixed = await stable();
  for (const hue of [0,120,240,300]) for (const intensity of [0,50,100]) {
    await page.locator('#appearance-interface-hue').fill(String(hue));
    await page.locator('#appearance-interface-intensity').fill(String(intensity));
    await page.waitForFunction(() => !saveInFlight);
    const colors = await page.evaluate(() => {
      const body = getComputedStyle(document.body);
      const rgb = value => value.match(/[\d.]+/g).slice(0,3).map(Number);
      return {
        surfaces: ['.sidebar','.toolbar','.panel'].map(selector => rgb(getComputedStyle(document.querySelector(selector)).backgroundColor)),
        tokens: ['bg','surface','soft','backdrop'].map(key => rgb(body.getPropertyValue(`--desk-${key}`))),
        text: body.getPropertyValue('--desk-text'), muted: body.getPropertyValue('--desk-muted'),
      };
    });
    assert.deepEqual(colors.surfaces[0],colors.tokens[1]);
    assert.deepEqual(colors.surfaces[1],colors.tokens[1]);
    assert.deepEqual(colors.surfaces[2],colors.tokens[1]);
    assert.equal(new Set(colors.tokens.map(rgb=>rgb.join(','))).size,4);
    if (intensity === 0) for (const rgb of colors.tokens) assert.equal(Math.max(...rgb)-Math.min(...rgb),0);
    if (theme === 'light' && intensity > 0) for (const rgb of colors.tokens) {
      assert.ok(Math.max(...rgb)-Math.min(...rgb) >= (intensity === 50 ? 6 : 12), `${hue}/${intensity}: ${rgb}`);
      const [r,g,b]=rgb, max=Math.max(...rgb), min=Math.min(...rgb), d=max-min;
      const actual = (max===r ? ((g-b)/d+6)%6 : max===g ? (b-r)/d+2 : (r-g)/d+4)*60;
      assert.ok(Math.min(Math.abs(actual-hue),360-Math.abs(actual-hue)) <= 8, `${hue}: ${actual}`);
    }
    for (const rgb of colors.tokens) for (const text of [colors.text,colors.muted]) assert.ok(contrastRatio(text,`rgb(${rgb})`) >= 4.5);
    assert.deepEqual(await stable(),fixed);
    const after = await geometry(page);
    for (const key of ['sidebar','toolbar','content']) assert.deepEqual(after[key],before[key]);
    // Hue in the other theme must not alter any tokens of the visible theme.
    const current = await stable();
    await page.evaluate(other => { appearance[`interface_hue_${other}`] = 71; applyTheme(appearance.theme_preference); }, theme==='light'?'dark':'light');
    assert.deepEqual(await stable(),current);
  }
});


for (const width of [1366,1920]) for (const theme of ['light','dark']) {
  test(`list density, column reorder and resize overlay: ${theme}, ${width}`, async t => {
    const {page,requests} = await setup(t,{width,height:width===1366?768:1080,theme,pageName:'tickets'});
    const layout = await page.evaluate(() => {
      const content=document.querySelector('#appContent > .content'), style=getComputedStyle(content);
      const panel=document.querySelector('.table-wrap').getBoundingClientRect(), outer=content.getBoundingClientRect();
      return {padding:style.padding,gap:style.gap,inset:panel.x-outer.x,tableHeight:panel.height,
        taskMargin:getComputedStyle(document.querySelector('#taskPanel')).margin,
        overflow:document.documentElement.scrollWidth > innerWidth};
    });
    assert.equal(layout.padding,'14px 16px 16px');
    assert.equal(layout.gap,'12px');
    assert.equal(layout.inset,16);
    assert.equal(layout.taskMargin,'8px 16px 0px');
    assert.ok(layout.tableHeight>400); assert.equal(layout.overflow,false);
    const table = page.locator('.table-scroll');
    await table.evaluate(el => {el.scrollTop=el.scrollHeight;el.scrollLeft=el.scrollWidth;});
    assert.ok(await table.evaluate(el=>el.scrollTop>0 && el.scrollLeft>0));
    await table.evaluate(el=>{el.scrollTop=0;el.scrollLeft=0;});
    await page.locator('#columnsSettingsBtn').click();
    const modal=page.locator('#columnsModal');
    await modalVisible(page,'#columnsModal');
    const overlay=()=>modal.evaluate(el=>{const s=getComputedStyle(el);return {blur:s.backdropFilter,background:s.backgroundColor,pointer:s.pointerEvents};});
    const normal=await overlay();
    assert.equal(normal.blur,'blur(4px)');
    const keys=()=>page.locator('.columns-draft-row').evaluateAll(rows=>rows.map(el=>el.dataset.columnKey));
    const original=await keys();
    await page.locator('.columns-draft-row').nth(1).dragTo(page.locator('.columns-draft-row').nth(2));
    const reordered=await keys();
    assert.notDeepEqual(reordered,original);
    assert.equal(await modal.locator('.columns-dialog').isVisible(),true);
    assert.deepEqual(await overlay(),normal);
    await page.locator('#editColumnsWidthBtn').click();
    const resize=await overlay();
    assert.ok(parseFloat(resize.blur.match(/[\d.]+/)[0])<=.75,resize.blur);
    assert.ok(Number(resize.background.match(/[\d.]+/g)[3])<=.2,resize.background);
    assert.equal(resize.pointer,'none');
    assert.equal(await modal.locator('.columns-dialog').isVisible(),false);
    const heading=page.locator('th[data-column-key="subject"]');
    const before=await heading.boundingBox();
    const handle=await heading.locator('.column-resize-handle').boundingBox();
    assert.ok(handle);
    if (process.env.DESK_V2_SCREENSHOTS) {
      fs.mkdirSync(process.env.DESK_V2_SCREENSHOTS,{recursive:true});
      await page.screenshot({path:path.join(process.env.DESK_V2_SCREENSHOTS,`columns-resize-${theme}-${width}.png`)});
    }
    await page.mouse.move(handle.x+handle.width/2,handle.y+handle.height/2);
    await page.mouse.down();await page.mouse.move(handle.x+handle.width/2+54,handle.y+handle.height/2,{steps:6});await page.mouse.up();
    assert.ok((await heading.boundingBox()).width>=before.width+40);
    assert.ok(await page.locator('.ticket-row td[data-column-key="subject"]').first().isVisible());
    await page.locator('#saveResizeColumnsBtn').click();
    assert.deepEqual(await overlay(),normal);
    assert.equal(await modal.locator('.columns-dialog').isVisible(),true);
    await page.locator('#closeColumnsModalBtn').click();
    assert.equal(await modal.isVisible(),false);
    assert.deepEqual(await page.locator('th[data-column-key]').evaluateAll(nodes=>nodes.map(el=>el.dataset.columnKey)),original);
    assert.equal(requests.some(r=>r.method==='PATCH'),false);
  });
}


for (const theme of ['light', 'dark']) for (const role of ['agent', 'admin']) for (const height of [768, 1000]) {
  test(`sidebar stays still across notification AppShell navigation: ${theme}, ${role}, ${height}`, async t => {
    const { page } = await setup(t, { width: 1440, height, theme, role, pageName: 'tickets' });
    await page.addStyleTag({ content: '.sidebar * { transition: none !important; animation: none !important; }' });
    const measure = () => page.evaluate(() => ({
      windowScroll: scrollY,
      documentScroll: document.scrollingElement.scrollTop,
      elements: [...document.querySelectorAll('.sidebar, .sidebar *')].filter(el => el.getBoundingClientRect().height).map(el => {
        const r = el.getBoundingClientRect(), s = getComputedStyle(el);
        return { tag: el.tagName, id: el.id, x: r.x, y: r.y, width: r.width, height: r.height,
          padding: s.padding, margin: s.margin, font: s.font, scrollTop: el.scrollTop, scrollHeight: el.scrollHeight, clientHeight: el.clientHeight };
      }),
    }));
    const before = await measure();
    await page.evaluate(() => { window.sidebarBeforeNavigation = document.querySelector('.sidebar'); });
    await page.locator('[data-sidebar-user-menu-toggle]').click();
    await page.locator('[data-sidebar-user-menu-item="bot-notifications"]').click();
    await page.waitForSelector('[data-template-textarea]');
    await page.mouse.move(0, 0);
    assert.equal(await page.evaluate(() => document.querySelector('.sidebar') === window.sidebarBeforeNavigation), true, 'AppShell preserves the sidebar node');
    assert.deepEqual(await measure(), before, 'all sidebar coordinates, type metrics and scroll positions remain stable');
    await page.locator('.tab-btn[href="/tickets?tab=active"]').click();
    await page.waitForSelector('.ticket-row');
    await page.mouse.move(0, 0);
    assert.deepEqual(await measure(), before, 'return navigation has no drift');
    assert.equal(before.elements[0].width === 246, role === 'agent', 'admin intrinsic width and agent fixed width are preserved');
    // A stored sidebar scroll position is also preserved when navigation itself does not focus a link.
    await page.evaluate(() => { document.querySelector('.sidebar').scrollTop = 8; });
    const scrolled = await measure();
    await page.evaluate(() => window.HelpdeskAppShell.navigate('/profile/notification-bot'));
    await page.waitForSelector('[data-template-textarea]');
    assert.deepEqual(await measure(), scrolled);
    await page.evaluate(() => window.HelpdeskAppShell.navigate('/tickets?tab=active'));
    await page.waitForSelector('.ticket-row');
    assert.deepEqual(await measure(), scrolled);
  });
}
