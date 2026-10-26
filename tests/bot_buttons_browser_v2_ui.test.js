// Real templates/CSS/scripts with isolated API fixtures; no local DB or output files.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });


for(const theme of ['light','dark'])test(`bot menu browser V2: ${theme}`,async t=>{
 const page=await browser.newPage({viewport:{width:1920,height:1080}});t.after(()=>page.close());
 const calls=[],errors=[];page.on('pageerror',e=>errors.push(e.message));
 const me={id:'admin',role:'admin',login:'admin',full_name:'Администратор'};
 const leaf=(key,type='task')=>({kind:'terminal',label:key,technical_key:key,workflow_type:type,full_path:key,children_rows:[],is_enabled:true});
 const sub={kind:'submenu',label:'Подменю',technical_key:'sub',level_key:'sublevel',full_path:'Подменю',children_rows:[{columns:1,buttons:[leaf('Вложенная заявка','ticket')]}]};
 let rows=[{columns:1,buttons:[sub]},{columns:2,buttons:[leaf('Заявка','ticket'),leaf('Автоматическая','auto')]},{columns:1,buttons:[leaf('Задача')]}];
 const colors={'sub':'','Заявка':'primary','Автоматическая':'success','Задача':'danger'};
 const menus=[{menu_name:'Базовое',display_name:'Основное меню',destinations:['Колл Центр'],ok:true,buttons_count:5,menus_count:2}];
 const structure=()=>({root_level_key:'root',tree_rows:rows,removed_items:[]});
 await page.route('**/*',async route=>{
  const req=route.request(),u=new URL(req.url()),p=u.pathname;
  if(u.hostname!=='desk.test')return route.abort();
  if(p.startsWith('/static/')){const f=path.resolve('api/app',p.slice(1));return route.fulfill({status:fs.existsSync(f)?200:404,body:fs.existsSync(f)?fs.readFileSync(f):'',contentType:{'.css':'text/css','.js':'text/javascript','.ttf':'font/ttf','.svg':'image/svg+xml'}[path.extname(f)]||'application/octet-stream'});}
  if(!p.startsWith('/api/'))return route.fulfill({contentType:'text/html',body:fs.readFileSync('api/app/templates/settings_bot_buttons.html','utf8')});
  calls.push({path:p,method:req.method(),data:req.postDataJSON()});let data={};
  if(p.endsWith('/appearance-session'))data={writer_id:'00000000-0000-4000-8000-000000000001',preferences:{theme_preference:theme,appearance_revision:'0'}};
  else if(p.endsWith('/preferences'))data={theme_preference:theme,appearance_revision:'0'};
  else if(p.endsWith('/auth/session'))data={real_user:me,effective_user:me,is_real_admin:true,session_login_ts:1};
  else if(p.endsWith('/users/me')||p.endsWith('/users/me/profile'))data=me;
  else if(p.endsWith('/users/agents')||p.endsWith('/users/by-role')||p.endsWith('/assignees'))data=[];
  else if(p.endsWith('/counts'))data={active:0,frozen:0,closed:0,special:0,unassigned:0,by_assignee:{}};
  else if(p.endsWith('/menu-types/archived'))data=[];
  else if(p.endsWith('/destination-options'))data={items:['Колл Центр'],free:[]};
  else if(p.endsWith('/menu-types')){if(req.method()==='POST'){const input=req.postDataJSON();menus.push({menu_name:input.display_name,display_name:input.display_name,destinations:[],ok:true,buttons_count:0,menus_count:1});data=menus.at(-1);}else data=menus;}
  else if(p.endsWith('/structure'))data=structure();
  else if(p.endsWith('/layout')){
   if(req.method()==='PUT'){const byKey=new Map(rows.flatMap(r=>r.buttons).map(n=>[n.technical_key,n]));rows=req.postDataJSON().rows.map(r=>({columns:r.columns,buttons:r.button_keys.map(key=>byKey.get(key))}));}data={structure:structure()};
  }
  else if(p.endsWith('/item/create')){const input=req.postDataJSON();rows.push({columns:1,buttons:[leaf(input.label,input.workflow_type)]});data={ok:true};}
  else if(p.endsWith('/item'))data={button_style:colors[u.searchParams.get('button_key')]||null,button_key:u.searchParams.get('button_key'),workflow_type:'task',button_label:'Задача',currently_present:true,files_effective:[],conditional_files_effective:[]};
  return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
 });
 const colorGeometry=new Map();
 async function checkColors(){
  const expected={'':null,primary:'rgb(47, 128, 237)',success:'rgb(39, 174, 96)',danger:'rgb(235, 87, 87)'};
  await page.waitForFunction(()=>document.querySelector('.bot-button-color-danger'));
  for(const [key,style] of Object.entries(colors)){
   const values=await page.locator('[data-button-color-key]').evaluateAll((nodes,key)=>{
    const el=nodes.find(n=>n.dataset.buttonColorKey===key),css=getComputedStyle(el),tag=el.querySelector('.bot-button-type-tag'),border=getComputedStyle(el,'::after');
    const lum=color=>color.match(/[\d.]+/g).slice(0,3).map(Number).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;}).reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0);
    const contrast=(a,b)=>(Math.max(lum(a),lum(b))+.05)/(Math.min(lum(a),lum(b))+.05);
    return {bg:css.backgroundColor,height:el.getBoundingClientRect().height,radius:css.borderRadius,contrast:contrast(css.color,css.backgroundColor),tagContrast:contrast(getComputedStyle(tag).color,getComputedStyle(tag).backgroundColor),border:border.borderWidth,style:border.borderStyle,pointer:border.pointerEvents};
   },key);
   if(expected[style])assert.equal(values.bg,expected[style]);
   assert.ok(values.contrast>=4.5,JSON.stringify({key,...values}));assert.ok(values.tagContrast>=4.5,JSON.stringify({key,...values}));
   assert.equal(values.radius,'16px');assert.ok(values.height>=64);
   if(colorGeometry.has(key))assert.equal(values.height,colorGeometry.get(key),'Colors and submenu outline preserve height');else colorGeometry.set(key,values.height);
   if(key==='sub'){assert.equal(values.border,'5px');assert.equal(values.style,'dashed');assert.equal(values.pointer,'none');}
  }
 }
 await page.goto('http://desk.test/settings/bot-buttons');await page.waitForSelector('[data-menu-name]');
 assert.equal(await page.locator('#botButtonsRightPanel').count(),0);
 assert.match(await page.locator('[data-menu-name]').first().innerText(),/Основное меню[\s\S]*Колл Центр[\s\S]*Кнопки: 5[\s\S]*Подменю: 1/);
 const bounds=()=>page.evaluate(()=>{const main=document.querySelector('#appContent').getBoundingClientRect(),panel=document.querySelector('.bot-buttons-panel'),r=panel.getBoundingClientRect(),header=document.querySelector('.toolbar').getBoundingClientRect();return {top:r.top-header.bottom,left:r.left-main.left,right:main.right-r.right,bottom:main.bottom-r.bottom,radius:getComputedStyle(panel).borderRadius};});
 assert.deepEqual(await bounds(),{top:14,left:16,right:16,bottom:16,radius:'24px'});
 await page.locator('[data-menu-name]').first().click();await page.waitForSelector('#editLayoutBtn');
 assert.equal(await page.locator('#addLevelItemBtn').count(),1);
 assert.equal(await page.locator('#editLayoutBtn').innerText(),'Изменить структуру');
 assert.equal(await page.locator('.bot-button-submenu .bot-button-type-tag').innerText(),'Подменю');
 assert.equal(await page.locator('.bot-buttons-row').nth(1).locator('.bot-button-type-tag').last().innerText(),'Автоматическая задача');
 for(const width of [1920,768]){
  await page.setViewportSize({width,height:1080});
  assert.equal(await page.locator('.bot-buttons-level-item').first().evaluate(el=>getComputedStyle(el).fontSize),'21px');
  assert.ok(await page.locator('.bot-buttons-level-item').first().evaluate(el=>el.getBoundingClientRect().height>=64));
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 }
 await page.setViewportSize({width:1920,height:1080});
 await page.locator('.bot-button-submenu').click();await page.waitForSelector('#editSubmenuBtn');
 assert.match(await page.locator('.bot-buttons-level-list').innerText(),/Вложенная заявка/);
 await page.locator('#botButtonsBackBtn').click();await page.waitForSelector('#editLayoutBtn');
 await checkColors();
 const colorReads=calls.filter(c=>c.path.endsWith("/item")).length;
 await page.locator('#editLayoutBtn').click();await page.locator('.bot-structure-submenu > summary').click();
 await checkColors();
 assert.ok(calls.filter(c=>c.path.endsWith("/item")).length<=colorReads+1,"Structure mode reuses cached colors and reads the nested button");
 assert.match(await page.locator('.bot-structure-submenu').innerText(),/Вложенная заявка[\s\S]*Перенести сюда: Подменю/);
 await page.locator('[data-layout-drag-button="2:0"]').dragTo(page.locator('[data-layout-new-row-before="0"]'));
 assert.equal(await page.locator('[data-layout-drag-button]').first().locator('span').first().innerText(),'Задача');
 await page.locator('#cancelLayoutBtn').click();assert.equal(calls.filter(c=>c.method==='PUT').length,0);
 for(const style of ['primary','success','danger']){
  colors.sub=style;
  await page.locator('#botButtonsBackBtn').click();await page.waitForSelector('#createMenuTypeBtn');
  await page.locator('[data-menu-name]').first().click();await page.waitForSelector('#editLayoutBtn');await checkColors();
  await page.locator('#editLayoutBtn').click();await checkColors();await page.locator('#cancelLayoutBtn').click();
 }
 colors.sub='';

 await page.locator('#editLayoutBtn').click();
 await page.locator('[data-layout-drag-button="2:0"]').dragTo(page.locator('[data-layout-new-row-before="0"]'));
 await page.locator('#saveLayoutBtn').click();await page.waitForSelector('#editLayoutBtn');
 assert.equal(calls.find(c=>c.method==='PUT').data.rows[0].button_keys[0],'Задача');
 // Existing type picker stays unchanged; cancelling must not create anything.
 await page.locator('#addLevelItemBtn').click();await page.locator('#cancelCreateItemBtn').click();
 await page.locator('#addLevelItemBtn').click();await page.locator('#botCreateLabelInput').fill('Новая кнопка');await page.locator('#applyCreateItemBtn').click();
 await page.getByRole('button',{name:'Новая кнопка Заявка',exact:true}).waitFor();
 assert.ok(calls.some(c=>c.path.endsWith('/item/create')&&c.data.label==='Новая кнопка'&&c.data.parent_path===''));
 await page.getByRole('button',{name:'Новая кнопка Заявка',exact:true}).click();await page.locator('#botLabelInput').waitFor();
 assert.equal(await page.locator('.bot-buttons-v2-browse').count(),0,'Concrete editor keeps its existing UI');
 await page.locator('#botButtonsBackBtn').click();await page.waitForSelector('#editLayoutBtn');
 await page.locator('#botButtonsBackBtn').click();await page.waitForSelector('#createMenuTypeBtn');
 await page.locator('#createMenuTypeBtn').click();await page.locator('#botCreateMenuNameInput').fill('Новое меню');await page.locator('#applyCreateMenuBtn').click();
 await page.waitForSelector('#editLayoutBtn');assert.ok(calls.some(c=>c.method==='POST'&&c.path.endsWith('/menu-types')));
 await page.setViewportSize({width:320,height:1080});assert.deepEqual(await bounds(),{top:12,left:12,right:12,bottom:12,radius:'24px'});
 assert.deepEqual(errors,[]);
});
