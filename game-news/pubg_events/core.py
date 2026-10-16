from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import re

KST = timezone(timedelta(hours=9))
STAGES = (7, 3, 1)


def now_kst():
    return datetime.now(KST)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def clean(value):
    return re.sub(r"\s+", " ", value).strip()


def safe_text(value):
    return re.sub(r"([\\`*_~|<>\[\]])", r"\\\1", clean(value))


@dataclass(frozen=True)
class Event:
    title: str
    kind: str
    deadline: datetime
    url: str
    action: str
    evidence: str
    condition_key: str = ""

    def __post_init__(self):
        if self.deadline.tzinfo is None:
            raise ValueError("종료 시각에 시간대가 필요합니다")

    @property
    def identity(self):
        if self.condition_key:
            return digest(f"{self.condition_key}|{self.kind}")
        return digest(f"{self.url}|{self.title}|{self.kind}")

    def record(self):
        return {**asdict(self), "deadline": self.deadline.isoformat()}


@dataclass(frozen=True)
class Reminder:
    event: Event
    days: int

    @property
    def key(self):
        return digest(f"{self.event.identity}|{self.event.deadline.isoformat()}|D-{self.days}")

    @property
    def fingerprint(self):
        return self.key

    @property
    def url(self):
        return self.event.url

    @property
    def source(self):
        return "pubg-official-events"


def due(events, now, *, hour=20, respect_hour=True):
    """Korean calendar D-7/D-3/D-1; never backfill missed stages."""
    now = now.astimezone(KST)
    if respect_hour and now.hour < hour:
        return []
    result = {}
    for event in events:
        if event.deadline <= now:
            continue
        days = (event.deadline.astimezone(KST).date() - now.date()).days
        if days in STAGES:
            reminder = Reminder(event, days)
            result[reminder.key] = reminder
    return sorted(result.values(), key=lambda r: (r.event.deadline, r.event.title))


def payload(reminder):
    event = reminder.event
    ending = event.deadline.astimezone(KST)
    return {
        "username": "게임 소식 알리미",
        "allowed_mentions": {"parse": []},
        "embeds": [{
            "title": f"⏰ [배그 PC · D-{reminder.days}] {safe_text(event.title)}"[:256],
            "url": event.url,
            "color": {7: 0xF2AA00, 3: 0xF07C22, 1: 0xE74C3C}[reminder.days],
            "description": safe_text(event.action)[:1200],
            "fields": [
                {"name": "마감", "value": ending.strftime("%Y년 %m월 %d일 %H:%M · 한국 시간"), "inline": False},
                {"name": "구분", "value": safe_text(event.kind), "inline": True},
                {"name": "대상", "value": "PC · 플랫폼별 참여 조건은 원문 확인", "inline": True},
            ],
            "footer": {"text": "공식 공지 기준 · D-7 / D-3 / D-1 알림 · 참여·보상 조건은 원문 확인"},
        }],
    }
