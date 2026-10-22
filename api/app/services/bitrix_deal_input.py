"""Strict deal ID/URL input shared by the two Telegram Bitrix automations."""
import re


def extract_deal_id(body):
    match = re.fullmatch(r"([0-9]+)|https://crm\.elitesochi\.com/crm/deal/(?:show|edit|details)/([0-9]+)/", body)
    value = (match.group(1) or match.group(2)) if match else None
    return value if value and value.strip("0") else None
