#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CZ Agent · 动向速递（变化主动推送 A）
每次抓取后跑：对比最新一期，生成「当日速递 + 近7天回顾」简报。
输出：
  - data/brief_latest.json   给前端「动向速递」卡片读取
  - briefs/YYYY-MM-DD.md      留档
推送（可选）：设置环境变量 BRIEF_WEBHOOK 后，POST 简报文本到该地址（Mattermost/企业微信等 incoming webhook 通用）。
不调大模型，纯规则生成，稳定可靠。
"""
import json, os, datetime, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEADERS = os.path.join(ROOT, "data", "leaders.json")
OUT_JSON = os.path.join(ROOT, "data", "brief_latest.json")
BRIEF_DIR = os.path.join(ROOT, "briefs")


def np_of(s):
    v = s.get("new_phrasing")
    return v if isinstance(v, list) else ([v] if v else [])


def dminus(ymd, n):
    y, m, d = map(int, ymd.split("-"))
    return (datetime.date(y, m, d) - datetime.timedelta(days=n)).isoformat()


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
    # 近7天最活跃主题
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
    md_text = "\n".join(lines)
    open(os.path.join(BRIEF_DIR, f"{maxd}.md"), "w", encoding="utf-8").write(md_text)

    # 可选：推送到工作群（Mattermost / 企业微信 incoming webhook）
    hook = os.environ.get("BRIEF_WEBHOOK", "").strip()
    if hook and today_items:
        try:
            payload = {"text": "**📍 CZ Agent 动向速递**\n\n" + md_text}
            req = urllib.request.Request(hook, data=json.dumps(payload).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=15)
            print("brief: 已推送工作群")
        except Exception as e:
            print(f"brief: 推送失败 {e}")

    print(f"brief: {summary}")


if __name__ == "__main__":
    main()
