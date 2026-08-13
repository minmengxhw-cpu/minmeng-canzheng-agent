#!/usr/bin/env python3
"""Run the complete CZ Agent data refresh pipeline.

可靠性原则：
  - 采集失败尽量不阻断简报（有历史数据也应生成并推送）
  - 简报步骤优先保证执行
  - 各步独立记退出码，最终返回最严重失败码
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent


def run(label: str, script: str, extra_env: Optional[Dict[str, str]] = None,
        *, critical: bool = False) -> int:
    print(f"\n=== {label} ===", flush=True)
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    try:
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script)],
            cwd=ROOT,
            env=env,
            check=False,
        )
        rc = int(r.returncode or 0)
    except Exception as exc:
        print(f"ERROR: {label} 无法启动: {exc}", file=sys.stderr, flush=True)
        rc = 1
    if rc != 0:
        level = "ERROR" if critical else "WARN"
        print(f"{level}: {label} 退出码={rc}", flush=True)
    return rc


def main() -> int:
    parser = argparse.ArgumentParser(description="更新 CZ Agent 全部数据")
    parser.add_argument("--since", help="仅抓取指定日期之后的数据，格式 YYYY-MM-DD")
    parser.add_argument("--max-pages", type=int, help="抓取列表页上限")
    parser.add_argument("--skip-drafts", action="store_true", help="跳过 LLM 初稿生成")
    parser.add_argument("--draft-limit", type=int, help="本次最多生成多少个新切口初稿")
    args = parser.parse_args()

    fetch_env: Dict[str, str] = {}
    if args.since:
        fetch_env["SINCE"] = args.since
    if args.max_pages:
        fetch_env["MAX_PAGES"] = str(args.max_pages)

    print(f"CZ Agent 自动更新开始：{datetime.now().isoformat(timespec='seconds')}", flush=True)
    codes: List[int] = []

    # 采集：失败不阻断简报（仍可用历史 leaders / central 出报）
    codes.append(run("抓取中央领导全国考察调研", "fetch_central.py", fetch_env, critical=False))
    codes.append(run("抓取并分析领导动向", "fetch_leaders.py", fetch_env, critical=False))

    # 简报：关键步骤，必须尽量成功
    codes.append(run("生成动向速递", "gen_brief.py", critical=True))

    # 切口：附属
    codes.append(run("重算候选切口", "gen_cuts.py", critical=False))
    if not args.skip_drafts:
        draft_env = {"LIMIT": str(args.draft_limit)} if args.draft_limit else None
        codes.append(run("生成切口初稿", "gen_drafts.py", draft_env, critical=False))

    print(f"\n更新完成：{datetime.now().isoformat(timespec='seconds')}", flush=True)
    # 飞书日更成败只看简报；采集失败已 WARN，不得把 launchd 打成失败
    if codes[2] != 0:  # gen_brief
        return codes[2]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
