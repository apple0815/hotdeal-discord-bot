import argparse
from datetime import datetime
import json
import os
from pathlib import Path

from pubg_events.collector import collect as collect_events
from pubg_events.core import due, payload as event_payload
from .core import KST, Post, now_kst, payload
from . import patches, discovery
from .net import Client
from .delivery import deliver, webhook_url
from .state import GitHubState, State, LocalLock

ROOT = Path(__file__).resolve().parent.parent
SECRETS = {'events':'DISCORD_PUBG_NEWS_WEBHOOK_URL', 'patches':'DISCORD_PUBG_NEWS_WEBHOOK_URL', 'discovery':'DISCORD_GAME_DISCOVERY_WEBHOOK_URL'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--feature', choices=['all','events','patches','discovery'], default='all')
    parser.add_argument('--send', action='store_true')
    parser.add_argument('--now', help='Preview only: ISO8601 time')
    parser.add_argument('--resolve', help='Delivery key to resolve after checking Discord')
    parser.add_argument('--resolution', choices=['sent','retry'])
    args = parser.parse_args()
    if args.now and (args.send or args.resolve):
        parser.error('--now is preview only')
    if args.resolve and (args.send or not args.resolution):
        parser.error('--resolve requires --resolution and cannot use --send')
    now = datetime.fromisoformat(args.now) if args.now else now_kst()
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    now = now.astimezone(KST)
    output = ROOT/'reports'
    output.mkdir(exist_ok=True)
    report = {'checked_at':now.isoformat(), 'mode':'send' if args.send else 'preview', 'features':{}, 'sent':0}
    previews = []
    failed = False
    with LocalLock(ROOT/'state'/'.running'):
        store = GitHubState() if (args.send or args.resolve) and os.getenv('GITHUB_ACTIONS')=='true' else State(ROOT/'state'/'delivery-state.json')
        if args.resolve:
            if args.resolve not in store.data['posts']:
                raise ValueError('Unknown delivery key')
            if args.resolution=='retry':
                del store.data['posts'][args.resolve]
            else:
                store.data['posts'][args.resolve]['status']='sent'
            store.save()
            return 0
        for feature in (list(SECRETS) if args.feature=='all' else [args.feature]):
            try:
                url = webhook_url(os.getenv(SECRETS[feature],'')) if args.send else None
                client = Client()
                if feature=='events':
                    config = json.loads((ROOT/'events-config.json').read_text(encoding='utf-8'))
                    events, details = collect_events(client,config)
                    posts = [Post(r.key,r.url,'pubg-events',event_payload(r)) for r in due(events,now,hour=20,respect_hour=args.send)]
                    details['events'] = [e.record() for e in events]
                elif feature=='patches':
                    posts, details = patches.collect(client)
                else:
                    excluded = {appid for item in store.data['posts'].values() for appid in item.get('app_ids',[])}
                    posts, details = discovery.collect(client,now,excluded)
                pending = [p for p in posts if not store.seen(p)]
                details['pending'] = len(pending)
                details['keys'] = [p.key for p in pending]
                report['features'][feature] = details
                previews.extend([{'feature':feature,'key':p.key,'payload':payload(p)} for p in pending])
                if details.get('errors'):
                    # Partial collection is visible in the report, never silently delivered.
                    failed = True
                    continue
                if args.send:
                    report['sent'] += deliver(pending,store,url)
            except Exception as exc:
                failed = True
                # Never include a transport URL or credential in a log.
                report['features'].setdefault(feature,{})['failure'] = type(exc).__name__
        report['review_needed'] = {k:v for k,v in store.data['posts'].items() if v['status'] in ('sending','uncertain')}
        failed = failed or bool(report['review_needed'])
        (output/'preview.json').write_text(json.dumps(previews,ensure_ascii=False,indent=2),encoding='utf-8')
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'mode':report['mode'],'sent':report['sent'],'features':{k:{x:v[x] for x in ('pending','errors','failure') if x in v} for k,v in report['features'].items()}},ensure_ascii=False))
    return 1 if failed else 0


if __name__=='__main__':
    raise SystemExit(main())
