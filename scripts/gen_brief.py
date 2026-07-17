#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CZ Agent · 动向速递（变化主动推送 A）
每次抓取后跑：对比最新一期，生成「当日速递 + 近7天回顾」简报。
输出：
  - data/brief_latest.json   给前端「动向速递」卡片读取
  - briefs/YYYY-MM-DD.md      留档
推送（可选，主动推到手机）：
  - FEISHU_WEBHOOK           飞书群自定义机器人（推荐）
  - FEISHU_WEBHOOK_SECRET    可选签名密钥
  - FEISHU_SITE_URL          卡片链接，默认 GitHub Pages
  - FEISHU_PUSH_ALWAYS=1     无新增也推送（默认：有新增才推；设置 1 则每次都推）
  - BRIEF_WEBHOOK            兼容旧的 Mattermost/企业微信 text webhook
不调大模型，纯规则生成，稳定可靠。
"""
import json
import os
import datetime
import urllib.request
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

LEADERS = os.path.join(ROOT, "data", "leaders.json")
OUT_JSON = os.path.join(ROOT, "data", "brief_latest.json")
BRIEF_DIR = os.path.join(ROOT, "briefs")
SITE_DEFAULT = "https://minmengxhw-cpu.github.io/minmeng-canzheng-agent/"


def np_of(s):
    v = s.get("new_phrasing")
    return v if isinstance(v, list) else ([v] if v else [])


def dminus(ymd, n):
    y, m, d = map(int, ymd.split("-"))
    return (datetime.date(y, m, d) - datetime.timedelta(days=n)).isoformat()


def _push_feishu(summary: str, today_items: list, maxd: str, md_text: str) -> None:
    """主动推送到飞书（手机 App 会收到通知）。"""
    hook = os.environ.get("FEISHU_WEBHOOK", "").strip()
    if not hook:
        return
    always = os.environ.get("FEISHU_PUSH_ALWAYS", "").strip() in ("1", "true", "yes")
    if not today_items and not always:
        print("brief: 飞书跳过（当日无新增；设 FEISHU_PUSH_ALWAYS=1 可强制推）")
        return
    try:
        from feishu_push import push_brief_card, push_text
    except ImportError:
        # 同目录直接 import
        import importlib.util
        path = os.path.join(ROOT, "scripts", "feishu_push.py")
        spec = importlib.util.spec_from_file_location("feishu_push", path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(mod)
        push_brief_card = mod.push_brief_card
        push_text = mod.push_text

    lines = []
    for i in today_items[:6]:
        who = f"〔{i['role']}〕" if i.get("role") else ""
        head = (i.get("headline") or "")[:80]
        theme = i.get("theme") or ""
        lines.append(f"{who}{head}" + (f" · {theme}" if theme else ""))
    title = f"📍 CZ Agent 动向速递 · {maxd}"
    r = push_brief_card(title, summary, item_lines=lines or None)
    if r.get("ok"):
        print("brief: 已主动推送飞书")
        return
    # 卡片失败则退回纯文本
    print(f"brief: 飞书卡片失败 {r}，尝试文本…")
    r2 = push_text(f"{title}\n\n{summary}\n\n{SITE_DEFAULT}")
    if r2.get("ok"):
        print("brief: 已主动推送飞书（文本）")
    else:
        print(f"brief: 飞书推送失败 {r2}")


def _push_legacy_webhook(md_text: str, today_items: list) -> None:
    hook = os.environ.get("BRIEF_WEBHOOK", "").strip()
    if not hook:
        return
    always = os.environ.get("FEISHU_PUSH_ALWAYS", "").strip() in ("1", "true", "yes")
    if not today_items and not always:
        return
    try:
        payload = {"text": "**📍 CZ Agent 动向速递**\n\n" + md_text}
        req = urllib.request.Request(
            hook,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=15)
        print("brief: 已推送 BRIEF_WEBHOOK")
    except Exception as e:
        print(f"brief: BRIEF_WEBHOOK 推送失败 {e}")


def main():
    data = json.load(open(LEADERS, encoding="utf-8"))
    data = [s for s in data if s.get("date")]
    if not data:
        return
    maxd = max(s["date"] for s in data)

    # 当日速递
    today = [s for s in data if s["date"] == maxd]
    today.sort(key=lambda s: s.get("role_rank", 9))
    today_items = []
    for s in today:
        today_items.append({
            "date": s["date"], "role": s.get("role", ""), "leader": s.get("leader", ""),
            "theme": s.get("theme", ""), "headline": s.get("headline", "") or s.get("occasion", ""),
            "url": s.get("url", ""), "phrases": np_of(s),
        })
    today_phrases = sum(len(i["phrases"]) for i in today_items)

    # 近 7 天回顾
    since = dminus(maxd, 6)
    week = [s for s in data if s["date"] >= since]
    week_phrases = sum(len(np_of(s)) for s in week)
    theme_cnt = {}
    for s in week:
        t = s.get("theme")
        if t:
            theme_cnt[t] = theme_cnt.get(t, 0) + 1
    hot = sorted(theme_cnt.items(), key=lambda x: -x[1])[:3]
    hot_txt = "、".join(f"{t}({n})" for t, n in hot) if hot else "—"

    md = maxd.replace("-", ".")[5:]  # 06.11
    if today_items:
        summary = f"{md} 新增 {len(today_items)} 条公开信号、{today_phrases} 条新提法；近 7 天最活跃主题：{hot_txt}。"
    else:
        summary = f"{md} 无新增信号；近 7 天共 {len(week)} 条信号、{week_phrases} 条新提法，最活跃主题：{hot_txt}。"

    brief = {
        "date": maxd,
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "summary": summary,
        "today": {"n_signals": len(today_items), "n_phrases": today_phrases, "items": today_items},
        "week": {"since": since, "n_signals": len(week), "n_phrases": week_phrases, "hot_themes": hot},
    }
    json.dump(brief, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # Markdown 留档
    os.makedirs(BRIEF_DIR, exist_ok=True)
    lines = [f"# 动向速递 · {maxd}", "", f"> {summary}", ""]
    if today_items:
        lines.append("## 当日新增")
        for i in today_items:
            who = f"〔{i['role']}〕" if i["role"] else ""
            lines.append(f"### {who}{i['headline']}　`{i['theme']}`")
            if i["url"]:
                lines.append(f"原文：{i['url']}")
            if i["phrases"]:
                lines.append("新提法：")
                lines += [f"- {p}" for p in i["phrases"]]
            lines.append("")
    else:
        lines.append("_当日无新增信号。_\n")
    lines.append(f"## 近 7 天（{since} ~ {maxd}）")
    lines.append(f"- 公开信号 {len(week)} 条 · 新提法 {week_phrases} 条")
    lines.append(f"- 最活跃主题：{hot_txt}")
    lines.append("")
    lines.append(f"网页：{os.environ.get('FEISHU_SITE_URL', SITE_DEFAULT).strip() or SITE_DEFAULT}")
    md_text = "\n".join(lines)
    open(os.path.join(BRIEF_DIR, f"{maxd}.md"), "w", encoding="utf-8").write(md_text)

    # 主动推送：飞书（手机）
    _push_feishu(summary, today_items, maxd, md_text)
    # 兼容旧 webhook
    _push_legacy_webhook(md_text, today_items)

    print(f"brief: {summary}")


if __name__ == "__main__":
    main()
