const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function loadEditor() {
  const source = fs.readFileSync(path.join(__dirname, "..", "api", "app", "static", "telegram_text_editor.js"), "utf8");
  const context = {
    window: { location: { origin: "https://desk.example" } },
    URL,
    String,
    Set,
    RegExp,
    document: {},
  };
  context.global = context.window;
  vm.createContext(context);
  vm.runInContext(source, context);
  return context.window.TelegramTextEditor;
}

const cases = JSON.parse(fs.readFileSync(path.join(__dirname, "telegram_html_validation_cases.json"), "utf8"));
const stylesSource = fs.readFileSync(path.join(__dirname, "..", "api", "app", "static", "styles.css"), "utf8");
const appShellSource = fs.readFileSync(path.join(__dirname, "..", "api", "app", "static", "app_shell.js"), "utf8");

function loadAppShellHarness({ guard } = {}) {
  const fetchCalls = [];
  const historyCalls = [];
  const root = {
    id: "appContent",
    dataset: { shellPage: "tickets-list" },
    innerHTML: "<div>tickets</div>",
  };
  const listeners = {};
  function setLocation(urlLike) {
    const url = new URL(urlLike, window.location.origin);
    window.location.href = url.href;
    window.location.pathname = url.pathname;
    window.location.search = url.search;
    window.location.hash = url.hash;
  }
  const document = {
    readyState: "complete",
    scripts: [],
    title: "Tickets",
    body: { appendChild(node) { node.onload?.(); } },
    getElementById(id) { return id === "appContent" ? root : null; },
    querySelectorAll() { return []; },
    createElement() { return {}; },
    addEventListener(type, handler) { listeners[type] = handler; },
  };
  const window = {
    location: { href: "https://desk.example/tickets", origin: "https://desk.example", pathname: "/tickets", search: "", hash: "" },
    history: {
      pushState(...args) {
        historyCalls.push(["pushState", ...args]);
        setLocation(args[2]);
      },
      replaceState(...args) {
        historyCalls.push(["replaceState", ...args]);
        setLocation(args[2]);
      },
    },
    addEventListener(type, handler) { listeners[type] = handler; },
  };
  function DOMParserMock() {}
  DOMParserMock.prototype.parseFromString = function parseFromString(html) {
    const page = /data-shell-page="([^"]+)"/.exec(html)?.[1] || "";
    const body = /<main[^>]*>([\s\S]*)<\/main>/.exec(html)?.[1] || "";
    return {
      title: "Settings",
      getElementById(id) {
        return id === "appContent" ? { dataset: { shellPage: page }, innerHTML: body } : null;
      },
    };
  };
  const context = vm.createContext({
    window,
    document,
    DOMParser: DOMParserMock,
    URL,
    Map,
    Set,
    String,
    Boolean,
    Error,
    fetch: async (url) => {
      fetchCalls.push(String(url));
      return {
        ok: true,
        redirected: false,
        text: async () => '<main id="appContent" data-shell-page="settings-home"><div>settings</div></main>',
      };
    },
  });
  vm.runInContext(appShellSource, context);
  let destroyed = false;
  window.HelpdeskAppShell.registerPage("tickets-list", () => ({
    canNavigateAway: typeof guard === "function" ? guard : null,
    destroy() { destroyed = true; },
  }));
  window.HelpdeskAppShell.registerPage("settings-home", () => ({ destroy() {} }));
  return { window, root, fetchCalls, historyCalls, listeners, setLocation, destroyed: () => destroyed };
}

