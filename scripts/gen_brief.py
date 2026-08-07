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

# 推送体量：报告日新增 + 精神/关注点变化/参政议政切口；无省略号
MAX_PUSH_CHARS = 2800
MAX_POINTS_PER_ITEM = 4
MAX_QUOTES_PER_ITEM = 4
MAX_DAY_QUOTES = 5
MAX_KEYWORDS = 6
MAX_CHANGE_WORDS = 3
MAX_QUOTE_LEN = 48
MAX_KW_LEN = 16
MAX_CENTRAL_NEWS = 3
MAX_SH_NEWS = 5
# 中央：仅当「信号日 = 报告日」才写入；不再回看 7 天旧闻
CENTRAL_LOOKBACK_DAYS = 0
# 不展示的转载域名（如搜狐），避免非权威原文链进入简报
_BLOCKED_URL_HOSTS = (
    "sohu.com",
    "www.sohu.com",
    "m.sohu.com",
)

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
    # 场景叙述句（今天上午前往…）不是金句
    if re.match(r"^(?:今天|昨日|昨天|近日|上午|下午)", raw) or "前往" in raw[:12] or "走访调研" in raw:
        score -= 4
    # 含顿号/对仗的表述更像金句
    if "、" in raw or "，" in raw[:20]:
        score += 0.8
    # 判断式/部署式加分
    if any(k in raw for k in ("打造", "提升", "加快", "优化", "强化", "坚持", "聚焦", "推进")):
        score += 1.2
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


def _is_scene_sentence(p: str) -> bool:
    """是否现场叙述（非政策要点/金句）。"""
    p = _clean_space(p)
    if re.match(r"^(?:今天|昨日|昨天|近日)?(?:上午|下午|晚上)?，?(?:市委书记|市长)?", p):
        if any(k in p[:30] for k in ("前往", "走访", "调研", "主持", "出席", "会见")):
            return True
    if p.startswith("今天上午") or p.startswith("昨天下午"):
        return True
    return False


def _item_points(s: dict) -> List[str]:
    """取完整要点（不截断）。优先判断式/部署式，过滤纯现场叙述。"""
    out: List[str] = []
    seen: Set[str] = set()

    def _add(p: str) -> None:
        p = _clean_space(p).rstrip("。．")
        if not (8 <= len(p) <= 140) or _is_scene_sentence(p):
            return
        p2 = re.sub(
            r"^(?:陈吉宁|龚正|他|会议)(?:指出|强调|要求|表示|希望)[，,：:\s]*",
            "",
            p,
        )
        p = p2 or p
        if len(p) < 8:
            return
        n = _normalize_phrase(p)
        if not n or n in seen or _is_subsumed(n, out):
            return
        seen.add(n)
        out.append(p)

    for p in _as_list(s.get("key_points")):
        _add(p)
        if len(out) >= MAX_POINTS_PER_ITEM:
            return out
    for p in _split_sents(s.get("summary") or ""):
        _add(p)
        if len(out) >= MAX_POINTS_PER_ITEM:
            return out
    for p in _extract_judgement_sents(_clean_space(s.get("full_text") or ""), limit=8):
        _add(p)
        if len(out) >= MAX_POINTS_PER_ITEM:
            return out
    for p in np_of(s):
        _add(_display_phrase(p))
        if len(out) >= MAX_POINTS_PER_ITEM:
            break
    return out


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


def _public_url(url: str) -> str:
    """过滤搜狐等非权威转载链；保留市政府/解放日报/政府网等。"""
    url = (url or "").strip()
    if not url or not url.startswith("http"):
        return ""
    low = url.lower()
    for host in _BLOCKED_URL_HOSTS:
        if host in low:
            return ""
    return url


