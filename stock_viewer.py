#!/usr/bin/env python3
"""국내 주식 시세 + 투자자별 매매동향 뷰어 (yfinance + pykrx).

실행:
    .venv/bin/python stock_viewer.py   →  http://localhost:8000
- 현재가·전일종가: yfinance (야후 파이낸스, 지연 시세일 수 있음)
- 투자자별 순매수(외국인·기관·개인): pykrx (KRX 로그인 필요)
  KRX 계정은 환경변수(KRX_ID/KRX_PW, Vercel 배포용)가 있으면 그것을, 없으면 macOS 키체인에서 읽습니다
  (코드/파일에 저장하지 않음).
"""
import datetime as dt
import json
import os
import re
import subprocess
import time
from urllib.parse import quote
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 콜드 스타트 비용 줄이기 (Vercel 람다는 /tmp 만 쓰기 가능).
# MPLCONFIGDIR 가 없으면 matplotlib 이 import 될 때마다 폰트 캐시를 다시 만든다("generated new fontManager").
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import yfinance as yf

try:  # 없으면 매 호출마다 TzCache/CookieCache 생성 실패 로그와 함께 재조회가 일어난다
    yf.set_tz_cache_location("/tmp/py-yfinance")
except Exception:
    pass

# 키체인 항목: 서비스명 = KEYCHAIN_SERVICE, 계정(account) = KRX 로그인 ID, 암호 = KRX 비밀번호
KEYCHAIN_SERVICE = "krx-pw"


def load_krx_credentials():
    """KRX ID/PW를 pykrx가 쓰는 환경변수(KRX_ID/KRX_PW)에 준비한다. 이미 있으면(Vercel) 그대로 쓰고, 없으면 키체인에서 읽는다."""
    if os.environ.get("KRX_ID") and os.environ.get("KRX_PW"):
        return True
    try:
        meta = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE],
                              capture_output=True, text=True, check=True).stdout
        user = re.search(r'"acct"<blob>="(.*)"', meta).group(1)
        pw = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                            capture_output=True, text=True, check=True).stdout.rstrip("\n")
        os.environ["KRX_ID"], os.environ["KRX_PW"] = user, pw
        return True
    except Exception:
        return False


HAS_KRX_LOGIN = load_krx_credentials()  # pykrx import 전에 실행해야 함
# pykrx 는 import 비용이 크고(pandas·matplotlib) 순매수 조회에만 쓰므로 get_investors 안에서 지연 import 한다.

# ───────── 여기만 수정하세요: (야후 심볼, 표시 이름) ─────────
# 코스피는 .KS, 코스닥은 .KQ 를 붙입니다.
TICKERS = [
    ("005930.KS", "삼성전자"),
    ("000660.KS", "SK하이닉스"),
    ("009150.KS", "삼성전기"),
    ("035420.KS", "NAVER"),
    ("005380.KS", "현대차"),
    ("498400.KS", "KODEX 200타겟위클리커버드콜"),
    ("228810.KS", "TIGER 미디어컨텐츠"),
]
# 상단에 표시할 시장 지수: (야후 심볼, 표시 이름, pykrx 시장명)
MARKETS = [
    ("^KS11", "KOSPI", "KOSPI"),
    ("^KQ11", "KOSDAQ", "KOSDAQ"),
]
# 시장 아래 글로벌 지표: (야후 심볼, 표시 이름, 단위 설명, 소수점 자리수, 가격 뒤 기호)
MACROS = [
    ("GC=F", "금 (뉴욕 선물)", "USD/oz", 2, ""),
    ("BZ=F", "브렌트유", "USD/bbl", 2, ""),
    ("KRW=X", "원/달러 환율", "KRW", 2, ""),
    ("^TNX", "미국채 10년물 금리", "수익률", 3, "%"),
]
# 해외 지수: (야후 심볼, 표시 이름, 설명)
US_INDEXES = [
    ("^GSPC", "S&P 500", "^GSPC"),
    ("^NDX", "나스닥 100", "^NDX"),
    ("^DJI", "다우존스", "^DJI"),
    ("^SOX", "필라델피아 반도체", "^SOX"),
]
# 해외 종목/ETF: (야후 심볼, 표시 이름)  — 가격은 USD
US_TICKERS = [
    ("AIPO", "AIPO (Defiance AI & Power Infra ETF)"),
    ("QTUM", "QTUM (Defiance Quantum ETF)"),
    ("NVDA", "엔비디아"),
    ("MU", "마이크론"),
    ("GOOG", "알파벳 (GOOG)"),
]
# 카드 클릭 시 새 창으로 여는 야후 파이낸스 시세 페이지 (모든 카드 공통, 위 목록의 야후 심볼 사용)
YAHOO_URL = "https://finance.yahoo.com/quote/{symbol}"
PORT = 8000
REFRESH_SECONDS = 300  # 화면 자동 새로고침 주기 (초) — 5분
INVESTOR_CACHE_SECONDS = 300  # KRX 조회는 5분간 캐시
# ───────────────────────────────────────────────────────────

