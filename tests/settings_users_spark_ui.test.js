// Real settings template/script/styles, isolated API fixtures, no DEV services.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({channel:'msedge', headless:true}); });
test.after(async () => { await browser?.close(); });

async function setup(t, theme='light', realShell=false) {
  const page = await browser.newPage({viewport:{width:1100,height:850}});
  t.after(()=>page.close());
  const state = {patches:[], posts:0, autoError:null, phase:'success', polls:0, result:'success', errors:[]};
  const binding = {user_id:'a',local_active:true,enabled:false,external_employee_id:null,
    schedule:{status:'unknown',date:'2026-11-20',reason:'not_connected'}};
  const users = [{id:'a',full_name:'Сохранённое ФИО',login:'agent',role:'agent',is_active:true},
    {id:'admin',full_name:'Администратор',role:'admin',is_active:true},
    {id:'observer',full_name:'Наблюдатель',role:'chiki_piki',is_active:true}];
  page.on('pageerror',error=>state.errors.push(error.message));
  const sync = () => ({enabled:true,configured:true,schema_ready:true,status:state.phase,
    last_attempt_at:state.lastAttempt || (state.phase === 'running' || state.existingAttempt ? '2026-11-20T12:00:00Z' : '2026-11-19T12:00:00Z'),
    last_success_at:state.successAt || '2026-11-19T12:01:00Z',
    last_error_code:state.phase==='error'?'network_error':null,months:[]});
  await page.route('**/*',async route=>{
    const request=route.request(), url=new URL(request.url()), p=url.pathname;
    const json=(data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
    if(url.hostname!=='desk.test')return route.abort();
    if(p==='/settings/users')return route.fulfill({contentType:'text/html',body:fs.readFileSync('api/app/templates/settings_users.html','utf8')});
    if(p==='/static/sidebar.js')return route.fulfill({contentType:'text/javascript',body:'window.HelpdeskSidebar={init:async()=>{}};'});
    if(p==='/static/app_shell.js' && !realShell)return route.fulfill({contentType:'text/javascript',body:''});
    if(p.startsWith('/static/')) {
      const file=path.resolve('api/app',p.slice(1));
      return route.fulfill({status:fs.existsSync(file)?200:404,body:fs.existsSync(file)?fs.readFileSync(file):'',
        contentType:{'.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.ttf':'font/ttf'}[path.extname(file)]||'application/octet-stream'});
    }
    if(p==='/api/v1/settings/users')return json(users);
    if(p.match(/\/users\/[^/]+\/schedule$/)) {
      if(request.method()==='DELETE') {
        state.deletes=(state.deletes||0)+1;
        state.onDelete?.();
        if(state.deleteGate)await state.deleteGate;
        if(state.deleteError)return json({detail:{error:{code:'unlink_failed',message:'Не удалось отвязать сотрудника'}}},500);
        Object.assign(binding,{enabled:false,external_employee_id:null,external_full_name:null,
          external_department_id:null,external_department_name:null,schedule:{status:'unknown',date:'2026-11-20',reason:'not_connected'}});
      }
      if(request.method()==='PATCH') {
        const payload=request.postDataJSON();state.patches.push(payload);
        if(state.bindError)return json({detail:{error:{code:state.bindError,message:'Synthetic'}}},409);
        if(payload.enabled && !payload.external_employee_id && !binding.external_employee_id)
          return json({detail:{error:{code:'external_id_required',message:'Select an ID'}}},400);
        binding.enabled=payload.enabled;
        if(payload.enabled)Object.assign(binding,{external_employee_id:payload.external_employee_id||binding.external_employee_id||'1',
          external_full_name:'Сотрудник Spark',external_department_name:'Поддержка',external_department_id:'10',
          schedule:{status:'working',date:'2026-11-20',start_at:'2026-11-20T08:00:00Z',end_at:'2026-11-20T14:00:00Z'}});
      }
      return json(binding);
    }
    if(p==='/api/v1/settings/employee-schedule/employees') {
      assert.equal(url.searchParams.get('user_id'),'a');
      return json([{external_employee_id:'1',full_name:'Сотрудник Spark',department_name:'Поддержка',available:true},
        {external_employee_id:'2',full_name:'Другой сотрудник',department_name:'Бухгалтерия',available:true}]
        .filter(employee=>employee.external_employee_id!==binding.external_employee_id));
    }
    if(p==='/api/v1/settings/employee-schedule/sync') {
      state.posts++;state.phase='running';state.polls=0;
      state.lastAttempt=`2026-11-20T12:${String(state.posts).padStart(2,'0')}:00Z`;
      if(state.race)return json({detail:{error:{code:'running',message:'Already running'}}},409);
      return json({status:'accepted'},202);
    }
    if(p==='/api/v1/settings/employee-schedule') {
      if(state.posts && state.staleReads>0) {
        state.staleReads--;
        return json({...sync(),status:'success',last_attempt_at:'2026-11-19T12:00:00Z',last_success_at:'2026-11-19T12:01:00Z'});
      }
      if(state.phase==='running' && ++state.polls>=3 && !state.hold) {
        state.phase=state.result;
        if(state.result==='success') state.successAt=state.lastAttempt || '2026-11-20T12:00:00Z';
      }
      return json(sync());
    }
    return json({});
  });
  await page.goto('https://desk.test/settings/users');
  await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);
  await page.locator('[data-user-id="a"]').click();
  await page.locator('#openSparkBtn').waitFor({state:'visible'});
  return {page,state,binding};
}
async function open(page) {
  await page.locator('#openSparkBtn').click();
  await page.waitForFunction(()=>!document.querySelector('#sparkRefreshBtn').disabled);
}
async function selectEmployee(page, id='1') {
  await page.locator('#sparkChooseBtn').click();
  await page.locator(`#sparkEmployees option[value="${id}"]`).waitFor({state:'attached'});
  await page.locator('#sparkEmployees').selectOption(id);
  await page.locator('#sparkConfirm').check();
  await page.locator('#sparkBindBtn').click();
}

