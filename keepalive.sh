#!/usr/bin/env bash
# GitHub 保活（可选）：每周空提交一次并 push，防止 GitHub 因 60 天无仓库活动
# 而禁用定时任务。仅当你重新开启 .github/workflows/publish.yml 的 schedule 时才需要。
# 运行时已迁移到服务器时，本脚本可不放计划任务（跳过即可）。
# 凭据从同目录 .env 读取 GH_TOKEN（不写死在脚本里）。
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

GH_TOKEN="$(grep '^GH_TOKEN=' .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\"' )"
if [ -z "$GH_TOKEN" ]; then
  echo "[keepalive] 未配置 GH_TOKEN，跳过保活（运行时在服务器上无需此步）"
  exit 0
fi

git config user.name "xianbao-bot" 2>/dev/null || true
git config user.email "bot@jinbufenzi.work" 2>/dev/null || true
git commit --allow-empty -m "chore: keepalive $(date -u +%Y-%m-%dT%H:%M:%SZ)" || true
git push "https://${GH_TOKEN}@github.com/17678319606/xianbao.git" main || true
echo "[keepalive] done"
