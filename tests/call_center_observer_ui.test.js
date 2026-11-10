const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });

async function setup(t, role, workflow = 'ticket', width = 1600, options = {}) {
  const page = await browser.newPage({ viewport: { width, height: 1000 } });
  page.setDefaultTimeout(5000);
  t.after(() => page.close());
  const errors = [], sent = [], uploads = [], quickRequests = [], claimed = new Set();
  page.on('pageerror', e => errors.push(e.message));
  t.after(() => assert.deepEqual(errors, []));
  const me = { id: 'viewer', role, login: 'viewer', full_name: 'Viewer' };
  const messages = [{ id: 'source', message_type: 'client', body: 'Original client text', telegram_message_id: '123', created_at: '2026-01-01T10:00:00' }];
  const card = { id: '123', subject: 'Observer fixture', status: 'new', workflow_type: workflow,
    assignee_id: 'viewer', client_profile: { telegram_username: 'observer_client', internal_number: '123', bitrix_id: '456', custom_fields: { custom_url: 'https://example.test/' } },
    messages, quick_actions_used: {}, intake_state: 'ready', draft_mode: 'reply', draft_text: '', draft_revision: 1 };
  const config = { blocks: [{ key: 'profile', title: 'Client', fields: [
    {key:'telegram_username', label:'TG username', field_type:'text'},
    {key:'internal_number', label:'Internal number', field_type:'text'},
    {key:'bitrix_id', label:'Bitrix ID', field_type:'text'},
    {key:'custom_url', label:'URL', field_type:'url'},
  ] }] };
  await page.addInitScript(({role,pending}) => {
    localStorage.setItem('hd_user_role',role);
    if(pending) sessionStorage.setItem('hd_quick_reply_pending_use:123',JSON.stringify(pending));
  }, {role,pending:options.pending});
  await page.route('http://desk.test/**', async route => {
    const req = route.request(), p = new URL(req.url()).pathname;
    if(p.startsWith('/api/v1/quick-replies/')) quickRequests.push(p);
    if (p.startsWith('/static/')) return route.fulfill({ body: fs.readFileSync(path.resolve('api/app', p.slice(1))),
      contentType: { '.js':'text/javascript', '.css':'text/css', '.svg':'image/svg+xml' }[path.extname(p)] || 'application/octet-stream' });
    if (p === '/tickets/123') return route.fulfill({ contentType:'text/html', body:fs.readFileSync('api/app/templates/ticket.html','utf8') });
    let data = {};
    if (p.endsWith('/auth/session')) data = {real_user:me, effective_user:me, is_real_admin:role === 'admin'};
    else if (p.endsWith('/users/me') || p.endsWith('/users/me/profile')) data = me;
    else if (p.endsWith('/users/me/preferences')) data = {theme_preference:'light',send_by_enter:false};
    else if (p.endsWith('/agents') || p.endsWith('/by-role') || p.endsWith('/task-panel')) data = [];
    else if (p.endsWith('/client-card-view-config')) data = config;
    else if (p === '/api/v1/tickets/123') {
      card.active_attachment_preloads = uploads.filter(f => !claimed.has(f.id)).map(f => ({...f,status:'ready'}));
      data = card;
    }
    else if(p === '/api/v1/tickets/123/attachment-preloads' && req.method() === 'POST') {
      const form = await new Request(req.url(),{method:'POST',headers:{'content-type':req.headers()['content-type']},body:req.postDataBuffer()}).formData();
      const file = form.get('file');
      const upload = {id:form.get('upload_id'),original_filename:file.name,file_size:file.size,mime_type:file.type,status:'ready'};
      uploads.push(upload);
      data = upload;
    }
    else if(p.startsWith('/api/v1/tickets/123/attachment-preloads/')) data = {...uploads.find(f => f.id === p.split('/').pop()),status:'ready'};
    else if(p.startsWith('/api/v1/quick-replies/')) data = [];
    else if (p.endsWith('/draft')) data = {...req.postDataJSON(), revision:2};
    else if (req.method() === 'POST' && /\/(comment|reply)$/.test(p)) {
      let body;
      if(req.headers()['content-type'].includes('multipart/form-data')) {
        const form = await new Request(req.url(),{method:'POST',headers:{'content-type':req.headers()['content-type']},body:req.postDataBuffer()}).formData();
        body = {text:form.get('text'),preloaded_attachment_ids:form.getAll('preloaded_attachment_id'),rawFiles:form.getAll('files').length};
      } else body = req.postDataJSON();
      sent.push({path:p,body});
      const attachments = (body.preloaded_attachment_ids || []).map(id => {
        claimed.add(id);
        return {...uploads.find(f => f.id === id),source:'helpdesk',telegram_delivery_status:null};
      });
      messages.push({id:'sent',message_type:p.endsWith('/comment')?'internal':'agent',body:body.text,attachments,created_at:'2026-01-01T10:01:00'});
      data = {ok:true};
    }
    return route.fulfill({contentType:'application/json', body:JSON.stringify(data)});
  });
  await page.goto('http://desk.test/tickets/123');
  try {
    await page.waitForFunction(() => document.querySelector('#ticketSubject')?.textContent === 'Observer fixture', null, {timeout:5000});
    await page.locator('#clientDynamicCard .client-card-field').first().waitFor();
  } catch(e) {
    console.error('Fixture DOM', await page.evaluate(() => ({title:document.querySelector('#ticketSubject')?.textContent, error:document.querySelector('#errorText')?.textContent, client:document.querySelector('#clientDynamicCard')?.innerHTML})), errors);
    throw e;
  }
  return {page,sent,uploads,quickRequests};
}

