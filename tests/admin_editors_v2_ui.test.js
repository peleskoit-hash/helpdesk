// Real templates/CSS/scripts with isolated API fixtures; no local DB or output files.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });

const pages = { '/settings/users': 'settings_users', '/settings/automatic-messages': 'settings_automatic_messages', '/mass-notifications': 'mass_notifications', '/management/tasks': 'management_tasks' };
async function setup(t, theme, width) {
 const page = await browser.newPage({viewport:{width,height:width>=1920?1200:900}}); t.after(()=>page.close());
 const calls=[], errors=[]; page.on('pageerror',e=>errors.push(e.message)); t.after(()=>assert.deepEqual(errors,[]));
 const me={id:'admin',role:'admin',login:'admin',full_name:'Администратор',availability_status:'online'};
 const agent={id:'agent',role:'agent',login:'agent',full_name:'Александр Иванов',is_active:true,availability_status:'online',legacy_ticket_count:3,last_login_at:'2026-10-25T10:30:00Z'};
 const item={key:'message.test',title:'Автоматическое сообщение',description:'Условия отправки',effective_text:'Исходный текст',default_text:'Исходный текст',runtime_enabled:true,variables:[]};
 const definition={definition_id:'scheduled',kind:'scheduled',title:'Запланированная задача',description:'Описание',assignment_mode:'distribution',responsible_admin:{id:'admin',full_name:me.full_name},display_author:true,notify_on_completion:true,scheduled_for:'2027-01-01T10:00:00'};
 await page.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url()),p=url.pathname;
  if(url.hostname!=='desk.test')return route.abort();
  if(p.startsWith('/static/')){const file=path.resolve('api/app',p.slice(1));return route.fulfill({status:fs.existsSync(file)?200:404,body:fs.existsSync(file)?fs.readFileSync(file):'',contentType:{'.css':'text/css','.js':'text/javascript','.ttf':'font/ttf','.svg':'image/svg+xml'}[path.extname(file)]||'application/octet-stream'});}
  if(pages[p])return route.fulfill({contentType:'text/html',body:fs.readFileSync(`api/app/templates/${pages[p]}.html`,'utf8')});
  calls.push({path:p,method:req.method(),body:req.postData()});let data={};
  if(p.endsWith('/appearance-session'))data={writer_id:'00000000-0000-4000-8000-000000000001',preferences:{theme_preference:theme,appearance_revision:'0'}};
  else if(p.endsWith('/preferences'))data={theme_preference:theme,appearance_revision:'0'};
  else if(p==='/api/v1/auth/session')data={real_user:me,effective_user:me,is_real_admin:true,session_login_ts:1,app_timezone:'Europe/Moscow'};
  else if(p==='/api/v1/users/me'||p==='/api/v1/users/me/profile')data=me;
  else if(['/api/v1/users/agents','/api/v1/users/by-role','/api/v1/settings/users'].includes(p))data=p==='/api/v1/settings/users'?[agent,{...agent,id:'never',full_name:'Без входов',last_login_at:null}]:[agent];
  else if(p==='/api/v1/settings/users/agent'){Object.assign(agent,req.postDataJSON());data=agent;}
  else if(p.endsWith('/counts'))data={active:0,frozen:0,closed:0,special:0,unassigned:0,by_assignee:{}};
  else if(p==='/api/v1/settings/automatic-messages')data={items:[item],categories:[{category:'first',category_title:'Основные сообщения',items:[item]}]};
  else if(p.startsWith('/api/v1/settings/automatic-messages/')){item.effective_text=req.method()==='DELETE'?item.default_text:req.postDataJSON().body;data=item;}
  else if(p.endsWith('/recipients'))data=[{id:'client',full_name:'Клиент'}];
  else if(p.endsWith('/mass-notifications/send'))data={sent:1,failed:0};
  else if(p.endsWith('/internal-tasks/options'))data={current_admin_id:'admin',app_timezone:'Europe/Moscow',assignees:[agent],admins:[me]};
  else if(p.endsWith('/internal-tasks/overview'))data={active:[],scheduled:[definition],recurring:[],completed:[]};
  else if(p.startsWith('/api/v1/management/internal-tasks/'))data={ok:true};
  return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
 });
 return {page,calls};
}
async function checkPage(page){
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 const skin=await page.locator('#appContent .panel').evaluate(el=>({radius:getComputedStyle(el).borderRadius,font:getComputedStyle(el).fontSize}));
 assert.deepEqual(skin,{radius:'24px',font:'21px'});
 if (page.viewportSize().width <= 700) {
   const inset=await page.locator('#appContent').evaluate(main=>{
     const m=main.getBoundingClientRect(), p=main.querySelector('.panel').getBoundingClientRect(), toolbar=main.querySelector('.toolbar').getBoundingClientRect();
     return {top:p.top-toolbar.bottom,left:p.left-m.left,right:m.right-p.right,bottom:m.bottom-p.bottom};
   });
   assert.deepEqual(inset,{top:12,left:12,right:12,bottom:12});
 }

 assert.equal(await page.locator('#appContent .panel').evaluate(el=>{
   const probe=document.createElement('span');el.append(probe);
   const resolve=token=>{probe.style.color=`var(${token})`;return getComputedStyle(probe).color};
   const css=getComputedStyle(el),match=css.backgroundColor===resolve('--desk-surface')&&css.color===resolve('--desk-text');probe.remove();return match;
 }),true);
 assert.equal(await page.locator('#appContent').evaluate(root=>[...root.querySelectorAll('.panel button')].some(el=>el.clientHeight&&(el.scrollWidth>el.clientWidth+1||el.scrollHeight>el.clientHeight+1))),false);

 assert.equal(await page.locator('.toolbar button').first().evaluate(el=>getComputedStyle(el).fontSize),'14px');
}
async function checkDialog(page,selector){
 const dialog=page.locator(selector);await dialog.waitFor({state:'visible'});
 assert.equal(await dialog.evaluate(el=>el.scrollWidth<=el.clientWidth+1),true);
 assert.equal(await dialog.evaluate(el=>getComputedStyle(el).borderRadius),'24px');
 assert.equal(await dialog.evaluate(el=>{
   const probe=document.createElement('span');el.append(probe);probe.style.color='var(--desk-surface)';
   const match=getComputedStyle(el).backgroundColor===getComputedStyle(probe).color;probe.remove();return match;
 }),true);
 assert.deepEqual(await dialog.evaluate(el=>[...el.querySelectorAll('button,select,textarea')].filter(control=>control.clientHeight&&control.scrollWidth>control.clientWidth+1).map(control=>({id:control.id,width:control.clientWidth,scroll:control.scrollWidth,value:control.value}))),[]);

}
async function activate(page, selector) {
 const target=page.locator(selector).first();
 if(page.viewportSize().width<700){await target.focus();await page.keyboard.press('Enter');}
 else await target.click();
}
for(const theme of ['light','dark'])for(const width of [320,700,1920])test(`second V2 editor package: ${theme}, ${width}`,async t=>{
 const {page,calls}=await setup(t,theme,width);
 await page.goto('http://desk.test/settings/users');await page.waitForSelector('.settings-user-row');
 await checkPage(page);
 assert.equal(await page.locator('.edit-user-btn').count(),0);
 assert.equal(await page.locator('.settings-title').innerText(),'Настройка ролей');
 assert.match(await page.locator('#usersTableBody tr:first-child td').last().innerText(),/25\.10\.2026/);
 assert.equal(await page.locator('#usersTableBody tr:first-child td').nth(2).innerText(),'Тех. поддержка');
 assert.equal(await page.locator('#usersTableBody tr:last-child td').last().innerText(),'—');
 if(width<700) await activate(page,'.settings-user-row');else await page.locator('.settings-user-row td').first().click();await checkDialog(page,'#userModal .modal-dialog');
 assert.equal(await page.locator('#fRole option[value=agent]').innerText(),'Тех. поддержка');
 assert.equal(await page.locator('#fIsActive').evaluate(el=>getComputedStyle(el).appearance),'none');
 await page.locator('#fFullName').fill('Изменённое имя');await page.locator('#saveUserBtn').click();
 await page.waitForFunction(()=>document.querySelector('#usersTableBody').textContent.includes('Изменённое имя'));
 assert.ok(calls.some(c=>c.path.endsWith('/users/agent')&&c.method==='PATCH'));
 await page.locator('.settings-user-row').first().focus();await page.keyboard.press('Space');
 await checkDialog(page,'#userModal .modal-dialog');
 await page.locator('#closeUserModalBtn').click();

 await page.evaluate(()=>window.HelpdeskAppShell.navigate('/settings/automatic-messages'));
 await page.waitForSelector('.automatic-message-section-head');await checkPage(page);
 await activate(page,'summary');await activate(page,'.automatic-message-row');
 await checkDialog(page,'#automaticMessageEditorModal .modal-dialog');
 const editor=await page.locator('#automaticMessageEditorModal').evaluate(el=>{
   const rect=el.querySelector('.modal-dialog').getBoundingClientRect(),textarea=el.querySelector('textarea'),preview=el.querySelector('.bot-telegram-preview-wrap');
   return {width:rect.width,height:rect.height,font:getComputedStyle(textarea).fontSize,textarea:textarea.getBoundingClientRect().height,preview:preview.getBoundingClientRect().height,scroll:getComputedStyle(el.querySelector('.bot-telegram-preview')).scrollbarWidth};
 });
 assert.equal(editor.font,'16.8px');assert.equal(editor.scroll,'thin');
 const previewContrast=await page.locator('#automaticMessageEditorModal .bot-telegram-preview-title').evaluate(el=>{
   const luminance=color=>color.match(/[\d.]+/g).slice(0,3).map(Number).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;}).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
   const text=luminance(getComputedStyle(el).color),bg=luminance(getComputedStyle(el.closest('.bot-telegram-preview-wrap')).backgroundColor);
   return (Math.max(text,bg)+.05)/(Math.min(text,bg)+.05);
 });
 assert.ok(previewContrast>=4.5,`Preview title contrast: ${previewContrast}`);

 assert.ok(Math.abs(editor.textarea-editor.preview)<=2,JSON.stringify(editor));
 if(width>=1920){assert.ok(Math.abs(editor.width-1456)<=2,JSON.stringify(editor));assert.ok(Math.abs(editor.height-1014)<=2,JSON.stringify(editor));}

 await page.locator('#automaticMessageTextarea').fill('Новый текст');
 await page.waitForFunction(()=>document.querySelector('#automaticMessagePreview').textContent.includes('Новый текст'));
 await activate(page,'#automaticMessageApplyBtn');
 assert.ok(calls.some(c=>c.path.endsWith('/automatic-messages/message.test')&&c.method==='PATCH'));
 await page.evaluate(()=>window.HelpdeskAppShell.navigate('/mass-notifications'));
 await page.waitForSelector('#broadcastText');await checkPage(page);
 assert.equal(await page.locator('#filterValueSelect').isDisabled(),true);
 await page.selectOption('#filterFieldSelect','__all__');
 await page.waitForFunction(()=>document.querySelector('#recipientCount').textContent==='1');
 await page.locator('#broadcastText').fill('Только mock-рассылка');await activate(page,'#sendBroadcastBtn');
 await page.waitForFunction(()=>document.querySelector('#resultBox').textContent.includes('Отправлено: 1'));
 assert.equal(calls.filter(c=>c.path.endsWith('/mass-notifications/send')).length,1);
 await page.evaluate(()=>window.HelpdeskAppShell.navigate('/management/tasks'));
 await page.waitForSelector('#internalTaskCreateBtn');await checkPage(page);
 await activate(page,'#internalTaskCreateBtn');await checkDialog(page,'#internalTaskModal .modal-dialog');
 assert.equal(await page.locator('#internalTaskNotify').evaluate(el=>getComputedStyle(el).appearance),'none');
 await page.locator('#internalTaskTiming').selectOption('recurring');
 await page.locator('#internalTaskTitle').fill('Регулярка');await page.locator('#internalTaskDescription').fill('Описание');
 await page.locator('#internalTaskCycleStartAt').fill('2027-01-01T10:00');
 await page.locator('#internalTaskDailyTimes').fill('10:00, 16:30');
 await activate(page,'#internalTaskSaveBtn');
 await page.waitForFunction(()=>document.querySelector('#internalTaskModal').classList.contains('hidden'));
 assert.ok(calls.some(c=>c.path.endsWith('/internal-tasks/recurring')&&c.method==='POST'));
 await activate(page,'[data-internal-task-tab="scheduled"]');await page.waitForSelector('#internalTasksScheduledBody tr');
 assert.equal(await page.locator('#internalTasksScheduledBody td').first().evaluate(el=>getComputedStyle(el).borderTopLeftRadius),'16px');
 assert.equal(await page.locator('.management-tasks-table').first().evaluate(el=>getComputedStyle(el).borderCollapse),'separate');

 await activate(page,'#internalTasksScheduledBody button');await checkDialog(page,'#internalTaskModal .modal-dialog');
});

test('second package leaves the shared layout and sidebar CSS alone',()=>{
 const css=fs.readFileSync('api/app/static/admin-editors-v2.css','utf8');
 assert.doesNotMatch(css,/&\s+\.layout(?:\s|[>{])/);
});

for(const theme of ['light','dark'])test(`four editor bubbles keep mobile geometry: ${theme}, 320`,async t=>{
 const {page}=await setup(t,theme,320);
 for(const url of Object.keys(pages)){
   await page.goto('http://desk.test'+url);
   await page.waitForFunction(()=>document.body.dataset.sidebarReady==='1');
   const bounds=await page.locator('#appContent').evaluate(main=>{
     const m=main.getBoundingClientRect(), p=main.querySelector('.panel').getBoundingClientRect(), bar=main.querySelector('.toolbar').getBoundingClientRect();
     return {top:p.top-bar.bottom,left:p.left-m.left,right:m.right-p.right,bottom:m.bottom-p.bottom,radius:getComputedStyle(main.querySelector('.panel')).borderRadius};
   });
   assert.deepEqual(bounds,{top:12,left:12,right:12,bottom:12,radius:'24px'},url);
 }
});
