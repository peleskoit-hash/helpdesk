const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('api/app/static/ticket_client_card.js', 'utf8');
const ticket = fs.readFileSync('api/app/static/ticket.js', 'utf8');
const template = fs.readFileSync('api/app/templates/ticket.html', 'utf8');
const css = fs.readFileSync('api/app/static/ticket_client_card.css', 'utf8');
const shell = fs.readFileSync('api/app/static/app_shell.js', 'utf8');

function element(tagName = 'input') {
  const classes = new Set();
  return {
    tagName: tagName.toUpperCase(), type: 'text', value: '', checked: false,
    dataset: {}, disabled: false, readOnly: false, required: false,
    attributes: {}, setAttribute(name, value) { this.attributes[name] = value; },
    handlers: {}, focus() {}, getClientRects: () => [1],
    closest() { return null; },
    classList: {
      contains: (name) => classes.has(name),
      add: (...names) => names.forEach((name) => classes.add(name)),
      remove: (...names) => names.forEach((name) => classes.delete(name)),
      toggle(name, flag) { if (flag) classes.add(name); else classes.delete(name); },
    },
    addEventListener(name, handler) { this.handlers[name] = handler; },
  };
}

function fixture({ inline = true, savedPassword = false, required = false, runtime = {}, crypto, web = false } = {}) {
  const panel = element('aside');
  const body = element('div');
  const el = {
    clientModal: element('div'), editClientBtn: element('button'),
    closeClientModalBtn: element('button'), saveClientBtn: element('button'),
  };
  const fields = {
    contact_name: 'fContactName', last_name: 'fLastName', destination: 'fDestination',
    sand_owner: 'fSandOwner', sales_group: 'fSalesGroup', location_type: 'fLocationType',
    anydesk: 'fAnydesk', internal_number: 'fInternalNumber', bitrix_login: 'fBitrixLogin',
    bitrix_password: 'fBitrixPassword', bitrix_id: 'fBitrixId', is_verified: 'fIsVerified',
  };
  const controls = Object.values(fields).map((id) => (el[id] = element()));
  el.fIsVerified.type = 'checkbox';
  el.fDestination.tagName = 'SELECT';
  const custom = ['text', 'select', 'checkbox', 'number', 'textarea'].map((type, index) => {
    const control = element(type === 'select' || type === 'textarea' ? type : 'input');
    control.type = type;
    control.dataset = { customFieldKey: `custom_${index}`, customFieldType: type };
    if (type === 'checkbox') control.checked = false;
    else control.value = type === 'number' ? '12' : 'initial';
    controls.push(control);
    return control;
  });
  el.clientModal.classList.add('hidden');
  if (web) {
    for (const key of ['custom_web_login', 'custom_web_password']) {
      const input = element();
      input.dataset = { customFieldKey: key, customFieldType: key.endsWith('password') ? 'password' : 'text' };
      custom.push(input); controls.push(input);
    }
  }
  el.clientModal.closest = () => panel;
  el.clientModal.querySelectorAll = (selector) => {
    if (selector === 'input, select, textarea') return controls;
    if (selector === '[data-custom-field-key]') return custom;
    return [];
  };
  el.clientModal.querySelector = (selector) => {
    if (selector === '.modal-body') return body;
    const key = selector.match(/data-(?:client-card-field-key|custom-field-key)="([^"]+)"/)?.[1];
    const control = custom.find((input) => input.dataset.customFieldKey === key);
    if (selector.includes('data-custom-field-key=')) return control || null;
    return control ? Object.assign(control.wrap ||= element('label'), { querySelector: () => control }) : null;
  };
  const calls = [], errors = [], confirmations = [], reloads = [];
  let allowExit = false, destroyed = false;
  let profile = { contact_name: 'Client', destination: 'HR', bitrix_password_set: savedPassword };
  let request = async (url, options) => {
    if (url.endsWith('/bitrix-password')) return { bitrix_password: 'test-value' };
    if (options?.method === 'PATCH') return { ...profile, ...JSON.parse(options.body) };
    return profile;
  };
  const window = { crypto, HelpdeskDialogs: { confirm: async (message) => { confirmations.push(message); return allowExit; } } };
  if (web) vm.runInNewContext(fs.readFileSync('api/app/static/client_web_credentials.js', 'utf8'), { window });
  vm.runInNewContext(source, { window, console, setTimeout, clearTimeout, Uint8Array, ...runtime });
  const card = window.TicketClientCard;
  const ctx = {
    inlineClient: inline, isDestroyed: () => destroyed,
    state: { ticketId: 'ticket-1', role: 'agent', card: {}, clientCardViewConfig: {
      blocks: [{ key: 'test', fields: [
        ...Object.keys(fields).map((key) => ({ key })),
        ...custom.map((control) => ({ key: control.dataset.customFieldKey })),
      ] }], rules: required ? [{ condition_operator: 'always', actions: [
        { target_type: 'field', target_key: 'contact_name', action: 'required' },
      ] }] : [],
    } },
    el, setStatus() {}, showError: (message) => errors.push(message),
    setControlValue: (control, value) => { control.value = value ?? ''; },
    loadCard: async (options) => reloads.push(options),
    api: async (url, options) => { calls.push({ url, options }); return request(url, options); },
  };
  return {
    card, ctx, el, panel, body, controls, custom, calls, errors, confirmations, reloads,
    allowExit: (value) => { allowExit = value; },
    request: (value) => { request = value; },
    destroy: () => { destroyed = true; card.destroyClientEditor(ctx); },
    open: () => card.openClientModal(ctx), dirty: () => card.hasInlineChanges(ctx),
    close: () => card.closeClientModal(ctx), save: () => card.saveClientProfile(ctx),
  };
}

