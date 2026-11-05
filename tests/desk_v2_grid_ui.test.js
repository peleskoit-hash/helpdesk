// Real templates and palette code; synthetic preferences keep local data untouched.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });
const variants = ['tickets', 'ticket', 'reviews', 'profile', 'profile_notification_bot', 'settings_notification_bot_basic', 'settings', 'management'];
async function setup(t, template, theme) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  t.after(() => page.close());
  const errors = [], assets = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const url = new URL(route.request().url()), p = url.pathname;
    if (url.hostname !== 'desk.test') return route.abort();
    if (p.startsWith('/static/')) {
      assets.push(p + url.search);
      const file = path.resolve('api/app', p.slice(1));
      return route.fulfill({ status: fs.existsSync(file) ? 200 : 404, body: fs.existsSync(file) ? fs.readFileSync(file) : '',
        contentType: { '.css': 'text/css', '.js': 'text/javascript', '.ttf': 'font/ttf', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream' });
    }
    if (p.startsWith('/api/')) return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ theme_preference: theme }) });
    const html = fs.readFileSync(`api/app/templates/${template}.html`, 'utf8')
      .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, match => match.includes('/static/theme.js?') ? match : '');
    return route.fulfill({ contentType: 'text/html', body: html });
  });
  await page.goto('http://desk.test/' + template);
  await page.waitForFunction(theme => document.body.dataset.theme === theme, theme);
  await page.evaluate(() => {
    document.body.dataset.sidebarReady = '1';
    const main = document.querySelector('#appContent');
    // A blank workspace surface, plus a nested surface, permits exact pixel checks
    // without sampling text. Both use the real shared .panel rule.
    for (const [id, left] of [['gridParent', 500], ['gridSibling', 760]]) {
      const el = document.createElement('div'); el.id = id; el.className = 'panel';
      el.style.cssText = `position:fixed;left:${left}px;top:300px;width:210px;height:126px;padding:0;border:0;border-radius:0;box-shadow:none;backdrop-filter:none;z-index:10;background-color:var(--desk-surface)`;
      main.appendChild(el);
    }
    const child = document.createElement('div'); child.id = 'gridChild'; child.className = 'panel';
    child.style.cssText = 'position:absolute;left:84px;top:0;width:126px;height:126px;border:0;border-radius:0;box-shadow:none;background-color:transparent;backdrop-filter:none';
    document.querySelector('#gridParent').appendChild(child);
  });
  t.after(() => assert.deepEqual(errors, []));
  return { page, assets };
}
async function pixels(page) {
  const screenshot = (await page.screenshot()).toString('base64');
  return page.evaluate(async screenshot => {
    const image = new Image(); image.src = 'data:image/png;base64,' + screenshot; await image.decode();
    const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
    const ctx = canvas.getContext('2d'); ctx.drawImage(image, 0, 0);
    return [[546, 350], [547, 350], [588, 350], [798, 350], [546, 336]].map(([x, y]) => [...ctx.getImageData(x, y, 1, 1).data].slice(0, 3));
  }, screenshot);
}
function contrast(a, b) {
  const l = rgb => rgb.map(v => { v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; })
    .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
  const x = l(a), y = l(b); return (Math.max(x, y) + .05) / (Math.min(x, y) + .05);
}
for (const theme of ['light', 'dark']) {
  for (const template of variants) {
    test(`one background grid: ${template}, ${theme}`, async t => {
      const { page, assets } = await setup(t, template, theme);
      const selectors = ['#appContent', '.sidebar', '.toolbar', '#appContent .panel'];
      if (template === 'ticket') selectors.push('#messages');
      const surfaces = await page.evaluate(selectors => selectors.map(selector => {
        const s = getComputedStyle(document.querySelector(selector));
        return { selector, image: s.backgroundImage, size: s.backgroundSize, attachment: s.backgroundAttachment };
      }), selectors);
      for (const s of surfaces) {
        assert.equal((s.image.match(/linear-gradient/g) || []).length, 2, JSON.stringify(s));
        assert.equal(s.size, '42px 42px, 42px 42px');
        assert.equal(s.attachment, 'fixed, fixed');
      }
      assert.equal(await page.evaluate(() => getComputedStyle(document.body, '::before').content), 'none');
      const geometry = () => page.locator('.sidebar, .toolbar, #appContent, #appContent .panel').evaluateAll(nodes => nodes.map(el => {
        const r = el.getBoundingClientRect(), s = getComputedStyle(el);
        return [r.x, r.y, r.width, r.height, s.color, s.font, s.padding];
      }));
      const before = await geometry();
      const disable = await page.addStyleTag({ content: 'body:has(.layout > .main) :is(#appContent.main, #appContent.main .panel, #appContent.main .messages, .layout > .sidebar, .layout > .main > .toolbar) {background-image:none !important}' });
      assert.deepEqual(await geometry(), before, 'grid does not change geometry, type or text color');
      await disable.evaluate(el => el.remove());
      const button = page.locator('#themeToggleBtn');
      await button.click();
      assert.equal(await page.locator('#appearanceMenu').isVisible(), true);
      await page.keyboard.press('Escape');
      const [line, base, nested, sibling, crossing] = await pixels(page);
      const ratio = contrast(line, base);
      assert.ok(ratio >= 1.03 && ratio <= 1.25, `${theme}: ${JSON.stringify({line, base, ratio})}`);
      assert.deepEqual(line, nested, 'nested grids never add opacity');
      assert.deepEqual(line, sibling, 'sibling surfaces share grid phase');
      assert.ok(line.every((value, i) => Math.abs(value - crossing[i]) <= 1), 'crossing strokes match within compositor rounding');
      if (template === 'ticket') assert.equal(await page.locator('#messages').evaluate(el => getComputedStyle(el).getPropertyValue('--desk-grid-base').trim() === getComputedStyle(el).getPropertyValue('--desk-soft').trim()), true);
      assert.ok(assets.includes('/static/styles.css?v=20261106-system-editors-v2'));
      assert.ok(assets.includes('/static/desk-v2.css?v=20261106-system-editors-v2'));
    });
  }
  test(`grid contrast is stable across appearance hue and intensity: ${theme}`, async t => {
    const { page } = await setup(t, 'tickets', theme);
    const ratios = [];
    for (const hue of [0, 60, 180, 259]) for (const intensity of [0, 50, 100]) {
      await page.evaluate(({ theme, hue, intensity }) => {
        appearance = { ...APPEARANCE_DEFAULTS, theme_preference: theme,
          [`interface_hue_${theme}`]: hue, [`interface_intensity_${theme}`]: intensity,
          [`accent_hue_${theme}`]: (hue + 120) % 360, [`accent_strength_${theme}`]: intensity };
        window.HelpdeskTheme.applyTheme(theme);
      }, { theme, hue, intensity });
      const [line, base] = await pixels(page);
      ratios.push(contrast(line, base));
    }
    assert.ok(Math.min(...ratios) >= 1.03 && Math.max(...ratios) <= 1.25, JSON.stringify(ratios));
    assert.ok(Math.max(...ratios) - Math.min(...ratios) < .06, JSON.stringify(ratios));
  });
}

