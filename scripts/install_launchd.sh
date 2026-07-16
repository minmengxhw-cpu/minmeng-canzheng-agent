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
rsync -a --exclude 'data/logs/' "$ROOT/" "$RUNTIME_ROOT/"

python3 - "$RUNTIME_ROOT" "$PLIST" "$CENTRAL_PLIST" "$LOG_DIR" <<'PY'
import plistlib, sys
from pathlib import Path

root, plist_path, central_plist_path, log_dir = map(Path, sys.argv[1:])
common_env = {
    "HOME": str(Path.home()),
    "PATH": "/Users/cheer/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
    "MINIMAX_MODEL": "MiniMax-M3.0",
}
data = {
    "Label": "com.minmeng.canzheng-agent",
    "ProgramArguments": [
        "/bin/bash",
        str(root / "scripts" / "update_and_push.sh"),
    ],
    "EnvironmentVariables": common_env,
    "StartCalendarInterval": [
        {"Hour": 9, "Minute": 0},
        {"Hour": 21, "Minute": 0},
    ],
    "StandardOutPath": str(log_dir / "launchd.out.log"),
    "StandardErrorPath": str(log_dir / "launchd.err.log"),
}
plist_path.write_bytes(plistlib.dumps(data))
central = {
    "Label": "com.minmeng.canzheng-agent.central-watch",
    "ProgramArguments": [
        "/bin/bash",
        str(root / "scripts" / "update_central_and_push.sh"),
    ],
    "EnvironmentVariables": common_env,
    "StartCalendarInterval": [
        {"Hour": hour, "Minute": 0} for hour in (7, 10, 13, 16, 19, 22)
    ],
    "StandardOutPath": str(log_dir / "central-watch.out.log"),
    "StandardErrorPath": str(log_dir / "central-watch.err.log"),
}
central_plist_path.write_bytes(plistlib.dumps(central))
print(plist_path)
print(central_plist_path)
PY

launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootout "gui/$(id -u)" "$CENTRAL_PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl bootstrap "gui/$(id -u)" "$CENTRAL_PLIST"
echo "已安装：日常 09:00/21:00 全量更新；中央关注 07:00-22:00 每 3 小时轻量巡检"
