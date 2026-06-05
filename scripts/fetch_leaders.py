#!/usr/bin/env python3
"""市委主要领导讲话/活动抓取 + 入库分析（数据源：上海发布）

2026-06 数据源切换：上海市政府门户网 nw4411 更新太慢，改抓腾讯新闻「上观新闻」
（上报集团党媒号，日更多条，书记/市长调研会见时政覆盖快）。
注：「上海发布」腾讯号实测为民生资讯号，300 条 0 领导，故弃用；改用上观新闻。

数据流：
  1. 列表接口 getSubNewsMixedList（JSON，offsetInfo 游标翻页，无需渲染）
     guestSuid=8QMd3H1b7oIVvz7b 即「上观新闻」账号
     动态停止：翻到 --since 截止日期 / hasNext=0 / 达到 --max-pages
  2. 候选过滤（两阶段）：
     - 标题/AI摘要直接署领导名（陈吉宁/龚正…）→ 必抓
     - 标题命中"领导活动动词"（主持/出席/调研/会见/座谈/推进会/动员/
       部署/考察/督查/检查/慰问/讲话/会议/强调）→ 抓详情二次判定
     - 其余（民生资讯/活动预告/天气）→ 跳过
  3. 详情页 news.qq.com/rain/a/<id> → 解析 originContent.text 全文
     → detail_has_leader 二次确认含书记/市长
  4. 辅助理解 → 摘要 + 关键论断 + 新提法 + 政策启示
  5. 与历史同主题对比 → 识别重点变化（持续提及 vs 新出现）
  6. 断点续抓：复用 data/leaders.json 已分析条目（按 url），增量写盘 + 进度日志
     旧的政府网历史条目原样保留，新增条目来自上海发布。

运行：
  python3 scripts/fetch_leaders.py                  # 默认回溯最近 180 天
  SINCE=2026-01-01 MAX_PAGES=120 python3 scripts/fetch_leaders.py   # 指定回溯
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
MAX_PAGES = int(os.environ.get("MAX_PAGES", "60"))  # 一页=一次接口翻页(约20条)

# 上观新闻（上报集团党媒号，时政覆盖强、日更多条，含书记/市长调研会见）
# 实测优于政府网与「上海发布」民生号：后者 300 条 0 领导，上观 160 条命中 4 条署名
# media_id=5004941，guestSuid 为列表接口所需的账号令牌
GUEST_SUID = os.environ.get("SH_GUEST_SUID", "8QMd3H1b7oIVvz7b")
LIST_API = "https://i.news.qq.com/getSubNewsMixedList"
DETAIL_RAIN = "https://news.qq.com/rain/a/"   # + <articleId>
SOURCE_NAME = "上观新闻"

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


def fetch_list_page(offset_info: str) -> Optional[Dict]:
    """拉一页上海发布列表（JSON）。offset_info 为上一页返回的游标，首页传空。"""
    params = {
        "offset_info": offset_info or "",
        "guestSuid": GUEST_SUID,
        "tabId": "om_index",
        "caller": "1",
        "from_scene": "103",
    }
    for attempt in range(3):
        try:
            r = requests.get(LIST_API, params=params,
                             headers={**HEADERS, "Referer": "https://news.qq.com/"},
                             timeout=25)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == 2:
                log(f"    ✗ 列表接口失败: {e}")
                return None
            time.sleep(2 + attempt * 2)


def parse_list(data: Dict) -> List[Dict]:
    """从列表接口 JSON 提取条目：标题 + 摘要 + URL + 日期"""
    out: List[Dict] = []
    for n in data.get("newslist", []):
        if n.get("articletype") not in ("0", 0, "", None):  # 0=图文；过滤纯视频/直播/广告
            pass  # 不强制，部分时政为视频，仍保留
        aid = n.get("id") or ""
        t = (n.get("time") or "")[:10]  # "2026-06-05 07:38:02" → 2026-06-05
        if not aid or not t:
            continue
        title = (n.get("longtitle") or n.get("title") or "").strip()
        if len(title) < 5:
            continue
        abstract = (n.get("nlpAbstract") or "") + " " + (n.get("nlpContentAbstract") or "")
        out.append({
            "date": t, "headline": title[:160], "id": aid,
            "url": n.get("url") or (DETAIL_RAIN + aid),
            "abstract": abstract.strip(),
        })
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


def fetch_detail(article_id: str) -> str:
    """抓 news.qq.com/rain/a/<id> SSR 页，解析 originContent.text 全文"""
    import html as _html
    page = fetch(DETAIL_RAIN + article_id, timeout=30)
    if not page:
        return ""
    i = page.find('"originContent":')
    if i >= 0:
        start = page.find("{", i)
        try:
            obj, _ = json.JSONDecoder().raw_decode(page, start)
            raw = obj.get("text", "") or ""
            txt = re.sub(r"<[^>]+>", " ", raw)
            txt = re.sub(r"\s+", " ", _html.unescape(txt)).strip()
            if len(txt) > 40:
                return txt
        except Exception:
            pass
    # 兜底：BeautifulSoup 取 rich_media_content
    soup = BeautifulSoup(page, "html.parser")
    el = soup.find(class_="rich_media_content") or soup.find(class_="content-article")
    if el:
        return re.sub(r"\s+", " ", el.get_text(" ", strip=True))
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
    log(f"  源：上海发布 · 回溯至 {since} · 最多 {MAX_PAGES} 翻页 · "
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

    # 1. 翻页收集候选（offsetInfo 游标翻页）
    candidates: List[Dict] = []
    seen = set()
    offset = ""
    for page in range(1, MAX_PAGES + 1):
        data = fetch_list_page(offset)
        if not data or data.get("ret") not in (0, "0"):
            log(f"  · 第 {page} 翻页接口异常，停止")
            break
        items = parse_list(data)
        if not items:
            log(f"  · 第 {page} 翻页无条目，停止")
            break
        page_min = min(it["date"] for it in items)
        new_n = 0
        for it in items:
            if it["id"] in seen:
                continue
            seen.add(it["id"])
            if it["date"] < since:
                continue  # 早于截止日期，丢弃（但继续看本页其余）
            candidates.append(it)
            new_n += 1
        log(f"  · 第 {page} 翻页 {len(items)} 条（最早 {page_min}）→ 收 {new_n} 条候选")
        offset = urllib.parse.unquote_plus(data.get("offsetInfo", "") or "")
        if not data.get("hasNext") or page_min < since or not offset:
            log(f"  · 到底 / 越过截止日期 {since}，停止翻页")
            break

    log(f"\n共收集 {len(candidates)} 条候选（≥{since}）→ 两阶段过滤 + 入库分析")

    # 2 + 3 + 4. 过滤 + 分析
    skip_existed = skip_norel = skip_noleader = analyzed = 0
    for i, c in enumerate(candidates, 1):
        if c["url"] in results_urls and history_urls.get(c["url"], {}).get("summary"):
            skip_existed += 1
            continue

        abstract = c.get("abstract", "")
        named = title_named_leader(c["headline"]) or title_named_leader(abstract)
        if not named and not title_is_activity(c["headline"]) and not title_is_activity(abstract):
            skip_norel += 1
            continue  # 标题/摘要既未署名也非领导活动 → 不下钻

        full = fetch_detail(c["id"])
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
            "id": f"ld-sh-{c['id']}",
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
            "source": SOURCE_NAME, "url": c["url"],
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
