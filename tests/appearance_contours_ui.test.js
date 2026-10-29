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
  viewport = { width: 1440, height: 1100 }, listError = 0, imageError = false, patterns, server, clock = false } = {}) {
  const context = await browser.newContext({ viewport, hasTouch: touch });
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
  assert.equal(await card(page, 9).locator('.contour-author').textContent(), 'Made by <b>Автор</b>');
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
  assert.equal(await page.getByRole('button', { name: 'Создать свой узор' }).isEnabled(), true);
  assert.match(await page.locator('#contourCreateHint').textContent(), /Сохранение пока недоступно/);
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
  const boxes = await card(h.page, 1).locator('.contour-action').evaluateAll(buttons => buttons.map(button => { const r = button.getBoundingClientRect(); return [r.x, r.right]; }));
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

for (const width of [1440, 390]) test(`few cards and empty library have no inner scrollbar: ${width}`, async t => {
  const h = await setup(t, { viewport: { width, height: 1100 }, items: [contour(1)] }); await h.open();
  for (const empty of [false, true]) {
    if (empty) {
      h.state.items = [];
      await h.page.locator('.appearance-pattern summary').click();
      await h.open();
    }
    const metrics = await h.page.locator('.pattern-library').evaluate(el => ({ height: el.clientHeight, scroll: el.scrollHeight,
      width: el.clientWidth, scrollWidth: el.scrollWidth, overflow: getComputedStyle(el).overflowY, radius: getComputedStyle(el).borderRadius }));
    assert.equal(metrics.height, metrics.scroll);
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
  await h.page.getByRole('button', { name: 'Создать свой узор' }).tap();
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
    images: root.querySelectorAll('image').length, width: Number(root.querySelector('image')?.getAttribute('width')), opacity: root.querySelector('svg > g')?.getAttribute('opacity'),
    size: document.body.style.getPropertyValue('--desk-pattern-size') };
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
  assert.equal(paint.width, 162);
  assert.ok(paint.transforms.every(transform => /translate\([\d.]+ [\d.]+\) rotate\(47\)$/.test(transform)));
  if (rings) {
    const positions = paint.transforms.map(value => value.match(/translate\(([\d.]+) ([\d.]+)\)/).slice(1).map(Number));
    assert.equal(new Set(positions.map(point => point[0])).size, rings * 2 + 1);
    assert.equal(new Set(positions.map(point => point[1])).size, rings * 2 + 1);
    assert.ok(Math.abs(positions[1][0] - positions[0][0] - 162 * 1.7) < 1e-6);
  }
  assert.match(paint.svg, new RegExp(`flood-color="${theme === 'dark' ? 'white' : 'black'}"`));
  assert.equal(h.calls.filter(c => c.path === `${API}/${contour(1).id}/image`).length, 1, 'preview and background reuse the bitmap');
  const size = Number.parseFloat(paint.size);
  await h.page.locator('#patternScale').fill('150'); await idle(h.page);
  assert.ok(Math.abs(Number.parseFloat((await composition(h.page)).size) - size * 1.5) < 1e-6);
  await h.page.locator('#patternIntensity').fill('80'); await idle(h.page);
  assert.equal((await composition(h.page)).opacity, '0.08');
  await h.page.locator('#patternVariable').check(); await idle(h.page);
  const seed = await h.page.locator('.pattern-code span').textContent();
  assert.match((await composition(h.page)).svg, new RegExp(`seed="${seed}"`));
  assert.equal(h.calls.filter(c => c.path === `${API}/${contour(1).id}/image`).length, 1);
  const actual = await h.page.locator('#appContent').evaluate(el => getComputedStyle(el).backgroundImage);
  assert.match(actual, /data:image\/svg\+xml/);
  if (theme === 'light' && rings === 2) {
    await h.page.evaluate(() => {
      const probe = document.createElement('div'); probe.id = 'contourPaintProbe'; probe.className = 'panel';
      probe.style.cssText = 'position:fixed;left:0;top:0;width:500px;height:500px;z-index:100;border:0;border-radius:0;background-color:var(--desk-surface)';
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
