const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });

async function fixture(t, fields, profile = {}, role = 'agent') {
  const page = await browser.newPage();
  t.after(() => page.close());
  await page.route('**/*', route => route.abort());
  await page.setContent('<aside class="ticket-side"><div id="clientDynamicCard"></div></aside>');
  await page.evaluate(template => {
    const doc = new DOMParser().parseFromString(template, 'text/html');
    document.querySelector('aside').appendChild(doc.getElementById('clientModal'));
  }, fs.readFileSync('api/app/templates/ticket.html', 'utf8'));
  await page.addScriptTag({ content: fs.readFileSync('api/app/static/ticket_client_card.js', 'utf8') });
  await page.evaluate(({ fields, profile, role }) => {
    window.config = { blocks: [{ key: 'department', title: 'Configured block', fields: fields.map(field => ({
      is_editable: true, is_visible: true, is_visible_in_editor: true, ...field,
    })) }], rules: [] };
    window.profile = profile;
    window.calls = [];
    window.errors = [];
    window.ctx = {
      state: { role, ticketId: 'synthetic', card: { client_profile: profile }, clientCardViewConfig: config },
      el: Object.fromEntries([...document.querySelectorAll('[id]')].map(el => [el.id, el])),
      setControlValue: (control, value) => { control.value = value ?? ''; },
      setStatus() {}, showError: message => errors.push(message),
      isBlank: value => value == null || value === '',
      api: async (url, options) => { calls.push({ url, options }); return url.endsWith('client-card-view-config') ? config : window.profile; },
    };
    TicketClientCard.fillModal(ctx, profile);
  }, { fields, profile, role });
  return page;
}

// Feed the actual backend effective options into the browser editor.
const systemOptions = JSON.parse(execFileSync(path.resolve('.venv/Scripts/python.exe'), ['-c', `
import json
from types import SimpleNamespace
from api.app.services.client_card import SYSTEM_SELECT_FIELD_OPTIONS, _field_options_out
print(json.dumps([
    dict(key=key, raw=raw, defaults=defaults,
         options=_field_options_out(SimpleNamespace(key=key, is_system=True, options_json=raw)))
    for key, defaults in SYSTEM_SELECT_FIELD_OPTIONS.items()
    for raw in (None, [], ['Individual'])
]))
`], { encoding: 'utf8' }));

for (const { key, raw, defaults, options } of systemOptions) {
  test(`${key}: backend ${JSON.stringify(raw)} produces effective editor choices`, async t => {
    const expected = raw?.length ? raw : defaults;
    assert.deepEqual(options, expected);
    const current = { destination: 'Руководство', location_type: 'Удалённый' }[key] || defaults[0];
    const page = await fixture(t, [{ key, label: key, field_type: 'select', is_system: true, options }], { [key]: current });
    const selector = { destination: '#fDestination', sand_owner: '#fSandOwner', sales_group: '#fSalesGroup', location_type: '#fLocationType' }[key];
    const actual = await page.locator(`${selector} option`).evaluateAll(items => items.map(item => ({ value: item.value, label: item.textContent, disabled: item.disabled })));
    assert.deepEqual(actual.filter(item => item.value && !item.disabled).map(item => item.value), expected);
    const selected = actual.find(item => item.value === current);
    assert.equal(await page.locator(selector).inputValue(), current);
    if (expected.includes(current)) {
      assert.equal(selected.label, current);
      assert.equal(selected.disabled, false);
    } else {
      assert.match(selected.label, /устаревшее значение/);
      assert.equal(selected.disabled, true);
    }
    const payload = await page.evaluate(() => TicketClientCard.getModalPayload(ctx));
    assert.equal(payload[key], undefined);
  });
}

