"""Conservative extraction from the official Korean PUBG website.

Only explicit PC date ranges inside the article body are accepted. Listing
header dates, console dates, sale periods and ambiguous times are excluded.
Unrecognized relevant articles are returned for review, never silently guessed.
"""
from datetime import datetime
import re
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .core import Event, KST, clean, digest

BASE = "https://pubg.com"
DATE = re.compile(r"(?<!\d)(?P<year>20\d{2}|\d{2})[/.-](?P<month>\d{1,2})[/.-](?P<day>\d{1,2})\s*[,，]?\s*(?P<hour>\d{1,2}):(?P<minute>\d{2})(?!\d)")
INTEREST = re.compile(r"특별 보급|이벤트|콜라보|PUBG\s*x\s*|패스|드롭스|쿠폰|시즌|상점|보상", re.I)
META = re.compile(r"^(?:미션|이벤트 안내|총 획득 가능한 보상|획득 가능한 보상|보상|유의사항|유의 사항|이벤트 기간|판매 기간|운영 기간|서비스 기간|교환 기간|보상 수령 기간|수령 기간|특수 제작 탭 운영 종료일)$")
PERIOD = re.compile(r"(?:기간|종료일|마감일)$")


def canonical(url):
    p = urlsplit(url)
    if p.scheme != "https" or p.hostname not in ("pubg.com", "www.pubg.com") or not re.fullmatch(r"/ko/(?:events/notice|news)/\d+", p.path):
        raise ValueError("PUBG 한국어 공식 공지 주소만 지원합니다")
    return urlunsplit(("https", "pubg.com", p.path, "", ""))


def discover(html, kind):
    """Read literal IDs from Nuxt SSR data; never execute website JavaScript."""
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(".post-contents__card")
    scripts = " ".join(s.get_text() for s in soup.select("script") if "window.__NUXT__=" in s.get_text())
    ids = list(dict.fromkeys(re.findall(r"\bpostId:(\d+)", scripts)))
    if not cards or len(cards) != len(ids):
        raise ValueError("공식 목록 구조가 변경됐거나 목록이 비었습니다")
    found = []
    for card, post_id in zip(cards, ids):
        title_node = card.select_one(".post__title")
        title = clean(title_node.get_text(" ", strip=True) if title_node else card.get_text(" ", strip=True))
        if kind == "news" and not INTEREST.search(title):
            continue
        # Some notices are explicitly console-only.
        if card.select(".label--console") and not card.select(".label--pc"):
            continue
        path = "events/notice" if kind == "events" else "news"
        found.append(f"{BASE}/ko/{path}/{post_id}")
    return found


def own_text(node):
    # Exclude nested list items so a parent does not repeat console/PC rows.
    clone = BeautifulSoup(str(node), "html.parser").find(node.name)
    if node.name == "li":
        for child in clone.find_all(["ul", "ol"]):
            child.decompose()
    elif node.name.startswith("h"):
        for child in clone.find_all(["p", "li", "ul", "ol", "table", "h1", "h2", "h3", "h4"]):
            child.decompose()
    return clean(clone.get_text(" ", strip=True))


