#!/usr/bin/env bash
# 日更入口（launchd 仅 08:30，不发晚报）
# 分析：优先 MiniMax（mmx · MiniMax-M3），额度不足回退 Grok Build（grok · grok-4.5）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# 加载本地密钥（飞书等），不要提交仓库
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

export PATH="$HOME/.grok/bin:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:${PATH:-/usr/bin:/bin}"

# —— MiniMax（主）——
export MINIMAX_CLI="${MINIMAX_CLI:-$(command -v mmx 2>/dev/null || true)}"
export MINIMAX_CLI="${MINIMAX_CLI:-/opt/homebrew/bin/mmx}"
export MINIMAX_MODEL="${MINIMAX_MODEL:-MiniMax-M3}"
export MINIMAX_TIMEOUT="${MINIMAX_TIMEOUT:-240}"

# —— Grok Build（回退）——
export GROK_CLI="${GROK_CLI:-$(command -v grok 2>/dev/null || true)}"
export GROK_CLI="${GROK_CLI:-$HOME/.grok/bin/grok}"
export GROK_MODEL="${GROK_MODEL:-grok-4.5}"
export GROK_TIMEOUT="${GROK_TIMEOUT:-240}"
export GROK_PERMISSION_MODE="${GROK_PERMISSION_MODE:-bypassPermissions}"
export LLM_ENGINE="${LLM_ENGINE:-auto}"
export LLM_FALLBACK="${LLM_FALLBACK:-1}"

HAS_MMX=0
HAS_GROK=0
[[ -x "$MINIMAX_CLI" ]] && HAS_MMX=1
[[ -x "$GROK_CLI" ]] && HAS_GROK=1

if [[ "$HAS_MMX" -eq 0 && "$HAS_GROK" -eq 0 ]]; then
  echo "ERROR: MiniMax（mmx）与 Grok Build（grok）均不可用" >&2
  exit 1
fi

if [[ "$HAS_MMX" -eq 1 ]]; then
  echo "执行引擎: 主=MiniMax CLI=$MINIMAX_CLI model=$MINIMAX_MODEL"
  "$MINIMAX_CLI" --version 2>/dev/null | head -n 1 || true
else
  echo "WARN: 未找到 MiniMax，将直接使用 Grok Build"
fi
if [[ "$HAS_GROK" -eq 1 ]]; then
  echo "回退引擎: Grok Build CLI=$GROK_CLI model=$GROK_MODEL"
  "$GROK_CLI" --version 2>/dev/null | head -n 1 || true
else
  echo "WARN: 未找到 Grok Build，MiniMax 失败时无回退"
fi

# 运行副本常被 install_launchd rsync 弄脏 scripts/，会阻断 merge 导致整链失败、简报漏发
git fetch origin main
if ! git merge --ff-only origin/main; then
  echo "WARN: git merge --ff-only 失败，对齐 origin/main 后继续（避免漏推飞书）"
  git reset --hard origin/main
  git clean -fd --exclude=data/logs --exclude=.env --exclude='.env.*' || true
fi

SINCE="$(date -v-7d +%F)"
echo "开始流水线（MiniMax→Grok 分析 → 简报 → 飞书外部群） since=$SINCE"
# max-pages：上观腾讯约 20 条/页；过小会漏掉书记市长通稿
MINIMAX_CLI="$MINIMAX_CLI" \
MINIMAX_MODEL="$MINIMAX_MODEL" \
MINIMAX_TIMEOUT="$MINIMAX_TIMEOUT" \
GROK_CLI="$GROK_CLI" \
GROK_MODEL="$GROK_MODEL" \
GROK_TIMEOUT="$GROK_TIMEOUT" \
GROK_PERMISSION_MODE="$GROK_PERMISSION_MODE" \
LLM_ENGINE="$LLM_ENGINE" \
LLM_FALLBACK="$LLM_FALLBACK" \
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
