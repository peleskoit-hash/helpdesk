from email.message import Message
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO
from threading import Thread
from urllib.error import URLError
from urllib.request import HTTPSHandler
from urllib.response import addinfourl

import pytest

from api.app.config import Settings, settings
from api.app.services import bitrix_client, telegram_bot


@pytest.mark.parametrize("proxy", [None, ""])
def test_unconfigured_proxy_preserves_module_urlopen(monkeypatch, proxy):
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.delenv("TELEGRAM_HTTP_PROXY", raising=False)
    assert Settings(_env_file=None).telegram_http_proxy is None
    monkeypatch.setattr(settings, "telegram_http_proxy", proxy)

    def direct_open(request, timeout):
        assert request.full_url == "https://api.telegram.org/botclient-test/getMe"
        assert timeout == 7
        return BytesIO(b'{"ok": true}')

    monkeypatch.setattr(telegram_bot, "urlopen", direct_open)
    assert telegram_bot.telegram_request("getMe", timeout=7, bot_token="client-test") == {"ok": True}


def test_configured_proxy_tunnels_both_bots_and_leaves_bitrix_direct(monkeypatch):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_CONNECT(self):
            requests.append(("CONNECT", self.path, self.headers.get("Proxy-Authorization")))
            self.send_error(407)

        def do_GET(self):
            requests.append(("GET", self.path, self.headers.get("Proxy-Authorization")))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"result": true}')

        def log_message(self, *args):
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            address = f"127.0.0.1:{server.server_port}"
            monkeypatch.setenv("NO_PROXY", "127.0.0.1")
            monkeypatch.setenv("TELEGRAM_HTTP_PROXY", f"http://user:password@{address}")
            monkeypatch.setattr(settings, "telegram_http_proxy", Settings(_env_file=None).telegram_http_proxy)

            with pytest.raises(telegram_bot.TelegramApiError, match="407"):
                telegram_bot.telegram_request("getMe", timeout=2, bot_token="client-test")
            with pytest.raises(URLError, match="407"):
                telegram_bot.get_updates(offset=None, timeout=1, bot_token="employee-test")
            assert bitrix_client.bitrix_get("test", {}, webhook_base=f"http://{address}") == {"result": True}
        finally:
            server.shutdown()
            thread.join()

    assert requests == [
        ("CONNECT", "api.telegram.org:443", "Basic dXNlcjpwYXNzd29yZA=="),
        ("CONNECT", "api.telegram.org:443", "Basic dXNlcjpwYXNzd29yZA=="),
        ("GET", "/test", None),
    ]


def test_invalid_proxy_error_omits_credentials(monkeypatch):
    monkeypatch.setattr(settings, "telegram_http_proxy", "http:/user:proxy-secret")
    with pytest.raises(telegram_bot.TelegramApiError, match="^<urlopen error Invalid TELEGRAM_HTTP_PROXY;"):
        telegram_bot.telegram_request("getMe", bot_token="client-test")


def test_proxy_credentials_are_not_forwarded_on_redirect():
    from urllib.request import Request
    from api.app.services.telegram_http import _TelegramRedirectHandler

    handler = _TelegramRedirectHandler()
    request = Request("https://api.telegram.org/file")
    request.add_header("Proxy-Authorization", "Basic synthetic")
    redirected = handler.redirect_request(request, None, 302, "Found", {}, "https://example.test/file")
    assert not redirected.has_header("Proxy-authorization")


@pytest.mark.parametrize("variable", ["NO_PROXY", "no_proxy"])
@pytest.mark.parametrize("bypass", ["api.telegram.org", "*"])
def test_configured_proxy_rejects_bypass_before_connecting(monkeypatch, variable, bypass):
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setenv(variable, bypass)
    monkeypatch.setattr(settings, "telegram_http_proxy", "http://user:proxy-secret@proxy.test:8080")

    def unexpected_connection(*args):
        pytest.fail("Telegram bypass reached the HTTPS transport")

    monkeypatch.setattr(HTTPSHandler, "https_open", unexpected_connection)
    with pytest.raises(telegram_bot.TelegramApiError) as error:
        telegram_bot.telegram_request("getMe", bot_token="bot-secret")

    assert str(error.value) == "<urlopen error TELEGRAM_HTTP_PROXY bypass blocked by proxy exclusions>"


def test_configured_proxy_rejects_bypass_after_https_redirect(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "redirect.test")
    monkeypatch.setattr(settings, "telegram_http_proxy", "http://user:proxy-secret@proxy.test:8080")

    def redirect_once(handler, request):
        assert request.host == "proxy.test:8080", "Redirect bypass reached the HTTPS transport"
        headers = Message()
        headers["Location"] = "https://redirect.test/botbot-secret/file"
        response = addinfourl(BytesIO(), headers, request.full_url, 302)
        response.msg = "Found"
        return response

    monkeypatch.setattr(HTTPSHandler, "https_open", redirect_once)
    with pytest.raises(URLError) as error:
        telegram_bot.get_updates(offset=None, timeout=1, bot_token="bot-secret")

    assert str(error.value) == "<urlopen error TELEGRAM_HTTP_PROXY bypass blocked by proxy exclusions>"