for (const workflow of ['ticket','task']) test(`observer comments and quotes without client reply: ${workflow}`, async t => {
  const {page,sent} = await setup(t,'chiki_piki',workflow);
  assert.equal(await page.locator('.ticket-side:not(.ticket-client-side)').isVisible(),false);
  assert.equal(await page.locator('#modeReplyBtn').isDisabled(),true);
  assert.equal(await page.locator('#modeCommentBtn').isDisabled(),false);
  assert.equal(await page.locator('#modeCommentBtn').evaluate(n => n.classList.contains('primary')),true);
  assert.equal(await page.locator('#ticketMessageBody').isEnabled(),true);
  assert.equal(await page.locator('#addFilesBtn').isDisabled(),false);
  assert.equal(await page.locator('#clientDynamicCard a').count(),0);
  const values = await page.locator('#clientDynamicCard .client-card-field-value').allTextContents();
  assert.deepEqual(values,['@observer_client','123','456','https://example.test/']);
  await page.locator('[data-sidebar-user-menu-toggle]').click();
  assert.equal(await page.locator('[data-sidebar-user-menu-item="bot-notifications"]').isVisible(),false);
  for(const key of ['profile','logout']) assert.equal(await page.locator(`[data-sidebar-user-menu-item="${key}"]`).isVisible(),true);
  await page.keyboard.press('Escape');
  await page.evaluate(() => Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text => {window.copiedText=text;}}}));
  await page.locator('[data-message-id="source"]').first().click({button:'right'});
  await page.locator('.message-context-menu').getByText('Копировать',{exact:true}).click();
  assert.equal(await page.evaluate(() => window.copiedText),'Original client text');
  await page.locator('[data-message-id="source"]').first().click({button:'right'});
  assert.equal(await page.locator('.message-context-menu').getByText('Копировать',{exact:true}).count(),1);
  await page.locator('.message-context-menu').getByText('Цитировать',{exact:true}).click();
  assert.match(await page.locator('#quotePreviewText').textContent(),/Original client text/);
  await page.locator('#modeReplyBtn').evaluate(n => {n.disabled=false;n.click();});
  assert.equal(await page.locator('#modeCommentBtn').evaluate(n => n.classList.contains('primary')),true);
  await page.locator('#ticketMessageBody').fill('Observation');
  await page.locator('#sendNowBtn').click();
  await page.waitForFunction(() => document.querySelector('[data-message-id="sent"]'));
  assert.deepEqual(sent,[{path:'/api/v1/tickets/123/comment',body:{text:'Observation',quote_message_id:'source'}}]);
});

for (const width of [1600,1366,1100]) test(`observer chat fills available space without changing client column: ${width}`, async t => {
  const normal = await setup(t,'agent','ticket',width);
  const observer = await setup(t,'chiki_piki','ticket',width);
  async function geometry(page) {
    return page.evaluate(() => {
      const layout=document.querySelector('.ticket-layout'), chat=document.querySelector('.ticket-card'), client=document.querySelector('#clientDynamicCard').closest('aside');
      const l=layout.getBoundingClientRect(), c=chat.getBoundingClientRect(), p=client.getBoundingClientRect(), css=getComputedStyle(layout);
      return {clientWidth:p.width, clientX:p.x, chatRight:c.right, layoutRight:l.right, paddingRight:parseFloat(css.paddingRight), gap:css.gap};
    });
  }
  const a=await geometry(normal.page), b=await geometry(observer.page);
  assert.equal(b.clientWidth,a.clientWidth);
  assert.equal(b.clientX,a.clientX);
  assert.equal(b.paddingRight,a.paddingRight);
  assert.equal(b.gap,a.gap);
  assert.ok(Math.abs(b.chatRight-(b.layoutRight-b.paddingRight))<2,JSON.stringify(b));
  if(width>1200) assert.ok(b.chatRight>a.chatRight+200);
});

for (const role of ['admin','agent','trainee']) test(`other roles retain controls, links and reply mode: ${role}`, async t => {
  const {page} = await setup(t,role);
  assert.equal(await page.locator('.ticket-side:not(.ticket-client-side)').isVisible(),true);
  assert.equal(await page.locator('#modeReplyBtn').isEnabled(),true);
  assert.equal(await page.locator('#modeReplyBtn').evaluate(n => n.classList.contains('primary')),true);
  assert.equal(await page.locator('#clientDynamicCard .client-card-field-external-link').count(),3);
});

