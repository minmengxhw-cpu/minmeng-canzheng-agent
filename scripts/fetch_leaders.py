#!/usr/bin/env python3
"""市委主要领导讲话/活动抓取 + 入库分析（数据源：上海市政府官网、上观新闻）

同时抓取上海市政府官网和腾讯新闻「上观新闻」
（上报集团党媒号，补充书记/市长调研会见等时政信息）。

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
     旧条目原样保留，新增条目来自上海市政府官网和上观新闻。

运行：
  python3 scripts/fetch_leaders.py                  # 默认回溯最近 180 天
  SINCE=2026-01-01 MAX_PAGES=120 python3 scripts/fetch_leaders.py   # 指定回溯
  ONLY_SECRETARY=1 python3 scripts/fetch_leaders.py # 只抓市委书记
  mmx auth login
"""
from __future__ import annotations

import json
import hashlib
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

from minimax_cli import minimax_json

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

# 上观新闻（上报集团党媒号，补充书记/市长调研会见等时政信息）
# media_id=5004941，guestSuid 为列表接口所需的账号令牌
GUEST_SUID = os.environ.get("SH_GUEST_SUID", "8QMd3H1b7oIVvz7b")
LIST_API = "https://i.news.qq.com/getSubNewsMixedList"
DETAIL_RAIN = "https://news.qq.com/rain/a/"   # + <articleId>
SOURCE_NAME = "上观新闻"
OFFICIAL_SOURCE_NAME = "上海市人民政府"
SHIO_SOURCE_NAME = "上海市政府新闻办"
OFFICIAL_NEWS_URL = "https://www.shanghai.gov.cn/zzbshyw/index.html"
# 上海要闻列表分页（扩大搜集面，仍只走官方域名）
OFFICIAL_NEWS_PAGES = int(os.environ.get("OFFICIAL_NEWS_PAGES", "5"))
SHIO_PUSH_URL = "https://www.shio.gov.cn/TrueCMS/shxwbgs/ywts/ywts.html"
# 解放日报 / 上观新闻网（同一套 staticsg 数据；旧 journal/yaowen/list.json 已 404）
JFDAILY_SOURCE_NAME = "解放日报"
JFDAILY_DATA_BASE = "https://www.jfdaily.com/staticsg/data/"
JFDAILY_DETAIL_API = "https://www.jfdaily.com/news/getNewsDetail"
JFDAILY_HOME_LISTS = (
    "web/home/topnewslist.json",
    "web/home/morenewslist.json",
    "web/home/recommandnewslist.json",
    "web/home/quicknewslist.json",
    "web/home/bannernewslist.json",
)
# 上观新闻（shobserver）主站详情，作为腾讯号列表的补充发现通道
SHOBSERVER_SOURCE_NAME = "上观新闻网"
SHOBSERVER_DETAIL_URLS = (
    "https://www.shobserver.cn/news/detail?id={id}",
    "https://www.jfdaily.com/wx/detail.do?id={id}",
    "https://www.shobserver.com/wx/detail.do?id={id}",
)
# 搜狐「上观新闻」账号（转载及时，可发现官网/腾讯列表尚未露出的书记市长通稿）
SOHU_SHOBSERVER_MEDIA_ID = "121332532"
SOHU_SHOBSERVER_API = (
    "https://v2.sohu.com/author-page-api/author-articles/pc/"
    + SOHU_SHOBSERVER_MEDIA_ID
)
# 替代/补充：东方网上海频道（失败时静默跳过，不阻断主链路）
EASTDAY_SH_URLS = (
    "https://sh.eastday.com/",
)
EASTDAY_SOURCE_NAME = "东方网"
OFFICIAL_SEARCH_API = "https://search.sh.gov.cn/searchResult"
# 搜索关键词：姓名 + 职务/会议场景（适度扩面，不铺全站）
SEARCH_EXTRA_KEYWORDS = (
    "市委书记", "市长", "市委常委会", "市政府常务会议",
    "市委常委会会议", "市政府党组", "市推进",
)
OFFICIAL_SEARCH_FORM = {
    "pageSize": "20", "resourceType": "", "channel": "",
    "category1": "", "category2": "", "category3": "", "category4": "",
    "category6": "", "category7": "", "sortMode": "", "searchMode": "",
    "timeRange": "", "accurateMode": "", "district": "", "street": "",
    "stealthy": "0", "showItemAgency": "false",
}
_OFFICIAL_SESSION = requests.Session()
_OFFICIAL_SESSION_READY = False

