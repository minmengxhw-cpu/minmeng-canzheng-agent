#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CZ Agent · 每日动向简报
每次抓取后跑：用已有分析字段拼「1 分钟可读」简报（不调大模型）。

双通道：
  - 最高层 / 中央考察：data/central_leaders.json
  - 上海书记市长：data/leaders.json

结构：
  【一】最高层 · 关键词 / 金句 / 变化
  【二】上海 · 关键词 / 金句 / 变化
  【三】当日简报（先中央后上海）

约束：无省略号截断；靠条数控制在约 1 分钟内读完。

输出：
  - data/brief_latest.json
  - briefs/YYYY-MM-DD.md

推送：
  - FEISHU_WEBHOOK / FEISHU_CHAT_ID
  - FEISHU_PUSH_ALWAYS=1  两侧皆无新增时也推
  - BRIEF_WEBHOOK
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys
import urllib.request
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

LEADERS = os.path.join(ROOT, "data", "leaders.json")
CENTRAL = os.path.join(ROOT, "data", "central_leaders.json")
PHRASE_CHRONO = os.path.join(ROOT, "data", "phrase_chronology.json")
OUT_JSON = os.path.join(ROOT, "data", "brief_latest.json")
BRIEF_DIR = os.path.join(ROOT, "briefs")
SITE_DEFAULT = "https://minmengxhw-cpu.github.io/minmeng-canzheng-agent/"

# 飞书 1 分钟：三块结构，略放宽总长；无省略号
MAX_PUSH_CHARS = 760
MAX_POINTS_PER_ITEM = 1
MAX_QUOTES_PER_ITEM = 2
MAX_DAY_QUOTES = 2
MAX_KEYWORDS = 5
MAX_CHANGE_WORDS = 2
MAX_QUOTE_LEN = 36
MAX_KW_LEN = 16
MAX_CENTRAL_NEWS = 2
MAX_SH_NEWS = 3
CENTRAL_LOOKBACK_DAYS = 7

# 低信息量填充，不作为金句/关键词主推
_STOP_FRAGMENTS = (
    "有关",
    "相关",
    "工作",
    "进一步",
    "持续",
    "不断",
    "有力",
    "切实",
    "认真",
    "深入",
)


def np_of(s: dict) -> List[str]:
    v = s.get("new_phrasing")
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str) and v.strip():
        return [v.strip()]
    return []


def dminus(ymd: str, n: int) -> str:
    y, m, d = map(int, ymd.split("-"))
    return (datetime.date(y, m, d) - datetime.timedelta(days=n)).isoformat()


def _clean_space(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _full_under(text: str, n: int) -> str:
    """完整文本；超长则返回空串（禁止用省略号截断）。"""
    text = _clean_space(text)
    if not text:
        return ""
    return text if len(text) <= n else ""


def _complete_sentences(text: str, max_chars: int) -> str:
    """按完整句子取摘要，绝不在句中截断，也不加省略号。"""
    text = _clean_space(text)
    if not text:
        return ""
    if len(text) <= max_chars:
        return text.rstrip("。．") + ("。" if text[-1] not in "。．！？!?" else "")
    # 按句号/分号切开，拼到预算内
    parts = [p.strip() for p in re.split(r"(?<=[。．！？!?；;])", text) if p.strip()]
    if not parts:
        # 无句读：宁可选更短字段，也不截断
        return ""
    out: List[str] = []
    size = 0
    for p in parts:
        # 保证每句以句读收尾（分号保留）
        piece = p if p[-1] in "。．！？!?；;" else p + "。"
        add = len(piece) + (0 if not out else 0)
        if size + add > max_chars:
            break
        out.append(piece)
        size += add
    if not out:
        # 第一句就超长：尝试分号/逗号完整意群
        chunks = [c.strip() for c in re.split(r"[；;]", parts[0]) if c.strip()]
        for c in chunks:
            piece = c if c[-1] in "。．！？!?" else c + "。"
            if len(piece) <= max_chars:
                return piece
        return ""
    return "".join(out)


def _as_list(v: Any) -> List[str]:
    if not v:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str) and v.strip():
        return [v.strip()]
    return []


def _strip_leader_names(text: str) -> str:
    for name in ("陈吉宁", "龚正"):
        text = text.replace(name, "")
    return re.sub(r"\s+", " ", text).strip(" 　·—-，,")


def _activity_title(s: dict) -> str:
    """完整标题：优先场合；过长则换用更短完整字段，绝不省略号截断。"""
    candidates = [
        _strip_leader_names((s.get("occasion") or "").strip()),
        _strip_leader_names((s.get("headline") or "").strip()),
    ]
    # 也可用主题+首关键词作短标题
    theme = (s.get("theme") or "").strip()
    kws = _as_list(s.get("keywords"))
    if theme and kws:
        candidates.append(f"{theme}·{kws[0]}")
    elif theme:
        candidates.append(theme)
    # 选不超过 28 字的完整候选；否则取最短完整候选
    under = [c for c in candidates if c and len(c) <= 28]
    if under:
        return under[0]
    nonempty = [c for c in candidates if c]
    return min(nonempty, key=len) if nonempty else "公开活动"