const patchCalls = (f) => f.calls.filter((call) => call.options?.method === 'PATCH');

test('WEB password is omitted until explicitly edited; changes and cancellation preserve existing inline rules', async () => {
  const f = fixture({ web: true });
  await f.open();
  const password = f.custom.find(input => input.dataset.customFieldKey === 'custom_web_password');
  password.dataset.webPasswordDirty = '0';
  assert.equal(Object.hasOwn(f.card.getModalPayload(f.ctx).custom_fields, 'custom_web_password'), false);
  password.value = '  Manual-A1!  '; // Do not silently trim a manually chosen password.
  password.dataset.webPasswordDirty = '1';
  f.ctx.state.webPasswordDraft = password.value; // Synthetic fixture has no dynamically rendered input handler.
  assert.equal(f.dirty(), true);
  assert.equal(f.card.getModalPayload(f.ctx).custom_fields.custom_web_password, '  Manual-A1!  ');
  f.allowExit(true);
  await f.close();
  assert.equal(password.value, '');
  assert.equal(password.dataset.webPasswordDirty, '0');
  assert.equal(patchCalls(f).length, 0);
});

test('ticket reuses one editor with all original IDs, inside left panel and without modal overlay', () => {
  const ids = [...template.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]);
  assert.equal(new Set(ids).size, ids.length);
  for (const id of ['clientModal', 'editClientBtn', 'closeClientModalBtn', 'saveClientBtn',
    'customFieldsSection', 'customFieldsContainer', 'fContactName', 'fLastName', 'fDestination',
    'fSandOwner', 'fSandOwnerWrap', 'fSalesGroup', 'fSalesGroupWrap', 'fLocationType',
    'fAnydesk', 'fAnydeskWrap', 'fInternalNumber', 'fBitrixLogin', 'fBitrixPassword', 'fBitrixId', 'fIsVerified']) {
    assert.ok(ids.includes(id), `existing editor ID missing: ${id}`);
  }
  assert.match(template, /ticket-client-side[\s\S]*?id="clientModal"[\s\S]*?<\/aside>/);
  assert.match(template, /id="clientModal" class="ticket-client-editor hidden"/);
  const editor = template.slice(template.indexOf('id="clientModal"'), template.indexOf('class="client-edit-border"'));
  assert.doesNotMatch(editor, /class="modal |modal-head|Редактирование клиента/);
  assert.match(editor, /modal-foot[\s\S]*closeClientModalBtn[\s\S]*Отмена[\s\S]*saveClientBtn/);
  assert.match(template, /id="editClientBtn"[^>]*>Редактировать<\/button>/);
  assert.match(template, /appContent[\s\S]*ticket_client_card\.css\?v=/);
});

test('CSS is ticket scoped; fields scroll, footer and header do not shrink, border does not intercept clicks', () => {
  assert.doesNotMatch(css, /grid-template-columns:\s*266px|position:\s*fixed/);
  assert.match(css, /modal-body \{[^}]*flex: 1 1 0;[^}]*min-height: 0;/);
  assert.match(css, /modal-foot \{[^}]*flex: 0 0 auto;/);
  assert.match(css, /> \.card-head \{[^}]*flex: 0 0 auto;/);
  assert.match(css, /modal-body \* \{[^}]*min-width: 0;[^}]*max-width: 100%;[^}]*box-sizing: border-box/);
  assert.match(css, /pointer-events: none/);
  assert.match(css, /animation: ticket-client-border 9s linear infinite/);
  assert.match(css, /stroke-dashoffset: -10/);
  assert.match(css, /prefers-reduced-motion: reduce[\s\S]*animation: none/);
});

