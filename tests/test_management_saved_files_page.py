from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.app import main
from api.app.auth import CurrentUser
from api.app.db import get_db


ROOT = Path(__file__).resolve().parents[1]
PAGE_ROUTE = "/management/saved-files"


def page_client(monkeypatch, role):
    user = None if role is None else CurrentUser(
        id="user-1",
        role=role,
        real_user_id="user-1",
        real_role=role,
        is_real_admin=role == "admin",
    )
    monkeypatch.setattr(main, "_require_page_auth", lambda request, db: user)
    app = FastAPI()
    for route in main.app.routes:
        if route.path == PAGE_ROUTE:
            app.add_api_route(route.path, route.endpoint, methods=["GET"])
    app.dependency_overrides[get_db] = lambda: None
    return TestClient(app)


@pytest.mark.parametrize("role", ["admin", "agent", "trainee", "chiki_piki"])
def test_management_saved_files_page_is_admin_only(monkeypatch, role):
    response = page_client(monkeypatch, role).get(PAGE_ROUTE, follow_redirects=False)
    if role != "admin":
        assert response.status_code == 303
        assert response.headers["location"] == "/tickets"
        assert "/static/management_saved_files.js" not in response.text
        return
    assert response.status_code == 200
    assert 'data-shell-page="management-saved-files"' in response.text
    assert "/static/management_saved_files.js?v=20260930a" in response.text
    assert 'id="managementSavedFilesTableBody"' in response.text


def test_management_saved_files_page_redirects_anonymous_to_login(monkeypatch):
    response = page_client(monkeypatch, None).get(PAGE_ROUTE, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/management/saved-files"


def test_management_saved_files_navigation_contract():
    shell = (ROOT / "api/app/static/app_shell.js").read_text(encoding="utf-8")
    management = (ROOT / "api/app/templates/management.html").read_text(encoding="utf-8")
    assert '["/management/saved-files", { page: "management-saved-files", scripts: ["/static/management_saved_files.js?v=20260930a"] }]' in shell
    assert 'href="/management/saved-files">Сохранённые файлы</a>' in management


def test_management_saved_files_table_columns_and_default_disabled_actions():
    html = (ROOT / "api/app/templates/management_saved_files.html").read_text(encoding="utf-8")
    headers = ["Имя файла", "Размер", "Используется", "Где используется"]
    positions = [html.index(f"<th scope=\"col\">{header}</th>") for header in headers]
    assert positions == sorted(positions)
    assert 'id="managementSavedFilesDownloadBtn" class="action" type="button" disabled' in html
    assert 'id="managementSavedFilesRenameBtn" class="action" type="button" disabled' in html
    assert 'id="managementSavedFilesDeleteBtn" class="action danger" type="button" disabled' in html


def test_management_saved_files_js_contracts():
    source = (ROOT / "api/app/static/management_saved_files.js").read_text(encoding="utf-8")
    assert 'const API = "/api/v1/management/saved-files";' in source
    assert "state.selectedId = id;" in source
    assert "el.DownloadBtn.disabled = !hasSelection;" in source
    assert "el.RenameBtn.disabled = !hasSelection;" in source
    assert "el.DeleteBtn.disabled = !hasSelection || Boolean(item?.used);" in source
    assert "Файл используется: ${usageText(item)}. Удаление недоступно." in source
    assert 'data.append("file", file);' in source
    assert 'method: "POST"' in source
    assert 'method: "PATCH"' in source
    assert 'const link = document.createElement("a");' in source
    assert 'link.href = `${API}/${encodeURIComponent(item.id)}/download`;' in source
    assert 'link.download = item.display_filename || "";' in source
    assert "document.body.appendChild(link);" in source
    assert "link.click();" in source
    assert "link.remove();" in source
    assert "window.location.href" not in source
    assert 'method: "DELETE"' in source
    assert 'state.items = state.items.filter((value) => value.id !== item.id);' in source
    assert 'el.Empty.textContent = "Список пуст";' in source
    assert 'el.Empty.textContent = "Ошибка загрузки";' in source
