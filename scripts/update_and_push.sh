#!/usr/bin/env bash
# 日更入口（launchd 每天 09:00 / 21:00）
# 铁律：飞书简报推送优先于 git 同步；git 网络失败不得阻断日更。
# 分析：仅使用 Grok CLI（grok-4.6），失败即报错，不切换模型。
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
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"

# —— Grok 4.6（唯一分析引擎）——
export GROK_CLI="${GROK_CLI:-$(command -v grok 2>/dev/null || true)}"
export GROK_CLI="${GROK_CLI:-$HOME/.grok/bin/grok}"
export GROK_MODEL="${GROK_MODEL:-grok-4.6}"
export GROK_TIMEOUT="${GROK_TIMEOUT:-240}"
export GROK_PERMISSION_MODE="${GROK_PERMISSION_MODE:-bypassPermissions}"
export LLM_ENGINE="grok"
export LLM_FALLBACK="0"
# 重要平台：默认每日必推一封（无新增也推「报告日无新增+近七日主轴」）
export FEISHU_PUSH_ALWAYS="${FEISHU_PUSH_ALWAYS:-1}"
# 防双通道 / 发错群
export FEISHU_CHAT_ID="${FEISHU_CHAT_ID:-oc_381bea46653394d135daf14739524904}"
if [[ "$FEISHU_CHAT_ID" == "oc_9334707219faab92091bdfb24344fa95" ]]; then
  echo "ERROR: 检测到旧内部群 ID，拒绝推送" >&2
  exit 1
fi
unset FEISHU_WEBHOOK BRIEF_WEBHOOK FEISHU_JOIN_URL 2>/dev/null || true
# 09:00 早报看昨日（T-1）；21:00 晚报看今日。
if [[ -z "${CZ_BRIEF_REPORT_DATE:-}" ]]; then
  CURRENT_HOUR=$((10#$(date +%H)))
  if [[ "$CURRENT_HOUR" -lt 15 ]]; then
    CZ_BRIEF_REPORT_DATE="$(date -v-1d +%F 2>/dev/null || date -d 'yesterday' +%F)"
    CZ_BRIEF_PERIOD="${CZ_BRIEF_PERIOD:-早报}"
  else
    CZ_BRIEF_REPORT_DATE="$(date +%F)"
    CZ_BRIEF_PERIOD="${CZ_BRIEF_PERIOD:-晚报}"
  fi
fi
export CZ_BRIEF_REPORT_DATE
export CZ_BRIEF_PERIOD="${CZ_BRIEF_PERIOD:-早报}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "ERROR: Python 不可用：$PYTHON_BIN" >&2
  exit 1
fi
if ! "$PYTHON_BIN" -c 'import requests, bs4' >/dev/null 2>&1; then
  echo "ERROR: $PYTHON_BIN 缺少 requests / beautifulsoup4，停止出报以免误报无新增" >&2
  exit 2
fi
if [[ ! -x "${GROK_CLI:-}" ]]; then
  echo "ERROR: Grok CLI 不可用：${GROK_CLI:-未配置}" >&2
  exit 1
fi
echo "Python: $PYTHON_BIN"
echo "执行引擎: Grok CLI=$GROK_CLI model=$GROK_MODEL（唯一引擎）"
"$GROK_CLI" --version 2>/dev/null | head -n 1 || true

# —— git 同步：失败只告警，绝不阻断抓取/简报/飞书 ——
echo "git: 尝试同步 origin/main（失败不阻断日更）"
if git fetch origin main 2>&1; then
  if ! git merge --ff-only origin/main 2>&1; then
    echo "WARN: git merge --ff-only 失败；保留全部本地文件，继续当前运行副本" >&2
  fi
else
  echo "WARN: git fetch 失败（常见 DNS/github 不可达），跳过代码同步，继续日更流水线" >&2
fi

SINCE="$(date -v-7d +%F 2>/dev/null || date -d '7 days ago' +%F)"
echo "开始流水线（采集→分析→简报→飞书） since=$SINCE report_date=$CZ_BRIEF_REPORT_DATE"
set +e
GROK_CLI="$GROK_CLI" \
GROK_MODEL="$GROK_MODEL" \
GROK_TIMEOUT="$GROK_TIMEOUT" \
GROK_PERMISSION_MODE="$GROK_PERMISSION_MODE" \
LLM_ENGINE="$LLM_ENGINE" \
LLM_FALLBACK="$LLM_FALLBACK" \
FEISHU_PUSH_ALWAYS="$FEISHU_PUSH_ALWAYS" \
CZ_BRIEF_REPORT_DATE="$CZ_BRIEF_REPORT_DATE" \
CZ_BRIEF_PERIOD="$CZ_BRIEF_PERIOD" \
  "$PYTHON_BIN" scripts/update_all.py --since "$SINCE" --max-pages 12 --skip-drafts
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
