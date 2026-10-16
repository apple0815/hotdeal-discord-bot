import argparse
from datetime import datetime
import json
import os
from pathlib import Path

from .collector import collect
from .core import KST, due, now_kst, payload
from .delivery import UncertainDelivery, deliver, webhook_url
from .net import Client, NetworkError
from .state import GitHubState, LocalLock, State

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description="PUBG PC D-7 / D-3 / D-1 reminders")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--send", action="store_true")
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--resolve", metavar="KEY")
    parser.add_argument("--resolution", choices=("sent", "retry"))
    parser.add_argument("--now", help="미리보기용 한국 시각 ISO 8601; 실제 전송에서는 금지")
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    args = parser.parse_args()
    if args.now and (args.send or args.resolve):
        parser.error("--now는 미리보기에서만 사용할 수 있습니다")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    now = datetime.fromisoformat(args.now) if args.now else now_kst()
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    output = ROOT / "reports"
    output.mkdir(parents=True, exist_ok=True)
    report = {"mode": "send" if args.send else "preview", "checked_at": now.isoformat(), "sent": 0}
    with LocalLock(ROOT / "state" / ".running"):
        try:
            if args.send or args.resolve:
                # A token is deliberately only loaded for explicit write modes.
                store = GitHubState() if os.environ.get("GITHUB_ACTIONS") == "true" else State(ROOT / "state" / "delivery-state.json")
            else:
                store = State(ROOT / "state" / "delivery-state.json")
            if args.resolve:
                if args.resolve not in store.data["posts"] or not args.resolution:
                    raise ValueError("존재하는 기록 키와 sent/retry 선택이 필요합니다")
                if args.resolution == "sent":
                    store.data["posts"][args.resolve]["status"] = "sent"
                else:
                    del store.data["posts"][args.resolve]
                store.save()
                print("전송 기록을 수정했습니다")
                return 0
            # Validate destination before fetching. Never log the secret URL.
            url = webhook_url(os.environ.get("DISCORD_EVENTS_WEBHOOK_URL", "")) if args.send else None
            events, source_report = collect(Client(), config)
            report.update(source_report)
            report["event_count"] = len(events)
            report["events"] = [event.record() for event in sorted(events, key=lambda e: e.deadline)]
            reminders = due(events, now, hour=config.get("send_hour_kst", 20), respect_hour=args.send)
            reminders = [r for r in reminders if not store.seen(r)]
            report["due_count"] = len(reminders)
            report["due_keys"] = [r.key for r in reminders]
            (output / "preview.json").write_text(json.dumps([payload(r) for r in reminders], ensure_ascii=False, indent=2), encoding="utf-8")
            if args.send:
                report["sent"] = deliver(reminders, store, url)
            report["review_needed"] = {k: v for k, v in store.data["posts"].items() if v["status"] in ("sending", "uncertain")}
            print(json.dumps({k: report[k] for k in ("mode", "source_count", "event_count", "due_count", "sent", "errors")}, ensure_ascii=False))
            return 1 if report["errors"] or report["review_needed"] else 0
        except (ValueError, NetworkError, UncertainDelivery) as exc:
            # Exceptions from transport contain neither a webhook nor token.
            report["failure"] = type(exc).__name__
            print("실행 실패: " + type(exc).__name__ + "; reports/report.json 및 전송 기록을 확인하세요")
            return 1
        finally:
            if "store" in locals():
                report["review_needed"] = {k: v for k, v in store.data["posts"].items() if v["status"] in ("sending", "uncertain")}
            (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
