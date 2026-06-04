#!/usr/bin/env python3
"""市委主要领导讲话/活动抓取 + 入库分析（全任期回溯版）

数据流：
  1. 列表页 nw4411（市政府要闻，服务端渲染、稳定翻页）
     翻页：index.html, index_2.html, index_3.html ...（无 index_1）
     动态停止：翻到 --since 截止日期 / 达到 --max-pages / 连续 404
  2. 候选过滤（两阶段）：
     - 标题直接署领导名（陈吉宁/龚正…）→ 必抓
     - 标题未署名但命中"领导活动动词"（主持/出席/调研/会见/座谈/
       推进会/动员/部署/考察/督查/检查/慰问/讲话/会议/强调）→ 抓详情二次判定
     - 其余（民生资讯/政策解读/区县动态）→ 跳过，避免无谓抓 1.7 万详情页
  3. 详情页全文（.Article_content 等）→ detail_has_leader 二次确认含书记/市长
  4. 辅助理解 → 摘要 + 关键论断 + 新提法 + 政策启示
  5. 与历史同主题对比 → 识别重点变化（持续提及 vs 新出现）
  6. 断点续抓：复用 data/leaders.json 已分析条目（按 url），增量写盘 + 进度日志

运行：
  python3 scripts/fetch_leaders.py                  # 默认回溯最近 180 天
  SINCE=2022-10-01 MAX_PAGES=900 python3 scripts/fetch_leaders.py   # 全任期
  ONLY_SECRETARY=1 python3 scripts/fetch_leaders.py # 只抓市委书记
  DEEPSEEK_API_KEY=sk-xxx ...
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
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
LOG = ROOT / "data" / "fetch_leaders.log"
OUT.parent.mkdir(parents=True, exist_ok=True)

LEADERS = {
    "陈吉宁": {"role": "市委书记", "rank": 1},
    "龚正":   {"role": "市长",     "rank": 2},
}
ONLY_SECRETARY = os.environ.get("ONLY_SECRETARY", "") in ("1", "true", "yes")
if ONLY_SECRETARY:
    LEADERS = {"陈吉宁": {"role": "市委书记", "rank": 1}}

# 回溯参数
SINCE = os.environ.get("SINCE", "")  # YYYY-MM-DD；空则取 今天-DEFAULT_DAYS
DEFAULT_DAYS = int(os.environ.get("DEFAULT_DAYS", "180"))
MAX_PAGES = int(os.environ.get("MAX_PAGES", "60"))
LIST_BASE = "https://www.shanghai.gov.cn/nw4411/"

# 领导活动动词门控：标题未署名时，只有命中这些词才下钻详情页
ACTIVITY_VERBS = [
    "主持", "出席", "调研", "会见", "座谈", "推进会", "动员", "部署",
    "考察", "督查", "检查", "慰问", "讲话", "会议", "强调", "指出",
    "走访", "现场办公", "接待", "看望", "宣讲", "专题", "工作要求",
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

_log_fh = None


# 主题分类纠偏：LLM 偶尔会把科技/产业类活动分到城市治理
# 规则：标题或场合命中关键词时强制覆盖
THEME_OVERRIDE_RULES = [
    # (匹配关键词列表, 强制主题)
    (["政务智能", "数字政府", "智算", "人工智能", "AI ", "大模型", "模速空间",
      "集成电路", "生物医药", "新质生产力", "科技创新", "硬科技", "量子",
      "合成生物", "数据要素", "数据资产"], "科技产业"),
    (["营商环境", "民营企业", "民营经济", "市场主体", "企业服务"], "营商环境"),
    (["进博会", "进博", "外商", "外资", "外贸", "国际枢纽", "一带一路"], "开放发展"),
    (["乡村振兴", "为农", "三农", "长护险", "一老一小", "养老"], "民生治理"),
    (["双碳", "碳中和", "生态环境", "美丽上海", "长江大保护"], "生态环境"),
]


def _correct_theme(theme: str, headline: str, occasion: str) -> str:
    """对 LLM 分类结果做纠偏，仅在命中规则时覆盖"""
    txt = (headline or "") + " " + (occasion or "")
    for keywords, override in THEME_OVERRIDE_RULES:
        for kw in keywords:
            if kw in txt:
                return override
    return theme or "城市治理"


def log(msg: str):
    global _log_fh
    print(msg, file=sys.stderr)
    if _log_fh is None:
        _log_fh = open(LOG, "a", encoding="utf-8")
    _log_fh.write(msg + "\n")
    _log_fh.flush()


def fetch(url: str, timeout: int = 25) -> Optional[str]:
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            return r.text
        except Exception as e:
            if attempt == 2:
                log(f"    ✗ {url}: {e}")
                return None
            time.sleep(2 + attempt * 2)


def list_url(page: int) -> str:
    return f"{LIST_BASE}index.html" if page == 1 else f"{LIST_BASE}index_{page}.html"


def parse_list(html: str, base_url: str) -> List[Dict]:
    """解析列表页所有要闻条目（标题 + URL + 日期，日期取 URL 内嵌段）"""
    soup = BeautifulSoup(html, "html.parser")
    out: List[Dict] = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/"):
            href = urllib.parse.urljoin(base_url, href)
        elif not href.startswith("http"):
            continue
        m_url = re.search(r"/nw\d+/(20\d{2})(\d{2})(\d{2})/", href)
        if not m_url:
            continue
        if href in seen:
            continue
        seen.add(href)
        title = (a.get_text(strip=True) or "").strip()
        title = re.sub(r"\s*20\d{2}[-./]\d{1,2}[-./]\d{1,2}\s*$", "", title).strip()
        if len(title) < 6:
            continue
        date_str = f"{m_url.group(1)}-{m_url.group(2)}-{m_url.group(3)}"
        out.append({"date": date_str, "headline": title[:160], "url": href})
    return out


def title_named_leader(title: str) -> Optional[str]:
    for name in LEADERS:
        if name in title:
            return name
    return None


def title_is_activity(title: str) -> bool:
    return any(v in title for v in ACTIVITY_VERBS)


def detail_has_leader(full_text: str) -> Optional[Dict]:
    if not full_text or len(full_text) < 50:
        return None
    for name, meta in LEADERS.items():  # 字典有序，书记在前 → 书记优先
        if name in full_text:
            return {"leader": name, **meta}
    return None


def fetch_detail(url: str) -> str:
    html = fetch(url, timeout=30)
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for cls in ["Article_content", "article-content", "TRS_Editor", "zoomCon", "ity_con"]:
        el = soup.find(class_=cls) or soup.find(id=cls)
        if el:
            text = el.get_text(" ", strip=True)
            return re.sub(r"\s+", " ", text)
    return ""


def analyze(headline: str, date: str, leader: str, full_text: str) -> Dict:
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
  "key_points": ["关键论断 1（凝练判断式）", "关键论断 2"],
  "new_phrasing": ["新提法 1", "新提法 2"],
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
                API_URL, data=json.dumps(body).encode("utf-8"),
                headers={"Authorization": f"Bearer {API_KEY}",
                         "Content-Type": "application/json"})
            r = urllib.request.urlopen(req, timeout=120).read()
            return json.loads(json.loads(r)["choices"][0]["message"]["content"])
        except Exception as e:
            if attempt == 1:
                log(f"    ✗ 分析失败: {e}")
                return {}
            time.sleep(3)


def detect_change(new: Dict, history: List[Dict]) -> Dict:
    same = [h for h in history
            if h.get("leader") == new["leader"]
            and h.get("theme") == new.get("theme")
            and h.get("date", "") < new.get("date", "")]
    if not same:
        return {"compared_to": None, "change_note": "首次入库该主题，建立基线。"}
    prev = max(same, key=lambda x: x.get("date", ""))
    new_phr = set(new.get("new_phrasing", []))
    old_phr = set(prev.get("new_phrasing", [])) | set(prev.get("key_points", []))
    fresh = [p for p in new_phr if p not in old_phr]
    fresh_text = "、".join(fresh[:3]) if fresh else "暂未识别新增表述"
    return {
        "compared_to": {"date": prev["date"], "headline": prev["headline"][:80],
                        "theme": prev.get("theme", "")},
        "change_note": f"与 {prev['date']} 同主题对比，本次新增表述：{fresh_text}。",
    }


def save(results: List[Dict]):
    results.sort(key=lambda x: (x.get("role_rank", 9),
                                -int(x["date"].replace("-", ""))))
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    since = SINCE or (datetime.now() - timedelta(days=DEFAULT_DAYS)).strftime("%Y-%m-%d")
    log(f"\n=== 抓取市委领导讲话/活动 + 入库分析 ===")
    log(f"  节点 nw4411 · 回溯至 {since} · 最多 {MAX_PAGES} 页 · "
        f"领导 {list(LEADERS)} · ONLY_SECRETARY={ONLY_SECRETARY}")

    # 读历史（断点续抓）
    history: List[Dict] = []
    if OUT.exists():
        try:
            history = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            history = []
    history_urls = {h.get("url"): h for h in history if h.get("url")}
    results: List[Dict] = list(history)  # 以历史为基底，增量补充
    results_urls = set(history_urls)

    # 1. 翻页收集候选
    candidates: List[Dict] = []
    seen = set()
    stop = False
    for page in range(1, MAX_PAGES + 1):
        if stop:
            break
        url = list_url(page)
        html = fetch(url)
        if not html:
            log(f"  · 第 {page} 页 404/失败，停止翻页")
            break
        items = parse_list(html, url)
        if not items:
            log(f"  · 第 {page} 页无条目，停止翻页")
            break
        page_min = min(it["date"] for it in items)
        new_n = 0
        for it in items:
            if it["url"] in seen:
                continue
            seen.add(it["url"])
            if it["date"] < since:
                continue  # 早于截止日期，丢弃（但继续看本页其余）
            candidates.append(it)
            new_n += 1
        log(f"  · 第 {page} 页 {len(items)} 条（最早 {page_min}）→ 收 {new_n} 条候选")
        if page_min < since:
            log(f"  · 本页已越过截止日期 {since}，停止翻页")
            stop = True

    log(f"\n共收集 {len(candidates)} 条候选（≥{since}）→ 两阶段过滤 + 入库分析")

    # 2 + 3 + 4. 过滤 + 分析
    skip_existed = skip_norel = skip_noleader = analyzed = 0
    for i, c in enumerate(candidates, 1):
        if c["url"] in results_urls and history_urls.get(c["url"], {}).get("summary"):
            skip_existed += 1
            continue

        named = title_named_leader(c["headline"])
        if not named and not title_is_activity(c["headline"]):
            skip_norel += 1
            continue  # 标题既未署名也非领导活动 → 不下钻

        full = fetch_detail(c["url"])
        if not full:
            continue
        leader = detail_has_leader(full)
        if not leader:
            skip_noleader += 1
            continue

        analysis = analyze(c["headline"], c["date"], leader["leader"], full)
        if not analysis:
            continue
        entry = {
            "id": f"ld-gov-{abs(hash(c['url'])) % 100000:05d}",
            "date": c["date"], "leader": leader["leader"], "role": leader["role"],
            "role_rank": leader["rank"], "occasion": analysis.get("occasion", ""),
            "headline": c["headline"], "full_text": full[:3000],
            "summary": analysis.get("summary", ""),
            "key_points": analysis.get("key_points", []),
            "new_phrasing": analysis.get("new_phrasing", []),
            "theme": _correct_theme(analysis.get("theme", ""), c["headline"], analysis.get("occasion", "")),
            "subthemes": analysis.get("subthemes", []),
            "keywords": analysis.get("keywords", []),
            "policy_implications": analysis.get("policy_implications", ""),
            "source": "上海市人民政府门户网站", "url": c["url"],
        }
        entry.update(detect_change(entry, results))
        results.append(entry)
        results_urls.add(c["url"])
        analyzed += 1
        log(f"  [{i}/{len(candidates)}] ✓ {c['date']} {leader['leader']} | "
            f"{entry['theme']} | 新提法{len(entry['new_phrasing'])} | {c['headline'][:40]}")
        if analyzed % 5 == 0:
            save(results)  # 增量写盘，崩溃可续

    save(results)
    log(f"\n=== 完成 ===")
    log(f"  候选总数        : {len(candidates)}")
    log(f"  复用历史已分析  : {skip_existed}")
    log(f"  标题非领导活动  : {skip_norel}（跳过未下钻）")
    log(f"  详情无书记/市长 : {skip_noleader}")
    log(f"  本次新入库分析  : {analyzed}")
    log(f"  累计入库总数    : {len(results)}")

    from collections import Counter
    log("\n  按领导：" + "  ".join(f"{n}:{k}" for n, k in
        Counter(r["leader"] for r in results).most_common()))
    log("  按主题：" + "  ".join(f"{t}:{k}" for t, k in
        Counter(r.get("theme", "") for r in results if r.get("theme")).most_common()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
