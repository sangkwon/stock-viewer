#!/usr/bin/env python3
"""주식 시세 리포트를 Slack 웹훅으로 전송.

    .venv/bin/python slack_report.py --mode kr        # 국내: 시장 + 관심 종목 + 글로벌 지표
    .venv/bin/python slack_report.py --mode us        # 해외: 해외 지수 + 해외 종목
    .venv/bin/python slack_report.py --mode all       # 전부
    .venv/bin/python slack_report.py --mode kr --dry-run   # 전송하지 않고 내용만 출력

웹훅 URL은 환경변수 SLACK_WEBHOOK_URL(Vercel)이 있으면 그것을, 없으면 macOS 키체인(서비스명 slack-webhook-url)에서 읽습니다.
예약 실행(GitHub Actions → Vercel /api/report)은 run(..., scheduled=True)로 호출되며, 정해진 시간대가 아니거나
휴장일이면 전송하지 않습니다.
"""
import argparse
import contextlib
import datetime as dt
import io
import json
import os
import subprocess
import sys
import urllib.request

# stock_viewer를 import하면 KRX 로그인 로그(로그인 ID 포함)가 출력되므로 숨긴다.
with contextlib.redirect_stdout(io.StringIO()):
    import stock_viewer as sv

KEYCHAIN_WEBHOOK = "slack-webhook-url"
BARS = "▁▂▃▄▅▆▇█"
WEEKDAY = "월화수목금토일"
SCHEDULED_MAX_AGE = 90 * 60   # 예약 실행: 마지막 장중 데이터가 이 시간(초) 이내여야 전송 (휴장일·조기 폐장 걸러냄)
MANUAL_MAX_AGE = 20 * 3600    # 수동 실행(미국): 장 마감 후에도 보낼 수 있게 느슨하게


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
    """종목 한 개의 줄 목록: 시세 줄 + (있으면) 순매수 줄(외국인 / 기관 / 개인)"""
    if q.get("error"):
        return [f"*{esc(q['name'])}*  조회 실패"]
    dec = 0 if q.get("code") else q.get("dec", 2)
    suffix = q.get("suffix", "")
    arrow = "🔴▲" if q["ratio"] > 0 else "🔵▼" if q["ratio"] < 0 else "⚪"
    price = f"{q.get('prefix', '')}{num(q['price'], dec)}{suffix}"
    chg = f"{arrow} {num(abs(q['change']), dec)}{'%p' if suffix else ''} ({q['ratio']:+.2f}%)"
    s = spark(q.get("intraday") or [])
    lines = [f"*{esc(q['name'])}*  {price}  {chg}" + (f"  `{s}`" if s else "")]
    inv = q.get("investors")
    if inv:  # 외국인 / 기관 / 개인 순서
        lines.append(f"      {eok(inv['foreign'])} / {eok(inv['organ'])} / {eok(inv['individual'])}")
    return lines


def last_ts(q):
    pts = q.get("intraday") or []
    return pts[-1][0] if pts else None


def stale_reason(mode, data, now, max_age):
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
        if now.timestamp() - ts > max_age:
            return "미국 장 데이터가 최근에 갱신되지 않았습니다 (휴장일/장 시간 외로 추정)"
    return None


def scheduled_slot(mode, now):
    """예약 실행에서 지금 보내는 게 맞는지 판단. 반환: (제목 라벨, 건너뛸 사유)

    국내: 평일(KST)이면 통과 (휴장일은 데이터로 판단).
    미국: GitHub Actions는 서머타임을 모르므로 서머타임/표준시 두 시각을 모두 호출한다.
          뉴욕 현재 시각이 장 시작 30분 후(10:00) 또는 마감 30분 전(15:30) 근처일 때만 전송한다.
    """
    if mode == "kr":
        if now.weekday() >= 5:
            return None, "주말입니다"
        return "국내", None
    if mode == "us":
        ny = now.astimezone(sv.NY)
        if ny.weekday() >= 5:
            return None, "뉴욕 기준 주말입니다"
        m = ny.hour * 60 + ny.minute
        if 9 * 60 + 45 <= m <= 10 * 60 + 45:
            return "미국 장 시작 30분 후", None
        if 15 * 60 + 15 <= m <= 16 * 60 + 5:
            return "미국 마감 30분 전", None
        return None, f"뉴욕 시각 {ny:%H:%M} 은 발송 시간대(10:00, 15:30 근처)가 아닙니다"
    return "종합", None


