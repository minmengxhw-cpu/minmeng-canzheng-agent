# 更新脚本

用于抓取中国政府网、上海市政府官网、上观新闻等公开信息，生成简报和候选切口。

## 日更怎么跑（由 MiniMax 执行）

本机 `launchd` 每天北京时间 **08:30 / 20:30** 调用 `scripts/update_and_push.sh`：

1. **MiniMax CLI（`mmx` · MiniMax-M3）** 分析中央 + 上海公开活动  
2. `gen_brief.py` 拼装政务体简报并推送到**外部飞书群**  
3. 有数据变化则 commit / push 到 GitHub Pages  

人工无需再点推送；保证 `mmx` 已登录且额度可用。

中央 / 最高层通道（`fetch_central.py`）轻量约束：
- 信源：中国政府网要闻 + 新华社时政列表 + 既有上海官方交叉验证
- 只收录总书记/政治局会议/总理考察/党外人士座谈会等最高层公开信号
- 每次新分析上限 `CENTRAL_MAX_ANALYZE`（默认 3）；MiniMax 额度不足时规则兜底摘要

所有新增内容统一由 MiniMax CLI（MiniMax-M3）分析，历史结果直接复用；定时任务不生成高消耗初稿。