def _normalize_phrase(p: str) -> str:
    p = re.sub(r"[（(].*?[）)]", "", p)  # 去掉括号注释，如（三化表述）
    p = re.sub(r"\s+", "", p)
    return p.strip("「」『』“”\"'·。；;，,")


def _display_phrase(p: str) -> str:
    """展示用：去掉分析附注括号，保留金句本体。"""
    p = re.sub(r"[（(][^）)]{0,20}[）)]", "", p)
    return re.sub(r"\s+", " ", p).strip(" 「」『』“”\"'")


def _parse_change_phrases(change_note: str, known_phrases: Optional[Sequence[str]] = None) -> List[str]:
    """从 change_note 抽出重大变化表述。

    优先用 new_phrasing 与正文对齐（避免「智能化、绿色化、融合化」被顿号切碎），
    对不齐再按分号/句号粗切。
    """
    if not change_note:
        return []
    m = re.search(r"本次新增表述[：:]\s*(.+)$", change_note.strip())
    if not m:
        m = re.search(r"新增表述[：:]\s*(.+)$", change_note.strip())
    if not m:
        return []
    body = m.group(1).rstrip("。．. ")
    body_norm = _normalize_phrase(body)
    out: List[str] = []
    if known_phrases:
        ranked = sorted(known_phrases, key=lambda x: -len(_normalize_phrase(x)))
        for p in ranked:
            n = _normalize_phrase(p)
            if len(n) < 4:
                continue
            if n in body_norm or p in body:
                out.append(_display_phrase(p))
                body_norm = body_norm.replace(n, " ")
        if out:
            return out
    # 回退：按分号切，不用顿号（金句里常有顿号对仗）
    parts = re.split(r"[；;]", body)
    for p in parts:
        p = _display_phrase(p)
        if len(p) >= 6:
            out.append(p)
    return out


def _load_phrase_counts() -> Dict[str, int]:
    path = PHRASE_CHRONO
    if not os.path.isfile(path):
        return {}
    try:
        raw = json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}
    counts: Dict[str, int] = {}
    if isinstance(raw, list):
        for row in raw:
            if not isinstance(row, dict):
                continue
            ph = _normalize_phrase(row.get("phrase") or "")
            if ph:
                counts[ph] = int(row.get("count") or 1)
    return counts


def _build_history_phrase_index(all_data: List[dict], before_date: str) -> Counter:
    """历史窗口内提法出现次数（不含当日）。"""
    c: Counter = Counter()
    for s in all_data:
        d = s.get("date") or ""
        if not d or d >= before_date:
            continue
        for p in np_of(s):
            n = _normalize_phrase(p)
            if n:
                c[n] += 1
        for p in _as_list(s.get("keywords")):
            n = _normalize_phrase(p)
            if 2 <= len(n) <= 16:
                c[n] += 1
    return c


def _score_gold_quote(
    phrase: str,
    *,
    change_norms: Set[str],
    hist: Counter,
    chrono: Dict[str, int],
) -> float:
    """金句打分：重大变化 > 首次出现 > 长度适中 > 非填充。"""
    raw = phrase.strip()
    norm = _normalize_phrase(raw)
    if len(norm) < 4:
        return -1
    score = 0.0
    n = len(raw)
    # 金句最佳长度约 8–28 字
    if 8 <= n <= 28:
        score += 4
    elif 6 <= n <= 40:
        score += 2
    elif n > 48:
        score -= 2

    if any(norm == c or norm in c or c in norm for c in change_norms):
        score += 6  # 明确标为重大变化

    hist_n = hist.get(norm, 0)
    chrono_n = chrono.get(norm, 0)
    if hist_n == 0 and chrono_n <= 1:
        score += 5  # 新词
    elif hist_n == 0:
        score += 3
    elif hist_n + chrono_n >= 4:
        score += 1.5  # 历史高频回响也可作金句，但弱于新变化

    if any(f in raw for f in _STOP_FRAGMENTS) and n < 12:
        score -= 1.5
    # 含顿号/对仗的表述更像金句
    if "、" in raw or "，" in raw[:20]:
        score += 0.8
    return score


def _is_subsumed(norm: str, kept: Sequence[str]) -> bool:
    """若 norm 被已选金句包含，或反过来是更短重复，则丢弃。"""
    for k in kept:
        kn = _normalize_phrase(k)
        if not kn:
            continue
        if norm == kn:
            return True
        if norm in kn or kn in norm:
            # 保留更长、信息更完整的那条
            if len(norm) <= len(kn):
                return True
    return False


