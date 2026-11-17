from pathlib import Path
from unittest.mock import Mock

import pytest

from api.app.services import mass_notification_staging as staging
from api.app.services.telegram_bot import TelegramApiError


@pytest.fixture(autouse=True)
def telegram(monkeypatch):
    response = {"ok": True, "result": {"message_id": 321}}
    photo = Mock(return_value=response)
    document = Mock(return_value=response)
    monkeypatch.setattr(staging.settings, "telegram_staging_chat_id", "  synthetic-staging  ")
    monkeypatch.setattr(staging, "send_photo", photo)
    monkeypatch.setattr(staging, "send_document", document)
    return photo, document


@pytest.fixture
def local_file(tmp_path):
    path = tmp_path / "stored.bin"
    path.write_bytes(b"synthetic attachment")
    return path


@pytest.mark.parametrize(("filename", "mime_type", "expected"), [
    ("photo.jpg", "image/jpeg", "image/jpeg"),
    ("photo.JPEG", None, "image/jpeg"),
    ("photo.png", "IMAGE/PNG", "image/png"),
    ("photo.png", "application/octet-stream", "image/png"),
])
def test_stages_photo_once(local_file, telegram, filename, mime_type, expected):
    photo, document = telegram
    result = staging.stage_mass_notification_file(local_file, filename, mime_type)

    photo.assert_called_once_with(
        chat_id="synthetic-staging", content=b"synthetic attachment", filename=filename, mime_type=expected,
    )
    document.assert_not_called()
    assert result == {
        "staging_chat_id": "synthetic-staging",
        "message_id": 321,
        "media_type": "photo",
        "original_filename": filename,
        "file_size": len(b"synthetic attachment"),
    }
    assert local_file.is_file()


@pytest.mark.parametrize(("name", "mime_type", "clean_name"), [
    ("report.pdf", "application/pdf", "report.pdf"),
    ("dir/..\\report\r\n\x00.pdf", "application/pdf", "report.pdf"),
    ('C:\\dir\\report".pdf', "application/pdf", "report_.pdf"),
    ("preview.gif", "image/gif", "preview.gif"),
])
def test_stages_document_by_path_without_reading_it(monkeypatch, local_file, telegram, name, mime_type, clean_name):
    photo, document = telegram
    monkeypatch.setattr(Path, "read_bytes", Mock(side_effect=AssertionError("unexpected file read")))
    result = staging.stage_mass_notification_file(local_file, name, mime_type)

    document.assert_called_once_with(
        chat_id="synthetic-staging", file_path=local_file, filename=clean_name, mime_type=mime_type,
    )
    photo.assert_not_called()
    assert result == {
        "staging_chat_id": "synthetic-staging",
        "message_id": 321,
        "media_type": "document",
        "original_filename": clean_name,
        "file_size": len(b"synthetic attachment"),
    }
    assert local_file.is_file()


@pytest.mark.parametrize("chat_id", [None, "", " \t"])
def test_requires_staging_chat(monkeypatch, local_file, telegram, chat_id):
    monkeypatch.setattr(staging.settings, "telegram_staging_chat_id", chat_id)
    with pytest.raises(staging.MassNotificationStagingError, match="not configured"):
        staging.stage_mass_notification_file(local_file, "report.pdf", "application/pdf")
    for upload in telegram:
        upload.assert_not_called()


def test_rejects_empty_missing_and_non_regular_files(tmp_path, telegram):
    empty = tmp_path / "empty"
    empty.touch()
    for path, message in [(empty, "empty"), (tmp_path / "missing", "Cannot access"), (tmp_path, "regular file")]:
        with pytest.raises(staging.MassNotificationStagingError, match=message):
            staging.stage_mass_notification_file(path, "report.pdf", None)
    for upload in telegram:
        upload.assert_not_called()


@pytest.mark.parametrize(("filename", "mib"), [("photo.png", 10), ("report.pdf", 20)])
def test_rejects_oversize_before_reading_or_uploading(monkeypatch, local_file, telegram, filename, mib):
    with local_file.open("r+b") as file:
        file.truncate(mib * 1024 ** 2 + 1)
    monkeypatch.setattr(Path, "read_bytes", Mock(side_effect=AssertionError("unexpected file read")))
    with pytest.raises(staging.MassNotificationStagingError, match=f"{mib} MiB"):
        staging.stage_mass_notification_file(local_file, filename, None)
    for upload in telegram:
        upload.assert_not_called()


@pytest.mark.parametrize(("filename", "mime_type"), [
    ("photo.png", "image/jpeg"),
    ("photo.jpg", "application/pdf"),
    ("report.pdf", "application/pdf\r\nX-Injected: value"),
])
def test_rejects_invalid_mime_before_upload(local_file, telegram, filename, mime_type):
    with pytest.raises(staging.MassNotificationStagingError, match="MIME type"):
        staging.stage_mass_notification_file(local_file, filename, mime_type)
    for upload in telegram:
        upload.assert_not_called()


@pytest.mark.parametrize("response", [
    None, {}, {"result": {}}, {"result": []}, {"result": {"message_id": True}},
    {"result": {"message_id": "321"}}, {"result": {"message_id": 0}},
    {"result": {"message_id": -1}}, {"ok": False, "result": {"message_id": 321}},
])
def test_unconfirmed_message_is_an_error_without_retry(local_file, telegram, response):
    photo, document = telegram
    document.return_value = response
    with pytest.raises(staging.MassNotificationStagingError, match="message ID"):
        staging.stage_mass_notification_file(local_file, "report.pdf", None)
    document.assert_called_once()
    photo.assert_not_called()
    assert local_file.is_file()


@pytest.mark.parametrize("error", [TelegramApiError("unavailable"), TimeoutError("timeout")])
def test_telegram_error_is_propagated_without_retry(local_file, telegram, error):
    photo, document = telegram
    photo.side_effect = error
    with pytest.raises(type(error)) as exc:
        staging.stage_mass_notification_file(local_file, "photo.jpg", None)
    assert exc.value is error
    photo.assert_called_once()
    document.assert_not_called()
    assert local_file.is_file()
