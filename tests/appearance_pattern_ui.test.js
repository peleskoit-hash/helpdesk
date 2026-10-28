// Real menu, V2 styles and palette code; preferences are isolated from the local DB.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });

async function setup(t, theme = 'light', viewport = { width: 1440, height: 1000 }) {
  const page = await browser.newPage({ viewport });
  t.after(() => page.close());
  const calls = [], errors = [];
  const preferences = { theme_preference: theme, appearance_revision: '0' };
  page.on('pageerror', error => errors.push(error.message));
  page.on('requestfailed', request => errors.push(request.url()));
  t.after(() => assert.deepEqual(errors, []));
  await page.route('http://desk.test/**', route => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve('api/app', url.pathname.slice(1));
      return route.fulfill({ body: fs.readFileSync(file), contentType: { '.js': 'text/javascript', '.css': 'text/css', '.ttf': 'font/ttf', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream' });
    }
    if (url.pathname.startsWith('/api/')) {
      calls.push({ method: request.method(), path: url.pathname, body: request.postData() ? request.postDataJSON() : null });
      if (url.pathname.endsWith('/appearance-session')) return route.fulfill({ json: { writer_id: '00000000-0000-4000-8000-000000000001' } });
      if (request.method() === 'PATCH') {
        const { appearance_write, ...patch } = request.postDataJSON();
        Object.assign(preferences, patch);
      }
      return route.fulfill({ json: preferences });
    }
    return route.fulfill({ contentType: 'text/html', body: fs.readFileSync('api/app/templates/settings.html', 'utf8')
      .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, script => script.includes('/static/theme.js?') ? script : '') });
  });
  await page.goto('http://desk.test/settings');
  await page.waitForFunction(theme => document.body.dataset.theme === theme, theme);
  await page.waitForFunction(() => [...document.styleSheets].some(sheet => sheet.href?.includes('/appearance-pattern.css')));
  await page.locator('#themeToggleBtn').click();
  await page.waitForFunction(() => document.getElementById('appearanceMenu').matches(':popover-open'));
  return { page, calls };
}
const section = page => page.locator('.appearance-pattern');
const select = (page, name) => page.locator('.pattern-card').filter({ hasText: name }).click();

for (const theme of ['light', 'dark']) test(`local accordion, cards and dependent controls without background/save effects: ${theme}`, async t => {
  const { page, calls } = await setup(t, theme);
  assert.equal(await section(page).getAttribute('open'), null);
  assert.equal(await page.locator('.pattern-card:visible').count(), 0);
  const initialWidth = (await page.locator('#appearanceMenu').boundingBox()).width;
  const background = () => page.evaluate(() => ({ style: document.body.getAttribute('style'),
    layers: ['body', '#appContent'].map(selector => ['::before','::after'].map(pseudo => {
      const s = getComputedStyle(document.querySelector(selector), pseudo); return [s.content,s.backgroundImage,s.backgroundColor,s.opacity];
    })) }));
  const before = await background(), initialCalls = calls.length;
  await section(page).locator('summary').click();
  assert.equal(await page.locator('.pattern-card:visible').count(), 6);
  assert.deepEqual(await page.locator('.pattern-card > span:last-child').allTextContents(), ['Без узора', 'Сетка', 'Точки', 'Диагонали', 'Волны', 'Рельеф']);
  assert.equal(await page.locator('.pattern-parameters').isVisible(), false);
  assert.equal((await page.locator('#appearanceMenu').boundingBox()).width, initialWidth);
  for (const name of ['Сетка', 'Точки', 'Диагонали', 'Волны', 'Рельеф']) {
    await select(page, name);
    assert.equal(await page.locator('.pattern-parameters').isVisible(), true);
  }
  await page.locator('#patternIntensity').fill('73');
  await page.locator('#patternScale').fill('160');
  await page.locator('#patternVariable').check();
  assert.equal(await page.locator('#patternIntensity').isDisabled(), true);
  assert.equal(await page.locator('#patternScale').isEnabled(), true);
  await page.locator('#patternVariable').uncheck();
  assert.equal(await page.locator('#patternIntensity').inputValue(), '73');
  await page.locator('#patternVariable').check();
  await select(page, 'Без узора');
  assert.equal(await page.locator('.pattern-parameters').isVisible(), false);
  await select(page, 'Точки');
  assert.equal(await page.locator('#patternIntensity').isDisabled(), true);
  assert.equal(await page.locator('#patternScale').inputValue(), '160');
  assert.equal(await page.locator('.pattern-code').textContent(), 'Код рисунка —');
  await section(page).locator('summary').click();
  assert.equal(await section(page).getAttribute('open'), null);
  await section(page).locator('summary').focus(); await page.keyboard.press('Enter');
  assert.equal(await page.locator('.pattern-card:visible').count(), 6);
  assert.deepEqual(await background(), before);
  await page.evaluate(() => dispatchEvent(new Event('pagehide')));
  assert.equal(calls.length, initialCalls);
  assert.equal(await page.getByText('Пересчитать', { exact: true }).count(), 0);
  assert.equal(await section(page).evaluate(el => getComputedStyle(el).animationName), 'none');
});