for (const key of ['destination', 'sand_owner', 'sales_group', 'location_type', 'custom_team', 'employee_type']) {
  const selector = ['destination', 'sand_owner', 'sales_group', 'location_type'].includes(key)
    ? { destination: '#fDestination', sand_owner: '#fSandOwner', sales_group: '#fSalesGroup', location_type: '#fLocationType' }[key]
    : `[data-custom-field-key="${key}"]`;
  const field = { key, label: `Label ${key}`, field_type: 'select', is_system: !key.startsWith('custom_'), options: ['Current', 'Admin'], admin_only_values: ['Admin'] };
  test(`${key}: configured choices replace HTML, preserve legacy, refresh on reopen`, async t => {
    const custom = key.startsWith('custom_');
    const profile = custom ? { custom_fields: { [key]: 'Legacy' } } : { [key]: 'Legacy' };
    const page = await fixture(t, [field], profile);
    assert.equal(await page.locator(selector).inputValue(), 'Legacy');
    assert.deepEqual(await page.locator(`${selector} option`).evaluateAll(options => options.map(o => [o.value, o.disabled])),
      [['', false], ['Current', false], ['Admin', true], ['Legacy', true]]);
    const payload = await page.evaluate(() => TicketClientCard.getModalPayload(ctx));
    assert.equal(custom ? payload.custom_fields[key] : payload[key], undefined);
    await page.selectOption(selector, 'Current');
    const changed = await page.evaluate(() => TicketClientCard.getModalPayload(ctx));
    assert.equal(custom ? changed.custom_fields[key] : changed[key], 'Current');
    await page.evaluate(() => {
      config.blocks[0].fields[0].options = ['Latest'];
      window.profile = {};
      return TicketClientCard.openClientModal(ctx);
    });
    assert.deepEqual(await page.locator(`${selector} option`).evaluateAll(options => options.map(o => o.value)), ['', 'Latest']);
    assert.deepEqual(await page.evaluate(() => errors), []);
    assert.ok(await page.evaluate(() => calls.some(c => c.url.endsWith('client-card-view-config') && c.options.cache === 'no-store')));
  });
}

test('configuration applies label, visibility, readonly, required and unchanged input types', async t => {
  const page = await fixture(t, [
    { key: 'destination', label: 'New label', field_type: 'select', is_system: true, options: ['Only'], is_required: true },
    { key: 'custom_admin', label: 'Admin', field_type: 'select', options: ['A'], admin_only_edit: true },
    { key: 'custom_readonly', label: 'Readonly', field_type: 'text', is_editable: false },
    { key: 'custom_hidden', label: 'Hidden', field_type: 'text', is_visible_in_editor: false },
    ...['number', 'date', 'checkbox', 'textarea', 'phone', 'url'].map(type => ({ key: `custom_${type}`, label: type, field_type: type })),
  ]);
  assert.match(await page.locator('[data-client-card-field-key="destination"]').textContent(), /New label/);
  assert.equal(await page.locator('#fDestination').getAttribute('required'), '');
  assert.equal(await page.locator('[data-custom-field-key="custom_admin"]').isDisabled(), true);
  assert.equal(await page.locator('[data-custom-field-key="custom_readonly"]').count(), 0);
  assert.equal(await page.locator('[data-client-card-field-key="custom_hidden"]').count(), 0);
  for (const type of ['number', 'date', 'checkbox', 'textarea', 'phone', 'url']) {
    const expected = type === 'phone' ? 'tel' : type;
    assert.equal(await page.locator(`[data-custom-field-key="custom_${type}"]`).evaluate(el => el.type), expected);
  }
  await assert.rejects(page.evaluate(() => TicketClientCard.getModalPayload(ctx)), /New label/);
});

test('admin-only values remain selectable for admins', async t => {
  const page = await fixture(t, [{ key: 'sales_group', field_type: 'select', is_system: true, options: ['Admin'], admin_only_values: ['Admin'] }], {}, 'admin');
  assert.equal(await page.locator('#fSalesGroup option[value="Admin"]').isDisabled(), false);
});

test('card and editor visibility are independent', async t => {
  const page = await fixture(t, [
    { key: 'custom_card', label: 'Card only', field_type: 'text', is_visible_in_editor: false },
    { key: 'custom_editor', label: 'Editor only', field_type: 'text', is_visible: false },
  ], { custom_fields: { custom_card: 'Saved', custom_editor: 'Saved' } });
  await page.evaluate(() => TicketClientCard.renderClientDynamicCard(ctx, profile, config));
  assert.deepEqual(await page.locator('#clientDynamicCard .client-card-field-label').allTextContents(), ['Card only']);
  assert.equal(await page.locator('#clientModal [data-custom-field-key="custom_editor"]').count(), 1);
  assert.equal(await page.locator('#clientModal [data-custom-field-key="custom_card"]').count(), 0);
});

function broadcastAudience(count = 1, fingerprint = 'a'.repeat(64)) {
  return { count, audience_fingerprint: fingerprint };
}

