const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
function extract(file, name) {
  const source = fs.readFileSync(`api/app/static/${file}.js`, 'utf8').replaceAll('\r\n','\n');
  const match = new RegExp(`^( *)(?:async )?function ${name}\\([^\\n]*`, 'm').exec(source);
  assert.ok(match, name);
  const end = source.indexOf(`\n${match[1]}}`, match.index + match[0].length);
  assert.ok(end > match.index, name); return source.slice(match.index, end + match[1].length + 2);
}
const cases = [
  ['settings_client_card', 'deleteSource', "deleteSource('id')"],
  ['settings_client_card', 'deleteRule', "deleteRule('id')"],
  ['settings_client_card', 'deleteBlock', "deleteBlock('id')"],
  ['settings_client_card', 'deleteField', "deleteField('id')"],
  ['management_users', 'toggleClientBlock', 'toggleClientBlock(root)'],
  ['management_users', 'resetClientData', 'resetClientData(root)'],
  ...['deleteMaterialized','deleteScheduled','deleteRecurring'].map(name => ['management_tasks', name, `${name}(root, 'id')`]),
  ['management_tasks', 'skipNextRecurring', 'skipNextRecurring(root)'],
  ['management_saved_files', 'replaceSelectedFile', "replaceSelectedFile({name:'new.txt'})"],
  ['management_saved_files', 'deleteSelected', 'deleteSelected()'],
  ['profile_notification_bot', 'restoreBase', 'restoreBase()'],
  ['settings_notification_bot_basic', 'canNavigateAway', 'canNavigateAway()'],
  ['ticket_controls', 'chooseWrongProblem', "chooseWrongProblem(ctx, {key:'id', label:'Результат'})"],
  ['ticket_message_view', 'deleteAgentMessage', "deleteAgentMessage(ctx, {id:'id', message_type:'internal'})"],
  ['ticket_message_view', 'deleteAgentMessage', "deleteAgentMessage(ctx, {id:'id', message_type:'agent'})"],
  ['ticket_client_card', 'closeClientModal', 'closeClientModal(ctx)'],
  ['management_tasks', 'closeModal', 'closeModal(root)'],
];
let browser;
test.before(async () => { browser = await chromium.launch({ channel: 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });
for (const theme of ['light','dark']) test(`remaining handlers wait for themed approval and preserve cancellation: ${theme}`, async t => {
  const page = await browser.newPage({ viewport: { width: 1366, height: 900 } }); t.after(() => page.close());
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  page.on('dialog', async d => { errors.push(`native ${d.type()}`); await d.dismiss(); });
  await page.setContent(`<body data-theme="${theme}"><div class="layout"><main class="main"><div class="ticket-side"><div id="underlying" class="modal"><input id="draft" value="draft"><button id="trigger">Action</button></div></div></main></div></body>`);
  for (const file of ['styles.css','desk-v2.css','desk-dialogs.css']) await page.addStyleTag({content: fs.readFileSync(`api/app/static/${file}`, 'utf8')});
  await page.addScriptTag({content: fs.readFileSync('api/app/static/desk_dialogs.js','utf8')});
  for (const [file, name, call] of cases) {
    await page.evaluate(() => {
      const input = document.querySelector('#draft'), modal = document.querySelector('#underlying'), trigger = document.querySelector('#trigger');
      modal.classList.remove('hidden'); input.value='draft';
      window.global=window; window.calls=[]; window.done=false; window.root=document.querySelector('main');
      const item={id:'id',name:'Название',technical_key:'key',title:'Название',key:'key',is_system:false,display_filename:'old.txt',used:false,next_occurrence_at:'2026-10-30T12:00:00Z'};
      window.state={ticketId:'id',card:{},selectedClientId:'id',selectedClientBlocked:false,activeType:'new_assignment',replaceId:'id',items:[item],editingKind:'recurring',editingDefinitionId:'id',clientCard:{blocks:[item]},expandedBlocks:new Set(['id']),pendingBlockColors:new Map(),modalSnapshot:'initial',clientCardInline:{open:true,values:new Map([[input,'initial']]),revision:0}};
      window.api=window.fetchJson=async (url,options) => { window.calls.push({url,method:options?.method}); return {profile:{},id:'id',is_blocked:true}; };
      window.ctx={state,api,apiPost:async url=>window.api(url,{method:'POST'}),setStatus(){},loadCard:async()=>{},showError(error){throw Error(error);},isDestroyed:()=>false,inlineClient:true,el:{wrongProblemBtn:{},clientModal:modal,editClientBtn:trigger}};
      window.el={timing:input,modal,replaceInput:input,ReplaceInput:document.createElement("input")}; window.API='/files'; window.apiBase='/notification';
      window.selectedItem=()=>item; window.findSourceById=window.findRuleById=window.findFieldById=window.findRecurring=()=>item;
      window.isCustomBlock=window.isFinalProblemButton=window.isDirty=()=>true;
      window.inlineEditorState=()=>state.clientCardInline; window.inlineEditorControls=()=>[input]; window.hasInlineChanges=()=>input.value!=='initial';
      window.isModalDirty=()=>input.value!=='initial'; window.formatLaunchDateTime=()=> '30.10.2026'; window.els=()=>window.el;
      window.clientCardCtx=()=>ctx; window.TicketClientCard={clearWebLogin(){},fillModal(){},trackModalChanges(){},clearBitrixPasswordCardReveal(){}};
      window.uiText={deleteRuleConfirmPrefix:'Удалить правило ',deleteRuleConfirmSuffix:'?',deleteBlockConfirmPrefix:'Удалить блок ',deleteBlockConfirmSuffix:'?',deleteFieldConfirmPrefix:'Удалить поле ',deleteFieldConfirmSuffix:'?'};
      for(const key of ['setStatus','render','setClientCardAndRender','closeRuleModal','closeSourceModal','renderClientCardSources','renderClientCardRules','closeEditFieldModal','setValidation','syncClientBlockControls','syncClientModalToggles','applyPayload','updateActions','updateScheduleInfo','clearBitrixPasswordField','openModal','setFormError']) window[key]=()=>{};
      for(const key of ['loadData','loadUsers','loadCard','loadFiles']) window[key]=async()=>{};
      window.closeWrongProblemModal=()=>modal.classList.add('hidden'); window.showError=message=>{throw Error(message);};
    });
    const dependencies = file === 'management_tasks' ? extract(file,'confirmAction') + '\n' + extract(file,'shouldCloseModal')
      : file === 'ticket_client_card' ? extract(file,'confirmInlineExit') : '';
    await page.addScriptTag({content: dependencies+'\n'+extract(file,name)+`\nwindow.runCase=()=>${call};`});
    for(const approved of [false,true]) {
      await page.evaluate(()=>{window.done=false;window.result=undefined;window.caseError=null;window.state.replaceId="id";Promise.resolve(window.runCase()).then(value=>{window.done=true;window.result=value;},error=>{window.caseError=error.message;window.done=true;});});
      const dialog=page.locator('dialog.desk-dialog[open]'); await page.waitForFunction(()=>document.querySelector('dialog[open]') || window.done);
      assert.equal(await page.evaluate(()=>window.caseError),null,`${file}/${name} opening`);
      assert.equal(await dialog.count(),1,`${file}/${name} must confirm`);
      assert.equal(await page.locator('#draft').inputValue(),'draft',`${file}/${name} before response`);
      assert.deepEqual(await page.evaluate(()=>window.calls),[],`${file}/${name} must wait`);
      if(approved) await dialog.locator('[data-dialog-accept]').click(); else await page.keyboard.press('Escape');
      await page.waitForFunction(()=>window.done);
      assert.equal(await page.evaluate(()=>window.caseError),null,`${file}/${name}`);
      if(!approved) { assert.deepEqual(await page.evaluate(()=>window.calls),[],`${file}/${name} cancel`); assert.equal(await page.locator('#draft').inputValue(),'draft'); }
      else if(['closeModal','closeClientModal','canNavigateAway'].includes(name)) assert.equal(await page.evaluate(()=>window.result),true);
      else assert.equal(await page.evaluate(()=>window.calls.length),1,`${file}/${name} approved`);
    }
  }
  assert.deepEqual(errors,[]);
});

test('project frontend contains no native alert/confirm/prompt calls or global dialog replacements', () => {
  const path = require('node:path');
  for (const name of fs.readdirSync('api/app', {recursive:true}).filter(name=>/\.(js|html)$/.test(name))) {
    const source=fs.readFileSync(path.join('api/app',name),'utf8');
    assert.doesNotMatch(source, /(?<![\w.])(?:(?:window|global|globalThis)\s*\.\s*)?(?:alert|confirm|prompt)\s*\(/, name);
    assert.doesNotMatch(source, /(?:window|global|globalThis)\.(?:alert|confirm|prompt)\s*=/, name);
  }
});