# 领导活动动词门控：标题未署名时，只有命中这些词才下钻详情页
ACTIVITY_VERBS = [
    "主持", "出席", "调研", "会见", "座谈", "推进会", "动员", "部署",
    "考察", "督查", "检查", "慰问", "讲话", "会议", "强调", "指出",
    "走访", "现场办公", "接待", "看望", "宣讲", "专题", "工作要求",
    "传达", "研究", "审议", "听取", "审定", "签署", "举行", "参加",
    "发布", "通过", "批示", "专题会", "工作会", "现场会", "推进",
    "走访调研", "实地调研", "督导", "暗访", "反馈", "会见", "会谈",
    "开幕式", "闭幕式", "启动", "揭牌", "签约", "视察", "巡查",
]
RETROSPECTIVE_TITLE_MARKERS = ("理论亲声讲", "【上海一周】", "文汇讲堂")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

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
    """拉一页上观新闻列表（JSON）。offset_info 为上一页返回的游标，首页传空。"""
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
    """从列表接口 JSON 提取条目：标题 + 摘要 + URL + 日期。

    必须标记 source=上观新闻，否则主循环会误走官网正文解析，导致腾讯上观候选静默丢弃。
    """
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
        abstract = (
            (n.get("nlpAbstract") or "")
            + " "
            + (n.get("nlpContentAbstract") or "")
            + " "
            + (n.get("abstract") or "")
        )
        out.append({
            "date": t, "headline": title[:160], "id": aid,
            "url": n.get("url") or (DETAIL_RAIN + aid),
            "abstract": abstract.strip(),
            "source": SOURCE_NAME,
        })
    return out


def fetch_official_search_page(keyword: str, page_no: int) -> List[Dict]:
    """从上海市政府公开搜索结果读取上海要闻标题、日期、摘要和原文链接。"""
    form = dict(OFFICIAL_SEARCH_FORM)
    form.update({"text": keyword, "pageNo": str(page_no), "newsPageNo": str(page_no)})
    global _OFFICIAL_SESSION_READY
    try:
        if not _OFFICIAL_SESSION_READY:
            _OFFICIAL_SESSION.post(
                "https://search.sh.gov.cn/search", data={"text": keyword},
                headers=HEADERS, timeout=30,
            )
            _OFFICIAL_SESSION_READY = True
        r = _OFFICIAL_SESSION.post(
            OFFICIAL_SEARCH_API, data=form,
            headers={**HEADERS, "Referer": "https://www.shanghai.gov.cn/"},
            timeout=30,
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        log(f"    ✗ 上海市政府搜索失败（{keyword} 第{page_no}页）: {e}")
        return []

    out = []
    for item in soup.select("div.result.result-elm"):
        title_el = item.select_one("a.restitle")
        url_el = item.select_one("a.url")
        if not title_el or not url_el:
            continue
        title = re.sub(r"\s+", " ", title_el.get_text(" ", strip=True)).strip()
        url = (url_el.get("href") or "").strip()
        if url.startswith("http://"):
            url = "https://" + url[7:]
        content = item.select_one(".content")
        abstract = re.sub(r"\s+", " ", content.get_text(" ", strip=True)) if content else ""
        m = re.search(r"(20\d{2}-\d{2}-\d{2})", abstract)
        if not m or not url or not title:
            continue
        out.append({
            "date": m.group(1), "headline": title[:160],
            "id": "official-" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:20],
            "url": url, "abstract": abstract, "source": OFFICIAL_SOURCE_NAME,
        })
    return out


