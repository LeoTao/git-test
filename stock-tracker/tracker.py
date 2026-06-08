#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
十倍股跟踪系统 - 每日运行脚本
=================================
用法：
  python3 tracker.py daily      每日检查清单
  python3 tracker.py review     查看全部标的评估 & 推荐
  python3 tracker.py update     录入新信号
  python3 tracker.py earnings   财报季关键词扫描模式
  python3 tracker.py add        添加新标的到观察池
  python3 tracker.py help       查看帮助
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ============================================================
# 配置
# ============================================================
BASE_DIR = Path(__file__).parent
WATCHLIST_FILE = BASE_DIR / "watchlist.json"
SIGNALS_LOG_FILE = BASE_DIR / "signals_log.json"

# 赛道名称映射
SECTOR_NAMES = {
    "AI算力": "🤖 AI 算力",
    "新能源": "🔋 新能源",
    "新消费": "🛍️ 新消费",
    "6G/通信": "🛰️ 6G/通信",
    "半导体": "💻 半导体",
    "医疗器械": "🏥 医疗器械",
    "银发经济": "👴 银发经济",
    "机器人": "🦾 机器人",
    "基因治疗": "🧬 基因治疗",
    "商业航天": "🚀 商业航天",
    "海上风电": "🌊 海上风电",
    "创新药出海": "💊 创新药出海",
}

# 信号定义 + 权重
SIGNAL_WEIGHTS = {
    "提价能力": 3,
    "产能利用率>90%且扩产": 5,
    "连续3季营收加速": 5,
    "毛利率连续3季提升": 4,
    "行业出清/对手退出": 3,
    "大客户/大订单公告": 4,
    "海外收入占比>30%": 3,
    "渗透率突破关键值": 5,
    "供不应求(财报关键词)": 4,
    "超预期(财报关键词)": 3,
    "新产品/新市场突破": 4,
    "高管增持/回购": 2,
    "机构调研密度增加": 2,
    "技术突破/专利获批": 3,
}

# 财报季关键词
EARNINGS_KEYWORDS_POSITIVE = [
    "供不应求", "产能爬坡", "满产满销", "超预期",
    "高景气度", "供给偏紧", "需求旺盛", "订单饱满",
    "毛利率提升", "净利率提升", "费用率下降", "现金流改善",
    "研发投入", "技术突破", "产品迭代", "客户拓展",
    "海外市场", "新产能", "投产", "放量",
]

EARNINGS_KEYWORDS_NEGATIVE = [
    "需求疲软", "竞争加剧", "降价", "毛利率下滑",
    "库存高企", "应收账款", "商誉减值", "大股东减持",
]


# ============================================================
# 工具函数
# ============================================================

