#!/usr/bin/env bash
# CZ Agent 日更健康检查 — 供 agent / 人工确认「今天会不会正常发」
set -euo pipefail

EXTERNAL="oc_381bea46653394d135daf14739524904"
OLD_INTERNAL="oc_9334707219faab92091bdfb24344fa95"
LABEL="com.minmeng.canzheng-agent"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
RUNTIME="$HOME/Library/Application Support/minmeng-canzheng-agent"
ENVF="$RUNTIME/.env"
OUT_LOG="$RUNTIME/data/logs/launchd.out.log"
ERR_LOG="$RUNTIME/data/logs/launchd.err.log"

PASS=0
FAIL=0
WARN=0

ok()  { echo "  OK  $*"; PASS=$((PASS+1)); }
bad() { echo "  FAIL $*"; FAIL=$((FAIL+1)); }
wrn() { echo "  WARN $*"; WARN=$((WARN+1)); }

echo "=== CZ Agent 日更健康检查 $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
echo

# 1) plist 存在
echo "[1] launchd plist"
if [[ -f "$PLIST" ]]; then
  ok "存在 $PLIST"
else
  bad "缺少 $PLIST — 请 bash scripts/install_launchd.sh"
fi

# 2) 仅 08:30（不得用 || true 吞掉 python 失败码，否则永远 PASS）
echo "[2] 定时仅 08:30"
if [[ -f "$PLIST" ]]; then
  if python3 - <<'PY'
import plistlib, sys
from pathlib import Path
p = Path.home() / "Library/LaunchAgents/com.minmeng.canzheng-agent.plist"
d = plistlib.loads(p.read_bytes())
sched = d.get("StartCalendarInterval")
if isinstance(sched, dict):
    sched = [sched]
ok_s = [{"Hour": 8, "Minute": 30}]
if sched == ok_s:
    sys.exit(0)
print("FAIL", sched)
sys.exit(1)
PY
  then
    ok "StartCalendarInterval = 仅 08:30"
  else
    bad "定时不是仅 08:30 — 禁止晚报/双时段"
  fi
fi

