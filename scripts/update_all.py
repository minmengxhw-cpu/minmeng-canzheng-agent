#!/usr/bin/env python3
"""Run the complete CZ Agent data refresh pipeline.

可靠性原则：
  - 任一核心采集失败就停止出报，禁止把抓取失败包装成“无新增”
  - 采集成功后才生成简报与候选切口
  - 各步独立记退出码，真实反映定时任务健康状态
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

    # 两条核心通道必须都成功，避免用旧数据生成误导性的“无新增”简报。
    codes.append(run("抓取中央领导全国考察调研", "fetch_central.py", fetch_env, critical=True))
    codes.append(run("抓取并分析领导动向", "fetch_leaders.py", fetch_env, critical=True))
    if codes[0] != 0 or codes[1] != 0:
        print(
            "ERROR: 核心采集不完整，拒绝生成简报；请修复后重跑",
            file=sys.stderr,
            flush=True,
        )
        return codes[0] or codes[1] or 1

    # 简报：关键步骤，必须尽量成功
    codes.append(run("生成动向速递", "gen_brief.py", critical=True))

    # 切口：附属
    codes.append(run("重算候选切口", "gen_cuts.py", critical=False))
    if not args.skip_drafts:
        draft_env = {"LIMIT": str(args.draft_limit)} if args.draft_limit else None
        codes.append(run("生成切口初稿", "gen_drafts.py", draft_env, critical=False))

    print(f"\n更新完成：{datetime.now().isoformat(timespec='seconds')}", flush=True)
    if codes[2] != 0:  # gen_brief
        return codes[2]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