_inv_cache = {}
# 야후 조회 캐시. 화면 자동 갱신(300초)보다 약간 짧게 잡아야 갱신 때마다 새 값을 받음
QUOTE_CACHE_SECONDS = 240
PREV_CACHE_SECONDS = 1800  # 전일 종가는 세션 중에 바뀌지 않는다
INDEX_PREV_CACHE_SECONDS = 21600  # KRX 지수 전일 종가: 하루에 한 번만 바뀐다
# 야후 지수 일봉에 직전 세션이 누락돼서 전일 종가를 KRX 에서 받는다 (아래 _krx_index_prev_close).
INDEX_KRX_TICKER = {"KOSPI": "1001", "KOSDAQ": "2001"}
_yf_cache = {}


def cached(key, ttl, fn):
    """ttl초 동안 캐시. 조회가 실패하면 1회 재시도하고, 그래도 실패하면 직전 값(오래돼도)을 반환."""
    hit = _yf_cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    err = None
    for attempt in range(2):
        try:
            val = fn()
            _yf_cache[key] = (time.time(), val)
            return val
        except Exception as e:  # 예: yfinance의 currentTradingPeriod KeyError (야후 응답 누락/제한)
            err = e
            if attempt == 0:
                time.sleep(1)
    if hit:
        return hit[1]
    raise err


def _daily_tail(symbol):
    """일봉 마지막 두 봉: ((마지막 종가, 날짜), (직전 종가, 날짜), 거래소 tzinfo).

    auto_adjust=False 가 중요하다. 기본값(True)은 배당락을 반영한 수정주가를 돌려주는데,
    실측(2026-11-27) SK하이닉스 전일 종가가 1,010,437 원으로 나왔다. 1,000원 단위가 아니라
    체결가가 될 수 없는 값이고 실제는 1,030,000 원, 등락률이 2%p 어긋났다(+4.21% 대 +2.23%).
    """
    def fetch():
        h = yf.Ticker(symbol).history(period="1mo", interval="1d", auto_adjust=False)["Close"].dropna()
        if len(h) < 2:
            raise ValueError("일봉이 2개 미만")
        items = list(h.items())
        (i1, v1), (i0, v0) = items[-1], items[-2]
        return ((float(v1), i1.date().isoformat()), (float(v0), i0.date().isoformat()), i1.tzinfo)
    return cached(("daily", symbol), PREV_CACHE_SECONDS, fetch)


def _prev_close(symbol, price, fallback):
    """(전일 종가, 그 거래일 날짜).

    fast_info["previousClose"] 는 야후가 세션 전환 때 잘못 채우는 경우가 있어 쓰지 않는다.
    실측(2026-11-24): KOSPI 를 4,740.97 로 반환했으나 거래소 확정치는 4,708.54 였다.

    핵심은 '현재가(lastPrice)의 세션이 일봉에 들어있는지'다. 들어있으면 그 직전 봉이 전일
    종가이고, 아직 없으면 일봉 마지막 봉 자체가 전일 종가다. 야후는 국내 지수의 일봉을 늦게
    붙여서 개장 전에 세션이 밀린다 — 실측(2026-11-27 07:22) KOSPI 현재가는 11/26 종가인데
    일봉 마지막 봉은 11/25 였다. 무조건 iloc[-2] 를 쓰면 두 세션 밀린 값이 나온다.

    판별은 두 단계다. (a) 마지막 봉의 날짜가 거래소 현지 기준 오늘이면 그 봉이 현재 세션이다.
    (b) 아니면 현재가가 마지막 봉 종가와 사실상 같은지 본다. 같으면 그 봉이 막 끝난 현재
    세션이고(미국 개장 전 등), 다르면 현재가의 세션이 아직 일봉에 없다(국내 지수 개장 전).
    """
    try:
        (last_v, last_d), (prev_v, prev_d), tz = _daily_tail(symbol)
    except Exception:
        return float(fallback), None  # 날짜 없음 = 보정 실패, fast_info 값 그대로
    if tz is not None and last_d == dt.datetime.now(tz).date().isoformat():
        return prev_v, prev_d  # (a) 마지막 봉 = 진행 중인(또는 오늘 끝난) 세션
    if abs(price - last_v) <= abs(last_v) * 0.001:
        return prev_v, prev_d  # (b) 마지막 봉 = 현재가의 세션
    return last_v, last_d      # (b) 현재가의 세션이 일봉에 없음 → 마지막 봉이 전일 종가


