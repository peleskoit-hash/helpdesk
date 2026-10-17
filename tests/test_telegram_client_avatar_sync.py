from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import BotButtonSetting, ClientProfile, Company, TelegramContact, Ticket, TicketStatus
from api.app.routers import telegram
from api.app.services import telegram_client_avatar_sync as sync
from api.app.services import telegram_client_avatars as storage
from api.app.services.button_config import ButtonSpec


def _image(color="red"):
    output = BytesIO()
    Image.new("RGB", (600, 400), color).save(output, format="JPEG")
    return output.getvalue()


def _photos(unique_id="current"):
    return {"ok": True, "result": {"total_count": 2, "photos": [[
        {"file_id": "small", "file_unique_id": "small-unique", "width": 100, "height": 100},
        {"file_id": "large", "file_unique_id": unique_id, "width": 600, "height": 400},
    ], [{"file_id": "older", "file_unique_id": "older-unique", "width": 1000, "height": 1000}]]}}


def _payload(update_id=1, text="/start"):
    return {"update_id": update_id, "message": {"message_id": update_id,
            "chat": {"id": 123, "type": "private"},
            "from": {"id": 123, "first_name": "Client"}, "text": text}}


@pytest.fixture
def case(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    jobs = []
    sessions = []
    def separate_session():
        session = factory()
        sessions.append(session)
        return session
    monkeypatch.setattr(sync, "SessionLocal", separate_session)
    monkeypatch.setattr(sync, "_executor", SimpleNamespace(submit=lambda fn, *args: jobs.append((fn, args))))
    monkeypatch.setattr(sync.settings, "telegram_bot_token", "client-test-token")
    monkeypatch.setattr(sync.settings, "employee_telegram_bot_token", "employee-test-token")
    monkeypatch.setattr(storage.settings, "telegram_client_avatar_storage_dir", str(tmp_path / "avatars"))
    api = Mock(side_effect=lambda method, *args, **kwargs: _photos() if method == "getUserProfilePhotos" else {
        "ok": True, "result": {"file_path": "photos/profile.jpg"}})
    monkeypatch.setattr(sync, "telegram_request", api)
    content = _image()
    def response(*args, **kwargs):
        result = BytesIO(content)
        result.headers = {"Content-Length": str(len(content))}
        return result
    download = Mock(side_effect=response)
    monkeypatch.setattr(sync, "urlopen", download)
    with factory() as db:
        db.add(Company(id="company", name="Company"))
        db.commit()
        yield SimpleNamespace(db=db, factory=factory, jobs=jobs, sessions=sessions,
                              api=api, download=download, content=content,
                              directory=tmp_path / "avatars")
    engine.dispose()


def _run(case):
    while case.jobs:
        fn, args = case.jobs.pop(0)
        fn(*args)
    case.db.expire_all()


def _contact(case):
    return case.db.scalar(select(TelegramContact))


def _snapshot(case):
    contact = _contact(case)
    return contact.avatar_thumbnail, contact.avatar_file_unique_id, contact.avatar_path, contact.avatar_updated_at


def _close_and_start(case, update_id=2):
    ticket = case.db.scalar(select(Ticket).where(Ticket.status != TicketStatus.CLOSED.value))
    ticket.status = TicketStatus.CLOSED.value
    case.db.commit()
    return telegram.process_telegram_update(case.db, _payload(update_id))


def test_new_ticket_avatar_runs_only_after_commit_in_separate_session(case):
    result = telegram.process_telegram_update(case.db, _payload())
    assert result["ok"] and result["first_connection"]
    assert len(case.jobs) == 1
    case.api.assert_not_called()  # The webhook does not wait for Telegram/photo I/O.
    with case.factory() as reader:
        assert reader.get(Ticket, result["ticket_id"]) is not None
    _run(case)
    assert case.sessions and all(session is not case.db for session in case.sessions)
    assert [call.args[0] for call in case.api.call_args_list] == ["getUserProfilePhotos", "getFile"]
    first = case.api.call_args_list[0]
    assert first.args[1] == {"user_id": 123, "offset": 0, "limit": 1}
    assert case.api.call_args_list[1].args[1] == {"file_id": "large"}
    assert all(call.kwargs["bot_token"] == "client-test-token" for call in case.api.call_args_list)
    assert all(call.kwargs["timeout"] == 10 for call in case.api.call_args_list)
    request = case.download.call_args.args[0]
    assert request.full_url == "https://api.telegram.org/file/botclient-test-token/photos/profile.jpg"
    assert case.download.call_args.kwargs["timeout"] == 10
    thumbnail, unique_id, path, updated_at = _snapshot(case)
    assert unique_id == "current" and thumbnail and updated_at
    assert (case.directory / Path(path).name).read_bytes() == case.content


def test_new_ticket_unchanged_avatar_does_not_download(case):
    telegram.process_telegram_update(case.db, _payload())
    _run(case)
    before = _snapshot(case)
    case.api.reset_mock()
    case.download.reset_mock()
    _close_and_start(case)
    _run(case)
    assert [call.args[0] for call in case.api.call_args_list] == ["getUserProfilePhotos"]
    case.download.assert_not_called()
    assert _snapshot(case) == before


@pytest.mark.parametrize("missing", ["deleted", "missing_on_disk"])
def test_new_ticket_restores_unchanged_full_photo(case, missing):
    telegram.process_telegram_update(case.db, _payload())
    _run(case)
    before = _snapshot(case)
    if missing == "deleted":
        with case.factory() as db:
            storage.delete_full_avatar(db, _contact(case).id)
    else:
        (case.directory / Path(before[2]).name).unlink()
    _close_and_start(case)
    _run(case)
    after = _snapshot(case)
    assert (after[0], after[1], after[3]) == (before[0], before[1], before[3])
    assert (case.directory / Path(after[2]).name).read_bytes() == case.content
    assert len(list(case.directory.iterdir())) == 1


def test_new_ticket_with_changed_avatar_replaces_saved_photo(case):
    telegram.process_telegram_update(case.db, _payload())
    _run(case)
    before = _snapshot(case)
    case.api.side_effect = lambda method, *args, **kwargs: _photos("new-photo") if method == "getUserProfilePhotos" else {
        "ok": True, "result": {"file_path": "photos/new.jpg"}}
    new_content = _image("blue")
    def response(*args, **kwargs):
        result = BytesIO(new_content)
        result.headers = {}
        return result
    case.download.side_effect = response
    _close_and_start(case)
    _run(case)
    after = _snapshot(case)
    assert after[0] != before[0] and after[1] == "new-photo"
    assert not (case.directory / Path(before[2]).name).exists()
    assert (case.directory / Path(after[2]).name).read_bytes() == new_content
    assert len(list(case.directory.iterdir())) == 1


def test_no_telegram_photo_preserves_last_known_thumbnail_and_identity(case):
    telegram.process_telegram_update(case.db, _payload())
    _run(case)
    before = _snapshot(case)
    case.api.side_effect = None
    case.api.return_value = {"ok": True, "result": {"total_count": 0, "photos": []}}
    case.download.reset_mock()
    _close_and_start(case)
    _run(case)
    assert _snapshot(case) == before
    case.download.assert_not_called()


@pytest.mark.parametrize("failure", ["api", "timeout", "invalid_response", "invalid_image", "oversized"])
def test_avatar_failure_never_breaks_ticket_and_logs_no_details(case, caplog, failure):
    if failure == "api":
        case.api.side_effect = TimeoutError("client-test-token / private client data")
    elif failure == "timeout":
        case.download.side_effect = TimeoutError("client-test-token / private client data")
    elif failure == "invalid_response":
        case.api.side_effect = None
        case.api.return_value = {"ok": False, "description": "client-test-token private client data"}
    elif failure == "invalid_image":
        def invalid(*args, **kwargs):
            response = BytesIO(b"invalid image")
            response.headers = {}
            return response
        case.download.side_effect = invalid
    elif failure == "oversized":
        def oversized(*args, **kwargs):
            response = BytesIO(b"x")
            response.headers = {"Content-Length": str(sync.MAX_IMAGE_BYTES + 1)}
            return response
        case.download.side_effect = oversized
    result = telegram.process_telegram_update(case.db, _payload())
    _run(case)
    with case.factory() as reader:
        assert reader.get(Ticket, result["ticket_id"]) is not None
    assert _snapshot(case) == (None, None, None, None)
    assert "Telegram client avatar refresh failed" in caplog.text
    assert "client-test-token" not in caplog.text and "private client data" not in caplog.text
    assert not case.directory.exists()


def test_duplicate_event_and_existing_ticket_messages_do_not_recheck_avatar(case, monkeypatch):
    monkeypatch.setattr(telegram, "_button_from_callback", lambda *args: None)
    payload = _payload()
    first = telegram.process_telegram_update(case.db, payload)
    assert telegram.process_telegram_update(case.db, payload)["duplicate"]
    _run(case)
    case.api.reset_mock()
    second = telegram.process_telegram_update(case.db, _payload(2, "More details"))
    assert second["ticket_id"] == first["ticket_id"]
    telegram.process_telegram_update(case.db, _payload(3))
    assert not case.jobs
    case.api.assert_not_called()
    assert len(case.db.scalars(select(Ticket)).all()) == 1


def test_collecting_ticket_refresh_is_deduplicated_and_rollback_cancels_it(case):
    telegram.process_telegram_update(case.db, _payload())
    _run(case)
    contact = _contact(case)
    ticket = telegram._create_collecting_ticket(case.db, contact.company_id, contact.client_id)
    sync.schedule_avatar_refresh(case.db, ticket)
    assert not case.jobs
    case.db.commit()
    assert len(case.jobs) == 1
    case.jobs.clear()
    telegram._create_collecting_ticket(case.db, contact.company_id, contact.client_id)
    case.db.rollback()
    case.db.commit()
    assert not case.jobs


@pytest.mark.parametrize("kind", ["ticket", "task", "group_task", "auto"])
def test_new_client_callback_queues_avatar_regardless_of_workflow_before_confirmation(case, monkeypatch, kind):
    telegram.process_telegram_update(case.db, _payload())
    _run(case)
    _close_and_start(case)
    _run(case)
    for ticket in case.db.scalars(select(Ticket)):
        ticket.status = TicketStatus.CLOSED.value
    case.db.commit()
    button = ButtonSpec(key="test", label="Test", kind=kind)
    monkeypatch.setattr(telegram, "_button_from_callback", lambda *args: button)
    monkeypatch.setattr(telegram, "_is_callback_button_enabled", lambda *args, **kwargs: True)
    monkeypatch.setattr(telegram, "_resolve_effective_confirmation", lambda *args: (True, "Confirm"))
    payload = {"update_id": 3, "callback_query": {"id": "callback", "data": "btn:test",
               "from": {"id": 123}, "message": {"message_id": 3, "chat": {"id": 123, "type": "private"}}}}
    result = telegram.process_telegram_update(case.db, payload)
    assert result["awaiting_confirmation"]
    assert len(case.jobs) == 1


def test_background_submission_failure_does_not_undo_ticket(case, monkeypatch, caplog):
    def fail(*args):
        raise RuntimeError("client-test-token")
    monkeypatch.setattr(sync._executor, "submit", fail)
    result = telegram.process_telegram_update(case.db, _payload())
    with case.factory() as reader:
        assert reader.get(Ticket, result["ticket_id"]) is not None
    assert "could not be scheduled" in caplog.text
    assert "client-test-token" not in caplog.text


@pytest.mark.parametrize("failed", [False, True])
def test_worker_does_not_commit_or_rollback_unrelated_main_session_changes(case, failed):
    result = telegram.process_telegram_update(case.db, _payload())
    ticket = case.db.get(Ticket, result["ticket_id"])
    saved_subject = ticket.subject
    ticket.subject = "Uncommitted main transaction"
    if failed:
        case.api.side_effect = OSError("network failure")
    fn, args = case.jobs.pop()
    fn(*args)
    assert ticket in case.db.dirty
    assert ticket.subject == "Uncommitted main transaction"
    with case.factory() as reader:
        assert reader.get(Ticket, ticket.id).subject == saved_subject


def test_start_of_collecting_ticket_queues_one_refresh(case, monkeypatch):
    telegram.process_telegram_update(case.db, _payload())
    _run(case)
    profile = case.db.scalar(select(ClientProfile))
    if profile is None:
        profile = ClientProfile(id="profile", client_id=_contact(case).client_id)
        case.db.add(profile)
    profile.is_verified = True
    profile.destination = "Support"
    ticket = case.db.scalar(select(Ticket))
    ticket.status = TicketStatus.CLOSED.value
    case.db.commit()
    monkeypatch.setattr(telegram, "_send_problem_menu_for_contact", lambda *args: {"result": {"message_id": 10}})
    result = telegram.process_telegram_update(case.db, _payload(2), notify_client=True)
    assert result["command"] == "start"
    assert len(case.jobs) == 1
    case.jobs.clear()
    telegram.process_telegram_update(case.db, _payload(3), notify_client=True)
    assert not case.jobs


@pytest.mark.parametrize("kind,task_kind", [("task", "normal"), ("auto", "normal"),
                                           ("task", "group_parent"), ("ticket", "normal")])
def test_worker_refreshes_new_client_request_regardless_of_workflow(case, kind, task_kind):
    result = telegram.process_telegram_update(case.db, _payload())
    ticket = case.db.get(Ticket, result["ticket_id"])
    ticket.workflow_type = kind
    ticket.task_kind = task_kind
    case.db.commit()
    _run(case)
    assert case.api.call_args_list[0].args[0] == "getUserProfilePhotos"
    assert _contact(case).avatar_file_unique_id == "current"


@pytest.mark.parametrize("response", [None, [], {"ok": True, "result": []},
                                     {"ok": True, "result": {"total_count": 1, "photos": []}},
                                     {"ok": True, "result": {"total_count": "1", "photos": [[]]}}])
def test_invalid_telegram_responses_are_ignored(case, response):
    case.api.side_effect = None
    case.api.return_value = response
    telegram.process_telegram_update(case.db, _payload())
    _run(case)
    case.download.assert_not_called()
    assert _snapshot(case) == (None, None, None, None)


def test_streaming_download_enforces_limit_without_content_length(case, monkeypatch):
    monkeypatch.setattr(sync, "MAX_IMAGE_BYTES", 8)
    def oversized(*args, **kwargs):
        response = BytesIO(b"x" * 9)
        response.headers = {}
        return response
    case.download.side_effect = oversized
    with pytest.raises(ValueError, match="size limit"):
        sync._download("photos/avatar.jpg", "client-test-token")


@pytest.mark.parametrize("path", ["https://example.com/photo.jpg", "../photo.jpg", "/photo.jpg", "a/../photo.jpg"])
def test_download_rejects_untrusted_paths(case, path):
    with pytest.raises(ValueError):
        sync._download(path, "client-test-token")
    case.download.assert_not_called()


def test_employee_token_is_never_used_when_client_token_is_absent(case, monkeypatch):
    monkeypatch.setattr(sync.settings, "telegram_bot_token", None)
    result = telegram.process_telegram_update(case.db, _payload())
    sync._refresh(result["ticket_id"])
    assert not case.jobs
    case.api.assert_not_called()
    case.download.assert_not_called()


def test_download_deadline_is_enforced(case, monkeypatch):
    clock = iter([0, 21])
    monkeypatch.setattr(sync, "monotonic", lambda: next(clock))
    with pytest.raises(TimeoutError):
        sync._download("photos/avatar.jpg", "client-test-token")


def test_failed_new_avatar_preserves_previous_saved_data(case):
    telegram.process_telegram_update(case.db, _payload())
    _run(case)
    before = _snapshot(case)
    case.api.side_effect = OSError("network failure")
    _close_and_start(case)
    _run(case)
    assert _snapshot(case) == before
    assert (case.directory / Path(before[2]).name).read_bytes() == case.content


@pytest.mark.parametrize("kind", ["section", "group_parent"])
def test_new_menu_intake_queues_one_refresh_including_group_task_menu(case, monkeypatch, kind):
    telegram.process_telegram_update(case.db, _payload())
    _run(case)
    case.db.scalar(select(Ticket)).status = TicketStatus.CLOSED.value
    case.db.add(BotButtonSetting(id="menu", button_key="menu-key", menu_type="Test",
                                workflow_type="group_task" if kind == "group_parent" else "ticket",
                                button_path="Menu", button_label="Menu", button_kind=kind,
                                is_db_created=True, currently_present=True))
    case.db.commit()
    monkeypatch.setattr(telegram, "_menu_name_for_contact", lambda *args: "Test")
    monkeypatch.setattr(telegram, "_submenu_path_by_key_or_db", lambda *args: "")
    callback = {"id": "callback", "from": {"id": 123}, "data": "menu:menu-key",
                "message": {"message_id": 2, "chat": {"id": 123, "type": "private"}}}
    result = telegram.process_telegram_update(case.db, {"update_id": 2, "callback_query": callback})
    assert result["ok"]
    assert len(case.jobs) == 1
    assert telegram.process_telegram_update(case.db, {"update_id": 2, "callback_query": callback})["duplicate"]
    assert len(case.jobs) == 1


@pytest.mark.parametrize("kind", ["task", "group_task"])
def test_telegram_task_refreshes_once_and_group_children_do_not_refresh(case, monkeypatch, kind):
    telegram.process_telegram_update(case.db, _payload())
    _run(case)
    case.db.scalar(select(Ticket)).status = TicketStatus.CLOSED.value
    case.db.commit()
    case.api.reset_mock()
    children = (ButtonSpec(key="child-1", label="Child 1", kind="task"),
                ButtonSpec(key="child-2", label="Child 2", kind="task"))
    button = ButtonSpec(key="parent", label="Parent", kind=kind, subtasks=children)
    monkeypatch.setattr(telegram, "_button_from_callback", lambda *args: button)
    monkeypatch.setattr(telegram, "_is_callback_button_enabled", lambda *args, **kwargs: True)
    monkeypatch.setattr(telegram, "_resolve_effective_confirmation", lambda *args: (False, None))
    monkeypatch.setattr(telegram, "_resolve_instruction_button_key", lambda *args: None)
    monkeypatch.setattr(telegram, "_menu_name_for_ticket_client", lambda *args: "Test")
    monkeypatch.setattr(telegram, "choose_online_agent", lambda *args: None)
    callback = {"id": "callback", "data": "btn:parent", "from": {"id": 123},
                "message": {"message_id": 2, "chat": {"id": 123, "type": "private"}}}
    payload = {"update_id": 2, "callback_query": callback}
    result = telegram.process_telegram_update(case.db, payload)
    assert result["task_pending"]
    parent = case.db.get(Ticket, result["ticket_id"])
    assert parent.workflow_type == "task"
    assert parent.task_kind == ("group_parent" if kind == "group_task" else "normal")
    assert len(case.jobs) == 1
    _run(case)
    assert [call.args[0] for call in case.api.call_args_list] == ["getUserProfilePhotos"]

    telegram.process_telegram_update(case.db, _payload(3, "Task description"))
    if kind == "group_task":
        # The same creation service also runs later when an agent is assigned.
        telegram._create_group_children_from_collecting(case.db, parent, button, "Task description", None)
        case.db.commit()
        telegram._create_group_children_from_collecting(case.db, parent, button, "Task description", None)
        case.db.commit()
        assert len(case.db.scalars(select(Ticket).where(Ticket.parent_ticket_id == parent.id)).all()) == 2
    assert telegram.process_telegram_update(case.db, payload)["duplicate"]
    telegram.process_telegram_update(case.db, _payload(4, "More details"))
    assert not case.jobs
    assert case.api.call_count == 1


@pytest.mark.parametrize("workflow", ["task", "auto"])
def test_tasks_without_new_telegram_intake_never_schedule_refresh(case, workflow):
    telegram.process_telegram_update(case.db, _payload())
    _run(case)
    case.api.reset_mock()
    contact = _contact(case)
    case.db.add(Ticket(id="internal", company_id=contact.company_id, client_id=contact.client_id,
                       subject="Internal", workflow_type=workflow, task_kind="normal"))
    case.db.commit()
    assert not case.jobs
    case.api.assert_not_called()
