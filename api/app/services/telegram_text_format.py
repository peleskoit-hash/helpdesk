from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

_ALLOWED_TAGS = {
    "b",
    "strong",
    "i",
    "em",
    "u",
    "ins",
    "s",
    "strike",
    "del",
    "tg-spoiler",
    "code",
    "pre",
    "blockquote",
    "a",
}

_TAG_NORMALIZE = {
    "strong": "b",
    "em": "i",
    "ins": "u",
    "strike": "s",
    "del": "s",
}

_URL_SCHEMES = {"http", "https", "tg", "mailto"}
_HAS_TAG_RE = re.compile(r"</?([a-zA-Z][a-zA-Z0-9\-]*)\b")
_STRIP_TAG_RE = re.compile(r"</?[^>]+>")


class TelegramHtmlValidationError(ValueError):
    pass


def _is_safe_href(value: str) -> bool:
    href = (value or "").strip()
    if not href:
        return False
    parsed = urlparse(href)
    if parsed.scheme:
        return parsed.scheme.lower() in _URL_SCHEMES
    return href.startswith(("http://", "https://"))


def has_telegram_html(text: str) -> bool:
    if not text:
        return False
    for match in _HAS_TAG_RE.finditer(text):
        tag = match.group(1).lower()
        if tag in _ALLOWED_TAGS:
            return True
    return False


def strip_telegram_html(text: str) -> str:
    if not text:
        return ""
    no_tags = _STRIP_TAG_RE.sub("", text)
    return html.unescape(no_tags)


class _TelegramHtmlSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self.stack: list[str] = []

    def _push_open(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = _TAG_NORMALIZE.get(tag, tag)
        if normalized == "a":
            href = ""
            for key, value in attrs:
                if (key or "").lower() == "href" and value is not None:
                    href = value
                    break
            if not _is_safe_href(href):
                self.stack.append("")
                return
            safe_href = html.escape(href, quote=True)
            self.parts.append(f'<a href="{safe_href}">')
            self.stack.append("a")
            return
        if normalized == "blockquote":
            expandable = any((key or "").lower() == "expandable" for key, _ in attrs)
            self.parts.append("<blockquote expandable>" if expandable else "<blockquote>")
            self.stack.append("blockquote")
            return
        self.parts.append(f"<{normalized}>")
        self.stack.append(normalized)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        raw = (tag or "").lower()
        if raw not in _ALLOWED_TAGS:
            self.stack.append("")
            return
        self._push_open(raw, attrs)

    def handle_endtag(self, tag: str) -> None:
        raw = (tag or "").lower()
        if not self.stack:
            return
        opened = self.stack.pop()
        if not opened:
            return
        expected = _TAG_NORMALIZE.get(raw, raw)
        if expected == "blockquote" and opened == "blockquote":
            self.parts.append("</blockquote>")
            return
        if expected == opened:
            self.parts.append(f"</{opened}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(html.escape(data or "", quote=False))

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def getvalue(self) -> str:
        while self.stack:
            opened = self.stack.pop()
            if opened:
                self.parts.append(f"</{opened}>")
        return "".join(self.parts)


def sanitize_telegram_html(text: str) -> str:
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    parser = _TelegramHtmlSanitizer()
    parser.feed(raw)
    parser.close()
    return parser.getvalue()


class _TelegramHtmlValidator(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.stack: list[tuple[str, str]] = []

    def _fail(self, message: str) -> None:
        raise TelegramHtmlValidationError(message)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        raw = (tag or "").lower()
        fragment = self.get_starttag_text() or f"<{raw}>"
        if raw not in _ALLOWED_TAGS:
            self._fail(f"Неподдерживаемый Telegram-тег {fragment}")
        normalized = _TAG_NORMALIZE.get(raw, raw)
        if normalized == "a":
            href_values = [value for key, value in attrs if (key or "").lower() == "href"]
            if len(href_values) != 1 or not _is_safe_href(href_values[0] or ""):
                self._fail(f"Некорректная ссылка в {fragment}")
            extra_attrs = [key for key, _ in attrs if (key or "").lower() != "href"]
            if extra_attrs:
                self._fail(f"Запрещённый атрибут в {fragment}")
        elif normalized == "blockquote":
            for key, value in attrs:
                if (key or "").lower() != "expandable" or value not in (None, "", "expandable"):
                    self._fail(f"Запрещённый атрибут в {fragment}")
        elif attrs:
            self._fail(f"Запрещённый атрибут в {fragment}")
        self.stack.append((normalized, fragment))

    def handle_endtag(self, tag: str) -> None:
        raw = (tag or "").lower()
        if raw not in _ALLOWED_TAGS:
            self._fail(f"Неподдерживаемый Telegram-тег </{raw}>")
        expected = _TAG_NORMALIZE.get(raw, raw)
        if not self.stack:
            self._fail(f"Закрывающий Telegram-тег </{raw}> без открывающего")
        opened, fragment = self.stack.pop()
        if opened != expected:
            self._fail(f"Некорректное пересечение Telegram-тегов: {fragment} закрывается </{raw}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        raw = (tag or "").lower()
        fragment = self.get_starttag_text() or f"<{raw}/>"
        self._fail(f"Самозакрывающийся Telegram-тег не поддерживается: {fragment}")

    def close(self) -> None:
        super().close()
        if self.stack:
            _opened, fragment = self.stack[-1]
            self._fail(f"Незакрытый Telegram-тег {fragment}")


def validate_telegram_html(text: str) -> None:
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    parser = _TelegramHtmlValidator()
    try:
        parser.feed(raw)
        parser.close()
    except TelegramHtmlValidationError:
        raise
    except Exception as exc:
        raise TelegramHtmlValidationError("Некорректная Telegram-разметка") from exc


def prepare_telegram_html_message(text: str) -> tuple[str, str | None]:
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not raw:
        return "", None
    if not has_telegram_html(raw):
        return raw, None
    safe = sanitize_telegram_html(raw)
    if not has_telegram_html(safe):
        return strip_telegram_html(safe), None
    return safe, "HTML"
