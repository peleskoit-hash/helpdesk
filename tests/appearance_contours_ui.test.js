const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const API = '/api/v1/background-contours';
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAFAAAABACAYAAACNx/A2AAAB+klEQVR4nO2bMW7DMAxFLR2yF+nOgXsu0ku26GDAMOzIkvnJb4VvyRDBkj4+qYSmliVJkiRJBikLGfr989saI68vmnWXJwjGLGh5snAMQhY24eSCCBbPsMJtIj3ZtMVmkc+mEFAPNojYnNc8rgLqblMervCcs7BsRDdjEWGNErEuZOKhftogTn2YgLpzk6fztuznRohoLqAOLnLd6PqJcoz1c00F1M68gxLJM5xhOVA6xNuPRbiQ/hDRjlyGzHvvQORDmAOv8E48dC60oka6L4IeF/5/3xrj6kDtDN1IF65zttZZvUTRoLyHzoXuOVA6xYtw4VX3uQmo5AfBHeACqkHoerqwx323BdSGOEx5D5UHXUJYDMTzcGGv+1zLWeyMiOdWzlqMYPx3Ai1nCVneazGy3rBqzCwuhNYDUTCVu0LKWTNhHsLiJB5LKN8SUE4qyU/AKmJCC6ozuNDsr9yToHipxCKeBLvwdggL+MW1NdYtH0MCsgklgS6sFqeXPMSFiIajOsuPZQly4VAIH4kn5C5EtbtdFrBXFA0Q8cyFyLVUy9AVp568u1imn0sCHh0WrbFR7F2I7lQtM/VGR3TtuxVU1TGcW4ecJdBiggSJOE2XPsM9EdRcK1PkJZ39ptKWvCtnhOZtTR4hP/K+8Cw31pMkSZIkSZLlI/kDb3ykjdnyFtUAAAAASUVORK5CYII=', 'base64');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });

function contour(n, overrides = {}) {
  return { id: `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`, name: `Контур ${n}`,
    author_name: `Автор ${n}`, can_edit: true, can_delete: true, rotation: 0, rings: 0, distance: 20, base_scale: 100, ...overrides };
}

async function setup(t, { items = [contour(3), contour(2), contour(1)], theme = 'light', touch = false,
  viewport = { width: 1440, height: 1100 }, listError = 0, imageError = false, patterns, server, clock = false, deviceScaleFactor = 1 } = {}) {
  const context = await browser.newContext({ viewport, hasTouch: touch, deviceScaleFactor });
  t.after(() => context.close());
  const page = await context.newPage(), errors = [], calls = [];
  if (clock) await page.clock.install();
  page.on('pageerror', error => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, []));
  const preferences = server?.preferences || { theme_preference: theme, appearance_revision: '0', background_patterns: patterns || {
    light: { selected: 'dots', patterns: { dots: { scale: 125, intensity: 73, variable: true, seed: 42 } } },
  } };
  const state = server || { items, listError, deleteError: 0, imageError, invalidList: false, preferences, writers: new Map(), writer: 0, seed: 100 };
  const custom = key => /^[0-9a-f-]{36}$/.test(key);
  function merge(patch) {
    const { background_patterns, ...rest } = patch;
    Object.assign(preferences, rest);
    if (background_patterns) {
      preferences.background_patterns ||= {};
      for (const [theme, changes] of Object.entries(background_patterns)) {
        const saved = preferences.background_patterns[theme] ||= { selected: 'grid', patterns: {} };
        if (changes.selected) { saved.selected = changes.selected; delete saved.fallback_grid; }
        const keys = new Set(Object.keys(changes.patterns || {}));
        if (saved.selected !== 'none') keys.add(saved.selected);
        for (const key of keys) {
          saved.patterns[key] ||= { scale: 100, intensity: 50, variable: false, seed: ++state.seed };
          Object.assign(saved.patterns[key], changes.patterns?.[key]);
        }
      }
    }
    preferences.appearance_revision = String(Number(preferences.appearance_revision) + 1);
  }
  await context.route('http://desk.test/**', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve('api/app', url.pathname.slice(1));
      return route.fulfill({ body: fs.readFileSync(file), contentType: { '.js': 'text/javascript', '.css': 'text/css', '.ttf': 'font/ttf' }[path.extname(file)] || 'application/octet-stream' });
    }
    if (url.pathname.startsWith('/api/')) {
      calls.push({ path: url.pathname, method: request.method(), body: request.postDataJSON() });
      if (url.pathname.startsWith(API) && url.pathname.endsWith('/image')) {
        return route.fulfill(state.imageError ? { status: 404, body: '' } : { contentType: 'image/png', body: PNG });
      }
      if (url.pathname === API) return route.fulfill({ status: state.listError || 200,
        json: state.listError ? { detail: { error: { message: 'Список временно недоступен' } } } : state.invalidList ? {} : { items: state.items } });
      if (url.pathname.startsWith(API + '/') && request.method() === 'DELETE') {
        if (state.deleteError) return route.fulfill({ status: state.deleteError, json: { detail: { error: { message: 'Удаление запрещено или временно недоступно' } } } });
        state.items = state.items.filter(item => url.pathname !== `${API}/${item.id}`);
        return route.fulfill({ status: 204 });
      }
      if (url.pathname.endsWith('/appearance-session')) {
        const body = request.postDataJSON(), accepted = body.expected_revision === preferences.appearance_revision;
        const writer_id = `00000000-0000-4000-8000-${String(++state.writer).padStart(12, '0')}`;
        if (accepted) merge(body.preferences);
        state.writers.set(writer_id, accepted ? body.sequence : 0);
        return route.fulfill({ json: { writer_id, preferences } });
      }
      if (request.method() === 'PATCH') {
        const { appearance_write, ...patch } = request.postDataJSON();
        const invalid = Object.values(patch.background_patterns || {}).some(theme => custom(theme.selected || '') && !state.items.some(item => item.id === theme.selected));
        if (invalid) return route.fulfill({ status: 422, json: {} });
        if (!state.writers.has(appearance_write.writer_id)) return route.fulfill({ status: 409, json: { detail: { error: { code: 'appearance_session_expired' }, preferences } } });
        if (appearance_write.sequence > state.writers.get(appearance_write.writer_id)) {
          merge(patch); state.writers.set(appearance_write.writer_id, appearance_write.sequence);
        }
      }
      for (const theme of Object.values(preferences.background_patterns || {})) {
        if (custom(theme.selected) && !state.items.some(item => item.id === theme.selected)) {
          theme.selected = 'grid'; theme.fallback_grid = true;
          preferences.appearance_revision = String(Number(preferences.appearance_revision) + 1);
        }
      }
      return route.fulfill({ json: preferences });
    }
    return route.fulfill({ contentType: 'text/html', body: fs.readFileSync('api/app/templates/settings.html', 'utf8')
      .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, script => script.includes('/static/theme.js?') ? script : '') });
  });
  await page.goto('http://desk.test/settings');
  await page.waitForFunction(() => confirmedAppearance.appearance_revision !== null);
  await page.locator('#themeToggleBtn').click();
  return { page, context, calls, state, preferences, async open() {
    const loaded = page.waitForResponse(response => new URL(response.url()).pathname === API && response.request().method() === 'GET');
    await page.locator('.appearance-pattern summary').click();
    await loaded;
    await page.waitForFunction(() => {
      const status = document.querySelector('.contour-status');
      return status.textContent !== 'Загрузка контуров…' && document.querySelector('.contour-list').getAttribute('aria-busy') === 'false';
    });
  } };
}