# 3) chat_id
echo "[3] 推送群 FEISHU_CHAT_ID"
CHAT=""
if [[ -f "$ENVF" ]]; then
  CHAT=$(grep -E '^FEISHU_CHAT_ID=' "$ENVF" | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")
fi
if [[ -f "$PLIST" ]]; then
  PLIST_CHAT=$(python3 -c "import plistlib;from pathlib import Path;d=plistlib.loads(Path('$PLIST').read_bytes());print(d.get('EnvironmentVariables',{}).get('FEISHU_CHAT_ID',''))")
else
  PLIST_CHAT=""
fi
if [[ "$CHAT" == "$EXTERNAL" ]]; then
  ok ".env 指向外部群"
else
  bad ".env FEISHU_CHAT_ID='$CHAT' 期望 $EXTERNAL"
fi
if [[ "$PLIST_CHAT" == "$EXTERNAL" ]]; then
  ok "launchd 指向外部群"
else
  bad "launchd FEISHU_CHAT_ID='$PLIST_CHAT' 期望 $EXTERNAL — 重跑 install_launchd.sh"
fi
if [[ "$CHAT" == "$OLD_INTERNAL" || "$PLIST_CHAT" == "$OLD_INTERNAL" ]]; then
  bad "检测到旧内部群 ID — 禁止推送"
fi
if grep -qE '^FEISHU_WEBHOOK=' "$ENVF" 2>/dev/null && ! grep -qE '^#.*FEISHU_WEBHOOK' "$ENVF" 2>/dev/null; then
  if grep -E '^FEISHU_WEBHOOK=.' "$ENVF" | grep -vq '^#'; then
    wrn "存在 FEISHU_WEBHOOK，可能双通道推送 — 建议注释掉"
  fi
fi

# 4) 分析引擎：MiniMax 主 + Grok 回退
echo "[4] 分析引擎（MiniMax → Grok）"
MMX="${MINIMAX_CLI:-/opt/homebrew/bin/mmx}"
GROK="${GROK_CLI:-$HOME/.grok/bin/grok}"
if [[ -x "$MMX" ]] || command -v mmx >/dev/null 2>&1; then
  ok "MiniMax mmx 可执行 (${MMX:-$(command -v mmx)})"
else
  wrn "找不到 mmx — 将依赖 Grok 回退"
fi
if [[ -x "$GROK" ]] || command -v grok >/dev/null 2>&1; then
  ok "Grok Build 可执行 (${GROK:-$(command -v grok)})"
else
  wrn "找不到 grok — MiniMax 失败时无回退"
fi
if ! { [[ -x "$MMX" ]] || command -v mmx >/dev/null 2>&1; } \
  && ! { [[ -x "$GROK" ]] || command -v grok >/dev/null 2>&1; }; then
  bad "mmx 与 grok 均不可用 — 分析链路会失败"
fi

# 5) 外部群发言权限 + 机器人在群
echo "[5] 外部群发言权限与机器人"
if command -v lark-cli >/dev/null 2>&1; then
  MOD=$(lark-cli im chat.moderation get --as bot --params "{\"chat_id\":\"$EXTERNAL\"}" --json 2>/dev/null \
    | python3 -c "import sys,json;d=json.load(sys.stdin);print((d.get('data') or {}).get('moderation_setting',''))" 2>/dev/null || echo "")
  if [[ "$MOD" == "all_members" ]]; then
    ok "moderation_setting=all_members"
  elif [[ "$MOD" == "only_owner" ]]; then
    bad "moderation_setting=only_owner → 230035 机器人发不出。群主改为全员可发言"
  elif [[ -z "$MOD" ]]; then
    wrn "无法读取 moderation（lark-cli/网络）"
  else
    wrn "moderation_setting=$MOD"
  fi
  BOTS=$(lark-cli im +chat-members-list --as bot --chat-id "$EXTERNAL" --json 2>/dev/null \
    | python3 -c "import sys,json;d=json.load(sys.stdin);print((d.get('data') or {}).get('bot_total',0))" 2>/dev/null || echo "0")
  if [[ "$BOTS" != "0" && -n "$BOTS" ]]; then
    ok "机器人在群内 bot_total=$BOTS"
  else
    bad "机器人可能不在群内 — 230002"
  fi
else
  wrn "无 lark-cli，跳过群权限检查"
fi

# 6) 今日 08:30 是否跑过且推送
echo "[6] 今日定时执行日志"
TODAY=$(date +%Y-%m-%d)
HOUR=$(date +%H)
MIN=$(date +%M)
if [[ -f "$OUT_LOG" ]]; then
  # 必须出现「今日」自动更新开始，不能用历史「执行引擎」行误报
  if rg -q "自动更新开始：${TODAY}T08:3" "$OUT_LOG" 2>/dev/null; then
    ok "日志出现今日 08:30 流水线启动：自动更新开始：${TODAY}T08:3x"
  elif rg -q "开始流水线.*report_date=${TODAY}|开始流水线（MiniMax→Grok|开始流水线（采集" "$OUT_LOG" 2>/dev/null \
    && rg -q "${TODAY}" <(tail -c 20000 "$OUT_LOG" 2>/dev/null); then
    # 兼容新脚本在 fetch 前打印「开始流水线」
    if tail -c 30000 "$OUT_LOG" | rg -q "开始流水线"; then
      # 进一步：mtime 是否为今天
      if [[ "$(date -r "$OUT_LOG" +%F 2>/dev/null || true)" == "$TODAY" ]]; then
        ok "今日 out 日志有更新且含开始流水线"
      else
        bad "未见今日 08:30 完整启动标记"
      fi
    fi
  else
    if [[ "$HOUR" -lt 8 || ( "$HOUR" -eq 8 && "$MIN" -lt 35 ) ]]; then
      wrn "尚未到/刚过 08:30，今日日志可暂无"
    else
      bad "今日 08:30 未见「自动更新开始：${TODAY}T08:3x」— 可能漏跑"
    fi
  fi
  # 今日推送：在今日相关段落内找「已主动推送」，避免历史误报
  if python3 - <<PY
from pathlib import Path
import re
p=Path("$OUT_LOG")
t=p.read_text(errors="replace")
# 取最后一个「自动更新开始：TODAY」之后的文本
key="自动更新开始：${TODAY}T"
idx=t.rfind(key)
chunk=t[idx:] if idx>=0 else ""
# 也接受今日 mtime 且末尾 8KB 内有推送+今日
if "已主动推送飞书" in chunk:
    raise SystemExit(0)
tail=t[-12000:]
if "${TODAY}" in tail and "已主动推送飞书" in tail and "自动更新开始：${TODAY}" in t:
    raise SystemExit(0)
raise SystemExit(1)
PY
  then
    ok "今日流水线段落内已出现「已主动推送飞书」"
  else
    if [[ "$HOUR" -lt 8 || ( "$HOUR" -eq 8 && "$MIN" -lt 40 ) ]]; then
      wrn "今日尚无飞书推送记录（或尚未到推送完成窗口）"
    else
      bad "今日未见飞书推送成功日志 — 查 launchd.out/err"
    fi
  fi
  if [[ -f "$ERR_LOG" ]]; then
    if tail -c 8000 "$ERR_LOG" | rg -q "Could not resolve host: github.com|fatal: unable to access"; then
      wrn "err 近期含 github 解析/访问失败 — 新脚本应不阻断日更，请确认已 install_launchd"
    fi
    if rg -q "$(date +%Y-%m-%d)" "$ERR_LOG" && rg -q "Aborting|overwritten by merge" "$ERR_LOG"; then
      wrn "err 日志含 git merge Aborting"
    fi
  fi
else
  bad "无 out 日志 $OUT_LOG"
fi

# 7) launchd last exit
echo "[7] launchd 最近退出码"
LE=$(launchctl print "gui/$(id -u)/${LABEL}" 2>/dev/null | rg "last exit code" | head -1 || true)
if echo "$LE" | rg -q "last exit code = 0"; then
  ok "$LE"
elif echo "$LE" | rg -q "never exited"; then
  wrn "$LE"
elif echo "$LE" | rg -q "last exit code = 128"; then
  bad "$LE — 多为 git fatal；新版应已隔离 git 失败，请重装 launchd 后观察明日"
elif echo "$LE" | rg -q "last exit code = 1"; then
  bad "$LE — 上次失败，查 launchd.err.log"
else
  wrn "${LE:-无法读取 launchctl}"
fi

echo
echo "=== 汇总: PASS=$PASS WARN=$WARN FAIL=$FAIL ==="
if [[ "$FAIL" -gt 0 ]]; then
  echo "结论: 异常 — 先修 FAIL 再补发/改配置"
  exit 1
fi
if [[ "$WARN" -gt 0 ]]; then
  echo "结论: 基本可用，但有 WARN 建议处理"
  exit 0
fi
echo "结论: 健康 — 按当前配置应能正常日更"
exit 0
