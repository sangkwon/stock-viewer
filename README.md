# Stock Viewer

국내·해외 주식 시세를 웹 화면으로 보고, 정해진 시각에 Slack으로 요약 리포트를 보내는 개인용 도구입니다.

- **웹 화면** (`stock_viewer.py`): `http://localhost:8000` — 시장 지수, 해외 지수, 글로벌 지표(금·유가·환율·미국채 10년물), 관심 종목, 해외 종목의 현재가·전일 대비·장중 그래프, 국내 시장·종목의 외국인/기관/개인 순매수. 5분마다 자동 갱신.
- **Slack 리포트** (`slack_report.py`): 같은 데이터를 Slack 웹훅으로 전송. macOS `launchd`로 평일 정해진 시각에 자동 실행.

## 데이터 출처

| 데이터 | 출처 | 비고 |
|---|---|---|
| 현재가, 전일 종가, 장중 그래프 | [yfinance](https://github.com/ranaroussi/yfinance) (야후 파이낸스) | 비공식, 지연 시세일 수 있음 |
| 외국인·기관·개인 순매수 | [pykrx](https://github.com/sharebook-kr/pykrx) (KRX 정보데이터시스템) | **KRX 로그인 필요**, 장 마감 후 확정되는 값 |

## 요구 사항

- **macOS** (키체인과 launchd를 사용합니다. Linux/Windows는 코드 수정이 필요합니다.)
- **Python 3.10 이상** — pykrx의 로그인 지원 버전이 3.10 이상을 요구합니다.
  Homebrew로 설치한 Python을 권장합니다: `brew install python@3.12`
  > `uv`로 설치한 Python은 launchd에서 `OS_REASON_CODESIGNING`으로 종료되는 문제가 있었습니다. 예약 실행까지 쓸 계획이면 Homebrew Python으로 `.venv`를 만드세요. 개발용 버전(예: 3.15)은 numpy/pandas가 크래시할 수 있어 피하세요.
- KRX 정보데이터시스템 계정 ([data.krx.co.kr](https://data.krx.co.kr) 회원가입)
- (Slack 리포트용) Slack 앱의 Incoming Webhook URL

## 설치

```bash
git clone https://github.com/sangkwon/stock-viewer.git
cd stock-viewer

/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

그다음 아래 [자격 증명 저장](#자격-증명-저장-키체인)을 진행합니다.

## 자격 증명 저장 (키체인)

**모든 비밀값은 macOS 로그인 키체인(`~/Library/Keychains/login.keychain-db`)에만 저장합니다.** 코드, 설정 파일, 환경변수 파일, 로그, git 저장소 어디에도 비밀번호나 URL이 들어 있지 않습니다. 코드는 실행할 때 `security find-generic-password -w` 명령으로 키체인에서 값을 읽어 옵니다.

| 키체인 서비스명 (`-s`) | 계정 (`-a`) | 저장하는 값 (비밀번호 칸) | 필요한 기능 |
|---|---|---|---|
| `krx-pw` | **KRX 로그인 ID** | KRX 비밀번호 | 외국인·기관·개인 순매수 (웹, Slack 공통) |
| `slack-webhook-url` | 아무 이름 (`$USER` 등) | Slack Incoming Webhook URL (`https://hooks.slack.com/services/...`) | Slack 리포트 전송 |

> `krx-pw`는 **계정(`-a`) 값을 KRX 로그인 ID로 사용**합니다. 맥 사용자명이 아니라 KRX ID를 넣어야 합니다.
> `slack-webhook-url`은 계정 값을 쓰지 않으므로 아무 이름이나 괜찮습니다.

### 저장

`-w` 뒤에 값을 적지 않으면 프롬프트로 입력받습니다. 입력한 글자는 화면에 보이지 않고, 쉘 히스토리에도 남지 않습니다. (`-w`에 값을 직접 적으면 히스토리에 남으니 피하세요.)

```bash
# KRX 계정 — <KRX 로그인 ID> 자리에 본인 ID를 넣고, 프롬프트에 KRX 비밀번호 입력
security add-generic-password -a "<KRX 로그인 ID>" -s krx-pw -w

# Slack 웹훅 URL — 프롬프트에 URL 붙여넣기
security add-generic-password -a "$USER" -s slack-webhook-url -w
```

### 확인 / 수정 / 삭제

```bash
# 저장되어 있는지만 확인 (값은 출력하지 않음)
security find-generic-password -s krx-pw >/dev/null 2>&1 && echo "있음" || echo "없음"
security find-generic-password -s slack-webhook-url >/dev/null 2>&1 && echo "있음" || echo "없음"

# 값 변경: 같은 명령에 -U 를 붙여 덮어쓰기
security add-generic-password -U -a "<KRX 로그인 ID>" -s krx-pw -w

# 삭제
security delete-generic-password -s krx-pw
security delete-generic-password -s slack-webhook-url
```

### Slack 웹훅 URL 만들기

1. <https://api.slack.com/apps> → **Create New App** → *From scratch*
2. 왼쪽 메뉴 **Incoming Webhooks** → 토글 **On**
3. **Add New Webhook to Workspace** → 메시지를 받을 채널 선택
4. 생성된 URL을 복사해서 위 명령으로 `slack-webhook-url`에 저장

웹훅 URL은 비밀번호처럼 다루세요. URL을 아는 사람은 누구나 그 채널에 글을 쓸 수 있습니다. 유출되면 Slack 앱 설정에서 폐기하고 새로 만든 뒤 키체인 값을 `-U`로 덮어쓰세요.

### 키체인 접근 허용

처음 실행할 때 키체인 접근 허용 창이 뜰 수 있습니다. **허용**(또는 항상 허용)을 선택하세요. 예약 실행(launchd)은 로그인된 상태의 로그인 키체인을 사용하므로, 맥이 로그인되어 있어야 합니다.

## 사용법

### 웹 화면

```bash
.venv/bin/python stock_viewer.py
```

브라우저에서 <http://localhost:8000> 을 엽니다. 서버를 처음 켠 뒤 첫 조회는 KRX 로그인과 조회 때문에 10~20초 걸리고, 이후에는 캐시(4분) 덕분에 빠릅니다. 종료는 `Ctrl+C`입니다.

### Slack 리포트 (수동 실행)

```bash
.venv/bin/python slack_report.py --mode kr    # 국내: 시장 + 관심 종목(순매수 포함)
.venv/bin/python slack_report.py --mode us    # 해외: 해외 지수 + 글로벌 지표 + 해외 종목
.venv/bin/python slack_report.py --mode all   # 전부
```

| 옵션 | 설명 |
|---|---|
| `--dry-run` | Slack으로 보내지 않고 메시지 내용만 출력 (형식 확인용) |
| `--force` | 휴장일/장 시작 전이라 건너뛰는 판단을 무시하고 전송 |

휴장일(국내는 오늘 데이터가 없을 때, 미국은 마지막 데이터가 20시간 넘게 지났을 때)에는 전송을 건너뛰고 로그에 한 줄만 남깁니다.

#### 짧은 명령으로 쓰기 (선택)

`~/.zshrc`에 아래 함수를 추가하면 어느 폴더에서든 `report kr` 처럼 실행할 수 있습니다. 경로는 본인이 clone한 위치로 바꾸세요.

```zsh
report() {
  (cd /path/to/stock-viewer && PYTHONWARNINGS=ignore .venv/bin/python slack_report.py --mode "${1:-all}" "${@:2}")
}
```

### 예약 실행 (launchd)

```bash
./launchd/install.sh              # 등록
./launchd/install.sh --uninstall  # 해제
```

| 작업 | 실행 시각 | 비고 |
|---|---|---|
| 국내 리포트 (`--mode kr`) | 월~금 16:00 | 장 마감 직후라 순매수는 **잠정치**일 수 있습니다 |
| 미국 리포트 (`--mode us`) | 화~토 07:30 | 미국 장이 한국시간 새벽에 끝나므로 화~토입니다 |

시각을 바꾸려면 `launchd/install.sh`의 `jobs` 부분을 고치고 다시 실행하세요.
맥이 잠자기 중이었으면 깨어날 때 놓친 작업을 실행하지만, 꺼져 있거나 로그인되어 있지 않으면 실행되지 않습니다.
실행 결과는 `logs/slack_report_kr.log`, `logs/slack_report_us.log`에, 오류는 같은 폴더의 `*.err.log`에 남습니다.

## 종목 설정

`stock_viewer.py` 위쪽의 목록을 직접 수정하면 웹 화면과 Slack 리포트에 모두 반영됩니다.

| 변수 | 형식 | 설명 |
|---|---|---|
| `TICKERS` | `("005930.KS", "삼성전자")` | 국내 관심 종목. 코스피 `.KS`, 코스닥 `.KQ`. 이름은 직접 입력 |
| `MARKETS` | `("^KS11", "KOSPI", "KOSPI")` | 시장 지수 (순매수는 pykrx 시장명으로 조회) |
| `US_INDEXES` | `("^GSPC", "S&P 500", "^GSPC")` | 해외 지수 |
| `US_TICKERS` | `("NVDA", "엔비디아")` | 해외 종목/ETF (USD) |
| `MACROS` | `("GC=F", "금 (뉴욕 선물)", "USD/oz", 2, "")` | 금·유가·환율·금리 |
| `REFRESH_SECONDS` | `300` | 웹 화면 자동 갱신 주기(초) |
| `QUOTE_CACHE_SECONDS` | `240` | 야후 조회 캐시(초). 갱신 주기보다 짧게 유지 |

## 문제 해결

| 증상 | 원인과 해결 |
|---|---|
| 순매수가 `-` 로 나오거나 비어 있음 | KRX 로그인 실패(키체인 `krx-pw` 항목, 계정 값이 KRX ID인지 확인) 또는 ETF처럼 pykrx가 지원하지 않는 종목입니다. |
| `Address already in use` | 포트 8000을 이미 쓰는 서버가 있습니다. `pkill -f stock_viewer.py` 후 다시 실행하세요. |
| launchd 작업이 `exit -9` (`OS_REASON_CODESIGNING`) | `.venv`의 Python이 `uv` 등으로 설치된 것인지 확인하고, Homebrew Python으로 `.venv`를 다시 만드세요. |
| 시세 카드가 가끔 오류로 표시 | 야후 응답 누락/제한입니다. 4분 캐시와 1회 재시도, 직전 값 사용으로 완화되어 있습니다. |
| Slack에 아무것도 오지 않음 | `logs/*.log`에 `전송 생략` 사유(휴장일 등)나 `*.err.log`의 오류가 있는지 확인하세요. 장 시작 전 `--mode kr`은 건너뜁니다. |
| 키체인 접근 허용 창이 계속 뜸 | 창에서 *항상 허용*을 선택하세요. |

## 주의

- 투자 조언이 아닌 **개인용 참고 도구**입니다. 야후 데이터는 지연·누락될 수 있고, 순매수 금액은 KRX 데이터 기준입니다.
- yfinance와 pykrx는 비공식 경로로 데이터를 가져오므로 제공처 사정으로 예고 없이 동작하지 않을 수 있습니다.
- 이 저장소에는 비밀값이 없습니다. 그래도 `.env`, 웹훅 URL, 비밀번호를 파일로 만들어 커밋하지 마세요.
