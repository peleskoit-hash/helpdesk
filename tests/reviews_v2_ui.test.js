// Browser checks use the real page, sidebar, theme and CSS with isolated API data.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });
async function setup(t, theme, role = 'agent', width = 1440) {
  const page = await browser.newPage({ viewport: { width, height: 1000 }, reducedMotion: 'reduce' });
  t.after(() => page.close());
  const errors = [], queries = [], assets = [];
  page.on('pageerror', error => errors.push(error.message));
  const me = { id: 'agent-1', role, full_name: 'Иван Петров', login: 'demo', availability_status: 'online' };
  const preferences = { theme_preference: theme, appearance_revision: '0' };
  let empty = false;
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    if (url.hostname !== 'desk.test') return route.abort();
    if (p.startsWith('/static/')) {
      const file = path.resolve('api/app', p.slice(1));
      assets.push(url.pathname + url.search);
      return route.fulfill({ status: fs.existsSync(file) ? 200 : 404, body: fs.existsSync(file) ? fs.readFileSync(file) : '',
        contentType: { '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml', '.ttf': 'font/ttf' }[path.extname(file)] || 'application/octet-stream' });
    }
    if (!p.startsWith('/api/')) return route.fulfill({ contentType: 'text/html', body: fs.readFileSync('api/app/templates/reviews.html', 'utf8') });
    let result = {};
    if (p.endsWith('/appearance-session')) result = { writer_id: '00000000-0000-4000-8000-000000000001', preferences };
    else if (p.endsWith('/preferences')) result = preferences;
    else if (p === '/api/v1/auth/session') result = { real_user: me, effective_user: me, is_real_admin: role === 'admin', session_login_ts: 1, app_timezone: 'Europe/Moscow' };
    else if (p === '/api/v1/users/me') result = me;
    else if (p === '/api/v1/users/agents' || p === '/api/v1/users/by-role') result = [me];
    else if (p.endsWith('/counts')) result = { active: 123, unassigned: 0, frozen: 12, closed: 595, special: 0, by_assignee: { 'agent-1': { active: 123, frozen: 12 } } };
    else if (p === '/api/v1/reviews/employees') result = [{ employee_id: me.id, display_name: me.full_name, is_active: true }];
    else if (p === '/api/v1/reviews') {
      queries.push(Object.fromEntries(url.searchParams));
      const item = { review_id: 'review-1', ticket_id: 'ticket-1', rated_at: '2026-10-23T12:00:00Z',
        client_name_snapshot: 'Мария Смирнова', ticket_subject_snapshot: 'Настройка доступа', rating: 5,
        review_text: 'Спасибо за помощь!\n'.repeat(14), version_number: 1, has_history: false };
      result = { items: empty ? [] : [item], total_count: empty ? 0 : 31, average_rating: empty ? null : 5,
        page: Number(url.searchParams.get('page') || 1), pages: empty ? 1 : 2, page_size: 30 };
    }
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(result) });
  });
  await page.goto('http://desk.test/reviews');
  await page.waitForFunction(theme => document.body.dataset.sidebarReady === '1' && document.body.dataset.theme === theme, theme);
  if (role === 'admin') {
    await page.locator('#reviewEmployee:not(:disabled)').waitFor();
    await page.locator('#reviewEmployee').selectOption('agent-1');
  }
  await page.waitForSelector('#reviewTableBody tr');
  await page.evaluate(() => document.fonts.ready);
  await page.addStyleTag({ content: '* { transition: none !important; animation: none !important; }' });
  t.after(() => assert.deepEqual(errors, []));
  return { page, queries, assets, makeEmpty: () => { empty = true; } };
}
async function contrast(page, selectors) {
  const checks = await page.evaluate(selectors => {
    const luminance = color => color.match(/[\d.]+/g).slice(0, 3).map(Number).map(v => {
      v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4;
    }).reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
    return selectors.map(selector => {
      const el = document.querySelector(selector), s = getComputedStyle(el);
      let node = el, bg;
      do { bg = getComputedStyle(node).backgroundColor; node = node.parentElement; } while (bg === 'rgba(0, 0, 0, 0)' && node);
      const a = luminance(s.color), b = luminance(bg);
      return { selector, color: s.color, bg, ratio: (Math.max(a, b) + .05) / (Math.min(a, b) + .05) };
    });
  }, selectors);
  for (const check of checks) assert.ok(check.ratio >= 4.5, JSON.stringify(check));
}
for (const theme of ['light', 'dark']) {
  for (const role of ['agent', 'admin']) {
    test(`reviews V2: ${theme}, ${role}, filters, stats, list and empty state`, async t => {
      const { page, queries, assets, makeEmpty } = await setup(t, theme, role);
      const panel = await page.locator('.reviews-page').evaluate(el => {
        const s = getComputedStyle(el), probe = document.createElement('div');
        probe.style.background = 'var(--desk-surface)'; el.appendChild(probe);
        const expected = getComputedStyle(probe).backgroundColor; probe.remove();
        return { background: s.backgroundColor, expected, shadow: s.boxShadow };
      });
      assert.equal(panel.background, panel.expected);
      await contrast(page, ['.reviews-filters label', '#reviewDateFrom', '#reviewStats', '.reviews-table th', '.reviews-table td', '.reviews-table a', '.reviews-text-action']);
      assert.equal(await page.locator('#reviewCount').textContent(), '31');
      assert.equal(await page.locator('#reviewAverage').textContent(), '5,00');
      const text = page.locator('.reviews-text-action').first();
      await text.click(); assert.equal(await text.getAttribute('aria-expanded'), 'true');
      await text.click(); assert.equal(await text.getAttribute('aria-expanded'), 'false');
      await Promise.all([page.waitForResponse(r => r.url().includes('/api/v1/reviews?') && r.url().includes('page=2')), page.locator('#reviewNext').click()]);
      assert.equal(queries.at(-1).page, '2');
      if (role === 'admin') assert.equal(queries.at(-1).employee_id, 'agent-1');
      assert.ok(assets.includes('/static/reviews.css?v=20261024-reviews-v2'));
      assert.ok(assets.includes('/static/desk-v2.css?v=20261025-status-log-responsive-v2'));
      makeEmpty();
      await page.locator('#reviewDateFrom').fill('2026-10-02');
      await page.waitForFunction(() => document.querySelector('#reviewStatus').textContent.includes('отзывов нет'));
      assert.equal(queries.at(-1).date_from, '2026-10-02');
      assert.equal(await page.locator('#reviewTableWrap').isVisible(), false);
      await contrast(page, ['#reviewStatus', '#reviewStats']);
      if (process.env.REVIEWS_V2_SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.REVIEWS_V2_SCREENSHOT_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.REVIEWS_V2_SCREENSHOT_DIR, `${theme}-${role}.png`), fullPage: true });
      }
    });
  }
  for (const width of [1366, 1920]) {
    test(`admin sidebar intrinsic width on every page: ${theme}, ${width}`, async t => {
      const { page } = await setup(t, theme, 'admin', width);
      const filter = page.locator('#adminAgentFilter');
      const measure = () => page.locator('.sidebar').evaluate(el => ({
        width: el.getBoundingClientRect().width,
        overflow: [el, ...el.querySelectorAll('.admin-agent-filter, .admin-agent-list, .admin-agent-item, .admin-agent-name')]
          .some(node => node.scrollWidth > node.clientWidth + 1),
      }));
      for (const name of ['reviews', 'profile', 'profile-notification-bot', 'management-home', 'settings-home', 'ticket-card', 'tickets-list']) {
        await page.locator('#appContent').evaluate((el, name) => { el.dataset.shellPage = name; }, name);
        await page.locator('.admin-agent-name').first().evaluate(el => { el.textContent = 'Иван Петров'; });
        const short = await measure();
        await page.locator('.admin-agent-name').first().evaluate(el => { el.textContent = 'Александра Константиновна Рождественская'; });
        const long = await measure();
        assert.ok(long.width > short.width, `${name}: ${JSON.stringify(short)} → ${JSON.stringify(long)}`);
        assert.equal(long.overflow, false);
        const aligned = await page.locator('.admin-agent-item').first().evaluate(el => {
          const name = el.querySelector('.admin-agent-name').getBoundingClientRect(), counts = el.querySelector('.admin-agent-counts').getBoundingClientRect();
          return counts.left >= name.right && counts.width >= 72;
        });
        assert.equal(aligned, true);
      }
      await filter.evaluate(el => el.classList.add('hidden'));
      assert.equal((await measure()).width, 246, 'agent width stays unchanged');
    });
  }
}
