// Real scene assets and native forms; HTTP is isolated from the local database.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });
const template = fs.readFileSync('api/app/templates/login.html', 'utf8');
const render = (error = '') => template.replace('{{NEXT_VALUE}}', '/profile').replace('{{LOGIN_VALUE}}', '').replace('{{LOGIN_ERROR}}', error);
async function setup(t, options = {}) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce', ...options });
  t.after(() => page.close());
  const calls = [], assets = [], errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('requestfailed', request => errors.push(request.url()));
  t.after(() => assert.deepEqual(errors, []));
  await page.addInitScript(() => {
    window.drawnGlyphs = [];
    const fill = CanvasRenderingContext2D.prototype.fillText;
    CanvasRenderingContext2D.prototype.fillText = function(text, ...args) { window.drawnGlyphs.push(text); return fill.call(this, text, ...args); };
  });
  await page.route('**/*', route => {
    const request = route.request(), url = new URL(request.url()), pathname = decodeURIComponent(url.pathname);
    if (pathname.startsWith('/static/')) {
      assets.push(pathname);
      const file = path.resolve('api/app', pathname.slice(1));
      return route.fulfill({ body: fs.readFileSync(file), contentType: { '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.ttf': 'font/ttf' }[path.extname(file)] });
    }
    if (pathname === '/login') {
      calls.push({ method: request.method(), body: request.postData() });
      return route.fulfill({ status: request.method() === 'POST' ? 401 : 200, contentType: 'text/html', body: render(request.method() === 'POST' ? 'Неверный логин или пароль, либо пользователь отключён.' : '') });
    }
    calls.push({ path: pathname, method: request.method() });
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ theme_preference: 'dark', accent_hue_dark: 153, accent_strength_dark: 80 }) });
  });
  await page.goto('https://desk.test/login');
  await page.evaluate(() => Promise.all([...document.images].map(img => img.decode())));
  return { page, calls, assets };
}
const matrix = (page, name) => page.locator(`.matrix-${name} canvas`);
const canvasImage = locator => locator.evaluate(canvas => canvas.toDataURL());
async function maxPixelDifference(page, first, second) {
  if (first.equals(second)) return 0;
  return page.evaluate(async images => {
    const data = await Promise.all(images.map(async base64 => {
      const image = new Image(); image.src = `data:image/png;base64,${base64}`; await image.decode();
      const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
      const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
      return context.getImageData(0, 0, image.width, image.height).data;
    }));
    return data[0].reduce((maximum, value, i) => Math.max(maximum, Math.abs(value - data[1][i])), 0);
  }, [first.toString('base64'), second.toString('base64')]);
}

test('empty displays contain no labels; ordinary logins fit without horizontal shifting', async t => {
  const { page } = await setup(t);
  for (const name of ['login', 'password']) {
    assert.equal(await matrix(page, name).evaluate(canvas => canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data.some((value, index) => index % 4 === 3 && value > 0)), false);
  }
  for (const [width, height] of [[1920,1080], [1440,900], [390,844], [320,568]]) {
    await page.setViewportSize({ width, height });
    for (const value of ['administrator.test', 'Сергей.Иванов123']) {
      await page.locator('#loginInput').fill(value);
      const focused = await canvasImage(matrix(page, 'login'));
      await page.locator('#loginInput').blur();
      assert.ok(await canvasImage(matrix(page, 'login')) === focused, `${width}: ${value} must fit fully`);
    }
  }
  await page.locator('#loginInput').fill('');
  assert.equal(await matrix(page, 'login').evaluate(canvas => canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data.some((value, index) => index % 4 === 3 && value > 0)), false);
});