const card = (page, n) => page.locator(`.contour-card[data-contour-id="${contour(n).id}"]`);
const remove = (page, n) => card(page, n).locator('[data-contour-delete]');
const mutationCalls = calls => calls.filter(call => !call.path.startsWith(API) && call.method !== 'GET');

for (const theme of ['light', 'dark']) test(`server order, previews, safe text and unchanged preferences on focus: ${theme}`, async t => {
  const items = [contour(9, { name: '<img src=x onerror=alert(1)>', author_name: '<b>Автор</b>' }), contour(2), contour(8)];
  const h = await setup(t, { theme, items });
  const { page, calls } = h;
  assert.equal(calls.filter(c => c.path === API).length, 0);
  const appearanceBefore = await page.evaluate(() => JSON.stringify(appearance));
  await h.open();
  assert.deepEqual(await page.locator('.contour-name').allTextContents(), items.map(item => item.name));
  assert.equal(await page.locator('.contour-name img, .contour-author b').count(), 0);
  assert.equal(await card(page, 9).locator('.contour-author').textContent(), 'Created: <b>Автор</b>');
  await page.waitForFunction(() => [...document.querySelectorAll('.contour-preview')].every(image => image.complete && image.naturalWidth > 0));
  assert.equal(await card(page, 9).evaluate(el => getComputedStyle(el).borderStyle), 'dashed');
  assert.equal(await card(page, 9).locator('.contour-footer').evaluate(el => getComputedStyle(el).opacity), '0');
  await card(page, 9).hover();
  assert.equal(await card(page, 9).locator('.contour-footer').evaluate(el => getComputedStyle(el).opacity), '1');
  await card(page, 9).focus();
  assert.equal(await page.evaluate(() => JSON.stringify(appearance)), appearanceBefore);
  assert.deepEqual(mutationCalls(calls), []);
  assert.equal(await page.locator('.pattern-card input').count(), 6);
  assert.deepEqual(await page.locator('.pattern-card input').evaluateAll(inputs => inputs.map(input => input.value)), ['none', 'grid', 'dots', 'diagonals', 'waves', 'relief']);
  assert.equal(await page.getByRole('button', { name: 'Добавить свой узор +' }).isEnabled(), true);
  assert.match(await page.locator('#contourCreateHint').textContent(), /добавьте его в общую библиотеку/);
  assert.equal(await page.locator('dialog').count(), 0);
  if (process.env.CONTOUR_SCREENSHOT_DIR) await page.locator('#appearanceMenu').screenshot({ path: path.join(process.env.CONTOUR_SCREENSHOT_DIR, `library-${theme}.png`) });
});

test('all users see both actions, denied actions explain permissions and editing is always disabled', async t => {
  const h = await setup(t, { items: [contour(1), contour(2, { can_edit: false }), contour(3, { can_edit: false, can_delete: false })] });
  await h.open();
  for (const n of [1, 2, 3]) {
    await card(h.page, n).focus();
    assert.equal(await card(h.page, n).locator('.contour-footer').evaluate(el => getComputedStyle(el).opacity), '1');
    assert.equal(await card(h.page, n).getByRole('button', { name: 'Редактировать контур' }).isDisabled(), true);
    assert.equal(await remove(h.page, n).isDisabled(), n === 3);
    assert.equal(await card(h.page, n).locator('.contour-action svg').count(), 2);
  }
  assert.match(await card(h.page, 1).locator('.contour-action-hint').first().getAttribute('title'), /следующем этапе/);
  assert.match(await card(h.page, 2).locator('.contour-action-hint').first().getAttribute('title'), /Нет права/);
  assert.match(await card(h.page, 3).locator('.contour-action-hint').last().getAttribute('title'), /автор или администратор/);
  const boxes = await card(h.page, 1).locator('.contour-action').evaluateAll(buttons => buttons.map(button => { const r = button.getBoundingClientRect(); return [r.y, r.bottom]; }));
  assert.ok(Math.abs(boxes[0][1] - boxes[1][0]) <= 1);
  assert.deepEqual(mutationCalls(h.calls), []);
});

test('deletion uses the real V2 confirmation; cancel sends nothing and confirmation refreshes the list only', async t => {
  const h = await setup(t);
  await h.open();
  const before = await h.page.evaluate(() => JSON.stringify(appearance));
  await card(h.page, 3).hover(); await remove(h.page, 3).click();
  await h.page.locator('.desk-dialog').waitFor();
  assert.match(await h.page.locator('.desk-dialog-message').textContent(), /Контур 3/);
  await h.page.locator('[data-dialog-cancel]').click();
  assert.equal(h.calls.filter(c => c.method === 'DELETE').length, 0);
  assert.equal(await h.page.locator('#appearanceMenu').evaluate(el => el.matches(':popover-open')), true);
  await card(h.page, 3).hover(); await remove(h.page, 3).click();
  await h.page.locator('[data-dialog-accept]').click();
  await h.page.waitForFunction(() => document.querySelectorAll('.contour-card').length === 2 && document.querySelector('.contour-list').getAttribute('aria-busy') === 'false');
  assert.deepEqual(h.calls.filter(c => c.method === 'DELETE').map(c => c.path), [`${API}/${contour(3).id}`]);
  assert.ok(h.calls.filter(c => c.path === API).length >= 2);
  assert.deepEqual(await h.page.locator('.contour-name').allTextContents(), ['Контур 2', 'Контур 1']);
  assert.equal(await h.page.evaluate(() => JSON.stringify(appearance)), before);
  assert.deepEqual(mutationCalls(h.calls), []);
});