function broadcastOperation(changes = {}) {
  return {
    operation_id: 'mock-operation', status: 'completed', total_count: 1, processed_count: 1,
    sent_count: 1, failed_count: 0, partial_count: 0, unknown_count: 0, skipped_count: 0,
    recipients: [], attachments: [], ...changes,
  };
}

async function loadBroadcastScripts(page) {
  for (const name of ['desk_dialogs', 'bot_buttons_emoji_data', 'telegram_text_editor', 'mass_notifications']) {
    await page.addScriptTag({ content: fs.readFileSync(`api/app/static/${name}.js`, 'utf8') });
  }
}

test('mass notification filters follow configured rules and discard stale recipient responses', async t => {
  const page = await browser.newPage();
  t.after(() => page.close());
  const fields = [
    { key: 'destination', label: 'Configured destination', options: ['Support', 'New destination'] },
    { key: 'location_type', label: 'Configured location', options: ['Remote'] },
    { key: 'custom_team', label: 'Configured team', options: ['New team'], is_visible_in_editor: false },
    { key: 'sales_group', label: 'Configured group', options: ['New group'], is_visible_in_editor: false },
    { key: 'group_name', label: 'Other group', options: ['Other'], is_visible_in_editor: false },
    { key: 'department', label: 'Department', options: ['Ops'], is_visible_in_editor: false },
    { key: 'contact_name', field_type: 'text', label: 'Text field' },
  ].map(field => ({ field_type: 'select', ...field }));
  const rule = (id, operator, key, value, target, options = {}) => ({
    id, sort_order: options.order ?? 10, is_active: options.active ?? true,
    condition_operator: operator, condition_field_key: key, condition_value: value,
    actions: [{ target_type: 'field', target_key: target, action: options.action ?? 'show', scope: options.scope ?? 'editor' }],
  });
  const rules = [
    rule('z', 'equals', 'destination', 'Support', 'custom_team', { action: 'hide' }),
    rule('a', 'equals', 'destination', 'Support', 'custom_team'),
    rule('b', 'is_not_empty', 'destination', null, 'sales_group', { scope: 'both', order: 1 }),
    rule('c', 'not_equals', 'destination', 'Support', 'sales_group', { scope: 'both', action: 'hide', order: 0 }),
    rule('d', 'is_empty', 'destination', null, 'group_name'),
    rule('e', 'always', null, null, 'department', { scope: 'card' }),
    rule('f', 'always', null, null, 'department', { active: false }),
    rule('g', 'always', null, null, 'contact_name'),
    rule('h', 'always', null, null, 'location_type', { action: 'hide' }),
    {
      ...rule('i', 'equals', 'location_type', 'Remote', 'other'),
      actions: [{ target_type: 'block', target_key: 'other', action: 'hide', scope: 'editor' }],
    },
  ];
  const queries = [];
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/client-card-view-config')) {
      return route.fulfill({ json: { blocks: [
        { key: 'routing', fields: fields.filter(field => field.key !== 'group_name') },
        { key: 'other', fields: fields.filter(field => field.key === 'group_name') },
      ], rules } });
    }
    if (url.pathname.endsWith('/preview')) {
      const filters = JSON.parse(url.searchParams.get('filters'));
      queries.push(filters);
      if (filters.custom_team) {
        return route.fulfill({ json: broadcastAudience(0) });
      }
      return route.fulfill({ json: broadcastAudience() });
    }
    if (url.pathname.includes('/operations/')) {
      return route.fulfill({ json: { operation: url.pathname.endsWith('/latest') ? null : broadcastOperation() } });
    }
    if (url.pathname.endsWith('/send')) return route.fulfill({ status: 202, json: { operation_id: 'mock-operation', status: 'queued', total_count: 1, created: true } });
    return route.fulfill({ json: [] });
  });
  await page.goto('https://synthetic.test/');
  const template = fs.readFileSync('api/app/templates/mass_notifications.html', 'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
  await page.setContent(template);
  await page.evaluate(() => {
    window.HelpdeskSidebar = { init: () => Promise.resolve() };
    const sendReal = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function (form) {
      window.sentFilters = form.get('filters');
      return sendReal.call(this, form);
    };
    const fetchReal = window.fetch;
    window.fetch = async (...args) => {
      const response = await fetchReal(...args);
      if (args[0].includes('custom_team')) await new Promise(resolve => { window.releaseOldCount = resolve; });
      return response;
    };
  });
  await loadBroadcastScripts(page);
  const dependent = page.locator('#dependentFilters select');
  await page.waitForFunction(() => document.querySelector('#recipientCount').textContent === '1');
  assert.deepEqual(queries[0], {});
  assert.equal(await dependent.count(), 1);
  assert.equal(await dependent.first().getAttribute('name'), 'group_name');
  assert.equal(await page.getByLabel('Подразделение').locator('option').first().textContent(), '');
  await page.getByLabel('Подразделение').selectOption('Support');
  assert.equal(await dependent.count(), 2);
  assert.deepEqual(await page.getByLabel('Configured team').locator('option').allTextContents(), ['', 'New team']);
  await page.getByLabel('Configured location').selectOption('Remote');
  await page.getByLabel('Configured group').selectOption('New group');
  await page.getByLabel('Configured team').selectOption('New team');
  await page.waitForFunction(() => window.releaseOldCount);
  assert.deepEqual(queries.at(-1), {
    destination: 'Support', location_type: 'Remote', custom_team: 'New team', sales_group: 'New group',
  });
  await page.getByLabel('Подразделение').selectOption('New destination');
  assert.equal(await dependent.count(), 0);
  await page.waitForFunction(() => document.querySelector('#recipientCount').textContent === '1');
  await page.evaluate(async () => {
    window.releaseOldCount();
    await new Promise(requestAnimationFrame);
  });
  assert.equal(await page.locator('#recipientCount').textContent(), '1');
  assert.deepEqual(queries.at(-1), { destination: 'New destination', location_type: 'Remote' });
  await page.locator('#broadcastText').fill('Mock notice');
  const confirmed = page.waitForEvent('dialog', { timeout: 5000 }).then(dialog => dialog.accept());
  await Promise.all([confirmed, page.locator('#sendBroadcastBtn').click()]);
  await page.waitForFunction(() => document.getElementById('resultBox').textContent.includes('Рассылка принята'));
  assert.deepEqual(JSON.parse(await page.evaluate(() => window.sentFilters)), queries.at(-1));

  await page.getByLabel('Подразделение').selectOption('');
  assert.equal(await dependent.count(), 0);
  await page.getByLabel('Подразделение').selectOption('Support');
  assert.equal(await page.getByLabel('Configured team').inputValue(), '');
  assert.equal(await page.getByLabel('Configured group').inputValue(), '');
});

