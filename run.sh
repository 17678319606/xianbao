#!/usr/bin/env bash
# 线报酷采集发布 —— 运行包装
# 职责：防重叠(flock) + 日志轮转 + 选定 Python 解释器
# 由宝塔「计划任务」/ crontab / systemd 每 5 分钟调用一次（或 --loop 常驻）。
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

LOG="$DIR/xianbao.log"

# 日志轮转：超过 2MB 则保留 1 份旧日志（避免无限增长占满 1H1G 磁盘）
if [ -f "$LOG" ]; then
  size=$(stat -c%s "$LOG" 2>/dev/null || echo 0)
  if [ "$size" -gt 2097152 ]; then
    mv -f "$LOG" "$LOG.old"
  fi
fi

# 选定解释器：优先用本项目 venv（Linux 在 bin/，Windows 在 Scripts/），否则退回系统 python3
if [ -x "$DIR/.venv/bin/python" ]; then
  PY="$DIR/.venv/bin/python"
elif [ -x "$DIR/.venv/Scripts/python" ]; then
  PY="$DIR/.venv/Scripts/python"
elif [ -x "$DIR/.venv/Scripts/python.exe" ]; then
  PY="$DIR/.venv/Scripts/python.exe"
else
  PY="$(command -v python3 || command -v python)"
fi

# 防重叠：上一次若仍在跑（极端网络慢），本次直接跳过，避免并发写状态文件
# 注意：用 >> 创建锁文件（< 在文件不存在时会报错）；无 flock 的环境直接跑（降级）
if command -v flock >/dev/null 2>&1; then
  exec 9>>"$DIR/.runlock"
  flock -n 9 || { echo "$(date '+%F %T') [SKIP] previous run still active"; exit 0; }
fi

exec "$PY" main.py "$@" >> "$LOG" 2>&1
