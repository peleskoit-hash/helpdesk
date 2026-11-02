from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.db import Base
from api.app.models import Client, Company, Ticket, TicketAttachment, TicketMessage, TicketStatus, TicketTaskKind, TicketWorkflowType
from api.app.services.ticket_admin_actions import _delete_ticket_hard


def _session():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def _seed_group_parent_with_child(session, tmp_path):
    now = datetime.utcnow()
    company = Company(id=str(uuid.uuid4()), name="ACME")
    client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Client")
    parent = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        subject="Parent",
        status=TicketStatus.NEW.value,
        workflow_type=TicketWorkflowType.TASK.value,
        task_kind=TicketTaskKind.GROUP_PARENT.value,
        created_at=now,
        updated_at=now,
    )
    child = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        subject="Child",
        status=TicketStatus.NEW.value,
        workflow_type=TicketWorkflowType.TASK.value,
        task_kind=TicketTaskKind.GROUP_CHILD.value,
        parent_ticket_id=parent.id,
        created_at=now,
        updated_at=now,
    )
    parent_message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=parent.id,
        message_type="system",
        body="Parent attachment",
        created_at=now,
    )
    child_message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=child.id,
        message_type="system",
        body="Child attachment",
        created_at=now,
    )
    parent_file = tmp_path / "parent.txt"
    child_file = tmp_path / "child.txt"
    parent_file.write_text("parent", encoding="utf-8")
    child_file.write_text("child", encoding="utf-8")
    parent_attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=parent.id,
        message_id=parent_message.id,
        source="upload",
        local_path=str(parent_file),
        original_filename="parent.txt",
        created_at=now,
    )
    child_attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=child.id,
        message_id=child_message.id,
        source="upload",
        local_path=str(child_file),
        original_filename="child.txt",
        created_at=now,
    )
    session.add_all([company, client, parent, child, parent_message, child_message, parent_attachment, child_attachment])
    session.commit()
    return parent, child, parent_attachment, child_attachment, parent_file, child_file


def test_delete_ticket_hard_removes_group_child_attachment_files(tmp_path):
    session = _session()
    parent, child, parent_attachment, child_attachment, parent_file, child_file = _seed_group_parent_with_child(session, tmp_path)
    parent_id = parent.id
    child_id = child.id
    parent_attachment_id = parent_attachment.id
    child_attachment_id = child_attachment.id

    warnings = _delete_ticket_hard(session, parent)
    session.flush()

    assert warnings == []
    assert session.execute(select(TicketAttachment.id).where(TicketAttachment.id == parent_attachment_id)).scalar_one_or_none() is None
    assert session.execute(select(TicketAttachment.id).where(TicketAttachment.id == child_attachment_id)).scalar_one_or_none() is None
    assert session.execute(select(Ticket.id).where(Ticket.id == child_id)).scalar_one_or_none() is None
    assert parent_file.exists() and child_file.exists()
    session.commit()
    assert not parent_file.exists()
    assert not child_file.exists()
    assert session.execute(select(Ticket.id).where(Ticket.id == parent_id)).scalar_one_or_none() is None
