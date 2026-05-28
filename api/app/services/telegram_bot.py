import json
import logging
from pathlib import Path
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..config import settings
from .button_config import load_button_config
from .telegram_text_format import strip_telegram_html


logger = logging.getLogger(__name__)


TEST_TELEGRAM_CHAT_ID = "test-chat"
TEST_TELEGRAM_CALLBACK_PREFIX = "test-callback"


def is_test_chat_id(chat_id: str | int | None) -> bool:
    return str(chat_id or "") == TEST_TELEGRAM_CHAT_ID


def is_test_callback_query_id(callback_query_id: str | None) -> bool:
    return str(callback_query_id or "").startswith(TEST_TELEGRAM_CALLBACK_PREFIX)


def fake_telegram_response(method: str, payload: dict | None = None) -> dict:
    result = {
        "fake": True,
        "method": method,
        "payload": payload or {},
    }
    if method in {"sendMessage", "sendDocument", "sendPhoto"}:
        result["message_id"] = 1
    if method == "getFile":
        file_id = (payload or {}).get("file_id") or "test-file"
        result["file_id"] = file_id
        result["file_unique_id"] = f"{file_id}-unique"
        result["file_path"] = f"test/{file_id}.bin"
    return {
        "ok": True,
        "result": result,
    }


class TelegramApiError(RuntimeError):
    pass


def _api_url(method: str) -> str:
    if not settings.telegram_bot_token:
        raise TelegramApiError("Telegram bot token is not configured")
    return f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"


def telegram_request(method: str, payload: dict | None = None, timeout: int = 30) -> dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"

    request = Request(_api_url(method), data=data, headers=headers, method="POST" if payload is not None else "GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            details = json.loads(raw)
            description = details.get("description") or raw
        except json.JSONDecodeError:
            description = raw or str(exc)
        raise TelegramApiError(description) from exc
    except URLError as exc:
        raise TelegramApiError(str(exc)) from exc


def telegram_multipart_request(method: str, fields: dict, files: dict, timeout: int = 60) -> dict:
    boundary = f"----helpdesk-{uuid.uuid4().hex}"
    body = bytearray()

    def add_part(name: str, value: str) -> None:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    for key, value in fields.items():
        if value is not None:
            add_part(key, str(value))

    for key, file_info in files.items():
        filename = file_info["filename"]
        content_type = file_info.get("content_type") or "application/octet-stream"
        content = file_info["content"]
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            f'Content-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'.encode("utf-8")
        )
        body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
        body.extend(content)
        body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    request = Request(
        _api_url(method),
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            details = json.loads(raw)
            description = details.get("description") or raw
        except json.JSONDecodeError:
            description = raw or str(exc)
        raise TelegramApiError(description) from exc
    except URLError as exc:
        raise TelegramApiError(str(exc)) from exc


def get_updates(offset: int | None, timeout: int) -> list[dict]:
    query = {
        "timeout": timeout,
        "allowed_updates": json.dumps(["message", "edited_message", "callback_query"], ensure_ascii=False),
    }
    if offset is not None:
        query["offset"] = offset

    url = f"{_api_url('getUpdates')}?{urlencode(query)}"
    request = Request(url, method="GET")
    with urlopen(request, timeout=timeout + 10) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if not payload.get("ok"):
        return []
    return payload.get("result", [])


def set_bot_commands() -> dict:
    return telegram_request(
        "setMyCommands",
        payload={
            "commands": [
                {"command": "start", "description": "Новая заявка"},
                {"command": "close", "description": "Закрыть заявку"},
            ],
            "scope": {"type": "default"},
        },
    )


def problem_menu_markup(menu_name: str | None = None, menu_key: str | None = None) -> dict:
    return load_button_config(menu_name or "Руководство").menu_markup(menu_key)


def client_close_markup() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "Закрыть обращение", "callback_data": "client_close"}],
        ],
    }


def remove_inline_keyboard(chat_id: str, message_id: int) -> dict:
    if is_test_chat_id(chat_id):
        return fake_telegram_response(
            "editMessageReplyMarkup",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": {"inline_keyboard": []},
            },
        )
    return telegram_request(
        "editMessageReplyMarkup",
        payload={
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": {"inline_keyboard": []},
        },
    )


def answer_callback_query(callback_query_id: str, text: str | None = None) -> dict:
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    if is_test_callback_query_id(callback_query_id):
        return fake_telegram_response("answerCallbackQuery", payload)
    return telegram_request("answerCallbackQuery", payload=payload)


