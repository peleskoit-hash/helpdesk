// Real templates/CSS/scripts with isolated API fixtures; no local DB or output files.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });


for (const theme of ['light', 'dark']) test(`management client modal V2: ${theme}`, async t => {
 const page = await browser.newPage({viewport:{width:1440,height:1000}}); t.after(()=>page.close());
 const errors=[], calls=[]; page.on('pageerror', e=>errors.push(e.message));
 let acceptDialog=true, dialogCount=0; page.on('dialog', d=>{dialogCount++;return acceptDialog?d.accept():d.dismiss();});
 const me={id:'admin',role:'admin',login:'admin',full_name:'Администратор'};
 let blocked=false;
 const profile={contact_name:'Иван',last_name:'Иванов',is_verified:false,custom_fields:{flag:false,note:'Комментарий'}};
 const config={blocks:[{key:'profile',title:'Профиль',fields:[
  {key:'contact_name',label:'Имя',field_type:'text',is_system:true},
  {key:'last_name',label:'Фамилия',field_type:'text',is_system:true},
 ]},{key:'extra',title:'Дополнительно',fields:[
  {key:'note',label:'Комментарий',field_type:'textarea',is_system:false},
  {key:'flag',label:'Допуск',field_type:'checkbox',is_system:false},
  {key:'is_verified',label:'Верификация',field_type:'checkbox',is_system:true},
 ]}],rules:[]};
 await page.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url()),p=url.pathname;
  if(url.hostname!=='desk.test')return route.abort();
  if(p.startsWith('/static/')){const file=path.resolve('api/app',p.slice(1));return route.fulfill({status:fs.existsSync(file)?200:404,body:fs.existsSync(file)?fs.readFileSync(file):'',contentType:{'.css':'text/css','.js':'text/javascript','.ttf':'font/ttf','.svg':'image/svg+xml'}[path.extname(file)]||'application/octet-stream'});}
  if(p==='/settings')return route.fulfill({contentType:'text/html',body:fs.readFileSync('api/app/templates/settings.html','utf8')});
  if(p==='/management/users')return route.fulfill({contentType:'text/html',body:fs.readFileSync('api/app/templates/management_users.html','utf8')});
  calls.push({path:p,method:req.method(),data:req.postDataJSON()});let data={};
  if(p.endsWith('/appearance-session'))data={writer_id:'00000000-0000-4000-8000-000000000001',preferences:{theme_preference:theme,appearance_revision:'0'}};
  else if(p.endsWith('/preferences'))data={theme_preference:theme,appearance_revision:'0'};
  else if(p==='/api/v1/auth/session')data={real_user:me,effective_user:me,is_real_admin:true,session_login_ts:1};
  else if(p==='/api/v1/users/me'||p==='/api/v1/users/me/profile')data=me;
  else if(p==='/api/v1/users/agents'||p==='/api/v1/users/by-role')data=[];
  else if(p.endsWith('/counts'))data={active:0,frozen:0,closed:0,special:0,unassigned:0,by_assignee:{}};
  else if(p.endsWith('/client-card-view-config'))data=config;
  else if(p==='/api/v1/management/users')data={items:[{client_id:'client',display_name:'Иван Иванов',is_blocked:blocked}],total:1};
  else if(p.endsWith('/block')||p.endsWith('/unblock')){blocked=p.endsWith('/block');data={is_blocked:blocked};}
  else if(p.endsWith('/reset-data'))data={is_blocked:blocked,profile:{...profile,is_verified:false,custom_fields:{flag:false}}};
  else if(p.endsWith('/profile')){if(req.method()==='PATCH')Object.assign(profile,req.postDataJSON());data=profile;}
  return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
 });
 await page.addInitScript(()=>{const add=document.addEventListener.bind(document),remove=document.removeEventListener.bind(document);window.testKeyListeners=new Set();document.addEventListener=(type,handler,...rest)=>{if(type==='keydown')window.testKeyListeners.add(handler);return add(type,handler,...rest);};document.removeEventListener=(type,handler,...rest)=>{if(type==='keydown')window.testKeyListeners.delete(handler);return remove(type,handler,...rest);};});
 await page.goto('http://desk.test/management/users');await page.locator('.management-user-row td').first().click();
 const modal=page.locator('#clientModal'),dialog=modal.locator('.modal-dialog');await dialog.waitFor({state:'visible'});
 assert.equal(await page.locator('body').getAttribute('data-theme'),theme);
 for(const close of [()=>page.locator('#closeClientModalBtn').click(),()=>page.keyboard.press('Escape'),()=>modal.click({position:{x:5,y:5}})]){
  await modal.locator('#fContactName').fill('Несохранённое имя');acceptDialog=false;const before=dialogCount;
  await close();assert.equal(dialogCount,before+1);assert.equal(await modal.isVisible(),true);assert.equal(await modal.locator('#fContactName').inputValue(),'Несохранённое имя');
  acceptDialog=true;await close();await modal.waitFor({state:'hidden'});assert.equal(dialogCount,before+2);
  await page.locator('.management-user-row td').first().click();await dialog.waitFor({state:'visible'});
 }
 const listeners=await page.evaluate(()=>[...window.testKeyListeners].filter(fn=>fn.name==='handlePageKeydown').length);
 await page.evaluate(()=>{window.testCloseCount=0;const close=window.TicketClientCard.closeClientModal;window.TicketClientCard.closeClientModal=(...args)=>{window.testCloseCount++;return close(...args);};});
 await dialog.locator('.modal-head').click();assert.equal(await modal.isVisible(),true);
 await modal.locator('#fContactName').click();await page.keyboard.press('Escape');await modal.waitFor({state:'hidden'});
 assert.equal(await page.evaluate(()=>window.testCloseCount),1);
 await page.locator('.management-user-row td').first().click();await dialog.waitFor({state:'visible'});
 await modal.click({position:{x:5,y:5}});await modal.waitFor({state:'hidden'});
 assert.equal(await page.evaluate(()=>window.testCloseCount),2);
 await page.locator('.management-user-row td').first().click();await dialog.waitFor({state:'visible'});
 assert.equal(calls.filter(c=>c.method==='PATCH').length,0);

 for(const width of [1440,768,320]){
  await page.setViewportSize({width,height:1000});
  const metrics=await dialog.evaluate(el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el),probe=document.createElement('span');el.append(probe);probe.style.color='var(--desk-surface)';const matches=s.backgroundColor===getComputedStyle(probe).color;probe.remove();return {radius:s.borderRadius,matches,fits:r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight,overflow:el.scrollWidth>el.clientWidth+1};});
  assert.deepEqual(metrics,{radius:'24px',matches:true,fits:true,overflow:false});
  assert.ok(Math.abs(await dialog.evaluate(el=>el.getBoundingClientRect().width)-Math.min(576,width-24))<=1);
  assert.equal(await dialog.evaluate(el=>getComputedStyle(el).overflow),'hidden');
  assert.equal(await modal.locator('.modal-body').evaluate(el=>getComputedStyle(el).scrollbarWidth),'thin');
  assert.equal(await modal.locator('input[type=checkbox]').evaluateAll(nodes=>nodes.length>=2&&nodes.every(el=>el.classList.contains('desk-toggle')&&getComputedStyle(el).appearance==='none')),true);
  for(const id of ['resetClientDataBtn','toggleClientBlockBtn','saveClientBtn'])assert.equal(await page.locator('#'+id).isVisible(),true);
 }
 await page.setViewportSize({width:1440,height:1000});
 await modal.locator('#fContactName').fill('Новое имя');
 await modal.locator('[data-custom-field-key="flag"]').check();
 await page.locator('#saveClientBtn').click();await modal.waitFor({state:'hidden'});
 assert.equal(dialogCount,6,'Saving does not ask to discard changes');
 const saved=calls.find(c=>c.path.endsWith('/client/profile')&&c.method==='PATCH');
 assert.equal(saved.data.contact_name,'Новое имя');assert.equal(saved.data.custom_fields.flag,true);
 await page.locator('.management-user-row td').first().click();await dialog.waitFor({state:'visible'});
 await page.locator('#toggleClientBlockBtn').click();await page.waitForFunction(()=>document.querySelector('#toggleClientBlockBtn').textContent.includes('Разблокировать'));
 await page.locator('#toggleClientBlockBtn').click();await page.waitForFunction(()=>document.querySelector('#toggleClientBlockBtn').textContent==='Заблокировать');
 await Promise.all([page.waitForResponse(r=>r.url().endsWith('/reset-data')),page.locator('#resetClientDataBtn').click()]);
 await page.waitForFunction(()=>[...document.querySelectorAll('#clientModal input[type=checkbox]')].every(el=>el.classList.contains('desk-toggle')));
 assert.ok(calls.some(c=>c.path.endsWith('/reset-data')&&c.method==='POST'));
 await page.locator('#closeClientModalBtn').click();await modal.waitFor({state:'hidden'});
 for(let repeat=0;repeat<2;repeat++){
  await page.evaluate(()=>window.HelpdeskAppShell.navigate('/settings'));await page.waitForSelector('[data-shell-page="settings-home"]');
  assert.equal(await page.evaluate(()=>[...window.testKeyListeners].filter(fn=>fn.name==='handlePageKeydown').length),listeners-1);
  await page.evaluate(()=>window.HelpdeskAppShell.navigate('/management/users'));await page.waitForSelector('.management-user-row');
  assert.equal(await page.evaluate(()=>[...window.testKeyListeners].filter(fn=>fn.name==='handlePageKeydown').length),listeners);
  await page.locator('.management-user-row td').first().click();await page.locator('#clientModal .modal-dialog').waitFor({state:'visible'});
  const previous=await page.evaluate(()=>window.testCloseCount);await page.keyboard.press('Escape');await page.locator('#clientModal').waitFor({state:'hidden'});
  assert.equal(await page.evaluate(()=>window.testCloseCount),previous+1);
 }
 assert.deepEqual(errors,[]);
});
