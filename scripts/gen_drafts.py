#!/usr/bin/env python3
"""
gen_drafts.py · 三档成品物初稿自动生成

对 data/cuts.json 里的每个切口，调用 LLM 生成：
- brief（社情民意）：600-900 字完整稿
- proposal（提案）：800-1200 字四段式完整稿
- research（课题）：600-900 字大纲式完整稿

输出 data/drafts.json：{cut_id: {brief, proposal, research}}
增量逻辑：已生成的 cut_id 跳过；新切口才调用 LLM。

用法：
  python3 scripts/gen_drafts.py                 # 增量
  REGEN_ALL=1 python3 scripts/gen_drafts.py     # 全部重生成
  LIMIT=5 python3 scripts/gen_drafts.py         # 只跑前 5 条（测试）
"""

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from grok_cli import grok_json

ROOT = Path(__file__).resolve().parent.parent
CUTS_PATH = ROOT / "data" / "cuts.json"
DRAFTS_PATH = ROOT / "data" / "drafts.json"
LEADERS_PATH = ROOT / "data" / "leaders.json"

REGEN_ALL = os.environ.get("REGEN_ALL", "0") == "1"
LIMIT = int(os.environ.get("LIMIT", "0"))  # 0 = 不限

SYSTEM_PROMPT = """你是上海民盟市委参政议政研究助理。任务：把一个候选切口的骨架填写成可用的初稿。

写作原则：
1. 判断式语言：直接给判断、给抓手，不要"领导强调""我们认为"等套话
2. 上海本地视角：所有举例尽量用上海本地的政策、机构、企业、数字
3. 机制层切入：聚焦"怎么做"的可观察、可测度、可问责安排，不要停留在"要重视"
4. 民盟立场：从参政党监督建议视角出发，体谅但不附和，专业但不学究
5. 数字 + 名词：每段至少 1 个具体数字或具体机构名，避免泛泛而谈
6. 严禁出现：内部技术细节、模型名称、自动生成痕迹、过分肯定句式

输出严格的 JSON 格式，不要任何额外解释。"""

# ---- 三种 prompt 模板 ----------------------------------------------

def build_user_prompt(cut: dict, draft_type: str, related_excerpts: list) -> str:
    """构造给 LLM 的 user 消息"""
    phrase = cut["phrase"]
    theme = cut["theme"]
    count = cut["count"]
    first_date = cut.get("first_date", "")
    first_occasion = cut.get("first_occasion", "")
    mechanism = "、".join(cut.get("mechanism", []))
    verification = "\n".join(f"- {v}" for v in cut.get("verification", []))

    # 相关原文素材（最多 3 条）
    excerpts_text = ""
    if related_excerpts:
        excerpts_text = "\n\n## 信号源原文素材（仅供参考，不要直接引用大段）\n"
        for ex in related_excerpts[:3]:
            excerpts_text += f"\n[{ex.get('date','')}] [{ex.get('leader','')}] {ex.get('occasion','')}\n"
            if ex.get("summary"):
                excerpts_text += f"摘要：{ex['summary'][:200]}\n"
            if ex.get("policy_implications"):
                excerpts_text += f"政策启示：{ex['policy_implications'][:150]}\n"

    common = f"""# 切口信息
- 锚点提法：「{phrase}」
- 主题：{theme}
- 累计反复：{count} 次
- 首发：{first_date} · {first_occasion}
- 涉及机制：{mechanism}
- 核验路径：
{verification}
{excerpts_text}
"""

    if draft_type == "brief":
        spec = """
# 任务
请基于上述切口信息，撰写一篇**社情民意信息**初稿，600-900 字。

格式要求：
- 标题：一句话判断式标题（25 字内，体现"问题—机制—建议"）
- 正文：三段式
  · 第一段「问题」(150-200 字)：直接给出当前现象 + 至少 1 个上海本地具体案例/数字
  · 第二段「机制」(200-250 字)：拆解为什么会这样，从"政策颗粒度 / 跨部门协同 / 长周期评价"三个机制层切入
  · 第三段「建议」(150-200 字)：3 条具体可操作的建议，每条带"由 X 部门牵头"或"在 Y 试点"的责任主语

输出 JSON：
{
  "title": "标题",
  "body": "正文全文（含三段，段间用空行分隔）"
}"""
    elif draft_type == "proposal":
        spec = """
# 任务
请基于上述切口信息，撰写一份**政协提案**初稿，800-1200 字。

格式要求：
- 案由 (50-80 字)：一句话说清楚问题与提案目的
- 主要问题 (250-350 字)：分 3 个层次说清问题，每层 1-2 句具体描述 + 1 个上海本地例证
- 建议措施 (400-600 字)：4 条具体措施，每条独立成段，措施要可操作（含责任主体 + 时间节点 + 评估指标）
- 第四段「评估与公开」(80-150 字)：怎么评估、怎么向社会公开进展节点

输出 JSON：
{
  "title": "提案题目（25 字内）",
  "by_who": "案由",
  "problems": "主要问题",
  "measures": "建议措施（4 条独立成段）",
  "evaluation": "评估与公开"
}"""
    else:  # research
        spec = """
# 任务
请基于上述切口信息，撰写一份**民盟参政议政课题**大纲，600-900 字。

格式要求：
- 研究问题 (80-120 字)：一句话核心问题 + 2-3 个子问题
- 研究意义 (100-150 字)：从上海"十五五"规划接续、跨部门协同、可监测可问责三个维度说意义
- 调研路径 (200-300 字)：分 4 步走（政策梳理 → 关键访谈 → 案例复盘 → 横向对照），每步说清做什么、谁来做
- 关键变量识别 (100-150 字)：列出 4-5 个需要观测的核心变量
- 成果结构 (100-150 字)：成果目录大纲 6-8 个章节标题

输出 JSON：
{
  "title": "课题题目（30 字内）",
  "research_question": "研究问题",
  "significance": "研究意义",
  "approach": "调研路径",
  "key_variables": "关键变量识别",
  "outline": "成果结构"
}"""

    return common + "\n" + spec


