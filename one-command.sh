#!/bin/bash
# xianbao 一键命令（宝塔计划任务专用）
# 用法：把下面整段复制到宝塔「计划任务」→「脚本内容」框里
#       只需改 3 个变量（WP_SITE / WP_USER / WP_APP_PASSWORD），其余不用动
# 首次运行自动 clone + 装 venv + 生成 .env；后续每次直接采集发布
set -u
DIR="/www/wwwroot/xianbao"
REPO="https://github.com/17678319606/xianbao.git"

# ====== 在这里填你的 3 个凭据（必改）======
WP_SITE="https://12313.icu"
WP_USER="tougao"
WP_APP_PASSWORD="EA8tfnopxdWiN7YtFX61PyT1"   # WordPress 应用密码
# ===========================================

# 自动 clone（首次）
if [ ! -d "$DIR/.git" ]; then
  echo "[SETUP] cloning repo..."
  rm -rf "$DIR"
  git clone "$REPO" "$DIR" || { echo "[FATAL] git clone failed"; exit 1; }
fi

cd "$DIR"

# 自动拉更新
git pull -q 2>/dev/null || true

# 自动建 venv + 装依赖（首次或被删后自动恢复）
if [ ! -f ".venv/bin/python" ]; then
  echo "[SETUP] creating venv..."
  python3 -m venv .venv || { echo "[FATAL] venv create failed"; exit 1; }
  .venv/bin/pip install -q requests beautifulsoup4 || { echo "[FATAL] pip install failed"; exit 1; }
fi

# 自动生成 .env（含凭据 + 企微告警地址）
cat > .env << ENVEOF
WP_SITE=${WP_SITE}
WP_USER=${WP_USER}
WP_APP_PASSWORD=${WP_APP_PASSWORD}
PUBLISH_STATUS=publish
WXWORK_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=ca5e918f-8e98-4c96-9734-8e5d27b298d0
ALERT_EMAIL=weixinkaifa@jinbufenzi.work
ENVEOF
chmod 600 .env

# 执行采集发布
exec .venv/bin/python main.py
