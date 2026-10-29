// Real menu, V2 styles and palette code; preferences are isolated from the local DB.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });

function preferencesServer(theme = 'light', background_patterns = null) {
  const preferences = { theme_preference: theme, appearance_revision: '0', background_patterns };
  const writers = new Map();
  let writerCount = 0, seed = 1000;
  function apply(patch) {
    const { background_patterns, ...colors } = patch;
    Object.assign(preferences, colors);
    if (background_patterns) {
      preferences.background_patterns ??= {};
      for (const [theme, changes] of Object.entries(background_patterns)) {
        const target = preferences.background_patterns[theme] ??= { selected: 'grid', patterns: {} };
        if (changes.selected) target.selected = changes.selected;
        const kinds = new Set(Object.keys(changes.patterns || {}));
        if (target.selected !== 'none') kinds.add(target.selected);
        for (const kind of kinds) {
          target.patterns[kind] ??= { scale: 100, intensity: 50, variable: false, seed: ++seed };
          Object.assign(target.patterns[kind], changes.patterns?.[kind]);
        }
      }
    }
    preferences.appearance_revision = String(Number(preferences.appearance_revision) + 1);
  }
  const server = { preferences, writers, apply, intercept: null, async respond(route) {
    if (server.intercept && await server.intercept(route)) return;
    const request = route.request();
    if (new URL(request.url()).pathname.endsWith('/appearance-session')) {
      const { preferences: initial, expected_revision, sequence } = request.postDataJSON();
      const accepted = expected_revision === preferences.appearance_revision;
      const writer_id = `00000000-0000-4000-8000-${String(++writerCount).padStart(12, '0')}`;
      if (accepted) apply(initial);
      else preferences.appearance_revision = String(Number(preferences.appearance_revision) + 1);
      writers.set(writer_id, accepted ? sequence : 0);
      return route.fulfill({ json: { writer_id, preferences } });
    }
    if (request.method() === 'PATCH') {
      const { appearance_write: write, ...patch } = request.postDataJSON();
      if (!writers.has(write.writer_id)) return route.fulfill({ status: 409, json: { detail: {
        error: { code: 'appearance_session_expired' }, preferences,
      } } });
      if (write.sequence > writers.get(write.writer_id)) {
        apply(patch);
        writers.set(write.writer_id, write.sequence);
      }
    }
    return route.fulfill({ json: preferences });
  } };
  return server;
}

async function setup(t, theme = 'light', viewport = { width: 1440, height: 1000 }, server = preferencesServer(theme), waitForLoad = true) {
  const page = await browser.newPage({ viewport });
  t.after(() => page.close());
  const calls = [], errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('requestfailed', request => errors.push(request.url()));
  t.after(() => assert.deepEqual(errors, []));
  await page.route('http://desk.test/**', route => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve('api/app', url.pathname.slice(1));
      return route.fulfill({ body: fs.readFileSync(file), contentType: { '.js': 'text/javascript', '.css': 'text/css', '.ttf': 'font/ttf', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream' });
    }
    if (url.pathname === '/api/v1/background-contours') return route.fulfill({ json: { items: [] } });
    if (url.pathname.startsWith('/api/')) {
      calls.push({ method: request.method(), path: url.pathname, body: request.postData() ? request.postDataJSON() : null });
      return server.respond(route);
    }
    return route.fulfill({ contentType: 'text/html', body: fs.readFileSync('api/app/templates/settings.html', 'utf8')
      .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, script => script.includes('/static/theme.js?') ? script : '') });
  });
  await page.goto('http://desk.test/settings');
  if (waitForLoad) await page.waitForFunction(() => confirmedAppearance.appearance_revision !== null);
  await page.waitForFunction(() => [...document.styleSheets].some(sheet => sheet.href?.includes('/appearance-pattern.css')));
  await page.locator('#themeToggleBtn').click();
  await page.waitForFunction(() => document.getElementById('appearanceMenu').matches(':popover-open'));
  return { page, calls, server, errors };
}
const section = page => page.locator('.appearance-pattern');
const idle = page => page.waitForFunction(() => !saveInFlight && !Object.keys(pendingAppearance).length);
const select = async (page, name) => { await page.locator('.pattern-card').filter({ hasText: name }).click(); await idle(page); };

