import asyncio
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

from api.app.models import TicketAttachment, TicketMessage
from api.app.routers import tickets
from api.app.services import ticket_messages as service
from test_ticket_messages_outbound import _JsonRequest, _seed_ticket, _session_bundle


@pytest.fixture
def case(monkeypatch):
    engine, _, db = _session_bundle()
    ticket = _seed_ticket(db)
    ticket.channel = "telegram"
    ticket.assignee_id = "agent-1"
    db.commit()

    def forbidden(*args, **kwargs):
        pytest.fail("Internal actions must not call Telegram or resolve its contact")

    for name in ("edit_message_text", "edit_message_caption", "delete_message"):
        monkeypatch.setattr(service, name, forbidden)
    monkeypatch.setattr(tickets, "_telegram_chat_id_for_client", forbidden)
    try:
        yield SimpleNamespace(db=db, ticket=ticket)
    finally:
        db.close()
        engine.dispose()


def add_message(case, kind="internal", author="agent-1", status="normal", telegram_id=None):
    message = TicketMessage(id="message", ticket_id=case.ticket.id, message_type=kind,
                            author_user_id=author, body="Original", message_status=status,
                            telegram_message_id=telegram_id)
    case.db.add(message)
    case.db.commit()
    return message


def act(case, operation, user):
    kwargs = dict(ticket_id=case.ticket.id, message_id="message", db=case.db, user=user)
    if operation == "edit":
        return tickets.edit_ticket_message(**kwargs, payload=SimpleNamespace(text="Edited"))
    return tickets.delete_ticket_message(**kwargs)


@pytest.mark.parametrize("operation", ["edit", "delete"])
@pytest.mark.parametrize("kind", ["agent", "internal"])
@pytest.mark.parametrize("role,author,allowed", [
    ("admin", "agent-2", True), ("admin", None, True),
    ("agent", "agent-1", True), ("agent", "agent-2", False), ("agent", None, False),
])
def test_message_author_permissions(case, operation, kind, role, author, allowed):
    # Web avoids Telegram for ordinary messages; internal Telegram is checked below.
    case.ticket.channel = "web"
    message = add_message(case, kind=kind, author=author)
    user = SimpleNamespace(id="agent-1", role=role)
    if allowed:
        assert act(case, operation, user) == {"ok": True}
        case.db.refresh(message)
        assert message.message_status == ("edited" if operation == "edit" else "deleted")
        assert message.body == ("Edited" if operation == "edit" else "Original")
    else:
        with pytest.raises(HTTPException) as exc:
            act(case, operation, user)
        assert exc.value.status_code == 403
        assert exc.value.detail["error"]["code"] == "message_author_required"
        case.db.refresh(message)
        assert (message.body, message.message_status) == ("Original", "normal")


@pytest.mark.parametrize("operation", ["edit", "delete"])
@pytest.mark.parametrize("role", ["admin", "agent"])
@pytest.mark.parametrize("kind", ["client", "system"])
def test_client_and_system_messages_are_immutable(case, operation, role, kind):
    message = add_message(case, kind=kind)
    with pytest.raises(HTTPException) as exc:
        act(case, operation, SimpleNamespace(id="agent-1", role=role))
    assert exc.value.status_code == 409
    assert (message.body, message.message_status) == ("Original", "normal")


@pytest.mark.parametrize("operation", ["edit", "delete"])
@pytest.mark.parametrize("role", ["admin", "agent", "chiki_piki"])
@pytest.mark.parametrize("reason", ["closed", "deleted"])
def test_internal_comment_readonly_guards(case, operation, role, reason):
    case.ticket.button_config_key = "Колл Центр|ticket|A"
    message = add_message(case, status="deleted" if reason == "deleted" else "normal")
    if reason == "closed":
        case.ticket.status = "closed"
        case.db.commit()
    with pytest.raises(HTTPException) as exc:
        act(case, operation, SimpleNamespace(id="agent-1", role=role))
    assert exc.value.status_code == (403 if role == "chiki_piki" else 409)
    expected = "readonly_role" if role == "chiki_piki" else (
        "ticket_closed_readonly" if reason == "closed" else "message_deleted")
    assert exc.value.detail["error"]["code"] == expected
    assert message.body == "Original"