def _pick_gold_quotes(
    phrases: Sequence[str],
    change_phrases: Sequence[str],
    *,
    hist: Counter,
    chrono: Dict[str, int],
    limit: int,
) -> List[str]:
    change_norms = {_normalize_phrase(p) for p in change_phrases if p}
    ranked: List[Tuple[float, str]] = []
    seen: Set[str] = set()
    pool = list(phrases) + [p for p in change_phrases if p not in phrases]
    for p in pool:
        p = _display_phrase(p.strip())
        norm = _normalize_phrase(p)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        sc = _score_gold_quote(p, change_norms=change_norms, hist=hist, chrono=chrono)
        if sc < 0:
            continue
        ranked.append((sc, p))
    ranked.sort(key=lambda x: (-x[0], -len(x[1])))
    picked: List[str] = []
    for _, p in ranked:
        norm = _normalize_phrase(p)
        if _is_subsumed(norm, picked):
            continue
        # 若新句更长且包含已选项，用新句替换
        replaced = False
        for i, old in enumerate(picked):
            on = _normalize_phrase(old)
            if on in norm and len(norm) > len(on):
                picked[i] = p
                replaced = True
                break
        if not replaced:
            picked.append(p)
        if len(picked) >= limit:
            break
    return picked[:limit]


def _item_points(s: dict) -> List[str]:
    """取完整要点（不截断）。保持分析原序，优先首条主旨。"""
    pts = _as_list(s.get("key_points"))
    if pts:
        out = []
        for p in pts:
            p = _clean_space(p)
            if 8 <= len(p) <= 56:
                out.append(p)
            if len(out) >= MAX_POINTS_PER_ITEM:
                break
        if out:
            return out
    summary = (s.get("summary") or "").strip()
    if summary:
        sent = _complete_sentences(summary, 56)
        if sent:
            return [sent.rstrip("。．")]
    phrases = np_of(s)
    for p in phrases:
        p = _display_phrase(p)
        if 6 <= len(p) <= 40:
            return [p]
    return []


def _load_json_list(path: str) -> List[dict]:
    if not os.path.isfile(path):
        return []
    try:
        raw = json.load(open(path, encoding="utf-8"))
    except Exception:
        return []
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict) and x.get("date")]
    return []


def _normalize_central_raw(s: dict) -> dict:
    """把中央条目对齐到上海条目字段，便于共用简报逻辑。"""
    s = dict(s)
    loc = _clean_space(s.get("location") or "")
    # 地点过长时取主地名（括号前）
    loc_short = loc.split("（")[0].strip() if loc else ""
    act = _clean_space(s.get("activity_type") or "")
    hl = _clean_space(s.get("headline") or "")
    # 会议类用标题区分（避免多场会都变成「北京重要会议」）
    if ("会议" in hl or "座谈" in hl or "主持" in hl) and hl:
        s["occasion"] = hl.replace("中央领导", "").strip(" 　") or hl
    elif not s.get("occasion"):
        s["occasion"] = (f"{loc_short}{act}" if loc_short or act else "") or (
            hl or "中央公开活动"
        )
    s["leader"] = s.get("leader") or "中央领导"
    s["role"] = "中央"
    s["role_rank"] = 0
    # theme 字段在中央侧常是长句，展示用活动类型
    long_theme = _clean_space(s.get("theme") or "")
    if len(long_theme) > 14:
        s["theme"] = act or "中央考察"
    elif not long_theme:
        s["theme"] = act or "中央考察"
    if not _as_list(s.get("keywords")):
        kws: List[str] = []
        if loc_short and len(loc_short) <= MAX_KW_LEN:
            kws.append(loc_short)
        if act and len(act) <= MAX_KW_LEN and act not in kws:
            kws.append(act)
        for p in np_of(s):
            p = _display_phrase(p)
            n = _normalize_phrase(p)
            # 过滤祈使长句，只留概念型短提法作关键词
            if n.startswith(("要", "把", "让", "将", "在", "对", "以")):
                continue
            if "，" in p or "、" in p:
                continue
            if 4 <= len(n) <= MAX_KW_LEN and n not in {_normalize_phrase(x) for x in kws}:
                kws.append(p)
            if len(kws) >= 6:
                break
        s["keywords"] = kws
    s["_channel"] = "central"
    return s


def _normalize_shanghai_raw(s: dict) -> dict:
    s = dict(s)
    s["_channel"] = "shanghai"
    return s


def _day_overview_dual(
    maxd: str,
    *,
    central_items: List[dict],
    central_date: str,
    sh_items: List[dict],
) -> str:
    """政务体要情导读（完整句，无省略号）。"""
    parts: List[str] = []
    if central_items:
        c_cn = _ymd_cn(central_date) if central_date else _ymd_cn(maxd)
        titles = []
        for it in central_items[:2]:
            direction = it.get("direction") or ""
            t = direction.split("｜")[-1] if "｜" in direction else direction
            t = _clean_space(t) or (it.get("occasion") or it.get("headline") or "公开活动")
            titles.append(t)
        tip = "、".join(titles)
        parts.append(
            f"中央层面（{c_cn}）发布公开要情{len(central_items)}条，涉及{tip}"
        )
    else:
        parts.append("中央层面公开通道本期无新增要情")
    if sh_items:
        themes: List[str] = []
        seen = set()
        for i in sh_items:
            t = (i.get("theme") or "").strip()
            if t and t not in seen:
                seen.add(t)
                themes.append(t)
        leaders = []
        for i in sh_items:
            name = (i.get("leader") or "").strip()
            if name and name not in leaders:
                leaders.append(name)
        who = "、".join(leaders) if leaders else "市委市政府主要领导"
        theme_txt = "、".join(themes[:3]) if themes else "综合工作"
        parts.append(
            f"上海层面{who}开展公开活动{len(sh_items)}场，工作方向集中于{theme_txt}"
        )
    else:
        parts.append("上海层面本期无新增公开活动")
    return "；".join(parts) + "。"


