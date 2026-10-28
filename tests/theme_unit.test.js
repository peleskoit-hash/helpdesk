const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const tick = () => new Promise(resolve => setImmediate(resolve));

function harness({ delayLoad = false, delaySession = false } = {}) {
  let server = { appearance_revision: '0', theme_preference: 'light', interface_hue_light: 259, interface_intensity_light: 50, interface_hue_dark: 259, interface_intensity_dark: 50, accent_hue_light: 259, accent_hue_dark: 259, accent_strength_light: 24, accent_strength_dark: 81 };
  const writes = [], pending = [], requests = [];
  const windowEvents = {};
  const writers = new Map();
  let writerCount = 0;
  let releaseLoad, completeSession;
  const tokens = {};
  const context = vm.createContext({
    document: { readyState: 'complete', body: { dataset: {}, toggleAttribute() {}, style: { setProperty(key, value) { tokens[key] = value; } } },
      getElementById: () => null, querySelectorAll: () => [] },
    window: { addEventListener: (name, handler) => { windowEvents[name] = handler; } }, localStorage: { removeItem() {} },
    fetch: async (url, options = {}) => {
      requests.push(new Request(`http://desk.test${url}`, options));
      if (url.endsWith('/appearance-session')) {
        const writer_id = `00000000-0000-4000-8000-${String(++writerCount).padStart(12, '0')}`;
        const initial = options.body ? JSON.parse(options.body) : null;
        const complete = () => {
          const accepted = initial?.expected_revision === server.appearance_revision;
          if (accepted) Object.assign(server, initial.preferences);
          writers.set(writer_id, accepted ? initial.sequence : 0);
          server.appearance_revision = String(Number(server.appearance_revision) + 1);
          return { ok: true, json: async () => ({ writer_id, preferences: { ...server } }) };
        };
        if (delaySession) return new Promise(resolve => {
          completeSession = (deliverResponse = true) => {
            const response = complete();
            if (deliverResponse) resolve(response);
          };
        });
        return complete();
      }
      if (options.method !== 'PATCH') {
        if (delayLoad) await new Promise(resolve => { releaseLoad = resolve; });
        return { ok: true, json: async () => ({ ...server }) };
      }
      const { appearance_write: write, ...patch } = JSON.parse(options.body);
      writes.push(patch);
      return new Promise(resolve => pending.push((ok = true) => {
        if (ok && !writers.has(write.writer_id)) {
          resolve({ ok: false, status: 409, json: async () => ({ detail: {
            error: { code: 'appearance_session_expired' }, preferences: { ...server },
          } }) });
          return;
        }
        if (ok && write.sequence > writers.get(write.writer_id)) {
          Object.assign(server, patch);
          writers.set(write.writer_id, write.sequence);
          server.appearance_revision = String(Number(server.appearance_revision) + 1);
        }
        resolve({ ok, status: ok ? 200 : 503, json: async () => ({ ...server }) });
      }));
    },
  });
  vm.runInContext(fs.readFileSync('api/app/static/theme.js', 'utf8'), context);
  return { tokens, run: code => vm.runInContext(code, context), writes, pending, requests, leave: () => windowEvents.pagehide(), releaseLoad: () => releaseLoad(),
    completeSession: deliverResponse => completeSession(deliverResponse),
    expire: () => writers.clear(), serverState: () => ({ ...server }),
    change: patch => vm.runInContext(`changeAppearance(${JSON.stringify(patch)})`, context),
    state: () => JSON.parse(vm.runInContext('JSON.stringify(appearance)', context)),
  };
}

test('appearance serializes saves, coalesces rapid input and retains independent intensities', async () => {
  const h = harness();
  await tick();
  h.change({ theme_preference: 'dark' });
  await tick();
  h.change({ accent_hue_light: 215, accent_strength_dark: 0 });
  h.change({ accent_strength_dark: 100 });
  assert.equal(h.writes.length, 1);
  assert.equal(h.state().accent_strength_dark, 100);
  h.pending.shift()();
  await tick();
  assert.deepEqual(h.writes[1], { accent_hue_light: 215, accent_strength_dark: 100 });
  assert.equal(h.state().accent_strength_dark, 100);
  h.pending.shift()();
  await tick();
  assert.equal(h.state().theme_preference, 'dark');
  assert.equal(h.state().accent_hue_light, 215);
  assert.equal(h.state().accent_strength_light, 24);
  assert.equal(h.state().accent_strength_dark, 100);
});

