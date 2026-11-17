#!/usr/bin/env python3
"""주식 시세 리포트를 Slack 웹훅으로 전송.

    .venv/bin/python slack_report.py --mode kr        # 국내: 시장 + 관심 종목(순매수 포함)
    .venv/bin/python slack_report.py --mode us        # 해외: 해외 지수 + 글로벌 지표 + 해외 종목
    .venv/bin/python slack_report.py --mode all       # 전부
    .venv/bin/python slack_report.py --mode kr --dry-run   # 전송하지 않고 내용만 출력

웹훅 URL은 macOS 키체인(서비스명 slack-webhook-url)에서 읽습니다.
"""
import argparse
import contextlib
import datetime as dt
import io
import json
import subprocess
import sys
import urllib.request

# stock_viewer를 import하면 KRX 로그인 로그(로그인 ID 포함)가 출력되므로 숨긴다.
with contextlib.redirect_stdout(io.StringIO()):
    import stock_viewer as sv

KEYCHAIN_WEBHOOK = "slack-webhook-url"
BARS = "▁▂▃▄▅▆▇█"
WEEKDAY = "월화수목금토일"


def spark(pts, n=24):
    """장중 가격 → 유니코드 미니 차트"""
    prices = [p[1] for p in pts]
    if len(prices) < 2:
        return ""
    step = max(1, len(prices) // n)
    prices = [prices[i] for i in range(step - 1, len(prices), step)][-n:]
    lo, hi = min(prices), max(prices)
    if hi == lo:
        return BARS[3] * len(prices)
    return "".join(BARS[round((p - lo) / (hi - lo) * (len(BARS) - 1))] for p in prices)


def num(n, dec):
    return f"{n:,.{dec}f}"


def esc(t):
    """Slack mrkdwn 특수문자 이스케이프"""
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def eok(v):
    return f"{v / 1e8:+,.0f}억"


def quote_lines(q):
    if q.get("error"):
        return [f"*{esc(q['name'])}*  조회 실패"]
    dec = 0 if q.get("code") else q.get("dec", 2)
    suffix = q.get("suffix", "")
    arrow = "🔴▲" if q["ratio"] > 0 else "🔵▼" if q["ratio"] < 0 else "⚪"
    price = f"{q.get('prefix', '')}{num(q['price'], dec)}{suffix}"
    chg = f"{arrow} {num(abs(q['change']), dec)}{'%p' if suffix else ''} ({q['ratio']:+.2f}%)"
    s = spark(q.get("intraday") or [])
    head = f"*{esc(q['name'])}*  {price}  {chg}" + (f"  `{s}`" if s else "")
    lines = [head]
    inv = q.get("investors")
    if inv:
        d = inv["date"]
        lines.append(f"      외국인 {eok(inv['foreign'])} · 기관 {eok(inv['organ'])} · 개인 {eok(inv['individual'])}"
                     f"  _({d[4:6]}/{d[6:8]} 순매수)_")
    return lines


def last_ts(q):
    pts = q.get("intraday") or []
    return pts[-1][0] if pts else None


def stale_reason(mode, data, now):
    """휴장일 등 데이터가 갱신되지 않았으면 사유를 반환 (전송 생략)."""
    if mode == "kr":
        ts = last_ts(data["markets"][0])
        if ts is None:
            raise RuntimeError("KOSPI 장중 데이터를 받지 못했습니다")
        if dt.datetime.fromtimestamp(ts, sv.KST).date() != now.date():
            return "오늘 국내 시장 데이터가 없습니다 (휴장일로 추정)"
    elif mode == "us":
        ts = last_ts(data["us_indexes"][0])
        if ts is None:
            raise RuntimeError("미국 지수 장중 데이터를 받지 못했습니다")
        if now.timestamp() - ts > 20 * 3600:
            return "미국 장 데이터가 갱신되지 않았습니다 (휴장일로 추정)"
    return None


def build(mode, data, now):
    sections = []
    if mode in ("kr", "all"):
        sections.append(("🇰🇷 시장", data["markets"]))
        sections.append(("⭐ 관심 종목", data["stocks"]))
    if mode in ("us", "all"):
        sections.append(("🇺🇸 해외 지수", data["us_indexes"]))
        sections.append(("🌐 글로벌 지표", data["macros"]))
        sections.append(("💵 해외 종목 (USD)", data["us_stocks"]))
    title = {"kr": "국내 마감", "us": "미국 마감", "all": "종합"}[mode]
    head = f"📈 주식 시세 리포트 · {title} · {now:%m/%d}({WEEKDAY[now.weekday()]}) {now:%H:%M}"
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": head}}]
    for name, items in sections:
        lines = [l for q in items for l in quote_lines(q)]
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*{name}*\n" + "\n".join(lines)[:2900]}})
    return head, blocks


def post(text, blocks):
    url = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_WEBHOOK, "-w"],
                         capture_output=True, text=True, check=True).stdout.strip()
    req = urllib.request.Request(url, data=json.dumps({"text": text, "blocks": blocks}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status, r.read().decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["kr", "us", "all"], default="all")
    ap.add_argument("--dry-run", action="store_true", help="전송하지 않고 내용만 출력")
    ap.add_argument("--force", action="store_true", help="휴장일 판단을 무시하고 전송")
    args = ap.parse_args()

    now = dt.datetime.now(sv.KST)
    with contextlib.redirect_stdout(io.StringIO()):  # 라이브러리 로그 숨김
        data = sv.get_all()

    reason = None if args.force else stale_reason(args.mode, data, now)
    if reason:
        print(f"[{now:%F %T}] 전송 생략: {reason}")
        return 0
    text, blocks = build(args.mode, data, now)
    if args.dry_run:
        print(text)
        for b in blocks:
            if b["type"] == "section":
                print("\n" + b["text"]["text"])
        return 0
    status, body = post(text, blocks)
    print(f"[{now:%F %T}] Slack 전송 {status} {body}")
    return 0 if status == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