// Includes native browser chrome/zoom equivalents and narrow landscape screens.
test('fullscreen reference, identical layer geometry, accessible inputs and lamps across viewports', async t => {
  const { page, assets } = await setup(t);
  for (const [width, height] of [[1920,1080], [1440,900], [2560,1080], [390,844], [320,568], [844,390], [640,360]]) {
    await page.setViewportSize({ width, height });
    for (const theme of ['light', 'dark']) {
      await page.evaluate(theme => { document.body.dataset.theme = theme; }, theme);
      const geometry = await page.evaluate(() => {
        const rect = element => { const r = element.getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; };
        return { scroll: document.documentElement.scrollWidth, width: innerWidth,
          layers: [...document.querySelectorAll('.scene-layer:not([hidden])')].map(rect),
          people: getComputedStyle(document.querySelector('.scene-people')).opacity,
          lamps: [...document.querySelectorAll('.lamp-hotspot')].filter(el => el.tabIndex === 0).length };
      });
      assert.equal(geometry.scroll, width);
      for (const layer of geometry.layers) assert.deepEqual(layer, geometry.layers[0]);
      assert.ok(Math.abs(geometry.layers[0][2] / geometry.layers[0][3] - 1672 / 941) < .001);
      assert.ok(geometry.layers[0][2] >= width && geometry.layers[0][3] >= height - .1);
      assert.equal(geometry.people, theme === 'light' ? '1' : '0');
      assert.ok(geometry.lamps > 0, `${width}x${height}: visible keyboard lamp`);
      for (const selector of ['#loginInput', '#passwordInput', '.login-submit']) {
        const control = page.locator(selector);
        const r = await control.boundingBox();
        assert.ok(r.x >= 0 && r.x + r.width <= width && r.y >= 0 && r.y + r.height <= height, `${width}x${height}: ${selector} ${JSON.stringify(r)}`);
        assert.ok(await control.evaluate(el => { const r = el.getBoundingClientRect(); return el === document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2); }));
      }
    }
  }
  assert.equal(await page.title(), 'ES DESK+');
  assert.equal(await page.locator('label').count(), 0);
  assert.equal(assets.some(asset => asset.includes('preview') || asset.includes('theme.js') || asset.includes('mask') || asset.includes('overlay')), false);
  for (const name of ['неоновая_киберпанк_студия_es_desk.png', 'esdesk_light_people.png']) assert.ok(assets.some(asset => asset.endsWith(name)), name);
});

test('lamp hotspots toggle only the local theme, with identical scenery and no preference writes', async t => {
  const { page, calls } = await setup(t);
  await page.evaluate(() => { document.activeElement.blur(); Math.random = () => { throw new Error('Login must not randomize lighting'); }; });
  const dark = await page.screenshot();
  await page.locator('.lamp-large').click();
  assert.equal(await page.locator('body').getAttribute('data-theme'), 'light');
  await page.evaluate(() => document.activeElement.blur());
  assert.ok(!(await page.screenshot()).equals(dark), 'light theme must show the corrected people layer');
  await page.locator('.lamp-left').focus(); await page.keyboard.press('Space');
  assert.equal(await page.locator('body').getAttribute('data-theme'), 'dark');
  await page.locator('.lamp-right').click(); await page.locator('.lamp-desk').click();
  assert.equal(await page.locator('.lamp-left').getAttribute('aria-pressed'), 'false');
  assert.equal(calls.length, 1);
  assert.deepEqual(await page.evaluate(() => [localStorage.length, sessionStorage.length]), [0, 0]);
  assert.equal(await page.locator('body').getAttribute('style'), null);
});

test('metal lettering comes only from the reference and remains unchanged between themes', async t => {
  const { page } = await setup(t);
  const plane = await page.locator('.login-scene-plane').boundingBox();
  const clip = { x: plane.x + plane.width * 680 / 1672, y: plane.y + plane.height * 85 / 941,
    width: plane.width * 320 / 1672, height: plane.height * 83 / 941 };
  const dark = await page.screenshot({ clip });
  await page.evaluate(() => { document.body.dataset.theme = 'light'; });
  // A transparent composited layer can change isolated rounded channels by one step.
  assert.ok(await maxPixelDifference(page, await page.screenshot({ clip }), dark) <= 1, 'metal lettering must not change');
  assert.equal(await page.locator('h1').textContent(), 'ES DESK+');
  assert.equal(await page.locator('h1').evaluate(el => getComputedStyle(el).clipPath), 'inset(50%)');
});

