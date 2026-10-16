import copy
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import hotdeal_bot as b

NOW = datetime(2026, 10, 16, 21, 0, tzinfo=b.KST)
CONFIG = b.load_config(Path(__file__).with_name("config.json"))


def pp_row(post="100", votes="8 - 0", comments="15", date="26.10.16 20:30:00", title="[몰] 테스트 키보드 (49,000원/무료)"):
    return f'''<table><tr class="baseList"><td class="baseList-numb">{post}</td>
    <td><a class="baseList-title" href="view.php?id=ppomppu&amp;no={post}">{title}</a><span class="baseList-c">{comments}</span>
    <small class="baseList-small">[컴퓨터]</small></td><td title="{date}"><time class="baseList-time">20:30</time></td>
    <td class="baseList-rec">{votes}</td></tr></table>'''


Q_HTML = '''<div class="v2-list-row v2-list-row--hotdeal" data-qc-id="200">
<span class="qc-count-good">1.2천</span><span class="v2-badge">PC/하드웨어</span>
<a class="subject-link" href="/bbs/qb_saleinfo/views/200">[몰] 테스트 GPU</a>
<span class="qc-count-comment">20</span><span class="v2-list-row__price">￦499,000</span>
<span class="v2-list-row__ship">배송비 무료</span><span class="v2-list-row__time">20분 전</span></div>'''
DETAIL = '<h1>[몰] 테스트 상품</h1><div class="board-contents"><p>앱 쿠폰 10%와 특정 카드 결제가 필요합니다.</p><p>배송비 무료입니다.</p><script>bad()</script></div>'
Q_DETAIL = '<h1 class="v2-view-head__title">진행중 [몰] 테스트 GPU</h1><div id="new_contents"><p>회원 쿠폰 적용가입니다.</p></div>'


class FakeClient:
    def __init__(self):
        self.pp = pp_row()
        self.fail = set()

    def get(self, source, url):
        if source in self.fail:
            raise b.SourceError("HTTP 403: 수집 일시 중단")
        if "view.php" in url:
            return DETAIL
        if "/views/" in url:
            return Q_DETAIL
        return self.pp if source == "ppomppu" else Q_HTML


class FakeDiscord:
    def __init__(self, error=None):
        self.messages = []
        self.error = error

    def send(self, message):
        self.messages.append(message)
        if self.error:
            raise self.error
        return str(len(self.messages))


class FakeResponse:
    def __init__(self, value):
        self.value = json.dumps(value).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self, *args):
        return self.value


class SequenceOpener:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []
    def open(self, request, **kwargs):
        self.requests.append(request)
        item = next(self.responses)
        if isinstance(item, Exception):
            raise item
        return item