def _derive_spirit(s: dict, points: List[str], summary: str) -> str:
    """提炼活动精神要旨（政治判断，非场面叙述）。"""
    chunks: List[str] = []
    for p in points[:3]:
        p = _clean_space(p)
        if p and not _is_scene_sentence(p):
            chunks.append(p.rstrip("。"))
    if not chunks and summary:
        for sent in _split_sents(summary):
            if _is_scene_sentence(sent):
                continue
            if any(k in sent for k in ("指出", "强调", "要求", "希望", "部署", "要")):
                s2 = re.sub(
                    r"^(?:陈吉宁|龚正|他|会议)(?:指出|强调|要求|表示|希望)[，,：:\s]*",
                    "",
                    _clean_space(sent),
                )
                if len(s2) >= 12:
                    chunks.append(s2.rstrip("。"))
            if len(chunks) >= 2:
                break
    theme = (s.get("theme") or "").strip()
    who = (s.get("leader") or "主要领导").strip()
    if not chunks:
        hl = _clean_space(s.get("headline") or s.get("occasion") or "")
        return f"{who}围绕{theme or '重点工作'}开展公开活动，释放持续推进相关部署的明确信号。"
    body = "；".join(chunks[:3])
    if not body.endswith("。"):
        body += "。"
    return f"{who}此次活动精神，集中体现为：{body}"


def _derive_policy_cut(s: dict, points: List[str], theme: str) -> str:
    """参政议政切口（项目初衷：服务民盟建言）。"""
    raw = s.get("policy_implications")
    if isinstance(raw, list):
        raw = "；".join(str(x) for x in raw if x)
    raw = _clean_space(str(raw or ""))
    weak = (
        not raw
        or "可对照本次公开要求" in raw
        or len(raw) < 24
    )
    if not weak:
        return raw if raw.endswith("。") else raw + "。"
    # 规则补强：按主题给可操作切口
    tip_map = {
        "科技产业": "民盟可围绕基础研究稳定支持、人工智能顶尖人才培养评价、产学研成果走向真实场景的中试与概念验证机制等中观议题开展调研。",
        "开放发展": "民盟可围绕外资研发本地化对接、开放平台制度型开放与界别优势转化等议题提出建议。",
        "民生治理": "民盟可聚焦基层治理末梢、公共服务均衡与群众可感的政策评估开展调研。",
        "城市治理": "民盟可围绕超大城市风险防控、治理数字化与事前预防型制度完善提出参政议政建议。",
        "营商环境": "民盟可就企业全周期服务、创新要素对接与细分赛道政策精准性开展界别调研。",
        "生态环境": "民盟可围绕绿色低碳转型、重大工程与生态协同治理开展专题建言。",
        "文化教育": "民盟可就教育科技人才一体推进、拔尖创新人才早期培养与评价改革提出建议。",
    }
    base = tip_map.get(theme, "民盟可对照本次公开部署，选择可操作的中观切口开展调研建言。")
    # 从要点抽 1 个关键词增强针对性
    hint = ""
    for p in points:
        for w in ("人工智能", "基础研究", "人才培养", "安全生产", "营商环境", "新质生产力"):
            if w in p:
                hint = f"尤可关注「{w}」落地中的制度堵点与资源错配。"
                break
        if hint:
            break
    return base.rstrip("。") + ("。" if not hint else "；" + hint)


def _focus_shift_lines(
    today_items: List[dict],
    recent_raw: List[dict],
    *,
    maxd: str,
) -> List[str]:
    """相对近七日历史，提炼关注点变化（服务参政议政研判）。"""
    lines: List[str] = []
    today_themes = []
    seen_t = set()
    for it in today_items:
        t = (it.get("theme") or "").strip()
        if t and t not in seen_t:
            seen_t.add(t)
            today_themes.append(t)
    prev = [s for s in recent_raw if s.get("date") and s["date"] < maxd]
    prev_themes = Counter(
        (s.get("theme") or "").strip() for s in prev if (s.get("theme") or "").strip()
    )
    # 主题层面
    if today_themes:
        cont = [t for t in today_themes if prev_themes.get(t, 0) >= 1]
        fresh = [t for t in today_themes if prev_themes.get(t, 0) == 0]
        if cont:
            lines.append(
                f"主题延续：{ '、'.join(cont) }仍在领导公开活动中保持高权重。"
            )
        if fresh:
            lines.append(f"主题抬升：{ '、'.join(fresh) }成为报告日新出现的主轴方向。")
        # 同主题内表述升级
        for t in today_themes[:2]:
            today_phr = []
            for it in today_items:
                if (it.get("theme") or "") == t:
                    today_phr.extend(it.get("phrases") or [])
                    today_phr.extend(it.get("gold_quotes") or [])
            prev_phr = set()
            for s in prev:
                if (s.get("theme") or "") != t:
                    continue
                for p in np_of(s):
                    prev_phr.add(_normalize_phrase(p))
            novel = []
            for p in today_phr:
                n = _normalize_phrase(p)
                if n and n not in prev_phr and 6 <= len(p) <= 40 and not _is_scene_sentence(p):
                    novel.append(_display_phrase(p))
            # 去重
            uniq = []
            seen = set()
            for p in novel:
                k = _normalize_phrase(p)
                if k not in seen:
                    seen.add(k)
                    uniq.append(p)
            if uniq:
                lines.append(
                    f"「{t}」表述升级：相较近期同主题活动，新近突出“{uniq[0]}”"
                    + (f"、“{uniq[1]}”" if len(uniq) > 1 else "")
                    + "。"
                )
    if not lines and today_items:
        who = "、".join(
            dict.fromkeys(it.get("leader") or "" for it in today_items if it.get("leader"))
        ) or "主要领导"
        lines.append(
            f"{who}公开活动释放的信号，宜放在近七日工作主轴中连续跟踪，研判部署落地节奏。"
        )
    return lines[:4]


