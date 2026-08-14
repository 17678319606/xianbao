#!/bin/bash
# xianbao 一键命令（宝塔计划任务专用，每 5 分钟执行一次）
# 用法：宝塔「计划任务」→ 添加任务 → 任务类型「Shell 脚本」→
#       执行周期选「N 分钟」填 5 → 把下面整段粘到「脚本内容」→ 保存 → 点「执行一次」看输出
# 只需改 3 个变量（WP_SITE / WP_USER / WP_APP_PASSWORD），其余不用动。
set -u
DIR="/www/wwwroot/xianbao"
REPO="https://github.com/17678319606/xianbao.git"
LOG="$DIR/xianbao.log"

# ====== 在这里填你的 3 个凭据（必改）======
WP_SITE="https://12313.icu"
WP_USER="tougao"
WP_APP_PASSWORD="l28f DeJP Vwfe zxNH iJhY npci"   # WordPress 应用密码（注意：含空格，原样填写）
# ===========================================

echo "$(date '+%F %T') [RUN] start"

# 首次 clone
if [ ! -d "$DIR/.git" ]; then
  echo "$(date '+%F %T') [SETUP] cloning repo..."
  rm -rf "$DIR"
  git clone "$REPO" "$DIR" || { echo "$(date '+%F %T') [FATAL] git clone failed"; exit 1; }
fi

cd "$DIR" || { echo "$(date '+%F %T') [FATAL] cannot cd $DIR"; exit 1; }

# 强制同步最新代码：用 fetch + reset --hard 代替 git pull，
# 不受本地未跟踪文件（如 posted.json 状态文件）干扰，避免 pull 冲突卡死导致永远停在旧版本。
git fetch -q origin
git reset --hard origin/main || { echo "$(date '+%F %T') [FATAL] git sync failed"; exit 1; }

# 自动建 venv + 装依赖（首次或被删后自动恢复）
if [ ! -f ".venv/bin/python" ]; then
  echo "$(date '+%F %T') [SETUP] creating venv..."
  python3 -m venv .venv || { echo "$(date '+%F %T') [FATAL] venv create failed"; exit 1; }
  .venv/bin/pip install -q requests beautifulsoup4 || { echo "$(date '+%F %T') [FATAL] pip install failed"; exit 1; }
fi

# 生成 .env（含凭据 + 企微告警地址）
cat > .env << ENVEOF
WP_SITE=${WP_SITE}
WP_USER=${WP_USER}
WP_APP_PASSWORD=${WP_APP_PASSWORD}
PUBLISH_STATUS=publish
WXWORK_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=ca5e918f-8e98-4c96-9734-8e5d27b298d0
ALERT_EMAIL=weixinkaifa@jinbufenzi.work
ENVEOF
chmod 600 .env

# 日志轮转：超过 5MB 则清空（避免占满 1H1G 磁盘）
if [ -f "$LOG" ]; then
  size=$(stat -c%s "$LOG" 2>/dev/null || echo 0)
  if [ "$size" -gt 5242880 ]; then
    : > "$LOG"
  fi
fi

# 执行采集发布（输出追加到日志，便于排查；宝塔里点「执行一次」也能看到本次输出）
.venv/bin/python main.py >> "$LOG" 2>&1
echo "$(date '+%F %T') [RUN] done exit=$?"
