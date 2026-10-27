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
      assert.ok(assets.includes('/static/styles.css?v=20261027-dialog-prompts-v4'));
      assert.ok(assets.includes('/static/desk-v2.css?v=20261027-dialog-prompts-v4'));
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
