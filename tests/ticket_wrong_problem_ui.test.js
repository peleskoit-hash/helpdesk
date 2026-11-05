const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

test('agent and admin expand renamed submenus and submit only the selected leaf key', async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    for (const role of ['agent', 'admin']) {
      const page = await browser.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.setContent('<style>.hidden { display: none; }</style><div id="modal" class="hidden"><div id="menu"></div></div><button id="action"></button>');
      await page.addScriptTag({ content: fs.readFileSync('api/app/static/ticket_controls.js', 'utf8') });
      await page.evaluate(async role => {
        const leaf = { key: 'menu|ticket|Проблема с техникой/Inner/Internet', label: 'Проблема с интернетом', kind: 'ticket' };
        const rows = [{ buttons: [{ key: 'old-section-key', kind: 'submenu', label: 'Техническая проблема',
          children: [{ buttons: [{ key: 'inner-key', kind: 'submenu', label: 'Вложенное меню',
            children: [{ buttons: [leaf] }] }] }] }] }];
        window.posts = [];
        window.HelpdeskDialogs = { confirm: async () => true };
        window.ctx = {
          state: { role, ticketId: 'ticket', card: { workflow_type: 'ticket' } },
          el: { wrongProblemModal: document.querySelector('#modal'), wrongProblemMenu: document.querySelector('#menu'),
            wrongProblemBtn: document.querySelector('#action') },
          api: async () => ({ rows }), apiPost: async (url, body) => window.posts.push({ url, body }),
          setStatus() {}, loadCard: async () => {}, showError(message) { throw new Error(message); },
        };
        await window.TicketControls.openWrongProblemModal(window.ctx);
        await window.TicketControls.chooseWrongProblem(window.ctx, rows[0].buttons[0]);
      }, role);
      assert.equal(await page.getByText('Проблема с интернетом', { exact: true }).isVisible(), false);
      await page.getByText('Техническая проблема ▸', { exact: true }).click();
      await page.getByText('Вложенное меню ▸', { exact: true }).click();
      assert.deepEqual(await page.evaluate(() => window.posts), []);
      await page.getByText('Проблема с интернетом', { exact: true }).click();
      await page.waitForFunction(() => window.posts.length === 1);
      assert.deepEqual(await page.evaluate(() => window.posts), [{ url: '/api/v1/tickets/ticket/wrong-problem',
        body: { button_key: 'menu|ticket|Проблема с техникой/Inner/Internet' } }]);
      assert.equal(await page.locator('#action').isDisabled(), true);
      assert.deepEqual(errors, []);
      await page.close();
    }
  } finally {
    await browser.close();
  }
});
