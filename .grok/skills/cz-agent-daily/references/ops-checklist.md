# CZ Agent 日更运维清单

## 上海信源（轻量）

| 源 | 方式 | 备注 |
|----|------|------|
| 上海要闻 | HTML | 市政府官网 |
| 市政府新闻办 | HTML | 要闻推送 |
| 市政府搜索 | 搜索 API | 姓名+职务词 |
| 解放日报/上观网 | `staticsg/data/web/home/*.json` + `/news/getNewsDetail` | **勿用已 404 的 journal/yaowen/list.json** |
| 东方网上海 | HTML 尽力而为 | SSL/页面结构不稳时跳过 |
| 上观腾讯号 | QQ 新闻 JSON 列表 | 原有 |

## 正确终态

| 项 | 正确值 |
|----|--------|
| 推送时间 | 仅 08:30 |
| 推送群 | `oc_381bea46653394d135daf14739524904` |
| 群类型 | external=true |
| 发言权限 | all_members |
| 分析 | Grok Build grok / grok-4.5 |
| 简报入口 | update_all → gen_brief 仅一次 |
| 旧群 | 永不推送 |
| 公开二维码 | 禁用 |

## 历史事故（勿重演）

1. **双推**：`update_all` 与 `update_and_push` 各调一次 `gen_brief`  
2. **发错群**：`.env` 仍是旧 `oc_9334…` 或 Documents `.env` 覆盖  
3. **230035**：外部群改成仅群主发言  
4. **晚报漏发**：运行目录 scripts 脏导致 `git merge` Aborting 整链失败  
5. **企业内群外单位加不进**：`external=false`（已改外部群）

## 手动全量跑（排障）

```bash
cd "$HOME/Library/Application Support/minmeng-canzheng-agent"
set -a; source .env; set +a
export FEISHU_CHAT_ID=oc_381bea46653394d135daf14739524904
export GROK_CLI="$HOME/.grok/bin/grok"
export GROK_MODEL=grok-4.5
bash scripts/update_and_push.sh
```

## 日志关键字

- 成功：`已主动推送飞书` + `执行引擎: Grok Build`  
- 失败：`Aborting` / `230035` / `230002` / Grok CLI 超时或登录失效
