#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

git fetch origin main
git merge --ff-only origin/main

python3 scripts/update_all.py --max-pages 6 --skip-drafts

git add data/*.json briefs/*.md
if git diff --cached --quiet; then
  echo "没有新的数据变化"
  exit 0
fi

git config user.name "minmeng-data-bot"
git config user.email "minmeng-data-bot@users.noreply.github.com"
git commit -m "refresh data $(date +%F)"
git push origin main
