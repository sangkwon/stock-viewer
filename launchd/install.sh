#!/bin/zsh
# Slack 리포트 예약 작업(launchd)을 이 컴퓨터에 등록/해제한다.
#
#   ./launchd/install.sh              등록 (국내 월~금 16:00, 미국 화~토 07:30)
#   ./launchd/install.sh --uninstall  해제
#
# 프로젝트 경로는 이 스크립트 위치에서 자동으로 계산하므로 어느 폴더에 clone해도 동작한다.
# 사전 조건: .venv 생성 및 의존성 설치, 키체인에 slack-webhook-url 저장 (README 참고)
set -e

PROJ="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
LABEL_KR="com.stock-viewer.report-kr"
LABEL_US="com.stock-viewer.report-us"
UID_NUM="$(id -u)"

unload() {
  launchctl bootout "gui/$UID_NUM/$1" >/dev/null 2>&1 || true
}

if [[ "$1" == "--uninstall" ]]; then
  for l in "$LABEL_KR" "$LABEL_US"; do
    unload "$l"
    rm -f "$AGENTS/$l.plist"
    echo "해제: $l"
  done
  exit 0
fi

if [[ ! -x "$PROJ/.venv/bin/python" ]]; then
  echo "오류: $PROJ/.venv 가 없습니다. README의 '설치' 단계를 먼저 진행하세요." >&2
  exit 1
fi

mkdir -p "$PROJ/logs" "$AGENTS"

/usr/bin/python3 - "$PROJ" "$AGENTS" "$LABEL_KR" "$LABEL_US" <<'PY'
import plistlib, sys
proj, agents, label_kr, label_us = sys.argv[1:5]

def make(label, mode, weekdays, hour, minute):
    return {
        "Label": label,
        "ProgramArguments": [f"{proj}/.venv/bin/python", f"{proj}/slack_report.py", "--mode", mode],
        "WorkingDirectory": proj,
        "StartCalendarInterval": [{"Weekday": w, "Hour": hour, "Minute": minute} for w in weekdays],
        "StandardOutPath": f"{proj}/logs/slack_report_{mode}.log",
        "StandardErrorPath": f"{proj}/logs/slack_report_{mode}.err.log",
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "PYTHONWARNINGS": "ignore"},
    }

# launchd 요일: 0/7=일, 1=월 ... 6=토
jobs = {
    label_kr: make(label_kr, "kr", [1, 2, 3, 4, 5], 16, 0),   # 월~금 16:00 (국내 장 마감 후)
    label_us: make(label_us, "us", [2, 3, 4, 5, 6], 7, 30),   # 화~토 07:30 (미국 장 마감 후, 한국시간)
}
for label, d in jobs.items():
    with open(f"{agents}/{label}.plist", "wb") as f:
        plistlib.dump(d, f)
PY

for l in "$LABEL_KR" "$LABEL_US"; do
  unload "$l"
  launchctl bootstrap "gui/$UID_NUM" "$AGENTS/$l.plist"
  echo "등록: $l"
done
echo "완료. 로그: $PROJ/logs/"
