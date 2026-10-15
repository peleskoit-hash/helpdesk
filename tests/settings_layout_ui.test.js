const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.join(__dirname, "..");
const templatesDir = path.join(root, "api", "app", "templates");
const stylesSource = fs.readFileSync(path.join(root, "api", "app", "static", "styles.css"), "utf8");
const massTemplate = fs.readFileSync(path.join(root, "api", "app", "templates", "mass_notifications.html"), "utf8");
const massScript = fs.readFileSync(path.join(root, "api", "app", "static", "mass_notifications.js"), "utf8");

function sourceBetween(source, start, end) {
  const startIndex = source.indexOf(start);
  assert.notEqual(startIndex, -1, `missing start marker: ${start}`);
  const endIndex = source.indexOf(end, startIndex + start.length);
  assert.notEqual(endIndex, -1, `missing end marker: ${end}`);
  return source.slice(startIndex, endIndex);
}

function parseElements(source) {
  const elements = [];
  const stack = [];
  const tagPattern = /<\/?([a-zA-Z][\w:-]*)([^>]*)>/g;
  const voidTags = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"]);
  let match;
  while ((match = tagPattern.exec(source))) {
    const [raw, tagName, rawAttrs] = match;
    const tag = tagName.toLowerCase();
    if (raw.startsWith("</")) {
      for (let index = stack.length - 1; index >= 0; index -= 1) {
        const node = stack.pop();
        node.end = match.index + raw.length;
        if (node.tag === tag) break;
      }
      continue;
    }
    const attrs = {};
    for (const attr of rawAttrs.matchAll(/([\w:-]+)(?:="([^"]*)")?/g)) {
      attrs[attr[1]] = attr[2] ?? "";
    }
    const node = { tag, attrs, start: match.index, end: source.length, parent: stack.at(-1) || null };
    elements.push(node);
    if (!raw.endsWith("/>") && !voidTags.has(tag)) stack.push(node);
  }
  return elements;
}

function findElement(source, predicate, label) {
  const node = parseElements(source).find(predicate);
  assert.ok(node, `missing element: ${label}`);
  return node;
}

function hasClass(node, className) {
  return String(node.attrs.class || "").split(/\s+/).includes(className);
}

function containsElement(parent, child) {
  return parent.start < child.start && child.end <= parent.end;
}

