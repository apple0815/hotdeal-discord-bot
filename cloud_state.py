"""Durable, optimistic-concurrency state on a dedicated GitHub branch.

Persist 'sending' BEFORE Discord. A killed job therefore leaves an uncertain
record instead of re-posting the same deal on the next ephemeral runner.
No webhook, channel ID or Discord message ID is stored in the public branch.
"""
import base64
import copy
import json
import math
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, build_opener, HTTPRedirectHandler


class StateError(RuntimeError):
    pass


def empty_state():
    return {"version": 1, "posts": {}, "meta": {}}


def validate_state(data):
    if not isinstance(data, dict) or set(data) != {"version", "posts", "meta"} or data["version"] != 1:
        raise StateError("원격 전송 기록 형식 오류: 초기화하지 않고 중단합니다")
    if not isinstance(data["posts"],dict) or not isinstance(data["meta"],dict):
        raise StateError("원격 전송 기록 손상")
    for key, value in data["posts"].items():
        if not re.fullmatch(r"(?:ppomppu|quasarzone):\d+", key) or not isinstance(value,dict):
            raise StateError("원격 게시글 기록 형식 오류")
        if set(value) != {"status", "updated"} or value["status"] not in {"sent", "sending", "unknown", "retry", "baseline"}:
            raise StateError("원격 전송 상태 형식 오류")
        if type(value["updated"]) not in (int,float) or not math.isfinite(value["updated"]):
            raise StateError("원격 전송 시각 형식 오류")
    for key, value in data["meta"].items():
        if re.fullmatch(r"initialized:(?:ppomppu|quasarzone)",key):
            valid = type(value) is bool
        elif re.fullmatch(r"retry:(?:ppomppu|quasarzone)",key):
            valid = type(value) in (int,float) and math.isfinite(value)
        else:
            valid = False
        if not valid:
            raise StateError("원격 메타데이터 형식 오류")
    return data


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise StateError("GitHub API 리디렉션을 허용하지 않습니다")


class GitHubStore:
    def __init__(self, repository, token, branch="hotdeal-state", opener=None):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise StateError("GITHUB_REPOSITORY 설정 오류")
        if not token:
            raise StateError("GitHub Actions GITHUB_TOKEN이 필요합니다")
        if branch != "hotdeal-state":
            raise StateError("상태 기록은 hotdeal-state 브랜치에만 저장합니다")
        self.base = "https://api.github.com/repos/" + repository
        self.token = token
        self.branch = branch
        self.opener = opener or build_opener(NoRedirect())

    @classmethod
    def from_environment(cls):
        return cls(os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_TOKEN", ""))

    def api(self, method, path, body=None, allow_missing=False):
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        req = Request(self.base + path, data=payload, method=method, headers={
            "Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
            "Content-Type": "application/json", "User-Agent": "HotdealNotifier/1.0",
            "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with self.opener.open(req,timeout=25) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise StateError("GitHub 상태 응답 크기 초과")
                return json.loads(raw)
        except HTTPError as exc:
            if exc.code == 404 and allow_missing:
                return None
            raise StateError(f"GitHub 상태 저장 API HTTP {exc.code}: 중복 방지를 위해 전송을 중단합니다") from None
        except (URLError,OSError,ValueError):
            # A timed-out PUT may have succeeded. Never guess or overwrite a newer SHA.
            raise StateError("GitHub 상태 확인/저장 실패: 전송을 중단하고 다음 실행에서 다시 읽습니다") from None

    def load(self, allow_missing=False):
        result = self.api("GET", "/contents/state.json?ref=" + quote(self.branch), allow_missing=allow_missing)
        if result is None:
            return None
        try:
            if result["encoding"] != "base64" or not result.get("sha"):
                raise ValueError()
            raw = base64.b64decode(result["content"])
            return validate_state(json.loads(raw)), result["sha"]
        except (KeyError,TypeError,ValueError):
            raise StateError("원격 전송 기록을 읽지 못했습니다. 새 기록으로 대체하지 않습니다") from None

    def save(self, data, sha):
        raw = json.dumps(validate_state(data),ensure_ascii=False,sort_keys=True,separators=(",", ":")).encode("utf-8")
        if len(raw) > 900_000:
            raise StateError("상태 기록 크기 제한 초과")
        body = {"message": "Update hotdeal delivery state", "branch": self.branch,
                "content": base64.b64encode(raw).decode("ascii")}
        if sha:
            body["sha"] = sha
        result = self.api("PUT", "/contents/state.json", body)
        try:
            return result["content"]["sha"]
        except (KeyError,TypeError):
            raise StateError("원격 저장 확인 응답 누락") from None

    def initialize(self):
        existing = self.load(allow_missing=True)
        if existing:
            return False  # Never erase existing state, even when initialized twice.
        ref = self.api("GET", "/git/ref/heads/" + self.branch, allow_missing=True)
        if ref is None:
            repo = self.api("GET", "")
            default = repo["default_branch"]
            base = self.api("GET", "/git/ref/heads/" + quote(default,safe=""))
            self.api("POST", "/git/refs", {"ref": "refs/heads/" + self.branch, "sha": base["object"]["sha"]})
        self.save(empty_state(),None)
        return True


class GitHubState:
    def __init__(self,store):
        self.store = store
        loaded = store.load(allow_missing=True)
        if loaded is None:
            raise StateError("원격 전송 기록이 없습니다. Run workflow에서 initialize를 먼저 실행하세요")
        self.data, self.sha = loaded
        validate_state(self.data)
        changed = False
        cutoff = time.time() - 7*86400
        for key, row in list(self.data["posts"].items()):
            if row["status"] == "sending":
                row["status"] = "unknown"
                changed = True
            elif row["status"] != "unknown" and row["updated"] < cutoff:
                del self.data["posts"][key]
                changed = True
        if changed:
            self.flush()

    def flush(self):
        self.sha = self.store.save(self.data,self.sha)

    def get(self,key,default=None):
        return self.data["meta"].get(key,default)

    def set(self,key,value):
        if self.get(key) == value:
            return
        self.data["meta"][key] = value
        self.flush()

    def status(self,key):
        return self.data["posts"].get(key,{}).get("status")

    def record(self,key,status,message_id=""):
        self.record_many([key],status)

    def record_many(self,keys,status):
        if not keys:
            return
        for key in keys:
            self.data["posts"][key] = {"status":status,"updated":time.time()}
        self.flush()

    def close(self):
        pass  # Every write is already persisted before returning.
