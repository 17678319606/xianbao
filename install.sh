#!/usr/bin/env bash
# 服务器一键部署（需系统自带 python3 >= 3.8）
# 用法：把本仓库传到服务器后，bash install.sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

PY="$(command -v python3 || command -v python)"
echo "[1/4] 使用 Python: $PY"
"$PY" --version

echo "[2/4] 创建虚拟环境并安装依赖（requests + beautifulsoup4）..."
"$PY" -m venv .venv
./.venv/bin/pip install -U pip -q
./.venv/bin/pip install -r requirements.txt -q

echo "[3/4] 准备 .env 凭据文件..."
if [ ! -f .env ]; then
  cp .env.example .env
  echo "      已生成 .env，请务必编辑填入真实 WP 应用密码："
  echo "      vi $DIR/.env"
else
  echo "      .env 已存在，保留。"
fi
chmod 600 .env 2>/dev/null || true

echo "[4/4] 完成。后续步骤："
echo "  a) 编辑 $DIR/.env 填入 WP_APP_PASSWORD（必须是 WordPress 应用密码，不是后台登录密码）"
echo "  b) 先用 dry-run 验证整条链路（不真正发文章）："
echo "       $DIR/.venv/bin/python main.py --dry-run"
echo "  c) 用宝塔『计划任务』或 systemd 每 5 分钟执行："
echo "       bash $DIR/run.sh"
echo "  d) 看运行状态：cat $DIR/last_run.json"
echo ""
echo "注意：posted.json 已自带近期去重种子，首次运行不会把历史线报批量重发。"
