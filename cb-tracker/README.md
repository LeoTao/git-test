# 可转债安道全策略助手

> 每天一键：全市场筛选可买入的低价转债 + 自动监控持仓卖出信号

## 快速开始

```bash
cd cb-tracker

# 每日例行：市场温度 + 买入候选 + 持仓卖出信号
python3 cb_tracker.py daily

# 只看买入候选
python3 cb_tracker.py screen

# 只看持仓卖出信号
python3 cb_tracker.py monitor

# 记录买入（默认按最新价，可加 --price 105.3 指定）
python3 cb_tracker.py buy 113050

# 记录卖出（默认按最新价，也可手动指定）
python3 cb_tracker.py sell 113050 132.5

# 持仓总览（含盈亏）
python3 cb_tracker.py status

# 查看当前策略参数
python3 cb_tracker.py config
```

## 策略逻辑（安道全模式）

**买入**：低价 + 低溢价 + 排除雷区，按「双低值 = 现价 + 溢价率×100」升序排名。

| 参数（cb_config.json 可调） | 默认 | 含义 |
|---|---|---|
| max_price | 110 | 最高买入价 |
| max_premium | 30% | 最高转股溢价率 |
| min_rating | A- | 最低债券评级（排除 BBB+ 及以下） |
| min_scale_yi | 2 | 最小发行规模（亿），过滤流动性差的 |
| min_remain_years | 0.5 | 最短剩余年限 |
| min_ytm | -3% | 最低到期收益率（估算） |
| top_n | 15 | 输出前 N 名 |

**卖出**（监控持仓，逐只给出信号）：

- ✅ 现价 ≥ 130 强赎线 → 按纪律卖出
- 🚨 公司公告强赎 → 必须卖出/转股
- 🚨 价格 < 90 → 违约警戒线，核查正股
- 🚨 正股被 ST / 评级降至 A- 以下 / YTM > 8% → 信用风险警报
- 🔄 双低排名掉出全市场前 200 → 轮动提示

**择时**：不看大盘 K 线，看转债市场自身温度（全市场中位价）。
< 110 元是寒冬（大胆建仓），110~120 正常持有，> 120 只卖不买。

## 数据源（三层容灾，全部免登录）

1. **默认**：东方财富数据中心公开接口（服务器端合并实时行情，全市场 500+ 只）
2. **备用**：新浪批量实时报价（自算转股价值/溢价率），东方财富接口故障时自动切换
3. **兜底**：最近一次成功行情缓存（`cb_market_cache.json`），全部数据源不可用时告警提醒

**可选增强**：登录集思录 → F12 抓 cookie → 填入 `cb_config.json` 的
`data.jisilu_cookie`，自动切换为集思录全量数据（含精确双低、YTM、剩余规模）。

⚠️ 说明：
- YTM 为估算值（票面利率文本 + 到期赎回价 108 估算），仅用于过滤风险
- 强赎公告依赖基础信息表夜间更新，可能滞后；重要持仓建议在集思录人工核对
- 停牌债在持仓监控中会提示“无行情”，不会误报为已摘牌

## 文件说明

```
cb-tracker/
├── cb_tracker.py        主程序
├── cb_config.json       策略参数（可改）
├── cb_portfolio.json    持仓（自动维护）
├── cb_screen_log.json   每日筛选快照（最近 30 天）
├── cb_trade_log.json    买卖记录
├── cb_market_cache.json 行情缓存（兜底数据源）
└── README.md            本文件
```

## 定时自动运行（macOS）

交易日收盘后自动跑一次，crontab 示例（`crontab -e`）：

```
# 每周一至五 15:10（收盘后）运行
10 15 * * 1-5 cd /Users/taolei/Desktop/code/git-test/cb-tracker && /Users/taolei/Desktop/code/git-test/.venv/bin/python cb_tracker.py daily >> daily.log 2>&1
```

## ⚠️ 免责声明

本工具仅用于辅助研究和跟踪，不构成任何投资建议。
数据来自公开接口，可能有延迟或误差；「下有保底」只对不违约的债成立，
务必自行核查候选标的的信用风险。投资决策请基于您自己的独立研究。
