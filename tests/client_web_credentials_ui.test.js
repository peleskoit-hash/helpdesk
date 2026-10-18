const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
const generationWire = [];
const profileSaveWire = [];
test.before(async () => { browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true }); });
test.after(async () => {
  await browser?.close();
  if (process.env.WEB_PASSWORD_CLEAR_WIRE_PATH) fs.writeFileSync(process.env.WEB_PASSWORD_CLEAR_WIRE_PATH, JSON.stringify(profileSaveWire));
  if (process.env.WEB_CREDENTIALS_WIRE_PATH) fs.writeFileSync(process.env.WEB_CREDENTIALS_WIRE_PATH, JSON.stringify(generationWire));
});
const script = name => fs.readFileSync(`api/app/static/${name}.js`, 'utf8');
const login = '[data-custom-field-key="custom_web_login"]';
const password = '[data-custom-field-key="custom_web_password"]';
const fields = [{ key: 'custom_web_login', label: 'WEB login', field_type: 'text', is_system: false, admin_only_edit: true },
  { key: 'custom_web_password', label: 'WEB password', field_type: 'password', is_system: false, admin_only_edit: true }];
const config = { blocks: [{ key: 'tg', title: 'Socials', fields }, { key: 'work', title: 'Work', fields: [
  { key: 'bitrix_login', label: 'Bitrix login', field_type: 'text', is_system: true },
  { key: 'bitrix_password', label: 'Bitrix password', field_type: 'password', is_system: true },
] }], rules: [] };
async function fixture(t, contact, options = {}) {
  const page = await browser.newPage({ viewport: { width: 1500, height: 1200 } });
  t.after(() => page.close());
  await page.clock.install();
  const requests = [], errors = [], dialogs = [];
  const server = { profile: { contact_name: 'Ivan Ivanov', last_name: 'Ivanov', bitrix_login: 'bitrix.current',
    web_login: options.login || null, web_password_set: Boolean(options.saved), bitrix_password_set: true },
    secret: options.saved ? 'Saved-Web-A1!' : null, fail: false, malformed: false, occupied: false, pending: null, hold: false, holdReveal: false,
  };
  page.on('pageerror', error => errors.push(error.message));
  let confirm = true;
  page.on('dialog', async dialog => { dialogs.push(dialog.message()); await (confirm ? dialog.accept() : dialog.dismiss()); });
  const template = fs.readFileSync(`api/app/templates/${contact ? 'management_users' : 'ticket'}.html`, 'utf8')
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, '');
  await page.route('**/*', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (!path.startsWith('/api/')) return route.fulfill({ contentType: 'text/html', body: template });
    const data = request.postDataJSON();
    const wire = { path, method: request.method(), data, headers: request.headers(), body: request.postData() };
    requests.push(wire);
    if (request.method() === 'PATCH') profileSaveWire.push(wire);
    if (path.endsWith('/generate')) generationWire.push(wire);
    let result = {}, status = 200;
    if (path.endsWith('client-card-view-config')) result = config;
    else if (path === '/api/v1/management/users') result = { items: [{ client_id: 'client', display_name: 'Synthetic' }], total: 1 };
    else if (path.endsWith('/generate')) {
      if (server.hold) await new Promise(resolve => { server.pending = resolve; });
      if (server.fail) { status = 422; result = { error: { message: 'Не удалось подготовить логин: заполните имя и фамилию' } }; }
      else if (server.malformed) result = { web_login: 'partial' };
      else result = { web_login: (!data.web_login || data.regenerate)
        ? (data.bitrix_login && !server.occupied ? data.bitrix_login : 'i.ivanov@elitesochi.com') : data.web_login,
        web_password: !data.web_password_set || data.regenerate ? 'Generated-Web-B2!' : null };
    } else if (path.endsWith('/web-password')) {
      if (server.holdReveal) await new Promise(resolve => { server.pending = resolve; });
      result = { web_password: server.secret };
    }
    else if (path.endsWith('/bitrix-password')) result = { bitrix_password: 'Saved-Bitrix-C3!' };
    else if (request.method() === 'PATCH') {
      const custom = data.custom_fields || {};
      if (custom.custom_web_login) server.profile.web_login = custom.custom_web_login;
      if (data.clear_web_password) { server.secret = null; server.profile.web_password_set = false; }
      else if (custom.custom_web_password) { server.secret = custom.custom_web_password; server.profile.web_password_set = true; }
      result = server.profile;
    } else result = server.profile;
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(result) });
  });
  async function load(reload = false) {
    if (reload) await page.reload();
    else await page.goto('http://helpdesk.test/');
    await page.addStyleTag({ content: fs.readFileSync('api/app/static/styles.css', 'utf8') });
    await page.addStyleTag({ content: fs.readFileSync('api/app/static/ticket_client_card.css', 'utf8') });
    await page.addScriptTag({ content: script('ticket_api') });
    await page.addScriptTag({ content: script('client_web_credentials') });
    await page.addScriptTag({ content: script('ticket_client_card') });
    await page.evaluate(({ contact, config }) => {
      const card = window.TicketClientCard;
      const original = card.fillModal;
      card.fillModal = (ctx, profile) => { window.ctx = ctx; return original(ctx, profile); };
      const alert = document.createElement('div'); alert.id = 'testError'; alert.setAttribute('role', 'alert'); document.body.appendChild(alert);
      if (contact) {
        window.HelpdeskSidebar = { init: async () => ({ role: 'admin' }) };
        window.HelpdeskAppShell = { registerPage: (name, init) => { window.initContacts = init; } };
        return;
      }
      const el = {};
      for (const node of document.querySelectorAll('[id]')) el[node.id] = node;
      const ctx = { inlineClient: true, state: { role: 'admin', ticketId: 'ticket', card: { client_id: 'client' }, clientCardViewConfig: config },
        el, isDestroyed: () => false, isBlank: value => value == null || value === '',
        setControlValue: (control, value) => { if (control) control.value = value ?? ''; },
        setStatus() {}, showError: message => { document.querySelector('#testError').textContent = message; }, loadCard: async () => {},
        api: window.TicketApi.api,
      };
      window.ctx = ctx;
    }, { contact, config });
    if (contact) {
      await page.addScriptTag({ content: script('management_users') });
      await page.evaluate(() => window.initContacts({ root: document.body, url: window.location.href }));
    }
  }
  async function open() {
    if (contact) {
      await page.locator('[data-client-id="client"]').click();
      await page.waitForFunction(() => window.ctx && !window.ctx.el.clientModal.classList.contains('hidden'));
    } else await page.evaluate(() => window.TicketClientCard.openClientModal(window.ctx));
  }
  await load();
  await open();
  async function generate() {
    await page.getByRole('button', { name: 'Генерировать WEB', exact: true }).click();
    const modal = page.locator('#clientWebGenerateConfirm');
    await modal.waitFor();
    dialogs.push(await modal.innerText());
    await modal.getByRole('button', { name: confirm ? 'Да' : 'Нет', exact: true }).click();
    await page.waitForFunction(() => !Array.from(document.querySelectorAll('button')).find(b => b.textContent === 'Генерировать WEB').disabled);
  }
  return { page, server, requests, errors, dialogs, open, reload: async () => { await load(true); await open(); }, generate, confirm: value => { confirm = value; },
    draft: () => page.evaluate(() => window.ClientWebCredentials.passwordValue(window.ctx)),
    generates: () => requests.filter(r => r.path.endsWith('/generate')),
  };
}
async function enterPassword(page, value) {
  const control = page.locator(password);
  await control.click();
  await control.press('ControlOrMeta+A');
  await control.press('Backspace');
  if (value) await control.pressSequentially(value);
}
for (const contact of [false, true]) {
  test(`${contact ? 'contact' : 'ticket'}: unsolicited autofill never becomes a WEB password draft`, async t => {
    const f = await fixture(t, contact);
    assert.equal(f.server.profile.web_password_set, false);
    assert.equal(await f.page.locator(password).inputValue(), '');
    await f.page.locator(password).evaluate(node => {
      node.value = 'Browser-Site-Password!';
      node.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertReplacementText', data: node.value }));
      node.dispatchEvent(new Event('blur'));
    });
    assert.equal(await f.page.locator(password).inputValue(), '');
    assert.equal(await f.draft(), '');
    assert.equal(await f.page.locator(password).evaluate(n => n.classList.contains('client-card-password')), false);
    const payload = await f.page.evaluate(() => window.TicketClientCard.getModalPayload(window.ctx));
    assert.equal(Object.hasOwn(payload.custom_fields, 'custom_web_password'), false);
    assert.equal(Object.hasOwn(payload, 'clear_web_password'), false);
    await f.page.locator(password).click();
    assert.equal(await f.page.locator(password).inputValue(), '');
    assert.equal(f.requests.filter(r => r.path.endsWith('/web-password')).length, 0);
    assert.deepEqual(await f.page.evaluate(() => ({
      plaintext: window.ctx.state.clientCardWebPasswordReveal?.plaintext,
      timer: window.ctx.state.clientCardWebPasswordReveal?.timerId,
      mask: window.ctx.state.clientCardWebPasswordMask || null,
    })), { plaintext: '', timer: null, mask: null });
  });
  test(`${contact ? 'contact' : 'ticket'}: empty GET stays empty on reopen and page reload`, async t => {
    const f = await fixture(t, contact);
    for (const reload of [false, true]) {
      if (reload) await f.reload();
      else {
        // A previous editor's draft must not survive a new profile response.
        await enterPassword(f.page, 'Abandoned-Draft!');
        await f.page.evaluate(() => window.TicketClientCard.closeClientModal(window.ctx, { force: true }));
        await f.open();
      }
      assert.equal(await f.page.evaluate(() => window.ctx.state.currentClientProfile.web_password_set), false);
      assert.equal(await f.draft(), '');
      assert.equal(await f.page.locator(password).inputValue(), '');
      assert.equal(await f.page.locator(password).evaluate(n => getComputedStyle(n).filter), 'none');
      await f.page.locator(password).click();
      await f.page.locator(login).click();
      await f.page.clock.runFor(5001);
      assert.equal(await f.page.locator(password).inputValue(), '');
    }
    assert.equal(f.requests.filter(r => r.path.endsWith('/web-password')).length, 0);
    assert.deepEqual(f.errors, []);
  });
  test(`${contact ? 'contact' : 'ticket'}: autofill is ignored on the wire, including trusted insertion without user intent`, async t => {
    const f = await fixture(t, contact);
    assert.deepEqual(await f.page.locator(password).evaluate(n => ({
      tag: n.tagName, rows: n.rows, autocomplete: n.autocomplete, spellcheck: n.spellcheck,
      name: n.name, id: n.id, form: n.form?.id || null,
      lp: n.dataset.lpignore, one: n.getAttribute('data-1p-ignore'), bw: n.dataset.bwignore,
      authInputs: n.parentElement.querySelectorAll('input[type="password"]').length,
    })), { tag: 'TEXTAREA', rows: 1, autocomplete: 'off', spellcheck: false, name: '', id: '', form: null,
      lp: 'true', one: 'true', bw: 'true', authInputs: 0 });
    // fill() emits trusted beforeinput/input, but has no keyboard/paste/drop intent.
    await f.page.locator(password).fill('Browser-Autofill!');
    assert.equal(await f.draft(), '');
    assert.equal(await f.page.locator(password).inputValue(), '');
    // Some extensions only assign .value; serialization must still use the draft state.
    await f.page.locator(password).evaluate(n => { n.value = 'Silent-Autofill!'; });
    if (contact) await f.page.locator('#saveClientBtn').click();
    else await f.page.evaluate(() => window.TicketClientCard.saveClientProfile(window.ctx));
    await f.page.waitForFunction(() => window.ctx.el.clientModal.classList.contains('hidden'));
    const sent = f.requests.find(r => r.method === 'PATCH');
    assert.equal(Object.hasOwn(sent.data.custom_fields, 'custom_web_password'), false);
    assert.equal(Object.hasOwn(sent.data, 'clear_web_password'), false);
    assert.equal(f.server.secret, null);
    await f.reload();
    assert.equal(await f.page.locator(password).inputValue(), '');
    assert.equal(await f.page.locator(password).evaluate(n => getComputedStyle(n).filter), 'none');
    assert.deepEqual(f.errors, []);
  });
  test(`${contact ? 'contact' : 'ticket'}: autofill preserves saved and intentional draft state`, async t => {
    const f = await fixture(t, contact, { saved: true });
    await f.page.locator(password).fill('Autofill-Over-Saved!');
    await f.page.waitForFunction(() => window.ctx.state.clientCardWebPasswordReveal?.plaintext === 'Saved-Web-A1!');
    await f.page.locator(login).click();
    assert.equal(await f.draft(), '');
    assert.equal(await f.page.locator(password).getAttribute('data-web-password-dirty'), '0');
    assert.equal(await f.page.locator(password).evaluate(n => n.classList.contains('client-card-password')), true);
    await enterPassword(f.page, 'Intentional-Password!');
    await f.page.locator(password).fill('Autofill-Over-Draft!');
    assert.equal(await f.draft(), 'Intentional-Password!');
    let payload = await f.page.evaluate(() => window.TicketClientCard.getModalPayload(window.ctx));
    assert.equal(payload.custom_fields.custom_web_password, 'Intentional-Password!');
    await enterPassword(f.page, '');
    await f.page.locator(password).fill('Autofill-After-Clear!');
    assert.equal(await f.page.locator(password).inputValue(), '');
    payload = await f.page.evaluate(() => window.TicketClientCard.getModalPayload(window.ctx));
    assert.equal(payload.clear_web_password, true);
    assert.equal(payload.custom_fields.custom_web_password, null);
    assert.deepEqual(f.errors, []);
  });
  test(`${contact ? 'contact' : 'ticket'}: absent profile clears old plaintext, mask and timer through generation transitions`, async t => {
    const f = await fixture(t, contact, { saved: true });
    await f.page.locator(password).click();
    await f.page.waitForFunction(() => window.ctx.state.clientCardWebPasswordReveal?.plaintext === 'Saved-Web-A1!');
    f.server.secret = null;
    f.server.profile.web_password_set = false;
    // Read an actual fresh response while the previous reveal's timer is still active.
    await f.page.evaluate(async () => {
      const ctx = window.ctx;
      const profile = await ctx.api(ctx.state.ticketId
        ? '/api/v1/tickets/ticket/client-profile' : '/api/v1/management/users/client/profile');
      window.TicketClientCard.fillModal(ctx, profile);
    });
    assert.deepEqual(await f.page.evaluate(() => ({
      draft: window.ClientWebCredentials.passwordValue(window.ctx),
      plaintext: window.ctx.state.clientCardWebPasswordReveal.plaintext,
      until: window.ctx.state.clientCardWebPasswordReveal.revealedUntil,
      timer: window.ctx.state.clientCardWebPasswordReveal.timerId,
      mask: window.ctx.state.clientCardWebPasswordMask || null,
    })), { draft: '', plaintext: '', until: 0, timer: null, mask: null });
    await f.page.locator(password).click();
    await f.page.clock.runFor(5001);
    assert.equal(await f.page.locator(password).inputValue(), '');
    assert.equal(f.requests.filter(r => r.path.endsWith('/web-password')).length, 1);
    await f.generate();
    assert.equal(f.server.secret, null); // Generation is still only a draft.
    await f.page.locator(password).click();
    assert.equal(await f.page.locator(password).inputValue(), 'Generated-Web-B2!');
    assert.equal(await f.draft(), 'Generated-Web-B2!');
    assert.equal(f.requests.filter(r => r.path.endsWith('/web-password')).length, 1);
    await enterPassword(f.page, '');
    await f.page.locator(login).click();
    await f.page.locator(password).click();
    await f.page.clock.runFor(5001);
    assert.equal(await f.page.locator(password).inputValue(), '');
    assert.equal(f.requests.filter(r => r.path.endsWith('/web-password')).length, 1);
    assert.equal(await f.page.locator(password).evaluate(n => getComputedStyle(n).filter), 'none');
    assert.deepEqual(f.errors, []);
  });
  const context = contact ? 'contact' : 'ticket';
  test(`${context}: current Bitrix login, generator order, no separate reveal button`, async t => {
    const f = await fixture(t, contact);
    await f.page.locator('#fBitrixLogin').fill('bitrix.edited');
    await f.generate();
    assert.equal(await f.page.locator(login).inputValue(), 'bitrix.edited');
    assert.equal(f.generates()[0].data.bitrix_login, 'bitrix.edited');
    assert.equal(f.generates()[0].headers['content-type'], 'application/json');
    assert.equal(typeof JSON.parse(f.generates()[0].body), 'object');
    assert.equal(await f.draft(), 'Generated-Web-B2!');
    assert.equal(f.requests.filter(r => r.method === 'PATCH').length, 0);
    const order = await f.page.locator(login).evaluate(input => {
      const block = input.parentElement.parentElement;
      return [...block.children].filter(n => n.tagName !== 'DIV').map(n => n.tagName === 'BUTTON' ? n.textContent : n.dataset.clientCardFieldKey);
    });
    assert.deepEqual(order, ['Генерировать WEB', 'custom_web_login', 'custom_web_password']);
    assert.equal(await f.page.getByRole('button', { name: 'Показать', exact: true }).count(), 0);
    assert.deepEqual(f.errors, []);
  });
  test(`${context}: missing editor source uses the current profile instead of explicit null`, async t => {
    const f = await fixture(t, contact);
    await f.page.evaluate(() => { document.querySelector('#fBitrixLogin').remove(); window.ctx.el.fBitrixLogin = null; });
    await f.generate();
    assert.equal(f.generates()[0].data.bitrix_login, 'bitrix.current');
    assert.equal(await f.page.locator(login).inputValue(), 'bitrix.current');
  });
  test(`${context}: manual password survives login-only generation and masks never become values`, async t => {
    const f = await fixture(t, contact);
    await enterPassword(f.page, 'Manual-Web-D4!');
    await f.generate();
    assert.equal(f.generates()[0].data.web_password_set, true);
    assert.equal(f.generates()[0].data.regenerate, false);
    assert.equal(await f.draft(), 'Manual-Web-D4!');
    const payload = await f.page.evaluate(() => window.TicketClientCard.getModalPayload(window.ctx));
    assert.equal(payload.custom_fields.custom_web_password, 'Manual-Web-D4!');
    assert.equal(f.dialogs.length, 1);
    assert.deepEqual(f.errors, []);
  });
  test(`${context}: saved password, password-only generation and confirmation policy`, async t => {
    const f = await fixture(t, contact, { saved: true });
    await f.generate();
    assert.equal(f.generates()[0].data.web_password_set, true);
    assert.equal(await f.draft(), '');
    assert.equal(f.dialogs.length, 1);
    f.confirm(false); await f.generate();
    assert.equal(f.dialogs.length, 2); assert.equal(f.generates().length, 1);
    f.confirm(true); await f.generate();
    assert.equal(f.generates()[1].data.regenerate, true);
    assert.equal(await f.draft(), 'Generated-Web-B2!');
    assert.deepEqual(f.errors, []);
  });
  test(`${context}: existing login generates only a password`, async t => {
    const f = await fixture(t, contact, { login: 'manual-login' });
    await f.generate();
    assert.equal(await f.page.locator(login).inputValue(), 'manual-login');
    assert.equal(await f.draft(), 'Generated-Web-B2!');
    assert.equal(f.dialogs.length, 1);
  });
  test(`${context}: save, reopen, direct reveal, shared blur and five-second timeout`, async t => {
    const f = await fixture(t, contact);
    await f.generate();
    if (contact) await f.page.locator('#saveClientBtn').click();
    else await f.page.evaluate(() => window.TicketClientCard.saveClientProfile(window.ctx));
    await f.page.waitForFunction(() => window.ctx.el.clientModal.classList.contains('hidden'));
    await f.open();
    assert.equal(await f.draft(), '');
    assert.equal(await f.page.locator(password).evaluate(n => n.classList.contains('client-card-password')), true);
    const before = f.requests.filter(r => r.path.endsWith('/web-password')).length;
    await f.page.locator(password).click();
    await f.page.waitForFunction(() => window.ctx.state.clientCardWebPasswordReveal?.plaintext === 'Generated-Web-B2!');
    assert.equal(f.requests.filter(r => r.path.endsWith('/web-password')).length, before + 1);
    assert.equal(await f.page.locator(password).inputValue(), 'Generated-Web-B2!');
    assert.equal(await f.page.locator(password).getAttribute('data-web-password-dirty'), '0');
    let payload = await f.page.evaluate(() => window.TicketClientCard.getModalPayload(window.ctx));
    assert.equal(Object.hasOwn(payload.custom_fields, 'custom_web_password'), false);
    await f.page.clock.runFor(5001);
    assert.notEqual(await f.page.locator(password).inputValue(), 'Generated-Web-B2!');
    assert.equal(await f.page.locator(password).evaluate(n => n.classList.contains('client-card-password')), true);
    assert.equal(await f.page.evaluate(() => window.ctx.state.clientCardWebPasswordReveal.plaintext), '');
    assert.deepEqual(f.errors, []);
  });
  test(`${context}: failure and malformed response change neither field and display an error`, async t => {
    const f = await fixture(t, contact);
    f.server.fail = true; await f.generate();
    assert.equal(await f.page.locator(login).inputValue(), ''); assert.equal(await f.draft(), '');
    const error = contact ? '#errorText' : '#testError';
    assert.ok(await f.page.locator(error).evaluate(n => n.value || n.textContent));
    if (contact) await f.page.locator('#closeErrorModalBtn').click();
    f.server.fail = false; f.server.malformed = true; await f.generate();
    assert.equal(await f.page.locator(login).inputValue(), ''); assert.equal(await f.draft(), '');
    assert.deepEqual(f.errors, []);
  });
  test(`${context}: late generation after closing cannot overwrite the reopened card`, async t => {
    const f = await fixture(t, contact);
    f.server.hold = true;
    await f.page.getByRole('button', { name: 'Генерировать WEB', exact: true }).click();
    await f.page.locator('#clientWebGenerateConfirm [data-web-confirm-yes]').click();
    while (!f.server.pending) await new Promise(resolve => setImmediate(resolve));
    await f.page.evaluate(() => window.TicketClientCard.closeClientModal(window.ctx, { force: true }));
    await f.open(); f.server.hold = false; f.server.pending();
    await f.page.waitForResponse(r => r.url().endsWith('/generate'));
    assert.equal(await f.page.locator(login).inputValue(), ''); assert.equal(await f.draft(), '');
    assert.deepEqual(f.errors, []);
  });
}
for (const role of ['admin', 'agent', 'trainee', 'chiki_piki']) {
  test(`${role}: WEB permission matrix and observer masks remain intact`, async t => {
    const f = await fixture(t, false, { login: 'saved', saved: true });
    await f.page.evaluate(role => {
      window.ctx.state.role = role;
      window.TicketClientCard.fillModal(window.ctx, window.ctx.state.currentClientProfile);
    }, role);
    const blocked = role === 'chiki_piki';
    assert.equal(await f.page.locator(password).isEditable(), !blocked);
    assert.equal(await f.page.getByRole('button', { name: 'Генерировать WEB', exact: true }).isDisabled(), blocked);
    await f.page.locator(password).dispatchEvent('focus');
    if (!blocked) await f.page.waitForFunction(() => window.ctx.state.clientCardWebPasswordReveal?.plaintext === 'Saved-Web-A1!');
    assert.equal(f.requests.filter(r => r.path.endsWith('/web-password')).length, blocked ? 0 : 1);
    if (blocked) assert.notEqual(await f.page.locator(password).inputValue(), 'Saved-Web-A1!');
    await f.page.evaluate(() => {
      window.TicketClientCard.closeClientModal(window.ctx, { force: true });
      window.TicketClientCard.renderClientDynamicCard(window.ctx, window.ctx.state.currentClientProfile, window.ctx.state.clientCardViewConfig);
    });
    assert.equal(await f.page.getByRole('button', { name: 'Генерировать WEB', exact: true }).count(), 0);
    for (const name of ['web', 'bitrix']) {
      const node = f.page.locator(`[data-client-card-${name}-password-value="1"]`);
      assert.equal(await node.getAttribute('aria-disabled'), String(blocked));
      const endpoint = `/${name}-password`, before = f.requests.filter(r => r.path.endsWith(endpoint)).length;
      await node.dispatchEvent('click');
      if (blocked) assert.equal(f.requests.filter(r => r.path.endsWith(endpoint)).length, before);
    }
    assert.deepEqual(f.errors, []);
  });
}