def parse_article(html, url):
    url = canonical(url)
    soup = BeautifulSoup(html, "html.parser")
    article = soup.select_one(".content-template")
    title_node = soup.select_one(".detail-header__title")
    if article is None or title_node is None:
        raise ValueError("공식 공지 본문 구조를 찾을 수 없습니다")
    page_title = clean(title_node.get_text(" ", strip=True))
    header = soup.select_one(".detail-header")
    if header and "detail-header--console" in header.get("class", []) and "detail-header--pc" not in header.get("class", []):
        return [], []
    # Platform-specific campaign exceptions require separate adapters.
    if "PC방" in page_title:
        return [], [f"PC방별 참여 조건 확인 필요: {url}"]
    headings = {}
    label = ""
    missions, rewards = [], []
    events = {}
    warnings = []
    for node in article.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
        if node.name == "p" and node.find_parent("li"):
            continue
        text = own_text(node)
        if not text:
            continue
        if node.name.startswith("h"):
            # Some publisher paragraphs are mistakenly styled as long h2s.
            if len(text) > 90:
                continue
            if META.fullmatch(text) or PERIOD.search(text) and len(text) < 45:
                label = text
            else:
                level = int(node.name[1])
                headings = {k: v for k, v in headings.items() if k < level}
                headings[level] = text
                label = ""
                missions, rewards = [], []
            continue
        if META.fullmatch(text) or PERIOD.search(text) and len(text) < 45:
            label = text
            continue
        if not re.match(r"^PC\s*[:：]", text, re.I):
            if node.name == "li":
                if label == "미션":
                    missions.append(text)
                elif "보상" in label and "기간" not in label:
                    rewards.append(text)
            continue
        pc = re.split(r"Console\s*[:：]", text, flags=re.I)[0]
        context = " / ".join(headings.values()) or page_title
        if "판매" in label:
            continue
        if not label or not PERIOD.search(label):
            warnings.append(f"기간 종류 확인 필요 ({context}): {url}")
            continue
        if re.search(r"\b(?:UTC|PDT|PST)\b", pc, re.I):
            warnings.append(f"한국 시간 이외 시간대 확인 필요 ({context}): {url}")
            continue
        # Range end must be a complete date plus exact clock time.
        tail = re.search(r"\s[-–~]\s*(.+)$", pc)
        match = DATE.fullmatch(tail[1].strip().rstrip(".")) if tail else None
        if not match:
            warnings.append(f"정확한 종료 일시 확인 필요 ({context}): {url}")
            continue
        parts = {k: int(v) for k, v in match.groupdict().items()}
        if parts["year"] < 100:
            parts["year"] += 2000
        try:
            ending = datetime(**parts, tzinfo=KST)
        except ValueError:
            warnings.append(f"유효하지 않은 날짜 ({context}): {url}")
            continue
        if "특수 제작" in label or "특수 제작" in context:
            kind = "재료 사용·특수 제작 마감"
            action = "제작소의 해당 특수 제작 탭에서 남은 재료를 사용하세요. 재료별 사용 조건은 공식 공지를 확인하세요."
        elif "수령" in label:
            kind = "보상 수령 마감"
            action = "이벤트 페이지에서 달성한 보상의 수령 여부를 확인하세요."
        elif "교환" in label:
            kind = "보상 교환 마감"
            action = "이벤트 페이지에서 남은 포인트와 교환 가능한 보상을 확인하세요."
        else:
            kind = "이벤트 참여 마감"
            action = "로비 또는 공식 이벤트 페이지에서 남은 미션과 참여 조건을 확인하세요."
        title = context
        if "특별 보급" not in page_title and page_title not in title:
            title = f"{page_title} · {title}"
        condition_key = ""
        if missions and rewards:
            # Merge repeated monthly/collaboration notices only when their
            # section name, mission text and reward text all match exactly.
            short_name = list(headings.values())[-1] if headings else page_title
            condition_key = digest("|".join([short_name, *sorted(missions), "REWARDS", *sorted(rewards)]))
        event = Event(title, kind, ending, url, action, pc, condition_key)
        events[(event.identity, ending)] = event
    if not events and not warnings:
        warnings.append(f"자동 추출 가능한 PC 종료 일시 없음: {url}")
    return list(events.values()), list(dict.fromkeys(warnings))


def collect(client, config):
    urls = set(canonical(u) for u in config.get("extra_sources", []))
    errors, review = [], []
    for listing in config["listings"]:
        try:
            urls.update(discover(client.text(listing["url"]), listing["kind"]))
        except (RuntimeError, ValueError) as exc:
            errors.append(f"목록 수집 실패: {listing['url']} ({type(exc).__name__})")
    events = {}
    for url in sorted(urls):
        try:
            extracted, notes = parse_article(client.text(url), url)
            for event in extracted:
                events.setdefault((event.identity, event.deadline), event)
            review.extend(notes)
        except (RuntimeError, ValueError) as exc:
            errors.append(f"본문 수집 실패: {url} ({type(exc).__name__})")
    return list(events.values()), {"source_count": len(urls), "errors": errors, "review": sorted(set(review))}