test('loads server settings and seeds without writes, and preserves the legacy grid for an absent theme', async t => {
  const patterns = { dark: { selected: 'diagonals', patterns: {
    diagonals: { scale: 175, intensity: 83, variable: true, seed: 65535 },
    dots: { scale: 65, intensity: 21, variable: false, seed: 1 },
  } } };
  const { page, calls, server } = await setup(t, 'dark', undefined, preferencesServer('dark', structuredClone(patterns)));
  assert.equal(await page.locator('input[name="appearance-pattern"]:checked').inputValue(), 'diagonals');
  assert.equal(await page.locator('#patternScale').inputValue(), '175');
  assert.equal(await page.locator('#patternIntensity').inputValue(), '83');
  assert.equal(await page.locator('#patternVariable').isChecked(), true);
  assert.equal(await page.locator('.pattern-code span').textContent(), '65535');
  assert.equal(calls.filter(c => c.method !== 'GET').length, 0);
  await section(page).locator('summary').click();
  await select(page, 'Точки');
  assert.equal(await page.locator('#patternScale').inputValue(), '65');
  assert.equal(await page.locator('#patternIntensity').inputValue(), '21');
  assert.equal(await page.locator('#patternVariable').isChecked(), false);
  await select(page, 'Диагонали');
  assert.equal(await page.locator('.pattern-code span').textContent(), '65535');
  await page.locator('[data-appearance-theme="light"]').click(); await idle(page);
  assert.equal(await page.locator('body').getAttribute('data-pattern-custom'), null);
  assert.match(await page.locator('#appContent').evaluate(el => getComputedStyle(el).backgroundSize), /42px 42px/);
  assert.equal(server.preferences.background_patterns.light, undefined);
  assert.deepEqual(server.preferences.background_patterns.dark, patterns.dark);
});

test('minimal patches keep each pattern and theme parameters and server seeds through reload and navigation', async t => {
  const { page, calls, server } = await setup(t);
  await section(page).locator('summary').click();
  await select(page, 'Точки');
  await page.locator('#patternScale').fill('125'); await idle(page);
  await page.locator('#patternIntensity').fill('73'); await idle(page);
  await page.locator('#patternVariable').check(); await idle(page);
  const seed = await page.locator('.pattern-code span').textContent();
  assert.equal(seed, String(server.preferences.background_patterns.light.patterns.dots.seed));
  await select(page, 'Волны');
  await page.locator('#patternScale').fill('175'); await idle(page);
  await page.locator('#patternIntensity').fill('21'); await idle(page);
  await page.locator('[data-appearance-theme="dark"]').click(); await idle(page);
  assert.equal(await page.locator('body').getAttribute('data-pattern-custom'), null);
  await select(page, 'Точки');
  await page.locator('#patternScale').fill('65'); await idle(page);
  await page.locator('#patternIntensity').fill('89'); await idle(page);
  await page.locator('#patternVariable').check(); await idle(page);
  const darkSeed = await page.locator('.pattern-code span').textContent();
  assert.notEqual(darkSeed, seed);
  await page.locator('[data-appearance-theme="light"]').click(); await idle(page);
  assert.equal(await page.locator('#patternScale').inputValue(), '175');
  assert.equal(await page.locator('#patternIntensity').inputValue(), '21');
  assert.equal(await page.locator('#patternVariable').isChecked(), false);
  await select(page, 'Точки');
  assert.equal(await page.locator('#patternScale').inputValue(), '125');
  assert.equal(await page.locator('#patternIntensity').inputValue(), '73');
  assert.equal(await page.locator('#patternVariable').isChecked(), true);
  assert.equal(await page.locator('.pattern-code span').textContent(), seed);
  const patches = calls.filter(c => c.method === 'PATCH').map(c => { const { appearance_write, ...patch } = c.body; return patch; });
  assert.deepEqual(patches.slice(0, 4), [
    { background_patterns: { light: { selected: 'dots' } } },
    { background_patterns: { light: { patterns: { dots: { scale: 125 } } } } },
    { background_patterns: { light: { patterns: { dots: { intensity: 73 } } } } },
    { background_patterns: { light: { patterns: { dots: { variable: true } } } } },
  ]);
  assert.ok(calls.filter(c => c.method !== 'GET').every(c => !JSON.stringify(c.body).includes('"seed"')));
  const saved = structuredClone(server.preferences.background_patterns);
  for (const navigate of [() => page.reload(), () => page.goto('http://desk.test/profile')]) {
    await navigate(); await page.waitForFunction(() => confirmedAppearance.appearance_revision !== null);
    assert.equal(await page.locator('input[name="appearance-pattern"]:checked').inputValue(), 'dots');
    assert.equal(await page.locator('#patternScale').inputValue(), '125');
    assert.equal(await page.locator('.pattern-code span').textContent(), seed);
    assert.deepEqual(server.preferences.background_patterns, saved);
  }
});

