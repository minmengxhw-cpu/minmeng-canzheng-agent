#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# 加载本地密钥（飞书 Webhook 等），不要提交仓库
# 优先：运行目录 .env → Application Support → 用户 config
for envf in \
  "$ROOT/.env" \
  "$HOME/Library/Application Support/minmeng-canzheng-agent/.env" \
  "$HOME/.config/minmeng-canzheng-agent/env"
do
  if [[ -f "$envf" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$envf"
    set +a
    echo "已加载环境: $envf"
    break
  fi
done

git fetch origin main
git merge --ff-only origin/main

SINCE="$(date -v-7d +%F)"
GROK_MODEL="${GROK_MODEL:-grok-4.5}" \
GROK_PERMISSION_MODE="${GROK_PERMISSION_MODE:-bypassPermissions}" \
  python3 scripts/update_all.py --since "$SINCE" --max-pages 6 --skip-drafts

# 无 git 变化时仍生成/推送简报（避免「有信号但未 commit」漏推）
python3 scripts/gen_brief.py || true

git add data/*.json briefs/*.md
if git diff --cached --quiet; then
  echo "没有新的数据变化（简报/推送已尝试）"
  exit 0
fi

git config user.name "minmeng-data-bot"
git config user.email "minmeng-data-bot@users.noreply.github.com"
git commit -m "refresh data $(date +%F)"
git push origin main