test('profile and settings display the new name while preserving the role key', () => {
  for(const file of ['profile.js','settings_users.js']) {
    const source=fs.readFileSync('api/app/static/'+file,'utf8');
    assert.match(source,/chiki_piki: "Call Center Observer"/);
  }
  assert.match(fs.readFileSync('api/app/templates/settings_users.html','utf8'), /value="chiki_piki">Call Center Observer</);
});


test('personal data renders Call Center Observer', async t => {
  const {page} = await setup(t,'chiki_piki');
  await page.route('http://desk.test/profile', route => route.fulfill({contentType:'text/html',body:fs.readFileSync('api/app/templates/profile.html','utf8')}));
  await page.goto('http://desk.test/profile');
  await page.waitForFunction(() => document.querySelector('#profileRole')?.textContent === 'Call Center Observer',null,{timeout:5000});
});

test('user settings render the observer label and keep the option identifier', async t => {
  const {page} = await setup(t,'admin');
  await page.route('http://desk.test/settings/users', route => route.fulfill({contentType:'text/html',body:fs.readFileSync('api/app/templates/settings_users.html','utf8')}));
  await page.route('http://desk.test/api/v1/settings/users', route => route.fulfill({contentType:'application/json',body:JSON.stringify([{id:'observer',role:'chiki_piki',full_name:'Observer',login:'observer',is_active:true}])}));
  await page.goto('http://desk.test/settings/users');
  await page.getByRole('cell',{name:'Call Center Observer',exact:true}).waitFor();
  assert.equal(await page.locator('option[value="chiki_piki"]').textContent(),'Call Center Observer');
});


for(const workflow of ['ticket','task']) for(const text of ['Observation','']) test(`observer uses comment preload: ${workflow} text=${!!text}`,async t => {
  const {page,sent,uploads} = await setup(t,'chiki_piki',workflow);
  assert.equal(await page.locator('#addFilesBtn').isEnabled(),true);
  const chooser = await Promise.all([page.waitForEvent('filechooser'),page.locator('#addFilesBtn').click()]);
  await chooser[0].setFiles([
    {name:'first.pdf',mimeType:'application/pdf',buffer:Buffer.from('pdf')},
    {name:'second.pdf',mimeType:'application/pdf',buffer:Buffer.from('pdf')},
  ]);
  await page.waitForFunction(() => document.querySelector('#selectedFilesList')?.textContent.match(/Готов к отправке/g)?.length === 2);
  assert.equal(uploads.length,2);
  assert.match(await page.locator('#selectedFilesList').textContent(),/first.pdf/);
  assert.match(await page.locator('#selectedFilesList').textContent(),/second.pdf/);
  await page.locator('#ticketMessageBody').fill(text);
  await page.locator('#sendNowBtn').click();
  await page.locator('[data-message-id="sent"]').first().waitFor();
  assert.equal(sent.length,1);
  assert.equal(sent[0].path,'/api/v1/tickets/123/comment');
  assert.equal(sent[0].body.text,text);
  assert.equal(sent[0].body.rawFiles,0);
  assert.deepEqual(sent[0].body.preloaded_attachment_ids,uploads.map(f => f.id));
  assert.match(await page.locator('#messages').textContent(),/first.pdf/);
  assert.match(await page.locator('#messages').textContent(),/second.pdf/);
});

test('observer cannot open quick replies by button, Ctrl+Q or pending editor use',async t => {
  const pending = {id:'quick',body:'Forbidden quick text',saved_files:[{id:'saved',display_filename:'saved.pdf'}]};
  const {page,quickRequests,uploads} = await setup(t,'chiki_piki','ticket',1600,{pending});
  assert.equal(await page.locator('#quickRepliesBtn').isVisible(),false);
  assert.equal(await page.locator('#quickRepliesPanel').isVisible(),false);
  await page.locator('#ticketMessageBody').focus();
  await page.keyboard.press('Control+q');
  assert.equal(await page.locator('#quickRepliesPanel').isVisible(),false);
  await page.locator('#quickRepliesBtn').evaluate(n => {n.disabled=false;n.classList.remove('hidden');n.click();});
  assert.equal(await page.locator('#quickRepliesPanel').isVisible(),false);
  assert.equal(await page.locator('#ticketMessageBody').inputValue(),'');
  assert.deepEqual(quickRequests,[]);
  assert.deepEqual(uploads,[]);
});

for(const role of ['admin','agent','trainee']) test(`quick replies remain accessible by button and Ctrl+Q: ${role}`,async t => {
  const {page,quickRequests} = await setup(t,role);
  assert.equal(await page.locator('#quickRepliesBtn').isVisible(),true);
  await page.locator('#quickRepliesBtn').click();
  assert.equal(await page.locator('#quickRepliesPanel').isVisible(),true);
  await page.keyboard.press('Escape');
  await page.locator('#ticketMessageBody').focus();
  await page.keyboard.press('Control+q');
  assert.equal(await page.locator('#quickRepliesPanel').isVisible(),true);
  assert.ok(quickRequests.some(url => url.includes('/search')));
});
