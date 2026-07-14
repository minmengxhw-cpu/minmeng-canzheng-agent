# 抓取脚本

## fetch_leaders.py

抓取市委主要领导（书记 / 市长）公开活动与讲话动向，输出 `data/leaders.json` 供前端加载。

### 安装

```bash
pip3 install requests beautifulsoup4
mmx auth login
```

### 运行

```bash
python3 scripts/fetch_leaders.py
```

输出：`data/leaders.json`

### 定时（建议）

旧版每 6 小时跑一次的示例：

```cron
0 */6 * * * cd /path/to/minmeng-canzheng-agent && python3 scripts/fetch_leaders.py >> /tmp/fetch_leaders.log 2>&1
```

### 抓取范围

| 源 | URL | 频率 |
|---|---|---|
| 上海市人民政府门户网站 | https://www.shanghai.gov.cn/ | 每日 |

后续可扩展：上观新闻、解放日报、市委各部公众号。

### 数据字段

输出 JSON 数组，每条字段：

```json
{
  "id": "ld-gov-1234",
  "date": "2026-06-02",
  "leader": "陈吉宁",
  "role": "市委书记",
  "role_rank": 1,
  "headline": "……",
  "summary": "……",
  "theme": "科技产业",
  "keywords": ["基础研究", "机制设计"],
  "source": "上海市人民政府门户网站",
  "url": "https://……",
  "change_note": "……（与历史对比的表述变化）",
  "compared_to": { "date": "2026-03-18", "headline": "……" }
}
```

### 前端接入

当前 `src/data.js` 内有 mock 数据。接入真实数据有两种方式：

**方式 A（最简）**：抓取后手工同步到 `src/data.js` 的 `leader_signals` 数组。

**方式 B（推荐）**：改 `src/app.js` 让 `renderLeaders` 先 fetch `/data/leaders.json`，失败则回退到 `DATA.leader_signals`。改造示意：

```js
async function loadLeaders() {
  try {
    const r = await fetch('./data/leaders.json', { cache: 'no-store' });
    if (r.ok) return await r.json();
  } catch (e) {}
  return DATA.leader_signals || [];
}
```

### 注意

- 仅采集公开发布内容，不做线索投稿
- 每条引用保留原始链接与发布日期，可追溯
- `change_note` 字段当前为占位（"待补充：……"），后续可接入辅助理解模型自动生成

## 自动更新

现在由 MiniMax CLI 负责抓取结果分析和三档初稿生成：

```bash
mmx auth login
python3 scripts/update_all.py --draft-limit 3
python3 scripts/update_all.py --skip-drafts
```

macOS 可安装每天 09:00 的定时任务：

```bash
bash scripts/install_launchd.sh
```

定时任务会抓取最近 6 页并用 MiniMax CLI 完成领导动向分析；为保证 09:00 主更新稳定，初稿不阻塞主任务。需要初稿时可手动运行 `python3 scripts/update_all.py --draft-limit 3`。
