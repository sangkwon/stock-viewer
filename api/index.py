"""Vercel 서버리스 진입점: 대시보드(/), 시세 API(/api/quotes), Slack 리포트(/api/report).

환경변수 (Vercel 프로젝트 설정):
  DASH_TOKEN         대시보드 접근 토큰. 처음 한 번 https://<주소>/?key=<토큰> 으로 접속하면 쿠키가 저장된다.
  CRON_SECRET        /api/report 호출용. 헤더 `Authorization: Bearer <값>` 이 맞아야 한다 (GitHub Actions가 사용).
  KRX_ID, KRX_PW     KRX 로그인 (순매수 조회)
  SLACK_WEBHOOK_URL  Slack Incoming Webhook
토큰/시크릿이 설정되지 않으면 해당 경로는 열리지 않는다 (fail closed).
"""
import contextlib
import hashlib
import hmac
import io
import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

COOKIE = "dash"


def _same(a, b):
    return hmac.compare_digest(str(a).encode(), str(b).encode())


def _cookie_value(token):
    return hashlib.sha256(("dashboard:" + token).encode()).hexdigest()


class handler(BaseHTTPRequestHandler):
    def _send(self, code, body=b"", ctype="text/plain", headers=()):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Cache-Control", "private, no-store")  # 인증이 필요한 응답이 CDN에 캐시되지 않게
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _cookie(self):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE:
                return v
        return ""

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/api/report":
            return self._report(q)

        token = os.environ.get("DASH_TOKEN")
        if not token:
            return self._send(503, "DASH_TOKEN 이 설정되지 않았습니다")
        want = _cookie_value(token)
        key = q.get("key", [""])[0]
        if key and _same(key, token):  # 토큰으로 접속 → 쿠키 저장 후 주소에서 토큰 제거
            cookie = f"{COOKIE}={want}; Max-Age=31536000; Path=/; HttpOnly; Secure; SameSite=Strict"
            return self._send(302, "", headers=[("Set-Cookie", cookie), ("Location", "/")])
        if not _same(self._cookie(), want):
            return self._send(401, "접근 권한이 없습니다")

        import stock_viewer as sv
        if u.path == "/api/quotes":
            with contextlib.redirect_stdout(io.StringIO()):  # pykrx 로그인 로그 숨김
                data = sv.get_all()
            return self._send(200, json.dumps(data, ensure_ascii=False), "application/json")
        if u.path in ("/", "/index.html", "/api/index"):
            return self._send(200, sv.PAGE.replace("__REFRESH__", str(sv.REFRESH_SECONDS)), "text/html")
        return self._send(404, "not found")

    def _report(self, q):
        secret = os.environ.get("CRON_SECRET")
        auth = self.headers.get("Authorization", "")
        if not secret or not _same(auth, f"Bearer {secret}"):
            return self._send(401, "unauthorized")
        mode = q.get("mode", [""])[0]
        if mode not in ("kr", "us"):
            return self._send(400, "mode 는 kr 또는 us")
        import slack_report
        try:
            ok, msg = slack_report.run(mode, scheduled=True, force=q.get("force", [""])[0] == "1")
        except Exception as e:  # noqa: BLE001
            return self._send(500, f"{type(e).__name__}: {e}")
        return self._send(200 if ok else 502, msg)
