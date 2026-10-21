const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const template = fs.readFileSync('api/app/templates/management_tasks.html', 'utf8')
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, '');
const styles = fs.readFileSync('api/app/static/styles.css', 'utf8');
const source = fs.readFileSync('api/app/static/management_tasks.js', 'utf8');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });

async function setup(t, theme, editing) {
  const page = await browser.newPage();
  t.after(() => page.close());
  await page.setContent(template);
  await page.evaluate(() => {
    const other = document.createElement('div');
    other.className = 'modal-body';
    other.innerHTML = '<label id="otherCheckboxLabel"><input id="otherCheckbox" type="checkbox">Другая форма</label>';
    document.body.append(other);
  });
  await page.addStyleTag({ content: styles });
  await page.evaluate(({ theme }) => {
    document.body.dataset.theme = theme;
    document.body.dataset.sidebarReady = '1';
    window.confirm = () => true;
    const scheduled = { definition_id: 'scheduled', kind: 'scheduled', title: 'Task', description: 'Text',
      assignment_mode: 'distribution', responsible_admin: { id: 'admin' },
      display_author: true, notify_on_completion: true, scheduled_for: '2099-10-21T10:00:00' };
    window.fetch = async (url) => ({ ok: true, text: async () => JSON.stringify(url.endsWith('/options')
      ? { current_admin_id: 'admin', app_timezone: 'Europe/Moscow', assignees: [], admins: [{ id: 'admin', full_name: 'Admin' }] }
      : { active: [], completed: [], scheduled: [scheduled], recurring: [{ ...scheduled,
        definition_id: 'recurring', kind: 'recurring', state: 'active', recurrence_mode: 'selected_weekdays',
        recurrence_config: { weekdays: [1, 3], time: '10:00' }, cycle_start_at: '2099-10-21T10:00:00', cycle_end_mode: 'never' }] }) });
    window.HelpdeskSidebar = { init: async () => ({ role: 'admin' }) };
    window.HelpdeskAppShell = { registerPage: (_, init) => { window.initTasks = init; } };
  }, { theme });
  await page.addScriptTag({ content: source });
  await page.evaluate(() => window.initTasks({ root: document.querySelector('#appContent') }));
  await page.waitForFunction(() => document.querySelector('#internalTasksStatus').hidden);
  if (editing) {
    await page.locator(`[data-internal-task-tab="${editing}"]`).click();
    await page.locator(`[data-edit-${editing}]`).click();
  } else {
    await page.locator('#internalTaskCreateBtn').click();
  }
  assert.equal(await page.locator('#internalTaskModal input[type="checkbox"]').count(), 10);
  return page;
}

async function checkVisibleBoxes(page, expected) {
  const boxes = page.locator('#internalTaskModal input[type="checkbox"]:visible');
  assert.equal(await boxes.count(), expected);
  for (const box of await boxes.all()) {
    const metrics = await box.evaluate(input => {
      const label = input.closest('label'), css = getComputedStyle(input), rect = input.getBoundingClientRect();
      const range = document.createRange();
      range.selectNodeContents(label);
      range.setStartAfter(input);
      const text = range.getBoundingClientRect();
      return { id: input.id || input.value, display: getComputedStyle(label).display,
        appearance: css.appearance, width: rect.width, height: rect.height,
        padding: css.padding, shadow: css.boxShadow, background: css.backgroundColor,
        left: rect.right <= text.left + 1, centered: Math.abs((rect.top + rect.bottom - text.top - text.bottom) / 2) < 3 };
    });
    assert.match(metrics.display, /flex/, metrics.id);
    assert.ok(['auto', 'checkbox'].includes(metrics.appearance), metrics.id);
    assert.ok(metrics.width > 0 && metrics.width <= 20 && metrics.height <= 20, JSON.stringify(metrics));
    assert.equal(metrics.padding, '0px', metrics.id);
    assert.equal(metrics.shadow, 'none', metrics.id);
    assert.equal(metrics.background, 'rgba(0, 0, 0, 0)', metrics.id);
    assert.ok(metrics.left && metrics.centered, JSON.stringify(metrics));
    const checked = await box.isChecked();
    await box.setChecked(!checked);
    assert.equal(await box.isChecked(), !checked);
  }
}

for (const theme of ['light', 'dark']) {
  for (const editing of [null, 'scheduled', 'recurring']) {
    test(`native task checkboxes: ${theme}, ${editing || 'create'}`, async t => {
      const page = await setup(t, theme, editing);
      if (editing !== 'recurring') await checkVisibleBoxes(page, 3);
      if (!editing) {
        await page.locator('#internalTaskTiming').selectOption('scheduled');
        await checkVisibleBoxes(page, 3);
        await page.locator('#internalTaskTiming').selectOption('recurring');
      }
      if (editing !== 'scheduled') {
        for (const mode of ['daily', 'selected_weekdays', 'weekly', 'monthly', 'every_n_days']) {
          await page.locator('#internalTaskRecurrenceMode').selectOption(mode);
          await checkVisibleBoxes(page, mode === 'selected_weekdays' ? 10 : 3);
          assert.equal(await page.locator('[data-internal-task-weekday]:disabled').count(), mode === 'selected_weekdays' ? 0 : 7);
        }
        await page.locator('#internalTaskRecurrenceMode').selectOption('selected_weekdays');
        assert.equal(await page.locator('#internalTaskWeekdaysWrap').evaluate(node => getComputedStyle(node).flexWrap), 'wrap');
        for (const end of ['never', 'at', 'count']) {
          await page.locator('#internalTaskCycleEndMode').selectOption(end);
          assert.equal(await page.locator('#internalTaskCycleEndAt').isVisible(), end === 'at');
          assert.equal(await page.locator('#internalTaskCycleMax').isVisible(), end === 'count');
        }
      }
      await page.locator('#internalTaskDeadlineEnabled').check();
      assert.equal(await page.locator('#internalTaskDeadlineAt').isVisible(), true);
      await page.locator('#internalTaskDeadlineEnabled').uncheck();
      assert.equal(await page.locator('#internalTaskDeadlineAt').isVisible(), false);
      assert.equal(await page.locator('#adminAgentToggleAll').evaluate(node => node.closest('#internalTaskModal')), null);
      assert.deepEqual(await page.locator('#otherCheckbox').evaluate(node => ({
        padding: getComputedStyle(node).padding,
        display: getComputedStyle(node.closest('label')).display,
        decorated: getComputedStyle(node).boxShadow !== 'none',
      })), { padding: '0px', display: 'grid', decorated: true });
    });
  }
}
