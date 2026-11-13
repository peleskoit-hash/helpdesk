from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

from ..config import settings


class _TelegramProxyHandler(ProxyHandler):
    def proxy_open(self, req, proxy, type):
        super().proxy_open(req, proxy, type)
        # urllib leaves the HTTPS request without a tunnel when proxy bypass applies.
        if not req._tunnel_host:
            raise URLError("TELEGRAM_HTTP_PROXY bypass blocked by proxy exclusions")


class _TelegramRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != "https":
            raise HTTPError(req.full_url, code, "Telegram redirect must use HTTPS", headers, fp)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected.remove_header("Proxy-authorization")
        return redirected


def open_telegram_url(direct_open, request, *, timeout):
    proxy = (settings.telegram_http_proxy or "").strip()
    if not proxy:
        return direct_open(request, timeout=timeout)

    try:
        parsed = urlsplit(proxy)
        port = parsed.port
        if (parsed.scheme != "http" or not parsed.hostname or port == 0
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ValueError
    except ValueError:
        raise URLError("Invalid TELEGRAM_HTTP_PROXY; expected an http:// proxy URL") from None

    opener = build_opener(_TelegramProxyHandler({"https": proxy}), _TelegramRedirectHandler())
    return opener.open(request, timeout=timeout)