for (const code of [403, 503]) test(`failed DELETE ${code} retains cards and preferences and exposes the error`, async t => {
  const h = await setup(t);
  await h.open(); h.state.deleteError = code;
  const before = await h.page.evaluate(() => JSON.stringify(appearance));
  await card(h.page, 3).hover(); await remove(h.page, 3).click();
  await h.page.locator('[data-dialog-accept]').click();
  await h.page.waitForFunction(() => document.querySelector('.contour-status').textContent.includes('Не удалось удалить'));
  assert.equal(await h.page.locator('.contour-card').count(), 3);
  assert.equal(await remove(h.page, 3).isEnabled(), true);
  assert.match(await h.page.locator('.contour-status').textContent(), /Удаление запрещено/);
  assert.equal(await h.page.evaluate(() => JSON.stringify(appearance)), before);
  assert.deepEqual(mutationCalls(h.calls), []);
});

test('another tab already deleted the contour: 404 refreshes without an error', async t => {
  const h = await setup(t); await h.open();
  await card(h.page, 3).hover(); await remove(h.page, 3).click();
  h.state.items = h.state.items.filter(item => item.id !== contour(3).id);
  h.state.deleteError = 404;
  await h.page.locator('[data-dialog-accept]').click();
  await h.page.waitForFunction(() => document.querySelectorAll('.contour-card').length === 2);
  assert.equal(await h.page.locator('.contour-status').textContent(), '');
});

test('GET errors and malformed responses can be retried without disturbing built-ins', async t => {
  const h = await setup(t, { listError: 503 }); await h.open();
  assert.match(await h.page.locator('.contour-status').textContent(), /Не удалось загрузить/);
  assert.equal(await h.page.locator('.pattern-card').count(), 6);
  h.state.listError = 0; h.state.invalidList = true;
  await h.page.locator('.contour-retry').click();
  await h.page.waitForFunction(() => document.querySelector('.contour-status').textContent.includes('Некорректный'));
  h.state.invalidList = false;
  await h.page.locator('.contour-retry').click();
  await h.page.waitForFunction(() => document.querySelectorAll('.contour-card').length === 3);
  assert.equal(await h.page.locator('.contour-status').textContent(), '');
  assert.equal(await h.page.locator('.contour-retry').isVisible(), false);
  assert.equal(await h.page.locator('input[name="appearance-pattern"]:checked').inputValue(), 'dots');
  assert.deepEqual(mutationCalls(h.calls), []);
});

test('a missing preview leaves the card and its actions usable', async t => {
  const h = await setup(t, { imageError: true }); await h.open();
  await h.page.waitForFunction(() => document.querySelectorAll('.contour-image-missing').length === 3);
  assert.equal(await card(h.page, 3).locator('img').isVisible(), false);
  assert.match(await card(h.page, 3).getAttribute('title'), /превью/);
  await card(h.page, 3).focus();
  assert.equal(await remove(h.page, 3).isEnabled(), true);
});

for (const width of [1440, 390]) test(`one partial row scrolls while an empty library fits: ${width}`, async t => {
  const h = await setup(t, { viewport: { width, height: 1100 }, items: [contour(1)] }); await h.open();
  for (const empty of [false, true]) {
    if (empty) {
      h.state.items = [];
      await h.page.locator('.appearance-pattern summary').click();
      await h.open();
    }
    const metrics = await h.page.locator('.pattern-library').evaluate(el => ({ height: el.clientHeight, scroll: el.scrollHeight,
      width: el.clientWidth, scrollWidth: el.scrollWidth, overflow: getComputedStyle(el).overflowY, radius: getComputedStyle(el).borderRadius }));
    assert.equal(metrics.scroll > metrics.height, !empty);
    assert.equal(metrics.width, metrics.scrollWidth);
    assert.equal(metrics.overflow, 'auto');
    assert.equal(metrics.radius, '16px');
  }
});

for (const width of [1440, 390]) test(`large library scrolls inside its bounded bubble and retains built-in cards: ${width}`, async t => {
  const h = await setup(t, { viewport: { width, height: 1100 }, items: Array.from({ length: 40 }, (_, n) => contour(40 - n)) }); await h.open();
  const metrics = await h.page.locator('.pattern-library').evaluate(el => ({ height: el.clientHeight, scroll: el.scrollHeight, width: el.clientWidth, scrollWidth: el.scrollWidth,
    bottom: el.getBoundingClientRect().bottom, headingBottom: el.querySelector('h3').getBoundingClientRect().bottom,
    firstTop: el.querySelector('.contour-card').getBoundingClientRect().top, builtInBottom: el.querySelector('.pattern-cards').getBoundingClientRect().bottom }));
  assert.ok(metrics.height <= 560 && metrics.scroll > metrics.height);
  assert.equal(metrics.width, metrics.scrollWidth);
  assert.ok(metrics.builtInBottom < metrics.bottom && metrics.headingBottom < metrics.bottom && metrics.firstTop < metrics.bottom);
  await card(h.page, 1).scrollIntoViewIfNeeded(); await card(h.page, 1).focus();
  assert.ok(await h.page.locator('.pattern-library').evaluate(el => el.scrollTop > 0));
  assert.equal(await remove(h.page, 1).isVisible(), true);
});

test('touch users can access both actions without hover and open the editor', async t => {
  const h = await setup(t, { touch: true, viewport: { width: 390, height: 844 }, items: [contour(1)] }); await h.open();
  assert.equal(await card(h.page, 1).locator('.contour-footer').evaluate(el => getComputedStyle(el).opacity), '1');
  await remove(h.page, 1).tap();
  await h.page.locator('.desk-dialog').waitFor();
  await h.page.locator('[data-dialog-cancel]').tap();
  if (process.env.CONTOUR_SCREENSHOT_DIR) await h.page.locator('#appearanceMenu').screenshot({ path: path.join(process.env.CONTOUR_SCREENSHOT_DIR, 'library-touch.png') });
  await h.page.getByRole('button', { name: 'Добавить свой узор +' }).tap();
  await h.page.locator('#contourEditor').waitFor();
  assert.equal(await h.page.locator('#contourEditor').getByRole('button', { name: 'Сохранить', exact: true }).isDisabled(), true);
  await h.page.locator('#contourEditor [data-editor-close]').first().tap();
  assert.equal(await h.page.locator('#contourEditor').count(), 0);
  assert.deepEqual(mutationCalls(h.calls), []);
});