test("frontend telegram html validation follows shared contract", () => {
  const editor = loadEditor();
  for (const item of cases) {
    const error = editor.validateTelegramHtml(item.text);
    if (item.ok) {
      assert.equal(error, "", item.name);
    } else {
      assert.match(error, new RegExp(item.message_contains.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")), item.name);
    }
  }
});

test("notification bot page keeps draft state before tab switching", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "api", "app", "static", "settings_notification_bot_basic.js"), "utf8");

  assert.match(source, /function syncActiveDraftFromDom\(\)/);
  assert.match(source, /draft\.enabled = !!enabled\?\.checked/);
  assert.match(source, /draft\.disableLinkPreview = !!pane\?\.querySelector\("\[data-telegram-disable-link-preview\]"\)\?\.checked/);
  assert.match(source, /draft\.sendSilently = !!pane\?\.querySelector\("\[data-telegram-send-silently\]"\)\?\.checked/);
  assert.match(source, /button\.addEventListener\("click", \(\) => \{\s+syncActiveDraftFromDom\(\);\s+state\.activeType/s);
});

test("app shell canNavigateAway false blocks navigation before fetch and mount", async () => {
  const shell = loadAppShellHarness({ guard: () => false });

  const result = await shell.window.HelpdeskAppShell.navigate("/settings");

  assert.equal(result, false);
  assert.deepEqual(shell.fetchCalls, []);
  assert.deepEqual(shell.historyCalls, []);
  assert.equal(shell.destroyed(), false);
  assert.equal(shell.root.dataset.shellPage, "tickets-list");
  assert.equal(shell.root.innerHTML, "<div>tickets</div>");
});

test("app shell canNavigateAway true and missing guard allow navigation", async () => {
  const allowed = loadAppShellHarness({ guard: () => true });
  assert.equal(await allowed.window.HelpdeskAppShell.navigate("/settings"), true);
  assert.deepEqual(allowed.fetchCalls, ["/settings"]);
  assert.equal(allowed.destroyed(), true);
  assert.equal(allowed.root.dataset.shellPage, "settings-home");
  assert.deepEqual(allowed.historyCalls.map((call) => call[0]), ["pushState"]);

  const clean = loadAppShellHarness();
  assert.equal(await clean.window.HelpdeskAppShell.navigate("/settings"), true);
  assert.deepEqual(clean.fetchCalls, ["/settings"]);
  assert.equal(clean.root.dataset.shellPage, "settings-home");
});

test("app shell confirmNavigateAway has no stateful bypass and skipGuard is explicit", async () => {
  let guardCalls = 0;
  const stateful = loadAppShellHarness({
    guard: () => {
      guardCalls += 1;
      return guardCalls === 1;
    },
  });

  assert.equal(stateful.window.HelpdeskAppShell.confirmNavigateAway(), true);
  assert.equal(await stateful.window.HelpdeskAppShell.navigate("/settings"), false);
  assert.equal(guardCalls, 2);
  assert.deepEqual(stateful.fetchCalls, []);
  assert.equal(stateful.root.dataset.shellPage, "tickets-list");

  let skipGuardCalls = 0;
  const explicit = loadAppShellHarness({
    guard: () => {
      skipGuardCalls += 1;
      return true;
    },
  });

  assert.equal(explicit.window.HelpdeskAppShell.confirmNavigateAway(), true);
  assert.equal(await explicit.window.HelpdeskAppShell.navigate("/settings", { skipGuard: true }), true);
  assert.equal(skipGuardCalls, 1);
  assert.deepEqual(explicit.fetchCalls, ["/settings"]);
  assert.equal(explicit.root.dataset.shellPage, "settings-home");
});

test("app shell popstate guard refusal restores current URL without navigation", async () => {
  let guardCalls = 0;
  const shell = loadAppShellHarness({
    guard: () => {
      guardCalls += 1;
      return false;
    },
  });

  shell.setLocation("/settings");
  await shell.listeners.popstate();

  assert.equal(guardCalls, 1);
  assert.deepEqual(shell.fetchCalls, []);
  assert.equal(shell.root.dataset.shellPage, "tickets-list");
  assert.equal(shell.root.innerHTML, "<div>tickets</div>");
  assert.equal(`${shell.window.location.pathname}${shell.window.location.search}`, "/tickets");
  assert.deepEqual(shell.historyCalls.map((call) => [call[0], call[3]]), [["pushState", "/tickets"]]);

  shell.setLocation("/settings");
  await shell.listeners.popstate();

  assert.equal(guardCalls, 2);
  assert.deepEqual(shell.fetchCalls, []);
  assert.equal(shell.root.dataset.shellPage, "tickets-list");
  assert.equal(`${shell.window.location.pathname}${shell.window.location.search}`, "/tickets");
  assert.deepEqual(shell.historyCalls.map((call) => [call[0], call[3]]), [["pushState", "/tickets"], ["pushState", "/tickets"]]);
});

test("app shell popstate guard approval navigates with one confirmation", async () => {
  let guardCalls = 0;
  const shell = loadAppShellHarness({
    guard: () => {
      guardCalls += 1;
      return true;
    },
  });

  shell.setLocation("/settings");
  await shell.listeners.popstate();

  assert.equal(guardCalls, 1);
  assert.deepEqual(shell.fetchCalls, ["/settings"]);
  assert.equal(shell.destroyed(), true);
  assert.equal(shell.root.dataset.shellPage, "settings-home");
  assert.equal(`${shell.window.location.pathname}${shell.window.location.search}`, "/settings");
  assert.deepEqual(shell.historyCalls.map((call) => [call[0], call[3]]), [["replaceState", "/settings"]]);
});

test("notification bot basic dirty guard tracks baseline save and page label", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "api", "app", "static", "settings_notification_bot_basic.js"), "utf8");
  const settingsTemplate = fs.readFileSync(path.join(__dirname, "..", "api", "app", "templates", "settings.html"), "utf8");
  const pageTemplate = fs.readFileSync(path.join(__dirname, "..", "api", "app", "templates", "settings_notification_bot_basic.html"), "utf8");

  assert.match(source, /baselines: new Map\(\)/);
  assert.match(source, /function snapshotDraft\(draft\)/);
  assert.match(source, /function isDirty\(\)/);
  assert.match(source, /state\.baselines\.set\(item\.type, snapshotDraft\(draft\)\)/);
  assert.match(source, /state\.baselines\.set\(normalized\.type, snapshotDraft\(normalized\)\)/);
  assert.match(source, /canNavigateAway/);
  assert.match(source, /window\.confirm\("Есть несохранённые изменения\. Уйти без сохранения\?"\)/);
  assert.match(source, /window\.addEventListener\("beforeunload", handleBeforeUnload\)/);
  assert.match(source, /window\.removeEventListener\("beforeunload", handleBeforeUnload\)/);
  assert.match(settingsTemplate, /Базовые сообщения бота уведомлений/);
  assert.match(pageTemplate, /Базовые сообщения бота уведомлений/);
  assert.doesNotMatch(settingsTemplate, /Базовые уведомления в боте/);
  assert.doesNotMatch(pageTemplate, /Базовые уведомления в боте/);
});

