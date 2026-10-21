const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

let browser;
test.before(async () => { browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });

async function setup(t, name) {
  const page = await browser.newPage();
  t.after(() => page.close());
  const source = fs.readFileSync(`api/app/static/${name}.js`, 'utf8');
  assert.match(source, /taskPanelStatus: "loading"/);
  const between = (start, end) => {
    const from = source.indexOf(start), to = source.indexOf(end, from);
    assert.ok(from >= 0 && to > from);
    return source.slice(from, to);
  };
  await page.setContent(`<main class="main" data-shell-page="${name === 'list' ? 'tickets-list' : 'ticket-card'}" style="height:600px">
    <header class="toolbar" style="height:40px">Шапка</header>
    <div id="taskPanel" class="task-panel hidden">
    <div id="taskPanelSlots" class="task-panel-slots"></div>
    <button id="taskPanelMoreBtn" class="task-panel-more hidden">…</button>
    <div id="taskPanelDropdown" class="task-panel-dropdown hidden"></div></div>
    <section class="content" style="grid-template-columns:1fr"><div class="panel">Основной контейнер</div></section></main>`);
  await page.addStyleTag({ content: fs.readFileSync('api/app/static/styles.css', 'utf8') });
  await page.evaluate(() => {
    window.state = { role: 'agent', taskPanelEnabled: true, taskPanelItems: [], taskPanelStatus: 'loading' };
    window.el = Object.fromEntries(['taskPanel', 'taskPanelSlots', 'taskPanelMoreBtn', 'taskPanelDropdown']
      .map(id => [id, document.getElementById(id)]));
    window.errors = [];
    window.calls = [];
    window.showError = message => errors.push(message);
    window.escapeHtml = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;');
    window.ticketHref = window.taskPanelTicketHref = id => `/tickets/${id}`;
    window.api = url => {
      calls.push(url);
      return new Promise((resolve, reject) => { window.resolveRequest = resolve; window.rejectRequest = reject; });
    };
  });
  await page.addScriptTag({ content:
    between('function canUseTaskPanel(', name === 'list' ? 'function allowedTicketColumnKeys(' : 'function taskPanelReturnUrl(')
    + between('function taskPanelItemHtml(', name === 'list' ? 'function closeOnBackdropClick(' : 'function cleanupTicketCardPage('),
  });
  await page.evaluate(() => renderTaskPanel());
  return page;
}

async function begin(page, silent = false) {
  await page.evaluate(silent => { window.pending = loadTaskPanel({ silent }); }, silent);
}

async function reply(page, items) {
  await page.evaluate(async items => { resolveRequest(items); await pending; }, items);
}

