# 겜방 게임 소식 자동화

기존 할인·게임 팁 자동화와 같은 GitHub Actions 저장소에 붙이는 독립 모듈입니다. 별도 유료 API나 상시 실행 PC 없이 작동하며, Discord 발신 이름은 `겜방 게임 소식`입니다. 채널마다 웹훅을 사용하므로 Discord 앱 봇 계정/슬래시 명령어는 필요하지 않습니다.

| 기능 | 채널 | 한국 시간 |
|---|---|---|
| PUBG PC 이벤트 마감 | 배그-정보 | 매일 20:07, D-7 / D-3 / D-1에 각각 1회 |
| PUBG PC 패치 요점 | 배그-정보 | 매일 09:07 / 21:07 확인, 새 내용만 게시 |
| 같이 할 신작·데모 소개 | 종합게임정보 | 금요일 20:07, 최대 3개, 소개만 게시 |

GitHub 예약은 혼잡 시 늦어질 수 있습니다. 첫 배포 수동 실행은 현재 패치·해당 주 후보·당일 마감 단계만 게시합니다. 이미 지난 D-7 등을 몰아서 보내지 않습니다.

## 기존 작업에 붙이기

이 폴더에서 `.github`를 제외한 소스를 기존 저장소의 `game-news/`에 넣고, `.github/workflows/game-news.yml`은 저장소 루트의 같은 위치에 추가합니다. 기존 파일을 덮어쓸 필요가 없습니다.

Repository Secrets:
- `DISCORD_PUBG_NEWS_WEBHOOK_URL`: 배그-정보 채널
- `DISCORD_GAME_DISCOVERY_WEBHOOK_URL`: 종합게임정보 채널

Repository Variable `GAME_NEWS_ENABLED=true`가 예약 전송을 켭니다. 비활성화할 때 `false`로 바꾸세요. 수동 `send`는 이 변수와 별개이며 명시적 실행만 허용합니다. `preview`와 코드 push 검증은 게시하지 않습니다.

전송 이력은 `game-news-state` 브랜치에 매 전송 전후 저장됩니다. 기존 할인·팁·단독 이벤트 모듈의 이력과 구분됩니다. 기존 단독 이벤트 자동화와 동시에 켜면 중복되므로 이 통합 버전만 활성화하세요.

## 수집 범위와 정확성

- 이벤트: PUBG 한국어 공식 공지에서 PC 종료 날짜·시간이 명시된 행사, 보상 수령, 재료 사용 등을 추출합니다. 이미지·표에만 있는 날짜, 시간대 불명확, PC방 전용, 별도 사이트 쿠폰/드롭스는 추측하지 않고 검토 목록으로 남깁니다. 모든 캠페인을 완전히 찾는 서비스는 아닙니다.
- 패치: 공식 최신 패치의 밸런스·새 콘텐츠·편의성·플레이에 영향을 주는 버그 수정 문장을 선정·축약합니다. 콘솔 전용·상점·스킨은 제외합니다. AI 추론으로 메타를 단정하지 않습니다. 내용이 길면 생략 부호가 붙으며 원문 링크에서 수치·조건을 확인할 수 있습니다.
- 신작: Steam 최근 30일 이내 출시 Windows 온라인 협동 게임과 데모에서 후보를 고릅니다. DLC, 출시 전, 로컬 멀티 전용은 제외합니다. 공식 영어 소개에서 인원/라운드 시간을 명시한 경우만 표시합니다. 크로스플레이 표기가 없는 경우 불가로 단정하지 않습니다. Steam의 지원 표기가 있어도 PC-콘솔 조합까지 추정하지 않습니다. 한국어 소개가 있으면 우선 사용합니다. 지난주 소개한 게임은 다시 추천하지 않습니다.
- 모든 게시물은 전체/역할 멘션을 차단합니다. 외부 본문에 명령이 있어도 실행하지 않습니다.

## 로컬 미리보기 및 검사

Python 3.12 이상:

```sh
pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m game_news --feature all
python -m game_news --feature events --now 2026-10-16T20:00:00+09:00
```

`reports/preview.json`은 실제 Discord 요청 형태, `reports/report.json`은 출처·후보·검토 필요 항목입니다. 실제 전송에는 `--send`가 필요하며 `--now`와 같이 사용할 수 없습니다. 명령줄에 웹훅을 직접 적지 말고 환경변수/Secrets를 쓰세요.

## 실패·중복 방지와 복구

429만 제한적으로 재시도합니다. 연결 단절/서버 오류로 성공 여부를 모르면 `uncertain`으로 남기고 자동 재전송하지 않습니다. 기록 저장에 실패하면 게시하지 않습니다. 수집 오류가 난 기능은 보고서만 남기고 해당 전송을 보류하며, 다른 기능의 결과는 유지합니다.

Discord에서 해당 메시지 존재 여부를 확인한 뒤 같은 상태 저장소에서:

```sh
python -m game_news --resolve KEY --resolution sent
python -m game_news --resolve KEY --resolution retry
```

`sent`는 이미 게시됨을 확인했을 때, `retry`는 게시되지 않았음을 확인했을 때만 사용합니다. GitHub의 경우 `GITHUB_ACTIONS=true`, 해당 저장소 `GITHUB_REPOSITORY`, 쓰기 가능한 `GITHUB_TOKEN`이 있는 신뢰할 수 있는 환경에서 수행합니다. 상태 브랜치를 삭제하면 중복이 생길 수 있습니다. 잠금 파일 `state/.running`은 실제 실행 프로세스가 종료된 것을 확인한 경우에만 제거합니다.

신작 소개에는 투표나 참여 요청을 붙이지 않습니다.

공식 문서: [PUBG 패치](https://pubg.com/ko/news?category=patch_notes), [Discord Webhook](https://docs.discord.com/developers/resources/webhook).