def _krx_index_prev_close(market):
    """(전일 종가, 거래일) — KRX 지수 일별 종가. 실패하면 None 을 돌려 야후 값을 쓰게 한다.

    야후는 국내 '지수'의 일봉에 직전 세션을 아예 붙이지 않는 경우가 있다. 실측(2026-11-27
    09:36, 장중) 지수 일봉의 마지막 봉이 11/25 여서 11/26 종가(KOSPI 4,711.21)를 어디서도
    꺼낼 수 없었고, 등락률이 +0.19%(11/25 기준)로 나왔다. 실제는 +0.19% 가 아니라 11/26
    기준이어야 한다. 같은 시각 '종목' 일봉에는 11/26 이 정상으로 있었다 — 지수에 국한된 결손이다.

    KRX 일별 시세는 끝난 세션만 담으므로 판별이 단순하다. 마지막 날짜가 거래소 기준 오늘이면
    (장 마감 후 게시됨) 그 직전이 전일 종가고, 아니면 마지막 날짜가 곧 전일 종가다.
    """
    ticker = INDEX_KRX_TICKER.get(market)
    if not ticker or not HAS_KRX_LOGIN:
        return None

    def fetch():
        from pykrx import stock  # 지연 import: pandas·matplotlib 를 끌고 온다
        fn = next((getattr(stock, n) for n in ("get_index_ohlcv", "get_index_ohlcv_by_date")
                   if hasattr(stock, n)), None)
        if fn is None:
            raise AttributeError("pykrx 에 지수 OHLCV 함수가 없음")
        now = dt.datetime.now(KST)
        df = fn((now - dt.timedelta(days=20)).strftime("%Y%m%d"), now.strftime("%Y%m%d"), ticker)
        col = df["종가"] if "종가" in df.columns else df.iloc[:, 3]  # OHLC 순서상 4번째가 종가
        col = col.dropna()
        if len(col) < 2:
            raise ValueError("KRX 지수 일별 시세가 2개 미만")
        dates = [i.date().isoformat() for i in col.index]
        if dates[-1] == now.date().isoformat():
            return float(col.iloc[-2]), dates[-2]  # 오늘 치가 이미 게시됨
        return float(col.iloc[-1]), dates[-1]      # 마지막 세션이 전일

    try:
        return cached(("krxidx", ticker), INDEX_PREV_CACHE_SECONDS, fetch)
    except Exception:
        return None


def get_price(symbol):
    """(현재가, 전일종가, 전일종가 거래일)"""
    def fetch():
        fi = yf.Ticker(symbol).fast_info
        return float(fi["lastPrice"]), float(fi["previousClose"])
    price, fi_prev = cached(("price", symbol), QUOTE_CACHE_SECONDS, fetch)
    prev, prev_date = _prev_close(symbol, price, fi_prev)
    return price, prev, prev_date


KST = ZoneInfo("Asia/Seoul")
NY = ZoneInfo("America/New_York")