for(const theme of ['light','dark']) test(`Spark modal preserves draft and fits ${theme}`,async t=>{
  const {page,state}=await setup(t,theme);
  await page.locator('#fFullName').fill('Незаписанное имя');
  await page.locator('#fLogin').fill('draft-login');
  await page.locator('#fPassword').fill('draft-password');
  assert.equal(await page.locator('#sparkBlock .spark-summary').evaluate(el=>{
    const [a,b]=[...el.children].map(child=>child.getBoundingClientRect());
    return Math.abs(a.top-b.top)<2 && a.right<b.left;
  }),true);
  await open(page);
  assert.equal(await page.locator('#userModal').isVisible(),false);
  assert.equal(await page.locator('#sparkLocalName').textContent(),'ES DESK: Сохранённое ФИО');
  assert.match(await page.locator('#sparkShift').textContent(),/неизвестно/);
  for(const width of [1100,390]) {
    await page.setViewportSize({width,height:850});
    const fits=await page.locator('#sparkModal .modal-dialog').evaluate(el=>{
      const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight&&el.scrollWidth<=el.clientWidth+1;
    });assert.ok(fits);
  }
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#userModal').isVisible(),true);
  assert.equal(await page.locator('#fFullName').inputValue(),'Незаписанное имя');
  assert.equal(await page.locator('#fLogin').inputValue(),'draft-login');
  assert.equal(await page.locator('#fPassword').inputValue(),'draft-password');
  await page.locator('#closeUserModalBtn').click();
  await page.locator('#createUserBtn').click();
  assert.equal(await page.locator('#sparkBlock').isVisible(),false);
  await page.locator('#closeUserModalBtn').click();
  await page.locator('[data-user-id="observer"]').click();
  assert.equal(await page.locator('#sparkBlock').isVisible(),false);
  await page.locator('#closeUserModalBtn').click();
  await page.locator('[data-user-id="admin"]').click();
  assert.equal(await page.locator('#sparkBlock').isVisible(),true);
  assert.deepEqual(state.errors,[]);
});