test('LED focus uses the blinking caret alone; hit area stays inside the printed metal panel', async t => {
  const { page } = await setup(t);
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  for (const [width, height] of [[1920,1080], [1440,900], [2560,1080], [390,844], [320,568], [844,390], [640,360]]) {
    await page.setViewportSize({ width, height });
    const plane = await page.locator('.login-scene-plane').boundingBox();
    const button = page.locator('.login-submit');
    const checkPanel = async () => {
      const r = await button.boundingBox();
      assert.ok(r.x >= plane.x + plane.width * 652 / 1672 && r.x + r.width <= plane.x + plane.width * 1018 / 1672, `${width}: panel width`);
      assert.ok(r.y >= plane.y + plane.height * 372 / 941 && r.y + r.height <= plane.y + plane.height * 432 / 941, `${width}: panel height`);
    };
    await checkPanel();
    const rect = await button.boundingBox();
    await page.mouse.move(rect.x + rect.width / 2, rect.y + rect.height / 2); await page.mouse.down();
    await page.waitForTimeout(100); await checkPanel(); await page.mouse.up();
    for (const selector of ['#loginInput', '#passwordInput']) {
      await page.locator(selector).focus();
      assert.equal(await page.locator(selector).evaluate(el => getComputedStyle(el).outlineStyle), 'none');
      assert.equal(await page.locator(selector).evaluate(el => getComputedStyle(el.parentElement, '::after').animationName), 'matrix-blink');
    }
  }
});

test('corrected people asset uses the original canvas without CSS offsets or masking', async t => {
  const { page } = await setup(t);
  const state = await page.locator('.scene-people').evaluate(el => ({ hidden: el.hidden, width: el.naturalWidth, height: el.naturalHeight,
    mask: getComputedStyle(el).maskImage, transform: getComputedStyle(el).transform, src: new URL(el.src).pathname }));
  assert.equal(state.hidden, false); assert.equal(state.width, 1672); assert.equal(state.height, 941);
  assert.equal(state.mask, 'none'); assert.equal(state.transform, 'none');
  assert.ok(state.src.endsWith('/esdesk_light_people.png'));
});

test('theme changes only the five head regions, leaving room, crowns and chairs intact', async t => {
  const { page } = await setup(t);
  await page.evaluate(() => document.activeElement.blur());
  const plane = await page.locator('.login-scene-plane').boundingBox();
  const dark = await page.screenshot();
  await page.locator('.lamp-large').click(); await page.evaluate(() => document.activeElement.blur());
  const light = await page.screenshot();
  const changes = await page.evaluate(async ({ images, plane }) => {
    const pixels = await Promise.all(images.map(async data => {
      const image = new Image(); image.src = `data:image/png;base64,${data}`; await image.decode();
      const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
      const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
      return { data: context.getImageData(0, 0, image.width, image.height).data, width: image.width };
    }));
    const boxes = [[321,521,72,76],[568,517,80,86],[795,503,79,92],[1039,518,76,81],[1293,518,77,82]];
    const count = [0,0,0,0,0]; let outside = 0;
    for (let i = 0; i < pixels[0].data.length; i += 4) {
      if (Math.max(...[0,1,2].map(c => Math.abs(pixels[0].data[i+c] - pixels[1].data[i+c]))) <= 1) continue;
      const x = ((i/4) % pixels[0].width - plane.x) / plane.width * 1672;
      const y = (Math.floor(i/4/pixels[0].width) - plane.y) / plane.height * 941;
      const box = boxes.findIndex(([bx,by,w,h]) => x >= bx && x <= bx+w && y >= by && y <= by+h);
      if (box < 0) outside++; else count[box]++;
    }
    return { count, outside };
  }, { images: [dark.toString('base64'), light.toString('base64')], plane });
  assert.equal(changes.outside, 0, 'only heads may change');
  assert.ok(changes.count.every(count => count > 100));
});