def _fetch_intraday(symbol, macro, us=False, long=False):
    if us:  # 미국 정규장 5분봉, x축은 (최근 거래일) 09:30~16:00 뉴욕시간 고정
        h = yf.Ticker(symbol).history(period="1d", interval="5m", auto_adjust=False)["Close"].dropna()
        pts = [[int(t.timestamp()), round(float(v), 2)] for t, v in h.items()]
        if pts:
            d = dt.datetime.fromtimestamp(pts[-1][0], NY).replace(hour=9, minute=30, second=0, microsecond=0)
            domain = [int(d.timestamp()), int(d.timestamp()) + 390 * 60]
    elif macro and long:  # 장기 추이: 최근 3개월 일봉, x축은 데이터 범위
        h = yf.Ticker(symbol).history(period="3mo", interval="1d", auto_adjust=False)["Close"].dropna()
        pts = [[int(t.timestamp()), round(float(v), 4)] for t, v in h.items()]
        if pts:
            domain = [pts[0][0], pts[-1][0]]
    elif macro:
        h = yf.Ticker(symbol).history(period="2d", interval="15m", auto_adjust=False)["Close"].dropna()
        pts = [[int(t.timestamp()), round(float(v), 4)] for t, v in h.items()]
        if pts:
            pts = [p for p in pts if p[0] >= pts[-1][0] - 86400]
            domain = [pts[0][0], pts[-1][0]]
    else:
        h = yf.Ticker(symbol).history(period="1d", interval="1m", auto_adjust=False)["Close"].dropna()
        pts = [[int(t.timestamp()), round(float(v), 2)] for t, v in h.items()]
        if pts:
            d = dt.datetime.fromtimestamp(pts[-1][0], KST).replace(hour=9, minute=0, second=0, microsecond=0)
            domain = [int(d.timestamp()), int(d.timestamp()) + 390 * 60]
    if not pts:
        raise ValueError("빈 응답")  # 빈 값은 캐시하지 않음
    return pts, domain


def get_intraday(symbol, macro=False, us=False, long=False):
    """장중 추이. 반환: (points, domain)
    points = [[epoch초, 가격], ...], domain = [x축 시작, x축 끝] (epoch초)
    - 국내(기본): 오늘 1분봉, x축은 09:00~15:30 고정
    - macro: 최근 24시간 15분봉, x축은 데이터 범위 (long=True 이면 최근 3개월 일봉)
    """
    try:
        return cached(("intra", symbol, macro, us, long), QUOTE_CACHE_SECONDS,
                      lambda: _fetch_intraday(symbol, macro, us, long))
    except Exception:
        return [], [0, 0]


def get_investors(code):
    """pykrx로 최근 거래일의 투자자별 순매수 거래대금(원). 반환: (데이터 또는 None, 오류 사유)"""
    if not HAS_KRX_LOGIN:
        return None, f"KRX 계정을 찾지 못했습니다 (환경변수 KRX_ID/KRX_PW 또는 키체인 '{KEYCHAIN_SERVICE}')"
    hit = _inv_cache.get(code)
    if hit and time.time() - hit[0] < INVESTOR_CACHE_SECONDS:
        return hit[1]
    try:
        today = dt.date.today()
        from pykrx import stock  # 지연 import: 순매수 조회에만 필요
        df = stock.get_market_trading_value_by_date(
            (today - dt.timedelta(days=10)).strftime("%Y%m%d"),
            today.strftime("%Y%m%d"), code)
        if df is None or df.empty:
            result = (None, "-")
        else:
            row = df.iloc[-1]
            result = ({
                "date": df.index[-1].strftime("%Y%m%d"),
                "foreign": float(row["외국인합계"]),
                "organ": float(row["기관합계"]),
                "individual": float(row["개인"]),
            }, None)
    except Exception as e:
        result = (None, f"pykrx 오류: {e}")
    _inv_cache[code] = (time.time(), result)
    return result


def yahoo_url(symbol):
    return YAHOO_URL.format(symbol=quote(symbol, safe=""))  # ^KS11 → %5EKS11, GC=F → GC%3DF


def get_quote(item, investors=True):
    symbol, name = item
    code = symbol[:6]
    out = {"code": code, "name": name, "url": yahoo_url(symbol)}
    try:
        price, prev, prev_date = get_price(symbol)
        out.update(price=price, prev_close=prev, prev_date=prev_date,
                   change=price - prev, ratio=(price - prev) / prev * 100)
    except Exception as e:
        out["error"] = f"시세 조회 실패: {e}"
        return out
    out["intraday"], out["domain"] = get_intraday(symbol)
    if investors:
        out["investors"], out["investors_error"] = get_investors(code)
    return out