test('each new menu opening collapses only the accordion, including Escape and outside click', async t => {
  const { page, calls, server } = await setup(t);
  await section(page).locator('summary').click(); await select(page, 'Рельеф');
  await page.locator('#patternVariable').check(); await idle(page);
  const saved = structuredClone(server.preferences.background_patterns), count = calls.length;
  const seed = await page.locator('.pattern-code span').textContent();
  for (const close of [() => page.locator('#themeToggleBtn').click(), () => page.keyboard.press('Escape'), () => page.mouse.click(10, 990)]) {
    await close();
    await page.waitForFunction(() => !appearanceMenu.matches(':popover-open'));
    await page.locator('#themeToggleBtn').click();
    await page.waitForFunction(() => appearanceMenu.matches(':popover-open'));
    assert.equal(await section(page).getAttribute('open'), null);
    await section(page).locator('summary').click();
    assert.equal(await page.locator('input[name="appearance-pattern"]:checked').inputValue(), 'relief');
    assert.equal(await page.locator('.pattern-code span').textContent(), seed);
  }
  assert.equal(calls.length, count);
  assert.deepEqual(server.preferences.background_patterns, saved);
});

for (const pagehide of [false, true]) test(`nested queued edits preserve themes and inactive patterns${pagehide ? ' with reversed pagehide writes' : ''}`, async t => {
  const { page, calls, server } = await setup(t);
  await section(page).locator('summary').click(); await select(page, 'Волны');
  let release, started;
  const held = new Promise(resolve => { started = resolve; });
  const gate = new Promise(resolve => { release = resolve; });
  let hold = true;
  server.intercept = async route => {
    if (route.request().method() === 'PATCH' && hold) { hold = false; started(); await gate; }
    return false;
  };
  await page.locator('#patternScale').fill('160'); await held;
  try {
    await page.locator('#patternIntensity').fill('73');
    await page.locator('.pattern-card').filter({ hasText: 'Точки' }).click();
    await page.locator('#patternScale').fill('125');
    await page.locator('[data-appearance-theme="dark"]').click();
    await page.locator('.pattern-card').filter({ hasText: 'Рельеф' }).click();
    await page.locator('#patternVariable').check();
    await page.locator('#patternScale').fill('180');
    await page.locator('[data-appearance-theme="light"]').click();
    assert.equal(await page.locator('#patternScale').inputValue(), '125');
    if (pagehide) {
      const departure = page.waitForResponse(response => response.request().method() === 'PATCH');
      await page.evaluate(() => dispatchEvent(new Event('pagehide')));
      await departure;
    }
  } finally { release(); }
  await idle(page);
  const patterns = server.preferences.background_patterns;
  assert.equal(patterns.light.selected, 'dots');
  assert.equal(patterns.light.patterns.waves.scale, 160);
  assert.equal(patterns.light.patterns.waves.intensity, 73);
  assert.equal(patterns.light.patterns.dots.scale, 125);
  assert.equal(patterns.dark.selected, 'relief');
  assert.equal(patterns.dark.patterns.relief.scale, 180);
  assert.equal(patterns.dark.patterns.relief.variable, true);
  const merged = calls.filter(c => c.method === 'PATCH').at(-1).body;
  assert.deepEqual(merged.background_patterns.dark, { selected: 'relief', patterns: { relief: { variable: true, scale: 180 } } });
  assert.equal(merged.background_patterns.light.patterns.waves.intensity, 73);
  if (pagehide) assert.equal(calls.filter(c => c.method === 'PATCH')[2].body.background_patterns.light.patterns.waves.scale, 160);
});

