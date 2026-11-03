from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.app.auth import get_current_user
from api.app.db import get_db
from api.app.routers import settings_bot_button_items as items, settings_bot_button_structure as structure
from test_db_only_blockers import db, button


@pytest.mark.parametrize("workflow,kind", [("ticket", "terminal"), ("submenu", "section"), ("group_task", "group_parent")])
def test_saved_label_structure_reopen_and_reset_preserve_original_and_identity(db, monkeypatch, workflow, kind):
    monkeypatch.setattr(items, "_list_available_button_files", lambda: [])
    monkeypatch.setattr(items, "get_automation_registry", lambda: {})
    row = button(db, "Original", workflow=workflow, kind=kind)
    db.commit()
    identity = (row.id, row.button_key, row.button_path, row.parent_path)
    app = FastAPI()
    app.include_router(items.router)
    app.include_router(structure.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="admin", role="admin")
    params = {"button_key": row.button_key, "menu_type": row.menu_type, "workflow_type": workflow,
              "button_path": row.button_path, "button_label": row.button_label}
    with TestClient(app) as http:
        for renamed in ["New label", "Second label", None]:
            response = http.put("/api/v1/settings/bot-buttons/item/instruction", params=params,
                                json={"label_overridden": renamed is not None, "label_override": renamed})
            assert response.status_code == 200, response.text
            db.expire_all()
            item = http.get("/api/v1/settings/bot-buttons/item", params={"button_key": row.button_key, "menu_type": row.menu_type})
            assert item.status_code == 200, item.text
            effective = renamed or "Original"
            assert item.json()["button_label_effective"] == effective
            tree = http.get(f"/api/v1/settings/bot-buttons/menu-types/{row.menu_type}/structure")
            assert tree.status_code == 200, tree.text
            node = tree.json()["tree_rows"][0]["buttons"][0]
            assert node["label"] == effective
            assert (node["button_id"], node["technical_key"], node["full_path"], node["parent_path"]) == identity
            assert row.button_label == "Original"
            # Existing clients pass the displayed (effective) label on subsequent saves/reset.
            params["button_label"] = effective