test('dot matrix renders Latin/Cyrillic, selection and silent password-manager assignments; passwords always masked', async t => {
  const { page } = await setup(t);
  const input = page.locator('#loginInput'), password = page.locator('#passwordInput');
  assert.equal(await input.evaluate(el => el === document.activeElement), true);
  await input.fill('Test.ЖЩЁЯю123');
  assert.ok((await page.evaluate(() => window.drawnGlyphs)).includes('Ж'));
  assert.ok((await page.evaluate(() => window.drawnGlyphs)).includes('T'));
  const original = await canvasImage(matrix(page, 'login'));
  await input.press('Control+A');
  assert.notEqual(await canvasImage(matrix(page, 'login')), original);
  await input.press('End'); await input.press('Shift+ArrowLeft');
  assert.equal(await input.evaluate(el => el.selectionStart), 'Test.ЖЩЁЯю123'.length - 1);
  await input.fill('ДлинныйЛогин'.repeat(12)); await input.press('End');
  const caret = await page.locator('.matrix-login').evaluate(el => ({ x: parseFloat(el.style.getPropertyValue('--matrix-caret')), width: el.clientWidth }));
  assert.ok(caret.x >= 16 && caret.x <= caret.width - 16);
  await input.evaluate(el => { el.value = 'Autofill.Юлия'; }); // Managers may not dispatch input/change.
  await page.waitForFunction(() => window.drawnGlyphs.includes('Ю'));
  const updated = await canvasImage(matrix(page, 'login'));
  assert.notEqual(updated, original);
  await page.evaluate(() => { window.drawnGlyphs.length = 0; });
  await password.fill('secret'); await password.blur();
  const maskA = await canvasImage(matrix(page, 'password'));
  await password.fill('пароль'); await password.blur();
  assert.equal(await canvasImage(matrix(page, 'password')), maskA);
  assert.deepEqual(await page.evaluate(() => window.drawnGlyphs), ['●']);
  assert.equal(await password.getAttribute('type'), 'password');
  assert.equal(await password.inputValue(), 'пароль');
  assert.equal(await page.locator('.matrix-password').evaluate(el => getComputedStyle(el.querySelector('input')).webkitTextFillColor), 'rgba(0, 0, 0, 0)');
  assert.equal(await input.getAttribute('autocomplete'), 'username');
  assert.equal(await password.getAttribute('autocomplete'), 'current-password');
});

test('real clipboard paste, native tab order, Enter submit and accessible server error', async t => {
  const { page, calls } = await setup(t);
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
  const clipboard = await page.evaluate(() => navigator.clipboard.readText());
  try {
    await page.evaluate(() => navigator.clipboard.writeText('Сергей.Test'));
    await page.locator('#loginInput').focus(); await page.keyboard.press('Control+V');
    assert.equal(await page.locator('#loginInput').inputValue(), 'Сергей.Test');
    await page.keyboard.press('Tab');
    assert.equal(await page.locator('#passwordInput').evaluate(el => el === document.activeElement), true);
    await page.evaluate(() => navigator.clipboard.writeText('test-secret'));
    await page.keyboard.press('Control+V');
    assert.equal(await page.locator('#passwordInput').inputValue(), 'test-secret');
    await page.keyboard.press('Tab');
    assert.equal(await page.locator('.login-submit').evaluate(el => el === document.activeElement), true);
    await Promise.all([page.waitForNavigation(), page.keyboard.press('Enter')]);
    const form = new URLSearchParams(calls.find(call => call.method === 'POST').body);
    assert.equal(form.get('username'), 'Сергей.Test'); assert.equal(form.get('password'), 'test-secret'); assert.equal(form.get('next'), '/profile');
    assert.match(await page.locator('[role="alert"]').textContent(), /Неверный логин/);
    assert.equal(await page.locator('#passwordInput').inputValue(), '');
    assert.equal(await page.locator('#passwordInput').getAttribute('aria-invalid'), 'true');
    assert.equal(await page.locator('#passwordInput').evaluate(el => el === document.activeElement), true);
  } finally { await page.evaluate(value => navigator.clipboard.writeText(value), clipboard); }
});