test("notification bot basic settings panel has inner spacing", () => {
  assert.match(
    stylesSource,
    /\.notification-bot-basic-panel\s*\{[\s\S]*padding:\s*22px;/,
  );
  assert.match(
    stylesSource,
    /#notificationBotBasicRoot\s*\{[\s\S]*display:\s*grid;[\s\S]*gap:\s*18px;/,
  );
  assert.match(
    stylesSource,
    /\.notification-template-textarea\s*\{[\s\S]*box-sizing:\s*border-box;[\s\S]*padding:\s*10px 12px;/,
  );
});

test("automatic messages panel fills content height while preserving inner scroll", () => {
  const panelScope = sourceBetween(
    stylesSource,
    ".automatic-messages-panel {",
    ".automatic-messages-panel .settings-description {",
  );
  const rootScope = sourceBetween(
    stylesSource,
    ".automatic-messages-root {",
    ".automatic-messages-panel .settings-empty {",
  );
  const sectionScope = sourceBetween(
    stylesSource,
    ".automatic-message-section {",
    ".automatic-message-section-head {",
  );

  assert.match(
    panelScope,
    /display:\s*flex;[\s\S]*flex-direction:\s*column;[\s\S]*height:\s*100%;[\s\S]*overflow:\s*hidden;[\s\S]*padding:\s*0;/,
  );
  assert.match(
    rootScope,
    /display:\s*flex;[\s\S]*flex-direction:\s*column;[\s\S]*flex:\s*1 1 auto;[\s\S]*min-height:\s*0;[\s\S]*overflow:\s*auto;[\s\S]*padding:\s*18px 22px 22px;/,
  );
  assert.doesNotMatch(rootScope, /display:s*grid;/);
  assert.match(sectionScope, /flex:\s*0 0 auto;/);
  assert.doesNotMatch(stylesSource, /\.automatic-messages-panel\s*\{[\s\S]*max-height:\s*calc\(100vh - 132px\);/);
});

test("mass notifications toolbar has back navigation and no visible ready status", () => {
  assert.doesNotMatch(massTemplate, /id="roleControl"/);
  assert.doesNotMatch(massTemplate, /id="agentControl"/);
  assert.doesNotMatch(massTemplate, /id="roleSelect"/);
  assert.doesNotMatch(massTemplate, /id="agentSelect"/);
  assert.ok(massTemplate.includes('<button id="backToManagementBtn" class="action" type="button">Назад</button>'));
  assert.ok(massTemplate.includes('<div class="settings-title">Массовые оповещения</div>'));
  assert.match(massScript, /backToManagementBtn:\s*document\.getElementById\("backToManagementBtn"\)/);
  assert.ok(massScript.includes("window.HelpdeskAppShell.goBack()"));
  assert.ok(massScript.includes("window.history.back()"));
  assert.doesNotMatch(massScript, /window\.HelpdeskAppShell\.navigate\("\/management"\)/);
  assert.doesNotMatch(massScript, /window\.location\.href = "\/management"/);
});

test("menu page back buttons use shared browser-history navigation", () => {
  const pageScripts = [
    "settings_users.js",
    "settings_client_card.js",
    "settings_automatic_messages.js",
    "settings_notification_bot_basic.js",
    "mass_notifications.js",
    "profile.js",
    "profile_notification_bot.js",
  ];

  for (const filename of pageScripts) {
    const source = fs.readFileSync(path.join(root, "api", "app", "static", filename), "utf8");
    assert.match(source, /HelpdeskAppShell\?\.goBack/);
    assert.match(source, /window\.history\.back\(\)/);
    assert.doesNotMatch(source, /HelpdeskAppShell\.navigate\("\/(settings|management|tickets)"\)/);
    assert.doesNotMatch(source, /window\.location\.href = "\/(settings|management|tickets)"/);
  }

  const botButtons = fs.readFileSync(path.join(root, "api", "app", "static", "settings_bot_buttons.js"), "utf8");
  assert.doesNotMatch(botButtons, /HelpdeskAppShell\.navigate\("\/settings"\)/);
});

test("management users page owns vertical scroll without global shell hacks", () => {
  const mainScope = sourceBetween(
    stylesSource,
    '.main[data-shell-page="management-users"] {',
    ".management-users-page {",
  );
  const pageScope = sourceBetween(
    stylesSource,
    ".management-users-page {",
    ".management-users-controls {",
  );
  const managementUsersScroll = sourceBetween(stylesSource, ".table-scroll {", "table {");
  const bodyScope = sourceBetween(stylesSource, "body {", 'body:not([data-sidebar-ready="1"])');
  const sidebarScope = sourceBetween(stylesSource, ".sidebar {", ".sidebar-brand {");

  assert.match(mainScope, /grid-template-rows:\s*auto minmax\(0, 1fr\);[\s\S]*min-height:\s*0;[\s\S]*overflow:\s*hidden;/);
  assert.match(
    pageScope,
    /height:\s*100%;[\s\S]*min-height:\s*0;[\s\S]*overflow-y:\s*auto;[\s\S]*overflow-x:\s*hidden;/,
  );
  assert.match(managementUsersScroll, /overflow:\s*auto;[\s\S]*flex:\s*1;[\s\S]*min-height:\s*0;/);
  assert.doesNotMatch(sidebarScope, /position:\s*(fixed|sticky);/);
  assert.doesNotMatch(bodyScope, /overflow:\s*hidden;/);
});

test("management home uses compact two-column cards while settings stays one-column", () => {
  const baseLinksScope = sourceBetween(
    stylesSource,
    ".settings-home-links {",
    ".management-home-panel .settings-home-links {",
  );
  const managementLinksScope = sourceBetween(
    stylesSource,
    ".management-home-panel .settings-home-links {",
    ".settings-home-link {",
  );
  const linkScope = sourceBetween(
    stylesSource,
    ".settings-home-link {",
    ".settings-home-link:hover {",
  );

  assert.match(baseLinksScope, /grid-template-columns:\s*1fr;[\s\S]*max-width:\s*546px;/);
  assert.match(managementLinksScope, /grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\);[\s\S]*max-width:\s*1120px;/);
  assert.match(linkScope, /font-size:\s*30px;[\s\S]*padding:\s*18px 28px;[\s\S]*white-space:\s*normal;[\s\S]*overflow-wrap:\s*anywhere;/);
  assert.match(stylesSource, /@media \(max-width:\s*1180px\)\s*\{[\s\S]*\.management-home-panel \.settings-home-links\s*\{[\s\S]*grid-template-columns:\s*1fr;/);
});

test("display settings container wraps theme toggle and keeps ticket color control scoped", () => {
  const templates = fs.readdirSync(templatesDir)
    .filter((name) => name.endsWith(".html"))
    .map((name) => [name, fs.readFileSync(path.join(templatesDir, name), "utf8")]);
  const themeTemplates = templates.filter(([, source]) => source.includes('id="themeToggleBtn"'));
  assert.ok(themeTemplates.length > 0);

  for (const [name, source] of themeTemplates) {
    const scope = sourceBetween(source, '<div class="display-settings">', "</div>");
    assert.match(scope, /id="themeToggleBtn"/, name);
  }

  const tickets = fs.readFileSync(path.join(templatesDir, "tickets.html"), "utf8");
  const ticketsScope = sourceBetween(tickets, '<div class="display-settings">', "</div>");
  assert.ok(ticketsScope.indexOf('id="colorToggleLabel"') < ticketsScope.indexOf('id="themeToggleBtn"'));
  assert.match(ticketsScope, /id="colorEnabledInput" type="checkbox"/);
  assert.match(ticketsScope, /id="colorToggleText"[^>]*>Цветные плашки<\/span>/);
  assert.match(ticketsScope, /id="colorLegend" class="color-legend" role="tooltip" aria-hidden="true"/);

  for (const [name, source] of templates) {
    if (name === "tickets.html") continue;
    assert.doesNotMatch(source, /id="colorEnabledInput"/, name);
  }

  const displaySettingsScope = sourceBetween(stylesSource, ".display-settings {", ".search-input-wrap {");
  assert.match(displaySettingsScope, /margin-left:\s*auto;[\s\S]*display:\s*inline-flex;[\s\S]*overflow:\s*visible;/);
  assert.match(stylesSource, /\.display-settings \.theme-toggle\s*\{[\s\S]*margin-left:\s*0;/);
  assert.match(stylesSource, /body\[data-theme="dark"\] \.display-settings\s*\{/);
});

test("menu templates use the unified top toolbar contract", () => {
  const pages = new Map([
    ["settings.html", "Настройки"],
    ["management.html", "&#1059;&#1087;&#1088;&#1072;&#1074;&#1083;&#1077;&#1085;&#1080;&#1077;"],
    ["quick_replies.html", "Быстрые ответы"],
    ["reviews.html", "Отзывы"],
    ["statuses.html", "Статусы"],
    ["management_users.html", "Пользователи"],
    ["management_integrations.html", "Интеграции"],
    ["management_feedback.html", "Предложения и замечания"],
    ["management_statistics.html", "Статистика"],
    ["management_saved_files.html", "Сохранённые файлы"],
    ["management_tasks.html", "Задачи"],
  ]);

  for (const [name, title] of pages) {
    const source = fs.readFileSync(path.join(templatesDir, name), "utf8");
    assert.equal((source.match(/data-shell-back/g) || []).length, 1, name);
    assert.equal((source.match(/id="themeToggleBtn"/g) || []).length, 1, name);
    assert.match(source, /<div class="toolbar">/, name);
    assert.ok(source.includes(`class="settings-title">${title}`) || source.includes(`class="settings-title">${title}</h1>`), name);
    assert.ok(source.indexOf("data-shell-back") < source.indexOf("settings-title"), name);
    assert.ok(source.indexOf("settings-title") < source.indexOf("display-settings"), name);
    assert.ok(source.includes('/static/theme.js?v='), name);
    const ids = [...source.matchAll(/\sid="([^"]+)"/g)].map((match) => match[1]);
    assert.equal(new Set(ids).size, ids.length, name);
  }

  const statuses = fs.readFileSync(path.join(templatesDir, "statuses.html"), "utf8");
  assert.doesNotMatch(statuses, /id="roleControl"/);
  assert.doesNotMatch(statuses, /id="roleSelect"/);
  assert.doesNotMatch(statuses, /id="agentControl"/);
  assert.doesNotMatch(statuses, /id="agentSelect"/);

  const quickReplies = fs.readFileSync(path.join(templatesDir, "quick_replies.html"), "utf8");
  assert.match(quickReplies, /id="createQuickReplyBtn"/);

  for (const name of ["management_tasks.html", "management_users.html", "management_integrations.html", "management_feedback.html", "management_statistics.html", "management_saved_files.html"]) {
    const source = fs.readFileSync(path.join(templatesDir, name), "utf8");
    assert.doesNotMatch(source, /class="management-eyebrow"/, name);
  }
});

test("local page actions live inside their work containers after toolbar unification", () => {
  const settingsUsers = fs.readFileSync(path.join(templatesDir, "settings_users.html"), "utf8");
  const settingsUsersPanel = findElement(settingsUsers, (node) => hasClass(node, "settings-panel"), "settings users panel");
  const createUserBtn = findElement(settingsUsers, (node) => node.attrs.id === "createUserBtn", "create user button");
  const settingsTable = findElement(settingsUsers, (node) => hasClass(node, "settings-table"), "settings users table");
  assert.ok(containsElement(settingsUsersPanel, createUserBtn));
  assert.ok(containsElement(settingsUsersPanel, settingsTable));
  assert.ok(createUserBtn.start < settingsTable.start);

  const clientCard = fs.readFileSync(path.join(templatesDir, "settings_client_card.html"), "utf8");
  const clientCardPanel = findElement(clientCard, (node) => hasClass(node, "client-card-work-panel"), "client card work panel");
  const mainRow = findElement(clientCard, (node) => hasClass(node, "client-card-main-row"), "client card main row");
  const mainTabs = findElement(clientCard, (node) => hasClass(node, "client-card-page-tabs") && !hasClass(node, "client-card-subtabs"), "client card main tabs");
  const blocksPanel = findElement(clientCard, (node) => node.attrs.id === "clientCardBlocksPanel", "client card blocks panel");
  const automationPanel = findElement(clientCard, (node) => node.attrs.id === "clientCardAutomationPanel", "client card automation panel");
  const addBlockBtn = findElement(clientCard, (node) => node.attrs.id === "addClientCardBlockBtn", "add client card block button");
  assert.ok(containsElement(clientCardPanel, mainRow));
  assert.ok(containsElement(mainRow, mainTabs));
  assert.ok(containsElement(mainRow, addBlockBtn));
  assert.ok(containsElement(clientCardPanel, mainTabs));
  assert.ok(containsElement(clientCardPanel, blocksPanel));
  assert.ok(containsElement(clientCardPanel, automationPanel));
  assert.ok(!containsElement(blocksPanel, mainTabs));
  assert.ok(!containsElement(automationPanel, mainTabs));
  assert.ok(!containsElement(blocksPanel, addBlockBtn));
  assert.ok(!containsElement(automationPanel, addBlockBtn));

  const quickReplies = fs.readFileSync(path.join(templatesDir, "quick_replies.html"), "utf8");
  const quickRepliesWorkspace = findElement(quickReplies, (node) => hasClass(node, "quick-replies-workspace") && hasClass(node, "panel"), "quick replies panel workspace");
  const quickRepliesCreateBtn = findElement(quickReplies, (node) => node.attrs.id === "createQuickReplyBtn", "create quick reply button");
  const quickRepliesList = findElement(quickReplies, (node) => node.attrs.id === "quickRepliesList", "quick replies list");
  assert.ok(containsElement(quickRepliesWorkspace, quickRepliesCreateBtn));
  assert.ok(containsElement(quickRepliesWorkspace, quickRepliesList));
  assert.ok(quickRepliesCreateBtn.start < quickRepliesList.start);

  const managementUsers = fs.readFileSync(path.join(templatesDir, "management_users.html"), "utf8");
  const managementUsersPanel = findElement(managementUsers, (node) => hasClass(node, "management-users-table"), "management users panel");
  const managementUsersControls = findElement(managementUsers, (node) => hasClass(node, "management-users-controls"), "management users controls");
  const managementUsersSearch = findElement(managementUsers, (node) => hasClass(node, "management-users-search"), "management users search");
  const managementUsersStatus = findElement(managementUsers, (node) => node.attrs.id === "managementUsersStatus", "management users status");
  const managementUsersScroll = findElement(managementUsers, (node) => hasClass(node, "table-scroll"), "management users table scroll");
  assert.ok(containsElement(managementUsersPanel, managementUsersControls));
  assert.ok(containsElement(managementUsersSearch, managementUsersStatus));
  assert.ok(containsElement(managementUsersPanel, managementUsersScroll));
  assert.ok(managementUsersControls.start < managementUsersScroll.start);

  const reviews = fs.readFileSync(path.join(templatesDir, "reviews.html"), "utf8");
  const reviewsPanel = findElement(reviews, (node) => hasClass(node, "reviews-page") && hasClass(node, "panel"), "reviews workspace panel");
  const reviewsFilters = findElement(reviews, (node) => hasClass(node, "reviews-filters"), "reviews filters");
  const reviewsTable = findElement(reviews, (node) => node.attrs.id === "reviewTableWrap", "reviews table");
  assert.ok(containsElement(reviewsPanel, reviewsFilters));
  assert.ok(containsElement(reviewsPanel, reviewsTable));
  const reviewsStyles = fs.readFileSync(path.join(root, "api", "app", "static", "reviews.css"), "utf8");
  assert.match(reviewsStyles, /\.reviews-page\s*\{[^}]*margin:\s*12px;[^}]*overflow:\s*auto;/s);

  const managementFeedback = fs.readFileSync(path.join(templatesDir, "management_feedback.html"), "utf8");
  const managementFeedbackPanel = findElement(managementFeedback, (node) => hasClass(node, "management-feedback-panel"), "management feedback panel");
  const managementFeedbackDescription = findElement(managementFeedback, (node) => hasClass(node, "management-feedback-description"), "management feedback description");
  const managementFeedbackFilters = findElement(managementFeedback, (node) => hasClass(node, "management-feedback-filters"), "management feedback filters");
  assert.ok(containsElement(managementFeedbackPanel, managementFeedbackDescription));
  assert.ok(containsElement(managementFeedbackPanel, managementFeedbackFilters));
  assert.ok(managementFeedbackDescription.start < managementFeedbackFilters.start);

  const managementTasks = fs.readFileSync(path.join(templatesDir, "management_tasks.html"), "utf8");
  const managementTasksPanel = findElement(managementTasks, (node) => hasClass(node, "management-tasks-panel"), "management tasks panel");
  const internalTaskCreateBtn = findElement(managementTasks, (node) => node.attrs.id === "internalTaskCreateBtn", "internal task create button");
  const managementTasksTabs = findElement(managementTasks, (node) => hasClass(node, "management-tasks-tabs"), "management tasks tabs");
  assert.ok(containsElement(managementTasksPanel, internalTaskCreateBtn));
  assert.ok(containsElement(managementTasksPanel, managementTasksTabs));
  assert.ok(internalTaskCreateBtn.start < managementTasksTabs.start);
});

test("bot buttons separates shell back from internal level navigation", () => {
  const template = fs.readFileSync(path.join(templatesDir, "settings_bot_buttons.html"), "utf8");
  assert.match(template, /<button id="botButtonsBackBtn" class="action" type="button">Назад<\/button>/);
  assert.equal((template.match(/botButtonsBackBtn|backToSettingsBtn/g) || []).length, 1);
  assert.doesNotMatch(template, /id="backToSettingsBtn"|data-shell-back/);
  assert.match(template, /id="botButtonsPageTitle" class="settings-title">Кнопки бота/);
  assert.match(template, /id="botButtonsBreadcrumb" class="bot-buttons-breadcrumb hidden">Кнопки бота/);

  const source = fs.readFileSync(path.join(root, "api", "app", "static", "settings_bot_buttons.js"), "utf8");
  assert.match(source, /backButton:\s*document\.getElementById\("botButtonsBackBtn"\)/);
  assert.match(source, /el\.backButton\?\.addEventListener\("click"/);
  assert.match(source, /el\.backButton\.textContent = isRoot \? "Назад" : "На уровень вверх"/);
  assert.match(source, /HelpdeskAppShell\?\.goBack/);
  assert.match(source, /handleBack\(\);/);
  assert.match(source, /el\.pageTitle\.textContent = "Кнопки бота"/);
  assert.doesNotMatch(source, /HelpdeskAppShell\.navigate\("\/settings"\)/);
});

test("management tasks sidebar item is the only active management task link", () => {
  const appShell = fs.readFileSync(path.join(root, "api", "app", "static", "app_shell.js"), "utf8");
  assert.match(appShell, /url\.pathname\.startsWith\("\/management"\) && url\.pathname !== "\/management\/tasks"/);

  const template = fs.readFileSync(path.join(templatesDir, "management_tasks.html"), "utf8");
  assert.doesNotMatch(template, /href="\/management"\s*>\s*[\s\S]{0,120}class="settings-link active/);
  assert.match(template, /<a class="settings-link active admin-only hidden" href="\/management\/tasks">/);
});

test("targeted page actions use scoped spacing and hide only neutral completion statuses", () => {
  assert.match(stylesSource, /\.main\[data-shell-page="settings-users"\] \.settings-panel > #createUserBtn[\s\S]*margin: 4px 4px 12px;[\s\S]*width: auto;/);
  assert.match(stylesSource, /\.main\[data-shell-page="management-users"\] \.management-users-controls \{[\s\S]*padding: 4px 4px 12px;/);
  assert.match(stylesSource, /\.main\[data-shell-page="quick-replies"\] \.quick-replies-workspace > #createQuickReplyBtn/);
  assert.match(stylesSource, /\.main\[data-shell-page="settings-client-card"\] \.client-card-main-row/);
  assert.match(fs.readFileSync(path.join(root, "api", "app", "static", "management_tasks.js"), "utf8"), /el\.status\.hidden = !message \|\| message === "Готово"/);
  const managementUsersScript = fs.readFileSync(path.join(root, "api", "app", "static", "management_users.js"), "utf8");
  assert.match(managementUsersScript, /node\.hidden = !message \|\| message === "Готово"/);
  assert.match(managementUsersScript, /setStatus\(root, `\\u041d\\u0430\\u0439\\u0434\\u0435\\u043d\\u043e: \$\{payload\?\.total \?\? 0\}`\)/);
  assert.match(managementUsersScript, /setStatus\(root, error\.message\)/);
});

test("menu workspaces use scoped inset and fill available height", () => {
  assert.match(stylesSource, /\.main\[data-shell-page="management-users"\] \.management-users-page,[\s\S]*\.main\[data-shell-page="management-tasks"\] \.management-tasks-page\s*\{[^}]*box-sizing:\s*border-box;[^}]*height:\s*100%;[^}]*min-height:\s*0;[^}]*padding:\s*12px;[^}]*overflow:\s*hidden;/);
  assert.match(stylesSource, /\.main\[data-shell-page="management-feedback"\]\s*\{[^}]*grid-template-rows:\s*auto minmax\(0, 1fr\);[^}]*overflow:\s*hidden;/s);
  assert.match(stylesSource, /\.main\[data-shell-page="settings-notification-bot-basic"\] \.content\s*\{[^}]*grid-template-rows:\s*minmax\(0, 1fr\);[^}]*overflow:\s*hidden;/s);
  assert.match(stylesSource, /\.main\[data-shell-page="quick-replies"\] \.quick-replies-list\s*\{[^}]*overflow-y:\s*auto;[^}]*overflow-x:\s*hidden;/s);
});