for (const contact of [false, true]) {
  for (const action of ['close', 'typing']) {
    test(`${contact ? 'contact' : 'ticket'}: late WEB reveal after ${action} never writes plaintext`, async t => {
      const f = await fixture(t, contact, { saved: true });
      f.server.holdReveal = true;
      await f.page.locator(password).click();
      while (!f.server.pending) await new Promise(resolve => setImmediate(resolve));
      if (action === 'close') {
        await f.page.evaluate(() => window.TicketClientCard.closeClientModal(window.ctx, { force: true }));
        await f.open();
      } else await enterPassword(f.page, 'Manual-Late-E5!');
      const response = f.page.waitForResponse(r => r.url().endsWith('/web-password'));
      f.server.pending(); await response;
      await f.page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assert.notEqual(await f.page.locator(password).inputValue(), 'Saved-Web-A1!');
      assert.equal(await f.draft(), action === 'typing' ? 'Manual-Late-E5!' : '');
      assert.deepEqual(f.errors, []);
    });
  }
  test(`${contact ? 'contact' : 'ticket'}: occupied Bitrix login accepts the name algorithm draft`, async t => {
    const f = await fixture(t, contact);
    f.server.occupied = true;
    await f.generate();
    assert.equal(f.generates()[0].data.bitrix_login, 'bitrix.current');
    assert.equal(await f.page.locator(login).inputValue(), 'i.ivanov@elitesochi.com');
    assert.equal(await f.draft(), 'Generated-Web-B2!');
  });
}


