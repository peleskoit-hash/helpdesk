const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.join(__dirname, "..");
const templatesDir = path.join(root, "api", "app", "templates");
const staticDir = path.join(root, "api", "app", "static");

function read(...parts) {
  return fs.readFileSync(path.join(root, ...parts), "utf8");
}

test("top statusText element is removed from affected templates", () => {
  const templates = [
    "statuses.html",
    "mass_notifications.html",
    "settings_client_card.html",
    "settings_automatic_messages.html",
    "tickets.html",
    "ticket.html",
    "settings_users.html",
    "settings_bot_buttons.html",
    "settings.html",
    "index.html",
    "management.html",
    "profile.html",
    "quick_replies.html",
    "profile_notification_bot.html",
    "settings_notification_bot_basic.html",
  ];

  for (const template of templates) {
    const source = fs.readFileSync(path.join(templatesDir, template), "utf8");
    assert.doesNotMatch(source, /id="statusText"/, template);
  }
});

test("tickets list no longer requires statusText to initialize", () => {
  const source = read("api", "app", "static", "list.js");
  const requiredBlock = source.slice(
    source.indexOf("const REQUIRED_LIST_ELEMENT_KEYS"),
    source.indexOf("function requireListElements"),
  );
  assert.doesNotMatch(requiredBlock, /statusText/);
  assert.match(source, /function setStatus\(\) \{\}/);
});

test("tickets list load errors stay visible while silent refresh stays quiet", () => {
  const source = read("api", "app", "static", "list.js");
  const loadTicketsBlock = source.slice(
    source.indexOf("async function loadTickets"),
    source.indexOf("async function loadCounts"),
  );

  assert.match(loadTicketsBlock, /if \(!silent\) showError\(`Не удалось загрузить список заявок\.\\n\\n/);
  assert.doesNotMatch(loadTicketsBlock, /setStatus\(`Ошибка/);
});

test("app shell errors use copyable modal dialog instead of statusText", () => {
  const shell = read("api", "app", "static", "app_shell.js");
  const dialog = read("api", "app", "static", "error_dialog.js");

  assert.doesNotMatch(shell, /statusText/);
  assert.match(shell, /HelpdeskErrorDialog\?\.show\?\.\(\{/);
  assert.match(dialog, /appErrorDialogDetails/);
  assert.match(dialog, /navigator\.clipboard\.writeText\(text\)/);
  assert.match(dialog, /appErrorDialogCloseBtn/);
  assert.match(dialog, /appErrorDialogOkBtn/);
});

test("ordinary ready and loading statuses do not open the shared error dialog", () => {
  const dialog = read("api", "app", "static", "error_dialog.js");
  assert.doesNotMatch(dialog, /Готово|Загрузка/);
});

test("shared error dialog can copy and close technical details", async () => {
  const source = read("api", "app", "static", "error_dialog.js");
  const nodes = new Map();
  let clipboardText = "";

  function node(id = "") {
    if (nodes.has(id)) return nodes.get(id);
    const handlers = {};
    const item = {
      id,
      value: "",
      textContent: "",
      className: "",
      innerHTML: "",
      classList: {
        add(name) { item.className = `${item.className} ${name}`.trim(); },
        remove(name) { item.className = item.className.split(/\s+/).filter((part) => part && part !== name).join(" "); },
      },
      addEventListener(name, handler) { (handlers[name] ||= []).push(handler); },
      querySelector(selector) { return node(selector.replace("#", "")); },
      select() { item.selected = true; },
      fire(name, event = {}) {
        return Promise.all((handlers[name] || []).map((handler) => handler({ target: item, ...event })));
      },
    };
    nodes.set(id, item);
    return item;
  }

  const document = {
    body: { appendChild: (item) => { document.modal = item; } },
    createElement: () => node("appErrorDialog"),
    getElementById: (id) => nodes.get(id) || null,
    execCommand: () => true,
  };
  const context = vm.createContext({
    window: {},
    document,
    navigator: { clipboard: { writeText: async (text) => { clipboardText = text; } } },
  });
  vm.runInContext(source, context);

  context.window.HelpdeskErrorDialog.show({
    title: "Ошибка загрузки страницы",
    message: "Не удалось открыть страницу.",
    error: "HTTP 500",
  });
  assert.equal(node("appErrorDialogDetails").value, "HTTP 500");
  assert.doesNotMatch(node("appErrorDialog").className, /hidden/);

  await node("appErrorDialogCopyBtn").fire("click");
  assert.equal(clipboardText, "HTTP 500");

  node("appErrorDialogCloseBtn").fire("click");
  assert.match(node("appErrorDialog").className, /hidden/);
});

test("independent status indicators remain referenced", () => {
  const integrationsTemplate = read("api", "app", "templates", "management_integrations.html");
  const integrationsScript = read("api", "app", "static", "management_integrations.js");
  const savedFilesScript = read("api", "app", "static", "management_saved_files.js");
  const integrationsStatusIndex = integrationsTemplate.indexOf('id="integrationLogStatus"');
  const integrationsResetIndex = integrationsTemplate.indexOf('id="integrationLogResetBtn"');

  assert.match(integrationsTemplate, /id="integrationLogStatus"/);
  assert.ok(integrationsStatusIndex > integrationsResetIndex);
  assert.match(read("api", "app", "static", "styles.css"), /\.main\[data-shell-page="management-integrations"\] #integrationLogStatus\s*\{[^}]*margin-left:\s*auto;/s);
  assert.match(integrationsScript, /integrationLogStatus/);
  assert.match(savedFilesScript, /function setStatus\(message\)/);
  assert.match(savedFilesScript, /el\.Status\.hidden = !message \|\| message === "Готово"/);
  assert.match(savedFilesScript, /setStatus\("Загрузка\.\.\."\)/);
  assert.match(savedFilesScript, /setStatus\(error\.message \|\| "Ошибка загрузки"\)/);
});

test("management feedback workspace has an explicit stretching grid row", () => {
  const styles = read("api", "app", "static", "styles.css");
  assert.match(styles, /\.main\[data-shell-page="management-feedback"\] \.management-feedback-page\s*\{[^}]*grid-template-rows:\s*minmax\(0, 1fr\);[^}]*align-content:\s*stretch;/s);
});
