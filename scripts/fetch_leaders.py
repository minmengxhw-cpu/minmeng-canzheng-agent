#!/usr/bin/env python3
"""市委主要领导讲话/活动抓取 + 入库分析

数据流：
  1. 列表页 nw2315 → 过滤含市委书记/市长的条目
  2. 详情页 → 抓全文（.Article_content）
  3. 辅助理解 → 摘要 + 关键论断 + 新提法 + 政策启示
  4. 与历史对比 → 识别重点变化（持续提及 vs 新出现）
  5. 输出 data/leaders.json

输出 JSON 数组，每条字段：
  date / leader / role / role_rank / occasion / headline
  full_text / summary / key_points / new_phrasing / theme
  subthemes / keywords / policy_implications
  change_note / compared_to / source / url

运行：
  DEEPSEEK_API_KEY=sk-xxx python3 scripts/fetch_leaders.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("缺依赖：pip install requests beautifulsoup4", file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "leaders.json"
OUT.parent.mkdir(parents=True, exist_ok=True)

LEADERS = {
    "陈吉宁": {"role": "市委书记", "rank": 1},
    "龚正":   {"role": "市长",     "rank": 2},
}

LIST_URLS = [
    "https://www.shanghai.gov.cn/nw2315/index.html",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

API_URL = "https://api.deepseek.com/v1/chat/completions"
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-8d2989ca8fd449abad970937a36823d8")
MODEL = "deepseek-v4-flash"

SYSTEM_PROMPT = """你是民盟市委参政议政研究助理。任务：阅读市委书记或市长的一次公开讲话/活动报道全文，做结构化入库分析，输出 JSON。

判断原则：
- 提炼"判断式"语言，不要罗列"领导强调"等套话
- 关键论断要凝练到金句级（每条 15-25 字最佳）
- "新提法"指本次报道中可能升级或新出现的措辞（与典型既往表述对比识别）
- "政策启示"要从参政议政角度提供切口建议（民盟可介入的中观议题）
- 主题分类：科技产业 / 开放发展 / 民生治理 / 城市治理 / 营商环境 / 生态环境 / 文化教育"""


def fetch(url: str, timeout: int = 20) -> Optional[str]:
    for attempt in range(2):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            return r.text
        except Exception as e:
            if attempt == 1:
                print(f"    ✗ {url}: {e}", file=sys.stderr)
                return None
            time.sleep(3)


def find_leader(text: str) -> Optional[Dict]:
    for name, meta in LEADERS.items():
        if name in text:
            return {"leader": name, **meta}
    return None


def parse_list(html: str, base_url: str) -> List[Dict]:
    """从列表页解析含领导的条目（标题 + 日期 + 详情 URL）"""
    soup = BeautifulSoup(html, "html.parser")
    out: List[Dict] = []
    seen = set()

    for a in soup.find_all("a", href=True):
        raw = (a.get_text(strip=True) or "").strip()
        if len(raw) < 8:
            continue
        leader = find_leader(raw)
        if not leader:
            continue

        href = a["href"]
        if href.startswith("/"):
            href = urllib.parse.urljoin(base_url, href)
        elif not href.startswith("http"):
            continue
        if href in seen:
            continue
        seen.add(href)

        # 清洗标题
        title = raw
        title = re.sub(r"^(要闻|图|重要新闻)\s*", "", title)
        title = re.sub(r"\s*20\d{2}[-./]?\d{1,2}[-./]?\d{1,2}\s*$", "", title)
        title = title.strip()

        # 日期：先从 URL 路径，回退到文本
        date_str = ""
        m_url = re.search(r"/(20\d{2})(\d{2})(\d{2})/", href)
        if m_url:
            date_str = f"{m_url.group(1)}-{m_url.group(2)}-{m_url.group(3)}"
        if not date_str:
            m = re.search(r"(20\d{2})[-./年](\d{1,2})[-./月](\d{1,2})", raw)
            if m:
                date_str = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")

        out.append({
            "date": date_str,
            "leader_meta": leader,
            "headline": title[:160],
            "url": href,
        })
    return out


def fetch_detail(url: str) -> str:
    """抓详情页的正文文本"""
    html = fetch(url, timeout=25)
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for cls in ["Article_content", "article-content", "TRS_Editor", "zoomCon"]:
        el = soup.find(class_=cls) or soup.find(id=cls)
        if el:
            text = el.get_text(" ", strip=True)
            text = re.sub(r"\s+", " ", text)
            return text
    return ""


def analyze(headline: str, date: str, leader: str, full_text: str) -> Dict:
    """调辅助理解能力做入库分析"""
    if not full_text or len(full_text) < 80:
        return {}
    user = f"""标题：{headline}
日期：{date}
领导：{leader}

全文：
{full_text[:4500]}

