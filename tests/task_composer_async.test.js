const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const path = require("node:path");

global.window = global;
global.TicketApi = { formatFileSize: String };
global.FormData = class {
  constructor() { this.values = []; }
  append(key, value) { this.values.push([key, value]); }
  entries() { return this.values.values(); }
};
// Server polling is deliberately never completed by these tests.
global.setInterval = () => 1;
global.clearInterval = () => {};
eval(fs.readFileSync(path.join(__dirname, "../api/app/static/ticket_composer.js"), "utf8"));
const composer = global.TicketComposer;
const turn = () => new Promise((resolve) => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function context(workflow = "task", mode = "reply") {
  const calls = [], errors = [], history = [];
  const ctx = {
    state: { ticketId: "task-1", card: { workflow_type: workflow }, role: "agent", composeMode: mode,
      selectedFiles: [], filePreloads: new Map(), isSendingReply: false },
    el: { messageInput: { value: "Task answer" }, replyFilesInput: { value: "" } },
    canUseAttachmentPreload: () => true,
    setStatus: () => {}, showError: (error) => errors.push(error),
    isOnsitePhraseJs: () => false,
    loadCard: async () => { history.splice(0, history.length, ...calls.filter((c) => /\/(reply|comment)$/.test(c.url))); },
    api: async (url, options) => { calls.push({ url, options }); return { ok: true }; },
  };
  return { ctx, calls, errors, history };
}
function selected(ctx, name, status = "uploading") {
  const file = { name, size: 3 };
  ctx.state.selectedFiles.push(file);
  ctx.state.filePreloads.set(file, { stage: status, status, preloadId: name, removed: false });
  return file;
}
function entries(call) { return [...call.options.body.entries()]; }

test("task saves uploading and ready files as one message, then allows the next reply", async () => {
  const { ctx, calls, history } = context();
  selected(ctx, "a.pdf"); selected(ctx, "b.pdf", "ready");
  await composer.sendByMode(ctx);
  assert.equal(calls.length, 1);
  assert.deepEqual(entries(calls[0]), [["text", "Task answer"], ["preloaded_attachment_id", "a.pdf"], ["preloaded_attachment_id", "b.pdf"]]);
  assert.equal(history.length, 1);
  assert.equal(ctx.state.selectedFiles.length, 0);
  assert.equal(ctx.state.isSendingReply, false);
  ctx.el.messageInput.value = "Next answer";
  await composer.sendByMode(ctx);
  assert.deepEqual(JSON.parse(calls[1].options.body), { text: "Next answer", quote_message_id: null });
  assert.equal(history.length, 2);
});

test("task with a single uploading file uses preloadId immediately", async () => {
  const { ctx, calls } = context();
  selected(ctx, "a.pdf");
  await composer.sendByMode(ctx);
  assert.deepEqual(entries(calls[0]), [["text", "Task answer"], ["single_attachment", "true"], ["preloaded_attachment_id", "a.pdf"]]);
});

test("waits for all browser uploads automatically and ignores repeated Send", async () => {
  const { ctx, calls } = context();
  const uploads = [deferred(), deferred()];
  let uploadIndex = 0;
  const api = ctx.api;
  ctx.api = (url, options) => url.endsWith("/attachment-preloads")
    ? uploads[uploadIndex++].promise : api(url, options);
  composer.queueSelectedFiles(ctx, [{ name: "a.pdf", size: 3 }, { name: "b.pdf", size: 3 }]);
  const sending = composer.sendByMode(ctx);
  await turn();
  await composer.sendByMode(ctx);
  assert.equal(calls.length, 0);
  assert.equal(ctx.state.isSendingReply, true);
  uploads[0].resolve({ id: "preload-a", status: "uploading" });
  await turn();
  assert.equal(calls.length, 0);
  uploads[1].resolve({ id: "preload-b", status: "ready" });
  await sending;
  assert.equal(calls.length, 1);
  assert.deepEqual(entries(calls[0]), [["text", "Task answer"], ["preloaded_attachment_id", "preload-a"], ["preloaded_attachment_id", "preload-b"]]);
});

test("browser upload error ends waiting and keeps text and selected files", async () => {
  const { ctx, calls, errors } = context();
  const upload = deferred();
  ctx.api = () => upload.promise;
  const file = { name: "a.pdf", size: 3 };
  composer.queueSelectedFiles(ctx, [file]);
  const sending = composer.sendByMode(ctx);
  upload.reject(new Error("Upload failed"));
  await sending;
  assert.deepEqual(errors, ["Upload failed"]);
  assert.equal(ctx.state.isSendingReply, false);
  assert.equal(ctx.el.messageInput.value, "Task answer");
  assert.deepEqual(ctx.state.selectedFiles, [file]);
  assert.equal(ctx.state.filePreloads.get(file).stage, "failed");
  assert.equal(calls.length, 0);
});

test("one failed browser upload ends Send even while another upload is pending", async () => {
  const { ctx, calls, errors } = context();
  const uploads = [deferred(), deferred()];
  let index = 0;
  ctx.api = () => uploads[index++].promise;
  composer.queueSelectedFiles(ctx, [{ name: "a.pdf", size: 3 }, { name: "b.pdf", size: 3 }]);
  const sending = composer.sendByMode(ctx);
  uploads[0].reject(new Error("First upload failed"));
  await sending;
  assert.deepEqual(errors, ["First upload failed"]);
  assert.equal(ctx.state.isSendingReply, false);
  assert.equal(ctx.state.selectedFiles.length, 2);
  assert.equal(calls.length, 0);
  uploads[1].resolve({ id: "pending-file", status: "uploading" });
  await turn();
});

test("save failure retains usable preload IDs and retry stays one grouped request", async () => {
  const { ctx, calls, errors } = context();
  const a = selected(ctx, "a.pdf"), b = selected(ctx, "b.pdf", "ready");
  const saving = deferred();
  ctx.api = async (url, options) => { calls.push({ url, options }); return saving.promise; };
  const first = composer.sendByMode(ctx);
  await turn();
  await composer.sendByMode(ctx);
  assert.equal(calls.length, 1);
  saving.reject(new Error("Save failed"));
  await first;
  assert.deepEqual(errors, ["Save failed"]);
  assert.equal(ctx.el.messageInput.value, "Task answer");
  assert.deepEqual(ctx.state.selectedFiles, [a, b]);
  assert.equal(ctx.state.filePreloads.get(a).stage, "uploading");
  assert.equal(ctx.state.filePreloads.get(b).stage, "ready");
  ctx.api = async (url, options) => { calls.push({ url, options }); return { ok: true }; };
  await composer.sendByMode(ctx);
  assert.deepEqual(entries(calls[1]), entries(calls[0]));
  assert.equal(ctx.state.selectedFiles.length, 0);
});

test("removing a browser upload releases Send and discards its late preloadId", async () => {
  const { ctx, calls } = context();
  const upload = deferred(), api = ctx.api;
  ctx.api = (url, options) => url.endsWith("/attachment-preloads") ? upload.promise : api(url, options);
  const file = { name: "remove.pdf", size: 3 };
  composer.queueSelectedFiles(ctx, [file]);
  selected(ctx, "keep.pdf");
  const sending = composer.sendByMode(ctx);
  await turn();
  await composer.removeSelectedFile(ctx, file);
  await sending;
  assert.deepEqual(entries(calls[0]), [["text", "Task answer"], ["single_attachment", "true"], ["preloaded_attachment_id", "keep.pdf"]]);
  upload.resolve({ id: "late-preload", status: "uploading" });
  await turn();
  assert.equal(calls[1].options.method, "DELETE");
  assert.ok(calls[1].url.endsWith("/late-preload"));
  assert.equal(ctx.state.filePreloads.has(file), false);
});

test("removing the only browser upload cancels an empty answer without hanging", async () => {
  const { ctx, calls } = context();
  ctx.el.messageInput.value = "";
  const upload = deferred(), api = ctx.api;
  ctx.api = (url, options) => url.endsWith("/attachment-preloads") ? upload.promise : api(url, options);
  const file = { name: "only.pdf", size: 3 };
  composer.queueSelectedFiles(ctx, [file]);
  const sending = composer.sendByMode(ctx);
  await composer.removeSelectedFile(ctx, file);
  await sending;
  assert.equal(calls.length, 0);
  assert.equal(ctx.state.isSendingReply, false);
  upload.resolve({ id: "removed-only", status: "uploading" });
  await turn();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].options.method, "DELETE");
});