test('theme/color saves remain unchanged; local pattern selections survive theme changes', async t => {
  const { page, calls } = await setup(t);
  await section(page).locator('summary').click(); await select(page, 'Волны');
  await page.locator('#patternVariable').check(); await page.locator('#patternScale').fill('140');
  await page.locator('[data-appearance-theme="dark"]').click();
  await page.waitForFunction(() => document.body.dataset.theme === 'dark' && !saveInFlight);
  const selected = page.locator('input[name="appearance-pattern"]:checked');
  assert.equal(await selected.inputValue(), 'waves');
  assert.equal(await page.locator('#patternScale').inputValue(), '140');
  assert.equal(await page.locator('#patternIntensity').isDisabled(), true);
  await page.locator('#appearance-accent-hue').fill('120');
  await page.waitForFunction(() => !saveInFlight);
  const writes = calls.filter(call => call.method === 'PATCH').map(call => {
    const { appearance_write, ...patch } = call.body; return patch;
  });
  assert.deepEqual(writes, [{ theme_preference: 'dark' }, { accent_hue_dark: 120 }]);
  assert.equal(await page.locator('#appearancePatternStyles').count(), 1);
  await page.reload();
  assert.equal(await section(page).getAttribute('open'), null);
  assert.equal(await page.locator('input[name="appearance-pattern"]:checked').inputValue(), 'none');
});

for (const [width, height] of [[390,600], [640,360]]) for (const theme of ['light', 'dark']) test(`small viewport ${width}x${height} scrolls within the unchanged menu width: ${theme}`, async t => {
  const { page } = await setup(t, theme, { width, height });
  const menu = page.locator('#appearanceMenu');
  const initialWidth = (await menu.boundingBox()).width;
  await section(page).locator('summary').click(); await select(page, 'Рельеф');
  const metrics = await menu.evaluate(el => ({ client: el.clientHeight, scroll: el.scrollHeight, width: el.clientWidth, scrollWidth: el.scrollWidth }));
  assert.ok(metrics.scroll > metrics.client);
  assert.equal(metrics.width, metrics.scrollWidth);
  assert.equal((await menu.boundingBox()).width, initialWidth);
  await page.locator('.pattern-code').scrollIntoViewIfNeeded();
  const rect = await page.locator('.pattern-code').boundingBox();
  assert.ok(rect.y >= 0 && rect.y + rect.height <= height);
  assert.ok(await menu.evaluate(el => el.scrollTop > 0));
  const box = await menu.boundingBox(); assert.ok(box.x >= 0 && box.x + box.width <= width && box.y + box.height <= height);
});

test('visual smoke of the pattern section', { skip: !process.env.PATTERN_SCREENSHOT_DIR }, async t => {
  for (const theme of ['light', 'dark']) {
    const { page } = await setup(t, theme);
    await section(page).locator('summary').click(); await select(page, 'Сетка');
    await page.locator('#patternVariable').check();
    await page.locator('#appearanceMenu').screenshot({ path: path.join(process.env.PATTERN_SCREENSHOT_DIR, `appearance-pattern-${theme}.png`) });
  }
});

test('theme and pattern CSS entry points are versioned consistently', () => {
  const pages = fs.readdirSync('api/app/templates').filter(file => file.endsWith('.html'));
  const references = pages.flatMap(file => [...fs.readFileSync(`api/app/templates/${file}`, 'utf8').matchAll(/\/static\/theme\.js\?v=([^"']+)/g)].map(match => match[1]));
  assert.ok(references.length > 0);
  assert.deepEqual([...new Set(references)], ['20261028-pattern-section-v1']);
  assert.match(fs.readFileSync('api/app/static/theme.js', 'utf8'), /appearance-pattern\.css\?v=20261028-pattern-section-v1/);
});
