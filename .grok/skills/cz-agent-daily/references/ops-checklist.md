# CZ Agent 日更运维清单

## 正确终态

| 项 | 正确值 |
|----|--------|
| 推送时间 | 仅 08:30 |
| 推送群 | `oc_381bea46653394d135daf14739524904` |
| 群类型 | external=true |
| 发言权限 | all_members |
| 分析 | MiniMax mmx / MiniMax-M3 |
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
export MINIMAX_CLI=/opt/homebrew/bin/mmx
export MINIMAX_MODEL=MiniMax-M3
bash scripts/update_and_push.sh
```

## 日志关键字

- 成功：`已主动推送飞书` + `执行引擎: MiniMax`  
- 失败：`Aborting` / `230035` / `230002` / `Token Plan 用量上限`