def build_structured_items(
    raw_today: List[dict],
    *,
    hist: Counter,
    chrono: Dict[str, int],
) -> List[dict]:
    items = []
    for s in raw_today:
        phrases = np_of(s)
        change_phrases = _parse_change_phrases(s.get("change_note") or "", phrases)
        gold = _pick_gold_quotes(
            phrases,
            change_phrases,
            hist=hist,
            chrono=chrono,
            limit=MAX_QUOTES_PER_ITEM,
        )
        kws = [_display_phrase(k) for k in _as_list(s.get("keywords"))[:6]]
        channel = s.get("_channel") or "shanghai"
        theme = (s.get("theme") or "综合").strip()
        items.append(
            {
                "date": s.get("date", ""),
                "role": s.get("role", ""),
                "leader": s.get("leader", ""),
                "theme": theme,
                "headline": s.get("headline", "") or s.get("occasion", ""),
                "occasion": s.get("occasion", ""),
                "direction": f"{theme}｜{_activity_title(s)}",
                "points": _item_points(s),
                "gold_quotes": gold,
                "keywords": kws,
                "channel": channel,
                "change_phrases": [p for p in change_phrases[:4] if p],
                "summary": _clean_space(s.get("summary") or ""),
                "url": s.get("url", ""),
                "phrases": [_display_phrase(p) for p in phrases[:6]],
                "change_note": _clean_space(s.get("change_note") or ""),
            }
        )
    return items


def build_day_signals(
    items: List[dict],
    week_raw: List[dict],
    *,
    hist: Counter,
    chrono: Dict[str, int],
) -> Dict[str, Any]:
    """汇总全日金句 / 关键词 / 高频词 / 变化词。"""
    # —— 金句（跨活动重排 Top N）——
    change_all: List[str] = []
    phrase_all: List[str] = []
    for it in items:
        change_all.extend(it.get("change_phrases") or [])
        phrase_all.extend(it.get("phrases") or [])
        phrase_all.extend(it.get("gold_quotes") or [])
    day_quotes = _pick_gold_quotes(
        phrase_all,
        change_all,
        hist=hist,
        chrono=chrono,
        limit=MAX_DAY_QUOTES,
    )

    # —— 当日关键词：按活动轮询，保证每场调研都有代表性词 ——
    today_label: Dict[str, str] = {}
    today_kw: Counter = Counter()
    per_item_kws: List[List[str]] = []
    for it in items:
        row: List[str] = []
        for k in it.get("keywords") or []:
            n = _normalize_phrase(k)
            if 2 <= len(n) <= 16:
                today_kw[n] += 3
                today_label[n] = _display_phrase(k)
                row.append(n)
        for p in it.get("phrases") or []:
            n = _normalize_phrase(p)
            if n.startswith(("要", "把", "让", "将", "在", "对", "以")):
                continue
            if "，" in p or "、" in p:
                continue
            if 4 <= len(n) <= 12:
                today_kw[n] += 1
                today_label.setdefault(n, _display_phrase(p))
        per_item_kws.append(row)

    keywords: List[str] = []
    seen_kw: Set[str] = set()
    # 第 1 轮：每场活动取首个词
    for row in per_item_kws:
        for n in row:
            if n not in seen_kw:
                seen_kw.add(n)
                keywords.append(today_label.get(n, n))
                break
    # 第 2 轮：补满，优先当日更具体（略长）的词
    rest: List[Tuple[int, str]] = []
    for n, c in today_kw.items():
        if n in seen_kw:
            continue
        rest.append((len(n), n))  # 略偏具体概念
    rest.sort(key=lambda x: -x[0])
    for _, n in rest:
        if len(keywords) >= MAX_KEYWORDS:
            break
        seen_kw.add(n)
        keywords.append(today_label.get(n, n))
    keywords = keywords[:MAX_KEYWORDS]

    # —— 近 7 天词频：用于标「高频」——
    week_kw: Counter = Counter()
    for s in week_raw:
        for k in _as_list(s.get("keywords")):
            n = _normalize_phrase(k)
            if 2 <= len(n) <= 16:
                week_kw[n] += 1
        for p in np_of(s):
            n = _normalize_phrase(p)
            if 4 <= len(n) <= 12:
                week_kw[n] += 1

    # 高频：近 7 天出现 ≥2 且当日也出现
    high_ranked = sorted(
        ((k, week_kw[k]) for k in today_kw if week_kw.get(k, 0) >= 2),
        key=lambda kv: -kv[1],
    )
    high_freq = [today_label.get(k, k) for k, _ in high_ranked[:MAX_KEYWORDS]]
    if not high_freq:
        high_freq = keywords[: min(4, len(keywords))]

    # —— 重大变化词（去重、避免被顿号切碎）——
    change_scored: List[Tuple[float, str]] = []
    seen_c: Set[str] = set()
    for p in change_all:
        p = _display_phrase(p)
        n = _normalize_phrase(p)
        if not n or n in seen_c or _is_subsumed(n, [x[1] for x in change_scored]):
            continue
        seen_c.add(n)
        sc = _score_gold_quote(
            p,
            change_norms={n},
            hist=hist,
            chrono=chrono,
        )
        # 完整变化词；过长（>36）跳过，留给金句区展示完整句
        if len(p) <= 36:
            change_scored.append((sc, p))
    for q in day_quotes:
        q = _display_phrase(q)
        n = _normalize_phrase(q)
        if not n or n in seen_c or _is_subsumed(n, [x[1] for x in change_scored]):
            continue
        if hist.get(n, 0) == 0 and chrono.get(n, 0) <= 1 and len(q) <= 36:
            seen_c.add(n)
            change_scored.append((4.0, q))
    change_scored.sort(key=lambda x: -x[0])
    change_words: List[str] = []
    for _, p in change_scored:
        if _is_subsumed(_normalize_phrase(p), change_words):
            continue
        change_words.append(p)
        if len(change_words) >= MAX_CHANGE_WORDS:
            break

    return {
        "gold_quotes": day_quotes,
        "keywords": keywords,
        "high_freq": high_freq,
        "change_words": change_words,
    }


