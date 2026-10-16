from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest

from pubg_events.collector import discover, parse_article
from pubg_events.core import Event, KST, Reminder, due, payload
from pubg_events.delivery import UncertainDelivery, deliver, send_one, webhook_url
from pubg_events.net import HTTPFailure, NetworkError
from pubg_events.state import State

URL = "https://pubg.com/ko/events/notice/12345"


def event(day=19, hour=9):
    return Event("테스트 이벤트", "이벤트 참여 마감", datetime(2026, 10, day, hour, tzinfo=KST), URL, "미션 확인", "PC 일시")


def html(body, title="특별 보급 테스트"):
    return f'<article class="detail-header detail-header--pc"><h3 class="detail-header__title">{title}</h3></article><article class="content-template">{body}</article>'


class ScheduleTests(unittest.TestCase):
    def test_exactly_three_calendar_stages(self):
        got = []
        for day in range(1, 25):
            got += [r.days for r in due([event()], datetime(2026, 10, day, 20, tzinfo=KST))]
        self.assertEqual(got, [7, 3, 1])

    def test_before_daily_hour_and_after_deadline(self):
        self.assertEqual(due([event()], datetime(2026, 10, 16, 19, tzinfo=KST)), [])
        self.assertEqual(due([event()], datetime(2026, 10, 19, 20, tzinfo=KST)), [])

    def test_utc_to_kst_boundary(self):
        now = datetime.fromisoformat("2026-10-16T11:00:00+00:00")
        self.assertEqual(due([event()], now)[0].days, 3)

    def test_late_discovery_does_not_backfill(self):
        self.assertEqual(due([event()], datetime(2026, 10, 17, 20, tzinfo=KST)), [])
        self.assertEqual([r.days for r in due([event()], datetime(2026, 10, 18, 20, tzinfo=KST))], [1])

    def test_duplicate_source_and_deadline_revision(self):
        self.assertEqual(len(due([event(), event()], datetime(2026, 10, 16, 20, tzinfo=KST))), 1)
        self.assertNotEqual(Reminder(event(), 3).key, Reminder(event(hour=11), 3).key)

    def test_payload_is_bounded_and_cannot_ping(self):
        p = payload(Reminder(event(), 7))
        self.assertEqual(p["allowed_mentions"], {"parse": []})
        self.assertIn("D-7", p["embeds"][0]["title"])
        self.assertIn("한국 시간", p["embeds"][0]["fields"][0]["value"])


