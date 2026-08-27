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
    Event: class {
      constructor(type, options = {}) {
        this.type = type;
        this.bubbles = !!options.bubbles;
      }
    },
    document: {
      querySelectorAll() { return []; },
      getElementById() { return null; },
    },
  };
  context.global = context.window;
  vm.createContext(context);
  vm.runInContext(source, context);
  return context.window.TelegramTextEditor;
}

function fakeTextarea(value, start = 0, end = start) {
  const listeners = new Map();
  return {
    value,
    selectionStart: start,
    selectionEnd: end,
    dataset: {},
    dispatched: [],
    history: [value],
    historyIndex: 0,
    addEventListener(type, handler) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(handler);
    },
    dispatch(type, event = {}) {
      const payload = {
        type,
        target: this,
        defaultPrevented: false,
        preventDefault() { this.defaultPrevented = true; },
        ...event,
      };
      (listeners.get(type) || []).forEach((handler) => handler(payload));
      return payload;
    },
    dispatchEvent(event) {
      this.dispatched.push(event.type);
    },
    setRangeText(text, start = this.selectionStart, end = this.selectionEnd, selectionMode = "preserve") {
      const before = this.value;
      this.value = before.slice(0, start) + text + before.slice(end);
      const cursor = start + String(text).length;
      this.history = this.history.slice(0, this.historyIndex + 1);
      this.history.push(this.value);
      this.historyIndex = this.history.length - 1;
      if (selectionMode === "select") this.setSelectionRange(start, cursor);
      else this.setSelectionRange(cursor, cursor);
    },
    undo() {
      if (this.historyIndex <= 0) return;
      this.historyIndex -= 1;
      this.value = this.history[this.historyIndex];
    },
    redo() {
      if (this.historyIndex >= this.history.length - 1) return;
      this.historyIndex += 1;
      this.value = this.history[this.historyIndex];
    },
    setSelectionRange(nextStart, nextEnd) {
      this.selectionStart = nextStart;
      this.selectionEnd = nextEnd;
    },
    focus() {},
  };
}

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

test("atomic helper detects only known tag ranges", () => {
  const editor = loadEditor();
  assert.deepEqual(plain(editor.findAtomicTagRanges("x [Автор] y [Не тег]", ["[Автор]"])), [
    { start: 2, end: 9, text: "[Автор]" },
  ]);
});

test("caret inside known tag snaps to nearest boundary", () => {
  const editor = loadEditor();
  assert.equal(editor.snapCaretToAtomicBoundary("x [Автор] y", ["[Автор]"], 5), 2);
  assert.equal(editor.snapCaretToAtomicBoundary("x [Автор] y", ["[Автор]"], 7), 9);
});

test("backspace after tag and delete before tag target the whole token", () => {
  const editor = loadEditor();
  assert.deepEqual(plain(editor.atomicKeyEdit("x [Автор] y", ["[Автор]"], 9, 9, "Backspace")), { start: 2, end: 9 });
  assert.deepEqual(plain(editor.atomicKeyEdit("x [Автор] y", ["[Автор]"], 2, 2, "Delete")), { start: 2, end: 9 });
});

test("partial selection inside tag expands to the whole token", () => {
  const editor = loadEditor();
  assert.deepEqual(plain(editor.expandSelectionToAtomicTags("x [Автор] y", ["[Автор]"], 4, 7)), { start: 2, end: 9 });
});

test("selection crossing text and part of a tag includes the full tag", () => {
  const editor = loadEditor();
  assert.deepEqual(plain(editor.expandSelectionToAtomicTags("hello [Автор] y", ["[Автор]"], 0, 8)), { start: 0, end: 13 });
});

test("arrow keys skip known tags as one token", () => {
  const editor = loadEditor();
  const textarea = fakeTextarea("x [Автор][Номер] y", 9);
  editor.bindAtomicTokens(textarea, ["[Автор]", "[Номер]"]);

  let event = textarea.dispatch("keydown", { key: "ArrowLeft" });
  assert.equal(event.defaultPrevented, true);
  assert.equal(textarea.selectionStart, 2);

  textarea.setSelectionRange(9, 9);
  event = textarea.dispatch("keydown", { key: "ArrowRight" });
  assert.equal(event.defaultPrevented, true);
  assert.equal(textarea.selectionStart, 16);
});

test("two adjacent tags are treated as separate atomic tokens", () => {
  const editor = loadEditor();
  const ranges = plain(editor.findAtomicTagRanges("[Автор][Номер]", ["[Автор]", "[Номер]"]));

  assert.deepEqual(ranges, [
    { start: 0, end: 7, text: "[Автор]" },
    { start: 7, end: 14, text: "[Номер]" },
  ]);
  assert.deepEqual(plain(editor.atomicKeyEdit("[Автор][Номер]", ["[Автор]", "[Номер]"], 7, 7, "Backspace")), {
    start: 0,
    end: 7,
  });
  assert.deepEqual(plain(editor.atomicKeyEdit("[Автор][Номер]", ["[Автор]", "[Номер]"], 7, 7, "Delete")), {
    start: 7,
    end: 14,
  });
});

test("insert inside tag replaces the whole token instead of corrupting it", () => {
  const editor = loadEditor();
  const textarea = fakeTextarea("x [Автор] y", 5);
  editor.bindAtomicTokens(textarea, ["[Автор]"]);

  const event = textarea.dispatch("beforeinput", { inputType: "insertText", data: "Z" });

  assert.equal(event.defaultPrevented, true);
  assert.equal(textarea.value, "x Z y");
  assert.equal(textarea.selectionStart, 3);
});

test("paste selection crossing a tag replaces the full tag range", () => {
  const editor = loadEditor();
  const textarea = fakeTextarea("hello [Автор] y", 4, 8);
  editor.bindAtomicTokens(textarea, ["[Автор]"]);

  const event = textarea.dispatch("beforeinput", {
    inputType: "insertFromPaste",
    dataTransfer: { getData: () => "PASTE" },
  });

  assert.equal(event.defaultPrevented, true);
  assert.equal(textarea.value, "hellPASTE y");
});

test("unknown bracketed text remains editable as normal text", () => {
  const editor = loadEditor();
  assert.deepEqual(plain(editor.findAtomicTagRanges("x [неизвестно] y", ["[Автор]"])), []);
  assert.deepEqual(plain(editor.expandSelectionToAtomicTags("x [неизвестно] y", ["[Автор]"], 4, 7)), { start: 4, end: 7 });
});

test("undo and redo survive atomic tag deletion", () => {
  const editor = loadEditor();
  const textarea = fakeTextarea("x [Автор] y", 9);
  editor.bindAtomicTokens(textarea, ["[Автор]"]);

  const event = textarea.dispatch("keydown", { key: "Backspace" });

  assert.equal(event.defaultPrevented, true);
  assert.equal(textarea.value, "x  y");

  textarea.undo();
  assert.equal(textarea.value, "x [Автор] y");

  textarea.redo();
  assert.equal(textarea.value, "x  y");
});

test("undo and redo survive atomic tag replacement", () => {
  const editor = loadEditor();
  const textarea = fakeTextarea("x [Автор] y", 5);
  editor.bindAtomicTokens(textarea, ["[Автор]"]);

  const event = textarea.dispatch("beforeinput", { inputType: "insertText", data: "Z" });

  assert.equal(event.defaultPrevented, true);
  assert.equal(textarea.value, "x Z y");

  textarea.undo();
  assert.equal(textarea.value, "x [Автор] y");

  textarea.redo();
  assert.equal(textarea.value, "x Z y");
});