for (const contact of [false, true]) {
  for (const [hasLogin, saved, manual, expected] of [
    [false, false, false, 'Будут созданы новый WEB-логин и пароль'],
    [true, false, false, 'Будет создан новый WEB-пароль. Существующий логин останется без изменений'],
    [false, true, false, 'Будет создан WEB-логин. Существующий пароль останется без изменений'],
    [true, false, true, 'Внимание! Существующие WEB-логин и пароль будут заменены новыми после сохранения карточки'],
  ]) {
    test(`${contact ? 'contact' : 'ticket'}: confirmation content and No (${hasLogin}/${saved}/${manual})`, async t => {
      const f = await fixture(t, contact, { login: hasLogin ? 'existing-login' : null, saved });
      if (manual) await enterPassword(f.page, 'Manual-Confirm-F6!');
      const original = await f.draft();
      await f.page.getByRole('button', { name: 'Генерировать WEB', exact: true }).click();
      const modal = f.page.locator('#clientWebGenerateConfirm');
      assert.equal(await modal.getAttribute('role'), 'dialog');
      assert.ok((await modal.innerText()).includes('Подтверждение генерации WEB'));
      assert.ok((await modal.innerText()).includes(expected));
      assert.equal((await modal.innerText()).includes('Логин сначала берётся из Bitrix.'), !hasLogin || saved || manual);
      assert.ok((await modal.innerText()).includes('Изменения вступят в силу только после сохранения карточки. Продолжить генерацию?'));
      assert.equal((await modal.innerText()).includes('Manual-Confirm-F6!'), false);
      assert.equal((await modal.innerText()).includes('Saved-Web-A1!'), false);
      assert.equal(f.generates().length, 0);
      // A disabled generator cannot open another dialog.
      await f.page.getByRole('button', { name: 'Генерировать WEB', exact: true }).dispatchEvent('click');
      assert.equal(await modal.count(), 1);
      await modal.getByRole('button', { name: 'Нет', exact: true }).click();
      assert.equal(await modal.count(), 0);
      assert.equal(f.generates().length, 0);
      assert.equal(await f.draft(), original);
      assert.equal(await f.page.locator(login).inputValue(), hasLogin ? 'existing-login' : '');
      assert.deepEqual(f.errors, []);
    });
  }
  for (const dismissal of ['close', 'escape', 'backdrop', 'edit', 'client', 'status', 'card-close']) {
    test(`${contact ? 'contact' : 'ticket'}: confirmation rejects ${dismissal} without a request`, async t => {
      const f = await fixture(t, contact, { saved: true });
      await f.page.getByRole('button', { name: 'Генерировать WEB', exact: true }).click();
      const modal = f.page.locator('#clientWebGenerateConfirm');
      if (dismissal === 'close') await modal.locator('[data-web-confirm-close]').click();
      if (dismissal === 'escape') await f.page.keyboard.press('Escape');
      if (dismissal === 'backdrop') await modal.click({ position: { x: 2, y: 2 } });
      if (dismissal === 'edit') await f.page.locator(login).evaluate(n => { n.value = 'edited-during-confirm'; n.dispatchEvent(new Event('input', { bubbles: true })); });
      if (dismissal === 'client') {
        await f.page.evaluate(() => { if (window.ctx.state.ticketId) window.ctx.state.card.client_id = 'other'; else window.ctx.state.selectedClientId = 'other'; });
        await modal.locator('[data-web-confirm-yes]').click();
      }
      if (dismissal === 'status') {
        await f.page.evaluate(() => { window.ctx.state.card = { ...window.ctx.state.card, status: 'closed' }; });
        await modal.locator('[data-web-confirm-yes]').click();
      }
      if (dismissal === 'card-close') await f.page.evaluate(() => window.TicketClientCard.closeClientModal(window.ctx, { force: true }));
      await f.page.waitForFunction(() => !document.querySelector('#clientWebGenerateConfirm'));
      assert.equal(f.generates().length, 0);
      assert.deepEqual(f.errors, []);
    });
  }
  test(`${contact ? 'contact' : 'ticket'}: explicit clear, save, reopen and password-only regeneration`, async t => {
    const f = await fixture(t, contact, { login: 'keep-login', saved: true });
    await f.page.locator(password).click();
    await f.page.waitForFunction(() => window.ctx.state.clientCardWebPasswordReveal?.plaintext === 'Saved-Web-A1!');
    await f.page.locator(password).press('ControlOrMeta+A');
    await f.page.locator(password).press('Backspace');
    assert.equal(await f.page.locator(password).getAttribute('data-web-password-dirty'), '1');
    const payload = await f.page.evaluate(() => window.TicketClientCard.getModalPayload(window.ctx));
    assert.equal(payload.clear_web_password, true);
    assert.equal(payload.custom_fields.custom_web_password, null);
    if (contact) await f.page.locator('#saveClientBtn').click();
    else await f.page.evaluate(() => window.TicketClientCard.saveClientProfile(window.ctx));
    await f.page.waitForFunction(() => window.ctx.el.clientModal.classList.contains('hidden'));
    const sent = f.requests.find(r => r.method === 'PATCH');
    assert.equal(sent.data.clear_web_password, true);
    assert.equal(sent.data.custom_fields.custom_web_password, null);
    assert.equal(sent.headers['content-type'], 'application/json');
    assert.equal(f.server.secret, null);
    assert.equal(f.server.profile.web_password_set, false);
    assert.equal(f.server.profile.web_login, 'keep-login');
    await f.open();
    assert.equal(await f.page.locator(password).inputValue(), '');
    await f.reload();
    assert.equal(await f.page.locator(password).inputValue(), '');
    assert.equal(await f.page.locator(password).evaluate(n => getComputedStyle(n).filter), 'none');
    const plain = await f.page.evaluate(async () => window.ctx.api(window.ctx.state.ticketId
      ? '/api/v1/tickets/ticket/client-profile/web-password' : '/api/v1/management/users/client/profile/web-password'));
    assert.equal(plain.web_password, null);
    await f.generate();
    assert.ok(f.dialogs[0].includes('Будет создан новый WEB-пароль. Существующий логин останется без изменений'));
    assert.equal(f.generates()[0].data.web_password_set, false);
    assert.equal(f.generates()[0].data.regenerate, false);
    assert.equal(await f.page.locator(login).inputValue(), 'keep-login');
    assert.equal(await f.draft(), 'Generated-Web-B2!');
  });
  test(`${contact ? 'contact' : 'ticket'}: reveal, remask and cancelled clearing do not emit deletion`, async t => {
    const f = await fixture(t, contact, { login: 'keep-login', saved: true });
    await f.page.locator(password).click();
    await f.page.waitForFunction(() => window.ctx.state.clientCardWebPasswordReveal?.plaintext === 'Saved-Web-A1!');
    await f.page.clock.runFor(5001);
    let payload = await f.page.evaluate(() => window.TicketClientCard.getModalPayload(window.ctx));
    assert.equal(Object.hasOwn(payload, 'clear_web_password'), false);
    assert.equal(Object.hasOwn(payload.custom_fields, 'custom_web_password'), false);
    await enterPassword(f.page, '');
    await f.page.evaluate(() => window.TicketClientCard.closeClientModal(window.ctx, { force: true }));
    await f.open();
    payload = await f.page.evaluate(() => window.TicketClientCard.getModalPayload(window.ctx));
    assert.equal(Object.hasOwn(payload, 'clear_web_password'), false);
    assert.equal(f.server.secret, 'Saved-Web-A1!');
    assert.equal(f.requests.filter(r => r.method === 'PATCH').length, 0);
  });
}
