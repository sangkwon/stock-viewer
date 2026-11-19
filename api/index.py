"""Vercel 서버리스 진입점: 대시보드(/), 시세 API(/api/quotes), Slack 리포트(/api/report), 슬래시 커맨드(/api/slack).

환경변수 (Vercel 프로젝트 설정):
  DASH_TOKEN         대시보드 접근 토큰. 처음 한 번 https://<주소>/?key=<토큰> 으로 접속하면 쿠키가 저장된다.
  CRON_SECRET        /api/report 호출용. 헤더 `Authorization: Bearer <값>` 또는 `X-Cron-Secret: <값>` 이 맞아야 한다 (cron-job.org, 수동 Actions 실행이 사용).
  KRX_ID, KRX_PW     KRX 로그인 (순매수 조회)
  SLACK_WEBHOOK_URL  Slack Incoming Webhook
  SLACK_SIGNING_SECRET  슬래시 커맨드(/report) 요청 서명 검증용 (Slack 앱 Basic Information)
  SLACK_ALLOWED_USER_ID, SLACK_ALLOWED_CHANNEL_ID  (선택) 지정하면 해당 사용자/채널의 커맨드만 허용
토큰/시크릿이 설정되지 않으면 해당 경로는 열리지 않는다 (fail closed).
"""
import contextlib
import hashlib
import hmac
import io
import json
import os
import sys
import time
import traceback
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
        self.send_header("Content-Length", str(len(body)))  # 응답 본문이 끝났음을 알려 클라이언트(Slack)가 바로 받게 함
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
        # vercel.json 의 rewrite 가 원래 경로를 ?route= 로 넘겨준다 (rewrite 뒤에는 self.path 가 /api/index 가 됨)
        route = q.get("route", [""])[0] or u.path
        if route in ("report", "/api/report"):
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
        if route in ("quotes", "/api/quotes"):
            with contextlib.redirect_stdout(io.StringIO()):  # pykrx 로그인 로그 숨김
                data = sv.get_all()
            return self._send(200, json.dumps(data, ensure_ascii=False), "application/json")
        if route in ("home", "/", "/index.html", "/api/index"):
            return self._send(200, sv.PAGE.replace("__REFRESH__", str(sv.REFRESH_SECONDS)), "text/html")
        return self._send(404, "not found")

    def _report(self, q):
        secret = os.environ.get("CRON_SECRET")
        auth = self.headers.get("Authorization", "")
        alt = self.headers.get("X-Cron-Secret", "")  # Authorization 헤더를 못 쓰는 스케줄러용 대체 헤더
        if not secret or not (_same(auth, f"Bearer {secret}") or _same(alt, secret)):
            # 값은 남기지 않고 길이만 기록해 원인(헤더 미전달 / 값 불일치)을 가릴 수 있게 한다
            print(f"[report 401] secret_set={bool(secret)} auth_len={len(auth)} x_cron_secret_len={len(alt)} "
                  f"ua={self.headers.get('User-Agent', '')[:40]!r}", file=sys.stderr)
            return self._send(401, "unauthorized")
        mode = q.get("mode", [""])[0]
        if mode not in ("kr", "us"):
            return self._send(400, "mode 는 kr 또는 us")
        force = q.get("force", [""])[0] == "1"
        if q.get("wait", [""])[0] != "1":
            # 외부 스케줄러(cron-job.org)의 응답 대기 제한을 피하려고 먼저 접수 응답을 보내고, 이어서 리포트를 만들어 전송한다.
            # 결과는 Vercel 로그에 남는다. (?wait=1 이면 끝날 때까지 기다렸다가 결과를 응답으로 돌려줌)
            self._send(202, "accepted")
            try:
                self.wfile.flush()
            except Exception:  # noqa: BLE001
                pass
            try:
                import slack_report  # 무거운 import(pandas 등)는 접수 응답을 보낸 뒤에
                ok, msg = slack_report.run(mode, scheduled=True, force=force)
                print(f"[report {mode}] ok={ok} {msg}", file=sys.stderr)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            return
        try:
            import slack_report
            ok, msg = slack_report.run(mode, scheduled=True, force=force)
        except Exception as e:  # noqa: BLE001
            return self._send(500, f"{type(e).__name__}: {e}")
        return self._send(200 if ok else 502, msg)

    # ───────── Slack 슬래시 커맨드: /report [kr|us] ─────────
    def do_POST(self):
        u = urlparse(self.path)
        route = parse_qs(u.query).get("route", [""])[0] or u.path
        if route not in ("slack", "/api/slack"):
            return self._send(404, "not found")
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if not self._slack_verified(raw):
            return self._send(401, "invalid signature")
        if self.headers.get("X-Slack-Retry-Num"):  # Slack의 재시도 요청은 무시 (중복 리포트 방지)
            return self._send(200, "")
        form = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
        allowed_user = os.environ.get("SLACK_ALLOWED_USER_ID")
        allowed_channel = os.environ.get("SLACK_ALLOWED_CHANNEL_ID")
        if (allowed_user and form.get("user_id") != allowed_user) or \
           (allowed_channel and form.get("channel_id") != allowed_channel):
            return self._ephemeral("이 커맨드를 사용할 수 없는 사용자/채널입니다.")
        arg = form.get("text", "").strip().lower()
        mode = {"": "all", "all": "all", "kr": "kr", "us": "us"}.get(arg)
        if mode is None:
            return self._ephemeral("사용법: `/report` (전체), `/report kr` (국내), `/report us` (해외)")
        # 3초 안에 응답해야 하므로 먼저 접수 응답을 보내고, 완성되면 response_url 로 게시한다.
        self._ephemeral("⏳ 리포트를 만드는 중입니다 (10~20초)…")
        try:
            self.wfile.flush()
        except Exception:  # noqa: BLE001
            pass
        import slack_report
        url = form.get("response_url", "")
        try:
            ok, msg = slack_report.run(mode, scheduled=False, response_url=url)
            if "전송 생략" in msg:  # 휴장일 등: 요청한 사람에게만 사유 안내
                slack_report.post_response_url(url, msg.split("] ", 1)[-1], ephemeral=True)
        except Exception as e:  # noqa: BLE001
            try:
                slack_report.post_response_url(url, f"리포트 생성 실패: {type(e).__name__}: {e}"[:300], ephemeral=True)
            except Exception:  # noqa: BLE001
                pass

    def _ephemeral(self, text):
        self._send(200, json.dumps({"response_type": "ephemeral", "text": text}, ensure_ascii=False), "application/json")

    def _slack_verified(self, raw):
        secret = os.environ.get("SLACK_SIGNING_SECRET")
        ts = self.headers.get("X-Slack-Request-Timestamp", "")
        sig = self.headers.get("X-Slack-Signature", "")
        if not secret or not ts.isdigit() or abs(time.time() - int(ts)) > 300:
            return False
        want = "v0=" + hmac.new(secret.encode(), b"v0:" + ts.encode() + b":" + raw, hashlib.sha256).hexdigest()
        return _same(sig, want)