for (const name of ['list', 'ticket']) {
  test(`${name}: loading, empty, populated and overflow states replace each other without duplicates`, async t => {
    const page = await setup(t, name);
    assert.equal(await page.locator('#taskPanel').isVisible(), true);
    assert.equal(await page.locator('#taskPanelSlots').textContent(), 'Загрузка…');
    await begin(page);
    assert.equal(await page.locator('#taskPanelSlots').textContent(), 'Загрузка…');
    await reply(page, []);
    assert.equal(await page.locator('#taskPanel').isVisible(), true);
    assert.equal(await page.locator('#taskPanelSlots .task-panel-chip').count(), 1);
    assert.equal(await page.locator('#taskPanelSlots').textContent(), 'Нет заявок');
    assert.equal(await page.locator('#taskPanelSlots a').count(), 0);
    await page.evaluate(() => renderTaskPanel());
    assert.equal(await page.locator('#taskPanelSlots .task-panel-chip').count(), 1);
    await begin(page);
    assert.equal(await page.locator('#taskPanelSlots').textContent(), 'Нет заявок');
    const items = Array.from({ length: 9 }, (_, i) => ({ id: String(i), subject: `Заявка ${i}`, has_unread: i === 0 }));
    await reply(page, items);
    assert.deepEqual(await page.locator('#taskPanelSlots a .task-panel-subject').allTextContents(), items.slice(0, 8).map(i => i.subject));
    assert.equal(await page.locator('#taskPanelSlots .task-panel-unread').count(), 1);
    assert.equal(await page.locator('#taskPanelMoreBtn').isVisible(), true);
    assert.equal(await page.locator('#taskPanelDropdown a').count(), 1);
    assert.equal(await page.locator('#taskPanelSlots').textContent().then(s => s.includes('Нет заявок')), false);
    await begin(page);
    await reply(page, []);
    assert.equal(await page.locator('#taskPanelSlots').textContent(), 'Нет заявок');
    assert.equal(await page.locator('#taskPanelDropdown').innerHTML(), '');
    assert.equal(await page.locator('#taskPanelMoreBtn').isVisible(), false);
  });

  test(`${name}: failed requests including silent polling are not shown as an empty list`, async t => {
    const page = await setup(t, name);
    for (const silent of [false, true]) {
      await begin(page, silent);
      await page.evaluate(async () => { rejectRequest(new Error('test API failure')); await pending; });
      assert.equal(await page.locator('#taskPanel').isVisible(), true);
      assert.equal(await page.locator('#taskPanelSlots').textContent(), 'Не удалось загрузить панель задач');
      assert.equal(await page.locator('#taskPanelSlots .task-panel-chip').count(), 1);
    }
    assert.equal(await page.evaluate(() => errors.length), 1);
    await begin(page);
    assert.equal(await page.locator('#taskPanelSlots').textContent(), 'Не удалось загрузить панель задач');
    await reply(page, []);
    assert.equal(await page.locator('#taskPanelSlots').textContent(), 'Нет заявок');
    assert.deepEqual(await page.evaluate(() => [...new Set(calls)]), ['/api/v1/tickets/task-panel']);
  });

  test(`${name}: disabled preferences and disallowed roles stay hidden, agent and trainee retain access`, async t => {
    const page = await setup(t, name);
    for (const [role, enabled, visible] of [['agent', false, false], ['trainee', false, false],
      ['admin', true, false], ['chiki_piki', true, false], ['agent', true, true], ['trainee', true, true]]) {
      await page.evaluate(({ role, enabled }) => {
        state.role = role; state.taskPanelEnabled = enabled; state.taskPanelStatus = 'ready';
        renderTaskPanel();
      }, { role, enabled });
      assert.equal(await page.locator('#taskPanel').isVisible(), visible, `${role}/${enabled}`);
      if (!visible) {
        await page.evaluate(() => loadTaskPanel());
        assert.equal(await page.locator('#taskPanelSlots').innerHTML(), '');
      } else {
        assert.equal(await page.locator('#taskPanelSlots').textContent(), 'Нет заявок');
      }
    }
    assert.equal(await page.evaluate(() => calls.length), 0);
  });

  for (const populated of [false, true]) {
    test(`${name}: identical background replies preserve DOM, focus and the open dropdown (${populated})`, async t => {
      const page = await setup(t, name);
      const items = populated ? Array.from({ length: 10 }, (_, i) => ({ id: String(i), subject: `Заявка ${i}` })) : [];
      await begin(page);
      await reply(page, items);
      await page.evaluate(populated => {
        if (populated) {
          state.taskPanelDropdownOpen = true;
          el.taskPanelDropdown.classList.remove('hidden');
          el.taskPanelMoreBtn.classList.add('active');
          el.taskPanelDropdown.querySelector('a').focus();
        }
        window.savedChip = el.taskPanelSlots.firstElementChild;
        window.savedLink = el.taskPanelDropdown.firstElementChild;
        window.mutations = [];
        window.observer = new MutationObserver(records => mutations.push(...records));
        observer.observe(el.taskPanel, { subtree: true, childList: true, attributes: true, characterData: true });
      }, populated);
      for (let i = 0; i < 3; i++) {
        await begin(page, true);
        assert.equal(await page.locator('#taskPanelSlots').textContent().then(s => s.includes('Загрузка…')), false);
        await reply(page, items);
      }
      assert.equal(await page.evaluate(() => mutations.length), 0);
      assert.equal(await page.evaluate(() => savedChip === el.taskPanelSlots.firstElementChild), true);
      if (populated) {
        assert.equal(await page.evaluate(() => savedLink === el.taskPanelDropdown.firstElementChild && document.activeElement === savedLink), true);
        assert.equal(await page.locator('#taskPanelDropdown').isVisible(), true);
        assert.equal(await page.evaluate(() => state.taskPanelDropdownOpen), true);
        await begin(page, true);
        await reply(page, items.map((item, i) => ({ ...item, subject: i === 8 ? 'Изменено' : item.subject })));
        assert.equal(await page.locator('#taskPanel').isVisible(), true);
        assert.equal(await page.locator('#taskPanelDropdown').isVisible(), true);
        assert.equal(await page.locator('#taskPanelDropdown a').first().textContent(), 'Изменено');
        await page.evaluate(() => {
          el.taskPanelDropdown.addEventListener('click', event => { event.preventDefault(); window.clicked = event.target.closest('a')?.getAttribute('href'); });
        });
        await page.locator('#taskPanelDropdown a').first().click();
        assert.equal(await page.evaluate(() => clicked), '/tickets/8');
      } else {
        assert.equal(await page.locator('#taskPanelSlots').textContent(), 'Нет заявок');
      }
    });
  }

  test(`${name}: visible panel has symmetric 3px gaps and unchanged horizontal insets`, async t => {
    const page = await setup(t, name);
    await begin(page);
    await reply(page, []);
    const gaps = await page.evaluate(() => {
      const rect = selector => document.querySelector(selector).getBoundingClientRect();
      return { top: rect('#taskPanel').top - rect('.toolbar').bottom,
        bottom: rect('.content > .panel').top - rect('#taskPanel').bottom,
        left: rect('#taskPanel').left - rect('main').left,
        right: rect('main').right - rect('#taskPanel').right };
    });
    assert.deepEqual(gaps, { top: 3, bottom: 3, left: 12, right: 12 });
    await page.evaluate(() => { state.taskPanelEnabled = false; renderTaskPanel(); });
    assert.equal(await page.locator('.content').evaluate(node => getComputedStyle(node).paddingTop), '12px');
    await page.evaluate(() => { document.querySelector('main').dataset.shellPage = 'other-page'; });
    assert.equal(await page.locator('#taskPanel').evaluate(node => getComputedStyle(node).marginTop), '0px');
    assert.equal(await page.locator('.content').evaluate(node => getComputedStyle(node).paddingTop), '12px');
  });
}