@pytest.mark.parametrize("workflow", ["ticket", "task"])
@pytest.mark.parametrize("telegram_id", [None, "501"])
def test_internal_edit_delete_preserves_files_without_telegram(case, tmp_path, workflow, telegram_id):
    case.ticket.workflow_type = workflow
    message = add_message(case, telegram_id=telegram_id)
    path = tmp_path / "private.txt"
    path.write_text("Private attachment", encoding="utf-8")
    attachment = TicketAttachment(id="attachment", ticket_id=case.ticket.id, message_id=message.id,
        source="helpdesk", local_path=str(path), telegram_message_id="502", telegram_delivery_status="sending",
        original_filename="private.txt")
    case.db.add(attachment)
    case.db.commit()
    snapshot = {column.name: getattr(attachment, column.name) for column in attachment.__table__.columns}
    user = SimpleNamespace(id="agent-1", role="agent")
    assert act(case, "edit", user) == {"ok": True}
    case.db.refresh(message)
    assert (message.body, message.message_status) == ("Edited", "edited")
    assert act(case, "delete", user) == {"ok": True}
    case.db.expire_all()
    assert case.db.get(TicketMessage, message.id).message_status == "deleted"
    assert {name: getattr(case.db.get(TicketAttachment, attachment.id), name) for name in snapshot} == snapshot
    assert path.read_text(encoding="utf-8") == "Private attachment"
    for operation in ("edit", "delete"):
        with pytest.raises(HTTPException) as exc:
            act(case, operation, user)
        assert exc.value.detail["error"]["code"] == "message_deleted"


@pytest.mark.parametrize("status", ["normal", "deleted"])
def test_internal_quote_cannot_be_sent_to_client(case, monkeypatch, status):
    message = add_message(case, status=status, telegram_id="501")

    def forbidden(*args, **kwargs):
        pytest.fail("Quoting an internal comment must fail before client delivery")

    monkeypatch.setattr(tickets, "_send_to_telegram_client", forbidden)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(tickets.reply(ticket_id=case.ticket.id,
            request=_JsonRequest({"text": "Answer", "quote_message_id": message.id}),
            background_tasks=BackgroundTasks(), db=case.db, user=SimpleNamespace(id="agent-1", role="agent")))
    assert exc.value.detail["error"]["code"] == "internal_quote_forbidden"
    assert case.db.query(TicketMessage).count() == 1


@pytest.mark.parametrize("kind,operation", [("agent", "edit"), ("agent", "delete"), ("internal", "edit")])
def test_admin_changes_display_actual_editor_with_legacy_fallback(case, kind, operation):
    case.ticket.channel = "web"
    message = add_message(case, kind=kind, author="other-agent")
    assert act(case, operation, SimpleNamespace(id="admin", role="admin")) == {"ok": True}
    case.db.refresh(message)
    verb = "исправил" if operation == "edit" else "удалил"
    assert message.status_note == f"Администратор {verb} сообщение"
    data = dict(id=message.id, message_type=kind, message_status=message.message_status,
                status_note=message.status_note, author_name="Первоначальный автор", body=message.body)
    script = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
