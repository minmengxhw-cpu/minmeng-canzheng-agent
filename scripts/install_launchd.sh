#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.minmeng.canzheng-agent"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$ROOT/data/logs"
mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"

python3 - "$ROOT" "$PLIST" "$LOG_DIR" <<'PY'
import plistlib, sys
from pathlib import Path

root, plist_path, log_dir = map(Path, sys.argv[1:])
data = {
    "Label": "com.minmeng.canzheng-agent",
    "ProgramArguments": [
        str(root / "scripts" / "update_all.py"),
        "--max-pages", "6",
        "--draft-limit", "3",
    ],
    "WorkingDirectory": str(root),
    "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
    "StartCalendarInterval": [{"Hour": 7, "Minute": 30}, {"Hour": 19, "Minute": 30}],
    "StandardOutPath": str(log_dir / "launchd.out.log"),
    "StandardErrorPath": str(log_dir / "launchd.err.log"),
}
plist_path.write_bytes(plistlib.dumps(data))
print(plist_path)
PY

launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "已安装：每天 07:30 和 19:30 自动更新（最近 6 页，最多 3 个初稿）"
