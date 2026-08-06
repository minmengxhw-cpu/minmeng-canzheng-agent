---
name: cz-agent-daily
description: >
  民盟参政议政雷达（minmeng-canzheng-agent）日更简报运维与排障 Skill。
  负责：每日 08:30 仅推一次外部飞书群、MiniMax 优先/Grok 回退分析流水线、政务体简报格式、
  禁止双推/旧群/晚报、检查 launchd 与 230035 发言权限。
  用户提到：简报、飞书推送、早报、晚报、漏发、发错群、定时任务、launchd、
  外部群、动向速递、CZ Agent、参政议政动态简报、检查今天有没有发、/cz-agent-daily
  时必须加载本 Skill 再改配置或发消息。
---

# CZ Agent 日更简报（强制约束）

**仓库：** `minmeng-canzheng-agent`  
**目标：** 每天 **只** 在 **08:30** 向 **指定外部飞书群** 推送 **一份** 政务体简报，由 **MiniMax 分析（额度不足回退 Grok Build）**，**绝不发错群、不双推、不发晚报**。

## 铁律（违反即错误）

1. **推送目标唯一**  
   - 只允许：`FEISHU_CHAT_ID=oc_381bea46653394d135daf14739524904`（外部群「CZ Agent 日更简报主动推送群」）  
   - **禁止**写回旧内部群 `oc_9334707219faab92091bdfb24344fa95`  
   - **禁止**启用 `FEISHU_WEBHOOK` / `BRIEF_WEBHOOK`（会双通道）  
   - **禁止**恢复公开入群二维码 / `FEISHU_JOIN_URL`（旧群入口已撤）

2. **频次唯一**  
   - launchd **仅** `08:30` 一次  
   - **禁止** `20:30` / `21:00` / `09:00` 再加定时  
   - 用户说「晚上也发」之前，默认不发晚报

3. **调用唯一**  
   - 推送只在 `update_all.py` → `gen_brief.py` **一次**  
   - **禁止**在 `update_and_push.sh` 末尾再调 `gen_brief.py`（历史双推根因）

4. **执行引擎**  
   - 分析主：`mmx` / MiniMax-M3（`MINIMAX_CLI`、`MINIMAX_MODEL`）  
   - 分析回退：`grok` / Grok Build（额度不足或失败时，`LLM_ENGINE=auto`）  
   - 简报拼装：`gen_brief.py` 规则政务体（无省略号）  
   - 模型均失败：中央/上海均可规则兜底，流水线仍须跑完并尽量推飞书

5. **改配置后必须**  
   ```bash
   bash scripts/install_launchd.sh
   ```
   并核对 plist 的 `StartCalendarInterval` 与 `FEISHU_CHAT_ID`。

## 路径与配置

| 用途 | 路径 |
|------|------|
| 开发仓库 | `/Users/cheer/Documents/mm agent/minmeng-canzheng-agent` |
| 运行副本 | `~/Library/Application Support/minmeng-canzheng-agent` |
| 密钥 | `~/Library/Application Support/minmeng-canzheng-agent/.env` |
| launchd | `~/Library/LaunchAgents/com.minmeng.canzheng-agent.plist` |
| 日志 | `~/Library/Application Support/minmeng-canzheng-agent/data/logs/launchd.{out,err}.log` |

`.env` 应类似：

```bash
FEISHU_CHAT_ID=oc_381bea46653394d135daf14739524904
FEISHU_SITE_URL=https://minmengxhw-cpu.github.io/minmeng-canzheng-agent/
# FEISHU_JOIN_URL=   # 禁用
# FEISHU_WEBHOOK=    # 禁用
```

## 日更流水线（勿改顺序）

```
launchd 08:30
  → update_and_push.sh
      → 校验 mmx（主）+ grok（回退）
      → git fetch + merge（失败则 hard reset origin/main，避免脏 scripts 漏发）
      → update_all.py
            → fetch_central.py   # MiniMax→Grok / 规则兜底
            → fetch_leaders.py   # MiniMax→Grok / 规则兜底
            → gen_brief.py       # 唯一推送点 → 外部群
            → gen_cuts.py
      → 有数据变化则 git commit/push
```