def _parse_official_news_soup(soup: BeautifulSoup, base_url: str) -> List[Dict]:
    out = []
    for item in soup.select("ul.news-green li"):
        link = item.select_one("a[href]")
        date_el = item.select_one(".data01")
        title_el = item.select_one(".aa")
        abstract_el = item.select_one(".bb")
        if not link or not date_el:
            continue
        title = (link.get("title") or (title_el.get_text(" ", strip=True) if title_el else "")).strip()
        date = date_el.get_text(" ", strip=True).replace(".", "-")
        url = urllib.parse.urljoin(base_url, link.get("href") or "").split("?", 1)[0]
        if not title or not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", date) or not url:
            continue
        out.append({
            "date": date, "headline": title[:160],
            "id": "official-list-" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:20],
            "url": url,
            "abstract": abstract_el.get_text(" ", strip=True) if abstract_el else "",
            "source": OFFICIAL_SOURCE_NAME,
        })
    return out


def fetch_official_news_list() -> List[Dict]:
    """直读“上海要闻”列表（多页），绕过搜索索引延迟。"""
    urls = [OFFICIAL_NEWS_URL]
    # index.html, index_2.html ... index_N.html
    for i in range(2, max(2, OFFICIAL_NEWS_PAGES) + 1):
        urls.append(OFFICIAL_NEWS_URL.replace("index.html", f"index_{i}.html"))
    out: List[Dict] = []
    seen = set()
    for page_url in urls:
        try:
            r = requests.get(page_url, headers=HEADERS, timeout=30)
            if r.status_code == 404:
                break
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            log(f"    ✗ 上海要闻直连失败 {page_url}: {e}")
            continue
        page_items = _parse_official_news_soup(soup, page_url)
        for it in page_items:
            if it["url"] in seen:
                continue
            seen.add(it["url"])
            out.append(it)
        if not page_items and page_url != OFFICIAL_NEWS_URL:
            break
    log(f"    上海要闻列表共 {len(out)} 条（约 {OFFICIAL_NEWS_PAGES} 页）")
    return out


def fetch_shio_push_list() -> List[Dict]:
    """直读市政府新闻办“要闻推送”，作为当天发布的官方快速信源。"""
    try:
        r = requests.get(SHIO_PUSH_URL, headers=HEADERS, timeout=30)
        r.raise_for_status()
        # 服务端常不声明 charset，requests 会误判 ISO-8859-1 导致标题乱码，姓名门控全部失效
        if not r.encoding or r.encoding.lower() in ("iso-8859-1", "latin-1"):
            r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        log(f"    ✗ 市政府新闻办直连失败: {e}")
        return []

    out = []
    for item in soup.select(".newslist #initData li"):
        link = item.select_one("a[href]")
        date_el = item.select_one("span")
        if not link or not date_el:
            continue
        title = (link.get("title") or link.get_text(" ", strip=True)).strip()
        date_match = re.search(r"20\d{2}-\d{2}-\d{2}", date_el.get_text(" ", strip=True))
        raw_href = (link.get("href") or "").replace("../..//", "../../")
        url = urllib.parse.urljoin(SHIO_PUSH_URL, raw_href)
        if not title or not date_match or not url:
            continue
        out.append({
            "date": date_match.group(0), "headline": title[:160],
            "id": "shio-" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:20],
            "url": url, "abstract": title, "source": SHIO_SOURCE_NAME,
        })
    return out


def _ms_to_date(ms) -> str:
    """毫秒时间戳 → YYYY-MM-DD；失败返回空串。"""
    try:
        ms = int(ms)
        if ms > 10_000_000_000:  # ms
            ms = ms / 1000.0
        return datetime.fromtimestamp(ms).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _flatten_jfdaily_payload(data) -> List[Dict]:
    """解放日报 home JSON 结构不统一：list / {top1,top2,list} / {list:[]}。"""
    rows: List[Dict] = []
    if isinstance(data, list):
        rows = [x for x in data if isinstance(x, dict)]
    elif isinstance(data, dict):
        if isinstance(data.get("list"), list):
            rows = [x for x in data["list"] if isinstance(x, dict)]
        else:
            for v in data.values():
                if isinstance(v, list):
                    rows.extend(x for x in v if isinstance(x, dict))
                elif isinstance(v, dict) and isinstance(v.get("list"), list):
                    rows.extend(x for x in v["list"] if isinstance(x, dict))
    return rows


