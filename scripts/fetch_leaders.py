#!/usr/bin/env python3
"""市委主要领导动向抓取脚本

抓取范围（公开权威渠道，仅采集已公开发布的内容）：
- 上海市人民政府门户网站 www.shanghai.gov.cn
- 上观新闻 jfdaily.com
- 解放日报 jfdaily.com

输出：data/leaders.json
- 抓取后由前端 fetch 加载（替换 data.js 中的 leader_signals mock 数据）

运行方式：
    python3 scripts/fetch_leaders.py

依赖：requests + beautifulsoup4 + python-dateutil
    pip install requests beautifulsoup4 python-dateutil

建议在用户本地配 cron 定时跑（每 6 小时 / 每日凌晨各一次）。
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
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

# 市委主要领导关键词（用于识别报道是否涉及）
LEADERS = {
    "陈吉宁": {"role": "市委书记", "rank": 1},
    "龚正": {"role": "市长", "rank": 2},
    # 可扩展副书记副市长
}

# 主题分类关键词（用于把抓到的报道映射到 topics）
THEME_KEYWORDS = {
    "科技产业": ["科技创新", "基础研究", "人工智能", "工业软件", "成果转化", "高端产业"],
    "开放发展": ["跨境", "出海", "进博会", "国际消费", "开放枢纽", "自贸"],
    "民生治理": ["养老", "长护险", "民生", "社区", "教育", "医疗"],
    "城市治理": ["城市更新", "营商环境", "数字化", "智能化"],
    "生态环境": ["生态", "绿色", "双碳", "环保"],
}

# 候选数据源（公开渠道，按可靠性排序）
SOURCES = [
    {
        "name": "上海市人民政府门户网站",
        "kind": "gov-portal",
        # 上海市政府门户的"要闻动态"栏目
        "list_url": "https://www.shanghai.gov.cn/nw2315/index.html",
        "encoding": "utf-8",
    },
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def classify_theme(text: str) -> str:
    """根据正文关键词分类主题。"""
    for theme, kws in THEME_KEYWORDS.items():
        for kw in kws:
            if kw in text:
                return theme
    return "城市治理"


def extract_keywords(text: str, max_n: int = 4) -> List[str]:
    """简单从文本里抽出主题关键词命中。"""
    found: List[str] = []
    for theme, kws in THEME_KEYWORDS.items():
        for kw in kws:
            if kw in text and kw not in found:
                found.append(kw)
            if len(found) >= max_n:
                return found
    return found


def find_leader(text: str) -> Optional[Dict]:
    """检查报道是否提到主要领导。"""
    for name, meta in LEADERS.items():
        if name in text:
            return {"leader": name, **meta}
    return None


def fetch_url(url: str, timeout: int = 20) -> Optional[str]:
    """通用 GET，带 retry。"""
    for attempt in range(2):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            return r.text
        except Exception as e:
            if attempt == 1:
                print(f"  ✗ 抓取失败 {url}: {e}", file=sys.stderr)
                return None
            time.sleep(3)


def parse_shanghai_gov(html: str, base_url: str) -> List[Dict]:
    """从上海市府门户的栏目页解析条目。"""
    soup = BeautifulSoup(html, "html.parser")
    items: List[Dict] = []

    # 通用策略：找所有 <a> + 紧邻日期文本
    for a in soup.find_all("a", href=True):
        raw_text = (a.get_text(strip=True) or "").strip()
        if len(raw_text) < 8:
            continue
        leader = find_leader(raw_text)
        if not leader:
            continue

        href = a["href"]
        if href.startswith("/"):
            href = urllib.parse.urljoin(base_url, href)
        elif not href.startswith("http"):
            continue

        # 清洗标题：去前缀"要闻"/"重要新闻"等栏目名，去结尾日期
        title = raw_text
        title = re.sub(r"^(要闻|重要新闻|要闻动态|图)\s*", "", title)
        title = re.sub(r"\s*20\d{2}[-./]?\d{1,2}[-./]?\d{1,2}\s*$", "", title)
        title = title.strip()

        # 提取日期
        date_str = ""
        m = re.search(r"(20\d{2})[-./年](\d{1,2})[-./月](\d{1,2})", raw_text)
        if not m:
            # 从 URL 路径里看（如 /nw4411/20260603/...html）
            m_url = re.search(r"/(20\d{2})(\d{2})(\d{2})/", href)
            if m_url:
                date_str = f"{m_url.group(1)}-{m_url.group(2)}-{m_url.group(3)}"
        if not date_str and m:
            date_str = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")

        theme = classify_theme(title)
        kws = extract_keywords(title)

        # occasion：从标题中提取活动场合（在领导名之前的部分）
        occasion = ""
        m_occ = re.search(rf"{leader['leader']}([^，。]{{0,40}})", title)
        if m_occ:
            occasion = m_occ.group(1).strip()[:40]

        items.append({
            "id": f"ld-gov-{abs(hash(href)) % 10000:04d}",
            "date": date_str,
            "leader": leader["leader"],
            "role": leader["role"],
            "role_rank": leader["rank"],
            "occasion": occasion,
            "headline": title[:140],
            "summary": "",  # 留待二次抓取详情页填充
            "theme": theme,
            "keywords": kws,
            "source": "上海市人民政府门户网站",
            "url": href,
            "change_note": "",
            "compared_to": None,
        })

    # 去重
    seen = set()
    uniq: List[Dict] = []
    for it in items:
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        uniq.append(it)
    return uniq


def diff_with_history(new_items: List[Dict], history_path: Path) -> List[Dict]:
    """与历史记录比对，识别同一专题下表述变化。
    简化版：找同 leader+theme 的上一条，写入 compared_to 字段。
    """
    history: List[Dict] = []
    if history_path.exists():
        try:
            history = json.loads(history_path.read_text(encoding="utf-8"))
        except Exception:
            history = []

    # 按 (leader, theme) 索引历史最近一条
    last_by_key: Dict[tuple, Dict] = {}
    for h in history:
        key = (h.get("leader"), h.get("theme"))
        if key not in last_by_key or h["date"] > last_by_key[key]["date"]:
            last_by_key[key] = h

    for it in new_items:
        key = (it["leader"], it["theme"])
        prev = last_by_key.get(key)
        if prev and prev["date"] < it["date"]:
            it["compared_to"] = {
                "date": prev["date"],
                "headline": prev["headline"],
            }
            # change_note 留给人工补，或后续接入辅助理解模型
            if not it.get("change_note"):
                it["change_note"] = "（待补充：与上一条相比的表述变化要点）"

    return new_items


def main() -> int:
    print("=== 开始抓取市委主要领导动向 ===", file=sys.stderr)
    all_items: List[Dict] = []

    for src in SOURCES:
        print(f"\n→ {src['name']}", file=sys.stderr)
        html = fetch_url(src["list_url"])
        if not html:
            continue
        if src["kind"] == "gov-portal":
            items = parse_shanghai_gov(html, src["list_url"])
            print(f"  解析到 {len(items)} 条", file=sys.stderr)
            all_items.extend(items)

    # 排序：role_rank 升序，date 倒序
    all_items.sort(key=lambda x: (x.get("role_rank", 9), -int(x["date"].replace("-", ""))))

    # 与历史比对，补 change_note + compared_to
    all_items = diff_with_history(all_items, OUT)

    # 写出
    OUT.write_text(json.dumps(all_items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== 完成 === 共 {len(all_items)} 条 → {OUT}", file=sys.stderr)

    if all_items:
        print("\n样本：", file=sys.stderr)
        for it in all_items[:3]:
            print(f"  {it['date']} | {it['leader']} | {it['headline'][:60]}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
