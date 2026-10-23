import uuid

import pytest
from sqlalchemy import select

from api.app.models import BotButtonSetting
from api.app.services.button_automation_types import get_automation_type_definitions, is_automation_allowed_for_workflow
from api.app.routers import telegram
from test_telegram_group_registry import case, register
from test_telegram_client_origin import flow, choose, incoming, set_workflow
from test_telegram_bitrix_phone import phone_flow
from test_telegram_phone_deals import search_flow
from test_telegram_group_copy import flow as group_flow, select_button, incoming as group_incoming


KEYS = ["bitrix_deal_phone_lookup", "bitrix_phone_deal_lookup", "client_origin", "telegram_group"]


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("workflow", ["ticket", "task", "auto"])
def test_registry_and_server_allow_only_auto(key, workflow):
    assert get_automation_type_definitions()[key]["workflow_types"] == ["auto"]
    assert is_automation_allowed_for_workflow(workflow, key) == (workflow == "auto")


@pytest.mark.parametrize("key", KEYS)
def test_switch_saved_button_to_auto_preserves_identity_and_settings(flow, key):
    f = flow
    set_workflow(f, "task")
    f.button.automation_key = key
    f.button.stable_button_id = str(uuid.uuid4())
    f.button.instruction_text = "Инструкция"
    f.button.client_text = "Текст клиенту"
    f.button.client_text_overridden = True
    f.button.files_json = ["saved.pdf"]
    if key == "telegram_group":
        f.button.telegram_group_id = register(f)["group_id"]
    f.db.commit()
    previous = f.button.button_key
    preserved = (f.button.id, f.button.stable_button_id, f.button.instruction_text, f.button.client_text,
                 f.button.files_json, f.button.telegram_group_id)
    body = {"automation_type": key, "instruction_text": "Инструкция", "client_text": "Текст клиенту",
            "client_text_overridden": True, "telegram_group_id": f.button.telegram_group_id}
    for rejected in ["task", "ticket"]:
        result = f.client.put("/api/v1/settings/bot-buttons/item/instruction",
            params={"button_key": previous, "menu_type": "Menu", "workflow_type": rejected,
                    "button_path": "Origin", "button_label": "Откуда клиент"}, json=body)
        assert result.status_code == 422, result.text
        assert result.json()["detail"]["error"]["code"] == "automation_not_allowed_for_workflow"
        f.db.refresh(f.button)
        assert f.button.automation_key == key and f.button.workflow_type == "task"
    body.pop("automation_type")  # A workflow-only edit must retain the saved automation.
    result = f.client.put("/api/v1/settings/bot-buttons/item/instruction",
        params={"button_key": previous, "menu_type": "Menu", "workflow_type": "auto",
                "button_path": "Origin", "button_label": "Откуда клиент"}, json=body)
    assert result.status_code == 200, result.text
    f.db.refresh(f.button)
    assert f.button.workflow_type == "auto" and f.button.automation_key == key
    assert preserved == (f.button.id, f.button.stable_button_id, f.button.instruction_text,
                         f.button.client_text, f.button.files_json, f.button.telegram_group_id)
    assert len(f.db.scalars(select(BotButtonSetting)).all()) == 1


@pytest.mark.parametrize("key", KEYS)
def test_real_auto_telegram_route_waits_then_completes_without_distribution(request, key):
    if key == "telegram_group":
        f = request.getfixturevalue("group_flow")
        assert select_button(f, "auto")["group_waiting"]
        payload = group_incoming(f)
    else:
        f = request.getfixturevalue("phone_flow" if key == "bitrix_deal_phone_lookup" else "search_flow" if key == "bitrix_phone_deal_lookup" else "flow")
        f.button.automation_key = key
        set_workflow(f, "auto")
        assert choose(f)["automation_collecting"]
        payload = incoming(f, "+79991234567" if key == "bitrix_phone_deal_lookup" else "2528563")
        f.http.assert_not_called()
    assert f.ticket.workflow_type == "auto" and f.ticket.intake_state == "collecting"
    assert f.ticket.status != "closed" and f.ticket.assignee_id is None
    result = telegram.process_telegram_update(f.db, payload, notify_client=True)
    if key == "telegram_group":
        assert result.get("closed") or result.get("automation_completed"), result
        assert f.ticket.status == "closed"
    else:
        assert not result.get("closed") and result["automation_collecting"]
        assert f.ticket.status != "closed"
    assert f.ticket.workflow_type == "auto" and f.ticket.assignee_id is None
    assert telegram.process_telegram_update(f.db, payload, notify_client=True)["duplicate"]
