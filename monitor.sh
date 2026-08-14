#!/usr/bin/env bash
# 心跳巡检：检查 last_run.json，若超过阈值未成功运行则告警（非零退出）。
# 可放入宝塔『计划任务』每 30 分钟跑一次；停摆时自动调用 alert.py 发
# 企业微信机器人 + 邮件（读同目录 .env 的 WXWORK_WEBHOOK / ALERT_EMAIL）。
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
HB="$DIR/last_run.json"
NOW=$(date +%s)
MAX_AGE=900   # 15 分钟（应至少跑过 3 个 5 分钟周期）

# 选定 Python 解释器（与 run.sh 同逻辑）
if [ -x "$DIR/.venv/bin/python" ]; then
  PY="$DIR/.venv/bin/python"
elif [ -x "$DIR/.venv/Scripts/python" ]; then
  PY="$DIR/.venv/Scripts/python"
elif [ -x "$DIR/.venv/Scripts/python.exe" ]; then
  PY="$DIR/.venv/Scripts/python.exe"
else
  PY="$(command -v python3 || command -v python)"
fi

alert() {
  # 失败告警：企业微信机器人 + 邮件（alert.py 自带 6 小时冷却，不会刷屏）
  "$PY" "$DIR/alert.py" "$1" "$2" || true
}

if [ ! -f "$HB" ]; then
  MSG="未找到心跳文件 $HB，采集任务可能从未运行或目录不对"
  echo "ALERT: $MSG"
  alert "采集任务停摆" "$MSG"
  exit 1
fi

TS=$(grep -o '"ts": *[0-9]*' "$HB" | grep -o '[0-9]*' | head -1)
STATUS=$(grep -o '"status": *"[^"]*"' "$HB" | sed 's/.*"status": *"\([^"]*\)".*/\1/')
AGE=$(( NOW - ${TS:-0} ))

if [ "$AGE" -gt "$MAX_AGE" ]; then
  MSG="距上次运行已 ${AGE}s（> ${MAX_AGE}s），状态=$STATUS，采集可能已停摆。请检查服务器/宝塔计划任务/网络。"
  echo "ALERT: $MSG"
  alert "采集任务停摆" "$MSG"
  exit 2
fi
echo "OK: ${AGE}s 前运行过，状态=$STATUS"
exit 0
