const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "api", "app", "static", "profile_notification_bot.js"), "utf8");
const styles = fs.readFileSync(path.join(__dirname, "..", "api", "app", "static", "styles.css"), "utf8");

function loadTestHooks() {
  const instrumented = source.replace(/\}\)\(\);\s*$/, 'window.__profileNotificationBotTest = { shouldSkipTemplateValidationForInheritanceTransition, initProfileNotificationBotPage };\n})();');
  const context = {
    window: {
      HelpdeskAppShell: { registerPage() {} },
      HelpdeskSidebar: { init: async () => ({ role: "admin" }) },
    },
    document: {
      getElementById() { return null; },
    },
    fetch: async () => ({ ok: true, text: async () => "{}" }),
    setTimeout,
    clearTimeout,
  };
  vm.createContext(context);
  vm.runInContext(instrumented, context);
  return context.window.__profileNotificationBotTest;
}

test("profile notification bot uses one config-driven renderer for two tabs", () => {
  assert.match(source, /const PERSONAL_NOTIFICATION_TAB_CONFIG = \{/);
  assert.match(source, /new_assignment: \{/);
  assert.match(source, /new_message: \{/);
  assert.match(source, /internal_task_completion: \{[\s\S]+title: "Выполнение внутренних задач"/);
  assert.match(source, /function renderTab\(config\)/);
  assert.match(source, /function bindTab\(config\)/);
  assert.match(source, /function syncDraft\(config\)/);
  assert.match(source, /function validateTab\(config, options = \{\}\)/);
  assert.equal((source.match(/function renderTab\(config\)/g) || []).length, 1);
  assert.equal((source.match(/function bindTab\(config\)/g) || []).length, 1);
});

test("profile notification bot keeps drafts while switching tabs", () => {
  assert.match(source, /const current = getActiveConfig\(\);[\s\S]+syncDraft\(current\);[\s\S]+state\.activeType = button\.getAttribute\("data-tab-type"\)/);
});

test("syncDraft skips inactive unrendered tab before mutating saved draft", () => {
  assert.match(source, /const pane = el\.root\?\.querySelector\(`\[data-notification-pane="\$\{config\.baseType\}"\]`\);\s+if \(!pane\) return;\s+const textarea = pane\.querySelector\("\[data-template-textarea\]"\);\s+draft\.template = String\(textarea\?\.value \|\| ""\)/s);
});

test("profile notification bot has base readonly and personal editable paths", () => {
  assert.match(source, /const readonly = draft\.useBaseSettings/);
  assert.match(source, /readonly \? "readonly disabled" : ""/);
  assert.match(source, /if \(!draft\?\.useBaseSettings && tg\.bindToolbar/);
  assert.match(source, /const nextUseBaseSettings = !!event\.target\.checked/);
  assert.match(source, /activeDraft\.useBaseSettings = nextUseBaseSettings/);
});

test("personal notification tabs use dedicated two-column class", () => {
  assert.match(source, /class="notification-tabs notification-tabs-personal"/);
});

test("basic notification tabs fit five desktop tabs in one row with responsive fallback", () => {
  assert.match(styles, /\.notification-tabs \{[\s\S]+grid-template-columns: repeat\(5, minmax\(0, 1fr\)\)/);
  assert.match(styles, /@media \(max-width: 900px\) \{[\s\S]+\.notification-tabs \{[\s\S]+grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/);
});

test("delivery switches are page-level with explicit two-column order", () => {
  assert.match(source, /const DELIVERY_SWITCH_COLUMNS = \[/);
  assert.match(source, /key: "new_assignment"[\s\S]+key: "new_message"[\s\S]+key: "notify_internal_task_completion"[\s\S]+key: "transfer_to_agent"[\s\S]+key: "transfer_from_agent"/);
  assert.match(source, /label: "Выполнение внутренних задач"/);
  assert.match(source, /function renderDeliverySwitches\(\)/);
  assert.match(source, /isAdminMode\(\)[\s\S]+notify_internal_task_completion/);
  assert.match(source, /notification-control-card notification-delivery-card/);
  assert.match(source, /data-delivery-switch/);
  assert.match(source, /item\.mandatory \? "disabled" : ""/);
  assert.match(source, /state\.delivery\.new_assignment = true/);
  assert.match(source, /delivery: \{[\s\S]+transfer_to_agent:[\s\S]+transfer_from_agent:[\s\S]+new_message:[\s\S]+notify_internal_task_completion:/);
});

test("admin notification page hides agent-only tabs and switches", () => {
  assert.match(source, /function isAdminMode\(\) \{\s+return state\.role === "admin";\s+\}/);
  assert.match(source, /function visibleTabConfigs\(\)/);
  assert.match(source, /isAdminMode\(\)[\s\S]+config\.baseType === "internal_task_completion"[\s\S]+config\.baseType !== "internal_task_completion"/);
  assert.match(source, /\$\{config \? renderTab\(config\) : ""\}/);
  assert.match(source, /isAdminMode\(\)\s+\?\s+\[\[DELIVERY_SWITCH_COLUMNS\.flat\(\)\.find\(\(item\) => item\.key === "notify_internal_task_completion"\)\]/);
  assert.match(source, /items: visibleTabConfigs\(\)\.map/);
  assert.match(source, /notification-admin-compact/);
});

test("restore-base requires confirmation", () => {
  assert.match(source, /function restoreBase\(\)/);
  assert.match(source, /window\.confirm\(/);
  assert.match(source, /restore-base\/\$\{encodeURIComponent\(activeType\)\}/);
});

test("restore button remains in the upper control bar and is disabled in base mode", () => {
  assert.match(source, /notification-control-card notification-page-mode/);
  assert.match(source, /const restoreDisabled = !draft \|\| draft\.useBaseSettings/);
  assert.match(source, /data-restore-base \$\{restoreDisabled \? "disabled" : ""\}/);
  assert.equal((source.match(/restoreDisabled \? "disabled" : ""/g) || []).length, 1);
  assert.doesNotMatch(source, /\$\{draft && !draft\.useBaseSettings \? `<button type="button" class="action" data-restore-base>/);
});

test("profile notification bot reuses backend tags and shared Telegram editor", () => {
  assert.match(source, /item\.tags/);
  assert.match(source, /item\.tag_items/);
  assert.match(source, /tags: tagItems\.map\(\(tag\) => tag\.label\)/);
  assert.match(source, /draft\.tagItems\.map/);
  assert.doesNotMatch(source, /\[Номер заявки\]/);
  assert.doesNotMatch(source, /\[Тело сообщения\]/);
  assert.match(source, /window\.TelegramTextEditor/);
  assert.match(source, /tg\.toolbarHtml/);
  assert.match(source, /tg\.bindAtomicTokens\(textarea, draft\.tags\)/);
  assert.match(source, /tg\.renderTelegramPreviewHtml/);
  assert.match(source, /tg\.validateTelegramHtml/);
});

test("profile notification bot shows tooltip metadata on tag buttons without changing insert behavior", () => {
  assert.match(source, /data-tooltip="\$\{escapeHtml\(tag\.description\)\}"/);
  assert.doesNotMatch(source, /title="\$\{escapeHtml\(tag\.description\)\}"/);
  assert.match(source, /aria-label="\$\{escapeHtml\(tag\.description \? `\$\{tag\.label\}: \$\{tag\.description\}` : tag\.label\)\}"/);
  assert.match(source, /data-insert-tag="\$\{escapeHtml\(tag\.label\)\}"/);
});

test("save syncs active tab while leaving inactive drafts in state", () => {
  assert.match(source, /function syncAllDrafts\(\) \{\s+visibleTabConfigs\(\)\.forEach\(syncDraft\);\s+syncDeliveryFromDom\(\);\s+\}/s);
  assert.match(source, /if \(!pane\) return;/);
});

test("inheritance is stored per notification type", () => {
  assert.match(source, /useBaseSettings: item\.use_base_settings !== false/);
  assert.match(source, /use_base_settings: draft\?\.useBaseSettings !== false/);
  assert.match(source, /state\.useBaseSettings = payload\?\.use_base_settings !== false/);
});

test("admin personal editor uses the same validation as visible tabs", () => {
  assert.match(source, /function validateBeforeSave\(options = \{\}\) \{[\s\S]+for \(const config of visibleTabConfigs\(\)\) \{[\s\S]+const error = validateTab\(config, options\)/);
  const validateBeforeSave = source.match(/function validateBeforeSave\(options = \{\}\) \{[\s\S]+?\n    \}/)?.[0] || "";
  assert.doesNotMatch(validateBeforeSave, /isAdminMode/);
});

test("delivery switches are rendered outside the tab pane and survive tab switching", () => {
  assert.match(source, /el\.root\.innerHTML = `\s+\$\{renderModeControls\(draft\)\}\s+\$\{renderDeliverySwitches\(\)\}\s+\$\{renderTabs\(\)\}\s+\$\{config \? renderTab\(config\) : ""\}/s);
  assert.match(source, /syncDeliveryFromDom\(\);\s+state\.activeType = button\.getAttribute/);
});

test("binding status is in the action row next to save", () => {
  assert.match(source, /notification-actions notification-actions-with-binding/);
  assert.match(source, /notification-binding-status[\s\S]+data-save/);
});

test("inheritance transition bypasses empty-template validation only for first base-to-personal switch", () => {
  const helper = loadTestHooks();

  assert.equal(
    helper.shouldSkipTemplateValidationForInheritanceTransition(
      { skipTemplateValidationForInheritanceTransitionType: "new_assignment" },
      { baseType: "new_assignment" },
      { useBaseSettings: false, template: "" },
    ),
    true,
  );

  assert.equal(
    helper.shouldSkipTemplateValidationForInheritanceTransition(
      {},
      { baseType: "new_assignment" },
      { useBaseSettings: false, template: "" },
    ),
    false,
  );

  assert.equal(
    helper.shouldSkipTemplateValidationForInheritanceTransition(
      { skipTemplateValidationForInheritanceTransitionType: "new_assignment" },
      { baseType: "new_assignment" },
      { useBaseSettings: true, template: "" },
    ),
    false,
  );

  assert.equal(
    helper.shouldSkipTemplateValidationForInheritanceTransition(
      { skipTemplateValidationForInheritanceTransitionType: "new_assignment" },
      { baseType: "new_message" },
      { useBaseSettings: false, template: "" },
    ),
    false,
  );
});