def send_message(
    chat_id: str,
    text: str,
    parse_mode: str | None = None,
    reply_markup: dict | None = None,
    remove_keyboard: bool = False,
    reply_to_message_id: str | int | None = None,
) -> dict:
    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_to_message_id is not None:
        payload["reply_parameters"] = {"message_id": int(reply_to_message_id)}
    if remove_keyboard:
        payload["reply_markup"] = {"remove_keyboard": True}
    elif reply_markup is not None:
        payload["reply_markup"] = reply_markup
    if is_test_chat_id(chat_id):
        return fake_telegram_response("sendMessage", payload)
    try:
        return telegram_request("sendMessage", payload=payload)
    except TelegramApiError as exc:
        if parse_mode == "HTML":
            logger.warning("Telegram rejected HTML message, falling back to plain text: %s", exc)
            fallback_payload = dict(payload)
            fallback_payload.pop("parse_mode", None)
            fallback_payload["text"] = strip_telegram_html(text)
            return telegram_request("sendMessage", payload=fallback_payload)
        raise


def edit_message_text(chat_id: str, message_id: str | int, text: str) -> dict:
    payload = {
        "chat_id": chat_id,
        "message_id": int(message_id),
        "text": text,
        "disable_web_page_preview": True,
    }
    if is_test_chat_id(chat_id):
        return fake_telegram_response("editMessageText", payload)
    return telegram_request("editMessageText", payload=payload)


def delete_message(chat_id: str, message_id: str | int) -> dict:
    payload = {"chat_id": chat_id, "message_id": int(message_id)}
    if is_test_chat_id(chat_id):
        return fake_telegram_response("deleteMessage", payload)
    return telegram_request("deleteMessage", payload=payload)


def send_document(
    chat_id: str,
    file_path: str | Path | bytes,
    filename: str | None = None,
    caption: str | None = None,
    mime_type: str | None = None,
) -> dict:
    binary_content: bytes | None = None
    if isinstance(file_path, (bytes, bytearray)):
        binary_content = bytes(file_path)
        path = Path(filename or "attachment")
    else:
        path = Path(file_path)
    payload = {"chat_id": chat_id}
    if caption:
        payload["caption"] = caption
    if is_test_chat_id(chat_id):
        return fake_telegram_response(
            "sendDocument",
            {
                **payload,
                "filename": filename or path.name,
                "mime_type": mime_type or "application/octet-stream",
            },
        )
    return telegram_multipart_request(
        "sendDocument",
        payload,
        {
            "document": {
                "filename": filename or path.name,
                "content_type": mime_type or "application/octet-stream",
                "content": binary_content if binary_content is not None else path.read_bytes(),
            }
        },
    )


def send_photo(
    chat_id: str,
    content: bytes,
    filename: str = "image.jpg",
    caption: str | None = None,
    mime_type: str | None = None,
) -> dict:
    payload = {"chat_id": chat_id}
    if caption:
        payload["caption"] = caption
    if is_test_chat_id(chat_id):
        return fake_telegram_response(
            "sendPhoto",
            {
                **payload,
                "filename": filename,
                "mime_type": mime_type or "image/jpeg",
            },
        )
    return telegram_multipart_request(
        "sendPhoto",
        payload,
        {
            "photo": {
                "filename": filename,
                "content_type": mime_type or "image/jpeg",
                "content": content,
            }
        },
    )


def get_file(file_id: str, chat_id: str | int | None = None) -> dict:
    payload = {"file_id": file_id}
    if is_test_chat_id(chat_id):
        return fake_telegram_response("getFile", payload)
    return telegram_request("getFile", payload=payload)


def download_file(file_path: str, destination_path: str | Path, chat_id: str | int | None = None) -> None:
    destination = Path(destination_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if is_test_chat_id(chat_id):
        destination.write_bytes(f"fake telegram file: {file_path}\n".encode("utf-8"))
        return

    if not settings.telegram_bot_token:
        raise TelegramApiError("Telegram bot token is not configured")
    url = f"https://api.telegram.org/file/bot{settings.telegram_bot_token}/{file_path}"
    request = Request(url, method="GET")
    try:
        with urlopen(request, timeout=60) as response:
            destination.write_bytes(response.read())
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise TelegramApiError(raw or str(exc)) from exc
    except URLError as exc:
        raise TelegramApiError(str(exc)) from exc

def remove_reply_keyboard(chat_id: str) -> dict:
    return fake_telegram_response("removeReplyKeyboardSkipped", {"chat_id": chat_id})


def send_problem_menu(
    chat_id: str,
    menu_key: str | None = None,
    text: str | None = None,
    menu_name: str | None = None,
) -> dict:
    config = load_button_config(menu_name or "Руководство")
    menu = config.menus[menu_key or config.root_menu_key]
    return send_message(chat_id, text or menu.text or "Выберите тип проблемы", reply_markup=config.menu_markup(menu.key))
