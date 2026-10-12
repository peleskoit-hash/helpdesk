const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.join(__dirname, "..");
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
  assert.ok(massScript.includes('window.HelpdeskAppShell.navigate("/management")'));
  assert.ok(massScript.includes('window.location.href = "/management"'));
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
    ".management-users-head {",
  );
  const bodyScope = sourceBetween(stylesSource, "body {", 'body:not([data-sidebar-ready="1"])');
  const sidebarScope = sourceBetween(stylesSource, ".sidebar {", ".sidebar-brand {");

  assert.match(mainScope, /min-height:\s*0;[\s\S]*overflow:\s*hidden;/);
  assert.match(
    pageScope,
    /height:\s*100%;[\s\S]*min-height:\s*0;[\s\S]*overflow-y:\s*auto;[\s\S]*overflow-x:\s*hidden;/,
  );
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