def _strip_names_in_body(text: str) -> str:
    text = text.strip()
    text = re.sub(r"上海市?(?:委书记|市长)?\s*(?:陈吉宁|龚正)", "", text)
    text = re.sub(r"(?:市委书记|市长)\s*(?:陈吉宁|龚正)", "", text)
    for name in ("陈吉宁", "龚正", "中央领导"):
        text = text.replace(name, "")
    text = re.sub(r"(?:^|[，,。\s])(?:市委书记|市长)(?=[，,。\s专题调研会见主持出席])", " ", text)
    text = re.sub(r"(同志)?(表示|强调|指出|要求)[，,：:\s]*", "", text)
    text = re.sub(r"受党中央委托", "", text)
    return re.sub(r"\s+", " ", text).strip(" ，,。：:")


def _item_brief_line(it: dict, max_chars: int = 64) -> str:
    """单条动态完整摘要：优先完整要点句；不加省略号。"""
    for p in it.get("points") or []:
        p = _strip_names_in_body(_clean_space(p))
        if 8 <= len(p) <= max_chars:
            return p if p[-1] in "。．！？!?" else p + "。"
    if it.get("summary"):
        body = _strip_names_in_body(it["summary"])
        sent = _complete_sentences(body, max_chars)
        if sent:
            return sent
    for q in it.get("gold_quotes") or []:
        q = _display_phrase(q)
        if 4 <= len(q) <= 36:
            return f"核心表述为“{q}”。"
    return "详见公开报道。"


def _filter_full_phrases(phrases: Sequence[str], max_len: int, limit: int) -> List[str]:
    """只保留不超过 max_len 的完整短语。"""
    out: List[str] = []
    seen: Set[str] = set()
    for p in phrases:
        p = _display_phrase(p)
        n = _normalize_phrase(p)
        if not p or not n or n in seen:
            continue
        if len(p) > max_len:
            continue
        if _is_subsumed(n, out):
            continue
        seen.add(n)
        out.append(p)
        if len(out) >= limit:
            break
    return out


def _signal_lists(
    signals: Dict[str, Any], *, n_kw: int, n_q: int, n_chg: int
) -> Tuple[List[str], List[str], List[str]]:
    kws = [
        k
        for k in (signals.get("keywords") or [])
        if 2 <= len(_clean_space(k)) <= MAX_KW_LEN
    ][:n_kw]
    quotes = _filter_full_phrases(signals.get("gold_quotes") or [], MAX_QUOTE_LEN, n_q)
    chg = _filter_full_phrases(signals.get("change_words") or [], MAX_QUOTE_LEN, n_chg)
    return kws, quotes, chg


def _ymd_cn(ymd: str) -> str:
    """2026-07-31 → 2026年7月31日"""
    try:
        y, m, d = ymd.split("-")
        return f"{int(y)}年{int(m)}月{int(d)}日"
    except Exception:
        return ymd


def _period_label() -> str:
    hour = datetime.datetime.now().hour
    if hour < 12:
        return "早报"
    if hour < 18:
        return "午报"
    return "晚报"


def _item_title(it: dict) -> str:
    direction = it.get("direction") or ""
    title = direction.split("｜")[-1] if "｜" in direction else direction
    title = _clean_space(title)
    if not title:
        title = it.get("occasion") or it.get("headline") or it.get("theme") or "公开活动"
    return title


