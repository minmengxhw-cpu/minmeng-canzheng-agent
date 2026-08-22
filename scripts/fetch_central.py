#!/usr/bin/env python3
"""独立抓取中央 / 最高层公开信号（考察调研、重要会议与指示）。

本通道与市委主要领导数据分开存储、分开统计，只对新来源 URL 调用 LLM
（Grok CLI，grok-4.6）。
公开页面中的具体身份统一在站内显示为“中央领导”，保留原文链接供追溯。

轻量约束：
  - 信源仅官方一级口（政府网 + 新华社时政 + 既有上海官方交叉验证）
  - 每次运行新分析条数有上限（CENTRAL_MAX_ANALYZE，默认 3）
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fetch_leaders import (
    HEADERS,
    OFFICIAL_SOURCE_NAME,
    SHIO_SOURCE_NAME,
    SOURCE_NAME,
    fetch_detail,
    fetch_list_page,
    fetch_official_detail,
    fetch_official_news_list,
    fetch_official_search_page,
    fetch_shio_push_list,
    parse_list,
)
from llm_cli import llm_json
import requests

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    BeautifulSoup = None  # type: ignore

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "central_leaders.json"
LATEST = ROOT / "data" / "central_latest.json"
LOG = ROOT / "data" / "central_leaders.log"
SINCE = os.environ.get("SINCE", "")
DEFAULT_DAYS = int(os.environ.get("CENTRAL_DEFAULT_DAYS", "7"))
MAX_PAGES = int(os.environ.get("MAX_PAGES", "6"))
# 每次运行最多新调模型分析条数，防止费用与体量膨胀（可环境变量调高）
MAX_ANALYZE = int(os.environ.get("CENTRAL_MAX_ANALYZE", "5"))

GOV_SOURCE_NAME = "中国政府网"
XINHUA_SOURCE_NAME = "新华社"
GOV_YAOWEN_JSON = "https://www.gov.cn/yaowen/liebiao/YAOWENLIEBIAO.json"
XINHUA_POLITICS_URLS = (
    "https://www.news.cn/politics/index.html",
    "https://www.news.cn/politics/",
)

# 可进入「正文+模型」的信源（其余只作交叉验证挂源）
ANALYZE_SOURCES = {GOV_SOURCE_NAME, XINHUA_SOURCE_NAME}

OFFICIAL_QUERIES = ("总书记", "考察调研", "中央政治局", "主持召开")
ACTION_WORDS = (
    "考察", "调研", "视察", "看望", "慰问", "座谈",
    "讲话", "指示", "要求", "强调", "部署", "主持召开",
)
FOLLOWUP_WORDS = (
    "认真学习", "学习贯彻", "激励广大", "引发热烈反响",
    "重要讲话精神", "凝聚奋进力量", "迅速传达", "掀起学习",
)
# 评论 / 侧记 / 汇编，非现场通稿
SKIP_TITLE_MARKERS = (
    "微观察", "新华典评", "述评", "侧记", "图解", "金句来了",
    "权威快报", "新闻背景", "发言摘编", "代表通道",
)
# 地方跟进报道常见前缀，排除
FOLLOWUP_PREFIX_RE = re.compile(
    r"^(?:各地|各地各部门|广大|基层|党员干部|全市|全省|全国各地)"
)
PRIMARY_ACTIVITY_RE = re.compile(
    r"^[\u4e00-\u9fff]{2,4}.{0,12}(?:在|赴).{1,30}(?:考察|调研|视察|看望|慰问)"
)
# 最高层会议 / 主持
PRIMARY_MEETING_RE = re.compile(
    r"(?:中共中央政治局|中央政治局|中央全面深化改革|中央财经委员会|中央经济工作会议)"
    r".{0,20}(?:召开|举行)|"
    r"(?:总书记|国家主席).{0,20}主持(?:召开|会议)|"
    r"习近平主持"
)
PRIMARY_INSTRUCTION_RE = re.compile(
    r"(?:总书记|国家主席).{0,24}(?:作出重要指示|发表重要讲话|重要指示强调|回信)"
)
PRIMARY_DETAIL_RE = re.compile(
    r"(?:总书记|国家主席).{0,80}(?:"
    r"(?:在|赴).{0,80}(?:考察|调研|视察|看望|慰问)|"
    r"主持(?:召开|会议)|作出重要指示|发表重要讲话|"
    r"中共中央政治局|中央政治局"
    r")",
    re.S,
)
VALID_ACTIVITY_WORDS = (
    "考察", "调研", "视察", "看望", "慰问", "会议", "讲话", "指示", "主持", "座谈",
)

SYSTEM_PROMPT = """你是参政议政研究助理，负责提炼中央最高层公开活动中的工作精神、关注重点与新提法。
覆盖：考察调研、重要会议（如中央政治局会议、党外人士座谈会）、重要讲话与指示。
平台必须交付：精神要旨、重点关注、相对既往的变化信号、民盟可跟进的参政议政切口。
只输出严格 JSON 对象，不要 Markdown，不要解释。
不要输出具体人物姓名，统一称为“中央领导”。
字段必须包括：location、activity_type、summary、key_points、new_phrasing、directives、theme、policy_implications、keywords。
summary 为 120-220 字（先精神主轴，再具体部署）；key_points、new_phrasing、directives、keywords 各 2-5 条；
keywords 为 2-6 个短词（4-12 字概念，不要整句）；
policy_implications 为 1-3 条可核验的参政议政中观建议。
所有事实和工作要求必须能在原文中直接找到依据；不得补写背景、数字、地点或因果关系。政策建议必须与事实摘要明确分开。"""


def log(message: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {message}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def redact_identity(value: str) -> str:
    """避免在本通道的展示字段中重复具体身份，原文仍由 URL 追溯。"""
    value = re.sub(r"[\u4e00-\u9fff]{2,8}(?:总书记|国家主席|主席)", "中央领导", value)
    value = value.replace("国家主席", "中央领导")
    return value


def clean_result(value: Any, aliases: Optional[List[str]] = None) -> Any:
    aliases = aliases or []
    if isinstance(value, str):
        cleaned = redact_identity(value)
        for alias in aliases:
            cleaned = cleaned.replace(alias, "中央领导")
        return cleaned
    if isinstance(value, list):
        return [clean_result(v, aliases) for v in value]
    if isinstance(value, dict):
        return {k: clean_result(v, aliases) for k, v in value.items()}
    return value


def is_followup_report(text: str) -> bool:
    if any(word in text for word in FOLLOWUP_WORDS):
        return True
    if FOLLOWUP_PREFIX_RE.search(text.strip()):
        return True
    # 「激励…团结奋斗」类学习反响稿
    if "激励" in text and ("团结奋斗" in text or "奋进" in text):
        return True
    return False


def is_primary_report(text: str) -> bool:
    if PRIMARY_ACTIVITY_RE.search(text) or "考察时强调" in text:
        return True
    if PRIMARY_MEETING_RE.search(text):
        return True
    if PRIMARY_INSTRUCTION_RE.search(text):
        return True
    return False


def is_top_leadership_signal(text: str) -> bool:
    """只保留最高层：总书记/国家主席、中央政治局会议、总理考察。"""
    if "总书记" in text or "国家主席" in text:
        return True
    if PRIMARY_MEETING_RE.search(text):
        return True
    if "国务院总理" in text:
        return True
    # 标题直书姓名的最高层公开活动
    if PRIMARY_ACTIVITY_RE.search(text) or PRIMARY_INSTRUCTION_RE.search(text):
        if "习近平" in text or "李强" in text:
            return True
    if "李强" in text and ("主持召开" in text or "主持会议" in text):
        return True
    return False


def likely_candidate(item: Dict[str, Any]) -> bool:
    text = f"{item.get('headline', '')} {item.get('abstract', '')}"
    if any(m in text for m in SKIP_TITLE_MARKERS):
        return False
    # 学习贯彻 / 反响稿不进主通道
    if is_followup_report(text):
        return False
    if not is_primary_report(text):
        return False
    return is_top_leadership_signal(text)


def likely_detail(text: str) -> bool:
    return bool(PRIMARY_DETAIL_RE.search((text or "")[:2000]))


def fetch_gov_yaowen() -> List[Dict[str, Any]]:
    """中国政府网公开要闻 JSON，作为中央通道最高优先级快速信源。"""
    try:
        r = requests.get(GOV_YAOWEN_JSON, headers=HEADERS, timeout=30)
        r.raise_for_status()
        rows = r.json()
    except Exception as exc:
        log(f"中国政府网要闻读取失败：{exc}")
        return []
    out = []
    for row in rows if isinstance(rows, list) else []:
        title = str(row.get("TITLE") or "").strip()
        url = str(row.get("URL") or "").strip()
        date = str(row.get("DOCRELPUBTIME") or "")[:10]
        if title and url and re.fullmatch(r"20\d{2}-\d{2}-\d{2}", date):
            # 视频页优先对应图文（同标题 gov 文）
            out.append({
                "date": date, "headline": title[:160], "abstract": title,
                "id": "gov-" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:20],
                "url": url, "source": GOV_SOURCE_NAME,
            })
    return out


def _abs_xinhua_url(href: str) -> str:
    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://www.news.cn" + href
    if href.startswith("http://"):
        return "https://" + href[len("http://"):]
    return href


def fetch_xinhua_politics_list() -> List[Dict[str, Any]]:
    """新华社时政列表（轻量 HTML 解析，日期从 URL 路径提取）。"""
    if BeautifulSoup is None:
        log("新华社列表跳过：未安装 beautifulsoup4")
        return []
    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for list_url in XINHUA_POLITICS_URLS:
        try:
            r = requests.get(list_url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            if not r.encoding or r.encoding.lower() in ("iso-8859-1", "latin-1"):
                r.encoding = "utf-8"
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception as exc:
            log(f"新华社时政列表读取失败 {list_url}：{exc}")
            continue
        for a in soup.find_all("a", href=True):
            title = re.sub(r"\s+", " ", (a.get_text() or "").strip())
            href = _abs_xinhua_url(a.get("href", ""))
            if not title or len(title) < 10 or not href:
                continue
            if "news.cn" not in href and "xinhuanet.com" not in href:
                continue
            m = re.search(r"/politics/(20\d{2})(\d{2})(\d{2})/", href)
            if not m:
                m = re.search(r"/(20\d{2})-(\d{2})-(\d{2})/", href)
            if not m:
                continue
            date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            if href in seen:
                continue
            seen.add(href)
            out.append({
                "date": date,
                "headline": title[:160],
                "abstract": title,
                "id": "xh-" + hashlib.sha1(href.encode("utf-8")).hexdigest()[:20],
                "url": href,
                "source": XINHUA_SOURCE_NAME,
            })
    log(f"新华社时政列表：{len(out)} 条（未过滤）")
    return out


def fetch_article_body(item: Dict[str, Any]) -> str:
    """按来源取正文。"""
    url = item.get("url") or ""
    source = item.get("source") or ""
    if source == SOURCE_NAME and item.get("id"):
        return fetch_detail(item["id"])
    if "tv.cctv.com" in url:
        # 视频页无长文，跳过
        return ""
    if source == XINHUA_SOURCE_NAME or "news.cn" in url or "xinhuanet.com" in url:
        return fetch_xinhua_detail(url)
    return fetch_official_detail(url)


def fetch_xinhua_detail(url: str) -> str:
    """新华社 / 新华网正文。"""
    if BeautifulSoup is None:
        return ""
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        if not r.encoding or r.encoding.lower() in ("iso-8859-1", "latin-1"):
            r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        el = (
            soup.select_one("#detail")
            or soup.select_one(".main-left")
            or soup.select_one("#p-detail")
            or soup.select_one(".article")
            or soup.select_one("#content")
            or soup.select_one("article")
            or soup.select_one(".xlcontent")
        )
        if not el:
            # meta description 兜底不够，试正文段落拼
            ps = soup.select("#detail p, .main-left p, #p-detail p, article p")
            if ps:
                return re.sub(
                    r"\s+", " ", " ".join(p.get_text(" ", strip=True) for p in ps)
                )
            return ""
        return re.sub(r"\s+", " ", el.get_text(" ", strip=True))
    except Exception as exc:
        log(f"    ✗ 新华社正文失败: {exc}")
        return ""


def source_priority(name: str) -> int:
    return {
        GOV_SOURCE_NAME: 0,
        "央视网（政府网要闻）": 0,
        XINHUA_SOURCE_NAME: 0,
        SHIO_SOURCE_NAME: 1,
        OFFICIAL_SOURCE_NAME: 2,
        SOURCE_NAME: 3,
    }.get(name, 9)


def source_tier(name: str) -> str:
    if name in (GOV_SOURCE_NAME, XINHUA_SOURCE_NAME) or name.startswith("央视网"):
        return "中央权威"
    if name in (SHIO_SOURCE_NAME, OFFICIAL_SOURCE_NAME):
        return "上海官方"
    return "主流党媒"


def source_ref(item: Dict[str, Any]) -> Dict[str, str]:
    name = item.get("source", "公开来源")
    url = item.get("url", "")
    display_name = "央视网（政府网要闻）" if "tv.cctv.com" in url else name
    return {
        "name": display_name,
        "tier": source_tier(name),
        "url": url,
    }


def add_source(entry: Dict[str, Any], item: Dict[str, Any]) -> None:
    if not entry.get("source_tier"):
        entry["source_tier"] = source_tier(entry.get("source", ""))
    refs = entry.setdefault("sources", [])
    known = {ref.get("url") for ref in refs}
    if entry.get("url") and entry.get("url") not in known:
        refs.append({
            "name": entry.get("source", "公开来源"),
            "tier": entry.get("source_tier", source_tier(entry.get("source", ""))),
            "url": entry.get("url", ""),
        })
        known.add(entry.get("url"))
    ref = source_ref(item)
    if ref["url"] and ref["url"] not in known:
        refs.append(ref)
    refs.sort(key=lambda x: source_priority(x.get("name", "")))
    entry["source_count"] = len(refs)
    entry["last_verified_at"] = datetime.now().isoformat(timespec="minutes")
    entry["verification_status"] = "权威来源已核验" if any(
        ref.get("tier") in ("中央权威", "上海官方") for ref in refs
    ) else "公开来源待复核"


def entry_location(entry: Dict[str, Any]) -> str:
    return str(entry.get("location") or extract_location(entry.get("headline", ""))).strip()


def same_event(entry: Dict[str, Any], date: str, headline: str,
               location: str = "") -> bool:
    stored_location = entry_location(entry)
    incoming_location = location or extract_location(headline)
    same_label = entry.get("headline") == headline
    same_place = bool(stored_location and incoming_location and stored_location == incoming_location)
    if not same_label and not same_place:
        return False
    # 同城会议/座谈会标题不同则视为不同事件（避免「北京+会议」误合并）
    if not same_label and same_place:
        h1 = entry.get("headline") or ""
        h2 = headline or ""
        meeting_markers = ("会议", "座谈", "全会", "委员会")
        if any(m in h1 for m in meeting_markers) or any(m in h2 for m in meeting_markers):
            return False
    try:
        left = datetime.strptime(entry.get("date", ""), "%Y-%m-%d")
        right = datetime.strptime(date, "%Y-%m-%d")
        return abs((left - right).days) <= 3
    except ValueError:
        return entry.get("date") == date


def extract_location(raw: str) -> str:
    match = re.search(
        r"(?:在|赴)([\u4e00-\u9fff]{2,12}?)(?:考察|调研|视察|看望|慰问|座谈)", raw
    )
    if not match:
        return ""
    location = match.group(1)
    location = re.sub(r"^(?:春节前夕|期间|近日)", "", location)
    location = re.sub(r"(?:开展|进行|深入)$", "", location)
    return location[:10]


def display_headline(raw: str) -> str:
    location = extract_location(raw)
    if location == "上海":
        return "中央领导在上海开展考察活动"
    if location:
        return f"中央领导在{location}开展考察调研"
    if "党外人士座谈会" in raw:
        return "中央领导主持召开党外人士座谈会"
    if "政治局" in raw:
        return "中央领导主持召开中央政治局会议"
    if "中央全面深化改革" in raw:
        return "中央领导主持召开中央深改委会议"
    if "中央财经" in raw:
        return "中央领导主持召开中央财经委员会会议"
    if "中央经济工作会议" in raw:
        return "中央领导出席中央经济工作会议"
    if PRIMARY_MEETING_RE.search(raw):
        return "中央领导主持召开重要会议"
    if PRIMARY_INSTRUCTION_RE.search(raw) or "重要指示" in raw or "重要讲话" in raw:
        return "中央领导提出重要要求"
    if "讲话" in raw or "指示" in raw:
        return "中央领导提出重要要求"
    return "中央领导开展重要考察调研"


def analyze_fallback(date: str, headline: str, full_text: str) -> Dict[str, Any]:
    """模型不可用时的轻量规则摘要，保证通道不空转。"""
    text = re.sub(r"\s+", " ", full_text or "")[:5000]
    sents = [s.strip() for s in re.split(r"[。！？]", text) if 12 <= len(s.strip()) <= 80]
    key_sents = [
        s for s in sents
        if any(k in s for k in ("强调", "指出", "要求", "决定", "审议", "分析", "部署"))
    ][:5]
    if not key_sents:
        key_sents = sents[:3]
    phrases = []
    for s in key_sents:
        m = re.search(r"(?:强调|指出|要求)[，,:]?(.*)$", s)
        frag = (m.group(1) if m else s).strip(" ，,")
        frag = re.sub(r"^(?:他|她|其)(?:还)?", "", frag)
        frag = re.sub(r"^会议", "", frag)
        frag = frag.strip(" ，,")
        if 6 <= len(frag) <= 36:
            phrases.append(frag)
    meeting = any(k in headline for k in ("会议", "政治局", "座谈会", "委员会", "全会"))
    loc = extract_location(headline) or ("北京" if meeting else "")
    act = "重要会议" if meeting else "考察调研"
    # 要点去掉主语赘余
    clean_points = []
    for s in key_sents[:4]:
        s2 = re.sub(r"^(?:他|她)(?:指出|强调|要求)[，,]?", "", s)
        s2 = re.sub(r"^习近平(?:指出|强调|要求)[，,]?", "", s2)
        clean_points.append(s2.strip() or s)
    summary = "。".join(clean_points[:3])
    if summary and not summary.endswith("。"):
        summary += "。"
    kws = []
    if loc:
        kws.append(loc)
    for w in ("政治局", "五中全会", "经济工作", "城市更新", "防灾减灾", "党外人士", "座谈会"):
        if w in headline or w in text[:800]:
            kws.append(w)
    return clean_result({
        "location": loc or "全国",
        "activity_type": act,
        "summary": summary or headline,
        "key_points": clean_points or [headline],
        "new_phrasing": phrases[:4] or clean_points[:2],
        "directives": clean_points[:3],
        "theme": act,
        "keywords": kws[:6],
        "policy_implications": [
            "可对照本次公开要求，梳理上海相关领域落实切口与监测指标。"
        ],
        "_fallback": True,
    })


def analyze(date: str, headline: str, full_text: str) -> Dict[str, Any]:
    prompt = f"""日期：{date}