test('late preference load preserves local input and server values for untouched fields', async () => {
  const h = harness({ delayLoad: true });
  h.change({ accent_hue_light: 184 });
  h.releaseLoad();
  await tick();
  assert.equal(h.state().accent_hue_light, 184);
  assert.equal(h.state().accent_strength_light, 24);
  assert.deepEqual(h.writes, [{ accent_hue_light: 184 }]);
  h.pending.shift()();
  await tick();
});

test('failed write restores confirmed values while keeping a newer pending choice', async () => {
  const h = harness();
  await tick();
  h.change({ accent_hue_light: 215 });
  await tick();
  h.change({ accent_hue_light: 153 });
  h.pending.shift()(false);
  await tick();
  assert.equal(h.state().accent_hue_light, 153);
  h.pending.shift()();
  await tick();
  h.change({ accent_strength_light: 100 });
  await tick();
  h.pending.shift()(false);
  await tick();
  assert.equal(h.state().accent_hue_light, 153);
  assert.equal(h.state().accent_strength_light, 24);
});

test('first change survives pagehide while registration response never reaches the document', async () => {
  const h = harness({ delaySession: true });
  await tick();
  h.change({ accent_strength_light: 99 });
  await tick();
  const registration = h.requests.find(r => r.method === 'POST');
  assert.ok(registration);
  assert.equal(h.run('appearanceWriterId'), null);
  h.leave();
  // Process the outstanding request without resolving fetch: unloaded JS cannot send a later PATCH.
  h.completeSession(false);
  assert.equal(h.serverState().accent_strength_light, 99);
  assert.equal(h.requests.filter(r => r.method === 'PATCH').length, 0);
  assert.equal(registration.keepalive, true);
  assert.equal(registration.credentials, 'include');
  assert.deepEqual(await registration.json(), { preferences: { accent_strength_light: 99 }, sequence: 1, expected_revision: '0' });
});

test('acknowledged initial snapshot is retained if the duplicate PATCH fails', async () => {
  const h = harness();
  await tick();
  h.change({ accent_strength_light: 99 });
  await tick();
  h.pending.shift()(false);
  await tick();
  assert.equal(h.state().accent_strength_light, 99);
  assert.equal(h.serverState().accent_strength_light, 99);
});

test('full navigation sends the final queued appearance through keepalive', async () => {
  const h = harness();
  await tick();
  h.change({ accent_hue_light: 0 });
  await tick();
  h.change({ accent_strength_light: 0 });
  h.change({ accent_strength_light: 100 });
  assert.equal(h.requests.filter(r => r.method === 'PATCH').length, 1);
  h.leave();
  const [initialRequest, departureRequest] = h.requests.filter(r => r.method === 'PATCH');
  assert.equal(h.pending.length, 2); // pagehide sends without waiting for the first save.
  assert.equal(departureRequest.url, 'http://desk.test/api/v1/users/me/preferences');
  assert.equal(departureRequest.method, 'PATCH');
  assert.equal(departureRequest.keepalive, true);
  assert.equal(departureRequest.credentials, 'include');
  assert.equal(departureRequest.headers.get('content-type'), 'application/json');
  const initial = await initialRequest.json(), departure = await departureRequest.json();
  assert.equal(departure.appearance_write.writer_id, initial.appearance_write.writer_id);
  assert.equal(initial.appearance_write.sequence, 1);
  assert.equal(departure.appearance_write.sequence, 3);
  assert.deepEqual(departure, { accent_hue_light: 0, accent_strength_light: 100,
    appearance_write: { writer_id: initial.appearance_write.writer_id, sequence: 3 } });
  assert.deepEqual(h.writes[1], { accent_hue_light: 0, accent_strength_light: 100 });
  h.pending.shift()();
  await tick();
  h.pending.shift()();
  h.pending.shift()();
  await tick();
  assert.equal(h.state().accent_strength_light, 100);
});

for (const field of ['interface_hue_light', 'interface_intensity_light', 'accent_hue_dark', 'accent_strength_dark']) for (const reverse of [false, true]) {
  test(`pagehide ${field} survives ${reverse ? 'reversed' : 'forward'} processing before any retry`, async () => {
    const h = harness();
    await tick();
    h.change({ [field]: 17 });
    await tick();
    h.change({ [field]: 49 });
    h.change({ [field]: 99 });
    h.leave();
    const [old, latest] = h.pending.splice(0);
    if (reverse) { latest(); old(); } else { old(); latest(); }
    // Assert persistence before promises resume: no retry from an unloaded page is required.
    assert.equal(h.serverState()[field], 99);
    await tick();
    h.pending.shift()(); // A live document may also send the duplicate sequence.
    await tick();
    assert.equal(h.state()[field], 99);
  });
}

