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

test('mass notification selectors load configured values and keep filter parameters', async t => {
  const page = await browser.newPage();
  t.after(() => page.close());
  const requests = [];
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    requests.push(url);
    const result = url.pathname.endsWith('/filter-options')
      ? { values: { destination: ['Отдел Продаж', 'New destination'], sales_group: ['New group'],
                    sand_owner: ['New owner'], location_type: ['New location'] }[url.searchParams.get('filter_field')] }
      : [];
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(result) });
  });
  await page.goto('https://synthetic.test/');
  const template = fs.readFileSync('api/app/templates/mass_notifications.html', 'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
  await page.setContent(template);
  await page.evaluate(() => { window.HelpdeskSidebar = { init: () => Promise.resolve() }; });
  await page.addScriptTag({ content: fs.readFileSync('api/app/static/mass_notifications.js', 'utf8') });
  await page.waitForFunction(() => document.querySelector('#filterFieldSelect option[value="New destination"]'));
  await page.selectOption('#filterFieldSelect', 'Отдел Продаж');
  assert.deepEqual(await page.locator('#filterValueSelect option').evaluateAll(options => options.map(o => o.value)), ['', 'New group']);
  assert.deepEqual(await page.locator('#locationTypeSelect option').evaluateAll(options => options.map(o => o.value)), ['', 'New location']);
  await page.selectOption('#filterValueSelect', 'New group');
  await page.waitForFunction(() => document.getElementById('recipientCount').textContent === '0');
  const query = requests.filter(url => url.pathname.endsWith('/recipients')).at(-1).searchParams;
  assert.equal(query.get('filter_field'), 'destination');
  assert.equal(query.get('filter_value'), 'Отдел Продаж');
  assert.equal(query.get('branch_field'), 'sales_group');
  assert.equal(query.get('branch_value'), 'New group');
});
