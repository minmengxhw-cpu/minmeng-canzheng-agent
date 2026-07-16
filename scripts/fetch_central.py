#!/usr/bin/env python3
"""独立抓取中央领导在上海的公开行程与重要指示。

本通道与市委主要领导数据分开存储、分开统计，只对新来源 URL 调用 MiniMax-M3.0。
公开页面中的具体身份统一在站内显示为“中央领导”，保留原文链接供追溯。
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
from typing import Any, Dict, List

from fetch_leaders import (
    OFFICIAL_SOURCE_NAME,
    SHIO_SOURCE_NAME,
    SOURCE_NAME,
    fetch_detail,
    fetch_list_page,
    fetch_official_detail,
    fetch_official_news_list,
    fetch_official_search_page,
    fetch_shio_push_list,
    minimax_json,
    parse_list,
)
import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "central_leaders.json"
LATEST = ROOT / "data" / "central_latest.json"
LOG = ROOT / "data" / "central_leaders.log"
SINCE = os.environ.get("SINCE", "")
DEFAULT_DAYS = int(os.environ.get("CENTRAL_DEFAULT_DAYS", "7"))
MAX_PAGES = int(os.environ.get("MAX_PAGES", "6"))
GOV_SOURCE_NAME = "中国政府网"
GOV_YAOWEN_JSON = "https://www.gov.cn/yaowen/liebiao/YAOWENLIEBIAO.json"

OFFICIAL_QUERIES = ("总书记", "上海考察")
ACTION_WORDS = ("考察", "调研", "视察", "讲话", "指示", "要求", "强调", "部署")
FOLLOWUP_WORDS = ("认真学习", "学习贯彻", "激励广大", "引发热烈反响", "重要讲话精神")

SYSTEM_PROMPT = """你是参政议政研究助理，负责提炼中央领导在上海公开活动中的工作指向。
只输出严格 JSON 对象，不要 Markdown，不要解释。
不要输出具体人物姓名，统一称为“中央领导”。
字段必须包括：summary、key_points、directives、theme、policy_implications。
summary 为 120-220 字；key_points 和 directives 各 2-5 条；policy_implications 为 1-3 条可核验的参政议政建议。
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


def clean_result(value: Any) -> Any:
    if isinstance(value, str):
        return redact_identity(value)
    if isinstance(value, list):
        return [clean_result(v) for v in value]
    if isinstance(value, dict):
        return {k: clean_result(v) for k, v in value.items()}
    return value


def likely_candidate(item: Dict[str, Any]) -> bool:
    text = f"{item.get('headline', '')} {item.get('abstract', '')}"
    has_location = "上海" in text or "沪" in text
    has_central_marker = "总书记" in text or "上海考察" in text or "中央领导" in text
    is_primary_report = "在上海考察" in text or "上海考察时强调" in text
    if any(word in text for word in FOLLOWUP_WORDS) and not is_primary_report:
        return False
    return has_location and has_central_marker and any(word in text for word in ACTION_WORDS)


def likely_detail(text: str) -> bool:
    return (
        "上海" in text
        and ("总书记" in text or "党中央" in text)
        and any(word in text for word in ACTION_WORDS)
    )


def fetch_gov_yaowen() -> List[Dict[str, Any]]:
    """中国政府网公开要闻 JSON，作为中央通道最高优先级快速信源。"""
    try:
        r = requests.get(GOV_YAOWEN_JSON, timeout=30)
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
            out.append({
                "date": date, "headline": title[:160], "abstract": title,
                "id": "gov-" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:20],
                "url": url, "source": GOV_SOURCE_NAME,
            })
    return out


def source_priority(name: str) -> int:
    return {
        GOV_SOURCE_NAME: 0,
        "央视网（政府网要闻）": 0,
        SHIO_SOURCE_NAME: 1,
        OFFICIAL_SOURCE_NAME: 2,
        SOURCE_NAME: 3,
    }.get(name, 9)


