const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("api/app/static/management_saved_files.js", "utf8");
const tick = () => new Promise((resolve) => setImmediate(resolve));

class Element {
  constructor() {
    this.handlers = {};
    this.children = [];
    this.dataset = {};
    this.value = "";
    this.classList = { add() {}, remove() {} };
  }
  addEventListener(name, handler) { (this.handlers[name] ||= []).push(handler); }
  removeEventListener(name, handler) {
    this.handlers[name] = this.handlers[name].filter((value) => value !== handler);
  }
  fire(name, event = {}) {
    return Promise.all((this.handlers[name] || []).map((handler) => handler({ target: this, ...event })));
  }
  appendChild(child) { this.children.push(child); }
  set innerHTML(value) { this.html = value; this.children = []; }
  showPicker() { this.pickerOpened = true; }
}

async function setup({ fail = false, failMethod = "PUT", confirm = true, pending = false, used = true } = {}) {
  const elements = {};
  const root = { querySelector: (selector) => elements[selector] ||= new Element() };
  const requests = [];
  const confirmations = [];
  const item = { id: "file-id", display_filename: "old.pdf", file_size: 4, used, usage_sources: used ? ["bot_buttons", "quick_replies"] : [] };
  let factory, release;
  const window = {
    HelpdeskDialogs: { confirm: async (message) => { confirmations.push(message); return confirm; } },
    HelpdeskAppShell: { registerPage: (name, init) => { factory = init; } },
  };
  vm.runInNewContext(source, {
    window,
    document: { createElement: () => new Element() },
    FormData,
    fetch: async (url, options) => {
      requests.push({ url, options });
      if (options.method === "PUT" && pending) await new Promise((resolve) => { release = resolve; });
      return {
        ok: !(fail && options.method === failMethod), status: 500,
        json: async () => {
          if (fail && options.method === failMethod) {
            return { detail: { error: { message: "Ошибка записи. Прежний файл сохранён." } } };
          }
          if (options.method === "DELETE") return { deleted: true };
          if (options.method) return { ...item, display_filename: "new.txt", file_size: 3 };
          return { items: [item] };
        },
      };
    },
  });
  let page = factory({ root });
  await tick();
  const el = (name) => elements["#managementSavedFiles" + name];
  async function select() {
    await el("TableBody").fire("click", { target: { closest: () => ({ dataset: { savedFileId: item.id } }) } });
  }
  async function choose() {
    await el("ReplaceBtn").fire("click");
    el("ReplaceInput").files = [new File(["new"], "new.txt", { type: "text/plain" })];
    return el("ReplaceInput").fire("change");
  }
  return { el, requests, confirmations, select, choose, release: () => release(),
    destroy: () => page.destroy(), reopen: () => { page = factory({ root }); } };
}

test("replacement accepts a used file, confirms, updates row and keeps selection", async () => {
  const app = await setup();
  assert.equal(app.el("ReplaceBtn").disabled, true);
  await app.select();
  assert.equal(app.el("ReplaceBtn").disabled, false);
  assert.equal(app.el("DeleteBtn").disabled, true);
  await app.choose();
  assert.equal(app.el("ReplaceInput").pickerOpened, true);
  assert.match(app.confirmations[0], /во всех существующих привязках/);
  const put = app.requests.find((request) => request.options.method === "PUT");
  assert.equal(put.url, "/api/v1/management/saved-files/file-id/content");
  assert.equal(put.options.body.get("file").name, "new.txt");
  const row = app.el("TableBody").children[0];
  assert.match(row.html, /new.txt/);
  assert.match(row.className, /is-selected/);
  assert.equal(app.el("ReplaceInput").value, "");
  assert.equal(app.el("Status").textContent, "Файл заменён");
  assert.equal(app.el("Status").hidden, false);
  assert.match(app.el("ActionNote").textContent, /Файл используется:.*Удаление недоступно/);
  assert.equal(app.el("ActionNote").hidden, false);
});

test("cancel does not upload", async () => {
  const app = await setup({ confirm: false });
  await app.select();
  await app.choose();
  assert.equal(app.requests.length, 1);
  assert.equal(app.el("ReplaceInput").value, "");
});

test("replacement errors keep original row selected and allow retry", async () => {
  const app = await setup({ fail: true });
  await app.select();
  await app.choose();
  assert.match(app.el("Status").textContent, /Прежний файл сохранён/);
  assert.match(app.el("TableBody").children[0].html, /old.pdf/);
  assert.match(app.el("TableBody").children[0].className, /is-selected/);
  assert.equal(app.el("ReplaceBtn").disabled, false);
  assert.equal(app.el("Status").hidden, false);
  assert.match(app.el("ActionNote").textContent, /Удаление недоступно/);
});

test("upload and rename results survive rendering the independent usage warning", async () => {
  const app = await setup();
  app.el("UploadInput").files = [new File(["new"], "new.txt")];
  await app.el("UploadInput").fire("change");
  assert.equal(app.el("Status").textContent, "Файл добавлен");
  assert.match(app.el("ActionNote").textContent, /Удаление недоступно/);
  app.el("RenameInput").value = "new.txt";
  await app.el("RenameForm").fire("submit", { preventDefault() {} });
  assert.equal(app.el("Status").textContent, "Файл переименован");
  await app.select();
  assert.equal(app.el("Status").textContent, "Файл переименован");
  assert.match(app.el("ActionNote").textContent, /Удаление недоступно/);
});

test("successful deletion leaves a visible result", async () => {
  const app = await setup({ used: false });
  await app.select();
  await app.el("DeleteBtn").fire("click");
  assert.equal(app.el("Status").textContent, "Файл удалён");
  assert.equal(app.el("Status").hidden, false);
  assert.equal(app.el("ActionNote").hidden, true);
});

test("deletion error remains visible after reloading the table", async () => {
  const app = await setup({ used: false, fail: true, failMethod: "DELETE" });
  await app.select();
  await app.el("DeleteBtn").fire("click");
  assert.equal(app.requests.length, 3);
  assert.match(app.el("Status").textContent, /Ошибка записи/);
  assert.equal(app.el("Status").hidden, false);
  assert.equal(app.el("TableBody").children.length, 1);
});

test("pending replacement blocks duplicate requests and conflicting actions", async () => {
  const app = await setup({ pending: true });
  await app.select();
  const upload = app.choose();
  await tick();
  for (const name of ["ReplaceBtn", "UploadBtn", "RenameBtn", "DeleteBtn"]) {
    assert.equal(app.el(name).disabled, true);
  }
  await app.el("ReplaceInput").fire("change");
  assert.equal(app.requests.filter((request) => request.options.method === "PUT").length, 1);
  app.release();
  await upload;
  assert.equal(app.el("ReplaceBtn").disabled, false);
});

test("shell destroy and reopen leave one replacement handler", async () => {
  const app = await setup();
  app.destroy();
  assert.equal(app.el("ReplaceBtn").handlers.click.length, 0);
  assert.equal(app.el("ReplaceInput").handlers.change.length, 0);
  app.reopen();
  await tick();
  await app.select();
  await app.choose();
  assert.equal(app.el("ReplaceInput").handlers.change.length, 1);
  assert.equal(app.requests.filter((request) => request.options.method === "PUT").length, 1);
});
