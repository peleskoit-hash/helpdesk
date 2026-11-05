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
  assert.match(css, /#ticketsTable thead \{ background: var\(--desk-surface\); \}/);
  assert.match(fs.readFileSync('api/app/static/styles.css', 'utf8'), /desk-v2\.css\?v=20261105-table-header-overflow-v1/);
  for (const file of fs.readdirSync('api/app/templates').filter(file => file.endsWith('.html'))) {
    const source = fs.readFileSync(`api/app/templates/${file}`, 'utf8');
    for (const [, version] of source.matchAll(/styles\.css\?v=([^"']+)/g)) {
      assert.equal(version, '20261105-table-header-overflow-v1', file);
    }
  }
});

test('column scripts and the changed app shell use consistent resource versions at every entry point', () => {
  const shell = fs.readFileSync('api/app/static/app_shell.js', 'utf8');
  const tickets = fs.readFileSync('api/app/templates/tickets.html', 'utf8');
  for (const source of [shell, tickets]) {
    assert.match(source, /ticket_column_auto_width\.js\?v=20261105-column-auto-width-v6/);
    assert.match(source, /\/static\/list\.js\?v=20261105-column-auto-width-v7/);
  }
  for (const file of fs.readdirSync('api/app/templates').filter(file => file.endsWith('.html'))) {
    const source = fs.readFileSync(`api/app/templates/${file}`, 'utf8');
    for (const [, version] of source.matchAll(/app_shell\.js\?v=([^"']+)/g)) {
      assert.equal(version, '20261105-column-auto-width-v8', file);
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
