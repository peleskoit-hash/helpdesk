import json
from pathlib import Path
import subprocess

import pytest


BOOT = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
global.window = global;
class Node {
  constructor(tag = "div") {
    this.tagName = tag; this.children = []; this.dataset = {}; this.style = {};
    this.listeners = {}; this.className = ""; this.value = ""; this._text = "";
    this.classList = {
      add: (...values) => {this.className += " " + values.join(" ");},
      remove: (...values) => {this.className = this.className.split(" ").filter(v => !values.includes(v)).join(" ");},
      contains: value => this.className.split(" ").includes(value),
    };
  }
  get isConnected() {return this === document.body || Boolean(this.parent?.isConnected);}
  append(...nodes) {for (const node of nodes) {node.parent = this; this.children.push(node);}}
  appendChild(node) {this.append(node);}
  set textContent(value) {this.clear(); this._text = value;}
  get textContent() {return this._text + this.children.map(n => n.textContent).join("");}
  set innerHTML(value) {this.textContent = value;}
  clear() {for (const child of this.children) child.parent = null; this.children = []; this._text = "";}
  addEventListener(name, fn) {this.listeners[name] = fn;}
  removeAttribute(name) {delete this[name];}
  pause() {this.paused = true;}
  load() {}
  focus() {}
}
global.document = {createElement: tag => new Node(tag), body: new Node("body")};
global.TicketApi = {headers: () => ({Authorization: "test-auth"}), formatFileSize: () => "1 KB"};
const fetches = [], animations = [], revoked = [];
global.fetch = async (url, options) => {
  assert.equal(options.headers.Authorization, "test-auth");
  fetches.push(url);
  return {ok: true, blob: async () => new Blob(["image"]), arrayBuffer: async () => new ArrayBuffer(1)};
};
global.URL = {createObjectURL: () => "blob:authorized", revokeObjectURL: url => revoked.push(url)};
global.pako = {ungzip: () => JSON.stringify({frames: [1]})};
global.lottie = {loadAnimation: options => {
  const animation = {options, destroyed: false, destroy() {this.destroyed = true;}};
  animations.push(animation); return animation;
}};
for (const file of ["ticket_attachments.js", "ticket_composer.js", "ticket_message_view.js"]) {
  eval(fs.readFileSync("api/app/static/" + file, "utf8"));
}
const el = {messages: new Node(), quotePreview: new Node(), quotePreviewAuthor: new Node(),
  quotePreviewText: new Node(), messageInput: new Node()};
el.quotePreview.append(el.quotePreviewAuthor, el.quotePreviewText);
document.body.append(el.messages, el.quotePreview);
const ctx = {state: {role: "agent", userId: "agent", quoteMessageId: "source"}, el,
  messageAuthorLabel: m => m.message_type === "client" ? "Клиент" : m.author_name,
  fmtMessageStamp: () => "", linkifyText: text => text, setStatus() {}, showError(e) {throw Error(e);},
  isStickerOnlyMessage: TicketAttachments.isStickerOnlyMessage,
  messageRenderSignature: TicketAttachments.messageRenderSignature,
  cleanupAttachmentObjectUrls: TicketAttachments.cleanupAttachmentObjectUrls,
  cleanupStickerResources: TicketAttachments.cleanupStickerResources,
  renderAttachments: (message, container) => TicketAttachments.renderAttachments(ctx, message, container)};
function find(node, className) {
  return [node, ...node.children.flatMap(n => find(n, className))].filter(n => n.className.split(" ").includes(className));
}
function media(node) {return node.children.flatMap(n => [n, ...media(n)]).filter(n => ["img", "video"].includes(n.tagName));}
async function settle() {for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve));}
const attachment = (name, extra = {}) => ({id: name, original_filename: name,
  download_url: "/api/authorized/" + name, ...extra});
function render(card, signature = null) {
  ctx.state.card = card;
  const result = TicketMessageView.renderMessages(ctx, card, signature, ctx.state.quoteMessageId);
  TicketComposer.refreshComposerActions(ctx);
  return result.signature;
}
'''


def run_node(script, *args):
    result = subprocess.run(["node", "-e", BOOT + script, *args],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("variant", ["short", "long", "emoji", "text_and_photo", "photo", "photos",
                                     "webp", "webm", "tgs", "file", "empty", "missing", "deleted"])
def test_same_quote_above_composer_and_in_reloaded_message(variant):
    run_node(r'''