test("notification bot basic SPA route loads current script and saves active fifth type", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "api", "app", "static", "settings_notification_bot_basic.js"), "utf8");
  const template = fs.readFileSync(path.join(__dirname, "..", "api", "app", "templates", "settings_notification_bot_basic.html"), "utf8");
  const templateScript = template.match(/settings_notification_bot_basic\.js\?v=([0-9a-z]+)/)?.[1];
  const shellScript = appShellSource.match(/settings_notification_bot_basic\.js\?v=([0-9a-z]+)/)?.[1];

  assert.equal(shellScript, templateScript);
  assert.match(source, /const draft = normalizeDraft\(item\);[\s\S]+state\.drafts\.set\(item\.type, draft\);[\s\S]+state\.baselines\.set\(item\.type, snapshotDraft\(draft\)\);/);
  assert.match(source, /state\.activeType = button\.getAttribute\("data-tab-type"\) \|\| state\.activeType/);
  assert.match(source, /api\(`\$\{apiBase\}\/\$\{encodeURIComponent\(draft\.type\)\}`/);
  assert.match(source, /body: JSON\.stringify\(\{[\s\S]+template: draft\.template/s);
});

test("admin notification bot editor uses shared atomic tag helper with backend tags", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "api", "app", "static", "settings_notification_bot_basic.js"), "utf8");

  assert.match(source, /tg\.bindAtomicTokens\(textarea, draft\?\.tags \|\| \[\]\)/);
  assert.match(source, /tg\.bindToolbar\(pane, textarea, \{ tags: draft\?\.tags \|\| \[\] \}\)/);
  assert.match(source, /tags: tagItems\.map\(\(tag\) => tag\.label\)/);
  assert.match(source, /draft\.tagItems\.map/);
  assert.doesNotMatch(source, /\[Номер заявки\]/);
  assert.doesNotMatch(source, /\[Тело сообщения\]/);
  assert.doesNotMatch(source, /\[TG @username адресата\]/);
});