test('mass notification filters include editor-visible selects in a block shown by a rule', async t => {
  const page = await browser.newPage();
  t.after(() => page.close());
  const selectField = (key, label, options, is_visible_in_editor = true) => ({
    key, label, options, is_visible_in_editor, field_type: 'select',
  });
  const config = {
    blocks: [
      { key: 'routing', fields: [
        selectField('destination', 'Configured destination', ['Support', 'Sales']),
        selectField('custom_unrelated', 'Unrelated field', ['Unrelated']),
      ] },
      { key: 'conditional', show_in_editor: false, fields: [
        selectField('custom_team', 'Configured team', ['Alpha', 'Beta']),
        selectField('custom_region', 'Configured region', ['North', 'South']),
        selectField('custom_hidden', 'Hidden field', ['Hidden'], false),
      ] },
    ],
    rules: [{
      id: 'show-conditional', is_active: true, condition_operator: 'equals',
      condition_field_key: 'destination', condition_value: 'Support',
      actions: [{ target_type: 'block', target_key: 'conditional', action: 'show', scope: 'editor' }],
    }],
  };
  await page.route('**/*', route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/api/v1/management/users/client-card-view-config') return route.fulfill({ json: config });
    if (pathname === '/api/v1/mass-notifications/preview') return route.fulfill({ json: broadcastAudience() });
    if (pathname === '/') return route.fulfill({ body: '', contentType: 'text/html' });
    return route.abort();
  });
  const nextFilters = async change => {
    const [request] = await Promise.all([
      page.waitForRequest(request => new URL(request.url()).pathname === '/api/v1/mass-notifications/preview'),
      change(),
    ]);
    return JSON.parse(new URL(request.url()).searchParams.get('filters'));
  };
  await page.goto('https://synthetic.test/');
  const template = fs.readFileSync('api/app/templates/mass_notifications.html', 'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
  await page.setContent(template);
  await page.evaluate(() => { window.HelpdeskSidebar = { init: () => Promise.resolve() }; });
  assert.deepEqual(await nextFilters(() => loadBroadcastScripts(page)), {});

  const dependent = page.locator('#dependentFilters select');
  const destination = page.getByLabel('Подразделение');
  assert.equal(await dependent.count(), 0);
  assert.deepEqual(await nextFilters(() => destination.selectOption('Support')), { destination: 'Support' });
  assert.deepEqual(await dependent.evaluateAll(selects => selects.map(select => select.name)), [
    'custom_team', 'custom_region',
  ]);

  assert.deepEqual(await page.locator('#dependentFilters label').evaluateAll(
    labels => labels.map(label => label.firstChild.textContent.trim())
  ), ['Configured team', 'Configured region']);
  const team = dependent.and(page.locator('[name="custom_team"]'));
  const region = dependent.and(page.locator('[name="custom_region"]'));
  assert.deepEqual(await team.locator('option').allTextContents(), ['', 'Alpha', 'Beta']);
  assert.deepEqual(await region.locator('option').allTextContents(), ['', 'North', 'South']);
  await nextFilters(() => team.selectOption('Beta'));
  assert.deepEqual(await nextFilters(() => region.selectOption('North')), {
    destination: 'Support', custom_team: 'Beta', custom_region: 'North',
  });
  assert.deepEqual(await nextFilters(() => destination.selectOption('Sales')), { destination: 'Sales' });
  assert.equal(await dependent.count(), 0);
});