## 简报格式（定稿，服务参政议政）

- 标题：`参政议政动态简报〔YYYY〕第N期·早（早报）`
- 结构：一、要情导读 → 二、上海层面 → 三、中央层面 → 编校说明  
- 各层面：（一）关键词（二）重要表述（三）**关注点与信号变化**（四）**精神要旨与活动要情**（含参政议政切口）  
- **禁止**贴搜狐 sohu 等非权威转载链；**禁止**正文用 `…` 截断  
- 必须提炼：领导活动精神、关注点变化、民盟建言切口  
- 中央含：最高层考察、政治局会议、**党外人士座谈会**、总理考察（仅当日新增）  

## 健康检查（用户问「今天发了吗 / 确保正常」时必做）

运行：

```bash
bash "/Users/cheer/Documents/mm agent/minmeng-canzheng-agent/.grok/skills/cz-agent-daily/scripts/check_daily_health.sh"
```

或仓库内：

```bash
bash scripts/check_daily_health.sh
```

检查项：

1. plist 仅 08:30  
2. `FEISHU_CHAT_ID` 等于外部群  
3. 无 WEBHOOK  
4. 外部群 `moderation_setting=all_members`（否则 230035）  
5. 机器人在群内  
6. 今日 08:30 日志有开始与「已主动推送」  
7. `mmx` 或 `grok` 至少其一可执行

**任一失败 → 先修再推送，禁止盲目改 chat_id。**

## 常见故障与处理

### A. 230035 Send Message Permission deny

**原因：** 群「谁可以发言」= 仅群主（`moderation_setting=only_owner`），机器人在群但发不了。  

**处理（群主 user 身份）：**

```bash
CHAT=oc_381bea46653394d135daf14739524904
lark-cli im chat.moderation update --as user \
  --params "{\"chat_id\":\"$CHAT\"}" \
  --data '{"moderation_setting":"all_members"}'
lark-cli im +messages-send --as bot --chat-id "$CHAT" --text "连通测试"
```

### B. 230002 Bot not in chat

把应用机器人（App `cli_a911636338781bb6` / 名称「团宝」）重新加入外部群。

### C. 定时跑了但 exit 1、无推送

查 `launchd.err.log` 是否 `git merge` Aborting。  
`update_and_push.sh` 已在 merge 失败时 `reset --hard origin/main`；若仍失败，对齐运行副本后重装 launchd：

```bash
cd "/Users/cheer/Documents/mm agent/minmeng-canzheng-agent"
bash scripts/install_launchd.sh
```

### D. 双推两条相同消息

查是否 `update_and_push.sh` 又调用了第二次 `gen_brief.py` → **删掉第二次调用**。

### E. MiniMax 额度不足

自动回退 Grok Build；两者都失败则规则兜底。仍应生成简报并推送。可查 `mmx quota show` / `grok --version`。

### F. 漏发今日早报（补发）

仅在健康检查确认 chat_id 正确且发言权限正常后：

```bash
RUNTIME="$HOME/Library/Application Support/minmeng-canzheng-agent"
set -a; source "$RUNTIME/.env"; set +a
export FEISHU_CHAT_ID=oc_381bea46653394d135daf14739524904
unset FEISHU_WEBHOOK BRIEF_WEBHOOK
cd "$RUNTIME" && python3 scripts/gen_brief.py
```

## 改代码时的禁止清单

- 不要把推送目标改成 Webhook「顺便」双写  
- 不要恢复网站公开扫码入旧群  
- 不要为了「稳」加回 20:30  
- 不要在测试时默认推到旧群  
- 不要 `gen_brief` 调两次  

## 参考

- 详细清单：`references/ops-checklist.md`  
- 健康检查：`scripts/check_daily_health.sh`（仓库内 `scripts/check_daily_health.sh` 为软链或副本）