def _append_level_block(
    lines: List[str],
    *,
    section_no: str,
    level_name: str,
    signals: Dict[str, Any],
    items: List[dict],
    empty_note: str,
    date_note: str,
    n_kw: int,
    n_q: int,
    n_chg: int,
    n_news: int,
    brief_max: int,
) -> None:
    """政务体：某层面 = 关键词 + 重要表述 + 动态摘要。"""
    lines.append(f"**{section_no}、{level_name}**")
    if date_note:
        lines.append(f"（信号日期：{date_note}）")
    lines.append("")

    if not items:
        lines.append(empty_note)
        lines.append("")
        return

    kws, quotes, chg = _signal_lists(signals, n_kw=n_kw, n_q=n_q, n_chg=n_chg)
    quote_norms = {_normalize_phrase(q) for q in quotes}
    chg_show = [
        c
        for c in chg
        if _normalize_phrase(c) not in quote_norms
        and not _is_subsumed(_normalize_phrase(c), quotes)
    ]

    lines.append("（一）关键词")
    if kws:
        lines.append("、".join(kws) + "。")
    else:
        lines.append("本期暂无。")
    lines.append("")

    lines.append("（二）重要表述")
    if quotes:
        for i, q in enumerate(quotes, 1):
            lines.append(f"{i}. {q}")
    else:
        lines.append("本期暂无。")
    lines.append("")

    if chg_show:
        lines.append("（三）新提法与变化")
        for i, c in enumerate(chg_show, 1):
            lines.append(f"{i}. {c}")
        lines.append("")
        abs_title = "（四）动态摘要"
    else:
        abs_title = "（三）动态摘要"

    lines.append(abs_title)
    news = items[:n_news]
    for i, it in enumerate(news, 1):
        theme = it.get("theme") or "综合"
        title = _item_title(it)
        body = _item_brief_line(it, max_chars=brief_max)
        lines.append(f"{i}. 【{theme}】{title}。{body}")
    lines.append("")


def build_push_markdown(
    maxd: str,
    overview: str,
    *,
    central_items: List[dict],
    central_signals: Dict[str, Any],
    central_date: str,
    sh_items: List[dict],
    sh_signals: Dict[str, Any],
    hot_txt: str,
    site: str,
) -> str:
    """政务体简报：要情导读 → 中央层面 → 上海层面 → 周观察。无省略号。"""

    period = _period_label()
    date_cn = _ymd_cn(maxd)
    c_date_cn = _ymd_cn(central_date) if central_date else ""

    def assemble(
        n_ckw: int,
        n_cq: int,
        n_cchg: int,
        n_skw: int,
        n_sq: int,
        n_schg: int,
        n_cnews: int,
        n_snews: int,
        brief_max: int,
    ) -> str:
        lines: List[str] = [
            f"**参政议政动态简报（{period}）**",
            f"报告日期：{date_cn}",
            "",
            "**一、要情导读**",
            overview,
            "",
        ]
        _append_level_block(
            lines,
            section_no="二",
            level_name="中央层面",
            signals=central_signals if central_items else {},
            items=central_items,
            empty_note="本期中央层面公开通道无新增要情。",
            date_note=c_date_cn if central_items and central_date != maxd else (
                c_date_cn if central_items else ""
            ),
            n_kw=n_ckw,
            n_q=n_cq,
            n_chg=n_cchg,
            n_news=n_cnews,
            brief_max=brief_max,
        )
        _append_level_block(
            lines,
            section_no="三",
            level_name="上海层面",
            signals=sh_signals if sh_items else {},
            items=sh_items,
            empty_note="本期上海层面无新增公开活动。",
            date_note="",
            n_kw=n_skw,
            n_q=n_sq,
            n_chg=n_schg,
            n_news=n_snews,
            brief_max=brief_max,
        )
        lines.append("**四、近七日上海主题观察**")
        lines.append(hot_txt + "。" if hot_txt and not hot_txt.endswith("。") else (hot_txt or "暂无。"))
        lines.append("")
        lines.append("（根据公开报道整理，仅供参阅；具体表述以原文为准。）")
        if site:
            lines.append(f"网页专栏：{site}")
        return "\n".join(lines).strip()

    plans = [
        (4, 2, 2, 5, 2, 2, MAX_CENTRAL_NEWS, MAX_SH_NEWS, 56),
        (3, 2, 1, 4, 2, 1, 2, 2, 48),
        (3, 1, 1, 3, 2, 1, 1, 2, 44),
        (2, 1, 0, 3, 1, 1, 1, 2, 40),
    ]
    body = assemble(*plans[0])
    for plan in plans[1:]:
        if len(body) <= MAX_PUSH_CHARS and "…" not in body and "..." not in body:
            break
        body = assemble(*plan)
    return body