test('reopening the section refreshes the library and built-in selection still uses the existing queue', async t => {
  const h = await setup(t); await h.open();
  await h.page.evaluate(() => { HelpdeskTheme.initThemeToggle(); HelpdeskTheme.initThemeToggle(); });
  h.state.items = [contour(7)];
  await h.page.locator('.appearance-pattern summary').click(); await h.open();
  assert.deepEqual(await h.page.locator('.contour-name').allTextContents(), ['Контур 7']);
  assert.equal(h.calls.filter(call => call.path === API).length, 2);
  await h.page.locator('.pattern-card').filter({ hasText: 'Волны' }).click();
  await h.page.waitForFunction(() => !saveInFlight);
  const patch = h.calls.find(c => c.method === 'PATCH');
  assert.deepEqual(patch.body.background_patterns, { light: { selected: 'waves' } });
  assert.equal(await h.page.locator('input[name="appearance-pattern"]:checked').inputValue(), 'waves');
});

const idle = page => page.waitForFunction(() => !saveInFlight && !Object.keys(pendingAppearance).length);
async function choose(h, n) {
  await card(h.page, n).locator('.contour-select').click();
  await idle(h.page);
  await h.page.waitForFunction(() => document.body.dataset.patternKind === 'contour' && document.body.hasAttribute('data-pattern-custom'));
}
const composition = page => page.evaluate(() => {
  const source = document.body.style.getPropertyValue('--desk-pattern-image');
  const svg = decodeURIComponent(source.slice(source.indexOf(',') + 1, source.lastIndexOf('"')));
  const root = new DOMParser().parseFromString(svg, 'image/svg+xml');
  return { svg, count: root.querySelectorAll('use').length, transforms: [...root.querySelectorAll('use')].map(node => node.getAttribute('transform')),
    images: root.querySelectorAll('image').length, width: Number(root.querySelector('svg#contour')?.getAttribute('width')), height: Number(root.querySelector('svg#contour')?.getAttribute('height')), viewBox: root.documentElement.getAttribute('viewBox'), opacity: root.querySelector('svg > g')?.getAttribute('opacity'),
    size: document.body.style.getPropertyValue('--desk-contour-size') };
});

for (const theme of ['light', 'dark']) for (const rings of [0, 1, 2]) test(`custom composition ${[1, 9, 25][rings]} rotated elements and shared bitmap: ${theme}`, async t => {
  const h = await setup(t, { theme, items: [contour(1, { rings, rotation: 47, distance: 70, base_scale: 135 })] });
  await h.open(); await choose(h, 1);
  assert.equal(await card(h.page, 1).locator('.contour-select').getAttribute('aria-pressed'), 'true');
  assert.equal(await card(h.page, 1).evaluate(el => el.classList.contains('selected')), true);
  assert.equal(await h.page.locator('.pattern-card input:checked').count(), 0);
  const paint = await composition(h.page);
  assert.equal(paint.count, [1, 9, 25][rings]);
  assert.equal(paint.images, 1);
  assert.ok(paint.width > 0);
  assert.equal(paint.viewBox, '-720 -550 1440 1100');
  assert.ok(paint.transforms.every(transform => /translate\([-\d.]+ [-\d.]+\) rotate\(47\)$/.test(transform)));
  if (rings) {
    const positions = paint.transforms.map(value => value.match(/translate\(([-\d.]+) ([-\d.]+)\)/).slice(1).map(Number));
    assert.equal(new Set(positions.map(point => point[0])).size, rings * 2 + 1);
    assert.equal(new Set(positions.map(point => point[1])).size, rings * 2 + 1);
    assert.ok(Math.abs(positions[1][0] - positions[0][0] - (paint.width * Math.abs(Math.cos(47 * Math.PI / 180)) + paint.height * Math.abs(Math.sin(47 * Math.PI / 180))) * 1.7) < 1e-6);
  }
  assert.match(paint.svg, new RegExp(`flood-color="${theme === 'dark' ? 'white' : 'black'}"`));
  assert.equal(h.calls.filter(c => c.path === `${API}/${contour(1).id}/image`).length, 1, 'preview and background reuse the bitmap');
  const size = paint.width;
  await h.page.locator('#patternScale').fill('150'); await idle(h.page);
  assert.ok(Math.abs((await composition(h.page)).width - size * 1.5) < 1e-6);
  await h.page.locator('#patternIntensity').fill('80'); await idle(h.page);
  assert.equal((await composition(h.page)).opacity, '0.8');
  await h.page.locator('#patternVariable').check(); await idle(h.page);
  const seed = await h.page.locator('.pattern-code span').textContent();
  assert.match((await composition(h.page)).svg, new RegExp(`seed="${seed}"`));
  assert.equal(h.calls.filter(c => c.path === `${API}/${contour(1).id}/image`).length, 1);
  const actual = await h.page.locator('#appContent').evaluate(el => getComputedStyle(el).backgroundImage);
  assert.match(actual, /data:image\/svg\+xml/);
  if (theme === 'light' && rings === 2) {
    await h.page.evaluate(() => {
      const probe = document.createElement('div'); probe.id = 'contourPaintProbe'; probe.className = 'panel';
      probe.style.cssText = 'position:fixed;left:0;top:0;width:100vw;height:100vh;z-index:100;border:0;border-radius:0;background-color:var(--desk-surface)';
      document.querySelector('#appContent').appendChild(probe);
    });
    const painted = await h.page.locator('#contourPaintProbe').screenshot();
    await h.page.locator('.pattern-card').filter({ hasText: 'Без узора' }).click(); await idle(h.page);
    assert.ok(!painted.equals(await h.page.locator('#contourPaintProbe').screenshot()), 'SVG paints real pixels');
    await h.page.locator('#contourPaintProbe').evaluate(el => el.remove());
  }
  if (rings === 2 && process.env.CONTOUR_SCREENSHOT_DIR) {
    await choose(h, 1);
    await h.page.evaluate(() => appearanceMenu.hidePopover());
    await h.page.screenshot({ path: path.join(process.env.CONTOUR_SCREENSHOT_DIR, `custom-background-${theme}.png`) });
  }
});