test('two tabs use partial updates and an expired writer restores canonical patterns before renewal', async t => {
  const server = preferencesServer();
  const { page: first } = await setup(t, 'light', undefined, server);
  const { page: second, calls } = await setup(t, 'light', undefined, server);
  await section(first).locator('summary').click(); await select(first, 'Точки');
  await first.locator('#patternScale').fill('135'); await idle(first);
  await first.locator('#patternVariable').check(); await idle(first);
  const seed = server.preferences.background_patterns.light.patterns.dots.seed;
  // This tab registered from a stale GET; its initial snapshot must not replace the first tab's changes.
  await second.locator('[data-appearance-theme="dark"]').click(); await idle(second);
  await section(second).locator('summary').click(); await select(second, 'Волны');
  await second.locator('#patternScale').fill('185'); await idle(second);
  assert.equal(server.preferences.background_patterns.light.patterns.dots.scale, 135);
  await first.locator('#patternVariable').uncheck(); await idle(first);
  assert.equal(await first.locator('input[name="appearance-pattern"]:checked').inputValue(), 'waves');
  assert.equal(await first.locator('#patternScale').inputValue(), '185');
  await first.locator('#patternScale').fill('190'); await idle(first);
  assert.equal(server.preferences.background_patterns.light.patterns.dots.seed, seed);
  server.writers.delete(await second.evaluate(() => appearanceWriterId));
  await select(second, 'Рельеф');
  assert.equal(await second.locator('input[name="appearance-pattern"]:checked').inputValue(), 'waves');
  assert.equal(await second.locator('#patternScale').inputValue(), '190');
  assert.match(await second.locator('.appearance-status').textContent(), /Не удалось/);
  assert.equal(server.preferences.background_patterns.dark.patterns.relief, undefined);
  const count = calls.filter(c => c.method === 'POST').length;
  await second.locator('#patternVariable').check(); await idle(second);
  assert.equal(calls.filter(c => c.method === 'POST').length, count + 1);
  assert.equal(await second.locator('.appearance-status').textContent(), '');
  assert.equal(server.preferences.background_patterns.dark.patterns.waves.scale, 190);
  assert.equal(server.preferences.background_patterns.light.patterns.dots.scale, 135);
  assert.equal(server.preferences.background_patterns.light.patterns.dots.seed, seed);
});

test('failed save reloads canonical data, retains newer nested edits, and falls back to the last acknowledgement offline', async t => {
  const { page, calls, server } = await setup(t);
  await section(page).locator('summary').click(); await select(page, 'Волны');
  const seed = server.preferences.background_patterns.light.patterns.waves.seed;
  let release, started;
  const held = new Promise(resolve => { started = resolve; });
  const gate = new Promise(resolve => { release = resolve; });
  let fail = true;
  server.intercept = async route => {
    if (route.request().method() === 'PATCH' && fail) {
      fail = false; started(); await gate;
      await route.fulfill({ status: 503, json: {} }); return true;
    }
    return false;
  };
  const getCount = calls.filter(c => c.method === 'GET').length;
  await page.locator('#patternScale').fill('150'); await held;
  try { await page.locator('#patternIntensity').fill('83'); } finally { release(); }
  await idle(page);
  assert.equal(await page.locator('#patternScale').inputValue(), '100');
  assert.equal(await page.locator('#patternIntensity').inputValue(), '83');
  assert.equal(server.preferences.background_patterns.light.patterns.waves.seed, seed);
  assert.equal(calls.filter(c => c.method === 'GET').length, getCount + 1);
  // A gateway failure can follow a committed write; re-reading must retain its server seed.
  server.intercept = async route => {
    if (route.request().method() !== 'PATCH') return false;
    const { appearance_write, ...patch } = route.request().postDataJSON();
    server.apply(patch); server.intercept = null;
    await route.fulfill({ status: 503, json: {} }); return true;
  };
  await select(page, 'Рельеф');
  assert.equal(await page.locator('input[name="appearance-pattern"]:checked').inputValue(), 'relief');
  const saved = structuredClone(server.preferences.background_patterns);
  assert.equal(saved.light.patterns.waves.seed, seed);
  assert.match(await page.locator('.appearance-status').textContent(), /Не удалось/);
  server.intercept = async route => { await route.fulfill({ status: 503, json: {} }); return true; };
  await page.locator('#patternScale').fill('175'); await idle(page);
  assert.equal(await page.locator('#patternScale').inputValue(), '100');
  assert.deepEqual(await page.evaluate(() => appearance.background_patterns), saved);
  server.intercept = null;
  await page.locator('#patternVariable').check(); await idle(page);
  assert.equal(await page.locator('.pattern-code span').textContent(), String(saved.light.patterns.relief.seed));
});

test('lost registration response recovers the server-generated seed without creating a new one', async t => {
  const { page, server } = await setup(t);
  await section(page).locator('summary').click();
  server.intercept = async route => {
    if (!route.request().url().endsWith('/appearance-session')) return false;
    server.apply(route.request().postDataJSON().preferences);
    server.intercept = null;
    await route.fulfill({ status: 503, json: {} }); return true;
  };
  await select(page, 'Точки');
  const seed = server.preferences.background_patterns.light.patterns.dots.seed;
  assert.equal(await page.locator('input[name="appearance-pattern"]:checked').inputValue(), 'dots');
  await page.locator('#patternVariable').check(); await idle(page);
  assert.equal(await page.locator('.pattern-code span').textContent(), String(seed));
  await select(page, 'Без узора'); await select(page, 'Точки');
  assert.equal(await page.locator('.pattern-code span').textContent(), String(seed));
});