test("new files selected while saving are preserved for the next answer", async () => {
  const { ctx, calls } = context();
  selected(ctx, "first.pdf");
  const saving = deferred(), api = ctx.api;
  ctx.api = async (url, options) => { const result = await api(url, options); await saving.promise; return result; };
  const sending = composer.sendByMode(ctx);
  await turn();
  const second = selected(ctx, "next.pdf");
  saving.resolve();
  await sending;
  assert.deepEqual(ctx.state.selectedFiles, [second]);
  assert.equal(entries(calls[0]).some(([key, value]) => value === "next.pdf"), false);
  ctx.api = api;
  ctx.el.messageInput.value = "Next";
  await composer.sendByMode(ctx);
  assert.deepEqual(entries(calls[1]), [["text", "Next"], ["single_attachment", "true"], ["preloaded_attachment_id", "next.pdf"]]);
});

test("browser upload transitions and removal notify existing task controls", async () => {
  const { ctx } = context();
  const upload = deferred();
  ctx.api = () => upload.promise;
  const stages = [];
  const file = { name: "task.pdf", size: 3 };
  ctx.refreshTaskUploadControls = () => stages.push(ctx.state.filePreloads.get(file)?.stage || "removed");
  composer.queueSelectedFiles(ctx, [file]);
  assert.equal(stages.at(-1), "browser_uploading");
  upload.resolve({ id: "task-preload", status: "ready" });
  await turn();
  assert.equal(stages.at(-1), "ready");
  ctx.api = async () => ({ ok: true });
  await composer.removeSelectedFile(ctx, file);
  await turn();
  assert.equal(ctx.state.selectedFiles.length, 0);
  assert.ok(stages.length >= 3);
});

test("ordinary tickets still send files individually; comments still wait for ready", async () => {
  const ticket = context("ticket");
  selected(ticket.ctx, "a.pdf"); selected(ticket.ctx, "b.pdf", "ready");
  await composer.sendByMode(ticket.ctx);
  assert.equal(ticket.calls.length, 2);
  for (const workflow of ["ticket", "task"]) {
    const comment = context(workflow, "comment");
    selected(comment.ctx, "a.pdf");
    await composer.sendByMode(comment.ctx);
    assert.equal(comment.calls.length, 0);
    assert.equal(comment.errors.length, 1);
    assert.equal(comment.ctx.el.messageInput.value, "Task answer");
  }
});
