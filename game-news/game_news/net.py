"""Bounded HTTP requests; never print URLs containing credentials."""
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NetworkError(RuntimeError):
    pass


class HTTPFailure(NetworkError):
    def __init__(self, status, retry_after=0):
        self.status, self.retry_after = status, retry_after
        super().__init__(f"HTTP {status}")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(url, *, data=None, headers=None, method=None, redirects=True):
    req = Request(url, data=data, headers=headers or {}, method=method)
    opener = build_opener() if redirects else build_opener(NoRedirect())
    try:
        with opener.open(req, timeout=25) as response:
            raw = response.read(8_000_001)
            if len(raw) > 8_000_000:
                raise NetworkError("응답 크기 초과")
            return raw, response.headers.get_content_charset() or "utf-8"
    except HTTPError as e:
        delay = 0
        if e.code == 429:
            try:
                delay = float(json.loads(e.read(4096)).get("retry_after", 0))
            except (ValueError, TypeError):
                pass
            try:
                delay = max(delay, float(e.headers.get("Retry-After", "0")))
            except ValueError:
                pass
        raise HTTPFailure(e.code, delay) from None
    except (URLError, TimeoutError, OSError):
        raise NetworkError("네트워크 연결 실패/시간 초과") from None


class Client:
    def __init__(self, delay=1.5, user_agent="PubgEventReminder/1.0 (official PUBG event deadlines)"):
        self.delay, self.user_agent = delay, user_agent
        self.last = 0.0

    def get(self, url, headers=None):
        time.sleep(max(0, self.last + self.delay - time.monotonic()))
        self.last = time.monotonic()
        return request(url, headers={"User-Agent": self.user_agent, **(headers or {})})

    def text(self, url, headers=None):
        raw, encoding = self.get(url, headers)
        return raw.decode(encoding, errors="replace")

    def json(self, url, headers=None):
        return json.loads(self.text(url, headers))