def source_tier(name: str) -> str:
    if name == GOV_SOURCE_NAME:
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


def same_event(entry: Dict[str, Any], date: str, headline: str) -> bool:
    if entry.get("headline") != headline:
        return False
    try:
        left = datetime.strptime(entry.get("date", ""), "%Y-%m-%d")
        right = datetime.strptime(date, "%Y-%m-%d")
        return abs((left - right).days) <= 3
    except ValueError:
        return entry.get("date") == date


def display_headline(raw: str) -> str:
    if "上海考察" in raw or "考察" in raw:
        return "中央领导在上海开展考察活动"
    if "讲话" in raw or "指示" in raw:
        return "中央领导对上海作出重要指示"
    return "中央领导在上海的重要活动"


def analyze(date: str, headline: str, full_text: str) -> Dict[str, Any]:
    prompt = f"""日期：{date}
活动概括：{headline}

公开报道原文：
{full_text[:9000]}

请提炼工作指向，重点回答：对上海当前工作提出了哪些要求？哪些内容对民主党派参政议政具有直接参考价值？"""
    try:
        return clean_result(minimax_json(SYSTEM_PROMPT, prompt, max_tokens=1000, temperature=0.2))
    except Exception as exc:
        log(f"模型分析失败：{exc}")
        return {}


def save(results: List[Dict[str, Any]]) -> None:
    previous_out = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
    previous_latest = {}
    if LATEST.exists():
        try:
            previous_latest = json.loads(LATEST.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            previous_latest = {}
    results.sort(key=lambda x: x.get("date", ""), reverse=True)
    unique: List[Dict[str, Any]] = []
    for item in results:
        if not item.get("source_tier"):
            item["source_tier"] = source_tier(item.get("source", ""))
        for ref in item.get("sources", []):
            if "tv.cctv.com" in ref.get("url", ""):
                ref["name"] = "央视网（政府网要闻）"
        matched = next((x for x in unique if same_event(
            x, item.get("date", ""), item.get("headline", "")
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

    fast_sources = fetch_gov_yaowen() + fetch_shio_push_list() + fetch_official_news_list()
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

    analyzed = 0
    candidates.sort(key=lambda x: (source_priority(x.get("source", "")), x.get("date", "")))
    for item in candidates:
        if item["url"] in history_by_url and history_by_url[item["url"]].get("summary"):
            continue
        headline = display_headline(item["headline"])
        existing = next((entry for entry in results if same_event(entry, item["date"], headline)), None)
        if existing:
            existing["date"] = min(existing.get("date", item["date"]), item["date"])
            add_source(existing, item)
            continue
        full_text = (fetch_detail(item["id"])
                     if item.get("source") == SOURCE_NAME
                     else fetch_official_detail(item["url"]))
        if not full_text or not likely_detail(full_text):
            continue
        analysis = analyze(item["date"], headline, full_text)
        if not analysis:
            continue
        results.append({
            "id": "central-" + hashlib.sha1(item["url"].encode("utf-8")).hexdigest()[:20],
            "date": item["date"],
            "leader": "中央领导",
            "role": "中央层面重要活动",
            "role_rank": 0,
            "headline": headline,
            "summary": analysis.get("summary", ""),
            "key_points": analysis.get("key_points", []),
            "directives": analysis.get("directives", []),
            "theme": analysis.get("theme", "城市治理"),
            "policy_implications": analysis.get("policy_implications", ""),
            "source": item.get("source", SOURCE_NAME),
            "source_tier": source_tier(item.get("source", SOURCE_NAME)),
            "source_count": 1,
            "verification_status": "权威来源已核验" if source_priority(item.get("source", "")) <= 2 else "公开来源待复核",
            "sources": [source_ref(item)],
            "analyzed_at": datetime.now().isoformat(timespec="minutes"),
            "url": item["url"],
        })
        analyzed += 1

    save(results)
    log(f"中央通道：窗口 {since} 起，候选 {len(candidates)} 条，新分析 {analyzed} 条，累计 {len(results)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