活动概括：{headline}

公开报道原文：
{full_text[:9000]}

请提炼本次公开活动中的新提法、新要求和工作方法，重点回答：最高层释放了哪些方向性信号？哪些内容可转化为上海民主党派参政议政的调研切口？"""
    aliases = sorted(set(re.findall(
        r"([\u4e00-\u9fff]{2,4})(?:总书记|国家主席)", full_text
    )))
    result = clean_result(
        llm_json(SYSTEM_PROMPT, prompt, max_tokens=1100, temperature=0.2),
        aliases,
    )
    if result and (result.get("summary") or result.get("key_points")):
        return result
    raise RuntimeError("Grok 4.6 未返回有效中央通道分析")

def save(results: List[Dict[str, Any]]) -> int:
    previous_out = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
    previous_latest = {}
    if LATEST.exists():
        try:
            previous_latest = json.loads(LATEST.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            previous_latest = {}
    results = [item for item in results if (
        not item.get("activity_type")
        or any(word in str(item.get("activity_type")) for word in VALID_ACTIVITY_WORDS)
        or any(word in str(item.get("headline", "")) for word in ("会议", "考察", "调研", "指示", "讲话"))
    )]
    results.sort(key=lambda x: x.get("date", ""), reverse=True)
    unique: List[Dict[str, Any]] = []
    for item in results:
        if not item.get("source_tier"):
            item["source_tier"] = source_tier(item.get("source", ""))
        for ref in item.get("sources", []):
            if "tv.cctv.com" in ref.get("url", ""):
                ref["name"] = "央视网（政府网要闻）"
        if entry_location(item) == "上海":
            item["location"] = "上海"
            item["headline"] = "中央领导在上海开展考察活动"
        matched = next((x for x in unique if same_event(
            x, item.get("date", ""), item.get("headline", ""), entry_location(item)
        )), None)
        if matched:
            matched["date"] = min(matched.get("date", ""), item.get("date", ""))
            for ref in item.get("sources", []):
                add_source(matched, {
                    "source": ref.get("name", "公开来源"),
                    "url": ref.get("url", ""),
                })
            if item.get("url"):
                add_source(matched, item)
            continue
        if not item.get("sources") and item.get("url"):
            add_source(item, item)
        unique.append(item)
    unique.sort(key=lambda x: x.get("date", ""), reverse=True)
    rendered = json.dumps(unique, ensure_ascii=False, indent=2)
    changed = rendered != previous_out
    OUT.write_text(rendered, encoding="utf-8")
    latest = unique[0] if unique else {}
    LATEST.write_text(json.dumps({
        "date": latest.get("date", ""),
        "generated_at": (datetime.now().strftime("%Y-%m-%d %H:%M")
                         if changed else previous_latest.get("generated_at", "")),
        "count": len(unique),
        "latest": latest,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(unique)


def main() -> int:
    since = SINCE or (datetime.now() - timedelta(days=DEFAULT_DAYS)).strftime("%Y-%m-%d")
    history = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else []
    history_by_url = {x.get("url"): x for x in history if x.get("url")}
    for entry in history:
        for ref in entry.get("sources", []):
            if ref.get("url"):
                history_by_url[ref["url"]] = entry
    results = list(history)
    seen = set(history_by_url)
    candidates: List[Dict[str, Any]] = []

    fast_sources = (
        fetch_gov_yaowen()
        + fetch_xinhua_politics_list()
        + fetch_shio_push_list()
        + fetch_official_news_list()
    )
    for item in fast_sources:
        if item["date"] >= since and item["url"] not in seen and likely_candidate(item):
            candidates.append(item)
            seen.add(item["url"])

    for query in OFFICIAL_QUERIES:
        for page in range(1, min(MAX_PAGES, 6) + 1):
            for item in fetch_official_search_page(query, page):
                if item["date"] >= since and item["url"] not in seen and likely_candidate(item):
                    item["source"] = OFFICIAL_SOURCE_NAME
                    candidates.append(item)
                    seen.add(item["url"])

    offset = ""
    for _ in range(MAX_PAGES):
        data = fetch_list_page(offset)
        if not data:
            break
        for item in parse_list(data):
            if item["date"] >= since and item["url"] not in seen and likely_candidate(item):
                item["source"] = SOURCE_NAME
                candidates.append(item)
                seen.add(item["url"])
        offset = urllib.parse.unquote_plus(data.get("offsetInfo", "") or "")
        if not data.get("hasNext") or not offset:
            break

    def _kind(headline: str) -> int:
        if PRIMARY_MEETING_RE.search(headline):
            return 0
        if PRIMARY_ACTIVITY_RE.search(headline) or PRIMARY_INSTRUCTION_RE.search(headline):
            return 1
        return 2

    # 权威源优先 → 会议/考察优先 → 日期新优先
    candidates.sort(
        key=lambda x: (
            source_priority(x.get("source", "")),
            _kind(x.get("headline", "")),
            # 日期降序：取反时间戳不方便，用零填充负序键
            tuple(-int(p) for p in x.get("date", "0-0-0").split("-") if p.isdigit()),
        )
    )

    analyzed = 0
    skipped_quota = 0
    skipped_source = 0
    log(
        f"中央通道候选：窗口 {since} 起共 {len(candidates)} 条，"
        f"分析上限 {MAX_ANALYZE}，开始正文核验"
    )
    for item in candidates:
        if item["url"] in history_by_url and history_by_url[item["url"]].get("summary"):
            continue
        # 视频页跳过分析（无长文）
        if "tv.cctv.com" in (item.get("url") or ""):
            continue
        headline = display_headline(item["headline"])
        item_location = extract_location(item["headline"])
        existing = next((entry for entry in results if same_event(
            entry, item["date"], headline, item_location
        )), None)
        if existing:
            existing["date"] = min(existing.get("date", item["date"]), item["date"])
            add_source(existing, item)
            continue
        # 仅权威一级口做模型分析；上海源等只挂交叉验证
        if item.get("source") not in ANALYZE_SOURCES:
            skipped_source += 1
            continue
        if analyzed >= MAX_ANALYZE:
            skipped_quota += 1
            continue
        full_text = fetch_article_body(item)
        if not full_text or len(full_text) < 80:
            continue
        # 会议通稿可能不含「在…考察」，用更宽 detail 规则
        text_ok = likely_detail(full_text)
        if not text_ok and PRIMARY_MEETING_RE.search(item.get("headline", "")):
            text_ok = ("政治局" in full_text[:2000] or "会议" in full_text[:800])
        if not text_ok:
            continue
        log(f"  LLM 分析：{item['date']} · {headline} · {item.get('source')}")
        analysis = analyze(item["date"], headline, full_text)
        if not analysis:
            continue
        loc = analysis.get("location") or extract_location(item["headline"]) or ""
        if PRIMARY_MEETING_RE.search(item.get("headline", "")) and not loc:
            loc = "北京"
        act = analysis.get("activity_type") or (
            "重要会议" if PRIMARY_MEETING_RE.search(item.get("headline", "")) else "考察调研"
        )
        entry = {
            "id": "central-" + hashlib.sha1(item["url"].encode("utf-8")).hexdigest()[:20],
            "date": item["date"],
            "leader": "中央领导",
            "role": "中央层面重要活动",
            "role_rank": 0,
            "headline": headline,
            "summary": analysis.get("summary", ""),
            "key_points": analysis.get("key_points", []),
            "directives": analysis.get("directives", []),
            "new_phrasing": analysis.get("new_phrasing", []),
            "keywords": analysis.get("keywords", []),
            "location": loc,
            "activity_type": act,
            "theme": analysis.get("theme", "综合"),
            "policy_implications": analysis.get("policy_implications", ""),
            "source": item.get("source", SOURCE_NAME),
            "source_tier": source_tier(item.get("source", SOURCE_NAME)),
            "source_count": 1,
            "verification_status": (
                "权威来源已核验"
                if source_priority(item.get("source", "")) <= 1
                else "公开来源待复核"
            ),
            "sources": [source_ref(item)],
            "analyzed_at": datetime.now().isoformat(timespec="minutes"),
            "url": item["url"],
        }
        results.append(entry)
        history_by_url[item["url"]] = entry
        analyzed += 1
        log(f"  已入库：{item['date']} · {headline}")

    saved_count = save(results)
    log(
        f"中央通道：窗口 {since} 起，候选 {len(candidates)} 条，"
        f"新分析 {analyzed} 条（上限 {MAX_ANALYZE}），"
        f"非分析源跳过 {skipped_source}，配额跳过 {skipped_quota}，"
        f"累计 {saved_count} 条"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
