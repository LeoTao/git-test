# 十倍股跟踪系统

> 基于"渗透率跟踪 + 信号评分 + 全球信息差套利"的十倍股研究辅助工具

## 快速开始

```bash
cd stock-tracker

# 每天早上第一步 —— 全球信息差扫描（发现一手海外信号）
python3 discovery.py scan

# 每天早上第二步 —— 5 分钟了解今天该关注什么
python3 tracker.py daily

# 发现新信号时 —— 录入到系统中
python3 tracker.py update

# 每周/每月跑一次 —— 查看完整评估和推荐
python3 tracker.py review

# 财报季（1/4/7/10月）—— 扫描财报关键词
python3 tracker.py earnings
```

## 命令一览

| 命令 | 用途 | 建议频率 |
|------|------|---------|
| `discovery.py scan` | 🌍 全球信息差扫描（RSS+关键词） | 每天 |
| `daily` | 每日检查清单 | 每天 |
| `update` | 录入新信号 | 发现信号时 |
| `review` | 完整评估报告 | 每周/每月 |
| `earnings` | 财报关键词扫描 | 财报季 |
| `add` | 添加新标的 | 需要时 |
| `edit` | 编辑/删除标的 | 需要时 |
| `discover` | tracker.py 内调用 discovery | 每天 |
| `discovery.py source` | 管理信息源 | 需要时 |

## 信号评分体系

每个信号有不同权重（1-5 分），得分越高表示正面信号越多：

| 分数 | 建议 |
|------|------|
| ≥ 20 且 S 级 | 🔥 强烈关注-买入候选 |
| ≥ 12 且 S 级 | ⭐ 重点研究-等待加仓 |
| ≥ 12（非 S 级） | 🔔 信号强烈-可考虑升至 S 级 |
| ≥ 6 | 📋 常规跟踪 |
| < 6 | ⏳ 等待信号 |

## 文件说明

```
stock-tracker/
├── tracker.py           主程序
├── discovery.py         全球信息差扫描工具 🆕
├── info_sources.json    海外信息源配置 🆕
├── discoveries_log.json 信号发现历史 🆕
├── watchlist.json       观察池（标的、信号、记录）
├── signals_log.json     信号录入历史
└── README.md            本文件
```

## 核心理念：三级信号发现体系

```
[第一级] 全球信息差套利（discovery.py）
  英文行业期刊 → RSS + 关键词扫描 → 提前 2-8 周发现信号
          ↓
[第二级] 财报季关键词筛选（tracker.py earnings）
  业绩大增 + "供不应求/高景气度"关键词 → 验证第一级信号
          ↓
[第三级] 成长股真伪鉴定（growth-stock-authentication skill）
  3-5 年增速趋势 + 三张表交叉验证 → 确认买入
```

详见 skill 文件：`.github/skills/global-information-arbitrage/SKILL.md`

## ⚠️ 免责声明

本工具仅用于辅助研究和跟踪，不构成任何投资建议。所有投资决策请基于您自己的独立研究和判断。
