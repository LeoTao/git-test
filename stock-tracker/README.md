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

## 财报季筛选三件套 🆕

财报季（1/4/7/10 月）全市场量化筛选，实现 `earnings-season-keyword-screening` skill 的完整漏斗：

```bash
# 1. 全A股"双40%"筛选（营收 & 净利增速 > 40%）
#    → 输出：行业聚类（板块效应）+ 完整池 CSV
python3 screen_h1.py                                  # 2026中报，双40%
python3 screen_h1.py --rev 30 --profit 50             # 自定义阈值
python3 screen_h1.py --date 20260630 --keep-bj        # 保留北交所

# 2. 关键词扫描（巨潮资讯全文检索，7 个关键表述 → 信号分）
#    → 自动与双40%池取交集
python3 keyword_scan.py
python3 keyword_scan.py --sdate 2026-10-01 --edate 2026-10-31   # 三季报季

# 3. 扣非校验（剔除靠一次性收益撑起来的高增长）
python3 kf_check.py out/keyword_cross_2026-07-01_2026-09-20.csv
```

**漏斗**：全A 11450 → 双40% 901 → 剔亏损/小规模 332 → 关键词命中 184 → 扣非达标 151

| 脚本 | 数据源 | 核心能力 |
|------|--------|---------|
| `screen_h1.py` | 东财业绩报表 | 全市场一次拉完；行业聚类看板块效应；标记"小基数"（利润增速>1000%）与现金流告警（现金比<0.3） |
| `keyword_scan.py` | 巨潮全文检索 | 15 个关键词跨全部公告检索；按公告类型分级（中报正文 > 业绩预告 > 调研纪要） |
| `kf_check.py` | 同花顺财务摘要 | 扣非增速 + 非经常性损益占比；判定 ✅真主营增长 / ⚠️非经常占比高 / ❌扣非大降 |

产出目录 `out/`，网络缓存 `.cache/`。

**注意**：增速是筛选器，绝对额才是判断依据。利润增速 >1000% 多为低基数幻觉；周期股（锂/钨/锡/航运）高增长来自价格而非份额，不属于成长股范畴。


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
├── screen_h1.py         财报季双40%全市场筛选 🆕
├── keyword_scan.py      巨潮全文检索关键词扫描 🆕
├── kf_check.py          扣非校验（剔除一次性收益）🆕
├── info_sources.json    海外信息源配置 🆕
├── discoveries_log.json 信号发现历史 🆕
├── watchlist.json       观察池（标的、信号、记录）
├── signals_log.json     信号录入历史
├── out/                 筛选产出 CSV 🆕
├── .cache/              网络请求缓存 🆕
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
