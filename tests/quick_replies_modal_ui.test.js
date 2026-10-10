const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("api/app/static/quick_replies.js", "utf8");

class Element {
  constructor(id = "") {
    this.id = id;
    this.children = [];
    this.parentNode = null;
    this.handlers = {};
    this.dataset = {};
    this.value = "";
    this.textContent = "";
    this.innerHTML = "";
    this.disabled = false;
    this.readOnly = false;
    this.className = "";
    this.files = [];
    this.classList = {
      add: (name) => {
        const names = new Set(this.className.split(" ").filter(Boolean));
        names.add(name);
        this.className = [...names].join(" ");
      },
      remove: (name) => {
        const names = new Set(this.className.split(" ").filter(Boolean));
        names.delete(name);
        this.className = [...names].join(" ");
      },
      toggle: (name, enabled) => {
        const names = new Set(this.className.split(" ").filter(Boolean));
        if (enabled) names.add(name); else names.delete(name);
        this.className = [...names].join(" ");
      },
      contains: (name) => this.className.split(" ").includes(name),
    };
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  setAttribute(name, value) {
    this[name] = String(value);
  }

  focus() {
    this.focused = true;
  }

  addEventListener(name, handler) {
    (this.handlers[name] ||= []).push(handler);
  }

  removeEventListener(name, handler) {
    this.handlers[name] = (this.handlers[name] || []).filter((item) => item !== handler);
  }

  fire(name, event = {}) {
    for (const handler of this.handlers[name] || []) handler({ target: this, ...event });
  }

  closest(selector) {
    if (selector === "[data-reply-id]" && this.dataset.replyId) return this;
    if (selector === "[data-file-id]" && this.dataset.fileId) return this;
    if (selector === "[data-use]" && this.dataset.use) return this;
    return this.parentNode?.closest?.(selector) || null;
  }
}

function tick() {
  return new Promise((resolve) => setImmediate(resolve));
}

async function settle() {
  await tick();
  await tick();
}

async function loadQuickReplies({ replies = [], confirm = () => true } = {}) {
  const ids = [
    "statusText",
    "createQuickReplyBtn",
    "quickRepliesList",
    "quickReplyModal",
    "quickReplyModalTitle",
    "closeQuickReplyModalBtn",
    "cancelQuickReplyBtn",
    "saveQuickReplyBtn",
    "quickReplyTitleInput",
    "quickReplyBodyInput",
    "quickReplyAuthorFooter",
    "quickReplyFileInput",
    "attachQuickReplyFileBtn",
    "quickReplyFileUploadStatus",
    "quickReplyFilesBlock",
    "quickReplyFilesTitle",
    "quickReplyFilesList",
  ];
  const elements = Object.fromEntries(ids.map((id) => [id, new Element(id)]));
  elements.quickReplyModal.className = "modal hidden";
  elements.quickReplyModal.appendChild(new Element("dialog"));

  const document = {
    handlers: {},
    getElementById: (id) => elements[id] || null,
    createElement: () => new Element(),
    addEventListener(name, handler) {
      (this.handlers[name] ||= []).push(handler);
    },
    removeEventListener(name, handler) {
      this.handlers[name] = (this.handlers[name] || []).filter((item) => item !== handler);
    },
    fire(name, event = {}) {
      for (const handler of this.handlers[name] || []) handler(event);
    },
  };

  const confirms = [];
  const requests = [];
  const window = {
    location: { search: "", href: "" },
    HelpdeskSidebar: { init: async () => undefined },
    HelpdeskAppShell: {
      registerPage(name, value) {
        assert.equal(name, "quick-replies");
        this.factory = value;
      },
    },
    confirm(message) {
      confirms.push(message);
      return confirm(message);
    },
  };

  vm.runInNewContext(source, {
    window,
    document,
    console,
    URLSearchParams,
    encodeURIComponent,
    FormData: class {
      append() {}
    },
    fetch: async (url, options = {}) => {
      requests.push({ url, options });
      if (url === "/api/v1/auth/session") {
        return { ok: true, status: 200, text: async () => JSON.stringify({ effective_user: { id: "u1", role: "agent" } }) };
      }
      if (url === "/api/v1/quick-replies" && !options.method) {
        return { ok: true, status: 200, text: async () => JSON.stringify(replies) };
      }
      return { ok: true, status: 200, text: async () => JSON.stringify({ id: "saved" }) };
    },
  });

  const lifecycle = await window.HelpdeskAppShell.factory();
  await settle();
  return { document, el: elements, confirms, requests, lifecycle };
}

function isModalOpen(el) {
  return !el.quickReplyModal.classList.contains("hidden");
}

const dirtyMessage = "Есть несохранённые изменения. Закрыть без сохранения?";

test("quick reply modal closes on Esc and backdrop click, but not inside dialog click", async () => {
  const { document, el, confirms } = await loadQuickReplies();
  el.createQuickReplyBtn.fire("click");
  assert.equal(isModalOpen(el), true);

  el.quickReplyModal.fire("click", { target: el.quickReplyModal.children[0] });
  assert.equal(isModalOpen(el), true);

  el.quickReplyModal.fire("click");
  assert.equal(isModalOpen(el), false);
  assert.deepEqual(confirms, []);

  el.createQuickReplyBtn.fire("click");
  document.fire("keydown", { key: "Escape" });
  assert.equal(isModalOpen(el), false);
  assert.deepEqual(confirms, []);
});

test("quick reply modal confirms dirty title, body, and file list before closing", async () => {
  const reply = {
    id: "r1",
    short_title: "Old title",
    body: "Old body",
    can_edit: true,
    saved_files: [{ id: "f1", display_filename: "a.txt" }],
  };
  const { el, confirms } = await loadQuickReplies({ replies: [reply] });
  const card = el.quickRepliesList.children[0].children[0];

  el.quickRepliesList.fire("click", { target: card });
  el.quickReplyTitleInput.value = "New title";
  el.cancelQuickReplyBtn.fire("click");
  assert.equal(isModalOpen(el), false);

  el.quickRepliesList.fire("click", { target: card });
  el.quickReplyBodyInput.value = "New body";
  el.closeQuickReplyModalBtn.fire("click");
  assert.equal(isModalOpen(el), false);

  el.quickRepliesList.fire("click", { target: card });
  el.quickReplyFileInput.files = [{ name: "new.txt" }];
  el.quickReplyFileInput.fire("change");
  el.quickReplyModal.fire("click");
  assert.equal(isModalOpen(el), false);
  assert.deepEqual(confirms, [dirtyMessage, dirtyMessage, dirtyMessage]);
});

test("quick reply modal does not confirm when draft returns to initial values", async () => {
  const reply = {
    id: "r1",
    short_title: "Title",
    body: "Body",
    can_edit: true,
    saved_files: [{ id: "f1", display_filename: "a.txt" }],
  };
  const { el, confirms } = await loadQuickReplies({ replies: [reply] });

  el.quickRepliesList.fire("click", { target: el.quickRepliesList.children[0].children[0] });
  el.quickReplyTitleInput.value = "Changed";
  el.quickReplyTitleInput.value = "Title";
  el.quickReplyBodyInput.value = "Changed";
  el.quickReplyBodyInput.value = "Body";
  el.cancelQuickReplyBtn.fire("click");

  assert.equal(isModalOpen(el), false);
  assert.deepEqual(confirms, []);
});

test("quick reply modal keeps dirty draft when close confirmation is rejected", async () => {
  const { el, confirms } = await loadQuickReplies({ confirm: () => false });
  el.createQuickReplyBtn.fire("click");
  el.quickReplyTitleInput.value = "Draft";
  el.cancelQuickReplyBtn.fire("click");

  assert.equal(isModalOpen(el), true);
  assert.equal(el.quickReplyTitleInput.value, "Draft");
  assert.deepEqual(confirms, [dirtyMessage]);
});

test("quick reply modal closes after successful save without confirmation", async () => {
  const { el, confirms, requests } = await loadQuickReplies();
  el.createQuickReplyBtn.fire("click");
  el.quickReplyTitleInput.value = "Draft";
  el.quickReplyBodyInput.value = "Body";
  el.saveQuickReplyBtn.fire("click");
  await settle();

  assert.equal(isModalOpen(el), false);
  assert.deepEqual(confirms, []);
  assert.equal(requests.some((request) => request.url === "/api/v1/quick-replies" && request.options.method === "POST"), true);
});