def load_watchlist():
    """加载观察池"""
    if not WATCHLIST_FILE.exists():
        print("❌ 观察池文件不存在，请先运行 init")
        sys.exit(1)
    with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_watchlist(data):
    """保存观察池"""
    data["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_signals_log():
    """加载信号历史"""
    if not SIGNALS_LOG_FILE.exists():
        return []
    with open(SIGNALS_LOG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_signals_log(log):
    """保存信号历史"""
    with open(SIGNALS_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def calc_score(company):
    """计算公司综合得分"""
    score = 0
    for signal in company.get("signals", []):
        weight = SIGNAL_WEIGHTS.get(signal, 2)
        score += weight
    return score


def get_recommendation(score, tier):
    """根据得分和等级给出建议"""
    if tier == "S" and score >= 20:
        return "🔥 强烈关注-买入候选", "green"
    elif tier == "S" and score >= 12:
        return "⭐ 重点研究-等待加仓信号", "green"
    elif score >= 20:
        return "🔔 信号强烈-可考虑升至S级", "yellow"
    elif score >= 12:
        return "👀 密切关注-信号积累中", "yellow"
    elif score >= 6:
        return "📋 常规跟踪", "white"
    else:
        return "⏳ 等待信号", "dim"


def get_market_state():
    """获取当前宏观环境状态"""
    data = load_watchlist()
    return data.get("market_state", {"status": "neutral", "reason": "未设置", "updated": "未知"})


def save_market_state(status, reason):
    """保存宏观环境状态"""
    data = load_watchlist()
    data["market_state"] = {
        "status": status,
        "reason": reason,
        "updated": datetime.now().strftime("%Y-%m-%d"),
    }
    save_watchlist(data)


def is_actionable(company, market_status):
    """
    判断一只股票是否处于"可入手"状态。
    规则：
      🟢 可入手：S级 + 得分≥12 + 宏观 favorable
      🟡 接近  ：S级 + 得分≥12 + 宏观 neutral，或 A级 + 得分≥12 + 宏观 favorable
      🔴 等待  ：其余
    """
    score = company.get("score", 0)
    tier = company.get("tier", "B")

    if tier == "S" and score >= 12 and market_status == "favorable":
        return "🟢 可入手"
    elif (tier == "S" and score >= 12 and market_status == "neutral") or \
         (tier == "A" and score >= 12 and market_status == "favorable"):
        return "🟡 接近"
    else:
        return "🔴 等待"


def format_date(date_str):
    """格式化日期"""
    if not date_str:
        return "未记录"
    return date_str


def days_since(date_str):
    """距离上次检查的天数"""
    if not date_str:
        return 999
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return (datetime.now() - d).days


def print_header(title):
    """打印标题"""
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)


def print_separator():
    print("-" * 60)


# ============================================================
# 每日检查清单
# ============================================================

def cmd_daily():
    """每日检查清单模式"""
    print_header(f"📋 十倍股每日检查 - {datetime.now().strftime('%Y-%m-%d %A')}")

    data = load_watchlist()
    companies = data["companies"]

    print()
    print("🔍 今天就做这 3 件事（5 分钟）：")
    print()

    # 1. 扫新闻
    print("  [1] 扫一眼重点赛道新闻（2 分钟）")
    sectors = list(set(c["sector"] for c in companies))
    for sec in sectors:
        display = SECTOR_NAMES.get(sec, sec)
        count = len([c for c in companies if c["sector"] == sec])
        print(f"      {display}（{count} 只标的）")
    print()

    # 2. 检查哪些公司该复查了
    print("  [2] 以下公司超过 7 天未复查：")
    overdue = [c for c in companies if days_since(c.get("last_review")) > 7]
    if overdue:
        for c in overdue:
            d = days_since(c.get("last_review"))
            print(f"      ⚠️  {c['name']} ({c['code']}) - {d} 天未复查 | 等级: {c['tier']} | 赛道: {c['sector']}")
    else:
        print("      ✅ 全部已近期复查，做得好！")
    print()

    # 3. 刻意观察提醒
    print("  [3] 刻意观察：今天逛街/刷视频时留意——")
    print("      有没有看到新的消费品牌突然到处都是？")
    print("      有没有哪个品类突然频繁出现在短视频里？")
    print("      身边的人最近花钱买了什么\"新东西\"？")
    print()

    # 统计概览
    print_separator()
    print("📊 当前观察池统计：")
    s_count = len([c for c in companies if c["tier"] == "S"])
    a_count = len([c for c in companies if c["tier"] == "A"])
    b_count = len([c for c in companies if c["tier"] == "B"])
    print(f"   S 级（重仓候选）: {s_count} 只")
    print(f"   A 级（密切跟踪）: {a_count} 只")
    print(f"   B 级（保持关注）: {b_count} 只")
    print(f"   总计: {len(companies)} 只")
    print(f"   上次更新: {data.get('last_updated', '未知')}")
    print()

    # 宏观状态
    ms = data.get("market_state", {})
    macro_emoji = {"favorable": "🟢", "neutral": "🟡", "unfavorable": "🔴"}
    print(f"🌍 宏观环境: {macro_emoji.get(ms.get('status','neutral'), '⚪')} {ms.get('status', 'unknown')}")
    print(f"   {ms.get('reason', '')}")

    print()
    print("💡 运行 'python3 tracker.py review' 查看完整评估")
    print("💡 运行 'python3 tracker.py update' 录入新发现信号")
    print("💡 运行 'python3 tracker.py macro' 更新宏观判断")


# ============================================================
# 完整评估视图
# ============================================================

def cmd_review():
    """完整评估视图——按推荐度排序"""
    print_header(f"🔬 十倍股跟踪评估报告 - {datetime.now().strftime('%Y-%m-%d')}")

    data = load_watchlist()
    companies = data["companies"]
    ms = data.get("market_state", {})
    macro_status = ms.get("status", "neutral")

    # 更新得分
    for c in companies:
        c["score"] = calc_score(c)

    # 宏观看板
    macro_emoji = {"favorable": "🟢", "neutral": "🟡", "unfavorable": "🔴"}
    print()
    print(f"  🌍 宏观环境: {macro_emoji.get(macro_status, '⚪')} {macro_status.upper()}")
    print(f"     {ms.get('reason', '')}")
    print(f"     ⚠️  仅当宏观=favorable时，🟢可入手信号才会亮起")

    # 按得分排序
    ranked = sorted(companies, key=lambda c: c["score"], reverse=True)

    # 按赛道分组展示
    print()
    print("📊 按赛道分类评估：")
    print()

    sectors_order = ["AI算力", "新能源", "新消费", "海上风电", "创新药出海", "6G/通信", "半导体", "医疗器械"]
    for sec in sectors_order:
        sec_companies = [c for c in ranked if c["sector"] == sec]
        if not sec_companies:
            continue
        display = SECTOR_NAMES.get(sec, sec)
        print(f"  {display}")
        print(f"  {'名称':<10} {'代码':<10} {'等级':<6} {'得分':<6} {'信号':<4} {'建议':<20} {'入手?'}")
        print(f"  {'-'*60}")
        for c in sec_companies:
            score = c["score"]
            signals_count = len(c.get("signals", []))
            rec, _ = get_recommendation(score, c["tier"])
            action = is_actionable(c, macro_status)
            print(f"  {c['name']:<10} {c['code']:<10} {c['tier']:<6} {score:<6} {signals_count:<4} {rec:<20} {action}")
        print()

    # 处理剩余赛道
    shown_sectors = set(sectors_order)
    for sec in set(c["sector"] for c in ranked):
        if sec in shown_sectors:
            continue
        sec_companies = [c for c in ranked if c["sector"] == sec]
        display = SECTOR_NAMES.get(sec, sec)
        print(f"  {display}")
        print(f"  {'名称':<10} {'代码':<10} {'等级':<6} {'得分':<6} {'信号':<4} {'建议':<20} {'入手?'}")
        print(f"  {'-'*60}")
        for c in sec_companies:
            score = c["score"]
            signals_count = len(c.get("signals", []))
            rec, _ = get_recommendation(score, c["tier"])
            action = is_actionable(c, macro_status)
            print(f"  {c['name']:<10} {c['code']:<10} {c['tier']:<6} {score:<6} {signals_count:<4} {rec:<20} {action}")
        print()

    # TOP 推荐
    print_separator()
    print("🏆 当前最值得重点研究的标的（得分 ≥ 12 或 S 级）：")
    print()
    top_picks = [c for c in ranked if c["score"] >= 12 or c["tier"] == "S"]
    if top_picks:
        for i, c in enumerate(top_picks, 1):
            score = c["score"]
            sigs = c.get("signals", [])
            action = is_actionable(c, macro_status)
            print(f"  [{i}] {c['name']} ({c['code']}) | {c['sector']} | 得分: {score} | {action}")
            print(f"      等级: {c['tier']} | 渗透率: {c['penetration_rate']} | 关键拐点: {c['key_trigger']}")
            if sigs:
                print(f"      已触发信号: {', '.join(sigs)}")
            if c.get("notes"):
                print(f"      备注: {c['notes']}")
            print()
    else:
        print("  （暂无——快去录入信号吧！运行 python3 tracker.py update）")

    # 可入手汇总
    print_separator()
    print("🎯 可入手判断（宏观 + 公司信号双层过滤）：")
    print()
    actionable_list = [c for c in ranked if is_actionable(c, macro_status) == "🟢 可入手"]
    approaching_list = [c for c in ranked if is_actionable(c, macro_status) == "🟡 接近"]
    if actionable_list:
        print("  🟢 可入手：")
        for c in actionable_list:
            print(f"     {c['name']} ({c['code']}) | {c['sector']} | 得分: {c['score']}")
    else:
        print(f"  🟢 可入手：0 只（需同时满足：S级 + 得分≥12 + 宏观=favorable）")
    print()
    if approaching_list:
        print("  🟡 接近（只差一个条件）：")
        for c in approaching_list:
            missing = []
            if c["tier"] != "S" or c["score"] < 12:
                missing.append("公司信号不够(S级+得分≥12)")
            if macro_status != "favorable" and macro_status != "neutral":
                missing.append("宏观=" + macro_status)
            elif macro_status == "neutral" and c["tier"] == "A":
                missing.append("等待宏观转favorable 或 升至S级")
            print(f"     {c['name']} ({c['code']}) | 差: {', '.join(missing)}")
    else:
        print("  🟡 接近：0 只")
    print(f"\n  💡 当前宏观={macro_status}，{'不适合入场' if macro_status == 'unfavorable' else '可选择性入场' if macro_status == 'favorable' else '谨慎入场'}")

    # 各赛道信号汇总
    print_separator()
    print("📈 各赛道信号密度：")
    for sec in sectors_order + [s for s in set(c["sector"] for c in ranked) if s not in sectors_order]:
        sec_companies = [c for c in ranked if c["sector"] == sec]
        if not sec_companies:
            continue
        total_signals = sum(len(c.get("signals", [])) for c in sec_companies)
        total_score = sum(c["score"] for c in sec_companies)
        display = SECTOR_NAMES.get(sec, sec)
        bar = "█" * min(total_signals, 20)
        print(f"  {display:<20} {bar}  {total_signals} 个信号, 总分 {total_score}")

    print()
    print(f"📅 报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"💡 运行 'python3 tracker.py update' 录入新信号")


# ============================================================
# 录入信号模式
# ============================================================

def cmd_update():
    """录入新信号"""
    print_header("✏️ 录入新信号")

    data = load_watchlist()
    companies = data["companies"]

    # 列出所有公司
    print()
    print("选择要更新信号的公司：")
    print()
    for i, c in enumerate(companies, 1):
        sigs = c.get("signals", [])
        sig_str = ", ".join(sigs) if sigs else "（无信号）"
        d = days_since(c.get("last_review"))
        day_str = f"{d}天前" if d < 999 else "从未"
        print(f"  [{i}] {c['name']} ({c['code']}) | {c['tier']}级 | {c['sector']}")
        print(f"      当前信号: {sig_str}")
        print(f"      上次复查: {day_str}")
        print()

    try:
        choice = input("输入公司编号（多个用逗号分隔，q 返回）: ").strip()
        if choice.lower() == 'q':
            return
        indices = [int(x.strip()) - 1 for x in choice.split(",")]
    except (ValueError, IndexError):
        print("❌ 输入无效")
        return

    # 列出信号清单
    signals_list = list(SIGNAL_WEIGHTS.keys())
    print()
    print("可录入的信号（输入编号，多个用逗号分隔）：")
    print()
    for i, (sig, weight) in enumerate(SIGNAL_WEIGHTS.items(), 1):
        print(f"  [{i}] {sig}  (权重: +{weight})")
    print("  [0] 自定义信号")
    print()

    try:
        sig_choice = input("选择信号编号: ").strip()
    except ValueError:
        print("❌ 输入无效")
        return

    # 处理自定义信号
    if sig_choice == "0":
        custom_sig = input("输入自定义信号描述: ").strip()
        custom_weight = input("输入权重 (1-5): ").strip()
        try:
            custom_weight = int(custom_weight)
        except ValueError:
            custom_weight = 2
        selected_signals = [custom_sig]
        SIGNAL_WEIGHTS[custom_sig] = custom_weight
    else:
        try:
            sig_indices = [int(x.strip()) - 1 for x in sig_choice.split(",")]
            selected_signals = [signals_list[i] for i in sig_indices if 0 <= i < len(signals_list)]
        except (ValueError, IndexError):
            print("❌ 输入无效")
            return

    if not selected_signals:
        print("❌ 未选择任何信号")
        return

    # 应用到选中的公司
    for idx in indices:
        c = companies[idx]
        for sig in selected_signals:
            if sig not in c["signals"]:
                c["signals"].append(sig)
        c["last_review"] = datetime.now().strftime("%Y-%m-%d")
        print(f"✅ {c['name']} 已更新: +{', '.join(selected_signals)}")

    # 记录到信号日志
    log = load_signals_log()
    log.append({
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "companies": [companies[idx]["name"] for idx in indices],
        "signals": selected_signals,
    })
    save_signals_log(log)

    save_watchlist(data)
    print()
    print("💾 已保存。运行 'python3 tracker.py review' 查看最新评估")


# ============================================================
# 财报季关键词扫描
# ============================================================

def cmd_earnings():
    """财报季关键词扫描——手动输入财报摘要，自动检测关键词"""
    print_header("📊 财报季关键词扫描")
    print()
    print("这是一个辅助工具：当你阅读完一份财报后，")
    print("粘贴财报中的\"经营情况讨论与分析\"部分的关键段落。")
    print("程序会自动检测其中的正面/负面关键词。")
    print()

    data = load_watchlist()
    companies = data["companies"]

    print("选择公司：")
    for i, c in enumerate(companies, 1):
        print(f"  [{i}] {c['name']} ({c['code']})")
    print()

    try:
        choice = int(input("输入公司编号: ").strip()) - 1
        c = companies[choice]
    except (ValueError, IndexError):
        print("❌ 输入无效")
        return

    print()
    print(f"📄 正在分析: {c['name']} ({c['code']})")
    print("请粘贴财报关键段落（输入完成后按 Ctrl+D / Cmd+D 结束）：")
    print()

    lines = []
    try:
        while True:
            line = input()
            lines.append(line)
    except EOFError:
        pass

    text = "".join(lines)

    if not text.strip():
        print("❌ 未输入任何内容")
        return

    # 检测关键词
    positive_hits = []
    negative_hits = []

    for kw in EARNINGS_KEYWORDS_POSITIVE:
        if kw in text:
            positive_hits.append(kw)

    for kw in EARNINGS_KEYWORDS_NEGATIVE:
        if kw in text:
            negative_hits.append(kw)

    print()
    print("=" * 60)
    print(f"📊 {c['name']} 财报关键词扫描结果：")
    print()

    if positive_hits:
        print(f"✅ 正面关键词 ({len(positive_hits)} 个):")
        for kw in positive_hits:
            weight_map = {
                "供不应求": 5, "满产满销": 5, "产能爬坡": 4, "超预期": 3,
                "高景气度": 3, "供给偏紧": 3, "需求旺盛": 3, "订单饱满": 3,
                "毛利率提升": 4, "净利率提升": 3, "费用率下降": 2,
                "研发投入": 2, "技术突破": 3, "产品迭代": 2,
                "海外市场": 3, "新产能": 3, "投产": 3, "放量": 3,
            }
            w = weight_map.get(kw, 2)
            bar = "█" * w
            print(f"   {bar} {kw} (信号强度: {w})")
    else:
        print("✅ 正面关键词: 未检测到")

    print()

    if negative_hits:
        print(f"⚠️  负面关键词 ({len(negative_hits)} 个):")
        for kw in negative_hits:
            print(f"   ❌ {kw}")
    else:
        print("⚠️  负面关键词: 未检测到（很好！）")

    # 自动建议信号
    print()
    print("💡 根据检测结果，建议添加以下信号：")

    suggested = []
    if "供不应求" in positive_hits or "满产满销" in positive_hits:
        suggested.append("供不应求(财报关键词)")
    if "超预期" in positive_hits:
        suggested.append("超预期(财报关键词)")
    if "产能爬坡" in positive_hits or "新产能" in positive_hits or "投产" in positive_hits:
        suggested.append("产能利用率>90%且扩产")
    if "毛利率提升" in positive_hits:
        suggested.append("毛利率连续3季提升")
    if "海外市场" in positive_hits:
        suggested.append("海外收入占比>30%")
    if "技术突破" in positive_hits:
        suggested.append("技术突破/专利获批")

    if suggested:
        for s in suggested:
            print(f"   + {s}")
        print()
        confirm = input("是否自动添加以上信号？(y/n): ").strip().lower()
        if confirm == 'y':
            for s in suggested:
                if s not in c["signals"]:
                    c["signals"].append(s)
            c["last_review"] = datetime.now().strftime("%Y-%m-%d")
            save_watchlist(data)
            print("✅ 信号已添加！")
    else:
        print("   （未检测到足够强的信号）")

    if negative_hits:
        print()
        print("⚠️  检测到负面关键词，建议：")
        if "需求疲软" in negative_hits:
            print("   - 关注下游需求是否出现拐点")
        if "竞争加剧" in negative_hits:
            print("   - 评估公司护城河是否受损")
        if "毛利率下滑" in negative_hits:
            print("   - 分析毛利率下滑是短期还是结构性")
        if "大股东减持" in negative_hits:
            print("   - 关注减持原因和比例")
        print("   - 考虑是否降低该公司跟踪等级")


# ============================================================
# 添加新标的
# ============================================================

def cmd_add():
    """添加新标的到观察池"""
    print_header("➕ 添加新标的")

    print()
    name = input("公司名称: ").strip()
    if not name:
        print("❌ 名称不能为空")
        return

    code = input("股票代码 (如 300750): ").strip()
    if not code:
        print("❌ 代码不能为空")
        return

    # 选择赛道
    print()
    print("选择赛道：")
    sector_keys = list(SECTOR_NAMES.keys())
    for i, (key, display) in enumerate(SECTOR_NAMES.items(), 1):
        print(f"  [{i}] {display}")
    print(f"  [{len(SECTOR_NAMES)+1}] 自定义")
    try:
        sec_choice = int(input("选择赛道编号: ").strip())
        if sec_choice == len(SECTOR_NAMES) + 1:
            sector = input("输入赛道名称: ").strip()
        else:
            sector = sector_keys[sec_choice - 1]
    except (ValueError, IndexError):
        print("❌ 输入无效")
        return

    # 等级
    print()
    tier = input("等级 (S=重仓候选, A=密切跟踪, B=保持关注, 默认 A): ").strip().upper()
    if tier not in ("S", "A", "B"):
        tier = "A"

    penetration = input("当前渗透率估算 (如 5%): ").strip()
    key_trigger = input("关键拐点信号: ").strip()
    notes = input("备注: ").strip()

    data = load_watchlist()

    # 生成新 ID
    existing_ids = [int(c["id"]) for c in data["companies"]]
    new_id = str(max(existing_ids) + 1) if existing_ids else "1"

    new_company = {
        "id": new_id,
        "name": name,
        "code": code,
        "sector": sector,
        "tier": tier,
        "penetration_rate": penetration,
        "key_trigger": key_trigger,
        "notes": notes,
        "signals": [],
        "last_review": datetime.now().strftime("%Y-%m-%d"),
        "score": 0,
    }

    data["companies"].append(new_company)
    save_watchlist(data)

    print()
    print(f"✅ {name} ({code}) 已加入观察池！")
    print(f"   赛道: {sector} | 等级: {tier} | 渗透率: {penetration}")


# ============================================================
# 删除/修改标的
# ============================================================

def cmd_edit():
    """编辑或删除标的"""
    print_header("🔧 编辑观察池")

    data = load_watchlist()
    companies = data["companies"]

    print()
    for i, c in enumerate(companies, 1):
        sigs_count = len(c.get("signals", []))
        print(f"  [{i}] {c['name']} ({c['code']}) | {c['tier']}级 | {c['sector']} | {sigs_count} 个信号")

    print()
    print("操作: [编号] 编辑  [d 编号] 删除  [q] 返回")
    cmd = input("> ").strip()

    if cmd.lower() == 'q':
        return

    if cmd.lower().startswith('d '):
        try:
            idx = int(cmd.split()[1]) - 1
            c = companies[idx]
            confirm = input(f"确认删除 {c['name']} ({c['code']})？(y/n): ").strip().lower()
            if confirm == 'y':
                removed = companies.pop(idx)
                save_watchlist(data)
                print(f"✅ 已删除 {removed['name']}")
        except (ValueError, IndexError):
            print("❌ 输入无效")
        return

    try:
        idx = int(cmd) - 1
        c = companies[idx]
        print(f"\n编辑 {c['name']} ({c['code']})（直接回车保留原值）:")
        new_tier = input(f"等级 [{c['tier']}]: ").strip().upper()
        if new_tier in ("S", "A", "B"):
            c["tier"] = new_tier
        new_pen = input(f"渗透率 [{c['penetration_rate']}]: ").strip()
        if new_pen:
            c["penetration_rate"] = new_pen
        new_trigger = input(f"关键拐点 [{c['key_trigger']}]: ").strip()
        if new_trigger:
            c["key_trigger"] = new_trigger
        new_notes = input(f"备注 [{c.get('notes', '')}]: ").strip()
        if new_notes:
            c["notes"] = new_notes
        save_watchlist(data)
        print(f"✅ {c['name']} 已更新")
    except (ValueError, IndexError):
        print("❌ 输入无效")


# ============================================================
# 宏观环境判断
# ============================================================

def cmd_macro():
    """更新宏观环境状态"""
    print_header("🌍 宏观环境判断")

    ms = get_market_state()
    macro_emoji = {"favorable": "🟢", "neutral": "🟡", "unfavorable": "🔴"}
    print()
    print(f"  当前状态: {macro_emoji.get(ms['status'], '⚪')} {ms['status']}")
    print(f"  理由: {ms.get('reason', '无')}")
    print(f"  更新时间: {ms.get('updated', '未知')}")
    print()
    print("  宏观状态说明：")
    print("    🟢 favorable   — 流动性宽松 + 市场上升趋势 + 风险偏好正常")
    print("    🟡 neutral     — 方向不明，谨慎参与")
    print("    🔴 unfavorable — 加息/冲突/恐慌，不适合买成长股")
    print()
    print("  选择新状态：")
    print("    [1] 🟢 favorable（适合入场）")
    print("    [2] 🟡 neutral（方向不明）")
    print("    [3] 🔴 unfavorable（不适合入场）")
    print()

    choice = input("  输入: ").strip()
    status_map = {"1": "favorable", "2": "neutral", "3": "unfavorable"}
    if choice not in status_map:
        print("❌ 无效选择")
        return

    new_status = status_map[choice]
    reason = input("  简要理由: ").strip()
    save_market_state(new_status, reason)

    print()
    print(f"✅ 宏观状态已更新为: {macro_emoji.get(new_status, '⚪')} {new_status}")
    print("💡 运行 'python3 tracker.py review' 查看最新的可入手判断")


# ============================================================
# 帮助
# ============================================================

def cmd_help():
    """显示帮助"""
    print_header("📖 十倍股跟踪系统 - 使用指南")

    print("""
命令列表：
  python3 tracker.py daily      每日检查清单（推荐每天跑）
  python3 tracker.py review     查看完整评估报告（含可入手判断）
  python3 tracker.py macro      更新宏观环境判断
  python3 tracker.py update     录入新发现的信号
  python3 tracker.py earnings   财报季关键词扫描
  python3 tracker.py add        添加新标的
  python3 tracker.py edit       编辑/删除观察池中的标的
  python3 tracker.py help       显示此帮助

使用流程：
  1. 每天早上跑 daily，了解今天该关注什么
  2. 每周跑 macro，更新宏观判断（利率/冲突/市场情绪）
  3. 发现信号后跑 update 录入
  4. 每周/每月跑 review 查看"可入手"标的
  5. 财报季（1/4/7/10月）跑 earnings 扫描财报

可入手判断规则（双层过滤）：
  🟢 可入手 = S级 + 得分≥12 + 宏观=favorable
  🟡 接近   = S级+得分≥12+宏观=neutral，或 A级+得分≥12+宏观=favorable
  🔴 等待   = 其余情况

  宏观状态用 'python3 tracker.py macro' 手动判断并设置。

信号评分体系：
  每个信号有不同权重（1-5分），得分越高表示越多正面信号叠加。
  综合得分 ≥ 20 且 S 级 → 强烈买入候选
  综合得分 ≥ 12 且 S 级 → 重点研究，等待加仓
  综合得分 ≥ 12（非 S 级）→ 可考虑升至 S 级
  综合得分 ≥ 6 → 常规跟踪
  综合得分 < 6 → 等待信号

文件说明：
  watchlist.json     观察池数据（标的、信号、记录）
  signals_log.json   信号录入历史

⚠️ 免责声明：
  本工具仅用于辅助研究和跟踪，不构成任何投资建议。
  所有投资决策请基于您自己的独立研究和判断。
""")


# ============================================================
# 主入口
# ============================================================

def main():
    commands = {
        "daily": cmd_daily,
        "review": cmd_review,
        "macro": cmd_macro,
        "update": cmd_update,
        "earnings": cmd_earnings,
        "add": cmd_add,
        "edit": cmd_edit,
        "help": cmd_help,
    }

    if len(sys.argv) < 2:
        # 默认显示每日检查
        cmd_daily()
        return

    cmd = sys.argv[1].lower()
    if cmd in commands:
        commands[cmd]()
    else:
        print(f"❌ 未知命令: {cmd}")
        print(f"可用命令: {', '.join(commands.keys())}")
        print("运行 'python3 tracker.py help' 查看帮助")


if __name__ == "__main__":
    main()