def build(mode, data, now, label=None):
    sections = []
    if mode in ("kr", "all"):
        sections.append(("🇰🇷 시장", data["markets"]))
        sections.append(("⭐ 관심 종목", data["stocks"]))
        sections.append(("🌐 글로벌 지표", data["macros"]))
    if mode in ("us", "all"):
        sections.append(("🇺🇸 해외 지수", data["us_indexes"]))
        sections.append(("💵 해외 종목 (USD)", data["us_stocks"]))
    title = label or {"kr": "국내", "us": "미국", "all": "종합"}[mode]
    head = f"📈 주식 시세 리포트 · {title} · {now:%m/%d}({WEEKDAY[now.weekday()]}) {now:%H:%M}"
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": head}}]
    for name, items in sections:
        lines = [l for q in items for l in quote_lines(q)]
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*{name}*\n" + "\n".join(lines)[:2900]}})
    return head, blocks


def webhook_url():
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if url:
        return url.strip()
    return subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_WEBHOOK, "-w"],
                          capture_output=True, text=True, check=True).stdout.strip()


def post(text, blocks):
    req = urllib.request.Request(webhook_url(), data=json.dumps({"text": text, "blocks": blocks}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status, r.read().decode()


def post_response_url(url, text, blocks=None, ephemeral=False):
    """슬래시 커맨드의 response_url 로 게시한다 (기본: 채널 전체에 공개)."""
    body = {"response_type": "ephemeral" if ephemeral else "in_channel", "text": text}
    if blocks:
        body["blocks"] = blocks
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status, r.read().decode()


def run(mode, scheduled=False, force=False, dry_run=False, response_url=None):
    """리포트를 만들어 전송한다. 반환: (성공 여부, 결과 메시지)

    response_url 이 있으면(슬래시 커맨드) 웹훅 대신 그 주소로 게시한다."""
    now = dt.datetime.now(sv.KST)
    label = None
    if scheduled and not force:
        label, skip = scheduled_slot(mode, now)
        if skip:
            return True, f"[{now:%F %T}] 전송 생략: {skip}"
    parts = ("kr",) if mode == "kr" else ("us",) if mode == "us" else ("kr", "us")
    with contextlib.redirect_stdout(io.StringIO()):  # 라이브러리 로그 숨김
        data = sv.get_all(parts, investors=(mode != "us"))  # 순매수(KRX)는 국내 리포트에만
    if not force:
        max_age = SCHEDULED_MAX_AGE if scheduled else MANUAL_MAX_AGE
        reason = stale_reason(mode, data, now, max_age)
        if reason:
            return True, f"[{now:%F %T}] 전송 생략: {reason}"
    text, blocks = build(mode, data, now, label)
    if dry_run:
        out = [text] + ["\n" + b["text"]["text"] for b in blocks if b["type"] == "section"]
        return True, "\n".join(out)
    status, body = post_response_url(response_url, text, blocks) if response_url else post(text, blocks)
    return status == 200, f"[{now:%F %T}] Slack 전송 {status} {body}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["kr", "us", "all"], default="all")
    ap.add_argument("--dry-run", action="store_true", help="전송하지 않고 내용만 출력")
    ap.add_argument("--force", action="store_true", help="휴장일 판단을 무시하고 전송")
    ap.add_argument("--scheduled", action="store_true", help="예약 실행과 같은 발송 시간대/신선도 판단을 적용")
    args = ap.parse_args()
    ok, msg = run(args.mode, scheduled=args.scheduled, force=args.force, dry_run=args.dry_run)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
