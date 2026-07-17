# 民盟参政议政雷达

上海公开政务信息追踪与参政议政切口分析。

网站：<https://minmengxhw-cpu.github.io/minmeng-canzheng-agent/>

数据每天北京时间 **09:00** 和 **21:00** 自动更新（本机 launchd）。

## 飞书主动推送到手机

日更生成简报后，可**主动**发到飞书（手机 App 推送）。

### 1. 建机器人

1. 手机/电脑打开飞书 → 建一个群（可只拉自己）  
2. 群设置 → 群机器人 → 添加 **自定义机器人**  
3. 复制 **Webhook 地址**  
4. 建议开启 **签名校验**，并记下密钥  

### 2. 本机配置（不要提交 Git）

```bash
mkdir -p "$HOME/Library/Application Support/minmeng-canzheng-agent"
cat > "$HOME/Library/Application Support/minmeng-canzheng-agent/.env" <<'EOF'
FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/你的key
FEISHU_WEBHOOK_SECRET=你的签名密钥
# 可选：无新增也推送
# FEISHU_PUSH_ALWAYS=1
# FEISHU_SITE_URL=https://minmengxhw-cpu.github.io/minmeng-canzheng-agent/
EOF
chmod 600 "$HOME/Library/Application Support/minmeng-canzheng-agent/.env"
```

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