class ParseTests(unittest.TestCase):
    def test_monthly_sections_and_pc_only(self):
        body = '<h2>출석</h2><h3>이벤트 기간</h3><ul><li>PC: 26/10/14, 점검 완료 후 - 26/10/28, 11:00</li><li>Console: 26/10/22, 점검 완료 후 - 26/11/5, 11:00</li></ul><h2>할로윈</h2><h3>이벤트 기간</h3><li>PC: 26/10/28, 11:00 - 26/11/2, 11:00</li>'
        events, warnings = parse_article(html(body), URL)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].deadline, datetime(2026, 10, 28, 11, tzinfo=KST))
        self.assertEqual(warnings, [])

    def test_sales_are_excluded_crafting_is_separate(self):
        body = '<h2>콜라보</h2><p>판매 기간</p><li>PC: 26/9/10, 점검 후 - 26/10/14, 09:00</li><h3>특수 제작</h3><p>특수 제작 탭 운영 종료일</p><li>PC: 26/9/10, 점검 후 - 26/10/19, 09:00</li>'
        events, _ = parse_article(html(body, "PUBG x 테스트"), URL)
        self.assertEqual(len(events), 1)
        self.assertIn("재료 사용", events[0].kind)
        self.assertEqual(events[0].deadline.day, 19)

    def test_missing_clock_not_guessed(self):
        events, warnings = parse_article(html('<h2>출석</h2><p>이벤트 기간</p><li>PC: 26/10/1 - 26/10/19</li>'), URL)
        self.assertEqual(events, [])
        self.assertTrue(warnings)

    def test_invalid_date_not_guessed(self):
        events, warnings = parse_article(html('<p>이벤트 기간</p><li>PC: 26/1/1, 09:00 - 26/2/30, 09:00</li>'), URL)
        self.assertEqual(events, [])
        self.assertTrue(warnings)

    def test_header_date_never_used(self):
        events, _ = parse_article(html('<p>2026.10.14 08:00 - 2026.11.19 14:59 (UTC)</p>'), URL)
        self.assertEqual(events, [])

    def test_other_timezone_requires_review(self):
        events, warnings = parse_article(html('<p>이벤트 기간</p><li>PC: 26/1/1, 09:00 - 26/10/19, 09:00 UTC</li>'), URL)
        self.assertEqual(events, [])
        self.assertTrue(warnings)

    def test_pc_cafe_is_not_marked_common(self):
        events, warnings = parse_article(html('<p>이벤트 기간</p><li>PC: 26/1/1, 09:00 - 26/10/19, 09:00</li>', "PC방 이벤트"), URL)
        self.assertEqual(events, [])
        self.assertIn("PC방", warnings[0])

    def test_no_execution_of_nuxt_javascript(self):
        data = '<li class="post-contents__card"><span class="post__title">특별 보급</span></li><script>window.__NUXT__={postId:12345}; throw Error("never execute");</script>'
        self.assertEqual(discover(data, "events"), [URL])

    def test_list_structure_change_fails_visibly(self):
        with self.assertRaises(ValueError):
            discover('<script>window.__NUXT__={postId:123};</script>', "events")

    def test_long_marketing_heading_does_not_become_title(self):
        body = '<h2>제작소: 테스트</h2><h2>' + '이 설명은 제목이 아닙니다. ' * 12 + '</h2><h3>특수 제작</h3><p>특수 제작 탭 운영 종료일</p><li>PC: 26/9/10, 점검 후 - 26/10/19, 09:00</li>'
        events, _ = parse_article(html(body, "PUBG x 테스트"), URL)
        self.assertNotIn("설명", events[0].title)
        self.assertIn("특수 제작", events[0].title)

    def test_nested_blocks_do_not_leak_into_heading(self):
        body = '<h2>콜라보</h2><h3>수집가<p>미션</p><ul><li>아이템 장착하기</li></ul><p>보상</p><ul><li>보상 A</li></ul><p>이벤트 기간</p><li>PC: 26/9/10, 점검 후 - 26/10/19, 09:00</li></h3>'
        events, _ = parse_article(html(body), URL)
        self.assertEqual(events[0].title, "콜라보 / 수집가")
        self.assertTrue(events[0].condition_key)

    def test_matching_missions_and_rewards_have_shared_identity(self):
        body = '<h2>수집가</h2><h3>미션</h3><li>아이템 A 장착</li><h3>보상</h3><li>보상 A</li><h3>이벤트 기간</h3><li>PC: 26/9/10, 점검 후 - 26/10/19, 09:00</li>'
        first, _ = parse_article(html(body), URL)
        second, _ = parse_article(html(body, "PUBG x 테스트"), "https://pubg.com/ko/news/54321")
        self.assertEqual(first[0].identity, second[0].identity)
        different, _ = parse_article(html(body.replace("보상 A", "보상 B")), URL)
        self.assertNotEqual(first[0].identity, different[0].identity)


class DeliveryTests(unittest.TestCase):
    def test_restart_and_all_stages(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            sent = []
            for day in [12, 12, 16, 16, 18, 18]:
                store = State(path)
                reminders = due([event()], datetime(2026, 10, day, 20, tzinfo=KST))
                deliver(reminders, store, "unused", sender=lambda u, r: sent.append(r.days) or "123", sleep=lambda _: None)
            self.assertEqual(sent, [7, 3, 1])

    def test_uncertain_send_does_not_repeat(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            reminder = Reminder(event(), 3)
            def fail(*args):
                raise UncertainDelivery("timeout")
            with self.assertRaises(UncertainDelivery):
                deliver([reminder], State(path), "unused", sender=fail)
            self.assertTrue(State(path).seen(reminder))
            self.assertEqual(State(path).data["posts"][reminder.key]["status"], "uncertain")

    def test_no_send_if_state_cannot_save(self):
        with tempfile.TemporaryDirectory() as d:
            store = State(Path(d) / "state.json")
            def cannot_save():
                raise NetworkError("state unreachable")
            store.save = cannot_save
            calls = []
            with self.assertRaises(NetworkError):
                deliver([Reminder(event(), 1)], store, "unused", sender=lambda *a: calls.append(a))
            self.assertEqual(calls, [])

    def test_known_rejection_can_retry(self):
        with tempfile.TemporaryDirectory() as d:
            store = State(Path(d) / "state.json")
            reminder = Reminder(event(), 1)
            def rejected(*args):
                raise HTTPFailure(403)
            with self.assertRaises(HTTPFailure):
                deliver([reminder], store, "unused", sender=rejected)
            self.assertFalse(store.seen(reminder))

    def test_transport_failure_is_uncertain(self):
        def broken(*args, **kwargs):
            raise NetworkError("timeout")
        with self.assertRaises(UncertainDelivery):
            send_one("unused", Reminder(event(), 1), transport=broken)

    def test_webhook_validation_and_wait(self):
        value = webhook_url("https://discord.com/api/webhooks/123/test_token")
        self.assertTrue(value.endswith("?wait=true"))
        with self.assertRaises(ValueError):
            webhook_url("https://example.com/api/webhooks/123/test_token")


if __name__ == "__main__":
    unittest.main()