test('independent custom parameters and seeds restore between patterns, themes and reload', async t => {
  const h = await setup(t); await h.open(); await choose(h, 3);
  await h.page.locator('#patternScale').fill('160'); await idle(h.page);
  await h.page.locator('#patternIntensity').fill('72'); await idle(h.page);
  await h.page.locator('#patternVariable').check(); await idle(h.page);
  const seed = await h.page.locator('.pattern-code span').textContent();
  await choose(h, 2);
  assert.equal(await h.page.locator('#patternScale').inputValue(), '100');
  await h.page.locator('#patternScale').fill('75'); await idle(h.page);
  await h.page.locator('[data-appearance-theme="dark"]').click(); await idle(h.page);
  await choose(h, 3);
  assert.equal(await h.page.locator('#patternScale').inputValue(), '100');
  await h.page.locator('#patternScale').fill('185'); await idle(h.page);
  await h.page.locator('[data-appearance-theme="light"]').click(); await idle(h.page);
  assert.equal(await h.page.locator('#patternScale').inputValue(), '75');
  await choose(h, 3);
  assert.equal(await h.page.locator('#patternScale').inputValue(), '160');
  assert.equal(await h.page.locator('.pattern-code span').textContent(), seed);
  await h.page.reload();
  await h.page.waitForFunction(() => document.body.hasAttribute('data-pattern-custom') && document.body.dataset.patternKind === 'contour');
  assert.equal(await h.page.locator('#patternScale').inputValue(), '160');
  assert.equal(await h.page.locator('.pattern-code span').textContent(), seed);
  assert.equal(h.preferences.background_patterns.dark.patterns[contour(3).id].scale, 185);
});

test('deleting a selected contour repairs both themes and synchronizes another open tab without saving preferences', async t => {
  const id = contour(3).id;
  const patterns = { light: { selected: id, patterns: { [id]: { scale: 100, intensity: 50, variable: false, seed: 17 },
    grid: { scale: 175, intensity: 99, variable: true, seed: 22 } } }, dark: { selected: id, patterns: { [id]: { scale: 150, intensity: 70, variable: true, seed: 23 } } } };
  const h = await setup(t, { patterns }); await h.open();
  await h.page.waitForFunction(() => document.body.dataset.patternKind === 'contour' && document.body.hasAttribute('data-pattern-custom'));
  const second = await h.context.newPage();
  second.on('pageerror', error => assert.fail(error.message));
  await second.goto('http://desk.test/profile');
  await second.waitForFunction(() => document.body.dataset.patternKind === 'contour' && document.body.hasAttribute('data-pattern-custom'));
  await second.locator('#themeToggleBtn').click();
  await second.locator('.appearance-pattern summary').click();
  await second.waitForFunction(() => document.querySelectorAll('.contour-card').length === 3);
  await card(h.page, 3).hover(); await remove(h.page, 3).click();
  await h.page.locator('[data-dialog-accept]').click();
  for (const page of [h.page, second]) {
    await page.waitForFunction(() => appearance.background_patterns.light.fallback_grid && !document.body.hasAttribute('data-pattern-custom'));
    assert.match(await page.locator('#appContent').evaluate(el => getComputedStyle(el).backgroundSize), /42px 42px/);
    assert.equal(await page.locator('input[name="appearance-pattern"]:checked').inputValue(), 'grid');
    assert.equal(await card(page, 3).count(), 0);
  }
  assert.deepEqual(h.preferences.background_patterns.light.patterns, patterns.light.patterns);
  assert.equal(h.preferences.background_patterns.dark.fallback_grid, true);
  assert.deepEqual(mutationCalls(h.calls), []);
});

test('a contour choice in one tab updates the other tab through BroadcastChannel', async t => {
  const h = await setup(t); await h.open(); await choose(h, 3);
  const second = await h.context.newPage();
  await second.goto('http://desk.test/settings');
  await second.waitForFunction(() => document.body.dataset.patternKind === 'contour' && document.body.hasAttribute('data-pattern-custom'));
  const before = h.calls.filter(c => c.method === 'PATCH').length;
  await choose(h, 2);
  await second.waitForFunction(id => appearance.background_patterns.light.selected === id && document.body.dataset.patternKind === 'contour', contour(2).id);
  assert.equal(h.calls.filter(c => c.method === 'PATCH').length, before + 1);
});

test('deletion from another browser is detected by the 15-second visible-tab poll', async t => {
  const id = contour(3).id;
  const h = await setup(t, { clock: true, patterns: { light: { selected: id, patterns: { [id]: { scale: 100, intensity: 50, variable: false, seed: 9 } } } } });
  await h.page.waitForFunction(() => document.body.dataset.patternKind === 'contour' && document.body.hasAttribute('data-pattern-custom'));
  h.state.items = [];
  await h.page.clock.fastForward(15001);
  await h.page.waitForFunction(() => appearance.background_patterns.light.fallback_grid && !document.body.hasAttribute('data-pattern-custom'));
  assert.match(await h.page.locator('#appContent').evaluate(el => getComputedStyle(el).backgroundSize), /42px 42px/);
});

for (const failure of ['image', 'catalogue']) test(`safe 42px grid for unavailable ${failure}, then recovery without losing the seed`, async t => {
  const id = contour(3).id;
  const h = await setup(t, { imageError: failure === 'image', listError: failure === 'catalogue' ? 503 : 0,
    patterns: { light: { selected: id, patterns: { [id]: { scale: 170, intensity: 80, variable: true, seed: 49 } } } } });
  await h.open();
  if (failure === 'image') await h.page.waitForFunction(() => document.querySelector('.contour-image-missing'));
  assert.equal(await h.page.locator('body').getAttribute('data-pattern-custom'), null);
  assert.match(await h.page.locator('#appContent').evaluate(el => getComputedStyle(el).backgroundSize), /42px 42px/);
  h.state.imageError = false; h.state.listError = 0;
  await h.page.evaluate(() => dispatchEvent(new Event('focus')));
  await h.page.waitForFunction(() => document.body.hasAttribute('data-pattern-custom'));
  assert.equal(h.preferences.background_patterns.light.selected, id);
  assert.equal(await h.page.locator('.pattern-code span').textContent(), '49');
  assert.deepEqual(mutationCalls(h.calls), []);
});

