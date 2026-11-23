# Stock Viewer

국내·해외 주식 시세를 웹 화면으로 보고, 정해진 시각에 Slack으로 요약 리포트를 보내는 개인용 도구입니다.
Vercel(서울 리전)에 배포해 운영하고, 로컬에서도 그대로 실행할 수 있습니다.

- **웹 대시보드**: 시장 지수, 해외 지수, 글로벌 지표(금·유가·환율·미국채 10년물), 관심 종목, 해외 종목의 현재가·전일 대비·장중 그래프, 국내 시장·종목의 외국인/기관/개인 순매수. 5분마다 자동 갱신. 글로벌 지표(금·유가·환율·미국채)의 차트는 최근 3개월 일봉이고 점선은 3개월 전 값입니다(Slack 리포트의 지표 미니 차트는 최근 24시간). 모든 카드(지수·종목·글로벌 지표)를 클릭하면 야후 파이낸스의 해당 시세 페이지가 새 창으로 열립니다(목록의 야후 심볼로 링크를 자동 생성).
- **Slack 리포트**: 같은 데이터를 Slack 웹훅으로 전송. [cron-job.org](https://cron-job.org)가 정해진 시각에 Vercel의 `/api/report`를 호출합니다. (국내 리포트에는 시장 지수(KOSPI·KOSDAQ)에만 순매수 줄이 `외국인 / 기관 / 개인` 순서로 포함되고 개별 종목에는 없습니다. 장중에는 오늘 값이 확정되지 않아, 전 거래일 값이면 줄 끝에 `(전일)`이 붙습니다)

## 구조

```
cron-job.org (시간대별 예약) ──HTTP──▶  Vercel 함수 (api/index.py, 서울 리전)  ──▶  Slack 웹훅
                                          ├─ /                대시보드 (토큰 쿠키 인증)
                                          ├─ /api/quotes      시세 JSON (같은 인증)
                                          ├─ /api/plain       시세 text/plain (인증 없음, 순매수 제외)
                                          ├─ /api/report      Slack 전송 (Bearer CRON_SECRET)
                                          └─ /api/slack       Slack 슬래시 커맨드 /report (서명 검증)
                                       yfinance(야후) · pykrx(KRX 로그인) 로 데이터 조회
```

| 파일 | 역할 |
|---|---|
| `stock_viewer.py` | 종목 설정, 데이터 조회, 대시보드 HTML. 직접 실행하면 로컬 서버(`localhost:8000`) |
| `slack_report.py` | 리포트 생성·전송. CLI로도, Vercel에서도 사용 |
| `api/index.py` | Vercel 서버리스 진입점 (인증, 라우팅) |
| `vercel.json`, `pyproject.toml` | Vercel 설정 (서울 리전 `icn1`, 최대 60초, 경로 rewrite, Python 진입점) |
| `.github/workflows/report.yml` | Slack 리포트 예약 실행 |

Vercel Hobby 플랜의 Cron은 하루 1회까지이고, GitHub Actions의 `schedule`은 실제로 수 시간 지연되거나 누락되어서(2026-11-19 확인) 외부 스케줄러 cron-job.org를 씁니다. 서울 리전을 쓰는 이유는 KRX·야후가 해외 IP에서 막힐 수 있어서입니다.

## 데이터 출처

| 데이터 | 출처 | 비고 |
|---|---|---|
| 현재가, 전일 종가, 장중 그래프 | [yfinance](https://github.com/ranaroussi/yfinance) (야후 파이낸스) | 비공식, 한국 종목은 15~20분 지연될 수 있음 |
| 외국인·기관·개인 순매수 | [pykrx](https://github.com/sharebook-kr/pykrx) (KRX 정보데이터시스템) | **KRX 로그인 필요**, 장 마감 후 확정되는 값 |

## 자격 증명과 비밀값

코드와 저장소에는 비밀값이 없습니다. 실행 환경에 따라 저장 위치가 다릅니다.

| 값 | Vercel (운영) | 로컬 (macOS 키체인) | GitHub (수동 실행용) / cron-job.org |
|---|---|---|---|
| KRX 로그인 ID | 환경변수 `KRX_ID` | 서비스 `krx-pw`의 **계정(`-a`)** | – |
| KRX 비밀번호 | 환경변수 `KRX_PW` | 서비스 `krx-pw`의 비밀번호 | – |
| Slack 웹훅 URL | 환경변수 `SLACK_WEBHOOK_URL` | 서비스 `slack-webhook-url` | – |
| 대시보드 접속 토큰 | 환경변수 `DASH_TOKEN` | (로컬 서버는 인증 없음) | – |
| 슬래시 커맨드 서명 검증 | 환경변수 `SLACK_SIGNING_SECRET` | – | – |
| (선택) 허용 사용자/채널 | 환경변수 `SLACK_ALLOWED_USER_ID`, `SLACK_ALLOWED_CHANNEL_ID` | – | – |
| 리포트 호출 시크릿 | 환경변수 `CRON_SECRET` | – | GitHub Secret `CRON_SECRET`, cron-job.org 작업의 `Authorization: Bearer` 헤더 (모두 같은 값) |
| 배포 주소 | – | – | Variable `REPORT_BASE_URL` (비밀 아님) |

코드는 환경변수가 있으면 그것을, 없으면 키체인을 읽습니다. 토큰·시크릿이 설정되지 않은 Vercel 경로는 열리지 않습니다(fail closed).

### Vercel 환경변수 등록

`-w`나 값 인자를 직접 적으면 셸 히스토리에 남으므로, 프롬프트나 파이프로 입력하세요. 환경변수는 **다음 배포부터** 적용됩니다(`vercel deploy --prod`).

```bash
# 키체인에 이미 저장된 값을 그대로 옮기기 (다시 입력할 필요 없음)
security find-generic-password -s krx-pw | sed -n 's/.*"acct"<blob>="\(.*\)"/\1/p' | tr -d '\n' | vercel env add KRX_ID production --sensitive
security find-generic-password -s krx-pw -w | tr -d '\n' | vercel env add KRX_PW production --sensitive
security find-generic-password -s slack-webhook-url -w | tr -d '\n' | vercel env add SLACK_WEBHOOK_URL production --sensitive

# 같은 값을 Vercel·GitHub·cron-job.org에 넣어야 하는 CRON_SECRET (아래 "Slack 리포트 일정"의 스크립트가 cron-job.org에 넣어 줌)
CRON=$(openssl rand -hex 24)
printf '%s' "$CRON" | vercel env add CRON_SECRET production --sensitive
printf '%s' "$CRON" | gh secret set CRON_SECRET
unset CRON

# 대시보드 토큰 (클립보드로 복사되니 비밀번호 관리자에 저장)
DASH=$(openssl rand -hex 24)
printf '%s' "$DASH" | vercel env add DASH_TOKEN production --sensitive
printf '%s' "$DASH" | pbcopy
unset DASH

gh variable set REPORT_BASE_URL --body "https://<프로젝트>.vercel.app"
```

값을 바꿀 때는 `vercel env rm <이름> production --yes` 후 다시 추가하고 재배포합니다.

### 로컬용 키체인 저장

로컬에서 직접 실행할 때만 필요합니다. `krx-pw`는 **계정(`-a`) 값을 KRX 로그인 ID로 사용**합니다(맥 사용자명이 아님).

```bash
security add-generic-password -a "<KRX 로그인 ID>" -s krx-pw -w     # 프롬프트에 KRX 비밀번호
security add-generic-password -a "$USER" -s slack-webhook-url -w    # 프롬프트에 웹훅 URL
# 확인 (값은 출력하지 않음) / 수정(-U) / 삭제
security find-generic-password -s krx-pw >/dev/null 2>&1 && echo "있음" || echo "없음"
security add-generic-password -U -a "<KRX 로그인 ID>" -s krx-pw -w
security delete-generic-password -s krx-pw
```

### Slack 웹훅 URL 만들기

1. <https://api.slack.com/apps> → **Create New App** → *From scratch*
2. 왼쪽 메뉴 **Incoming Webhooks** → 토글 **On**
3. **Add New Webhook to Workspace** → 메시지를 받을 채널 선택
4. 생성된 URL을 위 방법으로 저장

웹훅 URL은 비밀번호처럼 다루세요. 유출되면 Slack 앱 설정에서 폐기하고 새로 만든 뒤 값을 교체하세요.

## `/api/plain` — 스크립트·AI 에이전트용 평문 시세

`https://<프로젝트>.vercel.app/api/plain` 은 **인증 없이** 열리고, 한 줄에 한 종목씩 파이프로 구분한 고정 포맷을 돌려줍니다. 쿠키를 들고 다닐 수 없는 도구(예: AI 에이전트의 웹 페이지 읽기)가 시세를 그대로 읽어 쓰려고 만든 경로입니다.

```
AS_OF 2026-11-23T15:04:11+0900 Asia/Seoul
SOURCE yahoo-finance(yfinance); KR quotes may lag 15-20min; low/high derived from 1m closes
FIELDS section|name|price|change|ratio_pct|prev_close|low|high|data_at_kst|age_sec|state

KR_INDEX|KOSPI|4,711.71|-158.25|-3.25|4,869.96|4,586.80|4,741.54|2026-11-23T15:00|240|OPEN
KR_STOCK|삼성전자|176,500|-4,400|-2.43|180,900|171,500|178,500|2026-11-23T15:00|240|OPEN
US_STOCK|엔비디아|125.53|-20.45|-14.01|145.98|124.10|140.22|2026-11-21T06:00|119000|CLOSED
```

- `section`: `KR_INDEX` / `KR_STOCK` / `US_INDEX` / `US_STOCK` / `MACRO`
- `data_at_kst`, `age_sec`: 장중 데이터의 마지막 시각(한국시간)과 그 경과 초. **야후 지연이 그대로 드러나므로 읽는 쪽이 시각을 추측할 필요가 없습니다.**
- `state`: 국내는 평일 09:00~15:40 + 오늘 데이터면 `OPEN`, 해외는 마지막 데이터가 90분 이내면 `OPEN`. 그래서 휴장일·조기 폐장이 자동으로 `CLOSED`로 나옵니다. 데이터가 없으면 `NODATA`, 조회 실패한 줄은 `section|이름|ERROR|사유`.
- `low`/`high`: 1분봉 **종가**들의 최저·최고라 실제 장중 저가·고가와 몇 호가 차이가 날 수 있습니다.
- 임의 쿼리(`?t=...`)는 무시합니다. 호출 측이 캐시를 우회할 때 쓰세요.
- 순매수(KRX)는 포함하지 않습니다. 장중에는 전일 값이고, KRX 로그인을 건너뛰어 응답을 빠르게 하려는 의도입니다.

비밀값이 없고 내용이 공개 시세뿐이라 토큰을 두지 않았습니다. 주소가 알려지면 누구나 호출할 수 있다는 점(Vercel 함수 호출량)만 감안하세요. 막으려면 이 라우트를 `DASH_TOKEN` 검사 뒤로 옮기면 됩니다.

## 대시보드 접속

배포 주소(`https://<프로젝트>.vercel.app`)에 처음 한 번만 `https://<프로젝트>.vercel.app/?key=<DASH_TOKEN>` 으로 접속하면 쿠키(1년)가 저장되고 주소에서 토큰이 사라집니다. 브라우저를 바꾸거나 쿠키를 지우면 다시 토큰이 필요합니다.

## Slack 슬래시 커맨드 `/report`

예약 시각 외에, Slack 채널에서 `/report`(전체), `/report kr`(국내), `/report us`(해외)를 입력하면 바로 리포트가 **채널 전체에 공개**로 올라옵니다. 휴장일이면 요청한 사람에게만 사유가 보입니다.

설정 (봇 토큰은 필요 없고, 웹훅을 만든 같은 Slack 앱을 사용합니다):
1. <https://api.slack.com/apps> → 해당 앱 → **Slash Commands** → *Create New Command*
   - Command: `/report`, Request URL: `https://<프로젝트>.vercel.app/api/slack`, Short Description: 주식 리포트 전송
2. 앱을 워크스페이스에 **다시 설치** (Install App → Reinstall)
3. **Basic Information → App Credentials → Signing Secret**을 Vercel 환경변수로 등록 후 재배포
   ```bash
   vercel env add SLACK_SIGNING_SECRET production --sensitive
   ```
4. (선택) 본인과 `#briefo` 채널로 제한: 사용자 ID/채널 ID(Slack에서 프로필·채널 정보의 "ID 복사")를 `SLACK_ALLOWED_USER_ID`, `SLACK_ALLOWED_CHANNEL_ID`로 등록

동작: Slack은 3초 안에 응답을 요구하므로 서버가 먼저 "리포트를 만드는 중" 안내(본인에게만 보임)를 보내고, 완성되면 Slack이 준 `response_url`로 게시합니다. 서명(`X-Slack-Signature`)과 타임스탬프(5분 이내)를 검증하며, Slack의 재시도 요청은 무시합니다.

## Slack 리포트 일정

[cron-job.org](https://cron-job.org)의 작업 4개가 각자 시간대(`Asia/Seoul`, `America/New_York`)로 실행합니다. 서머타임은 스케줄러가 처리합니다.

| 리포트 | 시각 | 비고 |
|---|---|---|
| 국내 | 월~금 KST 09:30, 11:30, 13:30, 15:00 | 오늘 국내 데이터가 없으면(휴장일) 전송 생략 |
| 미국 | 장 시작 30분 후(10:00 ET), 마감 30분 전(15:30 ET) | 서버가 뉴욕 시각이 발송 시간대인지 한 번 더 확인하고, 휴장일은 데이터로 거름 |

미국은 마지막 장중 데이터가 90분 이내여야 전송하므로 휴장일·조기 폐장일은 자동으로 건너뜁니다. 한국 종목은 야후 시세가 지연되므로 09:30 리포트는 장 초반 값입니다.

`/api/report`는 요청을 받으면 먼저 `202 accepted`로 응답하고 뒤에서 리포트를 만들어 보냅니다(스케줄러의 응답 대기 제한 회피). 결과는 Vercel 로그에 남고, 끝까지 기다려 결과를 보려면 `&wait=1`을 붙입니다.

### cron-job.org 작업 등록

1. [cron-job.org](https://cron-job.org) 가입 → 콘솔 **Settings → API**에서 API 키 발급
2. 터미널에서 (키와 시크릿은 프롬프트로 입력해 히스토리에 남기지 않기)

```bash
read -s "CRONJOB_API_KEY?cron-job.org API 키: "; export CRONJOB_API_KEY
read -s "CRON_SECRET?CRON_SECRET: "; export CRON_SECRET     # Vercel에 등록한 값과 같아야 함
python3 scripts/cronjob_setup.py --base-url https://<프로젝트>.vercel.app
unset CRONJOB_API_KEY CRON_SECRET
```

   - `--dry-run`: 보낼 내용만 출력, `--delete`: 이 스크립트가 만든 작업(제목이 `stock-viewer `로 시작) 삭제. 다시 실행하면 같은 제목의 작업은 갱신됩니다.
   - 만들어지는 작업: 국내 09:30/11:30/13:30(KST), 국내 15:00(KST), 미국 10:00(ET), 미국 15:30(ET) — 모두 월~금, GET, `Authorization: Bearer <CRON_SECRET>` 헤더
3. cron-job.org 콘솔에서 작업별 실행 기록(성공 여부)을 볼 수 있습니다.

### 수동 실행
Slack에서 `/report`, 또는 GitHub → Actions → *Slack report* → **Run workflow** (`mode`: kr/us, `force`: true면 시간대·휴장 판단 무시).

## 새로 배포하기

```bash
npm i -g vercel          # 또는 brew install node 후
vercel login
vercel link --yes --project <프로젝트명>
# 위 "Vercel 환경변수 등록" 수행
vercel deploy --prod --yes
```

- `vercel link`는 이 GitHub 저장소를 프로젝트에 연결할 수 있으며, 연결되면 `main`에 push할 때마다 자동 배포됩니다.
- Python 런타임은 `pyproject.toml`의 `[tool.vercel] entrypoint`가 필요합니다.
- Vercel rewrite 이후에는 함수가 보는 경로가 `/api/index`가 되므로, 원래 경로는 `vercel.json`이 `?route=` 로 넘겨줍니다.
- 이후 cron-job.org 작업을 등록하고(위 "Slack 리포트 일정"), 수동 실행용 GitHub Secret/Variable(`CRON_SECRET`, `REPORT_BASE_URL`)을 설정합니다.

## 로컬 실행

macOS, Python 3.10 이상(Homebrew `python@3.12` 권장; uv로 설치한 Python은 일부 환경에서 코드 서명 문제가 있었고, 개발용 3.15는 numpy/pandas가 크래시할 수 있음)이 필요합니다.

```bash
git clone https://github.com/sangkwon/stock-viewer.git
cd stock-viewer
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/python stock_viewer.py        # http://localhost:8000  (인증 없음, 127.0.0.1 전용)

# Slack 리포트 수동 실행 (키체인의 웹훅 사용)
.venv/bin/python slack_report.py --mode kr --dry-run   # 전송하지 않고 내용만 출력
.venv/bin/python slack_report.py --mode us             # 실제 전송
```

| 옵션 | 설명 |
|---|---|
| `--mode kr\|us\|all` | 국내 / 해외 / 전부 |
| `--dry-run` | Slack으로 보내지 않고 내용만 출력 |
| `--force` | 휴장일 판단을 무시하고 전송 |
| `--scheduled` | 예약 실행과 같은 발송 시간대·신선도 판단 적용 |

## 종목 설정

`stock_viewer.py` 위쪽의 목록을 수정하고 push하면 웹과 Slack 리포트에 모두 반영됩니다.

| 변수 | 형식 | 설명 |
|---|---|---|
| `TICKERS` | `("005930.KS", "삼성전자")` | 국내 관심 종목. 코스피 `.KS`, 코스닥 `.KQ` |
| `MARKETS` | `("^KS11", "KOSPI", "KOSPI")` | 시장 지수 (순매수는 pykrx 시장명으로 조회) |
| `US_INDEXES` | `("^GSPC", "S&P 500", "^GSPC")` | 해외 지수 |
| `US_TICKERS` | `("NVDA", "엔비디아")` | 해외 종목/ETF (USD) |
| `MACROS` | `("GC=F", "금 (뉴욕 선물)", "USD/oz", 2, "")` | 금·유가·환율·금리 |
| `REFRESH_SECONDS` | `300` | 화면 자동 갱신 주기(초) |
| `QUOTE_CACHE_SECONDS` | `240` | 야후 조회 캐시(초). 서버리스에서는 인스턴스 메모리 캐시라 보조적 효과 |

## 문제 해결

| 증상 | 원인과 해결 |
|---|---|
| 대시보드에 "접근 권한이 없습니다" | 쿠키가 없습니다. `/?key=<DASH_TOKEN>` 으로 접속하세요. 토큰을 바꿨다면 재배포가 필요합니다. |
| 리포트 호출이 401 | Vercel `CRON_SECRET`과 cron-job.org 헤더(또는 GitHub Secret) 값이 다르거나, 값을 바꾼 뒤 재배포하지 않았습니다. |
| 예약 시각에 리포트가 안 옴 | cron-job.org 콘솔의 실행 기록(응답 코드)과 Vercel 로그(`[report kr]` 줄)를 확인하세요. 응답 `202`인데 Slack에 안 오면 "전송 생략" 사유가 로그에 있습니다. |
| `/report`가 `invalid signature` 또는 응답 없음 | `SLACK_SIGNING_SECRET` 값이 틀렸거나 등록 후 재배포하지 않았습니다. |
| 수동 Actions 실행은 성공인데 Slack에 안 옴 | 응답이 "전송 생략"(휴장일·발송 시간대 아님)일 수 있습니다. 실행 로그를 확인하세요. |
| 순매수가 `-` 이거나 비어 있음 | KRX 로그인 실패(환경변수/키체인 확인) 또는 ETF처럼 pykrx가 지원하지 않는 종목입니다. |
| 시세 카드가 가끔 오류로 표시 | 야후 응답 누락/제한입니다. 1회 재시도와 직전 값 사용으로 완화되어 있습니다. |
| 로컬 `Address already in use` | 포트 8000 사용 중. `pkill -f stock_viewer.py` 후 다시 실행하세요. |

## 주의

- 투자 조언이 아닌 **개인용 참고 도구**입니다. 야후 데이터는 지연·누락될 수 있고, 순매수 금액은 KRX 데이터 기준입니다.
- yfinance와 pykrx는 비공식 경로로 데이터를 가져오므로 제공처 사정으로 예고 없이 동작하지 않을 수 있습니다.
- Vercel Hobby 플랜은 비상업적 개인 용도 전용입니다.
- 이 저장소에는 비밀값이 없습니다. `.env`, 웹훅 URL, 비밀번호를 파일로 만들어 커밋하지 마세요.
