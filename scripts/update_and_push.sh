#!/usr/bin/env bash
# 日更入口（launchd 仅 08:30，不发晚报）
# 分析统一由 Grok Build CLI（grok · grok-4.5）执行；简报规则拼装后推外部飞书群。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# 加载本地密钥（飞书等），不要提交仓库
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

# —— Grok Build 执行环境（分析链路依赖）——
export PATH="$HOME/.grok/bin:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:${PATH:-/usr/bin:/bin}"
export GROK_CLI="${GROK_CLI:-$(command -v grok 2>/dev/null || true)}"
export GROK_CLI="${GROK_CLI:-$HOME/.grok/bin/grok}"
export GROK_MODEL="${GROK_MODEL:-grok-4.5}"
export GROK_TIMEOUT="${GROK_TIMEOUT:-240}"
export GROK_PERMISSION_MODE="${GROK_PERMISSION_MODE:-bypassPermissions}"

if [[ ! -x "$GROK_CLI" ]]; then
  echo "ERROR: 未找到 Grok Build CLI（grok）。路径=$GROK_CLI" >&2
  echo "请安装/登录 grok 后重试；分析步骤依赖 Grok Build 执行。" >&2
  exit 1
fi

echo "执行引擎: Grok Build CLI=$GROK_CLI  model=$GROK_MODEL"
"$GROK_CLI" --version 2>/dev/null | head -n 1 || true

# 运行副本常被 install_launchd rsync 弄脏 scripts/，会阻断 merge 导致整链失败、简报漏发
git fetch origin main
if ! git merge --ff-only origin/main; then
  echo "WARN: git merge --ff-only 失败，对齐 origin/main 后继续（避免漏推飞书）"
  # 丢弃运行目录脏改动；本流水线会重新生成 data/
  git reset --hard origin/main
  git clean -fd --exclude=data/logs --exclude=.env --exclude='.env.*' || true
fi

SINCE="$(date -v-7d +%F)"
echo "开始流水线（Grok 分析 → 简报 → 飞书外部群） since=$SINCE"
# max-pages：上观腾讯约 20 条/页；过小会漏掉书记市长通稿
GROK_CLI="$GROK_CLI" \
GROK_MODEL="$GROK_MODEL" \
GROK_TIMEOUT="$GROK_TIMEOUT" \
GROK_PERMISSION_MODE="$GROK_PERMISSION_MODE" \
  python3 scripts/update_all.py --since "$SINCE" --max-pages 12 --skip-drafts

# 简报生成与飞书推送已在 update_all.py → gen_brief.py 完成，勿再调一次，否则会重复推送

git add data/*.json briefs/*.md
if git diff --cached --quiet; then
  echo "没有新的数据变化（简报/推送已在流水线内完成）"
  exit 0
fi

git config user.name "minmeng-data-bot"
git config user.email "minmeng-data-bot@users.noreply.github.com"
git commit -m "refresh data $(date +%F)"
git push origin main