test('manual binding, disable and reenable keep saved ID',async t=>{
  const {page,state}=await setup(t);await open(page);
  assert.equal(await page.getByRole('button',{name:'Найти и подключить',exact:true}).count(),0);
  assert.equal(await page.locator('#sparkReconnectBtn').isVisible(),false);
  await selectEmployee(page);
  await page.waitForFunction(()=>document.querySelector('#sparkBindingStatus').textContent==='Подключено');
  assert.match(await page.locator('#sparkShift').textContent(),/11:00–17:00 МСК/);
  assert.equal(await page.locator('#sparkSyncStatus').evaluate(el=>el.style.color),'var(--desk-success)');
  await page.locator('#sparkDisableBtn').click();
  await page.waitForFunction(()=>document.querySelector('#sparkBindingStatus').textContent==='Не подключено');
  await page.locator('#sparkReconnectBtn').click();
  await page.waitForFunction(()=>document.querySelector('#sparkBindingStatus').textContent==='Подключено');
  assert.deepEqual(state.patches,[{enabled:true,external_employee_id:'1'},{enabled:false},{enabled:true}]);
  await page.locator('#closeSparkBtn').click();
  assert.equal(await page.locator('#sparkSummary').textContent(),'Подключено');
});

test('searchable manual selection excludes occupied IDs and requires rebind confirmation',async t=>{
  const {page,state}=await setup(t);await open(page);
  await page.locator('#sparkChooseBtn').click();
  await page.locator('#sparkEmployees option').first().waitFor({state:'attached'});
  assert.equal(await page.locator('#sparkEmployees option[value="3"]').count(),0);
  for(const query of ['Бухгалтерия','2','Другой']) {
    await page.locator('#sparkSearch').fill(query);
    assert.equal(await page.locator('#sparkEmployees option').count(),1);
  }
  await page.locator('#sparkEmployees').selectOption('2');
  assert.equal(await page.locator('#sparkBindBtn').isDisabled(),true);
  await page.locator('#sparkConfirm').check();await page.locator('#sparkBindBtn').click();
  await page.waitForFunction(()=>document.querySelector('#sparkBindingStatus').textContent==='Подключено');
  await page.locator('#sparkChooseBtn').click();
  await page.locator('#sparkEmployees option[value="1"]').waitFor({state:'attached'});
  assert.equal(await page.locator('#sparkEmployees option[value="2"]').count(),0);
  await page.locator('#sparkEmployees').selectOption('1');
  assert.match(await page.locator('#sparkConfirmText').textContent(),/замену.*ID 2.*ID 1/);
  assert.equal(await page.locator('#sparkBindBtn').isDisabled(),true);
  await page.locator('#sparkConfirm').check();await page.locator('#sparkBindBtn').click();
  await page.waitForFunction(()=>document.querySelector('#sparkIdentity').textContent.includes('ID Spark: 1'));
  assert.deepEqual(state.patches,[{enabled:true,external_employee_id:'2'},{enabled:true,external_employee_id:'1'}]);
  assert.deepEqual(state.errors,[]);
});

for(const result of ['success','error']) test(`background sync waits for its result: ${result}`,async t=>{
  const {page,state}=await setup(t);state.result=result;await open(page);
  const posts=state.posts;
  const posted=page.waitForResponse(response=>response.url().endsWith('/employee-schedule/sync'));
  await page.locator('#sparkRefreshBtn').click();
  await posted;
  await page.waitForFunction(()=>document.querySelector('#sparkSyncStatus').textContent.includes('выполняется'));
  assert.equal(state.posts,posts+1);
  assert.equal(await page.locator('#sparkRefreshBtn').isDisabled(),true);
  await page.waitForFunction(()=>!document.querySelector('#sparkRefreshBtn').disabled);
  assert.equal(state.posts,posts+1);
  if(result==='error') {
    assert.match(await page.locator('#sparkSyncStatus').textContent(),/не подтверждено.*ранее сохранённые/);
    assert.match(await page.locator('#sparkSyncTimes').textContent(),/Ошибочная попытка/);
    assert.equal(await page.locator('#sparkSyncStatus').evaluate(el=>el.style.color),'var(--desk-danger)');
  } else assert.match(await page.locator('#sparkSyncTimes').textContent(),/20.11.2026/);
  assert.deepEqual(state.errors,[]);
});

