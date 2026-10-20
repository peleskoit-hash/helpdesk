from datetime import datetime, timedelta
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import select

from api.app.models import MessageType, TicketAttachment, TicketIntakeState, TicketMessage, TicketStatus
from api.app.routers import telegram, tickets
from api.app.services import telegram_attachments
from api.app.services.assignment import assignee_change_message
from api.app.services.ticket_messages import first_task_assignment_id, initial_task_description_ids
from test_telegram_inbound_attachments_archive import _document_payload, _seed_ticket_context, _session_bundle


@pytest.mark.parametrize("workflow,file_count,caption_index", [
    ("task", 4, 0), ("task", 4, 2), ("task", 4, 3), ("task", 1, 0), ("ticket", 4, 2),
])
@pytest.mark.parametrize("assigned", [False, True])
def test_initial_description_api(monkeypatch, tmp_path, workflow, file_count, caption_index, assigned):
    engine, Session = _session_bundle(tmp_path)
    with Session() as db:
        ticket, contact = _seed_ticket_context(db)
        ticket.workflow_type = workflow
        if workflow == "task":
            ticket.status = TicketStatus.UNASSIGNED.value
            ticket.intake_state = TicketIntakeState.COLLECTING.value
        db.commit()
        monkeypatch.setattr(telegram, "_button_from_callback", lambda *_: None)
        monkeypatch.setattr(telegram, "start_background_archive_inbound_attachment", lambda *_: None)
        monkeypatch.setattr(telegram_attachments.settings, "attachment_storage_dir", str(tmp_path))
        monkeypatch.setattr(telegram_attachments, "get_file", lambda *_args, **_kwargs: {"result": {"file_path": "file.pdf"}})
        admin = SimpleNamespace(id="admin", role="admin")
        assignment_id = None
        for index in range(file_count):
            payload = _document_payload(
                update_id=4100 + index, chat_id=contact.external_chat_id, user_id=contact.external_user_id,
                file_id=f"file-{index}", media_group_id="initial-album" if file_count > 1 else None,
            )
            if index == caption_index:
                payload["message"]["caption"] = "Первоначальное описание"
            assert telegram.process_telegram_update(db, payload)["ticket_id"] == ticket.id
            if assigned and index == 0:
                assignment_id = str(uuid.uuid4())
                db.add(TicketMessage(
                    id=assignment_id, ticket_id=ticket.id, message_type=MessageType.SYSTEM.value,
                    body=assignee_change_message(None, SimpleNamespace(full_name="First agent")),
                    created_at=datetime.utcnow(),
                ))
                db.commit()
            # Partial albums are immediately visible to the administrator, even without assignment.
            partial = tickets.get_ticket(ticket.id, db=db, user=admin)
            expected_count = index + 1 if workflow == "task" else 0
            assert sum(message.is_initial_task_message for message in partial.messages) == expected_count
            assert sum(len(message.attachments) for message in partial.messages) == index + 1

        # A later client message with a different album must retain the combined presentation.
        later = TicketMessage(
            id=str(uuid.uuid4()), ticket_id=ticket.id, message_type=MessageType.CLIENT.value,
            body="Последующее сообщение", telegram_message_id="4200",
            created_at=datetime.utcnow() + timedelta(seconds=1),
        )
        agent = TicketMessage(
            id=str(uuid.uuid4()), ticket_id=ticket.id, message_type=MessageType.AGENT.value,
            body="Ответ сотрудника", telegram_message_id="4201",
            created_at=datetime.utcnow() + timedelta(seconds=2),
        )
        db.add_all([later, agent])
        db.flush()
        for message in [later, agent]:
            db.add(TicketAttachment(
                id=str(uuid.uuid4()), ticket_id=ticket.id, message_id=message.id,
                source="document", local_path="unused", telegram_media_group_id="later-album",
            ))
        db.commit()
        original_messages = [(m.id, m.body) for m in db.execute(select(TicketMessage)).scalars()]
        for _ in range(2):
            card = tickets.get_ticket(ticket.id, db=db, user=admin).model_dump(mode="json")
            descriptions = [message for message in card["messages"] if message["is_initial_task_description"]]
            assert [message["body"] for message in descriptions] == (["Первоначальное описание"] if workflow == "task" else [])
            assert all(message["attachments"] for message in descriptions)
            initial = [message for message in card["messages"] if message["is_initial_task_message"]]
            assert len(initial) == (file_count if workflow == "task" else 0)
            assignments = [message["id"] for message in card["messages"] if message["is_initial_task_assignment"]]
            assert assignments == ([assignment_id] if workflow == "task" and assigned else [])
            if workflow == "task" and assigned and file_count > 1:
                ordered_ids = [message["id"] for message in card["messages"]]
                assert ordered_ids.index(initial[0]["id"]) < ordered_ids.index(assignment_id) < ordered_ids.index(initial[1]["id"])
            assert next(message for message in card["messages"] if message["id"] == later.id)["is_initial_task_description"] is False
            assert next(message for message in card["messages"] if message["id"] == agent.id)["is_initial_task_description"] is False
        assert [(m.id, m.body) for m in db.execute(select(TicketMessage)).scalars()] == original_messages
    engine.dispose()


@pytest.mark.parametrize("initial_telegram_id", ["1", None])
def test_does_not_choose_first_nonempty_text(initial_telegram_id):
    ticket = SimpleNamespace(workflow_type="task", channel="telegram")
    first = TicketMessage(id="first", message_type="client", body="", telegram_message_id=initial_telegram_id)
    later = TicketMessage(id="later", message_type="client", body="Later caption", telegram_message_id="2")
    attachments = {"later": [TicketAttachment(telegram_media_group_id="other-album")]}
    assert initial_task_description_ids(ticket, [first, later], attachments) == set()


def test_assignment_recognition_uses_server_format_and_ignores_client_and_reassignments():
    agent = SimpleNamespace(full_name="Agent")
    body = assignee_change_message(None, agent)
    client = TicketMessage(id="client", message_type="client", body=body)
    unrelated = TicketMessage(id="unrelated", message_type="system", body="prefix " + body)
    first = TicketMessage(id="first", message_type="system", body=body)
    later = TicketMessage(id="later", message_type="system", body=assignee_change_message(None, SimpleNamespace(full_name="Other")))
    reassignment = TicketMessage(id="reassignment", message_type="system", body=assignee_change_message(agent, SimpleNamespace(full_name="Other")))
    assert first_task_assignment_id([client, unrelated, first, later, reassignment]) == first.id
    assert first_task_assignment_id([reassignment, later]) is None
    assert first_task_assignment_id([client, unrelated]) is None
