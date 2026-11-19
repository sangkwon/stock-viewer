#!/usr/bin/env python3
"""cron-job.org 에 Slack 리포트 예약 작업 4개를 만들거나(없으면) 갱신한다(있으면, 제목으로 식별).

    CRONJOB_API_KEY=... CRON_SECRET=... python3 scripts/cronjob_setup.py --base-url https://<프로젝트>.vercel.app
    python3 scripts/cronjob_setup.py --base-url ... --dry-run     # 보낼 내용만 출력 (키/시크릿 불필요)
    python3 scripts/cronjob_setup.py --base-url ... --delete      # 이 스크립트가 만든 작업 삭제

- CRONJOB_API_KEY: cron-job.org 콘솔 → Settings → API 에서 발급
- CRON_SECRET: Vercel 환경변수 CRON_SECRET 과 같은 값. 요청 헤더 `Authorization: Bearer <값>` 로 전송된다.
각 작업은 자기 시간대로 실행되므로 미국 서머타임을 따로 처리할 필요가 없다.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.cron-job.org"
PREFIX = "stock-viewer "
WEEKDAYS = [1, 2, 3, 4, 5]  # cron-job.org: 0=일 ... 6=토

# (제목, mode, 시간대, 시, 분)
JOBS = [
    ("kr 09:30/11:30/13:30", "kr", "Asia/Seoul", [9, 11, 13], [30]),
    ("kr 15:00", "kr", "Asia/Seoul", [15], [0]),
    ("us 개장 30분 후 (10:00 ET)", "us", "America/New_York", [10], [0]),
    ("us 마감 30분 전 (15:30 ET)", "us", "America/New_York", [15], [30]),
]


def job_body(base_url, secret, title, mode, tz, hours, minutes):
    return {"job": {
        "title": PREFIX + title,
        "url": f"{base_url}/api/report?mode={mode}",
        "enabled": True,
        "saveResponses": False,
        "requestMethod": 0,  # GET
        "extendedData": {"headers": {"Authorization": f"Bearer {secret}"}},
        "schedule": {"timezone": tz, "expiresAt": 0, "hours": hours, "mdays": [-1], "minutes": minutes,
                     "months": [-1], "wdays": WEEKDAYS},
    }}


def call(key, method, path, body=None):
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        sys.exit(f"cron-job.org API 오류 {e.code} ({method} {path}): {e.read().decode()[:300]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True, help="예: https://stock-viewer-tau.vercel.app (끝에 / 없이)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--delete", action="store_true")
    a = ap.parse_args()
    base = a.base_url.rstrip("/")

    if a.dry_run:
        for j in JOBS:
            body = job_body(base, "***", *j)
            print(json.dumps(body, ensure_ascii=False, indent=2))
        return
    key = os.environ.get("CRONJOB_API_KEY")
    secret = os.environ.get("CRON_SECRET")
    if not key or (not secret and not a.delete):
        sys.exit("환경변수 CRONJOB_API_KEY, CRON_SECRET 이 필요합니다.")

    existing = {j["title"]: j["jobId"] for j in call(key, "GET", "/jobs").get("jobs", []) if j["title"].startswith(PREFIX)}
    if a.delete:
        for title, jid in existing.items():
            call(key, "DELETE", f"/jobs/{jid}")
            print(f"삭제: {title}")
        return
    for j in JOBS:
        body = job_body(base, secret, *j)
        title = body["job"]["title"]
        if title in existing:
            call(key, "PATCH", f"/jobs/{existing[title]}", body)
            print(f"갱신: {title}")
        else:
            res = call(key, "PUT", "/jobs", body)
            print(f"생성: {title} (jobId={res.get('jobId')})")


if __name__ == "__main__":
    main()