test('late GET merges local nested input without losing saved inactive patterns or their seeds', async t => {
  const saved = { selected: 'grid', patterns: {
    grid: { scale: 60, intensity: 10, variable: false, seed: 88 },
    waves: { scale: 190, intensity: 99, variable: true, seed: 77 },
  } };
  const server = preferencesServer('light', { light: structuredClone(saved) });
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  server.intercept = async route => {
    if (route.request().method() === 'GET') await gate;
    return false;
  };
  const { page, calls } = await setup(t, 'light', undefined, server, false);
  try {
    await section(page).locator('summary').click();
    await page.locator('.pattern-card').filter({ hasText: 'Точки' }).click();
    await page.locator('#patternScale').fill('130');
    await page.locator('#patternVariable').check();
    assert.equal(await page.locator('input[name="appearance-pattern"]:checked').inputValue(), 'dots');
    assert.equal(await page.locator('#patternScale').inputValue(), '130');
    assert.equal(calls.filter(c => c.method === 'POST').length, 0);
  } finally { release(); }
  await idle(page);
  assert.equal(server.preferences.background_patterns.light.selected, 'dots');
  assert.equal(server.preferences.background_patterns.light.patterns.dots.scale, 130);
  assert.equal(server.preferences.background_patterns.light.patterns.dots.variable, true);
  assert.deepEqual(server.preferences.background_patterns.light.patterns.grid, saved.patterns.grid);
  assert.deepEqual(server.preferences.background_patterns.light.patterns.waves, saved.patterns.waves);
  assert.equal(await page.locator('.pattern-code span').textContent(), String(server.preferences.background_patterns.light.patterns.dots.seed));
});

for (const theme of ['light', 'dark']) test(`accordion and independent controls with automatic saves: ${theme}`, async t => {
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
  await idle(page);
  assert.equal(await page.locator('#patternIntensity').isDisabled(), true);
  assert.equal(await page.locator('#patternScale').isEnabled(), true);
  await page.locator('#patternVariable').uncheck();
  assert.equal(await page.locator('#patternIntensity').inputValue(), '73');
  await page.locator('#patternVariable').check();
  await select(page, 'Без узора');
  assert.equal(await page.locator('.pattern-parameters').isVisible(), false);
  await select(page, 'Точки');
  assert.equal(await page.locator('#patternIntensity').isDisabled(), false);
  assert.equal(await page.locator('#patternScale').inputValue(), '100');
  await select(page, 'Рельеф');
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
  assert.ok(calls.length > initialCalls);
  assert.equal(await page.getByText('Пересчитать', { exact: true }).count(), 0);
  assert.equal(await section(page).evaluate(el => getComputedStyle(el).animationName), 'none');
});

test('theme/color saves remain unchanged; pattern selections survive themes and reload', async t => {
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
  const writes = calls.filter(call => call.method === 'PATCH' && !call.body.background_patterns).map(call => {
    const { appearance_write, ...patch } = call.body; return patch;
  });
  assert.deepEqual(writes, [{ theme_preference: 'dark' }, { theme_preference: 'light' }, { theme_preference: 'dark' }, { accent_hue_dark: 120 }]);
  await page.locator('[data-appearance-theme="light"]').click();
  await page.waitForFunction(() => !saveInFlight);
  assert.equal(await page.locator('.pattern-code span').textContent(), seed);
  assert.equal(await page.locator('#appearancePatternStyles').count(), 1);
  await page.reload();
  await page.waitForFunction(() => confirmedAppearance.appearance_revision !== null);
  assert.equal(await section(page).getAttribute('open'), null);
  assert.equal(await page.locator('input[name="appearance-pattern"]:checked').inputValue(), 'waves');
  assert.equal(await page.locator('#patternScale').inputValue(), '140');
  assert.equal(await page.locator('.pattern-code span').textContent(), seed);
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
  assert.deepEqual([...new Set(references)], ['20261029-contour-spacing-v1']);
  assert.match(fs.readFileSync('api/app/static/theme.js', 'utf8'), /appearance-pattern\.css\?v=20261029-contour-geometry-v1/);
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
    await idle(page);
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
  assert.ok(calls.some(c => c.method === 'PATCH' && c.body.background_patterns));
  assert.deepEqual(await page.evaluate(() => [Object.keys(localStorage), Object.keys(sessionStorage)]), [[], []]);
  if (process.env.PATTERN_SCREENSHOT_DIR && width === 1440) {
    await page.locator('#patternProbe').evaluate(el => el.remove());
    await page.locator('#themeToggleBtn').click();
    await page.screenshot({ path: path.join(process.env.PATTERN_SCREENSHOT_DIR, `pattern-workspace-${theme}.png`) });
  }
});