def _day_overview_dual(
    maxd: str,
    *,
    central_items: List[dict],
    central_date: str,
    sh_items: List[dict],
) -> str:
    """要情导读：政治判断口吻，点出精神主轴，不写场面流水账。"""
    date_cn = _ymd_cn(maxd)
    parts: List[str] = []
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
        spirits = [it.get("spirit") or "" for it in sh_items if it.get("spirit")]
        spirit_hint = ""
        if spirits:
            s0 = spirits[0]
            m = re.search(r"集中体现为[：:](.+)$", s0)
            core = (m.group(1) if m else s0).strip()
            # 导读只取第一分句，且保证完整句读
            core = re.split(r"[；;]", core)[0].strip().rstrip("。")
            if 8 <= len(core) <= 80:
                spirit_hint = f"核心精神指向{core}"
            elif core:
                # 过长则落到主题级判断，避免半句截断
                spirit_hint = f"核心精神聚焦{theme_txt}部署落地"
        parts.append(
            f"{date_cn}上海{who}新增公开活动{len(sh_items)}场，工作主轴在{theme_txt}"
            + (f"。{spirit_hint}" if spirit_hint else "")
        )
    if central_items and central_date == maxd:
        titles = []
        for it in central_items[:2]:
            t = _clean_space(it.get("headline") or it.get("occasion") or "")
            if t:
                titles.append(t)
        tip = "、".join(titles) if titles else "公开要情"
        parts.append(f"中央层面同日新增{len(central_items)}条，涉及{tip}")
    if not parts:
        return f"{date_cn}中央与上海公开通道均无新增信号。"
    return "；".join(parts) + "。"


def _split_sents(text: str) -> List[str]:
    return [x.strip() for x in re.split(r"(?<=[。！？])", text or "") if x and x.strip()]


def _extract_judgement_sents(full: str, limit: int = 5) -> List[str]:
    """从正文抽判断式/部署式句子（去掉纯现场叙述）。"""
    out: List[str] = []
    for x in _split_sents(full):
        if _is_scene_sentence(x):
            continue
        if not any(k in x for k in ("指出", "强调", "要求", "希望", "要", "部署", "打造", "提升", "加快", "优化")):
            continue
        # 去掉主语套话
        x2 = re.sub(
            r"^(?:陈吉宁|龚正|他|会议)(?:指出|强调|要求|表示|希望)[，,：:\s]*",
            "",
            _clean_space(x),
        )
        x2 = x2.rstrip("。．")
        if 10 <= len(x2) <= 140:
            out.append(x2)
        if len(out) >= limit:
            break
    return out