async function mountBroadcastFiles(t, { send, preview, journal } = {}) {
  const page = await browser.newPage();
  t.after(() => page.close());
  const requests = [];
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.href === 'https://synthetic.test/') return route.fulfill({ body: '', contentType: 'text/html' });
    if (url.origin !== 'https://synthetic.test' || !url.pathname.startsWith('/static/')) {
      requests.push(url.href);
    }
    if (url.pathname === '/api/v1/management/users/client-card-view-config') {
      return route.fulfill({ json: { blocks: [], rules: [] } });
    }
    if (url.pathname.endsWith('/preview')) return preview ? preview(route) : route.fulfill({ json: broadcastAudience() });
    if (url.pathname.includes('/operations/')) {
      return journal ? journal(route) : route.fulfill({ json: {
        operation: url.pathname.endsWith('/latest') ? null : broadcastOperation(),
      } });
    }
    if (url.pathname.endsWith('/send') && send) return send(route);
    return route.abort();
  });
  await page.goto('https://synthetic.test/');
  const template = fs.readFileSync('api/app/templates/mass_notifications.html', 'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
  await page.setContent(template);
  await page.evaluate(() => { window.HelpdeskSidebar = { init: () => Promise.resolve() }; });
  await loadBroadcastScripts(page);
  await page.waitForFunction(() => !document.getElementById('sendBroadcastBtn').disabled);
  return { page, requests };
}

function broadcastFile(name, size = 1) {
  return { name, mimeType: 'application/octet-stream', buffer: Buffer.alloc(size) };
}

