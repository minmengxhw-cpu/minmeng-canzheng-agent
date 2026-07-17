# 民盟参政议政雷达

上海公开政务信息追踪与参政议政切口分析。

网站：<https://minmengxhw-cpu.github.io/minmeng-canzheng-agent/>

数据每天北京时间 **09:00** 和 **21:00** 自动更新（本机 launchd）。

## 飞书主动推送到手机

日更生成简报后，可**主动**发到飞书（手机 App 推送）。

支持两条通道（二选一即可）：

| 通道 | 环境变量 | 说明 |
|------|----------|------|
| **应用机器人 + lark-cli**（推荐） | `FEISHU_CHAT_ID` | 用 `lark-cli` 建群/发消息，无需 Webhook |
| 自定义机器人 Webhook | `FEISHU_WEBHOOK` | 群里加「自定义机器人」后复制 Webhook |

### 1. 用 lark-cli 一键配置（推荐）

前提：本机 `lark-cli auth status` 里 **bot 身份 ready**。

```bash
# 建公开推送群（把你的 open_id 换成自己的；可用 lark-cli whoami 查看）
lark-cli im +chat-create --as bot \
  --name "民盟参政议政雷达推送" \
  --description "CZ Agent 日更简报主动推送群" \
  --type public \
  --users "ou_你的open_id" \
  --set-bot-manager

# 记下返回的 chat_id 与 share_link，写入本机 .env：
mkdir -p "$HOME/Library/Application Support/minmeng-canzheng-agent"
cat > "$HOME/Library/Application Support/minmeng-canzheng-agent/.env" <<'EOF'
FEISHU_CHAT_ID=oc_xxxxxxxx
FEISHU_JOIN_URL=https://applink.feishu.cn/client/chat/chatter/add_by_link?link_token=xxxx
# FEISHU_PUSH_ALWAYS=1
# FEISHU_SITE_URL=https://minmengxhw-cpu.github.io/minmeng-canzheng-agent/
EOF
chmod 600 "$HOME/Library/Application Support/minmeng-canzheng-agent/.env"
```

### 2. 或用自定义机器人 Webhook

1. 飞书建群 → 群机器人 → **自定义机器人** → 复制 Webhook  
2. 建议开启签名校验，密钥写入 `FEISHU_WEBHOOK_SECRET`  
3. `.env` 里配置 `FEISHU_WEBHOOK=...`（与 `FEISHU_CHAT_ID` 二选一即可；同时配时优先 Webhook）

### 3. 测试推送

```bash
cd "/path/to/minmeng-canzheng-agent"
set -a && source "$HOME/Library/Application Support/minmeng-canzheng-agent/.env" && set +a
python3 scripts/feishu_push.py --card --text "CZ Agent 飞书推送测试"
# 或整条简报链路：
python3 scripts/gen_brief.py
```

### 4. 装进定时任务

```bash
bash scripts/install_launchd.sh
```

之后每天 9 点 / 21 点：抓取 → 写数据 → **推飞书** → 有变化则 push 网站。

默认：**有当日新增信号才推**；要「无新闻也推一句」时设 `FEISHU_PUSH_ALWAYS=1`。

## 扫码订阅（给其他人）

别人扫网页上的二维码 → 加入飞书群 → 自动收到机器人推送。

### 管理员一次性配置

1. 已有推送群与 `FEISHU_CHAT_ID` / 或 Webhook  
2. 将建群返回的 `share_link`（或群设置 → 分享链接）写入 `FEISHU_JOIN_URL`  
3. 生成二维码并发布到网站：

```bash
cd "/path/to/minmeng-canzheng-agent"
set -a && source "$HOME/Library/Application Support/minmeng-canzheng-agent/.env" && set +a
python3 scripts/gen_feishu_join_qr.py
git add assets/feishu_join_qr.svg assets/feishu_join_qr.png data/feishu_join.json
git commit -m "docs: publish Feishu join QR for push subscribers"
git push origin main
```

4. 打开网站底部 **「扫码加入，主动收消息」**，用手机飞书扫码验证。

> 说明：二维码指向的是**群邀请链接**，不是 Webhook / chat_id 密钥。密钥只保存在本机 `.env`。
