"""Recent Windows online multiplayer games/demos from publisher-owned Steam pages."""
from datetime import datetime
import re
from bs4 import BeautifulSoup
from .core import Post, digest, safe, clean

SEARCH = 'https://store.steampowered.com/search/?sort_by=Released_DESC&category1={kind}&category3=38&ignore_preferences=1&l=english&cc=kr'
UNKNOWN = '공식 정보 미확인'


def search_ids(html):
    soup = BeautifulSoup(html, 'html.parser')
    cards = soup.select('a.search_result_row')
    if not cards:
        raise ValueError('Steam 신작 목록이 비었거나 구조 변경')
    found = []
    for card in cards:
        match = re.match(r'https://store\.steampowered\.com/app/(\d+)/', card.get('href', ''))
        if match and match[1] not in found:
            found.append(match[1])
    return found


def facts(appid, data, now):
    if data.get('type') not in ('game', 'demo') or not data.get('platforms', {}).get('windows'):
        return None
    categories = {x['id'] for x in data.get('categories', [])}
    if not categories.intersection({36, 38}):
        return None
    if data.get('content_descriptors', {}).get('ids') and set(data['content_descriptors']['ids']).intersection({3,4}):
        return None
    date = data.get('release_date', {})
    if date.get('coming_soon'):
        return None
    try:
        released = datetime.strptime(date['date'], '%d %b, %Y').date()
    except (KeyError, ValueError):
        return None
    if not 0 <= (now.date() - released).days <= 30:
        return None
    text = clean(BeautifulSoup(data.get('detailed_description', '') + ' ' + data.get('short_description',''), 'html.parser').get_text(' ',strip=True))
    # Match explicit player claims only, not review counts or campaign length.
    players = UNKNOWN
    for pattern in (r'(\d{1,2})\s*[-–]\s*(\d{1,2})[ -]*(?:players?|person)', r'(?:up to|with up to|for up to)\s*(\d{1,2})\s*(?:players?|friends?)', r'(\d{1,2})[ -]*player\s+(?:co-op|coop|cooperative|multiplayer)'):
        m = re.search(pattern, text, re.I)
        if m:
            nums = tuple(map(int, m.groups()))
            if all(1 <= n <= 64 for n in nums):
                players = (f'{nums[0]}–{nums[1]}명' if len(nums)==2 else f'최대 {nums[0]}명') + ' (공식 설명)'
                break
    duration = UNKNOWN
    m = re.search(r'(?:rounds?|matches?|sessions?|runs?)\s+(?:last(?:ing)?|of|take|in|around|about|are)?\s*(\d{1,3}(?:\s*[-–]\s*\d{1,3})?)\s*(?:minutes?|mins?)|(?:\b)(\d{1,3}(?:\s*[-–]\s*\d{1,3})?)[ -]*minute\s+(?:rounds?|matches?|sessions?|runs?)', text, re.I)
    if m:
        duration = (m[1] or m[2]) + '분 (공식 설명)'
    crossplay = '지원 표기 있음 · 플랫폼 조합은 원문 확인' if 27 in categories else UNKNOWN
    return {'appid': str(appid), 'name': data['name'], 'url': f'https://store.steampowered.com/app/{appid}/',
        'released': released.isoformat(), 'demo': data['type']=='demo', 'players':players,
        'duration':duration, 'crossplay':crossplay, 'coop':38 in categories,
        'description': clean(BeautifulSoup(data.get('short_description',''), 'html.parser').get_text(' ',strip=True))}


def make_post(games, now):
    if not games:
        return None
    week = now.isocalendar()
    embeds = []
    for i, game in enumerate(games[:3], 1):
        embeds.append({'title': f'{i}. ' + safe(game['name'], 200) + (' · 데모' if game['demo'] else ''),
            'url':game['url'], 'description':safe(game['description'], 450), 'color': 5763719,
            'fields':[{'name':'몇 명 가능?', 'value':game['players'], 'inline':True},
                {'name':'한 판 시간', 'value':game['duration'], 'inline':True},
                {'name':'크로스플레이', 'value':game['crossplay']},
                {'name':'유형 / 공개일', 'value':('온라인 협동' if game['coop'] else '온라인 멀티') + ' / ' + game['released']}],
            'footer':{'text':'Steam 제작사 설명 기준 · 한글 지원/데모 멀티 범위는 상점 확인'}})
    answers = [{'poll_media':{'text':f'{i}. ' + clean(g['name'])[:48]}} for i,g in enumerate(games[:3],1)]
    answers.append({'poll_media':{'text':'이번 주는 패스'}})
    body = {'content':'🎮 이번 주 같이 해볼 신작·데모 후보! 관심 있는 게임에 투표해 주세요. (72시간 · 복수 선택 가능)',
        'embeds':embeds, 'poll':{'question':{'text':'이번 주 같이 해보고 싶은 게임은?'}, 'answers':answers,
        'duration':72, 'allow_multiselect':True, 'layout_type':1}}
    return Post(digest(f'discovery|{week.year}-W{week.week}'), games[0]['url'], 'steam-discovery', body, tuple(g['appid'] for g in games[:3]))


def collect(client, now, excluded=()):
    ids, errors = [], []
    for kind in (998, 10):
        try:
            ids.extend(search_ids(client.text(SEARCH.format(kind=kind)))[:12])
        except Exception as exc:
            errors.append({'source':'Steam search', 'error':type(exc).__name__})
    games = []
    for appid in dict.fromkeys(ids):
        if appid in excluded:
            continue
        try:
            response = client.json(f'https://store.steampowered.com/api/appdetails?appids={appid}&l=english&cc=kr').get(appid, {})
            if not response.get('success'):
                continue
            game = facts(appid, response['data'], now)
            if game:
                games.append(game)
        except Exception as exc:
            errors.append({'source':f'Steam app {appid}', 'error':type(exc).__name__})
    # Prefer candidates whose party size is documented, mixing in a demo when available.
    games.sort(key=lambda g:(g['players'] != UNKNOWN, g['coop'], g['released']), reverse=True)
    picked = games[:3]
    demos = [g for g in games if g['demo']]
    if len(picked)==3 and demos and not any(g['demo'] for g in picked):
        picked[-1] = demos[0]
    for game in picked:
        try:
            translated = client.json(f'https://store.steampowered.com/api/appdetails?appids={game["appid"]}&l=koreana&cc=kr')
            text = translated.get(game['appid'],{}).get('data',{}).get('short_description','')
            if re.search(r'[가-힣]',text):
                game['description'] = clean(BeautifulSoup(text,'html.parser').get_text(' ',strip=True))
        except Exception:
            pass  # Original publisher description remains available.
    post = make_post(picked, now)
    return ([post] if post else []), {'source_count':len(set(ids))+2, 'candidates':games, 'selected':picked, 'errors':errors}