def get_market(item, investors=True):
    symbol, name, market = item
    out = {"name": name, "url": yahoo_url(symbol)}
    try:
        price, prev, prev_date = get_price(symbol)
        krx = _krx_index_prev_close(market)  # 야후 지수 일봉은 직전 세션이 빠지므로 KRX 우선
        if krx:
            prev, prev_date = krx
        out.update(price=price, prev_close=prev, prev_date=prev_date,
                   change=price - prev, ratio=(price - prev) / prev * 100)
    except Exception as e:
        out["error"] = f"지수 조회 실패: {e}"
        return out
    out["intraday"], out["domain"] = get_intraday(symbol)
    if investors:
        out["investors"], out["investors_error"] = get_investors(market)  # 시장 전체 순매수
    return out


def get_macro(item, long=False):
    symbol, name, unit, dec, suffix = item
    out = {"name": name, "sub": unit, "dec": dec, "suffix": suffix, "url": yahoo_url(symbol)}
    try:
        price, prev, prev_date = get_price(symbol)
        out.update(price=price, prev_close=prev, prev_date=prev_date,
                   change=price - prev, ratio=(price - prev) / prev * 100)
    except Exception as e:
        out["error"] = f"조회 실패: {e}"
        return out
    out["intraday"], out["domain"] = get_intraday(symbol, macro=True, long=long)
    out["macro"] = True
    out["long"] = long  # True: 3개월 일봉 차트
    return out


def get_us(item, is_index):
    symbol, name = item[0], item[1]
    out = {"name": name, "sub": item[2] if is_index else symbol, "dec": 2,
           "prefix": "" if is_index else "$", "url": yahoo_url(symbol)}
    try:
        price, prev, prev_date = get_price(symbol)
        out.update(price=price, prev_close=prev, prev_date=prev_date,
                   change=price - prev, ratio=(price - prev) / prev * 100)
    except Exception as e:
        out["error"] = f"조회 실패: {e}"
        return out
    out["intraday"], out["domain"] = get_intraday(symbol, us=True)
    out["macro"] = True  # 라벨에 날짜 포함 (한국시간 기준)
    return out


def get_all(parts=("kr", "us"), investors=True, stock_investors=True, macro_long=True):
    """parts: "kr" = 시장+관심 종목+글로벌 지표, "us" = 해외 지수+해외 종목. 요청하지 않은 항목은 빈 목록.
    investors=False 이면 KRX(순매수) 조회를 모두 건너뛰고, stock_investors=False 이면 개별 종목만 건너뜁니다
    (시장 지수의 순매수는 유지). Slack 리포트는 지수 순매수만 보냅니다.
    macro_long=True 이면 글로벌 지표 차트가 3개월 일봉(뷰어), False 이면 최근 24시간(Slack 리포트)."""
    kr, us = "kr" in parts, "us" in parts
    with ThreadPoolExecutor(max_workers=4) as ex:
        markets = ex.map(lambda i: get_market(i, investors), MARKETS if kr else [])
        stocks = ex.map(lambda i: get_quote(i, investors and stock_investors), TICKERS if kr else [])
        macros = ex.map(lambda i: get_macro(i, macro_long), MACROS if kr else [])
        us_idx = ex.map(lambda i: get_us(i, True), US_INDEXES if us else [])
        us_stocks = ex.map(lambda i: get_us(i, False), US_TICKERS if us else [])
        return {"markets": list(markets), "us_indexes": list(us_idx), "macros": list(macros),
                "stocks": list(stocks), "us_stocks": list(us_stocks)}


PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>주식 시세</title>
<style>
:root{--bg:#f5f5fa;--card:#fff;--fg:#222;--sub:#777;--line:#e6e6ef;--up:#e5303f;--down:#1f6feb}
@media (prefers-color-scheme:dark){:root{--bg:#1b1b27;--card:#262636;--fg:#eee;--sub:#9a9ab0;--line:#37374c;--up:#ff6b78;--down:#6aa5ff}}
body{margin:0;padding:20px;background:var(--bg);color:var(--fg);font-family:system-ui,sans-serif}
h1{font-size:1.3rem;margin:0 0 4px}
#meta{color:var(--sub);font-size:.85rem;margin-bottom:16px}
#markets,#macros,#usidx,#usstk{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px;margin-bottom:24px}
.card.mkt{border-left:4px solid var(--sub)}
h2{font-size:1rem;margin:0 0 10px;color:var(--sub);font-weight:600}
#grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px;margin-bottom:24px}
.card{background:var(--card);border-radius:14px;padding:16px;box-shadow:0 1px 4px #0002}
.name{font-weight:600}.code{color:var(--sub);font-size:.8rem;margin-left:6px}
.price{font-size:2rem;font-weight:700;margin:6px 0 0;font-variant-numeric:tabular-nums}
.chg{font-size:1rem;font-variant-numeric:tabular-nums}
.up{color:var(--up)}.down{color:var(--down)}.flat{color:var(--sub)}
.prev{color:var(--sub);font-size:.8rem;margin-top:2px}
.flow{display:grid;grid-template-columns:repeat(3,1fr);gap:4px;margin-top:8px;padding-top:6px;border-top:1px solid var(--line);font-variant-numeric:tabular-nums}
.flow div{text-align:center;white-space:nowrap}
.flow small{color:var(--sub);font-size:.72rem;margin-right:3px}
.flow b{font-size:.8rem;font-weight:600}
.cap{color:var(--sub);font-size:.75rem;margin-top:6px}
.spark{width:100%;height:72px;margin-top:10px;display:block}
.spark .ax{fill:var(--sub);font-size:9px}

a.card{display:block;color:inherit;text-decoration:none}
a.card:hover{box-shadow:0 2px 10px #0004}
.err{color:var(--up)}
</style></head><body>
<h1>📈 주식 시세</h1><div id="meta">불러오는 중…</div>
<h2>시장</h2><div id="markets"></div>
<h2>관심 종목</h2><div id="grid"></div>
<h2>해외 지수</h2><div id="usidx"></div>
<h2>해외 종목 (USD)</h2><div id="usstk"></div>
<h2>글로벌 지표</h2><div id="macros"></div>
<script>
const REFRESH=__REFRESH__;
const fmt=n=>Math.round(n).toLocaleString("ko-KR");
const sign=n=>n>0?"+":"";
const cls=n=>n>0?"up":n<0?"down":"flat";
const eok=n=>sign(n)+(n/1e8).toLocaleString("ko-KR",{maximumFractionDigits:0})+"억";
const md=d=>d?`${d.slice(4,6)}/${d.slice(6,8)}`:"";
const kst=sec=>new Date(sec*1000).toLocaleString("sv-SE",{timeZone:"Asia/Seoul"}); // "2026-11-17 14:59:00"
const hm=sec=>kst(sec).slice(11,16), mdhm=sec=>kst(sec).slice(5,16).replace("-","/"), md_=sec=>kst(sec).slice(5,10).replace("-","/");
function spark(q,f){
  const pts=q.intraday||[], [d0,d1]=q.domain||[0,0];
  if(pts.length<2||d1<=d0)return `<div class="cap">추이 데이터 없음</div>`;
  const W=300,H=60,PT=4,PB=4;
  const ref=q.long?pts[0][1]:q.prev_close; // 3개월 차트의 점선 기준은 3개월 전 값
  const ys=pts.map(p=>p[1]).concat(ref);
  let lo=Math.min(...ys),hi=Math.max(...ys);if(hi===lo){hi+=1;lo-=1}
  const X=t=>(t-d0)/(d1-d0)*W, Y=v=>PT+(hi-v)/(hi-lo)*(H-PT-PB);
  const line=pts.map((p,i)=>`${i?"L":"M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join("");
  const last=pts[pts.length-1], c=q.ratio>0?"var(--up)":q.ratio<0?"var(--down)":"var(--sub)";
  const area=`${line}L${X(last[0]).toFixed(1)},${H}L${X(pts[0][0]).toFixed(1)},${H}Z`;
  const py=Y(ref).toFixed(1);
  const L=q.long?md_:q.macro?mdhm:hm; // 3개월 차트는 날짜만, 24시간 지표는 날짜+시각
  return `<svg class="spark" viewBox="0 0 ${W} ${H+12}" preserveAspectRatio="none">
    <path d="${area}" fill="${c}" opacity=".12"/>
    <line x1="0" x2="${W}" y1="${py}" y2="${py}" stroke="var(--sub)" stroke-width=".6" stroke-dasharray="3 3"/>
    <path d="${line}" fill="none" stroke="${c}" stroke-width="1.4" vector-effect="non-scaling-stroke"/>
    <circle cx="${X(last[0]).toFixed(1)}" cy="${Y(last[1]).toFixed(1)}" r="2.2" fill="${c}"/>
    <text class="ax" x="0" y="${H+10}">${L(d0)}</text>
    <text class="ax" x="${W/2}" y="${H+10}" text-anchor="middle">- - ${q.long?"3개월 전":"전일 종가"} · 최근 ${L(last[0])}</text>
    <text class="ax" x="${W}" y="${H+10}" text-anchor="end">${L(d1)}</text></svg>`;
}
function card(q){
  const dec=q.dec??2;
  const f=q.code?fmt:(n=>n.toLocaleString("ko-KR",{minimumFractionDigits:dec,maximumFractionDigits:dec})); // 종목은 정수, 지수·지표는 소수
  if(q.error)return `<div class="card"><span class="name">${q.name||q.code}</span><div class="err">불러오기 실패: ${q.error}</div></div>`;
  const arrow=q.ratio>0?"▲":q.ratio<0?"▼":"";
  let inv="";
  if(q.investors){
    const cells=[["외국인","foreign"],["기관","organ"],["개인","individual"]].map(([l,k])=>{
      const v=q.investors[k];
      return `<div><small>${l}</small><b class="${cls(v)}">${eok(v)}</b></div>`;
    }).join("");
    inv=`<div class="flow" title="${md(q.investors.date)} 순매수 금액">${cells}</div>`;
  }else if(q.investors_error){
    // 데이터가 없어도 같은 구분선·같은 높이로 표시 (사유는 툴팁)
    const cells=["외국인","기관","개인"].map(l=>`<div><small>${l}</small><b class="flat">-</b></div>`).join("");
    inv=`<div class="flow" title="${q.investors_error}">${cells}</div>`;
  }
  const cls0=`card${q.code?"":" mkt"}`;
  const open=q.url?`<a class="${cls0}" href="${q.url}" target="_blank" rel="noopener noreferrer" title="야후 파이낸스에서 보기">`:`<div class="${cls0}">`;
  return `${open}
    <div><span class="name">${q.name}</span><span class="code">${q.code||q.sub||""}</span></div>
    <div class="price ${cls(q.ratio)}">${q.prefix||""}${f(q.price)}${q.suffix||""}</div>
    <div class="chg ${cls(q.ratio)}">${arrow} ${f(Math.abs(q.change))}${q.suffix?"%p":""} (${sign(q.ratio)}${q.ratio.toFixed(2)}%)</div>
    <div class="prev">전일 종가 ${f(q.prev_close)}</div>${spark(q)}${inv}${q.url?"</a>":"</div>"}`;
}
async function load(){
  try{
    const r=await fetch("/api/quotes");const data=await r.json();
    document.getElementById("markets").innerHTML=data.markets.map(card).join("");
    document.getElementById("macros").innerHTML=data.macros.map(card).join("");
    document.getElementById("usidx").innerHTML=data.us_indexes.map(card).join("");
    document.getElementById("usstk").innerHTML=data.us_stocks.map(card).join("");
    document.getElementById("grid").innerHTML=data.stocks.map(card).join("");
    document.getElementById("meta").textContent=`갱신 ${new Date().toLocaleTimeString("ko-KR")} · ${REFRESH>=60?REFRESH/60+"분":REFRESH+"초"}마다 자동 갱신`;
    lastLoad=Date.now();
  }catch(e){document.getElementById("meta").textContent="서버 연결 실패: "+e}
}
let lastLoad=0;
load();setInterval(load,REFRESH*1000);
// 탭이 백그라운드에 있다가 돌아오면(타이머가 지연될 수 있음) 주기가 지났을 때 즉시 갱신
document.addEventListener("visibilitychange",()=>{
  if(!document.hidden&&Date.now()-lastLoad>REFRESH*1000)load();
});
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/quotes"):
            body, ctype = json.dumps(get_all(), ensure_ascii=False).encode(), "application/json"
        elif self.path in ("/", "/index.html"):
            body, ctype = PAGE.replace("__REFRESH__", str(REFRESH_SECONDS)).encode(), "text/html"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"http://localhost:{PORT}  (Ctrl+C 로 종료)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