test('closing during sync prevents stale results changing another user',async t=>{
  const {page,state}=await setup(t);await open(page);state.hold=true;
  await page.locator('#sparkRefreshBtn').click();
  await page.waitForFunction(()=>document.querySelector('#sparkSyncStatus').textContent.includes('выполняется'));
  await page.locator('#closeSparkBtn').click();
  await page.locator('#closeUserModalBtn').click();
  await page.locator('[data-user-id="admin"]').click();
  state.hold=false;
  await page.waitForTimeout(1100);
  assert.equal(await page.locator('#sparkModal').isVisible(),false);
  assert.equal(await page.locator('#fFullName').inputValue(),'Администратор');
  assert.deepEqual(state.errors,[]);
});

test('existing running attempt is monitored without posting another sync',async t=>{
  const {page,state}=await setup(t);state.phase='running';state.existingAttempt=true;state.hold=true;
  await page.locator('#openSparkBtn').click();
  await page.waitForFunction(()=>document.querySelector('#sparkSyncStatus').textContent.includes('выполняется'));
  assert.equal(state.posts,0);
  state.hold=false;
  await page.waitForFunction(()=>!document.querySelector('#sparkRefreshBtn').disabled);
  assert.equal(state.posts,0);
  assert.equal(await page.locator('#sparkFeedback').textContent(),'');
});

test('concurrent sync claim is monitored after 409 running',async t=>{
  const {page,state}=await setup(t);await open(page);state.race=true;
  const posts=state.posts;
  const posted=page.waitForResponse(response=>response.url().endsWith('/employee-schedule/sync'));
  await page.locator('#sparkRefreshBtn').click();
  await posted;
  await page.waitForFunction(()=>document.querySelector('#sparkSyncStatus').textContent.includes('выполняется'));
  await page.waitForFunction(()=>!document.querySelector('#sparkRefreshBtn').disabled);
  assert.equal(state.posts,posts+1);
  assert.equal(await page.locator('#sparkFeedback').textContent(),'');
});

for(const code of ['external_id_in_use','spark_network_error']) test(`binding error is actionable: ${code}`,async t=>{
  const {page,state}=await setup(t);state.bindError=code;await open(page);
  await selectEmployee(page);
  await page.waitForFunction(()=>document.querySelector('#sparkFeedback').textContent.length>0);
  assert.match(await page.locator('#sparkFeedback').textContent(),code==='external_id_in_use'?/другому пользователю/:/Spark недоступен/);
  assert.equal(await page.locator('#sparkBindingStatus').textContent(),'Не подключено');
  assert.equal(await page.locator('#sparkRefreshBtn').isEnabled(),true);
});