def build_archive_md(
    maxd: str,
    overview: str,
    *,
    central_items: List[dict],
    central_signals: Dict[str, Any],
    central_date: str,
    sh_items: List[dict],
    sh_signals: Dict[str, Any],
    since: str,
    week_n: int,
    week_phrases: int,
    hot_txt: str,
    site: str,
) -> str:
    lines = [
        f"# 参政议政动态简报（{_period_label()}）",
        "",
        f"**报告日期：** {_ymd_cn(maxd)}",
        "",
        "## 一、要情导读",
        "",
        overview,
        "",
        "## 二、中央层面",
        "",
    ]
    if central_date:
        lines.append(f"信号日期：{_ymd_cn(central_date)}")
        lines.append("")
    if central_items:
        kws = central_signals.get("keywords") or []
        quotes = central_signals.get("gold_quotes") or []
        chg = central_signals.get("change_words") or []
        if kws:
            lines.append("### （一）关键词")
            lines.append("、".join(kws) + "。")
            lines.append("")
        if quotes:
            lines.append("### （二）重要表述")
            for i, q in enumerate(quotes, 1):
                lines.append(f"{i}. {q}")
            lines.append("")
        if chg:
            lines.append("### （三）新提法与变化")
            for c in chg:
                lines.append(f"- {c}")
            lines.append("")
        lines.append("### 动态摘要")
        for n, i in enumerate(central_items, 1):
            lines.append(f"{n}. {i.get('occasion') or i.get('headline')}")
            if i.get("summary"):
                lines.append(i["summary"])
            if i.get("url"):
                lines.append(f"原文：{i['url']}")
            lines.append("")
    else:
        lines.append("本期中央层面公开通道无新增要情。")
        lines.append("")

    lines.append("## 三、上海层面")
    lines.append("")
    kws = sh_signals.get("keywords") or []
    quotes = sh_signals.get("gold_quotes") or []
    chg = sh_signals.get("change_words") or []
    if kws:
        lines.append("### （一）关键词")
        lines.append("、".join(kws) + "。")
        lines.append("")
    if quotes:
        lines.append("### （二）重要表述")
        for i, q in enumerate(quotes, 1):
            lines.append(f"{i}. {q}")
        lines.append("")
    if chg:
        lines.append("### （三）新提法与变化")
        for c in chg:
            lines.append(f"- {c}")
        lines.append("")
    lines.append("### 动态摘要")
    lines.append("")
    if sh_items:
        for n, i in enumerate(sh_items, 1):
            who = f"{i.get('leader') or ''}"
            title = i.get("occasion") or i.get("headline") or ""
            lines.append(f"{n}. 【{i.get('theme') or ''}】{who} · {title}")
            if i.get("summary"):
                lines.append(i["summary"])
            elif i.get("points"):
                lines.append(i["points"][0])
            if i.get("url"):
                lines.append(f"原文：{i['url']}")
            lines.append("")
    else:
        lines.append("本期上海层面无新增公开活动。")
        lines.append("")

    lines.append(f"## 四、近七日上海主题观察（{since} 至 {maxd}）")
    lines.append(f"- 公开信号 {week_n} 条，新提法 {week_phrases} 条")
    lines.append(f"- 活跃主题：{hot_txt}")
    lines.append("")
    lines.append("（根据公开报道整理，仅供参阅；具体表述以原文为准。）")
    lines.append("")
    lines.append(f"网页专栏：{site}")
    return "\n".join(lines)