(async () => {
  const variant = JSON.parse(process.argv[1]);
  const source = {id: "source", message_type: "client", body: "", attachments: []};
  const photo = attachment("photo.jpg", {is_image: true, mime_type: "image/jpeg"});
  if (variant === "short") source.body = "Короткий текст";
  if (variant === "long") source.body = "А".repeat(101);
  if (variant === "emoji") source.body = "🙂".repeat(101);
  if (variant === "text_and_photo") {source.body = "Только текст"; source.attachments = [photo];}
  if (variant === "photo") source.attachments = [photo];
  if (variant === "photos") source.attachments = [photo, attachment("2.jpg", {is_image: true}), attachment("3.jpg", {is_image: true})];
  if (["webp", "webm", "tgs"].includes(variant)) {
    source.attachments = [attachment("sticker." + variant,
      {mime_type: {webp: "image/webp", webm: "video/webm", tgs: "application/x-tgsticker"}[variant]})];
  }
  if (variant === "file") source.attachments = [attachment("Документ.pdf")];
  if (variant === "deleted") {source.body = "Скрытый текст"; source.message_status = "deleted";}
  const reply = {id: "reply", message_type: "agent", author_name: "Сотрудник", body: "Ответ", quote_message_id: "source"};
  let card = {messages: variant === "missing" ? [reply] : [source, reply]};
  let signature = render(card);
  await settle();
  function check() {
    const quote = find(el.messages, "message-quote")[0];
    assert.ok(quote);
    assert.equal(find(quote, "quote-preview-author")[0].textContent, el.quotePreviewAuthor.textContent);
    assert.equal(find(quote, "quote-preview-text")[0].textContent, el.quotePreviewText.textContent);
    assert.deepEqual(media(quote).map(n => n.tagName), media(el.quotePreviewText).map(n => n.tagName));
    const text = el.quotePreviewText.textContent;
    if (variant === "long") assert.equal(text, "А".repeat(100) + "…");
    if (variant === "emoji") assert.equal(text, "🙂".repeat(100) + "…");
    if (variant === "short") assert.equal(text, source.body);
    if (variant === "text_and_photo") {assert.equal(text, "Только текст"); assert.equal(media(quote).length, 0);}
    if (variant === "file") assert.equal(text, "Документ.pdf");
    if (variant === "photos") assert.equal(text, "+2");
    if (["photo", "photos", "webp", "webm"].includes(variant)) {
      assert.equal(media(quote).length, 1);
      assert.equal(media(quote)[0].src, "blob:authorized");
    }
    if (variant === "tgs") assert.ok(animations.some(a => a.options.container.parent === find(quote, "quote-preview-text")[0]));
    if (["missing", "deleted"].includes(variant)) assert.equal(text, "Исходное сообщение недоступно");
    if (variant === "empty") assert.equal(text, "Сообщение без текста");
  }
  check();
  // A freshly loaded card reconstructs quotes using the ES DESK message ID.
  card = JSON.parse(JSON.stringify(card));
  el.messages.clear();
  signature = render(card);
  await settle();
  check();
  assert.equal(render(card, signature), signature);
  await settle();
  check();
  if (["photo", "photos", "webp", "webm", "tgs"].includes(variant)) assert.ok(fetches.length);
  TicketAttachments.destroyAllAttachmentResources();
})().catch(error => {console.error(error); process.exitCode = 1;});
''', json.dumps(variant))


@pytest.mark.parametrize("kind", ["tgs", "webm"])
def test_sticker_original_and_multiple_quotes_have_independent_resources(kind):
    run_node(r'''
(async () => {
  const kind = JSON.parse(process.argv[1]);
  const source = {id: "source", message_type: "client", body: "",
    attachments: [attachment("sticker." + kind)]};
  const reply = {id: "reply", message_type: "agent", body: "Ответ", quote_message_id: source.id};
  const card = {messages: [source, reply, {...reply, id: "reply-2"}]};
  render(card);
  await settle();
  const original = kind === "tgs" ? animations.find(a => a.options.autoplay) : media(el.messages).find(n => n.autoplay);
  assert.ok(original);
  if (kind === "tgs") {
    assert.equal(animations.filter(a => !a.options.autoplay && !a.destroyed).length, 3);
    assert.equal(original.destroyed, false);
    assert.equal(new Set(animations.map(a => a.options.container)).size, 4);
    assert.notEqual(original.options.animationData, animations.find(a => !a.options.autoplay).options.animationData);
  }
  TicketComposer.clearQuote(ctx);
  TicketAttachments.cleanupStickerResources(new Set([source.attachments[0].id]));
  if (kind === "tgs") {
    assert.equal(original.destroyed, false);
    assert.equal(animations.filter(a => !a.options.autoplay && !a.destroyed).length, 2);
  } else assert.ok(!original.paused);
  el.messages.clear();
  TicketAttachments.cleanupStickerResources(new Set());
  if (kind === "tgs") assert.ok(animations.every(a => a.destroyed));
  else assert.ok(original.paused);
})().catch(error => {console.error(error); process.exitCode = 1;});
''', json.dumps(kind))


def test_quote_link_and_source_edits_invalidate_render_signature():
    run_node(r'''
const source = {id: "source", message_type: "client", body: "Первый текст"};
const other = {id: "other", message_type: "agent", body: "Другой текст", author_name: "Автор"};
const reply = {id: "reply", message_type: "agent", body: "Ответ", quote_message_id: source.id};
const card = {messages: [source, other, reply]};
let signature = render(card);
source.body = "Изменённый текст";
let next = render(card, signature);
assert.notEqual(next, signature);
assert.equal(find(find(el.messages, "message-quote")[0], "quote-preview-text")[0].textContent, source.body);
reply.quote_message_id = other.id;
signature = next;
next = render(card, signature);
assert.notEqual(next, signature);
assert.equal(find(find(el.messages, "message-quote")[0], "quote-preview-author")[0].textContent, "Автор");
''')