test('a stale card cannot save a UUID removed before the click; canonical selection is restored', async t => {
  const h = await setup(t); await h.open(); await choose(h, 3);
  h.state.items = h.state.items.filter(item => item.id !== contour(2).id);
  await card(h.page, 2).locator('.contour-select').click(); await idle(h.page);
  assert.equal(h.preferences.background_patterns.light.selected, contour(3).id);
  assert.equal(await card(h.page, 3).locator('.contour-select').getAttribute('aria-pressed'), 'true');
  assert.match(await h.page.locator('.appearance-status').textContent(), /Не удалось/);
});

for (const theme of ['light', 'dark']) for (const viewport of [{ width: 1920, height: 1080 }, { width: 390, height: 844 }]) test(`old contour at 100% fills the viewport, zoom clips and resize updates without image requests: ${theme} ${viewport.width}`, async t => {
  const item = contour(1), id = item.id;
  const h = await setup(t, { theme, viewport, items: [item], patterns: { [theme]: { selected: id, patterns: { [id]: { scale: 100, intensity: 100, variable: false, seed: 88 } } } } });
  await h.page.waitForFunction(() => document.body.hasAttribute('data-pattern-custom') && document.body.dataset.patternKind === 'contour');
  const paint = await composition(h.page);
  assert.ok(Math.abs(Math.max(paint.width / viewport.width, paint.height / viewport.height) - .94) < 1e-9);
  assert.equal(paint.count, 1);
  const css = await h.page.locator('#appContent').evaluate(el => {
    const style = getComputedStyle(el); return [style.backgroundSize, style.backgroundRepeat, style.backgroundPosition, style.backgroundAttachment];
  });
  assert.deepEqual(css, [`${viewport.width}px ${viewport.height}px`, 'no-repeat', '50% 50%', 'fixed']);
  const raster = await h.page.evaluate(async svg => {
    const image = new Image(); image.src = 'data:image/svg+xml,' + encodeURIComponent(svg); await image.decode();
    const canvas = new OffscreenCanvas(image.naturalWidth, image.naturalHeight), ctx = canvas.getContext('2d'); ctx.drawImage(image, 0, 0);
    const { alphaBounds } = await import('/static/contour_geometry.js?v=20261029-contour-spacing-v1');
    return alphaBounds(ctx.getImageData(0, 0, canvas.width, canvas.height).data, canvas.width, canvas.height);
  }, paint.svg);
  assert.ok(Math.max(raster.width / viewport.width, raster.height / viewport.height) > .90, 'real pixels occupy almost the whole area');
  assert.equal(h.calls.filter(call => call.method !== 'GET').length, 0, 'old metadata needs no writes or migration');
  await h.open(); await h.page.locator('#patternScale').fill('150'); await idle(h.page);
  const zoomed = await composition(h.page);
  assert.ok(Math.abs(zoomed.width - paint.width * 1.5) < 1e-6);
  assert.ok(zoomed.width > viewport.width || zoomed.height > viewport.height);
  await h.page.locator('#patternScale').fill('50'); await idle(h.page);
  assert.ok(Math.abs((await composition(h.page)).width - paint.width * .5) < 1e-6);
  const next = viewport.width === 390 ? { width: 844, height: 390 } : { width: 1280, height: 720 };
  await h.page.setViewportSize(next);
  await h.page.waitForFunction(next => document.body.style.getPropertyValue('--desk-contour-size') === `${next.width}px ${next.height}px`, next);
  assert.equal((await composition(h.page)).viewBox, `${-next.width / 2} ${-next.height / 2} ${next.width} ${next.height}`);
  assert.equal(h.calls.filter(call => call.path === `${API}/${id}/image`).length, 1);
  if (process.env.CONTOUR_SCREENSHOT_DIR) {
    await h.page.evaluate(() => appearanceMenu.hidePopover());
    await h.page.screenshot({ path: path.join(process.env.CONTOUR_SCREENSHOT_DIR, `viewport-${theme}-${viewport.width}.png`) });
  }
});

for (const theme of ['light', 'dark']) for (const rings of [1, 2]) test(`zero-gap background uses touching rotated bounds horizontally and vertically: ${theme} ${rings}`, async t => {
  const h = await setup(t, { theme, items: [0, 45, 90].map((rotation, index) => contour(index + 1, { rotation, rings, distance: 0, base_scale: 137 })) });
  await h.open();
  for (const [index, rotation] of [0, 45, 90].entries()) {
    await choose(h, index + 1);
    const paint = await composition(h.page), side = rings * 2 + 1;
    assert.equal(paint.count, side ** 2);
    const positions = paint.transforms.map(value => value.match(/translate\(([-\d.]+) ([-\d.]+)\)/).slice(1).map(Number));
    const angle = rotation * Math.PI / 180;
    const width = paint.width * Math.abs(Math.cos(angle)) + paint.height * Math.abs(Math.sin(angle));
    const height = paint.width * Math.abs(Math.sin(angle)) + paint.height * Math.abs(Math.cos(angle));
    for (let y = 0; y < side; y++) for (let x = 0; x < side; x++) {
      const p = positions[y * side + x];
      if (x + 1 < side) { const n = positions[y * side + x + 1]; assert.ok(Math.abs(n[0] - p[0] - width) < 1e-6); assert.equal(n[1], p[1]); }
      if (y + 1 < side) { const n = positions[(y + 1) * side + x]; assert.ok(Math.abs(n[1] - p[1] - height) < 1e-6); assert.equal(n[0], p[0]); }
    }
    assert.equal(h.state.items[index].base_scale, 137);
    assert.ok(paint.transforms.every(value => value.endsWith(`rotate(${rotation})`)));
  }
});