def fetch_jfdaily_yaowen() -> List[Dict]:
    """解放日报/上观网首页静态 JSON 列表（替代已下线的 journal/yaowen/list.json）。

    列表：/staticsg/data/web/home/*.json
    正文：/news/getNewsDetail?id=...
    """
    out: List[Dict] = []
    seen = set()
    for rel in JFDAILY_HOME_LISTS:
        url = JFDAILY_DATA_BASE + rel
        try:
            r = requests.get(url, headers={**HEADERS, "Referer": "https://www.jfdaily.com/"}, timeout=25)
            r.raise_for_status()
            payload = r.json()
        except Exception as e:
            log(f"    ✗ 解放日报列表失败 {rel}: {e}")
            continue
        data = payload.get("data") if isinstance(payload, dict) else payload
        for row in _flatten_jfdaily_payload(data):
            nid = row.get("id") or row.get("articleid")
            title = str(row.get("title") or "").strip()
            if not nid or not title:
                continue
            date = _ms_to_date(row.get("publishtime") or row.get("addtime") or row.get("edittime"))
            if not date:
                continue
            detail_url = (
                f"https://www.jfdaily.com/staticsg/res/html/web/newsDetail.html"
                f"?id={nid}&sid=11"
            )
            key = str(nid)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "date": date,
                "headline": title[:160],
                "id": f"jfd-{nid}",
                "url": detail_url,
                "abstract": str(row.get("summary") or title)[:300],
                "source": JFDAILY_SOURCE_NAME,
                "jfd_nid": str(nid),
            })
    log(f"    解放日报/上观网列表 {len(out)} 条（{len(JFDAILY_HOME_LISTS)} 个首页接口）")
    return out


def fetch_jfdaily_detail(nid: str) -> str:
    """解放日报正文 API：返回纯文本。"""
    if not nid:
        return ""
    try:
        r = requests.get(
            JFDAILY_DETAIL_API,
            params={"id": nid, "ver": "1"},
            headers={**HEADERS, "Referer": "https://www.jfdaily.com/", "Accept": "application/json"},
            timeout=30,
        )
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        log(f"    ✗ 解放日报正文失败 id={nid}: {e}")
        return ""
    obj = payload.get("object") if isinstance(payload, dict) else None
    if not isinstance(obj, dict):
        return ""
    html = obj.get("detail") or obj.get("content") or obj.get("htmlcontent") or ""
    if not html:
        return str(obj.get("summary") or "")
    # 去 HTML 标签
    try:
        soup = BeautifulSoup(str(html), "html.parser")
        return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    except Exception:
        return re.sub(r"<[^>]+>", " ", str(html))


def _html_article_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for sel in (
        "article", ".article-content", ".detail-content", "#content",
        ".TRS_Editor", ".rich_media_content", "#mp-editor", ".text",
        ".article", ".content-article",
    ):
        el = soup.select_one(sel)
        if el:
            txt = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
            if len(txt) > 80:
                return txt
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))


def fetch_shobserver_html_detail(nid: str) -> str:
    """上观/解放日报 H5 详情页兜底（getNewsDetail 失败时）。"""
    if not nid:
        return ""
    for tmpl in SHOBSERVER_DETAIL_URLS:
        url = tmpl.format(id=nid)
        try:
            r = requests.get(url, headers=HEADERS, timeout=25)
            if r.status_code != 200 or len(r.text) < 200:
                continue
            if not r.encoding or r.encoding.lower() in ("iso-8859-1", "latin-1"):
                r.encoding = r.apparent_encoding or "utf-8"
            txt = _html_article_text(r.text)
            if len(txt) > 80 and any(n in txt for n in LEADERS):
                return txt
            if len(txt) > 200:
                return txt
        except Exception:
            continue
    return ""