test('browser autofill styling stays below the visible dot-matrix canvas', async t => {
  const { page } = await setup(t);
  const session = await page.context().newCDPSession(page);
  await session.send('DOM.enable'); await session.send('CSS.enable');
  const { root } = await session.send('DOM.getDocument');
  for (const selector of ['#loginInput', '#passwordInput']) {
    const { nodeId } = await session.send('DOM.querySelector', { nodeId: root.nodeId, selector });
    await session.send('CSS.forcePseudoState', { nodeId, forcedPseudoClasses: ['autofill'] });
    await page.locator(selector).evaluate(el => { el.value = 'Auto.Юлия'; });
  }
  await page.waitForTimeout(200);
  assert.equal(await page.locator('#loginInput').evaluate(el => el.matches(':autofill')), true);
  const before = await canvasImage(matrix(page, 'login'));
  await page.locator('#loginInput').evaluate(el => { el.value = 'After.Жанна'; });
  await page.waitForFunction(() => window.drawnGlyphs.includes('Ж'));
  assert.notEqual(await canvasImage(matrix(page, 'login')), before);
  const layers = await matrix(page, 'login').evaluate(el => ({ canvas: getComputedStyle(el).zIndex, input: getComputedStyle(el.parentElement.querySelector('input')).zIndex }));
  assert.equal(layers.canvas, '1'); assert.equal(layers.input, 'auto');
});

test('metal button physically depresses without replacing native submit', async t => {
  const { page } = await setup(t);
  const button = page.locator('.login-submit');
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.evaluate(() => { window.pressEvents = 0; document.querySelector('.login-submit').addEventListener('login-metal-press', () => window.pressEvents++); });
  const before = await button.boundingBox();
  await page.mouse.move(before.x + before.width / 2, before.y + before.height / 2); await page.mouse.down();
  await page.waitForTimeout(120);
  assert.ok((await button.boundingBox()).y > before.y + 2);
  assert.equal(await page.evaluate(() => window.pressEvents), 1);
  await page.mouse.up(); await page.waitForTimeout(120);
  assert.deepEqual(await button.boundingBox(), before);
  assert.equal(await button.textContent(), 'LOG IN');
});


test('authenticated application restores saved global theme after local scene preview', async t => {
  const { page, calls } = await setup(t);
  await page.locator('.lamp-large').click();
  await page.route('**/profile', route => route.fulfill({ contentType: 'text/html', body: '<body><script src="/static/theme.js?v=20261027-login-v2"></script></body>' }));
  await page.goto('https://desk.test/profile');
  await page.waitForFunction(() => document.body.dataset.theme === 'dark' && appearance.accent_hue_dark === 153);
  assert.deepEqual(calls.filter(call => call.path), [{ path: '/api/v1/users/me/preferences', method: 'GET' }]);
});

test('visual snapshots for desktop and mobile in both themes', { skip: !process.env.LOGIN_SCREENSHOT_DIR }, async t => {
  const { page } = await setup(t);
  for (const [width, height] of [[1920,1080], [1440,900], [390,844]]) {
    await page.setViewportSize({ width, height });
    for (const theme of ['dark', 'light']) {
      await page.evaluate(theme => { document.body.dataset.theme = theme; }, theme);
      await page.locator('#loginInput').fill(''); await page.locator('#passwordInput').fill(''); await page.locator('#passwordInput').blur();
      await page.screenshot({ path: path.join(process.env.LOGIN_SCREENSHOT_DIR, `scene-${width}-${theme}-empty.png`), fullPage: true });
      await page.locator('#loginInput').fill('administrator.test');
      await page.locator('#passwordInput').fill('test-password'); await page.locator('#passwordInput').blur();
      await page.screenshot({ path: path.join(process.env.LOGIN_SCREENSHOT_DIR, `scene-${width}-${theme}.png`), fullPage: true });
    }
  }
});
