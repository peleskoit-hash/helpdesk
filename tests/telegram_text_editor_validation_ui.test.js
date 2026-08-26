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

test("legacy bot button editor reuses toolbar without delivery flag controls", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "api", "app", "static", "bot_buttons_editor_view.js"), "utf8");

  assert.match(source, /TelegramTextEditor\?\.toolbarHtml/);
  assert.match(source, /TelegramTextEditor\?\.renderTelegramPreviewHtml/);
  assert.doesNotMatch(source, /settingsHtml/);
  assert.doesNotMatch(source, /data-telegram-disable-link-preview/);
  assert.doesNotMatch(source, /data-telegram-send-silently/);
});
