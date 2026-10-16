"""Extract PC gameplay changes from official patch notes; no inferred balance claims."""
import json
import re
from bs4 import BeautifulSoup
from pubg_events.collector import discover, own_text, canonical
from .core import Post, clean, digest, safe

LISTING = "https://pubg.com/ko/news?category=patch_notes&size=10"
EXCLUDE = re.compile(r"Console|콘솔|스킨|콜라보|보급고|제작소|상점|이스포츠|하이라이트|맵 서비스|점검", re.I)
BALANCE = re.compile(r"밸런스|총기|무기|전투|스폰|피해량|반동")
IMPORTANT_BUG = re.compile(r"소생|크래시|충돌|진입|사망|위치|발소리|조준|피해|낙하산|끊김|멈추")


def summarize(html, url):
    canonical(url)
    soup = BeautifulSoup(html, "html.parser")
    article, title = soup.select_one('.content-template'), soup.select_one('.detail-header__title')
    if not article or not title:
        raise ValueError('패치 본문 구조 변경')
    title = clean(title.get_text(' ', strip=True))
    if not re.search(r'패치|업데이트', title):
        raise ValueError('패치 공지가 아님')
    groups = {'밸런스·전투 변화': [], '새 콘텐츠·플레이 편의': [], '주요 버그 수정': []}
    headings, seen = {}, set()
    for node in article.find_all(['h2', 'h3', 'h4', 'p', 'li']):
        text = own_text(node)
        if not text:
            continue
        if node.name.startswith('h'):
            level = int(node.name[1])
            headings = {k:v for k,v in headings.items() if k < level}
            headings[level] = text
            continue
        path = ' / '.join(headings.values())
        if EXCLUDE.search(path) or re.search(r'Console|콘솔|개발자 코멘트', text, re.I):
            continue
        if node.find_parent('li') or text.startswith(('※', '*')) or len(text) < 18:
            continue
        # A parent list item includes its children to preserve numerical context.
        if node.name == 'li':
            text = clean(node.get_text(' ', strip=True))
        if re.search(r'Console|콘솔', text, re.I):
            continue
        if '버그 수정' in path:
            if node.name != 'li' or not IMPORTANT_BUG.search(text):
                continue
            group = '주요 버그 수정'
            label = ''
        elif BALANCE.search(path):
            if node.name != 'li':
                continue
            group = '밸런스·전투 변화'
            label = headings.get(3, headings.get(2, ''))
        else:
            group = '새 콘텐츠·플레이 편의'
            label = headings.get(4, headings.get(3, headings.get(2, '')))
            if not re.search(r'추가|개선|신규|등장|업데이트|변경|가동|살아남', text):
                continue
        if text in seen:
            continue
        seen.add(text)
        groups[group].append((label, text, node.name))
    fields = []
    for name, entries in groups.items():
        # Keep different topics represented rather than taking the first paragraphs.
        chosen, topics = [], {}
        if name == '주요 버그 수정':
            entries.sort(key=lambda item: bool(re.search(r'소생|크래시|진입|실제 위치',item[1])), reverse=True)
        elif name == '새 콘텐츠·플레이 편의':
            def relevance(item):
                label,text,tag=item
                return (3 if re.search(r'신규 모드|SLB|로비|팀 무작위|매치 강제|메시지 래디얼',label) else 0) + (2 if tag=='li' else 0) + (1 if re.search(r'추가|300m|레디|팀원|단축키',text) else 0)
            entries.sort(key=relevance,reverse=True)
        for label, text, tag in entries:
            if label and topics.get(label,0) >= (3 if name == '밸런스·전투 변화' else 1):
                continue
            topics[label] = topics.get(label,0)+1
            chosen.append((label, text))
            if len(chosen) == (4 if name == '주요 버그 수정' else 5):
                break
        if chosen:
            lines = ['• ' + (safe(label, 45) + ': ' if label else '') + safe(text, 180) for label, text in chosen]
            fields.append({'name': name, 'value': '\n'.join(lines)[:1024]})
    if not fields:
        raise ValueError('PC 변경 사항을 추출하지 못함')
    body = {'embeds': [{'title': safe(title, 220) + ' · PC 요점', 'url': url,
        'description': '스팀·카카오 PC 공통 패치에서 플레이 관련 변경을 추렸어요. 세부 수치와 적용 조건은 원문을 확인해 주세요.',
        'color': 15844367, 'fields': fields, 'footer': {'text': '공식 공지 자동 발췌 · 콘솔 전용·판매/스킨 제외'}}]}
    key = digest('patch|' + url + '|' + json.dumps(fields, ensure_ascii=False, sort_keys=True))
    return Post(key, url, 'pubg-patch', body)


def collect(client):
    urls = discover(client.text(LISTING), 'patches')
    if not urls:
        raise ValueError('패치 목록 비어 있음')
    # Latest only on bootstrap avoids flooding the channel with historical notes.
    return [summarize(client.text(urls[0]), urls[0])], {'source_count': 2, 'errors': []}
