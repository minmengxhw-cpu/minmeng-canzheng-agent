#!/usr/bin/env python3
"""Run the complete CZ Agent data refresh pipeline."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

ROOT = Path(__file__).resolve().parent.parent


def run(label: str, script: str, extra_env: Optional[Dict[str, str]] = None) -> None:
    print(f"\n=== {label} ===", flush=True)
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    subprocess.run([sys.executable, str(ROOT / "scripts" / script)], cwd=ROOT, env=env, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="更新 CZ Agent 全部数据")
    parser.add_argument("--since", help="仅抓取指定日期之后的数据，格式 YYYY-MM-DD")
    parser.add_argument("--max-pages", type=int, help="抓取列表页上限")
    parser.add_argument("--skip-drafts", action="store_true", help="跳过 MiniMax 初稿生成")
    parser.add_argument("--draft-limit", type=int, help="本次最多生成多少个新切口初稿")
    args = parser.parse_args()

    fetch_env = {}
    if args.since:
        fetch_env["SINCE"] = args.since
    if args.max_pages:
        fetch_env["MAX_PAGES"] = str(args.max_pages)

    print(f"CZ Agent 自动更新开始：{datetime.now().isoformat(timespec='seconds')}")
    run("抓取并分析领导动向", "fetch_leaders.py", fetch_env)
    run("生成动向速递", "gen_brief.py")
    run("重算候选切口", "gen_cuts.py")
    if not args.skip_drafts:
        draft_env = {"LIMIT": str(args.draft_limit)} if args.draft_limit else None
        run("生成切口初稿", "gen_drafts.py", draft_env)
    print(f"\n更新完成：{datetime.now().isoformat(timespec='seconds')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
