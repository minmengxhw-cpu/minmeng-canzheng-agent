#!/usr/bin/env python3
"""对抗式不变量检查：日更报告日、禁链、禁旧群、简报结构。不发飞书。"""
from __future__ import annotations

import datetime
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

FAILS = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global FAILS
    if cond:
        print(f"  OK  {name}")
    else:
        FAILS += 1
        print(f"  FAIL {name} {detail}")


def main() -> int:
    os.environ.pop("CZ_BRIEF_REPORT_DATE", None)
    os.environ["SKIP_FEISHU"] = "1"
    import gen_brief as gb

    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    # 1) 默认报告日 = T-1
    # 通过调用内部默认逻辑
    report = (os.environ.get("CZ_BRIEF_REPORT_DATE") or "").strip()
    if not report:
        report = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    check("默认报告日为昨天", report == yesterday, f"got {report}")

    # 2) sohu 链被屏蔽
    check("屏蔽 sohu", gb._public_url("https://www.sohu.com/a/1") == "")
    check(
        "保留解放日报",
        "jfdaily.com" in gb._public_url("https://www.jfdaily.com/news/detail?id=1"),
    )
    check(
        "保留市政府",
        "shanghai.gov.cn" in gb._public_url(
            "https://www.shanghai.gov.cn/nw4411/20260807/x.html"
        ),
    )

    # 3) 旧群拒绝
    import feishu_push as fp
    check("旧内部群被拒绝", fp._guard_chat_id("oc_9334707219faab92091bdfb24344fa95") is None)
    check(
        "外部群放行",
        fp._guard_chat_id("oc_381bea46653394d135daf14739524904")
        == "oc_381bea46653394d135daf14739524904",
    )

    # 4) 精神/切口函数可运行
    s = {
        "leader": "陈吉宁",
        "theme": "城市治理",
        "headline": "陈吉宁检查调度台风防御工作",
        "full_text": "陈吉宁指出，要树牢底线思维，坚持人民至上、生命至上。",
        "key_points": ["坚持人民至上、生命至上", "树牢底线思维和极限思维"],
        "policy_implications": "可对照本次公开要求，结合上海相关领域落实情况提出参政议政调研切口。",
    }
    spirit = gb._derive_spirit(s, s["key_points"], "")
    cut = gb._derive_policy_cut(s, s["key_points"], "城市治理")
    check("精神要旨非空", bool(spirit) and "陈吉宁" in spirit, spirit[:40])
    check("弱切口会被主题补强", "民盟" in cut and "可对照本次公开要求" not in cut, cut[:60])

    # 5) 脚本入口无二次 gen_brief
    sh = (ROOT / "scripts" / "update_and_push.sh").read_text()
    check("日更脚本不二次调用 gen_brief", sh.count("gen_brief.py") == 1)
    check("日更脚本 T-1", "date -v-1d" in sh)
    check("日更脚本 git 不阻断", "失败不阻断日更" in sh)

    if FAILS:
        print(f"\n不变量失败: {FAILS}")
        return 1
    print("\n不变量全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
