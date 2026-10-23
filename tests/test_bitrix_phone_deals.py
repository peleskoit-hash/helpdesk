from unittest.mock import Mock

import pytest

from api.app.services import bitrix_phone_deals as lookup
from api.app.services.bitrix_client import BitrixApiError


@pytest.mark.parametrize("body, expected", [
    ("8 (999) 123-45-67", "+79991234567"), ("7 999 123 45 67", "+79991234567"),
    ("+7(999)123-45-67", "+79991234567"), ("  +7.999.123–45–67  ", "+79991234567"),
    ("+123456789012", "+123456789012"), ("1234567890", None), ("", None),
    ("телефон 89991234567", None), ("89991234567\n89991234568", None), ("+7_encX", None),
])
def test_normalize_one_phone(body, expected):
    assert lookup.normalize_phone(body) == expected


def setup_api(monkeypatch, handler):
    monkeypatch.setattr(lookup.settings, "bitrix_deal_mark_webhook_base", "https://names.invalid/")
    api = Mock(side_effect=handler)
    monkeypatch.setattr(lookup, "bitrix_get", api)
    return api


def test_all_contacts_pages_deduplication_and_request_scoped_name_cache(monkeypatch):
    def handler(method, params, **kwargs):
        if method == "crm.duplicate.findbycomm.json":
            assert params["type"] == "PHONE" and params["values[0]"] in {"+79991234567", "79991234567", "89991234567"}
            return {"result": {"CONTACT": [714865, 714865, 2]}}
        if method == "user.get.json":
            assert kwargs == {"webhook_base": "https://names.invalid/"}
            assert params == {"FILTER[ID]": "11037"}
            return {"result": [{"ID": "11037", "NAME": "Иван", "LAST_NAME": "Иванов"}]}
        assert method == "crm.deal.list.json" and not kwargs
        assert [params[f"select[{i}]"] for i in range(3)] == ["ID", "CONTACT_ID", "ASSIGNED_BY_ID"]
        if params["filter[CONTACT_ID]"] == "714865":
            deal = "2415340" if params["start"] == 0 else "2555931"
            return {"result": [{"ID": deal, "CONTACT_ID": "714865", "ASSIGNED_BY_ID": "11037"}], **({"next": 50} if params["start"] == 0 else {})}
        return {"result": [{"ID": "2555931", "CONTACT_ID": "714865", "ASSIGNED_BY_ID": "11037"}, {"ID": "3", "CONTACT_ID": "2", "ASSIGNED_BY_ID": "11037"}]}
    api = setup_api(monkeypatch, handler)
    progress = Mock()
    rows = lookup.lookup("+79991234567", progress)
    assert [row["id"] for row in rows] == ["2415340", "2555931", "3"]
    assert all(row["responsible"] == "Иванов Иван" and row["complete"] for row in rows)
    assert sum(call.args[0] == "user.get.json" for call in api.call_args_list) == 1
    assert sum(call.args[0] == "crm.duplicate.findbycomm.json" for call in api.call_args_list) == 3
    lookup.lookup("+79991234567", progress)
    assert sum(call.args[0] == "user.get.json" for call in api.call_args_list) == 2


@pytest.mark.parametrize("failure", [BitrixApiError("401", http_status=401), BitrixApiError("500", http_status=500), None])
def test_unavailable_name_keeps_deal_and_responsible_id(monkeypatch, failure):
    def handler(method, params, **kwargs):
        if method == "crm.duplicate.findbycomm.json":
            return {"result": {"CONTACT": [1]}}
        if method == "crm.deal.list.json":
            return {"result": [{"ID": "2", "CONTACT_ID": "1", "ASSIGNED_BY_ID": "11037"}, {"ID": "3", "CONTACT_ID": "1", "ASSIGNED_BY_ID": "11037"}]}
        if failure:
            raise failure
        return {"result": []}
    api = setup_api(monkeypatch, handler)
    rows = lookup.lookup("+79991234567", Mock())
    assert [row["id"] for row in rows] == ["2", "3"]
    assert all(row["responsible_id"] == "11037" and not row["complete"] for row in rows)
    assert sum(call.args[0] == "user.get.json" for call in api.call_args_list) == 1


@pytest.mark.parametrize("result", [{}, {"CONTACT": []}, []])
def test_no_contact_means_no_deal_requests(monkeypatch, result):
    api = setup_api(monkeypatch, lambda *args, **kw: {"result": result})
    assert lookup.lookup("+79991234567", Mock()) == []
    assert api.call_count == 3


def test_page_failure_preserves_previously_processed_deals(monkeypatch):
    def handler(method, params, **kwargs):
        if method == "crm.duplicate.findbycomm.json":
            return {"result": {"CONTACT": [1]}}
        if params["start"] == 0:
            return {"result": [{"ID": "2", "CONTACT_ID": "1", "ASSIGNED_BY_ID": "3"}], "next": 50}
        raise BitrixApiError("offline")
    setup_api(monkeypatch, handler)
    saved = []
    with pytest.raises(BitrixApiError):
        lookup.lookup("+79991234567", lambda rows: saved.extend(dict(row) for row in rows))
    assert [row["id"] for row in saved] == ["2"]


