#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_ROOT="$HOME/Library/Application Support/minmeng-canzheng-agent"
LABEL="com.minmeng.canzheng-agent"
CENTRAL_LABEL="com.minmeng.canzheng-agent.central-watch"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
CENTRAL_PLIST="$HOME/Library/LaunchAgents/$CENTRAL_LABEL.plist"
LOG_DIR="$RUNTIME_ROOT/data/logs"
mkdir -p "$HOME/Library/LaunchAgents" "$RUNTIME_ROOT" "$LOG_DIR"

# macOS launchd 对 Documents 下的脚本可能受 TCC 限制；把运行副本放到用户 Library。
# 只增量覆盖同名项目文件，不删除运行目录里的任何本地文件、密钥或日志。
rsync -a \
  --exclude 'data/logs/' \
  --exclude '.env' \
  --exclude '.env.*' \
  "$ROOT/" "$RUNTIME_ROOT/"

python3 - "$RUNTIME_ROOT" "$PLIST" "$LOG_DIR" <<'PY'
import os, plistlib, sys
from pathlib import Path

root, plist_path, log_dir = map(Path, sys.argv[1:])
common_env = {
    "HOME": str(Path.home()),
    "PATH": "/Users/cheer/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
    "MINIMAX_CLI": "/opt/homebrew/bin/mmx",
    "MINIMAX_MODEL": "MiniMax-M3",
    "MINIMAX_TIMEOUT": "240",
}
# 从本地 .env 注入飞书 Webhook（不写进仓库）
env_candidates = [
    root / ".env",
    Path.home() / "Library/Application Support/minmeng-canzheng-agent/.env",
    Path.home() / ".config/minmeng-canzheng-agent/env",
]
for env_path in env_candidates:
    if not env_path.is_file():
        continue
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k in (
            "FEISHU_WEBHOOK",
            "FEISHU_WEBHOOK_SECRET",
            "FEISHU_CHAT_ID",
            "FEISHU_JOIN_URL",
            "FEISHU_SITE_URL",
            "FEISHU_PUSH_ALWAYS",
            "LARK_CLI",
            "BRIEF_WEBHOOK",
        ):
            common_env[k] = v
    break
data = {
    "Label": "com.minmeng.canzheng-agent",
    "ProgramArguments": [
        "/bin/bash",
        str(root / "scripts" / "update_and_push.sh"),
    ],
    "EnvironmentVariables": common_env,
    "StartCalendarInterval": [
        {"Hour": 8, "Minute": 30},
        {"Hour": 20, "Minute": 30},
    ],
    "StandardOutPath": str(log_dir / "launchd.out.log"),
    "StandardErrorPath": str(log_dir / "launchd.err.log"),
}
plist_path.write_bytes(plistlib.dumps(data))
print(plist_path)
if "FEISHU_WEBHOOK" in common_env:
    print("已注入 FEISHU_WEBHOOK（飞书 Webhook 推送已启用）")
elif "FEISHU_CHAT_ID" in common_env:
    print("已注入 FEISHU_CHAT_ID（lark-cli 应用机器人推送已启用）")
else:
    print("未找到 FEISHU_WEBHOOK / FEISHU_CHAT_ID：请写到 Application Support 下 .env 后重跑本脚本")
PY

launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootout "gui/$(id -u)" "$CENTRAL_PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "旧 central-watch 任务已停用，原 plist 文件保留不删除"
echo "已安装：每天 08:30/20:30 更新全国中央考察与上海领导动态（MiniMax CLI · MiniMax-M3）"
