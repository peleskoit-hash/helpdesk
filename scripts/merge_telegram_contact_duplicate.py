from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.app.db import SessionLocal
from api.app.models import Client, ClientProfile, PendingTaskIntent, TelegramContact, Ticket


PROFILE_FIELDS = [
    "contact_name",
    "last_name",
    "email",
    "phone",
    "verification_status",
    "destination",
    "sand_owner",
    "sales_group",
    "department",
    "first_department",
    "group_name",
    "employee_type",
    "location_type",
    "anydesk",
    "internal_number",
    "external_user_id",
    "bitrix_login",
    "bitrix_password",
    "bitrix_id",
]


@dataclass
class MergePlan:
    canonical: TelegramContact
    duplicate: TelegramContact
    canonical_profile: ClientProfile | None
    duplicate_profile: ClientProfile | None
    ticket_count: int
    pending_intent_count: int
    merged_profile_fields: dict[str, Any]
    duplicate_client_can_delete: bool
    canonical_chat_id_after_apply: str
    canonical_chat_id_preserved: bool
    duplicate_chat_id_is_private: bool


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _is_negative_chat_id(external_chat_id: str | None) -> bool:
    return str(external_chat_id or "").strip().startswith("-")


def _is_private_chat_id_for_user(external_chat_id: str | None, external_user_id: str | None) -> bool:
    chat_id = str(external_chat_id or "").strip()
    user_id = str(external_user_id or "").strip()
    if not chat_id or chat_id.startswith("-"):
        return False
    if user_id and chat_id == user_id:
        return True
    return chat_id.isdigit()


def _should_update_contact_chat_id(current_chat_id: str | None, incoming_chat_id: str | None, external_user_id: str | None) -> bool:
    current = str(current_chat_id or "").strip()
    incoming = str(incoming_chat_id or "").strip()
    if not incoming:
        return False
    if not current:
        return True
    current_private = _is_private_chat_id_for_user(current, external_user_id)
    incoming_private = _is_private_chat_id_for_user(incoming, external_user_id)
    if current_private and _is_negative_chat_id(incoming):
        return False
    if _is_negative_chat_id(current) and incoming_private:
        return True
    if current_private and incoming_private:
        return current != incoming
    return current != incoming


def _chat_id_after_merge(canonical: TelegramContact, duplicate: TelegramContact) -> str:
    if _should_update_contact_chat_id(canonical.external_chat_id, duplicate.external_chat_id, canonical.external_user_id):
        return duplicate.external_chat_id
    return canonical.external_chat_id


def _profile_score(profile: ClientProfile | None) -> int:
    if profile is None:
        return 0
    return sum(1 for field in PROFILE_FIELDS if not _blank(getattr(profile, field)))


def _merge_profile_values(canonical: ClientProfile | None, duplicate: ClientProfile | None) -> dict[str, Any]:
    if canonical is None and duplicate is None:
        return {}
    if canonical is None:
        return {field: getattr(duplicate, field) for field in PROFILE_FIELDS if not _blank(getattr(duplicate, field))}
    if duplicate is None:
        return {}

    values: dict[str, Any] = {}
    for field in PROFILE_FIELDS:
        canonical_value = getattr(canonical, field)
        duplicate_value = getattr(duplicate, field)
        if _blank(canonical_value) and not _blank(duplicate_value):
            values[field] = duplicate_value
    if duplicate.is_verified and not canonical.is_verified:
        values["is_verified"] = True
    if duplicate.custom_fields:
        custom_fields = dict(canonical.custom_fields or {})
        for key, value in duplicate.custom_fields.items():
            if key not in custom_fields or _blank(custom_fields.get(key)):
                custom_fields[key] = value
        values["custom_fields"] = custom_fields
    return values


def _contact_by_chat_id(db: Session, external_user_id: str, external_chat_id: str) -> TelegramContact:
    contact = db.execute(
        select(TelegramContact).where(
            TelegramContact.external_user_id == external_user_id,
            TelegramContact.external_chat_id == external_chat_id,
        )
    ).scalar_one_or_none()
    if contact is None:
        raise SystemExit(f"Contact not found for external_user_id={external_user_id!r}, chat_id={external_chat_id!r}")
    return contact


