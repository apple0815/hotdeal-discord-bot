import base64
import json
import os
import re
from pathlib import Path
from urllib.parse import quote

from .net import HTTPFailure, NetworkError, request


def validate(data):
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("posts"), dict):
        raise ValueError("전송 기록 형식 오류. 기록을 복구한 뒤 실행하세요.")
    for key, item in data["posts"].items():
        if not re.fullmatch(r"[a-f0-9]{24}", key) or not isinstance(item, dict) or item.get("status") not in ("sending", "sent", "uncertain"):
            raise ValueError("전송 기록 손상. 자동 전송을 중단합니다.")
    return data


class State:
    def __init__(self, path="state/state.json"):
        self.path = Path(path)
        self.data = validate(json.loads(self.path.read_text(encoding="utf-8"))) if self.path.exists() else {"version": 1, "posts": {}}

    def save(self):
        validate(self.data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, self.path)

    def seen(self, post):
        return post.key in self.data["posts"] or any(x.get("fingerprint") == post.fingerprint for x in self.data["posts"].values())

    def mark(self, post, status, **extra):
        # Keep identifiers, fingerprints and delivery status; do not retain full bodies.
        self.data["posts"][post.key] = {"status": status, "fingerprint": post.fingerprint, "url": post.url, "source": post.source, **extra}
        self.save()

    def forget(self, post):
        self.data["posts"].pop(post.key, None)
        self.save()


class GitHubState(State):
    """Persist BEFORE and AFTER each send, not only at the end of an Actions job."""
    def __init__(self):
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        token = os.environ.get("GITHUB_TOKEN", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or not token:
            raise ValueError("GitHub 전송 기록 저장에 필요한 설정이 없습니다")
        self.base = f"https://api.github.com/repos/{repo}"
        self.headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "User-Agent": "PubgEventReminder/1.0", "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28"}
        self.sha = None
        self.branch_exists = False
        try:
            self.api("/git/ref/heads/pubg-events-state")
            self.branch_exists = True
        except HTTPFailure as e:
            if e.status != 404:
                raise
        self.data = {"version": 1, "posts": {}}
        if self.branch_exists:
            try:
                doc = self.api("/contents/delivery-state.json?ref=pubg-events-state")
                self.sha = doc["sha"]
                self.data = validate(json.loads(base64.b64decode(doc["content"]).decode("utf-8")))
            except HTTPFailure as e:
                if e.status != 404:
                    raise
                # A missing file on an existing state branch may mean lost history.
                raise ValueError("pubg-events-state 브랜치의 기록 파일이 없습니다. 복구 후 실행하세요.") from None

    def api(self, path, method="GET", data=None):
        raw, _ = request(self.base + path, headers=self.headers, method=method, data=json.dumps(data).encode() if data is not None else None, redirects=False)
        return json.loads(raw)

    def save(self):
        validate(self.data)
        content = base64.b64encode(json.dumps(self.data, ensure_ascii=False, indent=2).encode()).decode()
        if not self.branch_exists:
            # Create an orphan state commit with its file, then attach a branch.
            # If killed during initialization, no visible empty state branch is left.
            blob = self.api("/git/blobs", "POST", {"content": content, "encoding": "base64"})
            tree = self.api("/git/trees", "POST", {"tree": [{"path": "delivery-state.json", "mode": "100644", "type": "blob", "sha": blob["sha"]}]})
            commit = self.api("/git/commits", "POST", {"message": "Initialize delivery history", "tree": tree["sha"], "parents": []})
            self.api("/git/refs", "POST", {"ref": "refs/heads/pubg-events-state", "sha": commit["sha"]})
            self.branch_exists, self.sha = True, blob["sha"]
            return
        result = self.api("/contents/delivery-state.json", "PUT", {"message": "Save delivery history", "content": content, "branch": "pubg-events-state", "sha": self.sha})
        self.sha = result["content"]["sha"]


class LocalLock:
    def __init__(self, path):
        self.path = Path(path)

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise ValueError("다른 실행이 진행 중이거나 잠금 파일이 남았습니다. README 복구 안내를 확인하세요.") from None
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
        return self

    def __exit__(self, *_):
        self.path.unlink(missing_ok=True)