for (const theme of ['light', 'dark']) for (const width of [1440, 390]) test(`bounded library reveals half a user row and never grows with more items: ${theme} ${width}`, async t => {
  const h = await setup(t, { theme, viewport: { width, height: 1100 }, items: [contour(1)] }); await h.open();
  const measure = () => h.page.locator('.pattern-library').evaluate(el => {
    const r = el.getBoundingClientRect(), card = el.querySelector('.contour-card').getBoundingClientRect();
    return { height: r.height, scroll: el.scrollHeight, fraction: (r.bottom - 1 - card.top) / card.height,
      builtIns: el.querySelector('.pattern-cards').getBoundingClientRect().bottom <= r.bottom,
      heading: el.querySelector('h3').getBoundingClientRect().bottom <= r.bottom };
  });
  const first = await measure();
  if (process.env.CONTOUR_SCREENSHOT_DIR) await h.page.locator('#appearanceMenu').screenshot({ path: path.join(process.env.CONTOUR_SCREENSHOT_DIR, `bounded-${theme}-${width}.png`) });
  assert.ok(first.fraction >= .48 && first.fraction <= .53, JSON.stringify(first)); assert.equal(first.builtIns, true); assert.equal(first.heading, true);
  h.state.items = Array.from({ length: 40 }, (_, n) => contour(40 - n));
  await h.page.locator('.appearance-pattern summary').click(); await h.open();
  await h.page.waitForFunction(() => document.querySelectorAll('.contour-card').length === 40);
  const many = await measure(); assert.equal(many.height, first.height); assert.ok(many.scroll > first.scroll);
  await card(h.page, 1).scrollIntoViewIfNeeded();
  assert.equal((await measure()).height, first.height, 'inner scrolling must not recalculate a smaller height');
  const button = h.page.getByRole('button', { name: 'Добавить свой узор +', exact: true });
  await button.scrollIntoViewIfNeeded();
  const buttonBox = await button.boundingBox(), library = await h.page.locator('.pattern-library').boundingBox();
  assert.ok(Math.abs(buttonBox.x + buttonBox.width / 2 - library.x - library.width / 2) < 1);
  assert.ok(await button.evaluate(el => parseFloat(getComputedStyle(el).fontSize) >= 18));
  assert.equal(await button.evaluate(el => el.closest('.pattern-library') === null), true);
});
for (const theme of ['light', 'dark']) test(`custom intensity spans zero to opaque white/black without changing stored values: ${theme}`, async t => {
  const id = contour(1).id, patterns = { light: { selected: id, patterns: { [id]: { scale: 100, intensity: 50, variable: false, seed: 71 } } },
    dark: { selected: id, patterns: { [id]: { scale: 100, intensity: 50, variable: false, seed: 72 } } } };
  const h = await setup(t, { theme, items: [contour(1)], patterns }); await h.open(); await choose(h, 1);
  const other = JSON.stringify(h.preferences.background_patterns[theme === 'light' ? 'dark' : 'light']);
  for (const intensity of [0, 25, 50, 75, 100]) {
    await h.page.locator('#patternIntensity').fill(String(intensity)); await idle(h.page);
    assert.equal(Number((await composition(h.page)).opacity), intensity / 100);
    assert.equal(h.preferences.background_patterns[theme].patterns[id].intensity, intensity);
  }
  const pixels = await h.page.evaluate(async svg => {
    const image = new Image(); image.src = 'data:image/svg+xml,' + encodeURIComponent(svg); await image.decode();
    const canvas = new OffscreenCanvas(image.naturalWidth, image.naturalHeight), ctx = canvas.getContext('2d'); ctx.drawImage(image, 0, 0);
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    for (let p = 0; p < data.length; p += 4) if (data[p + 3] >= 254) return [...data.slice(p, p + 4)];
    return null;
  }, (await composition(h.page)).svg);
  assert.ok(pixels, '100% has fully opaque line pixels');
  assert.deepEqual(pixels.slice(0, 3), theme === 'dark' ? [255, 255, 255] : [0, 0, 0]);
  assert.equal(JSON.stringify(h.preferences.background_patterns[theme === 'light' ? 'dark' : 'light']), other);
  assert.equal(h.preferences.background_patterns[theme].patterns[id].seed, theme === 'light' ? 71 : 72);
});

for (const theme of ['light', 'dark']) test(`appearance inner scroll and add action remain reachable at simulated 200% browser zoom: ${theme}`, async t => {
  const h = await setup(t, { theme, viewport: { width: 720, height: 450 }, deviceScaleFactor: 2, items: Array.from({ length: 30 }, (_, n) => contour(30 - n)) });
  await h.open(); assert.equal(await h.page.evaluate(() => devicePixelRatio), 2);
  await h.page.locator('.pattern-library').scrollIntoViewIfNeeded();
  const before = await h.page.locator('.pattern-library').evaluate(el => ({ height: el.clientHeight, scroll: el.scrollHeight, width: el.clientWidth, scrollWidth: el.scrollWidth }));
  assert.ok(before.scroll > before.height); assert.equal(before.width, before.scrollWidth);
  await card(h.page, 1).scrollIntoViewIfNeeded();
  assert.equal(await h.page.locator('.pattern-library').evaluate(el => el.clientHeight), before.height);
  const button = h.page.getByRole('button', { name: 'Добавить свой узор +', exact: true });
  await button.click(); await h.page.locator('#contourEditor').waitFor();
  assert.equal(await h.page.locator('#contourEditor .editor-processing-section').evaluate(el => el.tagName), 'SECTION');
  await h.page.locator('#contourEditor .editor-close').click();
});