def llm_call(system: str, user: str, max_tokens: int = 1800) -> dict:
    """调一次 LLM，返回 JSON"""
    return grok_json(system, user, max_tokens=max_tokens, temperature=0.4)


def main():
    cuts = json.loads(CUTS_PATH.read_text("utf-8"))
    leaders = json.loads(LEADERS_PATH.read_text("utf-8"))

    # 现有 drafts
    existing = {}
    if DRAFTS_PATH.exists() and not REGEN_ALL:
        try:
            existing = json.loads(DRAFTS_PATH.read_text("utf-8"))
        except Exception:
            existing = {}

    # 索引 phrase → 相关 leaders 条目（含 summary / policy_implications）
    phrase_to_items = {}
    for it in leaders:
        for p in (it.get("new_phrasing") or []):
            phrase_to_items.setdefault(p.strip(), []).append({
                "date": it.get("date", ""),
                "leader": it.get("leader", "") or it.get("role", ""),
                "occasion": it.get("occasion", ""),
                "summary": it.get("summary", ""),
                "policy_implications": it.get("policy_implications", ""),
            })

    # 决定要跑的列表
    queue = []
    for c in cuts:
        if c["id"] in existing and not REGEN_ALL:
            continue
        queue.append(c)
    if LIMIT:
        queue = queue[:LIMIT]

    if not queue:
        print("✓ 全部切口已有初稿，无需重跑（REGEN_ALL=1 可强制全部重生成）")
        return

    print(f"待生成：{len(queue)} 个切口 × 3 档 = {len(queue)*3} 次 LLM 调用")
    start = time.time()
    drafts = dict(existing)

    for i, c in enumerate(queue, 1):
        cid = c["id"]
        phrase = c["phrase"]
        related = phrase_to_items.get(phrase, [])
        print(f"\n[{i}/{len(queue)}] {cid} · 「{phrase}」({c['theme']}, 反复 {c['count']}×)")

        out = {"cut_id": cid, "phrase": phrase, "theme": c["theme"]}
        for draft_type in ["brief", "proposal", "research"]:
            try:
                prompt = build_user_prompt(c, draft_type, related)
                res = llm_call(SYSTEM_PROMPT, prompt)
                out[draft_type] = res
                title = res.get("title", "")
                print(f"  ✓ {draft_type:10s} → {title[:30]}")
                time.sleep(0.3)  # 速率限制兜底
            except Exception as e:
                print(f"  ✗ {draft_type:10s} 失败：{e}")
                out[draft_type] = {"error": str(e)[:200]}

        drafts[cid] = out

        # 每 5 条保存一次，避免中途断电丢失
        if i % 5 == 0:
            DRAFTS_PATH.write_text(
                json.dumps(drafts, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    DRAFTS_PATH.write_text(
        json.dumps(drafts, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    elapsed = time.time() - start
    print(f"\n✓ 完成 · 用时 {elapsed:.1f}s · drafts.json 共 {len(drafts)} 个切口的初稿")


if __name__ == "__main__":
    main()