def test_invalid_pagination_is_an_error_instead_of_an_infinite_loop(monkeypatch):
    setup_api(monkeypatch, lambda method, params, **kw: {"result": {"CONTACT": [1]}} if method.startswith("crm.duplicate") else {"result": [], "next": 0})
    with pytest.raises(BitrixApiError, match="next page"):
        lookup.lookup("+79991234567", Mock())


@pytest.mark.parametrize("source", ["contact", "lead", "historical_leads"])
def test_leads_expand_contacts_but_only_exact_contact_deals_enter_results(monkeypatch, source):
    def handler(method, params, **kwargs):
        if method == "crm.duplicate.findbycomm.json":
            if source == "contact":
                return {"result": {"CONTACT": [1]}}
            # Every variant is necessary: the first one has no matches.
            return {"result": {} if params["values[0]"].startswith("+") else {"LEAD": [10, 11]}}
        if method == "crm.deal.contact.items.get.json":
            assert params["id"] in {"100", "101"}
            return {"result": [{"CONTACT_ID": 2}, {"CONTACT_ID": 3}]} if source == "historical_leads" else {"result": []}
        if method == "user.get.json":
            return {"result": [{"ID": "11037", "NAME": "Иван", "LAST_NAME": "Иванов"}]}
        assert method == "crm.deal.list.json"
        if "filter[LEAD_ID]" in params:
            if source == "lead":
                return {"result": [{"ID": "100", "CONTACT_ID": "1"}]}
            # Repeated lead deals and a second page must not duplicate contact lookups.
            if params["filter[LEAD_ID]"] == "10" and params["start"] == 0:
                return {"result": [{"ID": "100", "CONTACT_ID": "1"}], "next": 50}
            return {"result": [{"ID": "101", "CONTACT_ID": "2"}, {"ID": "100", "CONTACT_ID": "1"}]}
        contact = params["filter[CONTACT_ID]"]
        own = {"1": [{"ID": "201", "CONTACT_ID": 1}],
               "2": [{"ID": "202", "CONTACT_ID": 2}, {"ID": "203", "CONTACT_ID": 2}],
               "3": [{"ID": "303", "CONTACT_ID": 3}]}[contact]
        # Bitrix can also return historically associated deals or omit CONTACT_ID.
        foreign = [{"ID": "100", "CONTACT_ID": 999}, {"ID": "101", "CONTACT_ID": 999},
                   {"ID": "999"}, {"ID": "201", "CONTACT_ID": 1}]
        return {"result": [{**deal, "ASSIGNED_BY_ID": 11037} for deal in own + foreign]}
    api = setup_api(monkeypatch, handler)
    rows = lookup.lookup("8 (999) 123-45-67", Mock())
    assert [row["id"] for row in rows] == (["201", "202", "203", "303"] if source == "historical_leads" else ["201"])
    assert all(row["responsible"] == "Иванов Иван" for row in rows)
    assert sum(call.args[0] == "user.get.json" for call in api.call_args_list) == 1
    variants = [call.args[1]["values[0]"] for call in api.call_args_list if call.args[0] == "crm.duplicate.findbycomm.json"]
    assert variants == ["+79991234567", "79991234567", "89991234567"]
    extra_ids = [call.args[1]["id"] for call in api.call_args_list if call.args[0] == "crm.deal.contact.items.get.json"]
    assert len(extra_ids) == len(set(extra_ids))
    contacts = [call.args[1]["filter[CONTACT_ID]"] for call in api.call_args_list
                if call.args[0] == "crm.deal.list.json" and "filter[CONTACT_ID]" in call.args[1]]
    assert contacts == (["1", "2", "3"] if source == "historical_leads" else ["1"])


@pytest.mark.parametrize("stage", ["duplicates", "lead_deals", "extra_contacts", "contact_deals"])
def test_discovery_errors_propagate_to_existing_safe_fallback(monkeypatch, stage):
    def handler(method, params, **kwargs):
        if method == "crm.duplicate.findbycomm.json":
            if stage == "duplicates":
                raise BitrixApiError("unavailable", http_status=500)
            return {"result": {"LEAD": [10]}}
        if method == "crm.deal.contact.items.get.json":
            if stage == "extra_contacts":
                raise BitrixApiError("unavailable", http_status=401)
            return {"result": []}
        if "filter[LEAD_ID]" in params:
            if stage == "lead_deals":
                raise BitrixApiError("unavailable", http_status=500)
            return {"result": [{"ID": "100", "CONTACT_ID": "1"}]}
        raise BitrixApiError("unavailable", http_status=500)
    setup_api(monkeypatch, handler)
    with pytest.raises(BitrixApiError):
        lookup.lookup("+79991234567", Mock())


def test_leads_without_deals_and_foreign_contacts_return_no_results(monkeypatch):
    def handler(method, params, **kwargs):
        if method == "crm.duplicate.findbycomm.json":
            return {"result": {"LEAD": [10], "CONTACT": [1]}}
        return {"result": []} if "filter[LEAD_ID]" in params else {"result": [{"ID": "100", "CONTACT_ID": 2}]}
    api = setup_api(monkeypatch, handler)
    assert lookup.lookup("+79991234567", Mock()) == []
    assert not any(call.args[0] == "user.get.json" for call in api.call_args_list)
