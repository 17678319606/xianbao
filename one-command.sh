#!/bin/bash
# ============================================================
# xianbao 自动采集发布（宝塔计划任务专用，每 5 分钟跑一次）
# 设计目标：在宝塔 cron 下也能稳跑——
#   1) 不用 heredoc 写 .env（宝塔输入框会破坏多行内容，是“配了没动静”的主因）
#       -> 直接在 shell 里 export 凭据，main.py 会自动读取环境变量
#   2) 任意步骤失败都记录到日志，不会静默死掉
#   3) 末尾打印“线上结果”到屏幕，宝塔「执行一次」就能看到是否真的发了
#   4) git 同步失败不阻塞发布（用本地代码继续跑）
# ============================================================

DIR="/www/wwwroot/xianbao"
REPO="https://github.com/17678319606/xianbao.git"
LOG="$DIR/xianbao.log"

# ===== 凭据（改成你自己的也行，默认已是正确值）=====
WP_SITE="https://12313.icu"
WP_USER="tougao"
WP_APP_PASSWORD="l28f DeJP Vwfe zxNH iJhY npci"
# ===================================================

# 同时打到屏幕(宝塔「执行一次」可见)和日志文件
log(){ echo "[$(date '+%F %T')] $*"; echo "[$(date '+%F %T')] $*" >> "$LOG"; }

mkdir -p "$DIR" 2>/dev/null

# —— 环境诊断（出问题一眼看出缺什么）——
log "[ENV] git=$(command -v git || echo MISSING) python3=$(command -v python3 || echo MISSING) curl=$(command -v curl || echo MISSING)"
log "[ENV] pwd_set=$( [ -n "$WP_APP_PASSWORD" ] && echo YES || echo NO )"

# 1) 首次 clone（非阻塞：clone 失败但本地已有代码则继续）
if [ ! -d "$DIR/.git" ]; then
  log "[SETUP] cloning repo..."
  rm -rf "$DIR"
  if ! git clone "$REPO" "$DIR" >> "$LOG" 2>&1; then
    if [ -f "$DIR/main.py" ]; then
      log "[WARN] git clone failed, but local code exists, continue"
    else
      log "[FATAL] git clone failed AND no local code -> exit"
      exit 1
    fi
  fi
fi

cd "$DIR" || { log "[FATAL] cannot cd $DIR"; exit 1; }

# 2) 同步代码（非阻塞：失败仅警告，用本地代码继续跑，保证发布不中断）
if git fetch -q origin 2>> "$LOG" && git reset --hard origin/main 2>> "$LOG"; then
  log "[SYNC] code updated -> $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
else
  log "[WARN] git sync failed, using local code (发布不受影响)"
fi

# 3) 选 python 解释器（优先 venv，否则系统 python3）
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"
else
  log "[FATAL] 系统没有 python3，无法运行 -> exit"
  exit 1
fi
log "[ENV] using python: $PY ($($PY --version 2>&1))"

# 4) 确保依赖装好（用选定的解释器，少一层 venv 失败）
$PY -m pip install -q requests beautifulsoup4 2>> "$LOG" \
  || log "[WARN] pip install failed, trying to continue"

# 5) 直接 export 凭据给 python（无需 .env 文件，绕开宝塔 heredoc 破坏）
export WP_SITE WP_USER WP_APP_PASSWORD
export PUBLISH_STATUS="publish"
export WXWORK_WEBHOOK="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=ca5e918f-8e98-4c96-9734-8e5d27b298d0"
export ALERT_EMAIL="weixinkaifa@jinbufenzi.work"

# 6) 运行主程序（详情进日志）
log "[RUN] start main.py"
$PY main.py >> "$LOG" 2>&1
rc=$?
log "[RUN] done exit=$rc"

# 7) 汇总：查线上 WP 类目1 最新时间 + 总数，打印到屏幕（重点可观测性）
total=$(curl -s -m 15 -D - -o /dev/null "https://12313.icu/wp-json/wp/v2/posts?categories=1&per_page=1" 2>/dev/null | grep -i "x-wp-total" | tr -d '\r' | awk '{print $2}')
latest=$(curl -s -m 15 "https://12313.icu/wp-json/wp/v2/posts?categories=1&per_page=1&orderby=date&order=desc&_fields=date" 2>/dev/null | grep -o '"date":"[^"]*"' | head -1 | sed 's/"date":"//;s/"//')
echo "================ xianbao 本轮结果 ================"
echo "退出码        : $rc"
echo "线上类目1总数 : ${total:-查询失败}"
echo "线上类目1最新 : ${latest:-查询失败}"
echo "本地日志      : $LOG （tail -20 看详情）"
echo "=================================================="

# 8) 日志轮转（>5MB 清空，避免占满 1H1G 磁盘）
if [ -f "$LOG" ]; then
  sz=$(stat -c%s "$LOG" 2>/dev/null || echo 0)
  [ "$sz" -gt 5242880 ] && : > "$LOG"
fi