test('only the client card scroll viewport uses native thin scrollbars with cache-busted scoped CSS', () => {
  const selector = '[data-shell-page="ticket-card"] .ticket-client-side > #clientDynamicCard';
  const rule = css.slice(css.indexOf(selector)).split('}')[0];
  assert.ok(css.includes(selector));
  assert.equal(rule.slice(rule.indexOf('{') + 1).trim(), 'scrollbar-width: thin;');
  assert.equal([...css.matchAll(/scrollbar-width:/g)].length, 1);
  assert.doesNotMatch(css, /::-webkit-scrollbar|scrollbar-color:/);
  assert.match(template, /appContent[\s\S]*ticket_client_card\.css\?v=20261021-client-scroll-thin"/);
});

test('open and clean cancel switch modes without confirm or PATCH; fields retain identity', async () => {
  const f = fixture();
  const originalInput = f.el.fContactName;
  await f.open();
  assert.equal(f.el.fContactName, originalInput);
  assert.equal(f.el.clientModal.classList.contains('hidden'), false);
  assert.equal(f.panel.classList.contains('client-editing'), true);
  assert.equal(f.el.editClientBtn.textContent, 'Редактирование');
  assert.equal(f.dirty(), false);
  assert.equal(await f.close(), true);
  assert.equal(f.panel.classList.contains('client-editing'), false);
  assert.equal(f.el.clientModal.classList.contains('hidden'), true);
  assert.equal(f.el.editClientBtn.textContent, 'Редактировать');
  assert.equal(f.confirmations.length, 0);
  assert.equal(patchCalls(f).length, 0);
});

test('real values including dynamic select, checkbox, number and textarea can change and revert', async () => {
  const f = fixture();
  await f.open();
  for (const control of [f.el.fContactName, f.el.fDestination, f.el.fIsVerified, ...f.custom]) {
    const value = control.value, checked = control.checked;
    if (control.type === 'checkbox') control.checked = !checked;
    else control.value = 'changed';
    assert.equal(f.dirty(), true);
    control.value = value; control.checked = checked;
    assert.equal(f.dirty(), false);
  }
  f.ctx.state.clientCardRuleState.required.add('anydesk');
  assert.doesNotThrow(() => f.dirty());
});

test('cancel decline preserves every value; acceptance clears draft without PATCH; reopening is fresh', async () => {
  const f = fixture();
  await f.open();
  f.custom[0].value = 'draft';
  assert.equal(await f.close(), false);
  assert.equal(f.custom[0].value, 'draft');
  assert.equal(f.panel.classList.contains('client-editing'), true);
  assert.equal(f.confirmations[0], 'Есть несохранённые изменения. Выйти без сохранения?');
  f.allowExit(true);
  assert.equal(await f.close(), true);
  assert.equal(f.custom[0].value, '');
  assert.equal(patchCalls(f).length, 0);
  await f.open();
  assert.equal(f.el.fContactName.value, 'Client');
  assert.equal(f.dirty(), false);
});

test('Bitrix mask/reveal/blur are clean; changed password and reversion use logical values', async () => {
  const f = fixture({ savedPassword: true });
  await f.open();
  const password = f.el.fBitrixPassword;
  await password.handlers.focus();
  assert.equal(f.dirty(), false);
  const original = password.value;
  password.value = 'edited'; password.handlers.input();
  assert.equal(f.dirty(), true);
  password.handlers.blur();
  assert.equal(f.dirty(), true);
  await password.handlers.focus();
  password.value = original; password.handlers.input();
  assert.equal(f.dirty(), false);
  password.handlers.blur();
  assert.equal(f.dirty(), false);
});

test('new Bitrix password can be typed and erased back to clean; cancel clears secret state', async () => {
  const f = fixture();
  await f.open();
  const password = f.el.fBitrixPassword;
  password.value = 'edited'; password.handlers.input();
  assert.equal(f.dirty(), true);
  password.value = ''; password.handlers.input();
  assert.equal(f.dirty(), false);
  await f.close();
  assert.equal(f.ctx.state.clientCardBitrixPassword.draftValue, '');
  assert.equal(f.ctx.state.clientCardBitrixPassword.originalValue, null);
});

test('save uses existing PATCH/payload, blocks duplicate submission and exits only on success', async () => {
  const f = fixture();
  await f.open();
  f.el.fContactName.value = 'Changed';
  f.custom[2].checked = true;
  let finish;
  f.request(() => new Promise((resolve) => { finish = resolve; }));
  const save = f.save();
  await f.save();
  assert.equal(patchCalls(f).length, 1);
  assert.equal(f.el.saveClientBtn.disabled, true);
  assert.equal(f.el.closeClientModalBtn.disabled, true);
  assert.equal(f.body.inert, true);
  assert.equal(await f.close(), false);
  const call = patchCalls(f)[0];
  assert.equal(call.url, '/api/v1/tickets/ticket-1/client-profile');
  const payload = JSON.parse(call.options.body);
  assert.equal(payload.contact_name, 'Changed');
  assert.equal(payload.custom_fields.custom_2, true);
  assert.equal(payload.custom_fields.custom_3, 12);
  assert.equal(Object.hasOwn(payload, 'bitrix_password'), false);
  finish({ contact_name: 'Changed' });
  await save;
  assert.equal(f.ctx.state.card.client_profile.contact_name, 'Changed');
  assert.equal(f.el.clientModal.classList.contains('hidden'), true);
  assert.equal(f.body.inert, false);
  assert.equal(f.el.saveClientBtn.disabled, false);
  assert.equal(f.confirmations.length, 0);
  assert.equal(f.reloads[0].preserveComposerDraft, true);
});

test('API and validation errors leave editor open, values intact, and retry enabled', async () => {
  for (const required of [false, true]) {
    const f = fixture({ required });
    await f.open();
    f.el.fContactName.value = required ? '' : 'Draft';
    f.custom[0].value = 'Dynamic draft';
    f.request(async () => { throw new Error('API error'); });
    await f.save();
    assert.equal(f.errors.length, 1);
    assert.equal(patchCalls(f).length, required ? 0 : 1);
    assert.equal(f.el.clientModal.classList.contains('hidden'), false);
    assert.equal(f.custom[0].value, 'Dynamic draft');
    assert.equal(f.el.saveClientBtn.disabled, false);
    assert.equal(f.body.inert, false);
    assert.equal(f.dirty(), true);
  }
});

test('permissions and rules still exclude readonly/hidden fields from the shared payload', async () => {
  const f = fixture();
  await f.open();
  f.ctx.state.clientCardRuleState.hidden.add('custom_0');
  f.custom[1].dataset.clientCardBaseReadonly = '1';
  const payload = f.card.getModalPayload(f.ctx);
  assert.equal(Object.hasOwn(payload.custom_fields, 'custom_0'), false);
  assert.equal(Object.hasOwn(payload.custom_fields, 'custom_1'), false);
});

test('late GET/PATCH/reveal after destroy cannot reopen or update another ticket', async () => {
  for (const operation of ['open', 'save', 'reveal']) {
    const f = fixture({ savedPassword: true });
    if (operation !== 'open') await f.open();
    let finish;
    f.request(() => new Promise((resolve) => { finish = resolve; }));
    const pending = operation === 'open' ? f.open() : operation === 'save' ? f.save() : f.el.fBitrixPassword.handlers.focus();
    f.destroy();
    finish({ contact_name: 'Late', bitrix_password: 'late-value' });
    await pending;
    assert.equal(f.el.clientModal.classList.contains('hidden'), true);
    assert.equal(f.el.fBitrixPassword.value, '');
    assert.equal(f.ctx.state.currentClientProfile, null);
    assert.equal(f.ctx.state.clientCardInline.open, false);
    assert.equal(f.ctx.state.clientCardInline.saving, false);
  }
});

test('AppShell/ESC/unload guard and polling retain existing composer cleanup', () => {
  assert.match(ticket, /canNavigateAway: \(\) => \{\s*if \(state.clientCardInline\?\.open\) return closeClientModal\(\)/);
  assert.match(ticket, /e.key === "Escape" && !el.clientModal.classList.contains\("hidden"\)[\s\S]*?closeClientModal\(\)/);
  assert.match(ticket, /event.type === "beforeunload"[\s\S]*?hasInlineChanges[\s\S]*?event.preventDefault\(\);[\s\S]*?event.returnValue = ""/);
  assert.match(ticket, /if \(!el.clientModal.classList.contains\("hidden"\)\) return;\s*loadCard/);
  assert.match(ticket, /if \(!state.clientCardInline\?\.open\) \{\s*state.clientCardViewConfig/);
  assert.match(ticket, /destroyed = true;\s*TicketClientCard.destroyClientEditor[\s\S]*?flushDraftKeepalive/);
  assert.match(shell, /addEventListener\("pagehide", \(event\) => \{\s*if \(!event.persisted\) destroyCurrentPage\(\)/);
  assert.doesNotMatch(shell, /addEventListener\("beforeunload", destroyCurrentPage\)/);
});

test('Users modal behavior/markup are unchanged; shared generator and payload remain single implementations', async () => {
  const f = fixture({ inline: false });
  await f.open();
  f.el.fContactName.value = 'Changed';
  assert.equal(await f.close(), true);
  assert.equal(f.panel.classList.contains('client-editing'), false);
  assert.equal(f.confirmations.length, 0);
  assert.equal(f.ctx.state.clientCardInline, undefined);
  assert.equal(f.el.fContactName.value, 'Changed');
  const usersJs = fs.readFileSync('api/app/static/management_users.js', 'utf8');
  const usersHtml = fs.readFileSync('api/app/templates/management_users.html', 'utf8');
  assert.match(usersHtml, /id="clientModal" class="modal hidden"/);
  assert.match(usersHtml, /class="modal-head"/);
  assert.doesNotMatch(usersHtml, /ticket_client_card\.css|ticket-client-side/);
  assert.doesNotMatch(usersJs, /inlineClient|client-editing|hasInlineChanges/);
  assert.match(usersJs, /TicketClientCard\.fillModal\(clientCardCtx\(root\), profile/);
  assert.match(usersJs, /TicketClientCard\.getModalPayload\(clientCardCtx\(root\)\)/);
  for (const name of ['fillModal', 'getModalPayload', 'saveClientProfile', 'renderCustomFieldsEditor']) {
    assert.equal([...source.matchAll(new RegExp(`function ${name}\\(`, 'g'))].length, 1);
  }
});

test('resource versions agree in every direct and AppShell connection', () => {
  const files = [...fs.readdirSync('api/app/templates').filter((name) => name.endsWith('.html')).map((name) => `api/app/templates/${name}`), 'api/app/static/app_shell.js'];
  for (const resource of ['ticket_client_card.js', 'ticket.js', 'app_shell.js']) {
    const versions = files.flatMap((file) => [...fs.readFileSync(file, 'utf8').matchAll(new RegExp(`/static/${resource.replaceAll('.', '\\.')}\\?v=([^"']+)`, 'g'))].map((match) => match[1]));
    assert.ok(versions.length >= 2);
    assert.deepEqual([...new Set(versions)], [resource === 'app_shell.js' ? '20261029-staging-recovery-v1' : resource === 'ticket_client_card.js' ? '20261027-dialogs-complete-v3' : '20261029-ticket-history-v1']);
  }
});

test('actual AppShell navigation/back guard keeps declined draft and confirms acceptance once', async () => {
  const f = fixture();
  await f.open();
  f.el.fContactName.value = 'Draft';
  const root = { dataset: { shellPage: 'ticket-card' } };
  const events = {};
  let requests = 0, backCalls = 0;
  const window = {
    location: { href: 'http://desk/tickets/ticket-1', origin: 'http://desk', pathname: '/tickets/ticket-1', search: '' },
    addEventListener(name, handler) { events[name] = handler; },
    history: { back() { backCalls += 1; } },
  };
  const document = { scripts: [], readyState: 'loading', getElementById: () => root, addEventListener() {}, querySelectorAll: () => [] };
  vm.runInNewContext(shell, { window, document, URL, console, setTimeout: () => 1, clearTimeout() {}, fetch: async () => { requests += 1; throw new Error('fixture fetch'); } });
  window.HelpdeskAppShell.registerPage('ticket-card', () => ({ destroy: f.destroy, canNavigateAway: f.close }));
  assert.equal(await window.HelpdeskAppShell.navigate('/tickets/ticket-2'), false);
  assert.equal(requests, 0);
  assert.equal(f.el.fContactName.value, 'Draft');
  assert.equal(f.ctx.state.clientCardInline.open, true);
  const confirmations = f.confirmations.length;
  f.allowExit(true);
  assert.equal(await window.HelpdeskAppShell.goBack(), true);
  assert.equal(backCalls, 1);
  assert.equal(f.confirmations.length, confirmations + 1);
  assert.equal(f.ctx.state.clientCardInline.open, false);
  assert.equal(typeof events.pagehide, 'function');
  assert.equal(events.beforeunload, undefined);
});

test('actual unload handler prevents dirty exit without destroying the editor or attachment draft', async () => {
  const f = fixture();
  await f.open();
  f.custom[0].value = 'Draft';
  const start = ticket.indexOf('  beforeUnloadHandler = (event) => {');
  const end = ticket.indexOf('  window.addEventListener("beforeunload", beforeUnloadHandler);', start);
  let flushes = 0, attachmentDestructions = 0, prevented = false;
  const context = {
    state: f.ctx.state, TicketClientCard: f.card, clientCardCtx: () => f.ctx,
    flushDraftKeepalive() { flushes += 1; },
    TicketAttachments: { destroyAllAttachmentResources() { attachmentDestructions += 1; } },
  };
  vm.runInNewContext(ticket.slice(start, end), context);
  const event = { type: 'beforeunload', preventDefault() { prevented = true; } };
  context.beforeUnloadHandler(event);
  assert.equal(prevented, true);
  assert.equal(event.returnValue, '');
  assert.equal(flushes, 1);
  assert.equal(attachmentDestructions, 0);
  assert.equal(f.custom[0].value, 'Draft');
  assert.equal(f.ctx.state.clientCardInline.open, true);
});

test('late password response never overwrites typing; GET error leaves ordinary card usable', async () => {
  const f = fixture({ savedPassword: true });
  await f.open();
  let finish;
  f.request(() => new Promise((resolve) => { finish = resolve; }));
  const pending = f.el.fBitrixPassword.handlers.focus();
  f.el.fBitrixPassword.value = 'Draft';
  f.el.fBitrixPassword.handlers.input();
  finish({ bitrix_password: 'test-value' });
  await pending;
  assert.equal(f.el.fBitrixPassword.value, 'Draft');
  assert.equal(f.dirty(), true);
  const failed = fixture();
  failed.request(async () => { throw new Error('GET error'); });
  await failed.open();
  assert.equal(failed.errors.length, 1);
  assert.equal(failed.el.editClientBtn.disabled, false);
  assert.equal(failed.el.clientModal.classList.contains('hidden'), true);
});

test('BFCache pagehide keeps AppShell lifecycle initialized without duplicate initialization', () => {
  const root = { dataset: { shellPage: 'ticket-card' } };
  const events = {};
  let initializations = 0, destructions = 0;
  const window = {
    location: { href: 'http://desk/tickets/ticket-1', origin: 'http://desk', pathname: '/tickets/ticket-1', search: '' },
    addEventListener(name, handler) { events[name] = handler; },
  };
  const document = { scripts: [], readyState: 'loading', getElementById: () => root, addEventListener() {}, querySelectorAll: () => [] };
  vm.runInNewContext(shell, { window, document, URL, console });
  window.HelpdeskAppShell.registerPage('ticket-card', () => {
    initializations += 1;
    return { destroy() { destructions += 1; } };
  });
  events.pagehide({ persisted: true });
  assert.equal(destructions, 0);
  assert.equal(window.HelpdeskAppShell.getCurrentPage().initialized, true);
  window.HelpdeskAppShell.startCurrentPage();
  assert.equal(initializations, 1);
  events.pagehide({ persisted: false });
  assert.equal(destructions, 1);
  assert.equal(window.HelpdeskAppShell.getCurrentPage().initialized, false);
});

test('ticket preserves attachment resources on beforeunload and BFCache pagehide, cleans real pagehide', () => {
  const f = fixture();
  const start = ticket.indexOf('  beforeUnloadHandler = (event) => {');
  const end = ticket.indexOf('  window.addEventListener("beforeunload", beforeUnloadHandler);', start);
  let flushes = 0, destructions = 0;
  const context = {
    state: f.ctx.state, TicketClientCard: f.card, clientCardCtx: () => f.ctx,
    flushDraftKeepalive() { flushes += 1; },
    TicketAttachments: { destroyAllAttachmentResources() { destructions += 1; } },
  };
  vm.runInNewContext(ticket.slice(start, end), context);
  context.beforeUnloadHandler({ type: 'beforeunload' });
  assert.equal(destructions, 0);
  context.beforeUnloadHandler({ type: 'pagehide', persisted: true });
  assert.equal(destructions, 0);
  context.beforeUnloadHandler({ type: 'pagehide', persisted: false });
  assert.equal(destructions, 1);
  assert.equal(flushes, 3);
});

function ticketLoader(f) {
  const state = f.ctx.state;
  Object.assign(state, {
    isLoading: false, pendingCardReloadOptions: null, initialComposerFocusApplied: true,
    composeMode: 'comment', draftDirty: false, draftSaveInFlight: false,
    draftSavePromise: null, draftAckRevision: 1,
    draftLastSavedText: 'Current message', draftLastSavedMode: 'comment',
  });
  f.el.messageInput = { value: 'Current message', selectionStart: 3, selectionEnd: 7 };
  let request = async () => ({ id: 'ticket-1', draft_text: 'Stale server message', draft_mode: 'reply', draft_revision: 2 });
  let renders = 0;
  const context = {
    state, el: f.el, destroyed: false, console,
    currentDraftText: () => f.el.messageInput.value,
    saveDraftSelection() {}, restoreDraftSelection() {}, applyComposeModeUi() {},
    setStatus() {}, showError(message) { assert.fail(message); },
    api: async (url) => url.endsWith('/client-card-view-config') ? { blocks: [] } : request(),
    TicketComposer: { syncRestoredPreloads() {} }, composerCtx: () => f.ctx,
    defaultClientCardViewConfig: () => ({ blocks: [] }),
    renderCard() { renders += 1; },
    consumePendingQuickReplyUse: async () => {}, loadTaskPanel: async () => {},
  };
  const draftFunctions = ticket.slice(ticket.indexOf('function normalizeDraftMode(value) {'), ticket.indexOf('function markDraftCleanFromServer(payload) {'));
  const loadFunction = ticket.slice(ticket.indexOf('async function loadCard(options = {}) {'), ticket.indexOf('async function sendByMode() {'));
  vm.runInNewContext(draftFunctions + loadFunction, context);
  f.ctx.loadCard = context.loadCard;
  return { load: context.loadCard, state, request: (next) => { request = next; }, renders: () => renders };
}

test('successful client PATCH uses real loadCard and preserves message text, mode and selection', async () => {
  const f = fixture();
  await f.open();
  const loader = ticketLoader(f);
  f.el.fContactName.value = 'Changed client';
  await f.save();
  assert.equal(patchCalls(f).length, 1);
  assert.equal(f.errors.length, 0);
  assert.equal(loader.renders(), 1);
  assert.equal(f.el.messageInput.value, 'Current message');
  assert.equal(loader.state.composeMode, 'comment');
  assert.equal(f.el.messageInput.selectionStart, 3);
  assert.equal(f.el.messageInput.selectionEnd, 7);
  assert.equal(loader.state.draftAckRevision, 1);
  assert.equal(loader.state.draftLastSavedText, 'Current message');
  // Prove that this server reply would replace the draft without the preservation flag.
  await loader.load({ silent: true });
  assert.equal(f.el.messageInput.value, 'Stale server message');
  assert.equal(loader.state.composeMode, 'reply');
});

test('client save during an in-flight refresh preserves a dirty message and queues preserveComposerDraft', async () => {
  const f = fixture();
  await f.open();
  const loader = ticketLoader(f);
  loader.state.draftDirty = true;
  f.el.messageInput.value = 'Unsaved message';
  let finish;
  loader.request(() => new Promise((resolve) => { finish = resolve; }));
  const inFlight = loader.load({ silent: true });
  await f.save();
  assert.equal(loader.state.pendingCardReloadOptions.preserveComposerDraft, true);
  loader.request(async () => ({ id: 'ticket-1', draft_text: 'Another server message', draft_mode: 'reply', draft_revision: 3 }));
  finish({ id: 'ticket-1', draft_text: 'Old server message', draft_mode: 'reply', draft_revision: 2 });
  await inFlight;
  assert.equal(loader.renders(), 2);
  assert.equal(f.el.messageInput.value, 'Unsaved message');
  assert.equal(loader.state.composeMode, 'comment');
  assert.equal(loader.state.draftDirty, true);
  assert.equal(loader.state.draftLastSavedText, 'Current message');
  assert.equal(loader.state.pendingCardReloadOptions, null);
});

test('readonly editor omits copy buttons only inline and keeps Users clipboard behavior', async () => {
  const readonly = source.slice(source.indexOf('  function renderReadonlyEditorField('), source.indexOf('  function renderEditableCustomField('));
  const system = source.slice(source.indexOf('  function renderConfiguredSystemEditorField('), source.indexOf('  function renderCustomFieldsEditor('));
  const copied = [];
  const document = { createElement(tag) {
    const node = element(tag);
    node.children = [];
    node.appendChild = (child) => { node.children.push(child); };
    return node;
  } };
  const context = { document, navigator: { clipboard: { async writeText(value) { copied.push(value); } } },
    renderClientFieldValue(field, value) { return { textContent: value || '' }; },
    fieldEditorNodes: () => ({ control: null }),
  };
  vm.runInNewContext(readonly + system, context);
  for (const inlineClient of [true, false, undefined]) {
    const ctx = { el: {}, inlineClient };
    for (const render of [
      () => context.renderReadonlyEditorField({ key: 'custom_readonly', label: 'Readonly' }, 'Value', ctx),
      () => context.renderConfiguredSystemEditorField(ctx, { key: 'telegram_username' }, 'Value'),
    ]) {
      const field = render();
      const row = field.children[1];
      const buttons = row.children.filter((node) => node.tagName === 'BUTTON');
      assert.equal(buttons.length, inlineClient ? 0 : 1);
      assert.equal(row.children[0].textContent, 'Value');
      assert.equal(field.dataset.customFieldKey, undefined);
      if (!inlineClient) {
        assert.equal(buttons[0].textContent, 'Скопировать');
        await buttons[0].handlers.click();
      }
    }
  }
  assert.deepEqual(copied, ['Value', 'Value', 'Value', 'Value']);
  assert.equal([...source.matchAll(/renderReadonlyEditorField\(field, currentValue, ctx\)/g)].length, 2);
});

test('view typography grows modestly only in ticket card; editor fonts and spacing stay unchanged', () => {
  for (const [selector, size] of [['client-card-block-title', 13], ['client-card-field', 14], ['client-card-field-label', 12]]) {
    assert.match(css, new RegExp(`\\[data-shell-page="ticket-card"\\] #clientDynamicCard \\.${selector} \\{\\s*font-size: ${size}px;`));
  }
  assert.match(css, /#clientDynamicCard \.client-card-field \{[^}]*line-height: 1\.3;/);
  assert.doesNotMatch(css, /#clientDynamicCard\s*\{[^}]*padding:/);
  assert.doesNotMatch(css, /#clientModal[^{}]*\{[^}]*font-size:/);
});


function passwordCardFixture() {
  let now = 1000, generations = 0, nextTimer = 0;
  const timers = new Map();
  const document = { createElement(tag) {
    const node = element(tag);
    node.children = [];
    node.insertBefore = (child, next) => {
      child.remove();
      const index = next ? node.children.indexOf(next) : node.children.length;
      node.children.splice(index, 0, child);
      child.parentNode = node;
      return child;
    };
    node.appendChild = (child) => node.insertBefore(child, null);
    node.remove = () => {
      if (node.parentNode) node.parentNode.children = node.parentNode.children.filter(child => child !== node);
      node.parentNode = null;
    };
    Object.defineProperty(node, 'innerHTML', { set() { node.children = []; } });
    node.querySelector = () => {
      const visit = (item) => item.dataset.clientCardBitrixPasswordValue === '1'
        ? item : item.children?.map(visit).find(Boolean);
      return visit(node) || null;
    };
    return node;
  } };
  const f = fixture({ savedPassword: true, crypto: { getRandomValues(bytes) { bytes.fill(++generations); return bytes; } }, runtime: {
    document, Date: { now: () => now },
    setTimeout(callback, delay) { const id = ++nextTimer; timers.set(id, { callback, at: now + delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
  } });
  // A deterministic generator lets the tests detect unnecessary regeneration.
  // Only synthetic values are used; secret assertions report booleans.
  f.el.clientDynamicCard = document.createElement('div');
  f.ctx.isBlank = (value) => value == null || value === '';
  f.ctx.state.card.client_id = 'client-1';
  const config = { blocks: [{ key: 'access', fields: [{ key: 'bitrix_password' }] }] };
  const render = () => {
    f.card.renderClientDynamicCard(f.ctx, { bitrix_password_set: true }, config);
    return f.el.clientDynamicCard.querySelector();
  };
  return { ...f, render, timers, advance(ms) {
    now += ms;
    for (const [id, timer] of [...timers]) {
      if (timer.at <= now) { timers.delete(id); timer.callback(); }
    }
  } };
}

test('password mask survives polling rerenders and inline editor hide/reopen', async () => {
  const f = passwordCardFixture();
  const mask = f.render().textContent;
  assert.match(mask, /^[A-Za-z0-9]{10}$/);
  assert.equal(f.render().textContent, mask);
  await f.open();
  assert.equal(f.el.fBitrixPassword.value, mask);
  await f.el.fBitrixPassword.handlers.focus();
  f.el.fBitrixPassword.handlers.blur();
  assert.equal(f.el.fBitrixPassword.value, mask);
  await f.close();
  await f.open();
  assert.equal(f.el.fBitrixPassword.value, mask);
});

test('inline editor cancel restores the client panel scroll position', async () => {
  const f = fixture();
  f.el.clientDynamicCard = { scrollTop: 175 };
  await f.open();
  f.el.clientDynamicCard.scrollTop = 0;
  await f.close();
  assert.equal(f.el.clientDynamicCard.scrollTop, 175);
});

test('password reveal survives rerender and hides at five seconds with the original mask and cleared plaintext', async () => {
  const f = passwordCardFixture();
  const value = f.render(), mask = value.textContent;
  await value.handlers.click();
  assert.equal(Boolean(f.ctx.state.clientCardBitrixPasswordReveal.plaintext), true);
  f.advance(3000);
  const current = f.render();
  assert.equal(current.classList.contains('client-card-password-revealed'), true);
  f.advance(1999);
  assert.equal(current.classList.contains('client-card-password-revealed'), true);
  f.advance(1);
  assert.equal(current.textContent, mask);
  assert.equal(current.classList.contains('client-card-password-revealed'), false);
  const state = f.ctx.state.clientCardBitrixPasswordReveal;
  assert.equal(Boolean(state.plaintext), false);
  assert.equal(state.ownerKey, '');
  assert.equal(state.revealedUntil, 0);
  assert.equal(state.timerId, null);
  assert.equal(f.timers.size, 0);
});

test('switching client creates its own mask and clears the previous reveal', async () => {
  const f = passwordCardFixture();
  const first = f.render(), mask = first.textContent;
  await first.handlers.click();
  f.ctx.state.card.client_id = 'client-2';
  const second = f.render();
  assert.notEqual(second.textContent, mask);
  assert.equal(second.classList.contains('client-card-password-revealed'), false);
  assert.equal(Boolean(f.ctx.state.clientCardBitrixPasswordReveal.plaintext), false);
  assert.equal(f.timers.size, 0);
  assert.equal(f.render().textContent, second.textContent);
  await f.open();
  assert.equal(f.el.fBitrixPassword.value, second.textContent);
});


for (const role of ['admin', 'agent', 'trainee', 'chiki_piki']) {
  test(`${role}: Bitrix password focus respects reveal permission`, async () => {
    const f = fixture({ savedPassword: true });
    f.ctx.state.role = role;
    await f.open();
    const mask = f.el.fBitrixPassword.value;
    assert.ok(mask && mask !== 'test-value');
    await f.el.fBitrixPassword.handlers.focus();
    const reveals = f.calls.filter(call => call.url.endsWith('/bitrix-password'));
    assert.equal(reveals.length, role === 'chiki_piki' ? 0 : 1);
    assert.equal(f.el.fBitrixPassword.value, role === 'chiki_piki' ? mask : 'test-value');
  });
}
