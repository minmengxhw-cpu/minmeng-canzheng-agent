#!/usr/bin/env python3
"""
gen_cuts.py · 候选切口自动生成

从 data/leaders.json + data/phrase_chronology.json 派生候选切口库：
- 仅对累计 ≥ 2 次反复的提法生成（说明已进入市委话语体系）
- 每个切口聚合所有引用了该提法的领导条目原文 / 场合 / 政策启示
- 输出 data/cuts.json 供前端 #cuts 区使用

不调用 LLM；纯结构化派生（确定性、零成本、可重跑）。
D 阶段会在此基础上用 LLM 精修各切口的「机制层」表述和三档成果初稿。
"""

import json
import re
import collections
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ---- 主题 → 机制层 / 验证路径 模板（用于切口骨架）-------------------

THEME_MECHANISM = {
    "科技产业": ["技术成熟度", "产业链协同", "创新生态", "评价与收益机制"],
    "开放发展": ["外事机制", "制度型开放", "枢纽功能", "国际合作清单"],
    "城市治理": ["决策响应", "基层运行", "数据归集", "考核机制"],
    "营商环境": ["政企对接", "诉求流转", "政策直达", "市场化法治化"],
    "民生治理": ["普惠覆盖", "供给侧改革", "差异化保障", "服务可及性"],
    "文化教育": ["资源配置", "公共服务", "文化软实力", "教育公平"],
    "生态环境": ["污染防治", "双碳路径", "生态修复", "评价指标"],
    "法治建设": ["立法供给", "执法规范", "司法保障", "守法责任"],
}

THEME_VERIFICATION = {
    "科技产业": [
        "梳理本市该方向的政策、项目、平台清单（基础研究/产业引导/中试平台）",
        "访谈高校院所、链主企业、初创公司、投资机构 4 类主体",
        "复盘 3-5 个典型案例的全周期：立项 → 成果 → 转化 → 产业化",
    ],
    "开放发展": [
        "梳理近 2 年市府对外签约清单与履约进度",
        "访谈外资外贸企业、外事服务机构、行业商会负责人",
        "对照其他直辖市/重点城市的同类机制，找上海特殊性",
    ],
    "城市治理": [
        "调研区/委办局相应模块的真实运行链路（不是组织架构）",
        "访谈一线执行者 + 服务对象两端，找断点而非赞美",
        "梳理已有数据归集口径，识别『已采但未联』『已联但未用』的环节",
    ],
    "营商环境": [
        "梳理该话题对应的政策清单与落地反馈",
        "访谈企业方诉求与委办局服务方两端",
        "复盘 2-3 个典型企业从诉求提出到问题解决的全流程时长与节点",
    ],
    "民生治理": [
        "梳理该话题对应的现有保障/服务清单与覆盖人群规模",
        "访谈服务对象与一线工作者两端，识别盲区",
        "对照长三角同口径数据，识别上海差距与亮点",
    ],
    "文化教育": [
        "梳理本市该方向的资源配置现状与公共服务清单",
        "访谈一线机构与服务对象，识别可感差距",
        "对照国际/国内同类城市经验，提炼上海可借鉴空间",
    ],
    "生态环境": [
        "梳理该话题对应的现有指标体系与达标情况",
        "访谈执法部门、企业方、专家三方",
        "对照『十四五』目标完成度与『十五五』承诺方向",
    ],
    "法治建设": [
        "梳理本市该领域的立法 / 规范性文件清单",
        "访谈执法机构、被监管方、法律专家",
        "对照中央立法精神，识别地方实施细则的可操作空间",
    ],
}


def stable_id(s: str) -> str:
    """phrase → 稳定短 hash id"""
    h = hashlib.md5(s.encode("utf-8")).hexdigest()[:8]
    return f"cut-{h}"


def clean_phrase(p: str) -> str:
    p = (p or "").strip().strip('"').strip('"').strip("'").strip("「").strip("」")
    return p


def derive_keywords(phrase: str, related_items: list) -> list:
    """合并相关条目的 keywords / subthemes，保留与提法相关的 Top 6"""
    pool = collections.Counter()
    for it in related_items:
        for kw in (it.get("keywords") or []):
            kw = (kw or "").strip()
            if 1 < len(kw) < 12:
                pool[kw] += 1
        for st in (it.get("subthemes") or []):
            st = (st or "").strip()
            if 1 < len(st) < 12:
                pool[st] += 1
    # 不要包含提法本身（保留信息量）
    return [k for k, _ in pool.most_common(8) if k not in phrase][:6]


def build_thesis(phrase: str, theme: str, count: int, first_date: str,
                 first_occasion: str, last_date: str) -> str:
    """构造切口的判断式 thesis（不空泛、含证据）"""
    return (f"市委在 {first_date}「{first_occasion[:18]}」首次提出「{phrase}」，"
            f"至 {last_date} 已累计反复 {count} 次，"
            f"说明这是 {theme} 主线上需要从『喊出来』推进到『做出来』的关键议题，"
            f"是民盟参政议政可以介入的『机制层』切口。")


def build_cut_summary(phrase: str, theme: str) -> str:
    """切口的『一句话切口』描述"""
    return f"围绕「{phrase}」从市委话语落到{theme}领域机制层的可测度执行抓手"


