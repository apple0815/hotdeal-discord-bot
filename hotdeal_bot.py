"""Personal Korean deal notifier. Python 3.10+, no LLM/API key required."""
from __future__ import annotations

import argparse
import contextlib
import gzip
import io
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
UA = "HotdealNotifier/1.0 (personal deal feed reader)"
BASES = {"ppomppu": "https://www.ppomppu.co.kr", "quasarzone": "https://quasarzone.com"}
NAMES = {"ppomppu": "뽐뿌", "quasarzone": "퀘이사존"}
LOG = logging.getLogger("hotdeal")


class SourceError(Exception):
    def __init__(self, message, cooldown=3600):
        super().__init__(message)
        self.cooldown = cooldown


class DeliveryError(Exception):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


def clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def text_at(root, selector, default=""):
    node = root.select_one(selector)
    return clean(node.get_text(" ", strip=True)) if node else default


def count(value):
    match = re.search(r"(\d+(?:\.\d+)?)\s*([천만kK]?)", value.replace(",", ""))
    if not match:
        return None
    return int(float(match[1]) * {"": 1, "천": 1000, "만": 10000, "k": 1000, "K": 1000}[match[2]])


def parse_date(value, now):
    value = clean(value)
    if value in ("방금", "방금 전"):
        return now
    relative = re.fullmatch(r"(\d+)\s*(초|분|시간|일)\s*전", value)
    if relative:
        return now - timedelta(seconds=int(relative[1]) * {"초": 1, "분": 60, "시간": 3600, "일": 86400}[relative[2]])
    for fmt in ("%y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=KST)
        except ValueError:
            pass
    try:
        if re.fullmatch(r"\d{2}:\d{2}(?::\d{2})?", value):
            parts = [int(p) for p in value.split(":")]
            result = now.replace(hour=parts[0], minute=parts[1], second=parts[2] if len(parts) > 2 else 0, microsecond=0)
            return result - timedelta(days=1) if result > now + timedelta(minutes=2) else result
        if re.fullmatch(r"\d{2}\.\d{2}", value):
            result = datetime.strptime(f"{now.year}.{value}", "%Y.%m.%d").replace(tzinfo=KST)
            return result.replace(year=now.year - 1) if result > now else result
    except ValueError:
        pass
    return None


def ended(title):
    return bool(re.search(r"(?:^|[\[(（【\s])(?:종료|품절|판매완료|마감)(?:[\])）】\s]|$)", title))


@dataclass
class Deal:
    source: str
    post_id: str
    title: str
    url: str
    votes: int | None
    comments: int | None
    published: datetime | None
    downvotes: int | None = None
    category: str = ""
    price: str = ""
    shipping: str = ""
    expired: bool = False
    summary: str = ""

    @property
    def key(self):
        return f"{self.source}:{self.post_id}"


def parse_ppomppu(html, now):
    soup = BeautifulSoup(html, "html.parser")
    result = {}
    for row in soup.select("tr.baseList"):
        number = text_at(row, ".baseList-numb")
        anchor = row.select_one("a.baseList-title")
        if not number.isdigit() or not anchor:
            continue  # notices and sponsored rows
        query = parse_qs(urlsplit(anchor.get("href", "")).query)
        if query.get("id") != ["ppomppu"] or query.get("no") != [number]:
            continue
        title = clean(anchor.get_text(" ", strip=True))
        rec = row.select_one(".baseList-rec")
        rec_text = clean(rec.get_text()) if rec else ""
        nums = re.findall(r"\d[\d,]*", rec_text)
        up = int(nums[0].replace(",", "")) if nums else (0 if rec is not None and not rec_text else None)
        down = int(nums[1].replace(",", "")) if len(nums) > 1 else (0 if rec is not None and (not rec_text or rec_text == "0") else None)
        date_node = row.select_one("time.baseList-time")
        date_text = date_node.parent.get("title", "") or date_node.get_text() if date_node else ""
        price_match = re.search(r"\(([^()]*\d[\d,]*(?:원)?[^()]*)\)\s*$", title)
        price, shipping = "", ""
        if price_match:
            parts = re.split(r"/", price_match[1], maxsplit=1)
            price = parts[0].strip()
            shipping = parts[1].strip() if len(parts) == 2 else ""
        classes = set(anchor.get("class", [])) | set(row.get("class", []))
        expired = ended(title) or bool(classes & {"expire", "expired", "end", "over", "finish", "is-ended"})
        expired = expired or bool(row.select_one(".baseList-title del, .baseList-title strike, .baseList-title s"))
        expired = expired or "line-through" in anchor.get("style", "")
        result[number] = Deal("ppomppu", number, title, f"{BASES['ppomppu']}/zboard/view.php?id=ppomppu&no={number}", up,
                              count(text_at(row, ".baseList-c", "0")), parse_date(date_text, now), down,
                              text_at(row, ".baseList-small").strip("[]"), price, shipping, expired)
    if not result:
        raise SourceError("목록을 해석하지 못했습니다. 페이지 구조 또는 접근 제한 확인 필요")
    return list(result.values())


def parse_quasarzone(html, now):
    soup = BeautifulSoup(html, "html.parser")
    result = {}
    for row in soup.select(".v2-list-row--hotdeal"):
        anchor = row.select_one("a.subject-link")
        if not anchor:
            continue
        match = re.fullmatch(r"(?:https://quasarzone.com)?/bbs/qb_saleinfo/views/(\d+)(?:\?[^#]*)?", anchor.get("href", ""))
        if not match:
            continue  # partner deals and other boards
        post_id = match[1]
        title = clean(anchor.get_text(" ", strip=True))
        date_node = row.select_one(".v2-list-row__time")
        date_text = (date_node.get("title") or date_node.get_text()) if date_node else ""
        badges = [clean(n.get_text()) for n in row.select(".v2-badge")]
        classes = set(row.get("class", [])) | set(anchor.get("class", []))
        expired = ended(title) or any(b in ("종료", "품절", "마감") for b in badges)
        expired = expired or bool(classes & {"is-ended", "ended", "done", "end", "subject-end"})
        shipping = [clean(n.get_text(" ", strip=True)) for n in row.select(".v2-list-row__ship")]
        result[post_id] = Deal("quasarzone", post_id, title, f"{BASES['quasarzone']}/bbs/qb_saleinfo/views/{post_id}",
                              count(text_at(row, ".qc-count-good")), count(text_at(row, ".qc-count-comment")),
                              parse_date(date_text, now), None, badges[0] if badges else "",
                              text_at(row, ".v2-list-row__price"), " / ".join(s for s in shipping if s), expired)
    if not result:
        raise SourceError("목록을 해석하지 못했습니다. 페이지 구조 또는 접근 제한 확인 필요")
    return list(result.values())


def qualify(deal, config, now, first_run=False):
    if deal.expired or not deal.published or deal.votes is None or deal.comments is None:
        return False
    age = (now - deal.published).total_seconds()
    max_age = min(config["max_age_hours"] * 3600, config["first_run_age_minutes"] * 60) if first_run else config["max_age_hours"] * 3600
    if age < -120 or age > max_age:
        return False
    if any(k.casefold() in deal.title.casefold() for k in config["exclude_keywords"]):
        return False
    rule = config["sources"][deal.source]
    if deal.downvotes is not None:
        if deal.downvotes > rule["max_downvotes"] or deal.downvotes / max(1, deal.votes) > rule["max_downvote_ratio"]:
            return False
    # Ppomppu exposes downvotes: a missing/unparseable field must not look like zero.
    elif deal.source == "ppomppu":
        return False
    return deal.votes >= rule["min_votes"] or (deal.votes >= rule["min_votes_with_comments"] and deal.comments >= rule["min_comments"])


def enrich(deal, html):
    soup = BeautifulSoup(html, "html.parser")
    if deal.source == "ppomppu":
        # Some current pages repeat the class attribute; html.parser retains the last (han).
        body = soup.select_one(".board-contents, td.han")
        heading = soup.select_one("h1")
        for selector, field in (("#vote_list_btn_txt", "votes"), ("#vote_anti_list_btn_txt", "downvotes")):
            value = text_at(soup, selector)
            if value:
                setattr(deal, field, count(value))
    else:
        body = soup.select_one("#new_contents")
        heading = soup.select_one(".v2-view-head__title")
    if body is None or heading is None:
        raise SourceError("본문 또는 제목을 찾지 못해 전송을 보류했습니다")
    heading_text = clean(heading.get_text(" ", strip=True))
    deal.expired = deal.expired or ended(heading_text)
    if deal.expired:
        return deal
    for node in body.select("script, style, iframe, form, .advertisement, #div_together_goods-container"):
        node.decompose()
    lines = [clean(line) for line in body.get_text("\n", strip=True).splitlines()]
    lines = [line for line in lines if len(line) >= 5 and not re.match(r"https?://|안녕하세요|반갑습니다", line)]
    # Extractive summary: never invent discounts or silently calculate conditional prices.
    priority = [line for line in lines if re.search(r"쿠폰|카드|멤버십|회원|와우|배송|할인|적립|최소|선착순|조건|중복|결제|기간|까지|선물하기|무료", line)]
    selected = list(dict.fromkeys(priority + lines))[:3]
    deal.summary = "\n".join("• " + line[:160] for line in selected)[:480] or "이미지 위주 게시글입니다. 상세 조건은 원문에서 확인하세요."
    return deal


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urlsplit(req.full_url), urlsplit(newurl)
        if req.data is not None or new.scheme != "https" or new.hostname != old.hostname:
            raise SourceError("예상하지 않은 리디렉션을 중단했습니다")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def decode_html(raw, content_type=""):
    match = re.search(r"charset\s*=\s*['\"]?([\w-]+)", content_type, re.I)
    if not match:
        match = re.search(r"charset\s*=\s*['\"]?([\w-]+)", raw[:4096].decode("ascii", errors="ignore"), re.I)
    encoding = match[1] if match else "utf-8"
    if encoding.lower() in ("euc-kr", "ks_c_5601-1987"):
        encoding = "cp949"
    try:
        return raw.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return raw.decode("cp949" if encoding == "utf-8" else "utf-8", errors="replace")


class HttpClient:
    def __init__(self, delay=2):
        self.delay = delay
        self.last_request = {}
        self.robots = {}
        self.opener = build_opener(SafeRedirect())

    def raw_get(self, url):
        host = urlsplit(url).hostname
        time.sleep(max(0, self.delay - (time.monotonic() - self.last_request.get(host, 0))))
        self.last_request[host] = time.monotonic()
        try:
            # urllib defaults to Accept-Encoding: identity; Ppomppu rejects that
            # request. Advertise gzip honestly and decode it with a bounded reader.
            headers = {"User-Agent": UA, "Accept": "text/html,text/plain", "Accept-Encoding": "gzip"}
            with self.opener.open(Request(url, headers=headers), timeout=25) as response:
                raw = response.read(3_000_001)
                if len(raw) > 3_000_000:
                    raise SourceError("페이지 크기 제한을 초과했습니다")
                coding = response.headers.get("Content-Encoding", "identity").lower().strip()
                if coding == "gzip":
                    try:
                        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as decoded:
                            raw = decoded.read(3_000_001)
                    except (OSError, EOFError):
                        raise SourceError("gzip 응답을 해석하지 못했습니다", 600) from None
                elif coding not in ("identity", ""):
                    raise SourceError("지원하지 않는 응답 압축 방식입니다")
                if len(raw) > 3_000_000:
                    raise SourceError("압축 해제 후 페이지 크기 제한을 초과했습니다")
                return decode_html(raw, response.headers.get("Content-Type", ""))
        except HTTPError as exc:
            cooldown = 3600
            if exc.code == 429:
                try:
                    cooldown = max(3600, float(exc.headers.get("Retry-After", "3600")))
                except ValueError:
                    pass
            raise SourceError(f"HTTP {exc.code}: 수집 일시 중단", cooldown) from None
        except (URLError, OSError):
            raise SourceError("네트워크 연결 실패", 600) from None

    def get(self, source, url):
        base = BASES[source]
        if not url.startswith(base + "/"):
            raise SourceError("허용된 게시판 주소가 아닙니다")
        cached = self.robots.get(source)
        if not cached or time.time() - cached[0] > 86400:
            robot_url = base + "/robots.txt"
            parser = RobotFileParser(robot_url)
            try:
                robot_text = self.raw_get(robot_url)
            except SourceError as exc:
                if "HTTP 404:" not in str(exc):
                    raise
                robot_text = "User-agent: *\nAllow: /"
            if "<html" in robot_text.lower():
                raise SourceError("robots.txt 확인 실패")
            parser.parse(robot_text.splitlines())
            self.robots[source] = (time.time(), parser)
        if not self.robots[source][1].can_fetch(UA, url):
            raise SourceError("robots.txt에서 해당 경로 수집을 허용하지 않습니다", 86400)
        return self.raw_get(url)


def list_url(source, page):
    return (f"{BASES[source]}/zboard/zboard.php?id=ppomppu" + (f"&page={page}" if page > 1 else "") if source == "ppomppu"
            else f"{BASES[source]}/bbs/qb_saleinfo?page={page}")


def collect(source, config, client, now):
    result = {}
    parser = parse_ppomppu if source == "ppomppu" else parse_quasarzone
    for page in range(1, config["sources"][source]["pages"] + 1):
        try:
            deals = parser(client.get(source, list_url(source, page)), now)
        except SourceError as exc:
            if not result:
                raise
            return list(result.values()), exc
        fresh = [d for d in deals if d.key not in result]
        if not fresh:
            break
        result.update((d.key, d) for d in fresh)
        # Each cycle rescans recent pages, so a post may qualify after votes accumulate.
        dated = [d.published for d in deals if d.published]
        if dated and max(dated) < now - timedelta(hours=config["max_age_hours"]):
            break
    return list(result.values()), None


def discord_text(value, limit):
    value = re.sub(r"https?://\S+", "[링크는 원문 참조]", value)
    value = value.replace("@", "＠")
    value = re.sub(r"([\\`*_~|<>\[\]])", r"\\\1", value)
    return value[:limit]


def payload(deal):
    reaction = f"추천 {deal.votes} · 댓글 {deal.comments}"
    reaction += f" · 다른의견 {deal.downvotes}" if deal.downvotes is not None else " · 반대 수 미제공"
    fields = [
        {"name": "게시글 표기 가격", "value": discord_text(deal.price or "원문 확인", 200), "inline": True},
        {"name": "배송/이용 조건", "value": discord_text(deal.shipping or "원문 확인", 200), "inline": True},
        {"name": "커뮤니티 반응", "value": reaction, "inline": False},
    ]
    return {"username": "핫딜 알림", "allowed_mentions": {"parse": []}, "embeds": [{
        "title": discord_text(deal.title, 250), "url": deal.url,
        "description": discord_text(deal.summary, 700), "color": 0xF97316,
        "fields": fields, "timestamp": deal.published.isoformat(),
        "footer": {"text": f"{NAMES[deal.source]} · {deal.category} · 본문 발췌 요약 / 가격·조건은 원문 확인"[:200]}
    }]}


def validate_webhook(url):
    if not re.fullmatch(r"https://(?:canary\.|ptb\.)?discord\.com/api(?:/v\d+)?/webhooks/\d+/[\w-]+", url):
        raise ValueError("Discord 텍스트 채널 웹후크 URL 형식을 확인하세요")
    return url


class Discord:
    def __init__(self, url, opener=None, sleep=time.sleep):
        self.url = validate_webhook(url)
        self.opener = opener or build_opener(SafeRedirect())
        self.sleep = sleep

    def send(self, message):
        data = json.dumps(message, ensure_ascii=False).encode("utf-8")
        for attempt in range(3):
            try:
                request = Request(self.url + "?wait=true", data=data,
                                  headers={"Content-Type": "application/json", "User-Agent": UA}, method="POST")
                with self.opener.open(request, timeout=25) as response:
                    result = json.loads(response.read(65536))
                if not result.get("id"):
                    raise DeliveryError("Discord 전송 확인 응답 누락", uncertain=True)
                return str(result["id"])
            except HTTPError as exc:
                if exc.code == 429:
                    try:
                        wait = float(json.loads(exc.read(65536)).get("retry_after", 5))
                    except (ValueError, TypeError):
                        wait = 5
                    if attempt < 2 and math.isfinite(wait) and 0 <= wait <= 60:
                        self.sleep(wait + 0.1)
                        continue
                    raise DeliveryError("Discord 속도 제한: 다음 주기에 재시도") from None
                raise DeliveryError(f"Discord HTTP {exc.code}", uncertain=exc.code >= 500) from None
            except (URLError, OSError, ValueError, SourceError):
                # A POST timeout can happen after delivery. Do not blindly duplicate it.
                raise DeliveryError("전송 결과 불명: 채널을 확인한 뒤 수동 재시도 필요", uncertain=True) from None


class State:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS posts (key TEXT PRIMARY KEY, status TEXT, message_id TEXT, updated REAL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        self.db.execute("UPDATE posts SET status='unknown' WHERE status='sending'")
        self.db.commit()

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        if self.get(key) == value:
            return
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(value)))
        self.db.commit()

    def status(self, key):
        row = self.db.execute("SELECT status FROM posts WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def record(self, key, status, message_id=""):
        self.db.execute("INSERT OR REPLACE INTO posts VALUES (?,?,?,?)", (key, status, message_id, time.time()))
        self.db.commit()

    def record_many(self, keys, status):
        self.db.executemany("INSERT OR REPLACE INTO posts VALUES (?,?,?,?)", [(key,status,"",time.time()) for key in keys])
        self.db.commit()

    def close(self):
        self.db.close()


def cycle(config, state, client, discord=None, dry_run=False, now=None):
    now = now or datetime.now(KST)
    health = {"checked_at": now.isoformat(), "sources": {}, "sent": 0, "preview": []}
    candidates = []
    first_flags = {}
    successful_sources = []
    for source, rule in config["sources"].items():
        if not rule["enabled"]:
            continue
        first = not state.get(f"initialized:{source}", False)
        first_flags[source] = first
        retry_at = state.get(f"retry:{source}", 0)
        if now.timestamp() < retry_at:
            health["sources"][source] = {"status": "cooldown", "retry_at": datetime.fromtimestamp(retry_at, KST).isoformat()}
            continue
        try:
            deals, partial_error = collect(source, config, client, now)
            successful_sources.append(source)
            chosen = [d for d in deals if qualify(d, config, now, first) and state.status(d.key) not in ("sent", "sending", "unknown", "baseline")]
            candidates.extend(chosen)
            health["sources"][source] = {"status": "ok", "scanned": len(deals), "qualified": len(chosen)}
            if partial_error:
                health["sources"][source].update(status="partial", message=str(partial_error))
                # Do not issue detail requests after a source returns an access error.
                candidates = [d for d in candidates if d.source != source]
                if not dry_run:
                    state.set(f"retry:{source}", now.timestamp() + partial_error.cooldown)
            if not dry_run:
                # Do not gradually dump the startup backlog in later cycles.
                baseline = [d.key for d in deals if first and (not d.published or (now-d.published).total_seconds() > config["first_run_age_minutes"]*60) and state.status(d.key) is None]
                if baseline:
                    state.record_many(baseline, "baseline")
        except SourceError as exc:
            health["sources"][source] = {"status": "error", "message": str(exc)}
            LOG.warning("%s: %s", source, exc)
            if not dry_run:
                state.set(f"retry:{source}", now.timestamp() + exc.cooldown)
    # Newest first; a global cap limits channel flooding, including first startup.
    candidates.sort(key=lambda d: d.published, reverse=True)
    sent = 0
    first_sent = 0
    detail_blocked = set()
    for deal in candidates:
        if deal.source in detail_blocked:
            continue
        if sent >= config["max_messages_per_cycle"]:
            break
        if first_flags[deal.source] and first_sent >= config["max_first_run_messages"]:
            continue
        try:
            enrich(deal, client.get(deal.source, deal.url))
            if not qualify(deal, config, now, first_flags[deal.source]):
                continue
        except SourceError as exc:
            LOG.warning("%s 본문 확인 실패: %s", deal.key, exc)
            health["sources"][deal.source]["detail_error"] = str(exc)
            if not dry_run:
                state.set(f"retry:{deal.source}", now.timestamp() + exc.cooldown)
            # Stop this source for the cycle; keep the other source working.
            detail_blocked.add(deal.source)
            continue
        message = payload(deal)
        if dry_run:
            health["preview"].append(message)
        else:
            state.record(deal.key, "sending")
            try:
                message_id = discord.send(message)
                state.record(deal.key, "sent", message_id)
                LOG.info("전송 완료: %s", deal.key)
            except DeliveryError as exc:
                state.record(deal.key, "unknown" if exc.uncertain else "retry")
                health["delivery_error"] = str(exc)
                LOG.error("%s: %s", deal.key, exc)
                break
        sent += 1
        first_sent += int(first_flags[deal.source])
    if not dry_run:
        for source in successful_sources:
            state.set(f"initialized:{source}", True)
    health["sent"] = sent if not dry_run else 0
    LOG.info("수집 결과: %s / 전송 %d / 미리보기 %d", health["sources"], health["sent"], len(health["preview"]))
    return health


@contextlib.contextmanager
def process_lock(path):
    with open(path, "a+b") as handle:
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("이미 실행 중인 봇이 있습니다") from None
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def load_config(path):
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    bounds = {"interval_seconds": (300,86400), "request_delay_seconds": (1,60), "max_age_hours": (1,72),
              "first_run_age_minutes": (1,1440), "max_messages_per_cycle": (1,20), "max_first_run_messages": (1,5)}
    for key, (lo, hi) in bounds.items():
        value = config.get(key)
        if not isinstance(value, (int,float)) or isinstance(value,bool) or not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError(f"config.json: {key}는 {lo}~{hi} 값이어야 합니다")
    if not isinstance(config.get("exclude_keywords"),list) or not all(isinstance(k,str) and k for k in config["exclude_keywords"]):
        raise ValueError("exclude_keywords는 비어 있거나 비어 있지 않은 문자열 목록이어야 합니다")
    if not isinstance(config.get("sources"),dict) or not config["sources"]:
        raise ValueError("sources 설정 필요")
    for source, rule in config["sources"].items():
        if source not in BASES or not isinstance(rule.get("enabled"),bool):
            raise ValueError("알 수 없는 source 또는 enabled 설정")
        for key in ("pages", "min_votes", "min_votes_with_comments", "min_comments", "max_downvotes"):
            if type(rule.get(key)) is not int or rule[key] < (1 if key == "pages" else 0):
                raise ValueError(f"{source}.{key} 설정 오류")
        ratio = rule.get("max_downvote_ratio")
        if rule["pages"] > 20 or not isinstance(ratio,(int,float)) or isinstance(ratio,bool) or not math.isfinite(ratio) or not 0 <= ratio <= 1:
            raise ValueError(f"{source}: pages 또는 max_downvote_ratio 설정 오류")
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="미전송/DB 변경 없이 실제 수집 결과 확인")
    parser.add_argument("--test-webhook", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--retry-unknown", metavar="SOURCE:ID", help="채널에 미전송임을 확인한 한 건만 수동 재시도 허용")
    args = parser.parse_args()
    args.data_dir = args.data_dir.resolve()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args.data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[
        logging.StreamHandler(), RotatingFileHandler(args.data_dir / "bot.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")])
    try:
        config = load_config(args.config)
        if args.status:
            health_path = args.data_dir / "health.json"
            print(health_path.read_text(encoding="utf-8") if health_path.exists() else "아직 수집 기록이 없습니다")
            db_path = args.data_dir / "state.sqlite3"
            if db_path.exists():
                with sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True) as db:
                    rows = db.execute("SELECT key FROM posts WHERE status IN ('unknown','sending')").fetchall()
                    print("전송 확인 필요:", [row[0] for row in rows])
            return 0
        with process_lock(args.data_dir / "bot.lock"):
            path = args.data_dir / "state.sqlite3"
            state = State(":memory:" if args.dry_run else path)
            if args.dry_run and path.exists():
                with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as original:
                    original.backup(state.db)
            try:
                if args.retry_unknown:
                    if state.status(args.retry_unknown) != "unknown":
                        raise ValueError("전송 결과가 unknown인 게시글만 지정하세요")
                    state.record(args.retry_unknown, "retry")
                    print("다음 수집 주기에 해당 글이 보이고 기준을 충족하면 재시도합니다")
                    return 0
                webhook = None
                if not args.dry_run:
                    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
                    if not url:
                        raise ValueError("웹후크 미설정: Windows에서는 setup.cmd를 먼저 실행하세요")
                    webhook = Discord(url)
                if args.test_webhook:
                    if args.dry_run:
                        raise ValueError("--test-webhook과 --dry-run을 함께 사용할 수 없습니다")
                    webhook.send({"content": "✅ 핫딜 알림 연결 확인. 설정된 기준을 통과한 게시글을 10분마다 확인합니다.", "allowed_mentions": {"parse": []}})
                    print("웹후크 테스트 전송 성공")
                    return 0
                client = HttpClient(config["request_delay_seconds"])
                while True:
                    start = time.monotonic()
                    health = cycle(config, state, client, webhook, args.dry_run)
                    if args.dry_run:
                        print(json.dumps(health, ensure_ascii=False, indent=2))
                    else:
                        temp = args.data_dir / "health.tmp"
                        temp.write_text(json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")
                        temp.replace(args.data_dir / "health.json")
                    if args.once or args.dry_run:
                        return int(bool(health.get("delivery_error")) or any(v["status"] in ("error","partial","cooldown") or "detail_error" in v for v in health["sources"].values()))
                    time.sleep(max(1, config["interval_seconds"] - (time.monotonic() - start)))
            finally:
                state.close()
    except KeyboardInterrupt:
        LOG.info("사용자 요청으로 종료")
        return 0
    except (ValueError, RuntimeError, DeliveryError, OSError, sqlite3.Error) as exc:
        # Do not log raw request/exception objects: they can contain webhook credentials.
        LOG.error("%s", str(exc) if isinstance(exc,(ValueError,RuntimeError,DeliveryError)) else type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