def fetch_sohu_shobserver_media(limit: int = 60, max_pages: int = 5) -> List[Dict]:
    """从搜狐「上观新闻」账号 API 发现近期时政稿（补腾讯列表滞后/漏载）。

    API: v2.sohu.com/author-page-api/author-articles/pc/121332532
    仅收下标题署领导名或活动动词的条目；正文走搜狐页 / 上观 H5。
    """
    out: List[Dict] = []
    seen = set()
    pages = max(1, min(max_pages, int(os.environ.get("SOHU_SG_PAGES", str(max_pages)))))
    api_headers = {
        **HEADERS,
        "Accept": "application/json, text/plain, */*",
        "Referer": f"https://www.sohu.com/media/{SOHU_SHOBSERVER_MEDIA_ID}",
        "Origin": "https://www.sohu.com",
    }
    for pno in range(1, pages + 1):
        rows = []
        try:
            r = requests.get(
                SOHU_SHOBSERVER_API,
                params={"pNo": pno},
                headers=api_headers,
                timeout=25,
            )
            if r.status_code == 406:
                # 部分环境对 python-requests 指纹返回 406，改 curl 兼容拉 JSON
                import subprocess
                url = f"{SOHU_SHOBSERVER_API}?pNo={pno}"
                raw = subprocess.check_output(
                    ["curl", "-sS", "-m", "25", "-A", HEADERS["User-Agent"],
                     "-H", "Accept: application/json, text/plain, */*",
                     "-H", f"Referer: https://www.sohu.com/media/{SOHU_SHOBSERVER_MEDIA_ID}",
                     url],
                    text=True,
                )
                payload = json.loads(raw)
            else:
                r.raise_for_status()
                payload = r.json()
            rows = ((payload.get("data") or {}).get("pcArticleVOS") or []) if isinstance(payload, dict) else []
        except Exception as e:
            log(f"    ✗ 搜狐上观 API 失败 pNo={pno}: {e}")
            break
        if not rows:
            break
        for row in rows:
            title = re.sub(r"\s+", " ", str(row.get("title") or row.get("mobileTitle") or "").strip())
            if len(title) < 10:
                continue
            if not (title_named_leader(title) or title_is_activity(title)):
                continue
            sohu_id = str(row.get("id") or "")
            if not sohu_id or sohu_id in seen:
                continue
            seen.add(sohu_id)
            # publicTime: 2026-08-05T12:47:24.000+00:00
            pt = str(row.get("publicTime") or "")
            date = pt[:10] if re.match(r"20\d{2}-\d{2}-\d{2}", pt) else datetime.now().strftime("%Y-%m-%d")
            link = str(row.get("link") or f"www.sohu.com/a/{sohu_id}_{SOHU_SHOBSERVER_MEDIA_ID}")
            if not link.startswith("http"):
                link = "https://" + link.lstrip("/")
            brief = re.sub(r"\s+", " ", str(row.get("brief") or title))[:300]
            out.append({
                "date": date,
                "headline": title[:160],
                "id": f"sohu-sg-{sohu_id}",
                "url": link,
                "abstract": brief,
                "source": SHOBSERVER_SOURCE_NAME,
                "jfd_nid": "",
                "sohu_url": link,
            })
            if len(out) >= limit:
                log(f"    搜狐上观账号候选 {len(out)} 条（标题门控后，{pno} 页）")
                return out
    log(f"    搜狐上观账号候选 {len(out)} 条（标题门控后，{pages} 页）")
    return out


def fetch_sohu_article_text(url: str) -> str:
    """读取搜狐转载正文（最后兜底）。"""
    if not url:
        return ""
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        r.raise_for_status()
        if not r.encoding or r.encoding.lower() in ("iso-8859-1", "latin-1"):
            r.encoding = "utf-8"
        return _html_article_text(r.text)
    except Exception as e:
        log(f"    ✗ 搜狐正文失败: {e}")
        return ""


def fetch_eastday_sh_list() -> List[Dict]:
    """东方网上海频道 HTML 列表（解放日报不可用时的替代补充源）。"""
    out: List[Dict] = []
    seen = set()
    for page_url in EASTDAY_SH_URLS:
        try:
            r = requests.get(page_url, headers=HEADERS, timeout=25)
            r.raise_for_status()
            if not r.encoding or r.encoding.lower() in ("iso-8859-1", "latin-1"):
                r.encoding = r.apparent_encoding or "utf-8"
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            log(f"    ✗ 东方网列表失败 {page_url}: {e}")
            continue
        for a in soup.find_all("a", href=True):
            title = re.sub(r"\s+", " ", (a.get_text() or "").strip())
            href = a["href"].strip()
            if len(title) < 12 or not href:
                continue
            if not any(k in href for k in ("eastday.com", "/n/", "news")):
                continue
            if not href.startswith("http"):
                href = urllib.parse.urljoin(page_url, href)
            # 尽量从 URL 抽日期
            m = re.search(r"(20\d{2})[-/]?(\d{2})[-/]?(\d{2})", href)
            if m:
                date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            else:
                date = datetime.now().strftime("%Y-%m-%d")
            if href in seen:
                continue
            seen.add(href)
            # 先宽进：领导名或活动动词；下游 detail 再核验
            if not (title_named_leader(title) or title_is_activity(title)):
                continue
            out.append({
                "date": date,
                "headline": title[:160],
                "id": "ed-" + hashlib.sha1(href.encode("utf-8")).hexdigest()[:20],
                "url": href,
                "abstract": title,
                "source": EASTDAY_SOURCE_NAME,
            })
    log(f"    东方网上海相关候选 {len(out)} 条")
    return out


