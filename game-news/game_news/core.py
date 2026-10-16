from dataclasses import dataclass
from hashlib import sha256
import re

from pubg_events.core import KST, now_kst


def digest(text):
    return sha256(text.encode()).hexdigest()[:24]


def clean(text):
    return re.sub(r"\s+", " ", text).strip()


def safe(text, limit=900):
    text = clean(text).replace("@", "＠")
    text = re.sub(r"([\\`*_~|<>])", r"\\\1", text)
    return text if len(text) <= limit else text[:limit-1] + "…"


@dataclass(frozen=True)
class Post:
    key: str
    url: str
    source: str
    body: dict
    app_ids: tuple = ()

    @property
    def fingerprint(self):
        return self.key


def payload(post):
    result = dict(post.body)
    result["username"] = "겜방 게임 소식"
    result["allowed_mentions"] = {"parse": []}
    return result
