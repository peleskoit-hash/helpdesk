const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });

// Real templates/styles; omit page scripts to compare the same sidebar role.
for (const theme of ['light', 'dark']) for (const [width, height] of [[1920, 1080], [1366, 768], [640, 740], [390, 844]]) {
  test(`V2 outer bubbles match the list: ${theme}, ${width}x${height}`, async t => {
    const page = await browser.newPage({ viewport: { width, height } });
    t.after(() => page.close());
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith('/static/')) {
        const file = path.resolve('api/app', url.pathname.slice(1));
        return route.fulfill({ body: fs.readFileSync(file), contentType: path.extname(file) === '.css' ? 'text/css' : 'application/octet-stream' });
      }
      const html = fs.readFileSync(`api/app/templates/${url.pathname.slice(1)}.html`, 'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '');
      return route.fulfill({ body: html, contentType: 'text/html' });
    });
    let reference;
    for (const template of ['tickets', 'profile', 'profile_notification_bot', 'settings_notification_bot_basic', 'reviews', 'management_users', 'statuses', 'management_integrations', 'management_feedback', 'management_statistics', 'management_saved_files', 'quick_replies', 'settings_users', 'settings_automatic_messages', 'mass_notifications', 'management_tasks']) {
      if (width === 390 && ['profile', 'profile_notification_bot', 'settings_notification_bot_basic', 'reviews', 'management_users'].includes(template)) continue;
      await page.goto(`http://desk.test/${template}`);
      await page.evaluate(theme => { document.body.dataset.theme = theme; document.body.dataset.sidebarReady = '1'; }, theme);
      const geometry = await page.evaluate(() => {
        const main = document.querySelector('#appContent'), panel = main.querySelector('.panel');
        const m = main.getBoundingClientRect(), p = panel.getBoundingClientRect(), toolbar = main.querySelector('.toolbar').getBoundingClientRect();
        return { top: p.top - toolbar.bottom, left: p.left - m.left, right: m.right - p.right, bottom: m.bottom - p.bottom, radius: getComputedStyle(panel).borderRadius };
      });
      if (template === 'tickets') reference = geometry;
      else assert.deepEqual(geometry, reference, template);
      if (template !== 'tickets') {
        // Extra content remains reachable through the page's existing scroll owner.
        assert.equal(await page.evaluate(template => {
          const panel = document.querySelector('#appContent .panel');
          const scroll = template === 'statuses' ? panel.querySelector('#statusBoardPanel')
            : template === 'quick_replies' ? panel.querySelector('.quick-replies-list')
            : template === 'management_tasks' ? panel.querySelector('section:not([hidden])')
            : template === 'management_statistics' ? panel : panel.querySelector('.table-scroll') || panel;
          scroll.hidden = false;
          const filler = document.createElement('div'); filler.style.cssText = 'height:1800px;flex-shrink:0'; scroll.appendChild(filler);
          scroll.scrollTop = scroll.scrollHeight;
          return scroll.scrollTop > 0;
        }, template), true, `${template}: content can scroll`);
      }
    }
  });
}
