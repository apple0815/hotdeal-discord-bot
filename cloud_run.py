"""Single GitHub Actions run. The schedule lives in .github/workflows/hotdeals.yml."""
import argparse
import json
import logging
import os
from pathlib import Path
import sys

import hotdeal_bot as b
from cloud_state import GitHubStore, GitHubState, StateError


def summarize(health, path):
    lines = ["## 핫딜 수집 결과", "", f"확인 시각: {health['checked_at']}", "",
             "| 게시판 | 상태 | 읽은 글 | 선별 |", "|---|---|---:|---:|"]
    for source, row in health["sources"].items():
        status = row["status"] + (" / 본문 오류" if row.get("detail_error") else "")
        lines.append(f"| {b.NAMES[source]} | {status} | {row.get('scanned',0)} | {row.get('qualified',0)} |")
    lines.extend(["",f"실제 전송: {health['sent']}건 / 미리보기: {len(health['preview'])}건"])
    if health.get("delivery_error"):
        lines.extend(["", "전송 오류: " + health["delivery_error"]])
    for item in health["preview"]:
        embed = item["embeds"][0]
        lines.extend(["", f"### {embed['title']}", embed["description"], "", embed["url"]])
    Path(path).write_text("\n".join(lines)+"\n",encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode",choices=("preview","initialize","live","status","test"),default="preview")
    args = parser.parse_args()
    if hasattr(sys.stdout,"reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO,format="%(levelname)s %(message)s")
    try:
        config = b.load_config(b.ROOT/"config.json")
        if args.mode == "test":
            b.Discord(os.environ.get("DISCORD_WEBHOOK_URL", "").strip()).send({
                "username": "핫딜 알림", "allowed_mentions": {"parse": []},
                "content": "✅ 핫딜 알림 연결 테스트입니다. 실제 수집·예약 실행 상태는 GitHub Actions에서 확인하세요."})
            print("Discord 웹후크 연결 테스트 성공")
            return 0
        if args.mode == "initialize":
            created = GitHubStore.from_environment().initialize()
            print("원격 전송 기록 초기화 완료" if created else "기존 전송 기록을 유지했습니다")
            return 0
        if args.mode == "preview":
            state = b.State(":memory:")
            # Preview the recent window without applying first-start flood protection.
            for source in config["sources"]:
                state.set(f"initialized:{source}",True)
            sender = None
        else:
            state = GitHubState(GitHubStore.from_environment())
            if args.mode == "status":
                print("전송 확인이 필요한 글:", [key for key,row in state.data["posts"].items() if row["status"] == "unknown"])
                return 0
            sender = b.Discord(os.environ.get("DISCORD_WEBHOOK_URL", "").strip())
        try:
            health = b.cycle(config,state,b.HttpClient(config["request_delay_seconds"]),sender,args.mode=="preview")
            summary = os.environ.get("GITHUB_STEP_SUMMARY")
            if summary:
                summarize(health,summary)
            print(json.dumps({key:value for key,value in health.items() if key!='preview'},ensure_ascii=False))
            return int(bool(health.get("delivery_error")) or any(row["status"] in ("error","partial","cooldown") or row.get("detail_error") for row in health["sources"].values()))
        finally:
            state.close()
    except (StateError,ValueError,b.DeliveryError) as exc:
        logging.error("%s",exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