class BotTests(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(CONFIG)
        for rule in self.config["sources"].values():
            rule["pages"] = 1
        self.state = b.State(":memory:")
        self.client = FakeClient()
        self.sender = FakeDiscord()

    def tearDown(self):
        self.state.close()

    def test_ppomppu_encoding_and_fields(self):
        raw = pp_row().encode("cp949")
        deal = b.parse_ppomppu(b.decode_html(raw, "text/html; charset=euc-kr"), NOW)[0]
        self.assertEqual((deal.votes, deal.downvotes, deal.comments), (8, 0, 15))
        self.assertEqual((deal.price, deal.shipping), ("49,000원", "무료"))
        self.assertEqual(deal.published, NOW - timedelta(minutes=30))
        self.assertTrue(deal.url.endswith("id=ppomppu&no=100"))

    def test_quasarzone_current_dom_and_partner_exclusion(self):
        deal = b.parse_quasarzone(Q_HTML + Q_HTML.replace("qb_saleinfo/views/200", "qb_sale/views/300"), NOW)
        self.assertEqual(len(deal), 1)
        self.assertEqual(deal[0].votes, 1200)
        self.assertEqual(deal[0].published, NOW - timedelta(minutes=20))

    def test_threshold_and_negative_opinion_filter(self):
        for votes, comments, expected in [("8 - 0", "0", True), ("4 - 0", "15", True), ("3 - 0", "200", False),
                                         ("8 - 3", "100", False), ("8 - 2", "100", False), ("bad", "100", False)]:
            with self.subTest(votes=votes, comments=comments):
                deal = b.parse_ppomppu(pp_row(votes=votes, comments=comments), NOW)[0]
                self.assertEqual(b.qualify(deal, self.config, NOW), expected)

    def test_expired_stale_unknown_time(self):
        for title, date in [("[종료] 상품", "26.10.16 20:30:00"), ("상품", "26.10.14 20:30:00"), ("상품", "unknown")]:
            deal = b.parse_ppomppu(pp_row(title=title, date=date), NOW)[0]
            self.assertFalse(b.qualify(deal, self.config, NOW))

    def test_summary_preserves_conditions_and_disables_mentions(self):
        deal = b.parse_ppomppu(pp_row(title="@everyone 테스트"), NOW)[0]
        b.enrich(deal, DETAIL)
        result = b.payload(deal)
        self.assertIn("특정 카드", result["embeds"][0]["description"])
        self.assertNotIn("bad()", result["embeds"][0]["description"])
        self.assertNotIn("@everyone", json.dumps(result, ensure_ascii=False))
        self.assertEqual(result["allowed_mentions"], {"parse": []})

    def test_ppomppu_duplicate_class_attribute(self):
        deal = b.parse_ppomppu(pp_row(), NOW)[0]
        html = '<h1>상품</h1><table><tr><td class="board-contents" align="left" class=han><p>쿠폰 적용 시 무료배송입니다.</p></td></tr></table>'
        self.assertIn("쿠폰", b.enrich(deal, html).summary)

    def test_no_duplicate_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.sqlite3"
            for _ in range(2):
                state = b.State(path)
                b.cycle(self.config, state, self.client, self.sender, now=NOW)
                state.close()
        self.assertEqual(len(self.sender.messages), 2)  # one per source total

    def test_later_votes_still_qualify(self):
        self.config["sources"]["quasarzone"]["enabled"] = False
        self.client.pp = pp_row(votes="1 - 0", comments="1")
        b.cycle(self.config, self.state, self.client, self.sender, now=NOW)
        self.assertEqual(len(self.sender.messages), 0)
        self.client.pp = pp_row(votes="8 - 0", comments="3")
        b.cycle(self.config, self.state, self.client, self.sender, now=NOW + timedelta(minutes=10))
        self.assertEqual(len(self.sender.messages), 1)

    def test_first_run_backlog_stays_suppressed(self):
        self.config["sources"]["quasarzone"]["enabled"] = False
        self.client.pp = pp_row(date="26.10.16 10:00:00")
        for _ in range(2):
            b.cycle(self.config, self.state, self.client, self.sender, now=NOW)
        self.assertEqual(len(self.sender.messages), 0)
        self.assertEqual(self.state.status("ppomppu:100"), "baseline")

    def test_one_source_blocked_does_not_stop_other(self):
        self.client.fail = {"quasarzone"}
        health = b.cycle(self.config, self.state, self.client, self.sender, now=NOW)
        self.assertEqual(health["sources"]["quasarzone"]["status"], "error")
        self.assertEqual(len(self.sender.messages), 1)
        second = b.cycle(self.config, self.state, self.client, self.sender, now=NOW)
        self.assertEqual(second["sources"]["quasarzone"]["status"], "cooldown")

    def test_later_page_failure_is_visible_and_stops_detail_requests(self):
        self.config["sources"]["ppomppu"]["pages"] = 2
        calls = []
        original = self.client.get
        def get(source,url):
            calls.append(url)
            if source == "ppomppu" and "page=2" in url:
                raise b.SourceError("HTTP 403")
            return original(source,url)
        self.client.get = get
        health = b.cycle(self.config,self.state,self.client,self.sender,now=NOW)
        self.assertEqual(health["sources"]["ppomppu"]["status"],"partial")
        self.assertFalse(any("view.php" in url for url in calls))
        self.assertEqual(len(self.sender.messages),1)

    def test_first_run_global_cap(self):
        self.config["max_first_run_messages"] = 1
        b.cycle(self.config,self.state,self.client,self.sender,now=NOW)
        self.assertEqual(len(self.sender.messages),1)

    def test_detail_failure_keeps_other_source(self):
        original = self.client.get
        def get(source, url):
            if source == "quasarzone" and "/views/" in url:
                raise b.SourceError("HTTP 403")
            return original(source, url)
        self.client.get = get
        b.cycle(self.config, self.state, self.client, self.sender, now=NOW)
        self.assertEqual(len(self.sender.messages), 1)

    def test_dry_run_does_not_mark_sent_or_initialize(self):
        health = b.cycle(self.config, self.state, self.client, dry_run=True, now=NOW)
        self.assertEqual(len(health["preview"]), 2)
        self.assertIsNone(self.state.status("ppomppu:100"))
        self.assertIsNone(self.state.get("initialized:ppomppu"))

    def test_uncertain_delivery_not_automatically_retried(self):
        self.config["sources"]["quasarzone"]["enabled"] = False
        sender = FakeDiscord(b.DeliveryError("timeout", uncertain=True))
        for _ in range(2):
            b.cycle(self.config, self.state, self.client, sender, now=NOW)
        self.assertEqual(len(sender.messages), 1)
        self.assertEqual(self.state.status("ppomppu:100"), "unknown")

    def test_crash_during_send_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.sqlite3"
            state = b.State(path)
            state.record("ppomppu:100", "sending")
            state.close()
            state = b.State(path)
            self.assertEqual(state.status("ppomppu:100"), "unknown")
            state.close()

    def test_429_retry_and_wait_confirmation(self):
        limited = HTTPError("REDACTED", 429, "rate limit", {}, io.BytesIO(b'{"retry_after":0.01}'))
        opener = SequenceOpener([limited, FakeResponse({"id": "42"})])
        sleeps = []
        sender = b.Discord("https://discord.com/api/webhooks/123/test_token", opener, sleeps.append)
        self.assertEqual(sender.send({"content":"test"}), "42")
        self.assertEqual(len(sleeps), 1)
        self.assertTrue(opener.requests[0].full_url.endswith("?wait=true"))

    def test_network_error_redacts_token_and_is_uncertain(self):
        opener = SequenceOpener([URLError("secret token")])
        sender = b.Discord("https://discord.com/api/webhooks/123/test_token", opener)
        with self.assertRaises(b.DeliveryError) as ctx:
            sender.send({"content":"test"})
        self.assertTrue(ctx.exception.uncertain)
        self.assertNotIn("secret", str(ctx.exception))

    def test_invalid_webhook_rejected(self):
        for url in ("https://discord.com.evil.test/api/webhooks/1/x", "http://discord.com/api/webhooks/1/x", "https://example.com/hook"):
            with self.assertRaises(ValueError):
                b.validate_webhook(url)

    def test_date_boundaries(self):
        newyear = datetime(2026,1,1,0,5,tzinfo=b.KST)
        self.assertEqual(b.parse_date("12.31",newyear).year, 2025)
        self.assertEqual(b.parse_date("23:59:00",newyear).day, 31)

    def test_lock_prevents_two_senders(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "lock"
            with b.process_lock(path):
                with self.assertRaises(RuntimeError):
                    with b.process_lock(path):
                        pass


if __name__ == "__main__":
    unittest.main()