test("admin notification bot renders tooltip metadata on the same tag buttons", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "api", "app", "static", "settings_notification_bot_basic.js"), "utf8");

  assert.match(source, /item\.tag_items/);
  assert.match(source, /data-tooltip="\$\{escapeHtml\(tag\.description\)\}"/);
  assert.doesNotMatch(source, /title="\$\{escapeHtml\(tag\.description\)\}"/);
  assert.match(source, /aria-label="\$\{escapeHtml\(tag\.description \? `\$\{tag\.label\}: \$\{tag\.description\}` : tag\.label\)\}"/);
  assert.match(source, /data-insert-tag="\$\{escapeHtml\(tag\.label\)\}"/);
});

test("notification tag tooltip elevates hovered and focused button above sibling tags", () => {
  assert.match(
    stylesSource,
    /\.notification-tag-btn:hover,\s*\.notification-tag-btn:focus-visible\s*\{[\s\S]*z-index:\s*21;/,
  );
  assert.match(
    stylesSource,
    /\.notification-tag-btn\[data-tooltip\]::after\s*\{[\s\S]*left:\s*auto;[\s\S]*right:\s*0;[\s\S]*z-index:\s*20;[\s\S]*width:\s*max-content;[\s\S]*max-width:\s*min\(320px,\s*calc\(100vw - 64px\)\);/s,
  );
  assert.match(
    stylesSource,
    /\.notification-tag-btn\[data-tooltip\]::before\s*\{[\s\S]*left:\s*auto;[\s\S]*right:\s*18px;/s,
  );
  assert.doesNotMatch(
    stylesSource,
    /\.notification-tag-btn\[data-tooltip\]::after\s*\{[\s\S]*left:\s*0;/s,
  );
});

test("legacy bot button editor reuses toolbar without delivery flag controls", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "api", "app", "static", "bot_buttons_editor_view.js"), "utf8");

  assert.match(source, /TelegramTextEditor\?\.toolbarHtml/);
  assert.match(source, /TelegramTextEditor\?\.renderTelegramPreviewHtml/);
  assert.doesNotMatch(source, /settingsHtml/);
  assert.doesNotMatch(source, /data-telegram-disable-link-preview/);
  assert.doesNotMatch(source, /data-telegram-send-silently/);
});

test("notification editor checkboxes do not reuse sidebar pressed header class", () => {
  const editorSource = fs.readFileSync(path.join(__dirname, "..", "api", "app", "static", "telegram_text_editor.js"), "utf8");
  const basicSource = fs.readFileSync(path.join(__dirname, "..", "api", "app", "static", "settings_notification_bot_basic.js"), "utf8");

  assert.doesNotMatch(editorSource, /admin-agent-header/);
  assert.match(editorSource, /class="checkbox-label"[\s\S]+data-telegram-disable-link-preview/);
  assert.match(editorSource, /class="checkbox-label"[\s\S]+data-telegram-send-silently/);
  assert.doesNotMatch(basicSource, /admin-agent-header/);
  assert.match(basicSource, /class="checkbox-label notification-enabled-row"[\s\S]+data-enabled/);
});
