#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_ROOT="$HOME/Library/Application Support/minmeng-canzheng-agent"
LABEL="com.minmeng.canzheng-agent"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$RUNTIME_ROOT/data/logs"
mkdir -p "$HOME/Library/LaunchAgents" "$RUNTIME_ROOT" "$LOG_DIR"

# macOS launchd 对 Documents 下的脚本可能受 TCC 限制；把运行副本放到用户 Library。
rsync -a --exclude 'data/logs/' "$ROOT/" "$RUNTIME_ROOT/"

python3 - "$RUNTIME_ROOT" "$PLIST" "$LOG_DIR" <<'PY'
import plistlib, sys
from pathlib import Path

root, plist_path, log_dir = map(Path, sys.argv[1:])
data = {
    "Label": "com.minmeng.canzheng-agent",
    "ProgramArguments": [
        "/bin/bash",
        str(root / "scripts" / "update_and_push.sh"),
    ],
    "EnvironmentVariables": {
        "HOME": str(Path.home()),
        "PATH": "/Users/cheer/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        "MINIMAX_MODEL": "MiniMax-M3.0",
    },
    "StartCalendarInterval": [
        {"Hour": 9, "Minute": 0},
        {"Hour": 21, "Minute": 0},
    ],
    "StandardOutPath": str(log_dir / "launchd.out.log"),
    "StandardErrorPath": str(log_dir / "launchd.err.log"),
}
plist_path.write_bytes(plistlib.dumps(data))
print(plist_path)
PY

launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "已安装：每天 09:00 和 21:00 自动更新并推送 GitHub（MiniMax CLI）"
