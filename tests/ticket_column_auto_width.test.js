const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = vm.createContext({ window: {} });
vm.runInContext(fs.readFileSync('api/app/static/ticket_column_auto_width.js', 'utf8'), context);
const { fill } = context.window.HelpdeskTicketColumnAutoWidth;

test('ticket header overflow decoration is removed and CSS cache versions match every template', () => {
  const css = fs.readFileSync('api/app/static/desk-v2.css', 'utf8');
  assert.doesNotMatch(css, /#ticketsTable\s+thead::after/);
  assert.match(css, /#ticketsTable thead \{ position: sticky; top: 6px; z-index: 3; background: var\(--desk-surface\); box-shadow: 0 -12px 0 12px var\(--desk-surface\); \}/);
  assert.match(fs.readFileSync('api/app/static/styles.css', 'utf8'), /desk-v2\.css\?v=20261106-ticket-table-v2/);
  for (const file of fs.readdirSync('api/app/templates').filter(file => file.endsWith('.html'))) {
    const source = fs.readFileSync(`api/app/templates/${file}`, 'utf8');
    for (const [, version] of source.matchAll(/styles\.css\?v=([^"']+)/g)) {
      assert.equal(version, '20261106-ticket-table-v2', file);
    }
  }
});

test('column scripts and the changed app shell use consistent resource versions at every entry point', () => {
  const shell = fs.readFileSync('api/app/static/app_shell.js', 'utf8');
  const tickets = fs.readFileSync('api/app/templates/tickets.html', 'utf8');
  for (const source of [shell, tickets]) {
    assert.match(source, /ticket_column_auto_width\.js\?v=20261106-column-auto-width-v1/);
    assert.match(source, /\/static\/list\.js\?v=20261111-requester-name-v1/);
  }
  for (const file of fs.readdirSync('api/app/templates').filter(file => file.endsWith('.html'))) {
    const source = fs.readFileSync(`api/app/templates/${file}`, 'utf8');
    for (const [, version] of source.matchAll(/app_shell\.js\?v=([^"']+)/g)) {
      assert.equal(version, '20261111-requester-name-v1', file);
    }
  }
});

for (const systemWidth of [54, 88]) {
  test(`subject uses the remaining viewport width, system columns=${systemWidth}`, () => {
    const columns = [{ key: 'requester', width: 329 }, { key: 'subject', width: 600 }, { key: 'age', width: 100 }];
    const filled = fill(columns, 944.5, systemWidth);
    assert.equal(filled[0].width, 329);
    assert.equal(filled[1].width, Math.floor(944.5 - systemWidth - 429));
    assert.ok(filled.reduce((sum, column) => sum + column.width, systemWidth) <= 944.5);
    assert.equal(columns[1].width, 600, 'saved widths are not mutated');
    assert.equal(fill(columns, 200, systemWidth)[1].width, 180, 'keep subject minimum even when scrolling is necessary');
  });
  test(`ordinary widths are preserved when scrolling is required, system=${systemWidth}`, () => {
    const columns = [{ key: 'requester', width: 163 }, { key: 'subject', width: 600 }, { key: 'age', width: 273 }];
    const fitted = fill(columns, 600, systemWidth);
    assert.equal(fitted[0].width, 163);
    assert.equal(fitted[1].width, 180);
    assert.equal(fitted[2].width, 273);
    assert.ok(fitted.reduce((sum, column) => sum + column.width, systemWidth) > 600);
  });
}

for (const invalidate of ['generation', 'tickets', 'disabled', 'resize', 'preview', 'none']) test(`loaded-page measurement keeps stale guards: ${invalidate}`, async () => {
  const source = fs.readFileSync('api/app/static/list.js', 'utf8');
  const start = source.indexOf('async function measureLoadedAutoColumns()');
  const end = source.indexOf('function fixedTableWidth(', start);
  let resolveFonts;
  const fonts = new Promise(resolve => { resolveFonts = resolve; });
  const state = { ticketListAutoWidth: true, tickets: [{ id: '1' }], ticketListColumns: [{ key: 'subject', width: 180 }], page: 1, tab: 'active' };
  let measured = 0, rendered = 0;
  const ctx = vm.createContext({ state, document: { fonts: { ready: fonts } }, el: { ticketsTable: {}, ticketsTableBody: { rows: [{}] } },
    normalizeTicketColumns: columns => columns, renderTable: () => { rendered++; }, window: { HelpdeskTicketColumnAutoWidth: { measure: () => { measured++; return [{ key: 'subject', width: 180 }]; } } } });
  vm.runInContext('let measuredAutoPage = null; let initialAutoWidthGeneration = 0;' + source.slice(start, end), ctx);
  const pending = vm.runInContext('measureLoadedAutoColumns()', ctx);
  if (invalidate === 'generation') vm.runInContext('initialAutoWidthGeneration++', ctx);
  if (invalidate === 'tickets') state.tickets = [{ id: '2' }];
  if (invalidate === 'disabled') state.ticketListAutoWidth = false;
  if (invalidate === 'resize') state.columnResizeActive = true;
  if (invalidate === 'preview') state.columnDraftPreviewActive = true;
  resolveFonts();
  await pending;
  assert.equal(measured, invalidate === 'none' ? 1 : 0);
  assert.equal(rendered, invalidate === 'none' ? 1 : 0);
  if (invalidate === 'none') {
    await vm.runInContext('measureLoadedAutoColumns()', ctx);
    assert.equal(measured, 1, 'same page signature is not measured again');
  }
});