def build_plan(db: Session, external_user_id: str, canonical_chat_id: str, duplicate_chat_id: str) -> MergePlan:
    canonical = _contact_by_chat_id(db, external_user_id, canonical_chat_id)
    duplicate = _contact_by_chat_id(db, external_user_id, duplicate_chat_id)
    if canonical.id == duplicate.id:
        raise SystemExit("Canonical and duplicate resolve to the same contact")

    canonical_profile = db.execute(
        select(ClientProfile).where(ClientProfile.client_id == canonical.client_id)
    ).scalar_one_or_none()
    duplicate_profile = db.execute(
        select(ClientProfile).where(ClientProfile.client_id == duplicate.client_id)
    ).scalar_one_or_none()
    ticket_count = db.execute(
        select(func.count()).select_from(Ticket).where(Ticket.client_id == duplicate.client_id)
    ).scalar_one()
    pending_intent_count = db.execute(
        select(func.count()).select_from(PendingTaskIntent).where(PendingTaskIntent.client_id == duplicate.client_id)
    ).scalar_one()
    duplicate_client_can_delete = True
    canonical_chat_id_after_apply = _chat_id_after_merge(canonical, duplicate)
    return MergePlan(
        canonical=canonical,
        duplicate=duplicate,
        canonical_profile=canonical_profile,
        duplicate_profile=duplicate_profile,
        ticket_count=ticket_count,
        pending_intent_count=pending_intent_count,
        merged_profile_fields=_merge_profile_values(canonical_profile, duplicate_profile),
        duplicate_client_can_delete=duplicate_client_can_delete,
        canonical_chat_id_after_apply=canonical_chat_id_after_apply,
        canonical_chat_id_preserved=canonical_chat_id_after_apply == canonical.external_chat_id,
        duplicate_chat_id_is_private=_is_private_chat_id_for_user(duplicate.external_chat_id, duplicate.external_user_id),
    )


def print_plan(plan: MergePlan) -> None:
    print("Telegram contact duplicate merge plan")
    print(f"canonical_contact_id={plan.canonical.id}")
    print(f"canonical_client_id={plan.canonical.client_id}")
    print(f"duplicate_contact_id={plan.duplicate.id}")
    print(f"duplicate_client_id={plan.duplicate.client_id}")
    print(f"tickets_to_relink={plan.ticket_count}")
    print(f"pending_task_intents_to_relink={plan.pending_intent_count}")
    print("client_id_tables_checked=tickets,pending_task_intents,client_profiles,telegram_contacts")
    print(f"canonical_profile_score={_profile_score(plan.canonical_profile)}")
    print(f"duplicate_profile_score={_profile_score(plan.duplicate_profile)}")
    print(f"profile_fields_to_fill={sorted(plan.merged_profile_fields.keys())}")
    print(f"duplicate_client_can_delete_after_relink={plan.duplicate_client_can_delete}")
    print(f"duplicate_chat_id_is_private={str(plan.duplicate_chat_id_is_private).lower()}")
    print(f"canonical_chat_id_preserved={str(plan.canonical_chat_id_preserved).lower()}")
    print(f"canonical_chat_id_after_apply={plan.canonical_chat_id_after_apply}")


def apply_plan(db: Session, plan: MergePlan) -> None:
    now = datetime.utcnow()
    if plan.canonical_profile is None and plan.duplicate_profile is not None:
        plan.duplicate_profile.client_id = plan.canonical.client_id
        plan.canonical_profile = plan.duplicate_profile
        plan.duplicate_profile = None
    elif plan.canonical_profile is not None:
        for field, value in plan.merged_profile_fields.items():
            setattr(plan.canonical_profile, field, value)
        plan.canonical_profile.updated_at = now
        if plan.duplicate_profile is not None:
            db.delete(plan.duplicate_profile)

    db.query(Ticket).filter(Ticket.client_id == plan.duplicate.client_id).update(
        {Ticket.client_id: plan.canonical.client_id, Ticket.updated_at: now},
        synchronize_session=False,
    )
    db.query(PendingTaskIntent).filter(PendingTaskIntent.client_id == plan.duplicate.client_id).update(
        {PendingTaskIntent.client_id: plan.canonical.client_id},
        synchronize_session=False,
    )
    duplicate_username = plan.duplicate.external_username
    duplicate_display_name = plan.duplicate.external_display_name
    db.delete(plan.duplicate)
    db.flush()
    plan.canonical.external_chat_id = plan.canonical_chat_id_after_apply
    plan.canonical.external_username = duplicate_username or plan.canonical.external_username
    plan.canonical.external_display_name = duplicate_display_name or plan.canonical.external_display_name
    plan.canonical.updated_at = now

    remaining_refs = (
        db.execute(select(func.count()).select_from(Ticket).where(Ticket.client_id == plan.duplicate.client_id)).scalar_one()
        + db.execute(select(func.count()).select_from(PendingTaskIntent).where(PendingTaskIntent.client_id == plan.duplicate.client_id)).scalar_one()
        + db.execute(select(func.count()).select_from(ClientProfile).where(ClientProfile.client_id == plan.duplicate.client_id)).scalar_one()
        + db.execute(select(func.count()).select_from(TelegramContact).where(TelegramContact.client_id == plan.duplicate.client_id)).scalar_one()
    )
    if remaining_refs == 0:
        duplicate_client = db.get(Client, plan.duplicate.client_id)
        if duplicate_client is not None:
            db.delete(duplicate_client)
    db.commit()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--external-user-id", required=True)
    parser.add_argument("--canonical-chat-id", required=True)
    parser.add_argument("--duplicate-chat-id", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.apply and args.dry_run:
        raise SystemExit("Use either --apply or --dry-run, not both")
    db = SessionLocal()
    try:
        plan = build_plan(db, args.external_user_id, args.canonical_chat_id, args.duplicate_chat_id)
        print_plan(plan)
        if not args.apply:
            print("mode=dry-run")
            return 0
        apply_plan(db, plan)
        print("mode=apply")
        print("applied=true")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