def _enrich_summary(s: dict) -> str:
    """摘要过短时从 full_text 补 2–4 句，便于「详细一点」。"""
    summary = _clean_space(s.get("summary") or "")
    full = _clean_space(s.get("full_text") or "")
    if not full:
        return summary
    # 摘要已够长时仍可并入 1–2 句判断式，避免只有“走访”场面
    judgements = _extract_judgement_sents(full, limit=4)
    if len(summary) >= 160 and judgements:
        # 若摘要已含关键判断句则不动
        if any(j[:12] in summary for j in judgements[:2]):
            return summary
    if judgements:
        # 首句可保留场面，后接判断句
        lead = ""
        for x in _split_sents(full):
            if _is_scene_sentence(x) or "调研" in x[:20]:
                lead = _clean_space(x)
                break
        body = "。".join(judgements[:3])
        if body and not body.endswith("。"):
            body += "。"
        if lead and lead not in body:
            merged = lead.rstrip("。") + "。" + body
        else:
            merged = body
        if len(merged) > len(summary):
            return merged[:480]
    if len(summary) >= 120:
        return summary
    sents = _split_sents(full)
    key = [
        x for x in sents
        if any(k in x for k in ("指出", "强调", "要求", "希望", "部署", "调研", "主持"))
    ]
    pool = key[:4] if key else sents[:3]
    merged = _clean_space("".join(pool))
    if len(merged) > len(summary):
        return merged[:400]
    return summary or merged


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
        summary = _enrich_summary(s)
        s2 = dict(s)
        s2["summary"] = summary
        points = _item_points(s2)
        # 金句池：new_phrasing + 变化词 + 从要点切短句
        gold_pool = list(phrases) + list(change_phrases)
        for p in points:
            # 长要点里按逗号切出 10–40 字判断小句
            for frag in re.split(r"[，,；;]", p):
                frag = _clean_space(frag)
                if 10 <= len(frag) <= MAX_QUOTE_LEN and any(
                    k in frag for k in ("打造", "提升", "加快", "优化", "强化", "坚持", "聚焦", "推进", "培养", "部署")
                ):
                    gold_pool.append(frag)
        gold = _pick_gold_quotes(
            gold_pool,
            change_phrases,
            hist=hist,
            chrono=chrono,
            limit=MAX_QUOTES_PER_ITEM,
        )
        gold = [g for g in gold if not _is_scene_sentence(g)]
        kws = [_display_phrase(k) for k in _as_list(s.get("keywords"))[:6]]
        channel = s.get("_channel") or "shanghai"
        theme = (s.get("theme") or "综合").strip()
        spirit = _derive_spirit(s, points, summary)
        policy_cut = _derive_policy_cut(s, points, theme)
        items.append(
            {
                "date": s.get("date", ""),
                "role": s.get("role", ""),
                "leader": s.get("leader", ""),
                "theme": theme,
                "headline": s.get("headline", "") or s.get("occasion", ""),
                "occasion": s.get("occasion", ""),
                "direction": f"{theme}｜{_activity_title(s)}",
                "points": points,
                "gold_quotes": gold,
                "keywords": kws,
                "channel": channel,
                "change_phrases": [p for p in change_phrases[:4] if p],
                "summary": summary,
                "spirit": spirit,
                "policy_cut": policy_cut,
                "url": _public_url(s.get("url", "")),
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


def _item_brief_line(it: dict, max_chars: int = 160) -> str:
    """单条动态完整摘要：优先 summary，其次要点；不加省略号。"""
    if it.get("summary"):
        body = _clean_space(it["summary"])
        # 摘要里保留领导名，读起来更清楚
        sent = _complete_sentences(body, max_chars)
        if sent:
            return sent
    for p in it.get("points") or []:
        p = _clean_space(p)
        if 8 <= len(p) <= max_chars:
            return p if p[-1] in "。．！？!?" else p + "。"
    for q in it.get("gold_quotes") or []:
        q = _display_phrase(q)
        if 4 <= len(q) <= MAX_QUOTE_LEN:
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
    forced = os.environ.get("CZ_BRIEF_PERIOD", "").strip()
    if forced in ("早报", "午报", "晚报"):
        return forced
    hour = datetime.datetime.now().hour
    if hour < 12:
        return "早报"
    if hour < 18:
        return "午报"
    return "晚报"


def _issue_no(ymd: str, period: str) -> str:
    """按报告日生成期号：〔2026〕第212期·早

    以年日内序为主；早/晚区分同日两报，便于归档检索。
    """
    try:
        y, m, d = map(int, ymd.split("-"))
        doy = datetime.date(y, m, d).timetuple().tm_yday
    except Exception:
        y = datetime.date.today().year
        doy = datetime.date.today().timetuple().tm_yday
    tag = {"早报": "早", "午报": "午", "晚报": "晚"}.get(period, "刊")
    return f"〔{y}〕第{doy}期·{tag}"


def _brief_heading(maxd: str) -> Tuple[str, str, str]:
    """返回 (卡片标题, 正文刊头, 期号)。"""
    period = _period_label()
    issue = _issue_no(maxd, period)
    date_cn = _ymd_cn(maxd)
    card_title = f"参政议政动态简报{issue}（{period}）"
    body_head = f"**参政议政动态简报{issue}（{period}）**"
    return card_title, body_head, issue


def _item_title(it: dict) -> str:
    """优先完整报道标题，避免只剩「调研」二字。"""
    for key in ("headline", "occasion"):
        t = _clean_space(it.get(key) or "")
        if t and len(t) >= 6:
            return t
    direction = it.get("direction") or ""
    title = direction.split("｜")[-1] if "｜" in direction else direction
    title = _clean_space(title)
    return title or (it.get("theme") or "公开活动")


def _append_item_detail(
    lines: List[str],
    it: dict,
    *,
    idx: int,
    brief_max: int,
    n_points: int,
    n_quotes: int,
) -> None:
    """单条：活动 + 精神要旨 + 要点 + 金句 + 参政议政切口（不贴 sohu 链）。"""
    theme = it.get("theme") or "综合"
    who = (it.get("leader") or "").strip()
    title = _item_title(it)
    head = f"{idx}. 【{theme}】"
    if who:
        head += f"{who} · "
    head += title
    lines.append(head)

    spirit = _clean_space(it.get("spirit") or "")
    if spirit:
        lines.append(f"精神要旨：{spirit}")

    body = _item_brief_line(it, max_chars=brief_max)
    if body:
        lines.append(f"活动概要：{body}")

    points = [p for p in (it.get("points") or []) if _clean_space(p)][:n_points]
    if points:
        lines.append("部署要点：")
        for p in points:
            p = _clean_space(p)
            if p[-1] not in "。．！？!?":
                p += "。"
            lines.append(f"· {p}")

    raw_q = list(it.get("gold_quotes") or []) + list(it.get("phrases") or [])
    raw_q = [q for q in raw_q if not _is_scene_sentence(q)]
    quotes = _filter_full_phrases(raw_q, MAX_QUOTE_LEN, n_quotes)
    if len(quotes) < n_quotes:
        for p in points:
            q = _display_phrase(p)
            if _is_scene_sentence(q):
                continue
            if 6 <= len(q) <= MAX_QUOTE_LEN and _normalize_phrase(q) not in {
                _normalize_phrase(x) for x in quotes
            }:
                quotes.append(q)
            if len(quotes) >= n_quotes:
                break
    if quotes:
        lines.append("重要表述：")
        for q in quotes:
            lines.append(f"· {q}")

    cut = _clean_space(it.get("policy_cut") or "")
    if cut:
        lines.append(f"参政议政切口：{cut if cut.endswith('。') else cut + '。'}")

    url = _public_url(it.get("url") or "")
    if url:
        lines.append(f"原文：{url}")
    lines.append("")


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
    omit_if_empty: bool = False,
    focus_lines: Optional[List[str]] = None,
) -> None:
    """参政议政体：关键词 → 重要表述 → 关注点变化 → 精神与活动 → 建言切口。"""
    if not items and omit_if_empty:
        return

    lines.append(f"**{section_no}、{level_name}**")
    if date_note:
        lines.append(f"（信号日期：{date_note}）")
    lines.append("")

    if not items:
        lines.append(empty_note)
        fl_empty = [x for x in (focus_lines or []) if x]
        if fl_empty:
            lines.append("")
            lines.append("（关注点与近七日主轴）")
            for i, c in enumerate(fl_empty[:4], 1):
                lines.append(f"{i}. {c}")
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

    # 关注点变化（相对近七日）优先于零散「新提法」列表
    fl = [x for x in (focus_lines or []) if x] or [
        c if c.endswith("。") else f"{c}。" for c in chg_show
    ]
    lines.append("（三）关注点与信号变化")
    if fl:
        for i, c in enumerate(fl[:4], 1):
            lines.append(f"{i}. {c}")
    else:
        lines.append("1. 相对近七日，主轴延续，暂未识别显著转向。")
    lines.append("")

    lines.append("（四）精神要旨与活动要情")
    lines.append("")
    news = items[:n_news]
    for i, it in enumerate(news, 1):
        _append_item_detail(
            lines, it, idx=i, brief_max=brief_max, n_points=MAX_POINTS_PER_ITEM, n_quotes=2
        )


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
    sh_focus_lines: Optional[List[str]] = None,
    central_focus_lines: Optional[List[str]] = None,
) -> str:
    """参政议政动态简报：精神/关注点变化/建言切口；不贴 sohu 链。"""

    period = _period_label()
    date_cn = _ymd_cn(maxd)
    c_date_cn = _ymd_cn(central_date) if central_date else ""
    _, _, issue = _brief_heading(maxd)

    c_items = central_items if (central_items and central_date == maxd) else []
    c_signals = central_signals if c_items else {}

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
            f"期号：{issue}",
            f"报告日期：{date_cn}",
            f"刊次：{period}",
            "",
            "**一、要情导读**",
            overview,
            "",
        ]
        sec_sh = "二"
        sec_c = "三"
        _append_level_block(
            lines,
            section_no=sec_sh,
            level_name="上海层面（当日新增）",
            signals=sh_signals if sh_items else {},
            items=sh_items,
            empty_note="本期上海层面无新增公开活动。",
            date_note="",
            n_kw=n_skw,
            n_q=n_sq,
            n_chg=n_schg,
            n_news=n_snews,
            brief_max=brief_max,
            focus_lines=sh_focus_lines,
        )
        if c_items:
            _append_level_block(
                lines,
                section_no=sec_c,
                level_name="中央层面（当日新增）",
                signals=c_signals,
                items=c_items,
                empty_note="",
                date_note=c_date_cn,
                n_kw=n_ckw,
                n_q=n_cq,
                n_chg=n_cchg,
                n_news=n_cnews,
                brief_max=brief_max,
                focus_lines=central_focus_lines,
            )
        else:
            lines.append(f"**{sec_c}、中央层面**")
            lines.append("本期无新增。")
            lines.append("")

        lines.append("**【编校说明】**")
        lines.append(
            "本文稿服务民盟参政议政研判，提炼公开活动精神与关注点变化及建言切口；"
            "具体表述以权威原文为准。"
        )
        if site:
            lines.append(f"专栏网页：{site}")
        return "\n".join(lines).strip()

    plans = [
        (4, 3, 2, 6, 4, 2, MAX_CENTRAL_NEWS, MAX_SH_NEWS, 220),
        (3, 2, 1, 5, 3, 2, 2, 4, 180),
        (2, 2, 1, 4, 2, 1, 1, 3, 150),
        (2, 1, 0, 3, 2, 1, 1, 2, 120),
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
    sh_focus_lines: Optional[List[str]] = None,
    central_focus_lines: Optional[List[str]] = None,
) -> str:
    period = _period_label()
    issue = _issue_no(maxd, period)
    c_items = central_items if (central_items and central_date == maxd) else []
    lines = [
        f"# 参政议政动态简报{issue}（{period}）",
        "",
        f"**期号：** {issue}",
        f"**报告日期：** {_ymd_cn(maxd)}",
        f"**刊次：** {period}",
        "",
        "## 一、要情导读",
        "",
        overview,
        "",
        "## 二、上海层面（当日新增）",
        "",
    ]
    kws = sh_signals.get("keywords") or []
    quotes = sh_signals.get("gold_quotes") or []
    if kws:
        lines.append("### （一）关键词")
        lines.append("、".join(kws) + "。")
        lines.append("")
    if quotes:
        lines.append("### （二）重要表述")
        for i, q in enumerate(quotes, 1):
            lines.append(f"{i}. {q}")
        lines.append("")
    lines.append("### （三）关注点与信号变化")
    fl = sh_focus_lines or []
    if fl:
        for c in fl:
            lines.append(f"- {c}")
    else:
        lines.append("- 相对近七日，主轴延续，暂未识别显著转向。")
    lines.append("")
    lines.append("### （四）精神要旨与活动要情")
    lines.append("")
    if sh_items:
        for n, i in enumerate(sh_items, 1):
            _append_item_detail(
                lines, i, idx=n, brief_max=220, n_points=MAX_POINTS_PER_ITEM, n_quotes=3
            )
    else:
        lines.append("本期上海层面无新增公开活动。")
        lines.append("")

    lines.append("## 三、中央层面（当日新增）")
    lines.append("")
    if c_items:
        if central_date:
            lines.append(f"信号日期：{_ymd_cn(central_date)}")
            lines.append("")
        ck = central_signals.get("keywords") or []
        cq = central_signals.get("gold_quotes") or []
        if ck:
            lines.append("### （一）关键词")
            lines.append("、".join(ck) + "。")
            lines.append("")
        if cq:
            lines.append("### （二）重要表述")
            for i, q in enumerate(cq, 1):
                lines.append(f"{i}. {q}")
            lines.append("")
        lines.append("### （三）关注点与信号变化")
        for c in (central_focus_lines or []) or ["相对近七日，主轴延续。"]:
            lines.append(f"- {c}")
        lines.append("")
        lines.append("### （四）精神要旨与活动要情")
        lines.append("")
        for n, i in enumerate(c_items, 1):
            _append_item_detail(
                lines, i, idx=n, brief_max=220, n_points=MAX_POINTS_PER_ITEM, n_quotes=3
            )
    else:
        lines.append("本期无新增。")
        lines.append("")

    lines.append("## 编校说明")
    lines.append("")
    lines.append(
        "本文稿服务民盟参政议政研判，提炼公开活动精神、关注点变化与建言切口；"
        "具体表述与政策口径以权威原文为准。"
    )
    lines.append("")
    lines.append(f"专栏网页：{site}")
    return "\n".join(lines)