def title_named_leader(title: str) -> Optional[str]:
    for name in LEADERS:
        if name in title:
            return name
    return None


def title_is_activity(title: str) -> bool:
    return any(v in title for v in ACTIVITY_VERBS)


def report_key(item: Dict) -> tuple:
    title = re.sub(r"\s+", "", item.get("headline", ""))
    return item.get("date", ""), title


def report_source_priority(item: Dict) -> int:
    return {
        SHIO_SOURCE_NAME: 0,
        OFFICIAL_SOURCE_NAME: 1,
        JFDAILY_SOURCE_NAME: 2,
        SHOBSERVER_SOURCE_NAME: 3,
        EASTDAY_SOURCE_NAME: 4,
        SOURCE_NAME: 5,
    }.get(item.get("source", ""), 9)


def detail_has_leader(full_text: str) -> Optional[Dict]:
    if not full_text or len(full_text) < 50:
        return None
    for name, meta in LEADERS.items():  # 字典有序，书记在前 → 书记优先
        if name in full_text:
            return {"leader": name, **meta}
    return None


def looks_like_current_activity(full_text: str, published_date: str, headline: str = "") -> bool:
    """拦截用旧活动作背景的回顾稿，避免把历史提及当成当天行程。"""
    if any(marker in headline for marker in RETROSPECTIVE_TITLE_MARKERS):
        return False
    if title_named_leader(headline) or title_is_activity(headline):
        return True
    lead = (full_text or "")[:900]
    try:
        published_year = int((published_date or "")[:4])
    except ValueError:
        return True
    years = [int(x) for x in re.findall(r"(20\d{2})年", lead)]
    return not years or published_year in years or max(years) >= published_year


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


def fetch_official_detail(url: str) -> str:
    """读取上海市政府官网文章正文。"""
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        if not r.encoding or r.encoding.lower() in ("iso-8859-1", "latin-1"):
            r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        el = (soup.select_one("#ivs_content") or soup.select_one("#UCAP-CONTENT")
              or soup.select_one(".pages_content") or soup.select_one(".Article_content")
              or soup.select_one(".TRS_Editor") or soup.select_one(".trs_editor_view")
              or soup.select_one("article"))
        return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""
    except Exception as e:
        log(f"    ✗ 官网正文失败: {e}")
        return ""


