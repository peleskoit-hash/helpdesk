const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

let browser;
test.before(async () => { browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });

async function setup(t, name) {
  const page = await browser.newPage({viewport:{width:1600,height:700}});
  t.after(() => page.close());
  const source = fs.readFileSync(`api/app/static/${name}.js`, 'utf8');
  assert.match(source, /taskPanelStatus: "loading"/);
  const between = (start, end) => {
    const from = source.indexOf(start), to = source.indexOf(end, from);
    assert.ok(from >= 0 && to > from);
    return source.slice(from, to);
  };
  await page.setContent(`<main id="appContent" class="main" data-shell-page="${name === 'list' ? 'tickets-list' : 'ticket-card'}" style="height:600px">
    <header class="toolbar" style="height:40px"><button id="backToListBtn">Назад</button>
    <div id="taskPanel" class="task-panel hidden">
    <div id="taskPanelSlots" class="task-panel-slots"></div>
    <button id="taskPanelMoreBtn" class="task-panel-more hidden">…</button>
    <div id="taskPanelDropdown" class="task-panel-dropdown hidden"></div></div>
    <div class="display-settings" style="margin-left:auto;width:100px">Оформление</div></header><section class="content" style="grid-template-columns:1fr"><div class="panel">Основной контейнер</div></section></main>`);
  await page.addStyleTag({ content: fs.readFileSync('api/app/static/styles.css', 'utf8') });
  await page.addStyleTag({ content: fs.readFileSync('api/app/static/desk-v2.css', 'utf8') });
  await page.addScriptTag({ content: fs.readFileSync('api/app/static/task_panel_layout.js', 'utf8') });
  await page.evaluate(() => {
    window.state = { role: 'agent', taskPanelEnabled: true, taskPanelItems: [], taskPanelStatus: 'loading' };
    HelpdeskTaskPanelLayout.restore(state,{userId:'agent',role:'agent',sessionLoginTs:1});
    state.taskPanelEnabled=true;
    window.pageEventController=new AbortController();
    window.destroyed=false;
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
    between('function canUseTaskPanel(', name === 'list' ? 'function allowedTicketColumnKeys(' : 'function taskPanelTicketHref(')
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
  const maximum = name === 'list' ? 6 : 7;
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
    assert.deepEqual(await page.locator('#taskPanelSlots a .task-panel-subject').allTextContents(), items.slice(0, maximum).map(i => i.subject));
    assert.equal(await page.locator('#taskPanelSlots .task-panel-unread').count(), 1);
    assert.equal(await page.locator('#taskPanelMoreBtn').isVisible(), true);
    assert.equal(await page.locator('#taskPanelDropdown a').count(), items.length-maximum);
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
          taskPanelLayout.positionDropdown();
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
        await reply(page, items.map((item, i) => ({ ...item, subject: i === maximum ? 'Изменено' : item.subject })));
        assert.equal(await page.locator('#taskPanel').isVisible(), true);
        assert.equal(await page.locator('#taskPanelDropdown').isVisible(), true);
        assert.equal(await page.locator('#taskPanelDropdown a').first().textContent(), 'Изменено');
        await page.evaluate(() => {
          el.taskPanelDropdown.addEventListener('click', event => { event.preventDefault(); window.clicked = event.target.closest('a')?.getAttribute('href'); });
        });
        await page.locator('#taskPanelDropdown a').first().click();
        assert.equal(await page.evaluate(() => clicked), `/tickets/${maximum}`);
      } else {
        assert.equal(await page.locator('#taskPanelSlots').textContent(), 'Нет заявок');
      }
    });
  }

  for (const role of ['agent','trainee']) test(`${name}: adaptive item counts retain every task for ${role}`, async t => {
    const page=await setup(t,name);
    await page.evaluate(role=>state.role=role,role);
    for(const count of [0,1,5,8,12]) {
      const items=Array.from({length:count},(_,i)=>({id:String(i),subject:'Длинное название '.repeat(8)+i,has_unread:i===0}));
      await begin(page);await reply(page,items);
      const links=await page.locator('#taskPanel a').all();
      assert.equal(links.length,count);
      assert.deepEqual(await page.locator('#taskPanel a').evaluateAll(nodes=>nodes.map(n=>n.getAttribute('href'))),items.map(i=>`/tickets/${i.id}`));
      assert.ok(await page.locator('#taskPanelSlots a').count()<=maximum);
      if(count)assert.equal(await page.locator('#taskPanel .task-panel-unread').count(),1);
    }
  });

  test(`${name}: inline panel has no shell and does not move the header or content`, async t => {
    const page = await setup(t, name);
    await begin(page);
    await reply(page, []);
    const measure = () => page.evaluate(() => ({
      toolbar: document.querySelector('.toolbar').getBoundingClientRect().height,
      content: document.querySelector('.content').getBoundingClientRect().top,
    }));
    const enabled = await measure();
    assert.equal(await page.locator('.toolbar>#taskPanel').count(), 1);
    assert.deepEqual(await page.locator('#taskPanel').evaluate(node => {
      const s=getComputedStyle(node); return [s.backgroundColor,s.borderWidth,s.borderRadius,s.boxShadow,s.padding,s.margin];
    }), ['rgba(0, 0, 0, 0)','0px','0px','none','0px','0px']);
    await page.evaluate(() => { state.taskPanelEnabled = false; renderTaskPanel(); });
    assert.deepEqual(await measure(), enabled);
  });
}


