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

for (const theme of ['light', 'dark']) test(`accordion and local controls without save effects: ${theme}`, async t => {
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
  assert.equal(await page.locator('.pattern-parameters').isVisible(), true);
  assert.deepEqual(await background(), before);
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
  assert.match(await page.locator('.pattern-code').textContent(), /Код рисунка \d+/);
  await section(page).locator('summary').click();
  assert.equal(await section(page).getAttribute('open'), null);
  await section(page).locator('summary').focus(); await page.keyboard.press('Enter');
  assert.equal(await page.locator('.pattern-card:visible').count(), 6);
  const painted = await background();
  await section(page).locator('summary').click();
  assert.deepEqual(await background(), painted);
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
  assert.equal(await selected.inputValue(), 'grid');
  assert.equal(await page.locator('#patternScale').inputValue(), '100');
  assert.equal(await page.locator('#patternIntensity').isDisabled(), false);
  await page.locator('[data-appearance-theme="light"]').click();
  await page.waitForFunction(() => !saveInFlight);
  assert.equal(await selected.inputValue(), 'waves');
  assert.equal(await page.locator('#patternScale').inputValue(), '140');
  assert.equal(await page.locator('#patternIntensity').isDisabled(), true);
  const seed = await page.locator('.pattern-code span').textContent();
  await page.locator('[data-appearance-theme="dark"]').click();
  await page.waitForFunction(() => !saveInFlight);
  await page.locator('#appearance-accent-hue').fill('120');
  await page.waitForFunction(() => !saveInFlight);
  const writes = calls.filter(call => call.method === 'PATCH').map(call => {
    const { appearance_write, ...patch } = call.body; return patch;
  });
  assert.deepEqual(writes, [{ theme_preference: 'dark' }, { theme_preference: 'light' }, { theme_preference: 'dark' }, { accent_hue_dark: 120 }]);
  await page.locator('[data-appearance-theme="light"]').click();
  await page.waitForFunction(() => !saveInFlight);
  assert.equal(await page.locator('.pattern-code span').textContent(), seed);
  assert.equal(await page.locator('#appearancePatternStyles').count(), 1);
  await page.reload();
  assert.equal(await section(page).getAttribute('open'), null);
  assert.equal(await page.locator('input[name="appearance-pattern"]:checked').inputValue(), 'grid');
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
  assert.deepEqual([...new Set(references)], ['20261028-pattern-render-v1']);
  assert.match(fs.readFileSync('api/app/static/theme.js', 'utf8'), /appearance-pattern\.css\?v=20261028-pattern-render-v1/);
});

for (const [width, height] of [[1440,1000], [390,600], [640,360]]) for (const theme of ['light', 'dark']) test(`real pattern paint, scale, intensity and stable per-pattern seeds: ${theme} ${width}`, async t => {
  const { page, calls } = await setup(t, theme, { width, height });
  const paint = () => page.locator('#appContent').evaluate(el => {
    const s = getComputedStyle(el); return [s.backgroundImage, s.backgroundSize, s.backgroundPosition, s.backgroundAttachment];
  });
  const original = await paint();
  assert.match(original[1], /42px 42px/);
  await section(page).locator('summary').click();
  assert.deepEqual(await paint(), original);
  await section(page).locator('summary').click();
  assert.deepEqual(await paint(), original);
  await section(page).locator('summary').click();
  const images = new Set(), seeds = [];
  const pixels = new Set();
  if (width === 1440) await page.evaluate(() => {
    const probe = document.createElement('div'); probe.id = 'patternProbe'; probe.className = 'panel';
    probe.style.cssText = 'position:fixed;left:300px;top:650px;width:300px;height:250px;border:0;border-radius:0;box-shadow:none;background-color:var(--desk-surface);z-index:10';
    document.querySelector('#appContent').appendChild(probe);
  });
  for (const name of ['Сетка', 'Точки', 'Диагонали', 'Волны', 'Рельеф']) {
    await select(page, name);
    images.add((await paint())[0]);
    const alignment = await page.locator('#appContent, .layout > .sidebar, .layout > .main > .toolbar').evaluateAll(nodes => nodes.map(el => {
      const s = getComputedStyle(el); return [s.backgroundImage, s.backgroundSize, s.backgroundPosition, s.backgroundAttachment];
    }));
    // Uniform grid stroke colors deliberately retain each surface's existing base.
    assert.ok(alignment.every(s => s[1] === alignment[0][1] && s[2] === alignment[0][2] && s[3] === alignment[0][3]));
    if (name !== 'Сетка') assert.ok(alignment.every(s => s[0] === alignment[0][0]));
    await page.locator('#patternScale').fill('150');
    assert.match((await paint())[1], name === 'Сетка' ? /63px/ : /1260px/);
    await page.locator('#patternIntensity').fill('0');
    const invisible = (await paint())[0];
    await page.locator('#patternIntensity').fill('100');
    assert.notEqual((await paint())[0], invisible);
    await page.locator('#patternVariable').check();
    const seed = await page.locator('.pattern-code span').textContent();
    seeds.push(seed);
    const variable = await paint();
    assert.match(decodeURIComponent(variable[0]), /feTurbulence/);
    await select(page, 'Без узора');
    assert.equal((await paint())[0], 'none');
    await select(page, name);
    assert.equal(await page.locator('.pattern-code span').textContent(), seed);
    assert.deepEqual(await paint(), variable);
    if (width === 1440) {
      const first = await page.locator('#patternProbe').screenshot();
      pixels.add(first.toString('base64'));
      await select(page, 'Без узора');
      const blank = await page.locator('#patternProbe').screenshot();
      assert.ok(!blank.equals(first), 'pattern paints real pixels');
      await select(page, name);
      assert.ok(first.equals(await page.locator('#patternProbe').screenshot()), 'seed reproduces identical pixels');
      if (process.env.PATTERN_SCREENSHOT_DIR) await page.locator('#patternProbe').screenshot({ path: path.join(process.env.PATTERN_SCREENSHOT_DIR, `paint-${theme}-${await page.locator('input[name="appearance-pattern"]:checked').inputValue()}.png`) });
    }
    await page.locator('#patternVariable').uncheck();
    await page.locator('#patternScale').fill('100');
    await page.locator('#patternIntensity').fill('50');
  }
  assert.equal(images.size, 5);
  assert.equal(seeds.length, 5);
  assert.ok(seeds.every(seed => Number(seed) > 0));
  if (width === 1440) assert.equal(pixels.size, 5);
  assert.equal(calls.filter(c => c.method !== 'GET').length, 0);
  assert.deepEqual(await page.evaluate(() => [Object.keys(localStorage), Object.keys(sessionStorage)]), [[], []]);
  if (process.env.PATTERN_SCREENSHOT_DIR && width === 1440) {
    await page.locator('#patternProbe').evaluate(el => el.remove());
    await page.locator('#themeToggleBtn').click();
    await page.screenshot({ path: path.join(process.env.PATTERN_SCREENSHOT_DIR, `pattern-workspace-${theme}.png`) });
  }
});