def analyze_fallback(headline: str, date: str, leader: str, full_text: str) -> Dict:
    """MiniMax 额度/网络失败时的规则入库，避免候选全丢、报告日长期卡住。"""
    text = re.sub(r"\s+", " ", full_text or "")[:5000]
    sents = [s.strip() for s in re.split(r"[。！？]", text) if 10 <= len(s.strip()) <= 90]
    key_sents = [
        s for s in sents
        if any(k in s for k in ("强调", "指出", "要求", "希望", "部署", "审议", "主持", "调研"))
    ][:5]
    if not key_sents:
        key_sents = sents[:3]
    phrases = []
    for s in key_sents:
        m = re.search(r"(?:强调|指出|要求|希望)[，,:]?(.*)$", s)
        frag = (m.group(1) if m else s).strip(" ，,")
        frag = re.sub(rf"^(?:{re.escape(leader)}|他|她|其)(?:还)?", "", frag).strip(" ，,")
        if 6 <= len(frag) <= 40:
            phrases.append(frag)
    theme = _correct_theme("", headline, headline)
    # 再从正文前部做一次主题纠偏
    theme = _correct_theme(theme, headline, text[:400])
    summary = "。".join(key_sents[:3])
    if summary and not summary.endswith("。"):
        summary += "。"
    if not summary:
        summary = (headline or "")[:160]
    kws = []
    for w in ("人工智能", "新质生产力", "营商环境", "安全生产", "全面从严治党",
              "科技创新", "民生", "开放", "城市治理", "创智学院", "调研", "座谈会"):
        if w in headline or w in text[:900]:
            kws.append(w)
    if not kws:
        kws = [leader, "公开活动"]
    occasion = headline[:18] if headline else f"{leader}公开活动"
    for marker in ("调研", "座谈会", "常委会", "会见", "会议", "考察", "走访"):
        if marker in (headline or ""):
            occasion = f"{leader}{marker}"[:18]
            break
    return {
        "occasion": occasion,
        "summary": summary[:220],
        "key_points": key_sents[:4] or [headline],
        "new_phrasing": phrases[:4] or key_sents[:2],
        "theme": theme or "城市治理",
        "subthemes": kws[:3],
        "keywords": kws[:5],
        "policy_implications": "可对照本次公开要求，结合上海相关领域落实情况提出参政议政调研切口。",
        "_fallback": True,
    }


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
    for attempt in range(2):
        try:
            result = minimax_json(SYSTEM_PROMPT, user, max_tokens=1200, temperature=0.2)
            if result and (result.get("summary") or result.get("key_points")):
                return result
        except Exception as e:
            if attempt == 1:
                log(f"    ✗ 分析失败，改用规则兜底: {e}")
            else:
                time.sleep(3)
    log("  使用规则兜底摘要（保证入库）")
    return analyze_fallback(headline, date, leader, full_text)


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
    log(f"  源：上海要闻 + 市政府新闻办 + 市政府搜索 + 解放日报/上观网 + 东方网 + 上观腾讯号 · 回溯至 {since} · 最多 {MAX_PAGES} 翻页 · "
        f"领导 {list(LEADERS)} · ONLY_SECRETARY={ONLY_SECRETARY}")

    # 读历史（断点续抓）
    history: List[Dict] = []
    if OUT.exists():
        try:
            history = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            history = []
    history = [h for h in history if looks_like_current_activity(
        h.get("full_text", ""), h.get("date", ""), h.get("headline", "")
    )]
    history_urls = {h.get("url"): h for h in history if h.get("url")}
    results: List[Dict] = list(history)  # 以历史为基底，增量补充
    results_urls = set(history_urls)

    # 1. 先抓官方直连列表，避免搜索索引延迟；再用搜索和上观新闻补充
    candidates: List[Dict] = []
    seen = set()
    for item in (
        fetch_official_news_list()
        + fetch_shio_push_list()
        + fetch_jfdaily_yaowen()
        + fetch_eastday_sh_list()
        + fetch_sohu_shobserver_media()
    ):
        if item["date"] >= since and item["url"] not in seen:
            candidates.append(item)
            seen.add(item["url"])
    search_keywords = list(LEADERS.keys()) + list(SEARCH_EXTRA_KEYWORDS)
    search_pages = min(MAX_PAGES, int(os.environ.get("OFFICIAL_SEARCH_PAGES", "10")))
    for keyword in search_keywords:
        for page in range(1, search_pages + 1):
            for item in fetch_official_search_page(keyword, page):
                if item["date"] >= since and item["url"] not in seen:
                    candidates.append(item)
                    seen.add(item["url"])
            if candidates and all(x["date"] < since for x in candidates[-20:]):
                break
    official_count = sum(1 for c in candidates if c.get("source") != SOURCE_NAME)
    log(f"  上海官方/党媒源收集 {official_count} 条（搜索词 {len(search_keywords)} 个 × 最多 {search_pages} 页）")

    # 上观新闻混合流翻页收集补充候选（offsetInfo 游标翻页）
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
            if it["url"] in seen:
                continue
            seen.add(it["url"])
            if it["date"] < since:
                continue  # 早于截止日期，丢弃（但继续看本页其余）
            candidates.append(it)
            new_n += 1
        log(f"  · 第 {page} 翻页 {len(items)} 条（最早 {page_min}）→ 收 {new_n} 条候选")
        offset = urllib.parse.unquote_plus(data.get("offsetInfo", "") or "")
        if not data.get("hasNext") or page_min < since or not offset:
            log(f"  · 到底 / 越过截止日期 {since}，停止翻页")
            break

    guanzhi_count = len(candidates) - official_count
    log(f"  上观新闻收集 {guanzhi_count} 条")
    candidates.sort(key=lambda x: (report_source_priority(x), x.get("date", "")))
    deduped = []
    report_keys = set()
    for item in candidates:
        key = report_key(item)
        if key in report_keys:
            continue
        report_keys.add(key)
        deduped.append(item)
    candidates = deduped
    log(f"\n共收集 {len(candidates)} 条候选（≥{since}）→ 两阶段过滤 + 入库分析")

    # 2 + 3 + 4. 过滤 + 分析
    skip_existed = skip_norel = skip_noleader = analyzed = 0
    results_report_keys = {report_key(x) for x in results}
    for i, c in enumerate(candidates, 1):
        if ((c["url"] in results_urls and history_urls.get(c["url"], {}).get("summary"))
                or report_key(c) in results_report_keys):
            skip_existed += 1
            continue

        abstract = c.get("abstract", "")
        named = title_named_leader(c["headline"]) or title_named_leader(abstract)
        if not named and not title_is_activity(c["headline"]) and not title_is_activity(abstract):
            skip_norel += 1
            continue  # 标题/摘要既未署名也非领导活动 → 不下钻

        src = c.get("source") or ""
        url = c.get("url") or ""
        # 腾讯上观：必须走 rain 详情；兼容历史漏标 source 的候选
        if src == SOURCE_NAME or ("news.qq.com" in url or "inews.qq.com" in url or re.match(r"^\d{8}[A-Z0-9]+$", str(c.get("id") or ""))):
            full = fetch_detail(str(c.get("id") or ""))
        elif src in (JFDAILY_SOURCE_NAME, SHOBSERVER_SOURCE_NAME):
            nid = str(c.get("jfd_nid") or "").replace("jfd-", "") or str(c.get("id", "")).replace("jfd-", "")
            full = fetch_jfdaily_detail(nid) if nid and nid.isdigit() else ""
            if not full and nid:
                full = fetch_shobserver_html_detail(nid)
            if not full and c.get("sohu_url"):
                full = fetch_sohu_article_text(c["sohu_url"])
            if not full and url and "sohu.com" in url:
                full = fetch_sohu_article_text(url)
        elif src == EASTDAY_SOURCE_NAME:
            full = fetch_official_detail(c["url"])
            if not full:
                page = fetch(c["url"])
                if page:
                    soup = BeautifulSoup(page, "html.parser")
                    el = (soup.select_one("article") or soup.select_one(".article")
                          or soup.select_one("#content") or soup.select_one(".TRS_Editor"))
                    full = re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""
        else:
            full = fetch_official_detail(c["url"])
            # shio 等可能编码/选择器差异：HTML 兜底
            if not full and url:
                page = fetch(url)
                if page:
                    full = _html_article_text(page)
        if not full:
            continue
        if not looks_like_current_activity(full, c["date"], c["headline"]):
            skip_noleader += 1
            continue
        leader = detail_has_leader(full)
        if not leader:
            skip_noleader += 1
            continue

        analysis = analyze(c["headline"], c["date"], leader["leader"], full)
        if not analysis:
            continue
        src = c.get("source") or SOURCE_NAME
        if src in (OFFICIAL_SOURCE_NAME, SHIO_SOURCE_NAME):
            tier = "上海官方"
        else:
            tier = "主流党媒"  # 解放日报 / 上观 / 东方网 / 腾讯号
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
            "source": src, "url": c.get("url") or c.get("sohu_url") or "",
            "source_tier": tier,
            "verification_status": "原文已核验" + ("·规则摘要" if analysis.get("_fallback") else ""),
            "analyzed_at": datetime.now().isoformat(timespec="minutes"),
        }
        entry.update(detect_change(entry, results))
        results.append(entry)
        results_urls.add(c["url"])
        results_report_keys.add(report_key(c))
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