test('expired writer restores canonical preferences and renews only for a new change', async () => {
  const h = harness();
  await tick();
  h.change({ accent_hue_light: 215 });
  await tick(); h.pending.shift()(); await tick();
  h.expire();
  h.change({ accent_strength_light: 99 });
  await tick(); h.pending.shift()(); await tick();
  assert.equal(h.state().accent_strength_light, 24);
  assert.equal(h.requests.filter(r => r.method === 'POST').length, 1);
  h.change({ accent_strength_light: 100 });
  await tick(); h.pending.shift()(); await tick();
  assert.equal(h.serverState().accent_strength_light, 100);
  assert.equal(h.requests.filter(r => r.method === 'POST').length, 2);
});

test('AppShell toolbar replacement keeps the native appearance menu usable without duplicate handlers', async t => {
  const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
  const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage();
  const errors = [], writes = [];
  let loads = 0;
  const preferences = { theme_preference: 'light', interface_hue_light: 259, interface_intensity_light: 50, interface_hue_dark: 259, interface_intensity_dark: 50, accent_hue_light: 259, accent_hue_dark: 259, accent_strength_light: 24, accent_strength_dark: 81 };
  page.on('pageerror', error => errors.push(error.message));
  await page.route('http://desk.test/**', route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (path.endsWith('/appearance-session')) return route.fulfill({ contentType: 'application/json',
      body: JSON.stringify({ writer_id: '00000000-0000-4000-8000-000000000001' }) });
    if (path === '/api/v1/users/me/preferences') {
      if (request.method() === 'PATCH') {
        const { appearance_write, ...patch } = request.postDataJSON();
        writes.push(patch);
        Object.assign(preferences, patch);
      } else loads++;
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(preferences) });
    }
    const name = path === '/settings/users' ? 'settings-users' : 'settings-home';
    return route.fulfill({ contentType: 'text/html', body: `<main id="appContent" data-shell-page="${name}">
      <div class="toolbar"><button type="button" id="themeToggleBtn">Appearance</button></div></main>` });
  });
  await page.goto('http://desk.test/settings');
  await page.evaluate(() => {
    window.listenerRegistrations = new WeakMap();
    const add = EventTarget.prototype.addEventListener;
    EventTarget.prototype.addEventListener = function (type, ...args) {
      const counts = listenerRegistrations.get(this) || {};
      counts[type] = (counts[type] || 0) + 1;
      listenerRegistrations.set(this, counts);
      return add.call(this, type, ...args);
    };
  });
  // Execute the actual modules, including AppShell's mount and theme reinitialization.
  await page.addScriptTag({ content: fs.readFileSync('api/app/static/app_shell.js', 'utf8') });
  await page.evaluate(() => {
    HelpdeskAppShell.registerPage('settings-home', () => {});
    HelpdeskAppShell.registerPage('settings-users', () => {});
    window.originalDocument = document;
  });
  await page.addScriptTag({ content: fs.readFileSync('api/app/static/theme.js', 'utf8') });
  await page.waitForFunction(() => document.querySelector('#appearance-accent-intensity')?.value === '24');
  const globalListeners = await page.evaluate(() => Object.fromEntries(
    ['resize', 'scroll', 'pagehide'].map(key => [key, listenerRegistrations.get(window)[key]]),
  ));
  for (const path of ['/settings/users', '/settings']) {
    await page.locator('#themeToggleBtn').click();
    await page.waitForFunction(() => document.querySelector('#appearanceMenu').matches(':popover-open'));
    await page.evaluate(() => {
      window.previousToolbar = document.querySelector('.toolbar');
      window.previousMenu = document.querySelector('#appearanceMenu');
    });
    assert.equal(await page.evaluate(path => HelpdeskAppShell.navigate(path), path), true);
    assert.deepEqual(await page.evaluate(() => ({
      sameDocument: document === originalDocument,
      toolbarDetached: !previousToolbar.isConnected,
      menuDetached: !previousMenu.isConnected,
      replaced: previousMenu !== document.querySelector('#appearanceMenu'),
    })), { sameDocument: true, toolbarDetached: true, menuDetached: true, replaced: true });
    await page.evaluate(() => {
      window.currentMenu = document.querySelector('#appearanceMenu');
      HelpdeskTheme.initThemeToggle();
      HelpdeskTheme.initThemeToggle();
    });
    assert.equal(await page.locator('#appearanceMenu').count(), 1);
    assert.equal(await page.evaluate(() => currentMenu === document.querySelector('#appearanceMenu')), true);
    assert.deepEqual(await page.evaluate(() => ({
      menu: listenerRegistrations.get(currentMenu),
      input: listenerRegistrations.get(currentMenu.querySelector('input')),
      globals: Object.fromEntries(['resize', 'scroll', 'pagehide'].map(key => [key, listenerRegistrations.get(window)[key]])),
    })), { menu: { toggle: 1, click: 1, input: 1 }, input: undefined, globals: globalListeners });
    await page.locator('#themeToggleBtn').click();
    await page.waitForFunction(() => currentMenu.matches(':popover-open'));
    const count = writes.length;
    const theme = preferences.theme_preference === 'light' ? 'dark' : 'light';
    await page.locator(`[data-appearance-theme="${theme}"]`).click();
    await page.waitForFunction(() => !saveInFlight);
    assert.deepEqual(writes.slice(count), [{ theme_preference: theme }]);
    assert.equal(await page.locator('#themeToggleBtn').getAttribute('aria-expanded'), 'true');
    await page.locator('#themeToggleBtn').click();
    await page.waitForFunction(() => !currentMenu.matches(':popover-open'));
  }
  assert.equal(loads, 1);
  assert.deepEqual(errors, []);
});