test('Spark resources use matching versions on direct and AppShell entry',()=>{
  const template=fs.readFileSync('api/app/templates/settings_users.html','utf8');
  const shell=fs.readFileSync('api/app/static/app_shell.js','utf8');
  for(const text of [template,shell])assert.ok(text.includes('/static/settings_users.js?v=20261120-spark-unlink-modal-v5'));
  for(const file of fs.readdirSync('api/app/templates').filter(f=>f.endsWith('.html'))) {
    const text=fs.readFileSync('api/app/templates/'+file,'utf8');
    for(const match of text.matchAll(/\/static\/app_shell\.js\?v=([^"']+)/g))assert.equal(match[1],'20261120-spark-unlink-modal-v5',file);
  }
});

test('Spark modal works after actual AppShell navigation without duplicate handlers',async t=>{
  const {page,state}=await setup(t,'light',true);
  await page.evaluate(()=>{window.sparkDocument=document;});
  await page.evaluate(()=>window.HelpdeskAppShell.navigate('/settings/users?check=1'));
  await page.waitForFunction(()=>location.search==='?check=1');
  assert.equal(await page.evaluate(()=>window.sparkDocument===document),true);
  await page.locator('[data-user-id="a"]').click();
  await open(page);
  await selectEmployee(page);
  await page.waitForFunction(()=>document.querySelector('#sparkBindingStatus').textContent==='Подключено');
  assert.deepEqual(state.patches,[{enabled:true,external_employee_id:'1'}]);
  assert.equal(await page.locator('#userModal').isVisible(),false);
  await page.locator('#closeSparkBtn').click();
  assert.equal(await page.locator('#userModal').isVisible(),true);
  assert.deepEqual(state.errors,[]);
});

for(const saved of [false,true]) test(`opening refreshes Spark and safely displays fallback: saved=${saved}`,async t=>{
  const {page,state,binding}=await setup(t);state.result='error';
  if(saved)Object.assign(binding,{enabled:true,external_employee_id:'1',external_full_name:'Сохранённый сотрудник',
    schedule:{status:'working',date:'2026-11-20',start_at:'2026-11-20T08:00:00Z',end_at:'2026-11-20T14:00:00Z'}});
  await open(page);
  assert.equal(state.posts,1,'opening must initiate a real background sync');
  assert.equal(await page.locator('#sparkSyncStatus').evaluate(el=>el.style.color),'var(--desk-danger)');
  assert.match(await page.locator('#sparkSyncTimes').textContent(),/19.11.2026/);
  assert.match(await page.locator('#sparkSyncTimes').textContent(),/Ошибочная попытка/);
  assert.match(await page.locator('#sparkShift').textContent(),saved?/11:00–17:00 МСК/:/неизвестно/);
  assert.deepEqual(state.errors,[]);
});

test('previous successful sync cannot complete a newly started opening refresh',async t=>{
  const {page,state}=await setup(t);state.staleReads=1;state.hold=true;
  const posted=page.waitForResponse(response=>response.url().endsWith('/employee-schedule/sync'));
  await page.locator('#openSparkBtn').click();await posted;
  await page.waitForFunction(()=>document.querySelector('#sparkSyncTimes').textContent.includes('19.11.2026'));
  assert.equal(await page.locator('#sparkRefreshBtn').isDisabled(),true);
  assert.match(await page.locator('#sparkSyncStatus').textContent(),/выполняется/);
  state.hold=false;
  await page.waitForFunction(()=>!document.querySelector('#sparkRefreshBtn').disabled);
  assert.match(await page.locator('#sparkSyncTimes').textContent(),/20.11.2026/);
  assert.equal(state.posts,1);
});

test('opening request timeout is bounded and cannot reuse previous success indication',async t=>{
  const {page,state}=await setup(t);
  await page.evaluate(()=>{
    const original=window.fetch;
    window.fetch=(url,options)=>String(url).endsWith('/employee-schedule/sync')
      ? Promise.reject(new DOMException('Synthetic timeout','TimeoutError')) : original(url,options);
  });
  await open(page);
  assert.match(await page.locator('#sparkFeedback').textContent(),/Истекло время ожидания/);
  assert.equal(await page.locator('#sparkSyncStatus').evaluate(el=>el.style.color),'var(--desk-danger)');
  assert.match(await page.locator('#sparkShift').textContent(),/неизвестно/);
  assert.equal(state.posts,0);
});

for(const enabled of [false,true]) test(`unlink confirmation releases saved Spark ID: enabled=${enabled}`,async t=>{
  const {page,state,binding}=await setup(t);
  Object.assign(binding,{enabled,external_employee_id:'1',external_full_name:'Сотрудник Spark'});
  await open(page);
  assert.equal(await page.locator('#sparkUnlinkBtn').isVisible(),true);
  await page.locator('#sparkUnlinkBtn').click();
  assert.equal(await page.locator('#sparkModal').isVisible(),false);
  assert.equal(await page.locator('#userModal').isVisible(),false);
  assert.equal(await page.locator('#confirmSparkUnlinkModal').isVisible(),true);
  assert.match(await page.locator('#sparkUnlinkIdentity').textContent(),/Сохранённое ФИО.*Сотрудник Spark.*ID Spark: 1/);
  assert.match(await page.locator('#confirmSparkUnlinkModal').textContent(),/ID Spark освобождён/);
  await page.locator('#cancelSparkUnlinkBtn').click();
  assert.equal(state.deletes||0,0);
  assert.equal(await page.locator('#sparkModal').isVisible(),true);
  assert.match(await page.locator('#sparkIdentity').textContent(),/ID Spark: 1/);
  await page.locator('#sparkUnlinkBtn').click();
  await page.keyboard.press('Escape');
  assert.equal(state.deletes||0,0);
  assert.equal(await page.locator('#sparkModal').isVisible(),true);
  await page.locator('#sparkUnlinkBtn').click();
  await page.locator('#confirmSparkUnlinkBtn').click();
  await page.waitForFunction(()=>document.querySelector('#sparkIdentity').textContent==='Сотрудник Spark не выбран');
  assert.equal(state.deletes,1);
  assert.equal(await page.locator('#sparkUnlinkBtn').isVisible(),false);
  assert.equal(await page.locator('#sparkReconnectBtn').isVisible(),false);
  assert.equal(await page.locator('#sparkBindingStatus').textContent(),'Не подключено');
  await selectEmployee(page,'1');
  await page.waitForFunction(()=>document.querySelector('#sparkBindingStatus').textContent==='Подключено');
  assert.deepEqual(state.patches,[{enabled:true,external_employee_id:'1'}]);
});

test('unlink failure keeps binding and duplicate or stale requests cannot change another draft',async t=>{
  const {page,state,binding}=await setup(t);
  Object.assign(binding,{enabled:false,external_employee_id:'1',external_full_name:'Сотрудник Spark'});
  await open(page);state.deleteError=true;
  await page.locator('#sparkUnlinkBtn').click();
  await page.locator('#confirmSparkUnlinkBtn').click();
  await page.waitForFunction(()=>document.querySelector('#sparkFeedback').textContent.includes('Не удалось отвязать'));
  assert.match(await page.locator('#sparkIdentity').textContent(),/ID Spark: 1/);
  assert.equal(await page.locator('#sparkUnlinkBtn').isVisible(),true);
  state.deleteError=false;
  let release;
  state.deleteGate=new Promise(resolve=>{release=resolve;});
  const started=new Promise(resolve=>{state.onDelete=resolve;});
  await page.locator('#sparkUnlinkBtn').click();
  await page.evaluate(()=>{
    const button=document.querySelector('#confirmSparkUnlinkBtn');
    button.dispatchEvent(new Event('click'));button.dispatchEvent(new Event('click'));
  });
  await started;
  assert.equal(state.deletes,2,'only one additional DELETE may be in flight');
  await page.locator('#closeSparkBtn').click();
  await page.locator('#closeUserModalBtn').click();
  await page.locator('[data-user-id="admin"]').click();
  await page.locator('#fFullName').fill('Новый черновик');
  release();
  await page.waitForTimeout(100);
  assert.equal(await page.locator('#fFullName').inputValue(),'Новый черновик');
  assert.equal(await page.locator('#sparkModal').isVisible(),false);
  assert.deepEqual(state.errors,[]);
});

test('unlink confirmation from a previous AppShell page cannot delete another user binding',async t=>{
  const {page,state,binding}=await setup(t,'dark',true);
  Object.assign(binding,{enabled:true,external_employee_id:'1',external_full_name:'Сотрудник Spark'});
  await open(page);
  await page.locator('#sparkUnlinkBtn').click();
  await page.evaluate(()=>{window.oldSparkConfirmation=document.querySelector('#confirmSparkUnlinkBtn');});
  await page.evaluate(()=>window.HelpdeskAppShell.navigate('/settings/users?another=1'));
  await page.locator('[data-user-id="admin"]').click();
  await page.evaluate(()=>window.oldSparkConfirmation.click());
  assert.equal(state.deletes||0,0);
  assert.equal(await page.locator('#fFullName').inputValue(),'Администратор');
  assert.equal(await page.locator('#confirmSparkUnlinkModal').isVisible(),false);
  assert.deepEqual(state.errors,[]);
});