global.window = global;
function node() {
  return {children: [], append(...children) {this.children.push(...children);},
    appendChild(child) {this.children.push(child);}, addEventListener() {}};
}
global.document = {createElement: node};
eval(fs.readFileSync("api/app/static/ticket_message_view.js", "utf8"));
const message = JSON.parse(process.argv[1]);
function note(m) {
  const messages = node();
  TicketMessageView.renderMessages({state: {}, el: {messages},
    fmtMessageStamp: () => "", linkifyText: (v) => v, isStickerOnlyMessage: () => false,
    messageRenderSignature: JSON.stringify, cleanupAttachmentObjectUrls() {},
    cleanupStickerResources() {}, renderAttachments() {}},
    {client_name: "Клиент", messages: [m]}, null, null);
  return messages.children[0].children.find((n) => n.className === "message-status-note").textContent;
}
assert.equal(note(message), message.status_note);
assert.ok(!note(message).includes(message.author_name));
// Both statuses keep their old author-based fallback for missing or blank notes.
for (const kind of ["agent", "internal"]) {
  for (const status of ["edited", "deleted"]) {
    if (kind === "internal" && status === "deleted") continue; // Hidden by the chat renderer.
    const verb = status === "edited" ? "исправил" : "удалил";
    for (const missing of [undefined, null, "", "   "]) {
      assert.equal(note({...message, message_type: kind, message_status: status, status_note: missing}),
        `${message.author_name} ${verb} сообщение`);
    }
    assert.equal(note({...message, message_type: "client", message_status: status}), `Клиент ${verb} сообщение`);
  }
}
'''
    result = subprocess.run(["node", "-e", script, json.dumps(data, ensure_ascii=False)],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr


def test_message_menu_edit_dialog_and_deleted_comment_rendering():
    root = Path(__file__).resolve().parents[1]
    script = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
global.window = global;
global.innerWidth = global.innerHeight = 1000;
function node() {
  const n = {children: [], listeners: {}, style: {}, value: "", classList: {add() {}, remove() {}},
    append(...children) {this.children.push(...children);},
    appendChild(child) {this.children.push(child);},
    addEventListener(name, fn) {this.listeners[name] = fn;},
    getBoundingClientRect() {return {width: 100, height: 100};},
    focus() {}, setSelectionRange() {}};
  Object.defineProperty(n, "innerHTML", {set() {this.children = [];}});
  return n;
}
global.document = {createElement: node, body: node()};
eval(fs.readFileSync("api/app/static/ticket_message_view.js", "utf8"));
const ctx = {state: {role: "agent", userId: "agent-1", ticketId: "ticket", card: {status: "pending"}},
  el: {messageContextMenu: node(), messages: node()}, quoteMessage() {throw Error("Unexpected quote");},
  closeOnBackdropClick() {}, setStatus() {}, loadCard: async () => {}, showError: (text) => {throw Error(text);}};
const event = {preventDefault() {}, stopPropagation() {}, clientX: 50, clientY: 50};
const message = {id: "internal", message_type: "internal", message_status: "normal",
  author_user_id: "agent-1", body: "Private", attachments: [{id: "private-file"}]};
function labels(m) {
  TicketMessageView.showMessageContextMenu(ctx, event, m);
  return ctx.el.messageContextMenu.children.map((n) => n.textContent);
}
assert.deepEqual(labels(message), ["Копировать", "Редактировать", "Удалить"]);
assert.deepEqual(labels({...message, message_type: "agent"}), ["Копировать", "Цитировать", "Редактировать", "Удалить"]);
assert.deepEqual(labels({...message, message_type: "client"}), ["Копировать", "Цитировать"]);
assert.deepEqual(labels({...message, author_user_id: "other"}), ["Копировать"]);
assert.deepEqual(labels({...message, author_user_id: null}), ["Копировать"]);
ctx.state.role = "admin";
assert.deepEqual(labels({...message, author_user_id: null}), ["Копировать", "Редактировать", "Удалить"]);
ctx.state.card.status = "closed";
assert.deepEqual(labels(message), ["Копировать"]);
ctx.state.card.status = "pending";
assert.deepEqual(labels({...message, message_status: "deleted"}), ["Копировать"]);
const calls = [];
ctx.api = async (url, options) => calls.push({url, ...options});
(async () => {
  const editing = TicketMessageView.editAgentMessage(ctx, message);
  assert.equal(ctx.el.editMessageTextarea.value, "Private");
  ctx.el.editMessageTextarea.value = "Edited";
  ctx.el.saveEditMessageBtn.listeners.click();
  await editing;
  assert.equal(calls[0].method, "PATCH");
  assert.deepEqual(JSON.parse(calls[0].body), {text: "Edited"});
  global.confirm = (text) => {assert.equal(text, "Удалить внутренний комментарий?"); return true;};
  await TicketMessageView.deleteAgentMessage(ctx, message);
  assert.equal(calls[1].method, "DELETE");
  assert.equal(calls[1].url, "/api/v1/tickets/ticket/messages/internal");
  const rendered = [], cleaned = [];
  Object.assign(ctx, {fmtMessageStamp: () => "", linkifyText: (v) => v, isStickerOnlyMessage: () => false,
    messageRenderSignature: JSON.stringify, cleanupAttachmentObjectUrls: (ids) => cleaned.push([...ids]),
    cleanupStickerResources() {}, renderAttachments: (m) => rendered.push(m.id)});
  const client = {...message, id: "client", message_type: "client", attachments: [{id: "client-file"}]};
  const card = {messages: [message, client]};
  const before = TicketMessageView.renderMessages(ctx, card, null, null);
  rendered.length = 0;
  card.messages[0] = {...message, message_status: "deleted"};
  const after = TicketMessageView.renderMessages(ctx, card, before.signature, message.id);
  assert.deepEqual(rendered, ["client"]);
  assert.deepEqual(cleaned.at(-1), ["client-file"]);
  assert.equal(ctx.el.messages.children.length, 1);
  assert.equal(after.quoteMissing, true);
  assert.equal(TicketMessageView.renderMessages(ctx, {messages: [message]}, null, message.id).quoteMissing, true);
})().catch((err) => {console.error(err); process.exitCode = 1;});
'''
    result = subprocess.run(["node", "-e", script], cwd=root, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
