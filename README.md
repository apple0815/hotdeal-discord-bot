# 디스코드 핫딜 알림 봇

뽐뿌·퀘이사존의 전체 카테고리를 10분마다 확인하고, 추천·댓글 기준을 충족한 글의 상품명, 가격, 배송 조건, 본문 발췌 요약과 원문 링크를 지정한 디스코드 텍스트 채널에 보냅니다. 별도 디스코드 봇 계정이나 AI API 키 없이 채널 웹후크로 동작합니다.

**PC 환경의 뽐뿌 403은 수정했습니다. GitHub Actions 배포·상태 초기화·Discord 연결 테스트는 성공했으나, GitHub 서버에서 두 게시판 접근이 차단되어 예약 전송은 아직 비활성 상태입니다.**

### 현재 배포 상태 (2026-10-16)

- [공개 저장소](https://github.com/apple0815/hotdeal-discord-bot): 코드 게시 완료, 34개 테스트 통과.
- [상태 초기화](https://github.com/apple0815/hotdeal-discord-bot/actions/runs/38903774180): 성공.
- [Discord 연결 테스트](https://github.com/apple0815/hotdeal-discord-bot/actions/runs/38903999447): 성공, 실제 채널 도착 확인. 웹후크는 Repository Secret에 저장.
- [GitHub HTTP 수집 검사](https://github.com/apple0815/hotdeal-discord-bot/actions/runs/38903756797): x64·ARM 모두 뽐뿌 목록 HTTP 403, 퀘이사존 Cloudflare 확인 페이지로 실패.
- [GitHub macOS·Windows 검사](https://github.com/apple0815/hotdeal-discord-bot/actions/runs/38907681297): 두 환경 모두 뽐뿌 목록 HTTP 403, 퀘이사존 robots.txt HTTP 403. 실제 수집 미리보기도 두 사이트 모두 실패.
- [일반 Chromium 검사](https://github.com/apple0815/hotdeal-discord-bot/actions/runs/38904152755): 두 게시판의 robots.txt 확인 단계에서 차단. CAPTCHA 해결·로그인 쿠키·프록시는 사용하지 않음.
- `HOTDEAL_ENABLED`는 설정하지 않았으며, 접속 가능한 실행 환경을 확인하기 전에는 켜지 않습니다. 아래 GitHub 설정 절차는 **해당 환경에서 preview가 두 사이트 모두 성공한 뒤** 사용합니다.

### 무료 실행 환경 검사

2026-10-16 실제 검사 결과입니다. 무료 요금제라는 사실만으로 사이트 접근까지 보장되지는 않습니다.

| 환경 | 뽐뿌 | 퀘이사존 | 현재 판단 |
|---|---|---|---|
| 현재 PC | 목록·본문 수집 성공 | 목록·본문 수집 성공 | PC 전원 필요 |
| GitHub Linux x64·ARM, macOS, Windows 표준 러너 | 목록 403 | Cloudflare 확인 화면 | 예약 비활성 |
| Cloudflare Workers Playground | robots 200, 목록 302의 Location을 따라가면 403 | robots·목록·표본 본문 200 | 두 사이트 동시 운영 불가 |
| Google Apps Script | PC·모바일 robots.txt 모두 403 | robots.txt 403 | 외부 연결 권한 승인 후 실제 검사 실패 |

Cloudflare 검사는 [공식 Playground](https://developers.cloudflare.com/workers/playground/)에서 일반 `fetch`로 진행했습니다. 브라우저 위장이나 로그인 쿠키를 사용하지 않았습니다. 시험용 코드는 Discord 웹후크를 포함하지 않습니다.

Google 검사는 외부 요청 권한(`script.external_request`)만 승인한 뒤 실행했습니다. 22:21 KST에 두 사이트의 robots.txt가 403을 반환했고, 22:28 KST에 뽐뿌 공식 모바일 도메인도 403을 반환했습니다. 이 환경에서는 목록 요청을 진행하지 않았습니다. 진단 프로젝트에는 예약 트리거를 만들지 않았습니다. **카드 등록·PC 상시 가동 없이 두 사이트를 모두 수집하는 운영 환경은 아직 확보하지 못했습니다.**

카드 등록이 필요한 클라우드는 현재 운영 후보에서 제외합니다. `apps-script-probe.gs`는 Google 서버의 접속 여부만 검사하는 수동 진단 코드입니다. 먼저 `probeRobots`로 정책을 확인한 다음 허용된 경로에 한해 `probeLists`를 실행합니다. 실제 전송·트리거·개인 문서 접근 기능은 없습니다. Apps Script의 개인 계정 한도는 외부 요청 20,000회/일, 트리거 실행 시간 90분/일이며 [Google 공식 한도](https://developers.google.com/apps-script/guides/services/quotas)를 따릅니다. **접속 성공 및 소요 시간 검증 전에는 운영 봇으로 간주하지 않습니다.**

2026-10-16 검증에서 Python 기본 요청의 `Accept-Encoding: identity`를 사용하면 뽐뿌 목록이 403을 반환했고, 같은 봇 이름과 URL에 `Accept-Encoding: gzip`을 적용하면 200을 반환했습니다. gzip 압축 해제와 크기 제한 처리를 추가한 후, 뽐뿌 최대 10페이지 200건과 퀘이사존 2페이지 60건을 실제 수집했습니다. 사용자 계정·로그인 쿠키·프록시·브라우저 위장은 사용하지 않습니다. 사이트 정책이나 구조가 바뀌면 수집을 멈추고 오류로 표시합니다.

## PC 없이: GitHub Actions

공개 저장소의 일반 GitHub 호스팅 Linux 러너에서 실행하도록 구성했습니다. 실행 자체에는 PC와 Codex 앱이 필요 없지만, 현재 검증한 GitHub 러너에서는 게시판 접속이 차단됩니다. 웹후크는 GitHub Secret에만 저장하며 Python은 GitHub 서버에서 실행됩니다.

1. 이 폴더의 내용을 공개 GitHub 저장소의 **최상위**에 올립니다. 숨김 폴더 `.github/workflows/`도 포함해야 합니다. `.venv`, `data`, `webhook.secret`, `.env`는 올리지 않습니다.
2. 저장소 → **Actions → Hotdeal notifier → Run workflow**에서 `preview`를 실행합니다. 전송 없이 두 게시판의 수집·선별·본문 요약을 확인합니다. 최근 24시간 범위를 미리 보므로 로컬 첫 실행의 최근 60분 제한과 다릅니다.
3. 같은 메뉴에서 `initialize`를 한 번 실행합니다. `hotdeal-state` 브랜치에 전송 기록을 준비합니다. 다시 실행해도 기존 기록을 지우지 않습니다.
4. 저장소 → **Settings → Secrets and variables → Actions → Secrets → New repository secret**에서 이름 `DISCORD_WEBHOOK_URL`, 값은 원하는 채널의 웹후크 URL로 저장합니다. [공식 Secret 안내](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets)
   `Run workflow`에서 `test`를 실행하면 실제 채널에 연결 확인 메시지 한 건을 보냅니다. 이 테스트는 수집 성공 여부와 별개입니다.
5. **Variables** 탭에 `HOTDEAL_ENABLED`를 값 `true`로 추가합니다. 이후 매시 03·13·23·33·43·53분 예약 실행을 시작합니다. 이 변수 설정 전에는 예약 실행을 건너뜁니다.
6. 첫 전송 확인은 `Run workflow`에서 `live`를 실행합니다. 기준을 통과한 새 글이 없으면 0건이 정상입니다. 실행 결과의 Summary에서 게시판별 읽은 글·선별·전송 수를 확인합니다.

중지할 때는 `HOTDEAL_ENABLED`를 `false`로 바꾸거나 Actions에서 해당 워크플로를 비활성화합니다. 변수는 예약 실행만 제어하며 수동 `live`는 명시적으로 실행할 수 있습니다. 원격 전송 기록이 없거나 읽기에 실패하면 새 기록으로 시작하지 않고 전송을 중단합니다. 토큰에 `contents: write`가 필요한 이유는 자신의 `hotdeal-state` 브랜치에 기록을 저장하기 위해서입니다. 조직 정책이 쓰기를 금지하면 해당 정책의 허용이 필요합니다.

GitHub는 예약 실행이 혼잡 시 늦어지거나 누락될 수 있다고 안내하므로 **정확한 10분 간격을 보장하지는 않습니다**. 공개 저장소는 활동이 60일 없으면 예약이 자동 비활성화될 수 있습니다. [예약 실행 제한](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

공개 저장소의 표준 러너 실행은 무료입니다. 이 구성은 유료 대형 러너를 사용하지 않으며 실행별 결과 파일을 Artifacts에 쌓지 않습니다. 의존성 캐시만 소량 사용합니다. 비공개 저장소로 바꾸면 계정 무료 시간 한도가 적용됩니다. 10분마다 실행하면 30일에 약 4,320회이므로, 사용량에 따라 기본 제공 시간을 초과할 수 있습니다. [GitHub 요금 안내](https://docs.github.com/en/billing/concepts/product-billing/github-actions)

### 클라우드의 중복 방지

매 실행마다 새로운 서버가 할당되므로 로컬 SQLite만 남기면 기록이 사라집니다. 이 구성은 만료될 수 있는 캐시 대신 `hotdeal-state/state.json`에 기록을 저장합니다. Discord에 보내기 **전**에 `sending`을 원격 저장하고, 확인 응답 후 `sent`로 바꿉니다. 중간에 작업이 종료되면 다음 실행에서 `unknown`으로 보류해 자동 중복 발송을 피합니다. 원격 저장 실패·경합이 생겨도 전송을 중단합니다. 최초 제외 글은 한 번에 기록해 불필요한 API 요청을 줄입니다.

공개 상태에는 원래 게시판의 글 ID, 전송 상태, 시각만 저장합니다. 웹후크, 디스코드 채널 ID, 디스코드 메시지 ID는 저장하지 않습니다. 일반 기록은 7일 뒤 정리하고 `unknown`은 확인할 때까지 유지합니다. Actions의 `status` 실행으로 확인할 글을 볼 수 있습니다. 실제 미전송을 확인한 뒤 원격 상태 파일에서 해당 건만 `retry`로 바꿔 재시도할 수 있습니다.

로컬 봇과 클라우드 봇을 동시에 실행하지 마세요. 두 방식의 전송 기록은 별개입니다.

## Windows에서 시작하기

1. 이 폴더를 압축에서 풀고 보관할 위치에 둡니다. 자동 실행 등록 후 폴더를 옮기면 다시 등록해야 합니다.
2. 디스코드에서 알림을 받을 **일반 텍스트 채널**을 만들고, 서버 설정 → 연동(Integrations) → 웹후크에서 해당 채널용 웹후크를 만듭니다. `웹후크 관리` 권한이 필요합니다. 이름은 예를 들어 `핫딜 알림`으로 설정합니다. [Discord 공식 안내](https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks)
3. **`setup.cmd`를 더블클릭**합니다. Python 가상환경과 라이브러리를 준비합니다. 웹후크 URL을 입력하면 테스트 메시지 한 건을 보내고, 성공했을 때만 Windows 계정에 묶인 암호화 파일로 저장합니다. 입력 글자는 화면에 표시되지 않습니다. URL을 채팅에 붙여넣을 필요가 없습니다.
4. **`install-autostart.cmd`를 더블클릭**하면 즉시 백그라운드 실행하며, 다음 Windows 로그인 때도 자동으로 시작합니다. 현재 계정의 일반 권한 작업 스케줄러에 등록합니다. 권한 정책상 등록이 거절되면 `start.cmd`로 실행할 수 있습니다.
5. `status.cmd`로 수집 상태를 확인합니다. `stop-autostart.cmd`는 자동 실행을 중지하고 등록을 해제합니다.

직접 켜 두고 싶으면 4번 대신 `start.cmd`를 실행하고 창을 유지하세요. 종료는 `Ctrl+C`입니다. 수동 실행과 자동 실행을 동시에 켜도 동일 폴더에서는 한 프로세스만 전송합니다. 서로 다른 복사본을 동시에 실행하면 중복 알림이 생길 수 있습니다.

**이 Windows 방식은 PC가 켜져 있고 잠들지 않았으며, 등록한 Windows 계정이 로그인된 동안 작동합니다.** 화면 잠금은 괜찮지만 절전·종료·로그아웃 중에는 수집하지 않습니다. PC 없이 계속 받으려면 두 게시판 접속이 가능한 클라우드 실행 환경이 필요합니다. 현재 GitHub 러너의 접속 제한은 위 배포 상태를 참고하세요.

Python 3.10 이상이 필요합니다. 설치된 Python 또는 현재 PC의 Codex 번들 Python을 자동으로 찾습니다. 없으면 [Python 공식 사이트](https://www.python.org/downloads/windows/)에서 설치합니다. 번들 Python을 사용할 경우 Codex 런타임 업데이트 뒤 가상환경을 다시 만들어야 할 수 있습니다.

웹후크 없이 수집만 확인하려면 `preview.cmd`를 실행하세요. 실제 전송과 전송 기록 변경은 하지 않습니다. 첫 실행의 최근 60분 조건까지 적용하므로, 정상 수집이어도 미리보기 메시지가 0개일 수 있습니다.

## 기본 선별 기준

| 항목 | 기본값 |
|---|---|
| 확인 간격 | 10분 |
| 카테고리 | 전체 |
| 추천 기준 | 추천 8 이상 **또는** 추천 4 이상이면서 댓글 15 이상 |
| 뽐뿌 다른의견 | 2 초과 또는 추천 대비 20% 초과면 제외 |
| 글의 나이 | 최근 24시간 이내 |
| 최초 실행 | 최근 60분 글만, 최대 3건 |
| 이후 한 번에 보내는 수 | 최대 5건 |
| 확인할 목록 범위 | 뽐뿌 최대 10페이지, 퀘이사존 최대 2페이지 |
| 다시 알릴지 | 같은 게시판의 같은 글은 한 번만 |

추천 기준은 초기 운영값이며 사이트의 공식 핫딜 인증 기준이 아닙니다. 댓글이 많은 것만으로 통과하지 않습니다. 퀘이사존 목록에 반대 수가 없어 해당 값은 `미제공`으로 표시합니다. 댓글 내용의 긍정/부정 분석은 포함하지 않았습니다.

`config.json`에서 사이트별 추천·댓글 수, 제외 키워드, 주기를 바꿀 수 있습니다. 변경 후 실행 중인 봇을 다시 시작하세요. `interval_seconds`는 600이 10분입니다. 사이트를 끄려면 해당 `enabled`를 `false`로 바꿉니다.

전체 카테고리는 두 일반 핫딜 게시판의 카테고리를 뜻합니다. 뽐뿌 해외뽐뿌·휴대폰 게시판과 퀘이사존 파트너핫딜은 포함하지 않았습니다. 최근 24시간이라도 설정한 최대 페이지보다 뒤로 밀린 글은 확인하지 못할 수 있습니다. 처음에는 기준 미달이어도 확인 범위 내에서 추천이 늘면 다음 주기에 재평가합니다. 최초 실행 때 이미 60분이 지난 글은 초기 알림 폭주 방지를 위해 제외 기록을 남깁니다.

## 알림 내용

> [쇼핑몰] 상품명 (표기 가격/배송 조건)
>
> • 본문에 나온 쿠폰·카드·회원 조건 등 최대 3개 문장을 발췌
>
> 게시글 표기 가격 / 배송·이용 조건
>
> 추천 12 · 댓글 20 · 다른의견 0
>
> 제목을 누르면 커뮤니티 원문으로 이동

요약은 AI가 새로운 문장을 생성하는 방식이 아닌 **주요 조건을 우선한 본문 발췌**입니다. 이미지 안의 글자는 읽지 않으며, 정보가 없으면 `원문 확인`이라고 표시합니다. 가격·배송은 게시글 표기값으로, 상품 페이지 실결제가나 최저가를 검증하지 않습니다. 종료/품절 표시를 확인하지만 이후의 품절·가격 변경을 실시간 추적하거나 이미 보낸 메시지를 갱신하지는 않습니다. 본문을 읽을 수 없는 글은 요약 없이 보내지 않고 보류합니다.

## 수집 오류·중복 처리

- `data/health.json`: 마지막 확인 시간, 사이트별 읽은 글 수·선별 수·오류, 전송 결과.
- `data/bot.log`: 회전 로그. 각 파일 최대 약 1MB, 이전 파일 3개 보관.
- `data/state.sqlite3`: 전송·초기 제외 기록. 재시작 후에도 같은 글을 다시 보내지 않게 유지해야 합니다.
- 403/429 등 접근 제한은 해당 사이트를 기본 1시간 쉬게 합니다. 네트워크 실패는 10분, robots 정책 제한은 24시간 후 다시 확인합니다. 다른 사이트는 계속 처리합니다. 사이트 구조를 읽지 못한 경우도 오류로 표시합니다.
- Discord 응답 429는 허용된 짧은 대기 후 재시도합니다. 응답 시간 초과·서버 오류로 전송 여부가 불명확하면 `unknown`으로 남기고 자동 재전송하지 않습니다. `status.cmd`에 확인할 글 ID가 표시됩니다.
- 수집·전송 상태는 **로컬 로그에서 확인**합니다. 장애를 별도 디스코드 메시지로 보내는 기능은 없습니다. 알림이 없다고 정상 작동으로 단정하지 말고 `status.cmd`의 시간과 상태를 확인하세요.

`unknown` 글이 디스코드에 실제로 올라가지 않았음을 확인했다면, 봇을 중지한 뒤 해당 글 하나만 재시도 상태로 바꿀 수 있습니다.

```powershell
.\.venv\Scripts\python.exe .\hotdeal_bot.py --retry-unknown ppomppu:123456
```

이후 봇을 다시 시작하면 해당 글이 확인 범위에 있고 현재 기준도 충족할 때 재시도합니다. 게시판 간 동일 상품이나 새 번호로 재등록된 글까지 중복 판정하지는 않습니다.

## Linux / NAS

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
read -rsp 'Discord webhook URL: ' DISCORD_WEBHOOK_URL; echo
export DISCORD_WEBHOOK_URL
.venv/bin/python hotdeal_bot.py --test-webhook
.venv/bin/python hotdeal_bot.py
```

마지막 명령이 10분 반복 실행을 담당합니다. 상시 운영할 때는 해당 호스트의 서비스 관리자에 등록하고 `data/`를 영구 저장하세요. Windows의 `webhook.secret`은 다른 계정·OS로 복사해 사용할 수 없습니다. 일반 텍스트 채널용 웹후크만 지원하며 Discord 포럼 채널은 별도 thread 설정이 필요합니다.

## 검증

```powershell
.\.venv\Scripts\python.exe -m unittest -v test_bot
.\.venv\Scripts\python.exe .\hotdeal_bot.py --dry-run --once
```

`python -m unittest discover -v`로 기본 봇과 클라우드 테스트를 모두 실행합니다. 선별 경계, 다른의견 비율, EUC-KR, 현재 두 게시판의 HTML 형태, 실제 뽐뿌의 중복 class 속성, 나중에 늘어난 추천, 최초 알림 제한, 재시작 중복, 중복 실행 잠금, 사이트별 장애 격리, 부분 수집 실패, 429 재시도, 전송 결과 불명, gzip 처리, 원격 상태 유실·저장 실패·경합 및 작업 중단 후 복구를 검증합니다. 실제 디스코드 전송은 GitHub Actions의 test 실행으로 채널 도착까지 확인했습니다. 게시판 접속 제한으로 실제 핫딜의 예약 전송은 아직 시작하지 않았습니다. Windows 작업 스케줄러 스크립트는 구문 검사를 마쳤으며, 현재 PC에는 작업을 등록하지 않았습니다.

참고: [뽐뿌 게시판](https://www.ppomppu.co.kr/zboard/zboard.php?id=ppomppu), [퀘이사존 핫딜](https://quasarzone.com/bbs/qb_saleinfo), [Discord 웹후크 API](https://docs.discord.com/developers/resources/webhook#execute-webhook). 사이트 접근 정책과 HTML은 변경될 수 있습니다.
