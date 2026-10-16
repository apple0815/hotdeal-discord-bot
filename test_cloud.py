import copy
import gzip
import io
import json
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import hotdeal_bot as b
from cloud_state import GitHubState, GitHubStore, StateError, empty_state
from test_bot import CONFIG, NOW, FakeClient, FakeDiscord, SequenceOpener


class MemoryStore:
    def __init__(self):
        self.data = empty_state()
        self.sha = 0
        self.fail = False
        self.writes = 0

    def load(self,allow_missing=False):
        return (copy.deepcopy(self.data),str(self.sha)) if self.data is not None else None

    def save(self,data,sha):
        if self.fail or str(self.sha) != sha:
            raise StateError('save failed or conflict')
        self.data = copy.deepcopy(data)
        self.sha += 1
        self.writes += 1
        return str(self.sha)


class HttpResponse:
    def __init__(self,raw,headers):
        self.raw = io.BytesIO(raw)
        self.headers = headers
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def read(self,size): return self.raw.read(size)


class CloudTests(unittest.TestCase):
    def test_gzip_html_and_honest_bot_identity(self):
        html = '<h1>뽐뿌 테스트</h1>'
        response = HttpResponse(gzip.compress(html.encode('cp949')),{'Content-Encoding':'gzip','Content-Type':'text/html; charset=euc-kr'})
        client = b.HttpClient(delay=0)
        client.opener = SequenceOpener([response])
        self.assertEqual(client.raw_get(b.BASES['ppomppu']+'/zboard/'),html)
        request = client.opener.requests[0]
        self.assertEqual(request.get_header('Accept-encoding'),'gzip')
        self.assertEqual(request.get_header('User-agent'),b.UA)

    def test_gzip_size_limit(self):
        response = HttpResponse(gzip.compress(b'x'*3_000_001),{'Content-Encoding':'gzip'})
        client = b.HttpClient(delay=0)
        client.opener = SequenceOpener([response])
        with self.assertRaises(b.SourceError):
            client.raw_get(b.BASES['ppomppu']+'/zboard/')

    def test_broken_gzip_and_plain_html(self):
        client = b.HttpClient(delay=0)
        client.opener = SequenceOpener([HttpResponse(b'invalid',{'Content-Encoding':'gzip'})])
        with self.assertRaises(b.SourceError): client.raw_get(b.BASES['ppomppu']+'/zboard/')
        client.opener = SequenceOpener([HttpResponse(b'plain',{})])
        self.assertEqual(client.raw_get(b.BASES['ppomppu']+'/robots.txt'),'plain')

    def test_pending_delivery_survives_runner_loss(self):
        store = MemoryStore()
        state = GitHubState(store)
        state.record('ppomppu:123','sending')
        recovered = GitHubState(store)
        self.assertEqual(recovered.status('ppomppu:123'),'unknown')
        self.assertEqual(store.data['posts']['ppomppu:123']['status'],'unknown')

    def test_missing_or_corrupt_state_is_not_reset(self):
        for data in (None,{}, {'version':99,'posts':{},'meta':{}}):
            store = MemoryStore()
            store.data = data
            with self.assertRaises(StateError): GitHubState(store)
            self.assertEqual(store.writes,0)

    def test_save_failure_prevents_discord_send(self):
        config = copy.deepcopy(CONFIG)
        config['sources']['quasarzone']['enabled'] = False
        config['sources']['ppomppu']['pages'] = 1
        store = MemoryStore()
        state = GitHubState(store)
        store.fail = True
        sender = FakeDiscord()
        with self.assertRaises(StateError):
            b.cycle(config,state,FakeClient(),sender,now=NOW)
        self.assertEqual(sender.messages,[])

    def test_post_send_save_failure_remains_uncertain_next_run(self):
        config = copy.deepcopy(CONFIG)
        config['sources']['quasarzone']['enabled'] = False
        config['sources']['ppomppu']['pages'] = 1
        store = MemoryStore()
        state = GitHubState(store)
        class Sender(FakeDiscord):
            def send(self,message):
                result = super().send(message)
                store.fail = True
                return result
        sender = Sender()
        with self.assertRaises(StateError):
            b.cycle(config,state,FakeClient(),sender,now=NOW)
        store.fail = False
        recovered = GitHubState(store)
        self.assertEqual(recovered.status('ppomppu:100'),'unknown')
        b.cycle(config,recovered,FakeClient(),sender,now=NOW)
        self.assertEqual(len(sender.messages),1)

    def test_two_runs_share_delivery_history(self):
        store = MemoryStore()
        sender = FakeDiscord()
        config = copy.deepcopy(CONFIG)
        for source in config['sources'].values(): source['pages']=1
        for _ in range(2):
            b.cycle(config,GitHubState(store),FakeClient(),sender,now=NOW)
        self.assertEqual(len(sender.messages),2)

    def test_batch_baseline_and_no_unchanged_writes(self):
        store = MemoryStore()
        state = GitHubState(store)
        state.record_many([f'ppomppu:{i}' for i in range(200)],'baseline')
        self.assertEqual(store.writes,1)
        state.set('initialized:ppomppu',True)
        state.set('initialized:ppomppu',True)
        self.assertEqual(store.writes,2)

    def test_public_state_has_no_discord_identifiers(self):
        store = MemoryStore()
        state = GitHubState(store)
        state.record('ppomppu:123','sent','private_discord_message_id')
        self.assertNotIn('private_discord_message_id',json.dumps(store.data))

    def test_conflict_stops_stale_writer(self):
        store = MemoryStore()
        first,second = GitHubState(store),GitHubState(store)
        first.record('ppomppu:123','sending')
        with self.assertRaises(StateError): second.record('ppomppu:124','sending')

    def test_api_errors_do_not_expose_credentials(self):
        opener = SequenceOpener([HTTPError('secret',403,'token secret',{},io.BytesIO(b'secret'))])
        store = GitHubStore('test/repo','very_secret_token',opener=opener)
        with self.assertRaises(StateError) as ctx: store.load()
        self.assertNotIn('secret',str(ctx.exception))

    def test_initialize_existing_state_is_idempotent(self):
        store = GitHubStore('test/repo','token')
        with patch.object(store,'load',return_value=(empty_state(),'sha')),patch.object(store,'save') as save:
            self.assertFalse(store.initialize())
            save.assert_not_called()


if __name__ == '__main__': unittest.main()
