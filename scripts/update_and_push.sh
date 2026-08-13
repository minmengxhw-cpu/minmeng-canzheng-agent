#!/usr/bin/env bash
# 日更入口（launchd 仅 08:30，不发晚报）
# 铁律：飞书简报推送优先于 git 同步；git 网络失败不得阻断日更。
# 分析：优先 MiniMax（mmx），额度不足回退 Grok Build（grok）
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PIPELINE_RC=0

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
# 重要平台：默认每日必推一封（无新增也推「报告日无新增+近七日主轴」）
export FEISHU_PUSH_ALWAYS="${FEISHU_PUSH_ALWAYS:-1}"
# 防双通道 / 发错群
export FEISHU_CHAT_ID="${FEISHU_CHAT_ID:-oc_381bea46653394d135daf14739524904}"
if [[ "$FEISHU_CHAT_ID" == "oc_9334707219faab92091bdfb24344fa95" ]]; then
  echo "ERROR: 检测到旧内部群 ID，拒绝推送" >&2
  exit 1
fi
unset FEISHU_WEBHOOK BRIEF_WEBHOOK FEISHU_JOIN_URL 2>/dev/null || true
# 早报逻辑：今天 08:30 讲「昨天」的市领导/中央公开活动（T-1）
# 有新增=报告日（昨日）有新增，不是日历今天零点后的通稿
if [[ -z "${CZ_BRIEF_REPORT_DATE:-}" ]]; then
  CZ_BRIEF_REPORT_DATE="$(date -v-1d +%F 2>/dev/null || date -d 'yesterday' +%F)"
fi
export CZ_BRIEF_REPORT_DATE
export CZ_BRIEF_PERIOD="${CZ_BRIEF_PERIOD:-早报}"

HAS_MMX=0
HAS_GROK=0
[[ -x "${MINIMAX_CLI:-}" ]] && HAS_MMX=1
[[ -x "${GROK_CLI:-}" ]] && HAS_GROK=1

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

# —— git 同步：失败只告警，绝不阻断抓取/简报/飞书 ——
# 对抗点：reset --hard 会抹掉未推送的 data/briefs，必须先保全
echo "git: 尝试同步 origin/main（失败不阻断日更）"
if git fetch origin main 2>&1; then
  if ! git merge --ff-only origin/main 2>&1; then
    echo "WARN: git merge --ff-only 失败，对齐 origin/main（保全 data/briefs）"
    STASH_DIR="$(mktemp -d /tmp/cz-data-XXXXXX)"
    cp -a data "$STASH_DIR/data" 2>/dev/null || true
    cp -a briefs "$STASH_DIR/briefs" 2>/dev/null || true
    git reset --hard origin/main 2>&1 || echo "WARN: git reset 失败，继续用当前工作副本"
    git clean -fd --exclude=data/logs --exclude=.env --exclude='.env.*' 2>&1 || true
    if [[ -d "$STASH_DIR/data" ]]; then
      mkdir -p data
      rsync -a "$STASH_DIR/data/" data/ 2>/dev/null || true
    fi
    if [[ -d "$STASH_DIR/briefs" ]]; then
      mkdir -p briefs
      rsync -a "$STASH_DIR/briefs/" briefs/ 2>/dev/null || true
    fi
    rm -rf "$STASH_DIR"
  fi
else
  echo "WARN: git fetch 失败（常见 DNS/github 不可达），跳过代码同步，继续日更流水线" >&2
fi

SINCE="$(date -v-7d +%F 2>/dev/null || date -d '7 days ago' +%F)"
echo "开始流水线（采集→分析→简报→飞书） since=$SINCE report_date=$CZ_BRIEF_REPORT_DATE"
set +e
MINIMAX_CLI="$MINIMAX_CLI" \
MINIMAX_MODEL="$MINIMAX_MODEL" \
MINIMAX_TIMEOUT="$MINIMAX_TIMEOUT" \
GROK_CLI="$GROK_CLI" \
GROK_MODEL="$GROK_MODEL" \
GROK_TIMEOUT="$GROK_TIMEOUT" \
GROK_PERMISSION_MODE="$GROK_PERMISSION_MODE" \
LLM_ENGINE="$LLM_ENGINE" \
LLM_FALLBACK="$LLM_FALLBACK" \
FEISHU_PUSH_ALWAYS="$FEISHU_PUSH_ALWAYS" \
CZ_BRIEF_REPORT_DATE="$CZ_BRIEF_REPORT_DATE" \
CZ_BRIEF_PERIOD="$CZ_BRIEF_PERIOD" \
  python3 scripts/update_all.py --since "$SINCE" --max-pages 12 --skip-drafts
PIPELINE_RC=$?
set -e

if [[ "$PIPELINE_RC" -ne 0 ]]; then
  echo "ERROR: update_all 退出码=$PIPELINE_RC（仍尝试落盘 git，不掩盖失败）" >&2
fi

# 简报生成与飞书推送已在 update_all.py → gen_brief.py 完成，勿再调一次

# —— 数据回写 git：失败不改变流水线成败 ——
set +e
git add data/*.json briefs/*.md 2>/dev/null
if git diff --cached --quiet 2>/dev/null; then
  echo "没有新的数据变化需要 commit"
else
  git config user.name "minmeng-data-bot"
  git config user.email "minmeng-data-bot@users.noreply.github.com"
  git commit -m "refresh data $(date +%F)" 2>&1 || true
  if ! git push origin main 2>&1; then
    echo "WARN: git push 失败（不影响已推飞书），下次再试" >&2
  fi
fi
set -e

exit "$PIPELINE_RC"