for (const theme of ['light', 'dark']) test(`V2 menu backdrop matches tickets: ${theme}`, async t => {
  const backdrop = page => page.evaluate(() => {
    const s = getComputedStyle(document.body);
    return [s.backgroundImage, s.backgroundColor, s.backgroundAttachment];
  });
  const { page: reference } = await setup(t, 'tickets', theme);
  const expected = await backdrop(reference);
  for (const template of ['settings', 'statuses', 'management_tasks', 'management_users', 'settings_users', 'settings_automatic_messages']) {
    const { page } = await setup(t, template, theme);
    assert.deepEqual(await backdrop(page), expected, template);
  }
});

for (const theme of ['light', 'dark']) test(`continuous chrome corner keeps page and table geometry: ${theme}`, async t => {
  for (const template of ['tickets', 'ticket', 'settings', 'profile', 'reviews', 'settings_automatic_messages']) {
    const { page } = await setup(t, template, theme);
    if (template === 'tickets') await page.locator('#ticketsHeaderRow').evaluate(el => {
      el.innerHTML = '<th>Канал</th><th>Номер</th><th>Тема</th>';
      document.querySelector('#ticketsTableBody').innerHTML = '<tr><td>Telegram</td><td>42</td><td>Заявка</td></tr>';
    });
    for (const width of [1440, 1024]) {
      await page.setViewportSize({width, height: 1000});
      const geometry = () => page.locator('.layout, .sidebar, #appContent, .toolbar, .content, .panel, .reviews-page, th, td').evaluateAll(els => els.map(el => {
        const r = el.getBoundingClientRect(), s = getComputedStyle(el);
        return [r.x, r.y, r.width, r.height, s.padding, s.margin, s.font];
      }));
      const before = await geometry();
      const shape = await page.locator('#appContent').evaluate(el => {
        const s = getComputedStyle(el, '::before');
        return {content: s.content, radius: s.borderTopLeftRadius, others: [s.borderTopRightRadius, s.borderBottomLeftRadius, s.borderBottomRightRadius],
          borders: [s.borderTopWidth, s.borderLeftWidth, s.borderRightWidth, s.borderBottomWidth], gridRow: s.gridRow, animation: s.animationName, transition: s.transitionDuration,
          side: getComputedStyle(document.querySelector('.sidebar')).borderRightColor,
          header: getComputedStyle(el.querySelector('.toolbar')).borderBottomColor};
      });
      assert.equal(shape.content, '""', template); assert.equal(shape.radius, '24px');
      assert.deepEqual(shape.others, ['24px', '24px', '0px']);
      assert.deepEqual(shape.borders, ['1px', '1px', '1px', '1px']);
      assert.equal(shape.gridRow, '2 / -1'); assert.equal(shape.animation, 'none'); assert.equal(shape.transition, '0s');
      assert.equal(shape.side, 'rgba(0, 0, 0, 0)'); assert.equal(shape.header, 'rgba(0, 0, 0, 0)');
      const edge = await page.locator('#appContent').evaluate(el => ({
        x: el.getBoundingClientRect().left, y: el.querySelector('.toolbar').getBoundingClientRect().bottom + 40,
        color: getComputedStyle(el, '::before').borderLeftColor.match(/\d+/g).slice(0, 3).map(Number),
      }));
      const pixel = (await page.screenshot({clip: {x: edge.x, y: edge.y, width: 1, height: 1}})).toString('base64');
      assert.deepEqual(await page.evaluate(async png => {
        const image = new Image(); image.src = 'data:image/png;base64,' + png; await image.decode();
        const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
        const ctx = canvas.getContext('2d'); ctx.drawImage(image, 0, 0);
        return [...ctx.getImageData(0, 0, 1, 1).data].slice(0, 3);
      }, pixel), edge.color, `${template}: the vertical contour is visible even with overflow:hidden`);
      if (template === 'tickets') {
        assert.equal(await page.locator('th').first().evaluate(el => getComputedStyle(el).borderBottomColor), 'rgba(0, 0, 0, 0)');
        assert.equal(await page.locator('thead').evaluate(el => getComputedStyle(el, '::after').height), '1px');
      }
      const original = await page.addStyleTag({content: `
        body:has(.layout > .main) #appContent.main {position:static; isolation:auto; background-color:transparent;}
        body:has(.layout > .main) #appContent.main::before {content:none;}
        body:has(.layout > .main) .layout > .sidebar {border-right-color:var(--desk-border);}
        body:has(.layout > .main) .layout > .main > .toolbar {border-bottom-color:var(--desk-border);}
        body #appContent.main[data-shell-page="tickets-list"] th {border-bottom-color:var(--desk-border);}
      `});
      assert.deepEqual(await geometry(), before, `${template}/${width}: no geometry, spacing or font change`);
      await original.evaluate(el => el.remove());
    }
    await page.setViewportSize({width: 700, height: 1000});
    assert.equal(await page.locator('#appContent').evaluate(el => getComputedStyle(el, '::before').content), 'none');
  }
});

