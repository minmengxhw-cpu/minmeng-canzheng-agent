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
rsync -a --delete --exclude 'data/logs/' "$ROOT/" "$RUNTIME_ROOT/"

python3 - "$RUNTIME_ROOT" "$PLIST" "$LOG_DIR" <<'PY'
import plistlib, sys
from pathlib import Path

root, plist_path, log_dir = map(Path, sys.argv[1:])
common_env = {
    "HOME": str(Path.home()),
    "PATH": "/Users/cheer/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
    "GROK_MODEL": "grok-4.5",
    "GROK_PERMISSION_MODE": "bypassPermissions",
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
print(plist_path)
PY

launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootout "gui/$(id -u)" "$CENTRAL_PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
python3 - "$CENTRAL_PLIST" <<'PY'
import sys
from pathlib import Path
Path(sys.argv[1]).unlink(missing_ok=True)
PY
echo "已安装：每天 09:00/21:00 更新全国中央考察与上海领导动态（Grok CLI）"