test('ticket: string UUID IDs highlight only the current task without changing links',async t=>{
  const page=await setup(t,'ticket');
  const current='6db5edac-dc60-4c8a-8a33-9b72175a2041', other='14ea1a8a-370f-4235-9c7b-aa572d2bb603';
  await page.evaluate(id=>state.ticketId=id,current);
  await begin(page);await reply(page,[{id:current,subject:'Current'},{id:other,subject:'Other'}]);
  assert.deepEqual(await page.locator('#taskPanel a').evaluateAll(nodes=>nodes.map(el=>({href:el.getAttribute('href'),active:el.getAttribute('aria-current')}))),[
    {href:`/tickets/${current}`,active:'page'},{href:`/tickets/${other}`,active:null},
  ]);
});


test('ticket: panel positions use local toolbar coordinates under scaling',async t=>{
  const page=await setup(t,'ticket');
  await page.evaluate(()=>document.querySelector('.toolbar').style.zoom='0.75');
  await begin(page);await reply(page,Array.from({length:10},(_,i)=>({id:String(i),subject:'Task '+i})));
  const geometry=await page.evaluate(()=>{
    const box=selector=>document.querySelector(selector).getBoundingClientRect();
    const back=box('#backToListBtn'), chip=box('#taskPanelSlots a');
    return {backRight:back.right,chipLeft:chip.left};
  });
  assert.ok(geometry.chipLeft>=geometry.backRight+4,JSON.stringify(geometry));
});


for(const name of ['list','ticket']) {
  test(`${name}: cached tasks hydrate a fresh page and disabled mode stays off`,async t=>{
    const page=await setup(t,name);
    await begin(page);await reply(page,[{id:'private-a',subject:'Private A'}]);
    const result=await page.evaluate(()=>{
      const cache=HelpdeskTaskPanelLayout, identity={userId:'agent',role:'agent',sessionLoginTs:1};
      const next={role:'agent'};cache.restore(next,identity);
      const before={enabled:next.taskPanelEnabled,items:next.taskPanelItems,status:next.taskPanelStatus};
      next.taskPanelEnabled=false;cache.remember(next);
      const disabled={role:'agent'};cache.restore(disabled,identity);
      return {before,disabled};
    });
    assert.equal(result.before.enabled,true);assert.equal(result.before.status,'ready');
    assert.equal(result.before.items[0].id,'private-a');
    assert.equal(result.disabled.taskPanelEnabled,false);assert.deepEqual(result.disabled.taskPanelItems,[]);
  });
  for(const change of [{userId:'other',role:'agent',sessionLoginTs:1},{userId:'agent',role:'trainee',sessionLoginTs:1},{userId:'agent',role:'agent',sessionLoginTs:2}]) {
    test(`${name}: cached data and pending replies cannot cross ${JSON.stringify(change)}`,async t=>{
      const page=await setup(t,name);
      await begin(page);await reply(page,[{id:'private-a',subject:'Private A'}]);
      await begin(page);
      await page.evaluate(identity=>{
        state.role=identity.role;state.userId=identity.userId;
        HelpdeskTaskPanelLayout.restore(state,identity);renderTaskPanel();
      },change);
      await reply(page,[{id:'late-private-a',subject:'Late private A'}]);
      assert.equal(await page.locator('#taskPanel a').count(),0);
      assert.equal(await page.locator('#taskPanel').isVisible(),false);
      assert.deepEqual(await page.evaluate(()=>state.taskPanelItems),[]);
      const back=await page.evaluate(()=>{const next={role:'agent'};HelpdeskTaskPanelLayout.restore(next,{userId:'agent',role:'agent',sessionLoginTs:1});return next;});
      assert.deepEqual(back.taskPanelItems,[]);
    });
  }
}

for(const name of ['list','ticket']) test(`${name}: replies from a destroyed page cannot replace the snapshot`,async t=>{
  const page=await setup(t,name);
  await begin(page);await reply(page,[{id:'current',subject:'Current'}]);
  await begin(page);
  await page.evaluate(name=>{if(name==='list'){pageEventController.abort();pageEventController=null;}else destroyed=true;},name);
  await reply(page,[{id:'obsolete',subject:'Obsolete'}]);
  const items=await page.evaluate(()=>{const next={role:'agent'};HelpdeskTaskPanelLayout.restore(next,{userId:'agent',role:'agent',sessionLoginTs:1});return next.taskPanelItems;});
  assert.equal(items[0].id,'current');
});