test('mass notification selected files show metadata and enforce the cumulative limit', async t => {
  const { page, requests } = await mountBroadcastFiles(t);
  const initialRequests = [...requests];
  const input = page.locator('#broadcastFilesInput');
  const count = page.locator('#broadcastFilesCount');
  const list = page.locator('#broadcastFilesList');
  const names = list.locator('.selected-file-name');
  const initialName = '<b>notice.txt';
  assert.equal(await count.textContent(), '0 из 10');
  assert.equal(await list.evaluate(element => element.classList.contains('hidden')), true);

  await input.setInputFiles(broadcastFile(initialName, 1536));
  assert.deepEqual(await names.allTextContents(), [initialName]);
  assert.equal(await names.locator('b').count(), 0);
  assert.equal(await list.locator('.selected-file-size').textContent(), '1.5 КБ · Ожидает отправки');
  assert.equal(await list.locator('.selected-file-progress').count(), 0);
  assert.equal(await count.textContent(), '1 из 10');

  const batch = Array.from({ length: 8 }, (_, i) => broadcastFile(`file-${i}.txt`));
  await input.setInputFiles(batch);
  assert.equal(await count.textContent(), '9 из 10');
  const firstNine = [initialName, ...batch.map(file => file.name)];
  await input.setInputFiles([broadcastFile('rejected-a.txt'), broadcastFile('rejected-b.txt')]);
  assert.equal(await page.locator('#errorText').inputValue(), 'Можно добавить не более 10 файлов');
  assert.deepEqual(await names.allTextContents(), firstNine);
  assert.equal(await input.inputValue(), '');
  await page.locator('#okErrorBtn').click();

  await input.setInputFiles(broadcastFile('tenth.txt', 1024 ** 2));
  assert.equal(await list.locator('.selected-file-size').last().textContent(), '1.0 МБ · Ожидает отправки');
  assert.equal(await count.textContent(), '10 из 10');
  await input.setInputFiles(broadcastFile('eleventh.txt'));
  assert.equal(await page.locator('#errorText').inputValue(), 'Можно добавить не более 10 файлов');
  assert.deepEqual(await names.allTextContents(), [...firstNine, 'tenth.txt']);
  assert.equal(await count.textContent(), '10 из 10');
  await page.locator('#okErrorBtn').click();

  await list.getByRole('button', { name: 'Убрать' }).nth(1).click();
  assert.deepEqual(await names.allTextContents(), [initialName, ...firstNine.slice(2), 'tenth.txt']);
  assert.equal(await count.textContent(), '9 из 10');
  await input.setInputFiles(broadcastFile('eleventh.txt'));
  assert.equal(await names.last().textContent(), 'eleventh.txt');
  assert.equal(await list.locator('.selected-file-size').last().textContent(), '1 Б · Ожидает отправки');
  assert.equal(await count.textContent(), '10 из 10');
  assert.deepEqual(requests, initialRequests);
});