def _push_feishu(title: str, push_body: str, has_signal: bool) -> None:
    hook = os.environ.get("FEISHU_WEBHOOK", "").strip()
    chat_id = os.environ.get("FEISHU_CHAT_ID", "").strip()
    if not hook and not chat_id:
        return
    always = os.environ.get("FEISHU_PUSH_ALWAYS", "").strip() in ("1", "true", "yes")
    if not has_signal and not always:
        print("brief: 飞书跳过（中央/上海均无新增；设 FEISHU_PUSH_ALWAYS=1 可强制推）")
        return
    try:
        from feishu_push import push_brief_card, push_text
    except ImportError:
        import importlib.util

        path = os.path.join(ROOT, "scripts", "feishu_push.py")
        spec = importlib.util.spec_from_file_location("feishu_push", path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(mod)
        push_brief_card = mod.push_brief_card
        push_text = mod.push_text

    r = push_brief_card(title, push_body, item_lines=None)
    if r.get("ok"):
        ch = r.get("channel") or ("webhook" if hook else "lark-cli")
        print(f"brief: 已主动推送飞书（{ch}）")
        return
    print(f"brief: 飞书卡片/Markdown 失败 {r}，尝试文本…")
    r2 = push_text(f"{title}\n\n{push_body}")
    if r2.get("ok"):
        print("brief: 已主动推送飞书（文本）")
    else:
        print(f"brief: 飞书推送失败 {r2}")


def _push_legacy_webhook(md_text: str, has_signal: bool) -> None:
    hook = os.environ.get("BRIEF_WEBHOOK", "").strip()
    if not hook:
        return
    always = os.environ.get("FEISHU_PUSH_ALWAYS", "").strip() in ("1", "true", "yes")
    if not has_signal and not always:
        return
    try:
        payload = {"text": "**参政议政动态简报**\n\n" + md_text}
        req = urllib.request.Request(
            hook,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=15)
        print("brief: 已推送 BRIEF_WEBHOOK")
    except Exception as e:
        print(f"brief: BRIEF_WEBHOOK 推送失败 {e}")


def main() -> None:
    sh_data = [_normalize_shanghai_raw(s) for s in _load_json_list(LEADERS)]
    central_data = [_normalize_central_raw(s) for s in _load_json_list(CENTRAL)]
    if not sh_data and not central_data:
        print("brief: 无 leaders / central 数据")
        return

    # 报告日：优先上海最新日；无上海则用中央最新日
    maxd = max(
        ([s["date"] for s in sh_data] or ["1970-01-01"])
        + ([s["date"] for s in central_data] or ["1970-01-01"])
    )
    if sh_data:
        maxd = max(s["date"] for s in sh_data)

    chrono = _load_phrase_counts()
    hist_all = _build_history_phrase_index(sh_data + central_data, maxd)

    # —— 上海：报告日当日 ——
    sh_raw = [s for s in sh_data if s["date"] == maxd]
    sh_raw.sort(
        key=lambda s: (s.get("role_rank", 9), s.get("leader", ""), s.get("headline", ""))
    )
    sh_items = build_structured_items(sh_raw, hist=hist_all, chrono=chrono)

    # —— 中央：报告日当日；若无，则取近 7 天内最新有数据的一天 ——
    central_date = ""
    central_raw: List[dict] = [s for s in central_data if s["date"] == maxd]
    if central_raw:
        central_date = maxd
    else:
        since_c = dminus(maxd, CENTRAL_LOOKBACK_DAYS)
        cand_dates = sorted(
            {s["date"] for s in central_data if since_c <= s["date"] <= maxd},
            reverse=True,
        )
        if cand_dates:
            central_date = cand_dates[0]
            central_raw = [s for s in central_data if s["date"] == central_date]
    central_raw.sort(key=lambda s: (s.get("headline", "")))
    central_items = build_structured_items(central_raw, hist=hist_all, chrono=chrono)

    since = dminus(maxd, 6)
    week_sh = [s for s in sh_data if s["date"] >= since]
    week_central = [s for s in central_data if s["date"] >= since]
    week_phrases = sum(len(np_of(s)) for s in week_sh)
    theme_cnt: Dict[str, int] = {}
    for s in week_sh:
        t = s.get("theme")
        if t and len(str(t)) <= 12:
            theme_cnt[t] = theme_cnt.get(t, 0) + 1
    hot = sorted(theme_cnt.items(), key=lambda x: -x[1])[:3]
    hot_txt = "、".join(f"{t}({n})" for t, n in hot) if hot else "—"

    sh_signals = build_day_signals(sh_items, week_sh, hist=hist_all, chrono=chrono)
    central_signals = build_day_signals(
        central_items, week_central, hist=hist_all, chrono=chrono
    )

    overview = _day_overview_dual(
        maxd,
        central_items=central_items,
        central_date=central_date,
        sh_items=sh_items,
    )
    summary = overview
    if central_signals.get("gold_quotes"):
        summary += f" 中央重要表述：{'；'.join(central_signals['gold_quotes'][:2])}。"
    if sh_signals.get("gold_quotes"):
        summary += f" 上海重要表述：{'；'.join(sh_signals['gold_quotes'][:2])}。"

    site = os.environ.get("FEISHU_SITE_URL", SITE_DEFAULT).strip() or SITE_DEFAULT
    push_body = build_push_markdown(
        maxd,
        overview,
        central_items=central_items,
        central_signals=central_signals,
        central_date=central_date,
        sh_items=sh_items,
        sh_signals=sh_signals,
        hot_txt=hot_txt,
        site=site,
    )
    period = _period_label()
    title = f"参政议政动态简报（{period}）· {_ymd_cn(maxd)}"

    brief = {
        "date": maxd,
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "summary": summary,
        "overview": overview,
        "push_body": push_body,
        "central": {
            "date": central_date,
            "n_signals": len(central_items),
            "signals": central_signals,
            "items": central_items,
        },
        "shanghai": {
            "date": maxd,
            "n_signals": len(sh_items),
            "n_phrases": sum(len(i.get("phrases") or []) for i in sh_items),
            "signals": sh_signals,
            "items": sh_items,
        },
        # 兼容旧字段
        "signals": sh_signals,
        "today": {
            "n_signals": len(sh_items),
            "n_phrases": sum(len(i.get("phrases") or []) for i in sh_items),
            "items": sh_items,
        },
        "week": {
            "since": since,
            "n_signals": len(week_sh),
            "n_phrases": week_phrases,
            "hot_themes": hot,
        },
    }
    json.dump(brief, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    os.makedirs(BRIEF_DIR, exist_ok=True)
    md_text = build_archive_md(
        maxd,
        overview,
        central_items=central_items,
        central_signals=central_signals,
        central_date=central_date,
        sh_items=sh_items,
        sh_signals=sh_signals,
        since=since,
        week_n=len(week_sh),
        week_phrases=week_phrases,
        hot_txt=hot_txt,
        site=site,
    )
    open(os.path.join(BRIEF_DIR, f"{maxd}.md"), "w", encoding="utf-8").write(md_text)

    has_signal = bool(sh_items or central_items)
    _push_feishu(title, push_body, has_signal)
    _push_legacy_webhook(md_text, has_signal)

    if "…" in push_body or "..." in push_body:
        print("brief: WARN 推送正文含省略号，请检查生成逻辑")
    print(f"brief: {overview}")
    print("--- push preview ---")
    print(push_body)
    print(
        f"--- chars: {len(push_body)} | ellipsis={('…' in push_body) or ('...' in push_body)} "
        f"| central={len(central_items)}@{central_date or '-'} | sh={len(sh_items)} ---"
    )


if __name__ == "__main__":
    main()