for (const theme of ['light', 'dark']) test(`new chrome corners follow viewport height on every page: ${theme}`, async t => {
  const templates = fs.readdirSync('api/app/templates').filter(name => name.endsWith('.html') &&
    /id="appContent"/.test(fs.readFileSync(`api/app/templates/${name}`, 'utf8')) &&
    /class="sidebar"/.test(fs.readFileSync(`api/app/templates/${name}`, 'utf8')));
  for (const file of templates) {
    const { page } = await setup(t, file.replace('.html', ''), theme);
    for (const height of [768, 1000]) {
      await page.setViewportSize({width: 1440, height});
      const geometry = () => page.locator('.layout, .sidebar, #appContent, .toolbar, .content, .panel, .reviews-page').evaluateAll(els => els.map(el => {
        const r = el.getBoundingClientRect(), s = getComputedStyle(el);
        return [r.x, r.y, r.width, r.height, s.padding, s.margin, s.font, el.scrollTop, el.scrollHeight, el.clientHeight];
      }));
      const before = await geometry();
      const edge = await page.locator('#appContent').evaluate(el => {
        const r = el.getBoundingClientRect(), top = el.querySelector('.toolbar').getBoundingClientRect().bottom - 1;
        const s = getComputedStyle(el, '::before');
        const probe = document.createElement('span'); probe.style.color = 'var(--desk-surface)'; el.appendChild(probe);
        const surface = getComputedStyle(probe).color; probe.remove();
        return {x: r.x, right: r.right, bottom: r.bottom, top,
          border: s.borderLeftColor, surface, radii: [s.borderTopLeftRadius, s.borderTopRightRadius, s.borderBottomLeftRadius, s.borderBottomRightRadius]};
      });
      assert.deepEqual(edge.radii, ['24px', '24px', '24px', '0px'], file);
      const png = (await page.screenshot()).toString('base64');
      const colors = await page.evaluate(async ({png, edge}) => {
        const image = new Image(); image.src = 'data:image/png;base64,' + png; await image.decode();
        const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
        const ctx = canvas.getContext('2d'); ctx.drawImage(image, 0, 0);
        return [[edge.right - 1, edge.top + 2], [edge.x, edge.bottom - 1],
          [edge.right - 1, edge.top + 40], [edge.x + 40, edge.bottom - 1]].map(([x, y]) =>
            [...ctx.getImageData(Math.floor(x), Math.floor(y), 1, 1).data].slice(0, 3));
      }, {png, edge});
      const rgb = color => color.match(/\d+/g).slice(0, 3).map(Number);
      const distance = (a, b) => Math.max(...a.map((v, i) => Math.abs(v - b[i])));
      // Keep the existing faint background grid visible through the cutouts.
      assert.ok(colors.slice(0, 2).every(color => distance(color, rgb(edge.surface)) < distance(color, rgb(edge.border))), `${file}/${height}: no straight border through new cutouts`);
      assert.ok(colors.slice(2).every(color => distance(color, rgb(edge.border)) < distance(color, rgb(edge.surface))), `${file}/${height}: continuous right and bottom contour`);
      const accepted = await page.addStyleTag({content: `body:has(.layout > .main) #appContent.main::before {
        border-width:1px 0 0 1px; border-top-right-radius:0; border-bottom-left-radius:0;
      }`});
      assert.deepEqual(await geometry(), before, `${file}/${height}: original geometry and scrolling`);
      await accepted.evaluate(el => el.remove());
    }
    await page.close();
  }
});