test('palette contrast across independent hues, intensities and neutral endpoints', async () => {
  const h = harness(); await tick();
  for (const theme of ['light','dark']) for (let hue=0;hue<=360;hue+=30) {
    for (const strength of [0,50,100]) for (const accentHue of [0,60,120,180,240,300,360]) for (const accentStrength of [0,50,100]) {
      h.run(`appearance = {...APPEARANCE_DEFAULTS, theme_preference: '${theme}', interface_hue_${theme}: ${hue}, interface_intensity_${theme}: ${strength}, accent_hue_${theme}: ${accentHue}, accent_strength_${theme}: ${accentStrength}}; applyTheme('${theme}')`);
      const rgb = key => h.tokens[`--desk-${key}`].match(/[\d.]+/g).map(Number);
      const ratio = (a,b) => h.run(`contrast(${JSON.stringify(a)},${JSON.stringify(b)})`);
      for (const bg of ['bg','surface','soft','backdrop','hover','tint']) for (const fg of ['text','muted','focus']) {
        assert.ok(ratio(rgb(fg),rgb(bg)) >= 4.5, `${theme}/${hue}/${strength}/${accentHue}/${accentStrength}: ${fg}/${bg}`);
      }
      assert.ok(ratio(rgb('on-tint'),rgb('tint')) >= 4.5);
      const onAccent = h.tokens['--desk-on-accent'] === '#ffffff' ? [255,255,255] : [0,0,0];
      assert.ok(ratio(onAccent,rgb('primary')) >= 4.5);
      for (const bg of ['bg','surface','soft']) assert.ok(ratio(rgb('border'),rgb(bg)) >= 1.35);
      if (strength === 0) for (const key of ['bg','surface','soft','backdrop','text','muted','border']) {
        const [r,g,b] = rgb(key); assert.equal(r,g); assert.equal(g,b);
      }
      if (accentStrength === 0) { const [r,g,b]=rgb('accent'); assert.equal(r,g); assert.equal(g,b); }
    }
  }
});

test('legacy colors restore to matching hue without losing independent intensities', async () => {
  const h=harness(); await tick();
  for (const [color,hue] of Object.entries({purple:259,blue:215,red:350,green:153,orange:29,teal:184,mono:0})) {
    const state=JSON.parse(h.run(`JSON.stringify(normalizeAppearance({accent_preference:'${color}',accent_intensity_light:17,accent_intensity_dark:99}))`));
    assert.equal(state.accent_hue_light,hue); assert.equal(state.accent_hue_dark,hue);
    assert.equal(state.accent_strength_light,color==='mono'?0:17);
    assert.equal(state.accent_strength_dark,color==='mono'?0:99);
  }
});


test('default surface hierarchy and primary colors remain consistent', async () => {
  const h=harness(); await tick();
  for (const [theme,expected] of Object.entries({
    light: {bg:'rgb(235, 232, 243)',surface:'rgb(245, 243, 249)',soft:'rgb(240, 238, 246)',accent:'rgb(119, 96, 168)',primary:'rgb(98, 74, 145)'},
    dark: {bg:'rgb(23, 27, 40)',surface:'rgb(32, 37, 53)',soft:'rgb(38, 43, 61)',accent:'rgb(182, 160, 226)',primary:'rgb(218, 199, 250)'},
  })) {
    h.run(`appearance={...APPEARANCE_DEFAULTS}; applyTheme('${theme}')`);
    for (const [key,value] of Object.entries(expected)) assert.equal(h.tokens[`--desk-${key}`],value);
    assert.equal(Object.keys(h.tokens).some(key => /danger|success|warning/.test(key)),false);
  }
});