test('mass notification selected files are locked until the send response arrives', async t => {
  let finishSend;
  const response = new Promise(resolve => { finishSend = () => resolve({ operation_id: 'mock-operation', status: 'queued', total_count: 1, created: true }); });
  const { page } = await mountBroadcastFiles(t, { send: async route => route.fulfill({ status: 202, json: await response }) });
  const input = page.locator('#broadcastFilesInput');
  const add = page.locator('#addBroadcastFilesBtn');
  const remove = page.getByRole('button', { name: 'Убрать' });
  const names = page.locator('#broadcastFilesList .selected-file-name');
  await input.setInputFiles([broadcastFile('first.txt'), broadcastFile('second.txt')]);
  await page.locator('#broadcastText').fill('Mock notice');
  const [request] = await Promise.all([
    page.waitForRequest(req => req.method() === 'POST' && req.url().endsWith('/mass-notifications/send')),
    page.waitForEvent('dialog', { timeout: 5000 }).then(dialog => dialog.accept()),
    page.locator('#sendBroadcastBtn').click(),
  ]);

  try {
    assert.equal(await add.isDisabled(), true);
    assert.equal(await input.isDisabled(), true);
    assert.deepEqual(await remove.evaluateAll(buttons => buttons.map(button => button.disabled)), [true, true]);
    await input.evaluate(element => {
      const selection = new DataTransfer();
      selection.items.add(new File(['new'], 'late.txt'));
      element.files = selection.files;
      element.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await remove.first().dispatchEvent('click');
    assert.deepEqual(await names.allTextContents(), ['first.txt', 'second.txt']);
    assert.equal(await input.inputValue(), '');
    assert.deepEqual([...request.postDataBuffer().toString().matchAll(/name="files"; filename="([^"]+)"/g)]
      .map(match => match[1]), ['first.txt', 'second.txt']);
  } finally {
    finishSend();
  }

  await page.locator('#newBroadcastBtn').waitFor();
  assert.equal(await page.locator('#sendBroadcastBtn').isDisabled(), true);
  await page.locator('#newBroadcastBtn').click();
  assert.equal(await add.isEnabled(), true);
  assert.equal(await input.isEnabled(), true);
  assert.equal(await names.count(), 0);
  await input.setInputFiles(broadcastFile('late.txt'));
  assert.deepEqual(await names.allTextContents(), ['late.txt']);
});


test('mass notifications validate formatting and require another confirmation when the audience changes', async t => {
  let fingerprint = 'a'.repeat(64);
  const posts = [];
  const { page } = await mountBroadcastFiles(t, {
    preview: route => route.fulfill({ json: broadcastAudience(1, fingerprint) }),
    send: route => {
      posts.push(route.request().postData());
      return route.fulfill({ status: 202, json: { operation_id: 'mock-operation', status: 'queued', total_count: 1 } });
    },
  });
  const textarea = page.locator('#broadcastText');
  const send = page.locator('#sendBroadcastBtn');
  await textarea.fill('<script>bad</script>');
  await send.click();
  await page.locator('#errorModal:not(.hidden)').waitFor();
  assert.match(await page.locator('#errorText').inputValue(), /Telegram-тег/);
  await page.locator('#okErrorBtn').click();
  assert.equal(posts.length, 0);

  await textarea.fill('Alert');
  await textarea.selectText();
  await page.locator('[data-format-action="bold"]').click();
  assert.equal(await page.locator('#broadcastPreview b').textContent(), 'Alert');
  await textarea.evaluate(element => {
    element.setSelectionRange(element.value.length, element.value.length);
    element.dispatchEvent(new Event('select'));
  });
  await page.locator('[data-format-action="emoji"]').click();
  const emoji = page.locator('#telegramTextEmojiPicker [data-emoji]').first();
  const value = await emoji.getAttribute('data-emoji');
  await emoji.click();
  assert.ok((await textarea.inputValue()).includes(value));
  assert.ok((await page.locator('#broadcastPreview').textContent()).includes(value));

  fingerprint = 'b'.repeat(64);
  await send.click();
  await page.locator('#errorModal:not(.hidden)').waitFor();
  assert.match(await page.locator('#errorText').inputValue(), /Аудитория изменилась/);
  assert.equal(posts.length, 0);
  await page.locator('#okErrorBtn').click();
  const confirmed = page.waitForEvent('dialog', { timeout: 5000 }).then(async dialog => {
    assert.match(dialog.message(), /\(1\)/);
    await dialog.accept();
  });
  await Promise.all([confirmed, send.click()]);
  await page.locator('#newBroadcastBtn').waitFor();
  assert.equal(posts.length, 1);
  assert.match(posts[0], /name="audience_fingerprint"\r\n\r\nb{64}/);
  assert.match(posts[0], /name="text_format"\r\n\r\ntelegram_html/);
  assert.match(await page.locator('#resultBox').textContent(), /Рассылка принята/);
  assert.equal(await send.isDisabled(), true);
});


test('mass notifications retry an unchanged draft with its original key after a network failure', async t => {
  const keys = [];
  const { page } = await mountBroadcastFiles(t, {
    send: route => {
      keys.push(route.request().postData().match(/name="idempotency_key"\r\n\r\n([0-9a-f-]+)/)[1]);
      return keys.length === 1 ? route.abort('failed') : route.fulfill({
        status: 202, json: { operation_id: 'mock-operation', status: 'queued', total_count: 1 },
      });
    },
  });
  await page.evaluate(() => { window.confirm = () => true; });
  const textarea = page.locator('#broadcastText');
  const send = page.locator('#sendBroadcastBtn');
  await textarea.fill('Keep the draft');
  await send.click();
  await page.locator('#errorModal:not(.hidden)').waitFor();
  assert.match(await page.locator('#errorText').inputValue(), /Не удалось подтвердить принятие.*дубликаты/);
  assert.equal(await textarea.inputValue(), 'Keep the draft');
  await page.locator('#okErrorBtn').click();
  await textarea.fill('Changed draft');
  await send.click();
  await page.locator('#errorModal:not(.hidden)').waitFor();
  assert.equal(keys.length, 1);
  await page.locator('#okErrorBtn').click();
  await textarea.fill('Keep the draft');
  await send.click();
  await page.getByText('Рассылка принята · mock-operation', { exact: true }).waitFor();
  assert.equal(keys.length, 2);
  assert.equal(keys[0], keys[1]);
});


test('mass notifications use upload events and show authoritative journal progress and errors', async t => {
  let poll = 0;
  const files = [
    { index: 0, original_filename: '<b>first.pdf', file_size: 1024, status: 'uploading' },
    { index: 1, original_filename: 'failed.pdf', file_size: 1, status: 'unknown', error: 'Не подтверждено' },
  ];
  const { page } = await mountBroadcastFiles(t, {
    send: route => route.fulfill({ status: 202, json: { operation_id: 'mock-operation', status: 'queued', total_count: 2 } }),
    journal: route => route.fulfill({ json: { operation: route.request().url().endsWith('/latest') ? null : broadcastOperation({
      status: ++poll === 1 ? 'running' : 'failed', total_count: 2, processed_count: 1,
      error_message: poll > 1 ? 'Операция остановлена' : null,
      attachments: poll === 1 ? files : [ { ...files[0], status: 'ready' }, files[1] ],
      recipients: [{ client_name: '<img src=x>', status: 'sent', components: [{ key: 'text', kind: 'text', status: 'sent' }] }],
    }) } }),
  });
  await page.evaluate(() => {
    window.confirm = () => true;
    const send = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function (form) {
      this.upload.addEventListener('loadstart', () => {
        window.progressDuringUpload = document.querySelectorAll('#broadcastFilesList .selected-file-progress-fill').length;
      });
      this.upload.addEventListener('loadend', () => {
        window.progressAfterUpload = document.querySelectorAll('#broadcastFilesList .selected-file-progress-fill').length;
      });
      // Chromium can skip upload completion when Playwright fulfills the POST locally.
      this.addEventListener('load', () => {
        if (window.progressAfterUpload === undefined) this.upload.dispatchEvent(new ProgressEvent('loadend'));
      });
      return send.call(this, form);
    };
  });
  await page.locator('#broadcastFilesInput').setInputFiles([broadcastFile('first.pdf', 1024), broadcastFile('failed.pdf')]);
  assert.equal(await page.locator('.selected-file-progress').count(), 0);
  await page.locator('#sendBroadcastBtn').click();
  const journal = page.locator('#broadcastJournal');
  await journal.locator('.selected-file-progress-fill').waitFor({ state: 'attached', timeout: 5000 });
  assert.equal(await page.evaluate(() => window.progressDuringUpload), 2);
  assert.equal(await page.evaluate(() => window.progressAfterUpload), 0);
  assert.equal(await journal.locator('.selected-file-progress').count(), 1);
  assert.match(await journal.textContent(), /Не подтверждено/);
  await journal.getByText('Операция остановлена', { exact: true }).waitFor();
  assert.equal(await journal.locator('.selected-file-progress').count(), 0);
  assert.equal(await journal.locator('img').count(), 0);
  assert.match(await journal.textContent(), /Обработано 1 из 2/);
  assert.match(await journal.textContent(), /Доставлено: 1/);
  assert.match(await journal.textContent(), /Текст · Доставлено/);
  assert.match(await journal.textContent(), /Подготовлено/);
});


test('mass notifications discard an edited snapshot and do not retry a server audience conflict', async t => {
  let previewCalls = 0;
  let posts = 0;
  let fingerprint = 'a'.repeat(64);
  let releasePreview;
  const delayedPreview = new Promise(resolve => { releasePreview = resolve; });
  const { page } = await mountBroadcastFiles(t, {
    preview: async route => {
      if (++previewCalls === 2) await delayedPreview;
      return route.fulfill({ json: broadcastAudience(1, fingerprint) });
    },
    send: route => {
      if (++posts === 1) {
        fingerprint = 'b'.repeat(64);
        return route.fulfill({ status: 409, json: { detail: { error: { code: 'audience_changed' } } } });
      }
      return route.fulfill({ status: 202, json: { operation_id: 'mock-operation', status: 'queued', total_count: 1 } });
    },
  });
  await page.evaluate(() => { window.confirm = () => true; });
  const send = page.locator('#sendBroadcastBtn');
  const text = page.locator('#broadcastText');
  await text.fill('Initial draft');
  try {
    await send.click();
    await text.fill('Changed while checking');
  } finally {
    releasePreview();
  }
  await page.locator('#errorModal:not(.hidden)').waitFor();
  assert.match(await page.locator('#errorText').inputValue(), /Черновик изменился/);
  assert.equal(posts, 0);
  await page.locator('#okErrorBtn').click();
  await send.click();
  await page.locator('#errorModal:not(.hidden)').waitFor();
  assert.match(await page.locator('#errorText').inputValue(), /Аудитория изменилась/);
  assert.equal(posts, 1);
  await page.locator('#okErrorBtn').click();
  await send.click();
  await page.getByText('Рассылка принята · mock-operation', { exact: true }).waitFor();
  assert.equal(posts, 2);
});
