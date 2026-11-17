import pytest

from api.app.routers import mass_notifications as notifications
from test_mass_notifications_send_operation import ORIGIN, SEND, send_env, send_payload, state


def test_public_origin_allows_json_behind_reverse_proxy(send_env, monkeypatch):
    monkeypatch.setattr(notifications.settings, "app_public_base_url", ORIGIN + ":443/desk")
    response = send_env.client.post(SEND, json=send_payload(send_env), headers={"Origin": ORIGIN})

    assert response.status_code == 202
    operation_id = response.json()["operation_id"]
    assert response.json() == {"operation_id": operation_id, "status": "queued", "total_count": 3, "created": True}
    send_env.runner.assert_called_once_with(operation_id)


def test_public_origin_allows_multipart_and_files(send_env, monkeypatch):
    monkeypatch.setattr(notifications.settings, "app_public_base_url", ORIGIN + ":443/desk")
    payload = send_payload(send_env, filters={"destination": "Support"}, text="")
    payload["filters"] = '{"destination":"Support"}'
    response = send_env.client.post(
        SEND,
        data=payload,
        files=[("files", ("notice.txt", b"Document", "text/plain")), ("files", ("notice.png", b"Image", "image/png"))],
        headers={"Origin": ORIGIN},
    )

    assert response.status_code == 202
    operation = state(send_env)[0][0]
    assert [item["original_filename"] for item in operation.attachments_json] == ["notice.txt", "notice.png"]
    send_env.runner.assert_called_once_with(operation.id)


@pytest.mark.parametrize("origins", [
    [], ["null"], ["https://other.example.test"], [ORIGIN + "/path"], [ORIGIN, ORIGIN],
])
def test_rejects_untrusted_or_duplicate_origin_before_parsing(send_env, origins):
    headers = [
        ("X-Forwarded-Host", "other.example.test"),
        ("X-Forwarded-Proto", "https"),
        *(("Origin", origin) for origin in origins),
    ]
    response = send_env.client.post(SEND, json={}, headers=headers)

    assert response.status_code == 403
    assert response.json()["detail"]["error"]["code"] == "invalid_origin"
    assert state(send_env) == ([], [])
    send_env.runner.assert_not_called()


def test_non_admin_cannot_queue_an_operation(send_env):
    payload = send_payload(send_env)
    send_env.user.role = "agent"
    response = send_env.client.post(SEND, json=payload, headers={"Origin": ORIGIN})
    assert response.status_code == 403
    assert response.json()["detail"]["error"]["code"] == "forbidden"
    assert state(send_env) == ([], [])
    send_env.runner.assert_not_called()


@pytest.mark.parametrize(("mode", "public_url", "host", "status"), [
    ("dev", None, "localhost:8000", 202),
    ("dev", None, "other.example.test", 403),
    ("dev", "invalid-address", "localhost:8000", 403),
    ("prod", None, "localhost:8000", 403),
])
def test_only_local_dev_can_use_request_origin(send_env, monkeypatch, mode, public_url, host, status):
    payload = send_payload(send_env)
    monkeypatch.setattr(notifications.settings, "app_env", mode)
    monkeypatch.setattr(notifications.settings, "app_public_base_url", public_url)
    response = send_env.client.post(
        SEND, json=payload, headers={"Host": host, "Origin": f"http://{host}"},
    )

    assert response.status_code == status
    assert send_env.runner.call_count == (1 if status == 202 else 0)