def _push_feishu(title: str, push_body: str, has_signal: bool) -> None:
    if os.environ.get("SKIP_FEISHU", "").strip() in ("1", "true", "yes"):
        print("brief: 已跳过飞书推送（SKIP_FEISHU=1）")
        return
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
    if os.environ.get("SKIP_FEISHU", "").strip() in ("1", "true", "yes"):
        return
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

    # 报告日：默认日历今天（可用 CZ_BRIEF_REPORT_DATE 覆盖）
    # 避免「数据最新日停在几天前」时反复把旧日内容当今日日报重推
    data_max = max(
        ([s["date"] for s in sh_data] or ["1970-01-01"])
        + ([s["date"] for s in central_data] or ["1970-01-01"])
    )
    report_date = (os.environ.get("CZ_BRIEF_REPORT_DATE") or "").strip()
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", report_date or ""):
        report_date = datetime.date.today().isoformat()
    maxd = report_date

    chrono = _load_phrase_counts()
    hist_all = _build_history_phrase_index(sh_data + central_data, maxd)

    # —— 上海 / 中央：仅报告日当日新增（不回填旧日活动当今日）——
    sh_raw = [s for s in sh_data if s["date"] == maxd]
    sh_raw.sort(
        key=lambda s: (s.get("role_rank", 9), s.get("leader", ""), s.get("headline", ""))
    )
    sh_items = build_structured_items(sh_raw, hist=hist_all, chrono=chrono)

    central_date = ""
    central_raw: List[dict] = [s for s in central_data if s["date"] == maxd]
    if central_raw:
        central_date = maxd
    lookback = int(os.environ.get("CENTRAL_LOOKBACK_DAYS", str(CENTRAL_LOOKBACK_DAYS)))
    if not central_raw and lookback > 0:
        since_c = dminus(maxd, lookback)
        cand_dates = sorted(
            {s["date"] for s in central_data if since_c <= s["date"] <= maxd},
            reverse=True,
        )
        if cand_dates:
            central_date = cand_dates[0]
            central_raw = [s for s in central_data if s["date"] == central_date]
    central_raw.sort(key=lambda s: (s.get("headline", "")))
    central_items = build_structured_items(central_raw, hist=hist_all, chrono=chrono)
    if central_date and central_date != maxd and lookback <= 0:
        central_items = []
        central_date = ""
        central_raw = []

    since = dminus(maxd, 6)
    week_sh = [s for s in sh_data if since <= s["date"] <= maxd]
    week_central = [s for s in central_data if since <= s["date"] <= maxd]
    week_phrases = sum(len(np_of(s)) for s in week_sh)
    theme_cnt: Dict[str, int] = {}
    for s in week_sh:
        t = s.get("theme")
        if t and len(str(t)) <= 12:
            theme_cnt[t] = theme_cnt.get(t, 0) + 1
    hot = sorted(theme_cnt.items(), key=lambda x: -x[1])[:3]
    hot_txt = "、".join(f"{t}（{n}）" for t, n in hot) if hot else "暂无突出主题"

    sh_signals = build_day_signals(sh_items, week_sh, hist=hist_all, chrono=chrono)
    central_signals = build_day_signals(
        central_items, week_central, hist=hist_all, chrono=chrono
    )
    # 有今日新增：对比近七日变化；无今日新增：仍给近七日主轴，供日更“必有一报”
    if sh_items:
        sh_focus_lines = _focus_shift_lines(sh_items, week_sh, maxd=maxd)
    else:
        sh_focus_lines = [
            f"今日上海书记/市长公开通道无新增调研讲话通稿；数据最新日 {data_max}。",
            f"近七日上海主题主轴：{hot_txt}。",
        ]
        # 点出近七日最新一条的精神，避免“空报”
        latest_week = sorted(week_sh, key=lambda x: x.get("date", ""), reverse=True)
        if latest_week:
            lw = latest_week[0]
            sh_focus_lines.append(
                f"最近一次公开活动（{lw.get('date')} {lw.get('leader') or ''}）："
                f"{(lw.get('headline') or lw.get('occasion') or '')[:48]}，"
                f"主题{(lw.get('theme') or '综合')}。"
            )
    central_focus_lines = (
        _focus_shift_lines(central_items, week_central, maxd=maxd)
        if central_items
        else []
    )

    overview = _day_overview_dual(
        maxd,
        central_items=central_items,
        central_date=central_date,
        sh_items=sh_items,
    )
    if not sh_items and not central_items:
        overview = (
            f"{_ymd_cn(maxd)}中央与上海公开通道均无新增调研/讲话通稿。"
            f"近七日上海主轴为{hot_txt}，请持续跟踪书记市长与中央公开信号落地节奏。"
        )
    summary = overview
    if sh_focus_lines:
        summary += " " + sh_focus_lines[0]
    if sh_signals.get("gold_quotes"):
        summary += f" 重要表述：{'；'.join(sh_signals['gold_quotes'][:2])}。"

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
        sh_focus_lines=sh_focus_lines,
        central_focus_lines=central_focus_lines,
    )
    title, _, issue = _brief_heading(maxd)

    brief = {
        "date": maxd,
        "data_max_date": data_max,
        "issue": issue,
        "period": _period_label(),
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
        sh_focus_lines=sh_focus_lines,
        central_focus_lines=central_focus_lines,
    )
    open(os.path.join(BRIEF_DIR, f"{maxd}.md"), "w", encoding="utf-8").write(md_text)

    # 每日平台：有新增必推；无新增在 FEISHU_PUSH_ALWAYS=1（日更默认）时仍推
    has_signal = bool(sh_items or central_items)
    _push_feishu(title, push_body, has_signal)
    _push_legacy_webhook(md_text, has_signal)

    if "…" in push_body or "..." in push_body:
        print("brief: WARN 推送正文含省略号，请检查生成逻辑")
    if "sohu.com" in push_body.lower():
        print("brief: WARN 推送正文仍含 sohu 链接，应已屏蔽")
    print(f"brief: {overview}")
    print("--- push preview ---")
    print(push_body)
    print(
        f"--- chars: {len(push_body)} | ellipsis={('…' in push_body) or ('...' in push_body)} "
        f"| report={maxd} data_max={data_max} "
        f"| central={len(central_items)}@{central_date or '-'} | sh={len(sh_items)} ---"
    )


if __name__ == "__main__":
    main()
