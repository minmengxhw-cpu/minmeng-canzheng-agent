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
    SOURCE_NAME,
    fetch_detail,
    fetch_list_page,
    fetch_official_detail,
    fetch_official_search_page,
    minimax_json,
    parse_list,
)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "central_leaders.json"
LATEST = ROOT / "data" / "central_latest.json"
LOG = ROOT / "data" / "central_leaders.log"
SINCE = os.environ.get("SINCE", "")
DEFAULT_DAYS = int(os.environ.get("CENTRAL_DEFAULT_DAYS", "7"))
MAX_PAGES = int(os.environ.get("MAX_PAGES", "6"))

OFFICIAL_QUERIES = ("总书记", "上海考察")
ACTION_WORDS = ("考察", "调研", "视察", "讲话", "指示", "要求", "强调", "部署")

SYSTEM_PROMPT = """你是参政议政研究助理，负责提炼中央领导在上海公开活动中的工作指向。
只输出严格 JSON 对象，不要 Markdown，不要解释。
不要输出具体人物姓名，统一称为“中央领导”。
字段必须包括：summary、key_points、directives、theme、policy_implications。
summary 为 120-220 字；key_points 和 directives 各 2-5 条；policy_implications 为 1-3 条可核验的参政议政建议。"""


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
    return has_location and has_central_marker and any(word in text for word in ACTION_WORDS)


def likely_detail(text: str) -> bool:
    return (
        "上海" in text
        and ("总书记" in text or "党中央" in text)
        and any(word in text for word in ACTION_WORDS)
    )


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
    results.sort(key=lambda x: x.get("date", ""), reverse=True)
    unique: List[Dict[str, Any]] = []
    seen_keys = set()
    for item in results:
        key = (item.get("date", ""), item.get("headline", ""))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        unique.append(item)
    OUT.write_text(json.dumps(unique, ensure_ascii=False, indent=2), encoding="utf-8")
    latest = unique[0] if unique else {}
    LATEST.write_text(json.dumps({
        "date": latest.get("date", ""),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "count": len(unique),
        "latest": latest,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    since = SINCE or (datetime.now() - timedelta(days=DEFAULT_DAYS)).strftime("%Y-%m-%d")
    history = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else []
    history_by_url = {x.get("url"): x for x in history if x.get("url")}
    results = list(history)
    seen = set(history_by_url)
    candidates: List[Dict[str, Any]] = []

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
    for item in candidates:
        if item["url"] in history_by_url and history_by_url[item["url"]].get("summary"):
            continue
        full_text = (
            fetch_official_detail(item["url"])
            if item.get("source") == OFFICIAL_SOURCE_NAME
            else fetch_detail(item["id"])
        )
        if not full_text or not likely_detail(full_text):
            continue
        headline = display_headline(item["headline"])
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
            "url": item["url"],
        })
        analyzed += 1

    save(results)
    log(f"中央通道：窗口 {since} 起，候选 {len(candidates)} 条，新分析 {analyzed} 条，累计 {len(results)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
