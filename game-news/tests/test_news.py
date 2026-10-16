from datetime import datetime
import copy
import json
import tempfile
from pathlib import Path
import unittest

from game_news.core import KST, Post, digest, payload
from game_news.discovery import facts, make_post, UNKNOWN
from game_news.patches import summarize
from game_news.state import State
from game_news.delivery import deliver, UncertainDelivery

NOW = datetime(2026,10,16,20,tzinfo=KST)


class NewsTests(unittest.TestCase):
    def app(self):
        return {'type':'game','name':'Test @everyone','platforms':{'windows':True},
            'categories':[{'id':38}], 'release_date':{'date':'15 Oct, 2026','coming_soon':False},
            'short_description':'Co-op for up to 4 players. 20-minute rounds.', 'detailed_description':''}

    def test_explicit_game_facts(self):
        g=facts('10',self.app(),NOW)
        self.assertEqual(g['players'],'최대 4명 (공식 설명)')
        self.assertEqual(g['duration'],'20분 (공식 설명)')
        self.assertEqual(g['crossplay'],UNKNOWN)

    def test_no_invented_player_or_session_count(self):
        d=self.app(); d['short_description']='100 players reviewed this game. 40 hours of story.'
        g=facts('10',d,NOW)
        self.assertEqual(g['players'],UNKNOWN)
        self.assertEqual(g['duration'],UNKNOWN)

    def test_player_range_and_crossplay_scope(self):
        d=self.app(); d['short_description']='2-6 player co-op';d['categories'].append({'id':27})
        g=facts('10',d,NOW)
        self.assertEqual(g['players'],'2–6명 (공식 설명)')
        self.assertIn('플랫폼 조합은 원문 확인',g['crossplay'])

    def test_filter_old_upcoming_dlc_and_local_only(self):
        for change in [{'type':'dlc'},{'release_date':{'date':'1 Aug, 2026'}},{'release_date':{'date':'17 Oct, 2026','coming_soon':True}},{'categories':[{'id':9}]},{'platforms':{'windows':False}}]:
            with self.subTest(change=change):
                d=self.app();d.update(change)
                self.assertIsNone(facts('10',d,NOW))

    def test_recommendations_have_no_poll_and_week_dedup(self):
        g=facts('10',self.app(),NOW)
        a=make_post([g],NOW)
        b=make_post([dict(g,name='changed')],NOW)
        self.assertEqual(a.key,b.key)
        body=payload(a)
        self.assertNotIn('poll',body)
        self.assertNotIn('투표',body['content'])
        self.assertEqual(len(body['embeds']),1)
        self.assertEqual(body['allowed_mentions'],{'parse':[]})
        self.assertEqual(a.app_ids,('10',))

    def test_patch_console_and_store_excluded_and_numbers_preserved(self):
        html='''<h1 class="detail-header__title">패치 노트 - 업데이트 44.1</h1><div class="content-template">
        <h2>게임 플레이</h2><h3>무기 밸런스</h3><p>밸런스 소개</p><ul><li>무기 피해량이 40에서 42로 변경됩니다.</li></ul>
        <h2>(Console) 신규 기능</h2><h3>무기 밸런스</h3><li>콘솔 무기 피해량 99로 변경됩니다.</li>
        <h2>제작소</h2><li>새로운 전설 스킨 20개가 추가됩니다.</li>
        <h2>신규 모드</h2><p>새로운 팀 모드가 추가되어 함께 플레이할 수 있습니다.</p>
        <h2>버그 수정</h2><h3>게임 플레이</h3><li>소생이 실패하는 현상을 수정했습니다.</li></div>'''
        post=summarize(html,'https://pubg.com/ko/news/12345')
        body=json.dumps(post.body,ensure_ascii=False)
        self.assertIn('40에서 42',body)
        self.assertNotIn('99',body)
        self.assertNotIn('전설 스킨',body)
        self.assertIn('소생이 실패',body)

    def test_sending_record_blocks_replay_after_interruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=State(Path(tmp)/'state.json')
            p=Post(digest('test'),'https://pubg.com/ko/news/1','pubg-patch',{})
            def fail(url,post):
                self.assertTrue(State(store.path).seen(post))
                raise UncertainDelivery()
            with self.assertRaises(UncertainDelivery):
                deliver([p],store,'unused',sender=fail,sleep=lambda _:None)
            self.assertEqual(store.data['posts'][p.key]['status'],'uncertain')
            self.assertEqual(deliver([p],store,'unused',sender=fail,sleep=lambda _:None),0)


if __name__=='__main__':
    unittest.main()
