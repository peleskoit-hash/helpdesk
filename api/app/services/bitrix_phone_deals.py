"""Phone-to-deal queries; delivery and ticket lifecycle stay in telegram_bitrix_phone."""
import re

from ..config import settings
from .bitrix_client import BitrixApiError, BitrixLookupError, bitrix_get


def normalize_phone(body):
    lines = [line.strip() for line in (body or "").splitlines() if line.strip()]
    if len(lines) != 1 or not re.fullmatch(r"\+?[0-9\s().\-‐‑‒–—−/\\]+", lines[0]):
        return None
    digits = re.sub(r"[^0-9]", "", lines[0])
    if len(digits) < 11:
        return None
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    return "+" + digits


def _collect_ids(target, values, source):
    if not isinstance(values, list):
        raise BitrixApiError(f"{source}: invalid ID list")
    for value in values:
        value = str(value)
        if not re.fullmatch(r"[0-9]+", value) or int(value) <= 0:
            raise BitrixApiError(f"{source}: invalid ID")
        target.setdefault(value, None)


def _deal_pages(field, value):
    start = 0
    while True:
        payload = bitrix_get("crm.deal.list.json", {
            f"filter[{field}]": value, "select[0]": "ID", "select[1]": "CONTACT_ID",
            "select[2]": "ASSIGNED_BY_ID", "start": start,
        })
        deals = payload.get("result")
        if not isinstance(deals, list):
            raise BitrixApiError("crm.deal.list.json: invalid result")
        for deal in deals:
            deal_id = str(deal.get("ID", "")) if isinstance(deal, dict) else ""
            if not re.fullmatch(r"[0-9]+", deal_id) or int(deal_id) <= 0:
                raise BitrixApiError("crm.deal.list.json: invalid deal ID")
        yield deals
        next_page = payload.get("next")
        if next_page is None:
            return
        if not str(next_page).isdigit() or int(next_page) <= start:
            raise BitrixApiError("crm.deal.list.json: invalid next page")
        start = int(next_page)


def lookup(phone, progress):
    """Expand contacts through leads; only exact contact matches enter the result."""
    phone = normalize_phone(phone)
    if phone is None:
        raise BitrixLookupError("Invalid phone")
    variants = [phone]
    if len(phone) == 12 and phone.startswith("+7"):
        variants += [phone[1:], "8" + phone[2:]]
    contacts, leads = {}, {}
    for variant in variants:
        result = bitrix_get("crm.duplicate.findbycomm.json", {"type": "PHONE", "values[0]": variant}).get("result")
        # Bitrix may encode an empty associative result as [].
        if result == []:
            continue
        if not isinstance(result, dict):
            raise BitrixApiError("crm.duplicate.findbycomm.json: invalid result")
        _collect_ids(contacts, result.get("CONTACT", []), "CONTACT")
        _collect_ids(leads, result.get("LEAD", []), "LEAD")
    lead_deals = set()
    for lead in leads:
        for deals in _deal_pages("LEAD_ID", lead):
            for deal in deals:
                deal_id = str(deal["ID"])
                if deal_id in lead_deals:
                    continue
                lead_deals.add(deal_id)
                if deal.get("CONTACT_ID") and str(deal["CONTACT_ID"]) != "0":
                    _collect_ids(contacts, [deal["CONTACT_ID"]], "crm.deal.list.json CONTACT_ID")
                items = bitrix_get("crm.deal.contact.items.get.json", {"id": deal_id}).get("result")
                if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                    raise BitrixApiError("crm.deal.contact.items.get.json: invalid result")
                _collect_ids(contacts, [item.get("CONTACT_ID") for item in items], "crm.deal.contact.items.get.json")
    rows, seen, names = [], set(), {}
    for contact in contacts:
        for deals in _deal_pages("CONTACT_ID", contact):
            for deal in deals:
                deal_id = str(deal["ID"])
                if str(deal.get("CONTACT_ID")) == contact and deal_id not in seen:
                    seen.add(deal_id)
                    rows.append({"id": deal_id, "responsible_id": str(deal.get("ASSIGNED_BY_ID") or ""),
                                 "responsible": None, "complete": False})
            progress(rows)
    for row in rows:
        user_id = row["responsible_id"]
        if user_id not in names:
            names[user_id] = None
            if user_id.isdigit() and int(user_id) > 0 and settings.bitrix_deal_mark_webhook_base:
                try:
                    users = bitrix_get("user.get.json", {"FILTER[ID]": user_id},
                                       webhook_base=settings.bitrix_deal_mark_webhook_base).get("result")
                    if isinstance(users, list):
                        user = next((user for user in users if isinstance(user, dict) and str(user.get("ID")) == user_id), {})
                        names[user_id] = " ".join(str(user.get(field) or "").strip() for field in ("LAST_NAME", "NAME")).strip() or None
                except BitrixLookupError:
                    pass  # Preserve the deals and display the responsible ID instead.
        row["responsible"] = names[user_id]
        row["complete"] = bool(names[user_id])
        progress(rows)
    return rows