def build_outputs(phrase: str, theme: str, related_items: list,
                  count: int, first_occasion: str) -> dict:
    """三档成果骨架：社情民意 / 提案 / 课题"""
    # 抓取相关条目里最具体的几条 policy_implications，作为『问题』的素材
    impls = [it.get("policy_implications", "") for it in related_items
             if it.get("policy_implications")]
    impls = [s for s in impls if s and len(s) > 30][:2]
    impl_hint = "；".join(s[:60] for s in impls) if impls else f"{theme}方向的机制层断点"

    return {
        "brief": {
            "title": f"关于推进「{phrase}」落地的若干迹象与建议",
            "blocks": [
                ["问题", f"市委已累计 {count} 次反复提出「{phrase}」，但从话语到落地之间存在断点。{impl_hint[:80]}。"],
                ["机制", f"现有政策链条偏重『提出与部署』，对中间环节（{('、'.join(THEME_MECHANISM.get(theme, ['执行机制'])[:3]))}）的标准化、可监测、可问责安排不足。"],
                ["建议", f"围绕「{phrase}」建立一份『6 个迹象』清单，区分『已立住』与『在路上』，每季度由委办局自报 + 第三方核验，作为推进度的可观察锚点。"],
            ],
        },
        "proposal": {
            "title": f"关于完善「{phrase}」在{theme}领域机制层抓手的建议",
            "blocks": [
                ["案由", f"「{phrase}」是市委 {first_occasion[:24]} 等场合反复强调的核心提法，体现{theme}主线的战略选择。"],
                ["主要问题", f"当前在该方向上，『喊到』与『做到』之间还存在 3 类断点：政策颗粒度、跨部门协同、长周期评价。"],
                ["建议措施", f"一是出台「{phrase}」的实施细则；二是建立跨委办局联合工作机制；三是设立 3 年滚动评估指标，公开发布关键进展节点。"],
            ],
        },
        "research": {
            "title": f"上海「{phrase}」的实施路径与可监测指标体系研究",
            "blocks": [
                ["研究问题", f"「{phrase}」从市委话语转化为可监测、可问责执行的机制路径是什么。"],
                ["调研路径", f"政策梳理、{theme}领域专家访谈、{('、'.join(THEME_MECHANISM.get(theme, ['执行机制'])[:2]))}的横向对照、典型项目复盘。"],
                ["成果结构", "现状判断、机制堵点、关键变量识别、可测度指标体系、阶段性试点清单、长三角横向对照、政策建议。"],
            ],
        },
    }


def build_signal_links(phrase: str, related_items: list) -> list:
    """切口的『信号链』：把相关领导条目的 url + 日期 + headline 保留下来，前端可点击溯源"""
    out = []
    for it in related_items[:5]:  # 最多 5 条溯源
        out.append({
            "date": it.get("date", ""),
            "headline": (it.get("headline") or it.get("title") or "")[:60],
            "occasion": it.get("occasion", "")[:30],
            "url": it.get("url", ""),
            "leader": it.get("leader", "") or it.get("role", ""),
        })
    return out


def main():
    leaders = json.loads((ROOT / "data" / "leaders.json").read_text("utf-8"))
    chronology = json.loads((ROOT / "data" / "phrase_chronology.json").read_text("utf-8"))

    # 取累计 ≥ 2 次的提法
    candidates = [c for c in chronology if c.get("count", 0) >= 2]
    candidates.sort(key=lambda x: (-x["count"], x["first_date"]))

    # 索引 phrase → 相关 leaders 条目
    phrase_to_items = collections.defaultdict(list)
    for it in leaders:
        for p in (it.get("new_phrasing") or []):
            phrase_to_items[clean_phrase(p)].append(it)

    cuts = []
    for c in candidates:
        phrase = clean_phrase(c["phrase"])
        if not phrase:
            continue
        related = phrase_to_items.get(phrase, [])
        if not related:
            continue
        related.sort(key=lambda x: x.get("date", ""))
        first_it = related[0]
        last_it = related[-1]
        theme = c.get("first_theme") or first_it.get("theme", "")
        if not theme:
            theme = "城市治理"

        keywords = derive_keywords(phrase, related)
        thesis = build_thesis(
            phrase, theme, c["count"],
            c.get("first_date", ""), c.get("first_occasion", ""),
            last_it.get("date", ""),
        )
        cut_summary = build_cut_summary(phrase, theme)
        outputs = build_outputs(phrase, theme, related, c["count"], c.get("first_occasion", ""))
        signal_links = build_signal_links(phrase, related)

        cuts.append({
            "id": stable_id(phrase),
            "theme": theme,
            "title": f"「{phrase}」的机制层落地",
            "phrase": phrase,
            "count": c["count"],
            "first_date": c.get("first_date", ""),
            "first_occasion": c.get("first_occasion", ""),
            "last_date": last_it.get("date", ""),
            "keywords": keywords,
            "thesis": thesis,
            "mechanism": THEME_MECHANISM.get(theme, ["机制层"]),
            "cut": cut_summary,
            "verification": THEME_VERIFICATION.get(theme, [
                "梳理该领域现状与已有政策链路",
                "访谈关键利益相关方两端",
                "复盘典型案例的全流程节点",
            ]),
            "outputs": outputs,
            "signal_links": signal_links,
            "_auto": True,
            "_generator_version": "v1",
        })

    # 输出
    out_path = ROOT / "data" / "cuts.json"
    out_path.write_text(
        json.dumps(cuts, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✓ 生成 {len(cuts)} 个候选切口 → {out_path}")
    # 按主题分布
    by_theme = collections.Counter(c["theme"] for c in cuts)
    for t, n in by_theme.most_common():
        print(f"   {t}: {n}")


if __name__ == "__main__":
    main()