for (const theme of ['light', 'dark']) for (const [width, touch, expectedHeight] of [[1440, false, 157], [390, false, 179.5], [390, true, 187.5], [1440, true, 165]]) test(`creator card keeps its size, vertical actions and centred SVG ink: ${theme} ${width} touch ${touch}`, async t => {
  const h = await setup(t, { theme, touch, viewport: { width, height: 1100 }, items: [contour(1, { author_name: 'Китченко Денис Сергеевич' }), contour(2, { author_name: 'Китченко Д.', can_edit: false, can_delete: false })] });
  await h.open();
  for (const n of [1, 2]) {
    const item = card(h.page, n); await item.focus();
    assert.equal(await item.locator('.contour-author').textContent(), 'Created: Китченко Д.');
    const metrics = await item.evaluate(el => {
      const r = el.getBoundingClientRect(), image = el.querySelector('img').getBoundingClientRect(), author = el.querySelector('.contour-author').getBoundingClientRect();
      return { width: r.width, height: r.height, imageRight: image.right, authorTop: author.top,
        authorStyle: [getComputedStyle(el.querySelector('.contour-author')).textOverflow, getComputedStyle(el.querySelector('.contour-author')).whiteSpace],
        actions: [...el.querySelectorAll('.contour-action')].map(button => {
          const b = button.getBoundingClientRect(), svg = button.querySelector('svg').getBoundingClientRect(), path = button.querySelector('path'), bounds = path.getBBox();
          const centre = new DOMPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2).matrixTransform(path.getScreenCTM());
          return { x: b.x, y: b.y, right: b.right, bottom: b.bottom, width: b.width, height: b.height,
            svgX: svg.x + svg.width / 2 - b.x - b.width / 2, svgY: svg.y + svg.height / 2 - b.y - b.height / 2,
            inkX: centre.x - b.x - b.width / 2, inkY: centre.y - b.y - b.height / 2 };
        }) };
    });
    assert.ok(Math.abs(metrics.height - expectedHeight) < .1, JSON.stringify(metrics));
    const [edit, remove] = metrics.actions;
    assert.equal(edit.x, remove.x); assert.equal(edit.width, touch ? 40 : 32); assert.equal(edit.height, touch ? 40 : 32);
    assert.ok(remove.y >= edit.bottom && remove.y - edit.bottom <= 1);
    assert.ok(metrics.imageRight + 6 <= edit.x); assert.ok(metrics.authorTop >= remove.bottom);
    for (const action of metrics.actions) for (const key of ['svgX', 'svgY', 'inkX', 'inkY']) assert.ok(Math.abs(action[key]) < .1, JSON.stringify(action));
    assert.notEqual(metrics.authorStyle[0], 'ellipsis'); assert.notEqual(metrics.authorStyle[1], 'nowrap');
    assert.equal(await item.getByRole('button', { name: 'Редактировать контур' }).isDisabled(), true);
    assert.equal(await item.getByRole('button', { name: 'Удалить контур' }).isDisabled(), n === 2);
    assert.equal(await item.locator('.contour-actions').evaluate(el => getComputedStyle(el).opacity), '1');
  }
  assert.equal(await card(h.page, 1).locator('.contour-author').getAttribute('title'), 'Created: Китченко Денис Сергеевич');
  if (process.env.CONTOUR_SCREENSHOT_DIR) { await card(h.page, 1).focus(); await card(h.page, 1).screenshot({ path: path.join(process.env.CONTOUR_SCREENSHOT_DIR, `creator-${theme}-${width}-${touch}.png`) }); }
});
test('creator names retain surnames and initials without ellipses or invented initials', async t => {
  const inputs = ['  Китченко   Денис  ', 'Иванова-Петрова Анна', 'Сидорова', 'Автор 7', '', 'ОченьДлиннаяСохранённаяФамилия Андрей'];
  const expected = ['Created: Китченко Д.', 'Created: Иванова-Петрова А.', 'Created: Сидорова', 'Created: Автор 7', 'Created: Автор не указан', 'Created: ОченьДлиннаяСохранённаяФамилия А.'];
  const h = await setup(t, { items: inputs.map((author_name, i) => contour(i + 1, { author_name })) }); await h.open();
  assert.deepEqual(await h.page.locator('.contour-author').allTextContents(), expected);
  for (const label of expected) assert.doesNotMatch(label, /…|\.{3}/);
  assert.deepEqual(mutationCalls(h.calls), []);
});

for (const theme of ['light', 'dark']) test(`custom uniform and noise opacity follows 0/30/100 without resetting intensity: ${theme}`, async t => {
  const id = contour(1).id, patterns = { [theme]: { selected: id, patterns: { [id]: { scale: 100, intensity: 50, variable: false, seed: 71 } } } };
  const h = await setup(t, { theme, items: [contour(1)], patterns }); await h.open(); await choose(h, 1);
  let mask;
  for (const intensity of [0, 30, 100]) {
    await h.page.locator('#patternIntensity').fill(String(intensity)); await idle(h.page);
    for (const variable of [true, false]) {
      await h.page.locator('#patternVariable').setChecked(variable); await idle(h.page);
      assert.equal(await h.page.locator('#patternIntensity').isEnabled(), true);
      assert.equal(await h.page.locator('#patternIntensity').inputValue(), String(intensity));
      const paint = await composition(h.page);
      assert.equal(Number(paint.opacity), intensity / 100);
      const stats = await h.page.evaluate(async svg => {
        const parsed = new DOMParser().parseFromString(svg, 'image/svg+xml');
        const mask = parsed.querySelector('mask')?.outerHTML || null;
        const image = new Image(); image.src = 'data:image/svg+xml,' + encodeURIComponent(svg); await image.decode();
        const canvas = new OffscreenCanvas(image.naturalWidth, image.naturalHeight), ctx = canvas.getContext('2d'); ctx.drawImage(image, 0, 0);
        const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data, alphas = new Set(); let max = 0;
        for (let i = 0; i < pixels.length; i += 4) { max = Math.max(max, pixels[i + 3]); if (pixels[i + 3] > 0) alphas.add(pixels[i + 3]); }
        return { mask, max, levels: alphas.size };
      }, paint.svg);
      assert.ok(stats.max <= Math.ceil(intensity * 2.55));
      if (!intensity) assert.equal(stats.max, 0);
      else { assert.ok(stats.max > 0); if (variable) assert.ok(stats.levels > 3); }
      assert.equal(!!stats.mask, variable);
      if (variable) { mask ??= stats.mask; assert.equal(stats.mask, mask); }
      assert.equal(h.preferences.background_patterns[theme].patterns[id].seed, 71);
      assert.equal(h.preferences.background_patterns[theme].patterns[id].intensity, intensity);
    }
  }
  await h.page.locator('#patternVariable').check();
  await h.page.locator('#patternIntensity').fill('30'); await idle(h.page);
  assert.equal(Number((await composition(h.page)).opacity), .3);
  assert.equal(h.preferences.background_patterns[theme].patterns[id].intensity, 30);
  await h.page.locator('#patternVariable').uncheck();
  assert.equal(await h.page.locator('#patternIntensity').inputValue(), '30');
  const edits = h.calls.filter(call => call.method === 'PATCH' && call.body.background_patterns);
  assert.ok(edits.some(call => call.body.background_patterns[theme]?.patterns?.[id]?.intensity === 30));
});
