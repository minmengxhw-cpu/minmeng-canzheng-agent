#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

git fetch origin main
git merge --ff-only origin/main

SINCE="$(date -v-7d +%F)"
MINIMAX_MODEL="${MINIMAX_MODEL:-MiniMax-M3.0}" \
  SINCE="$SINCE" MAX_PAGES=3 python3 scripts/fetch_central.py

git add data/central_leaders.json data/central_latest.json
if git diff --cached --quiet; then
  echo "中央关注没有新增变化"
  exit 0
fi

git config user.name "minmeng-data-bot"
git config user.email "minmeng-data-bot@users.noreply.github.com"
git commit -m "refresh central watch $(date +%F-%H%M)"
git push origin main
