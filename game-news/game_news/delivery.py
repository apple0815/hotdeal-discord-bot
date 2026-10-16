import json
import math
import re
import time
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from .core import now_kst, payload
from .net import HTTPFailure, NetworkError, request


class UncertainDelivery(RuntimeError):
    pass


def webhook_url(value):
    p = urlsplit(value)
    if p.scheme != "https" or p.hostname not in ("discord.com", "ptb.discord.com", "canary.discord.com") or p.port is not None or p.username or p.password or p.fragment or not re.fullmatch(r"/api(?:/v10)?/webhooks/\d+/[A-Za-z0-9_-]+", p.path):
        raise ValueError("유효한 Discord 웹훅 주소가 필요합니다")
    query = parse_qs(p.query)
    params = {"wait": "true"}
    if "thread_id" in query:
        thread = query["thread_id"][0]
        if not thread.isdigit():
            raise ValueError("thread_id 형식 오류")
        params["thread_id"] = thread
    if set(query) - {"wait", "thread_id"}:
        raise ValueError("지원하지 않는 웹훅 쿼리")
    return urlunsplit(("https", p.hostname, p.path, urlencode(params), ""))


def send_one(url, post, *, transport=request, sleep=time.sleep):
    for attempt in range(4):
        try:
            raw, _ = transport(url, method="POST", data=json.dumps(payload(post), ensure_ascii=False).encode(), headers={"Content-Type": "application/json", "User-Agent": "PubgEventReminder/1.0"}, redirects=False)
        except HTTPFailure as e:
            if e.status == 429 and attempt < 3:
                if not math.isfinite(e.retry_after) or e.retry_after > 60:
                    raise
                sleep(max(e.retry_after, 1) + 0.5)
                continue
            if e.status >= 500:
                raise UncertainDelivery("서버 오류로 전송 여부가 불확실합니다") from None
            raise
        except NetworkError:
            raise UncertainDelivery("연결 중단으로 전송 여부가 불확실합니다") from None
        try:
            message_id = json.loads(raw)["id"]
            if not isinstance(message_id, str) or not message_id.isdigit():
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise UncertainDelivery("Discord의 전송 확인 응답을 받지 못했습니다") from None
        return message_id
    raise RuntimeError("전송 재시도 초과")


def deliver(posts, store, url, *, sender=send_one, sleep=time.sleep):
    sent = 0
    for post in posts:
        if store.seen(post):
            continue
        # This must be durable before making the non-idempotent webhook call.
        store.mark(post, "sending", attempted_at=now_kst().isoformat())
        try:
            message_id = sender(url, post)
        except HTTPFailure:
            # Discord explicitly rejected the request, so next scan may retry.
            store.forget(post)
            raise
        except UncertainDelivery:
            store.mark(post, "uncertain", attempted_at=now_kst().isoformat())
            raise
        store.mark(post, "sent", message_id=message_id, sent_at=now_kst().isoformat())
        sent += 1
        sleep(2.0)
    return sent