请输出 JSON：
{{
  "occasion": "活动场合精炼 15 字内",
  "summary": "核心要点摘要，3-5 句话 120-180 字",
  "key_points": ["关键论断 1（凝练判断式）", "关键论断 2", ...],
  "new_phrasing": ["新提法 1", "新提法 2", ...],
  "theme": "主题（七选一）",
  "subthemes": ["子主题"],
  "keywords": ["关键词 1-5 个"],
  "policy_implications": "民盟参政议政切口建议，1-2 句，60-100 字"
}}"""
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "max_tokens": 1200,
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
    }
    for attempt in range(2):
        try:
            req = urllib.request.Request(
                API_URL,
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json",
                },
            )
            r = urllib.request.urlopen(req, timeout=120).read()
            content = json.loads(r)["choices"][0]["message"]["content"]
            return json.loads(content)
        except Exception as e:
            if attempt == 1:
                print(f"    ✗ 分析失败: {e}", file=sys.stderr)
                return {}
            time.sleep(3)


def detect_change(new: Dict, history: List[Dict]) -> Dict:
    """与历史同 leader+theme 对比，识别重点变化"""
    same = [h for h in history
            if h.get("leader") == new["leader"]
            and h.get("theme") == new.get("theme")
            and h.get("date", "") < new.get("date", "")]
    if not same:
        return {"compared_to": None, "change_note": "首次入库该主题，建立基线。"}
    prev = max(same, key=lambda x: x.get("date", ""))

    # 识别新提法（本次有 / 上次没有）
    new_phr = set(new.get("new_phrasing", []))
    old_phr = set(prev.get("new_phrasing", [])) | set(prev.get("key_points", []))
    fresh = [p for p in new_phr if p not in old_phr]
    fresh_text = "、".join(fresh[:3]) if fresh else "暂未识别新增表述"

    return {
        "compared_to": {
            "date": prev["date"],
            "headline": prev["headline"][:80],
            "theme": prev.get("theme", ""),
        },
        "change_note": f"与 {prev['date']} 同主题对比，本次新增表述：{fresh_text}。",
    }


def main() -> int:
    print("=== 抓取市委主要领导讲话/活动 + 入库分析 ===", file=sys.stderr)

    # 1. 列表页扫描
    candidates: List[Dict] = []
    seen = set()
    for url in LIST_URLS:
        print(f"\n→ 列表页 {url}", file=sys.stderr)
        html = fetch(url)
        if not html:
            continue
        items = parse_list(html, url)
        for it in items:
            if it["url"] in seen:
                continue
            seen.add(it["url"])
            candidates.append(it)
    print(f"  共发现 {len(candidates)} 条含领导关键词", file=sys.stderr)

    # 读历史
    history: List[Dict] = []
    if OUT.exists():
        try:
            history = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            history = []

    # 2 + 3. 详情页 + 入库分析
    results: List[Dict] = []
    for i, c in enumerate(candidates, 1):
        print(f"\n[{i}/{len(candidates)}] {c['date']} | {c['leader_meta']['leader']} | {c['headline'][:60]}", file=sys.stderr)

        # 跳过已分析过的（按 URL 去重）
        existed = next((h for h in history if h.get("url") == c["url"]), None)
        if existed and existed.get("summary"):
            print(f"    ⏭  已有分析，复用", file=sys.stderr)
            results.append(existed)
            continue

        print(f"    ↓ 抓详情页…", file=sys.stderr)
        full = fetch_detail(c["url"])
        if not full:
            print(f"    ✗ 详情页无内容", file=sys.stderr)
            continue
        print(f"    ✓ 全文 {len(full)} 字", file=sys.stderr)

        print(f"    ↓ 入库分析…", file=sys.stderr)
        analysis = analyze(c["headline"], c["date"], c["leader_meta"]["leader"], full)
        if not analysis:
            print(f"    ✗ 分析失败，跳过", file=sys.stderr)
            continue

        entry = {
            "id": f"ld-gov-{abs(hash(c['url'])) % 10000:04d}",
            "date": c["date"],
            "leader": c["leader_meta"]["leader"],
            "role": c["leader_meta"]["role"],
            "role_rank": c["leader_meta"]["rank"],
            "occasion": analysis.get("occasion", ""),
            "headline": c["headline"],
            "full_text": full[:3000],  # 控制 JSON 大小
            "summary": analysis.get("summary", ""),
            "key_points": analysis.get("key_points", []),
            "new_phrasing": analysis.get("new_phrasing", []),
            "theme": analysis.get("theme", ""),
            "subthemes": analysis.get("subthemes", []),
            "keywords": analysis.get("keywords", []),
            "policy_implications": analysis.get("policy_implications", ""),
            "source": "上海市人民政府门户网站",
            "url": c["url"],
        }

        # 4. 变化对比
        chg = detect_change(entry, history)
        entry.update(chg)

        results.append(entry)
        print(f"    ✓ 入库 · 主题：{entry['theme']} · 新提法：{len(entry['new_phrasing'])} 条", file=sys.stderr)

    # 排序：role_rank 升序 + date 倒序
    results.sort(key=lambda x: (x.get("role_rank", 9), -int(x["date"].replace("-", ""))))

    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== 完成 === {len(results)} 条 → {OUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
