#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
十倍股起点筛选器 —— 每日一筛

筛选画像（对齐"十倍股起点"）：小市值 + 次新 + 加速型曲线 + 扣非干净 + 现金流/毛利过关。

用法:
    python3 tenbagger.py                     # 自动识别最新报告期，全流程
    python3 tenbagger.py --top 30            # 打印前 30 家
    python3 tenbagger.py --date 20260930     # 指定报告期
    python3 tenbagger.py --mcap-min 20 --mcap-max 100   # 自定义市值区间
    python3 tenbagger.py --no-kf             # 跳过扣非校验（快速首筛）
    python3 tenbagger.py --refresh           # 忽略缓存重拉

流程:
    双增(营收/净利 >= --rev/--profit) → 加速型曲线(增速逐年抬升) → 市值区间
    → 上市年限 → 扣非校验 → 十倍股综合打分 → 与上一交易日结果对比(新增/掉出)

输出:
    out/tenbagger_YYYYMMDD.csv   每日候选池
    终端打印: 漏斗 + 排名表 + 今日新增/掉出

数据源: 东财业绩报表(财报季更新) + 腾讯行情(市值每日更新) + 同花顺财务摘要(扣非，缓存)
"""

import argparse
import glob
import os
import sys
import unicodedata
from datetime import datetime

import pandas as pd

from screen_h1 import (
    fetch_yjbb, fetch_listing, fetch_mcap, screen, pad, cut,
)
from kf_check import load_cache, save_cache, fetch_one

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
PERIOD_NAME = {"0331": "一季报", "0630": "中报", "0930": "三季报", "1231": "年报"}


def detect_period(today=None) -> str:
    """按月份自动识别最新已披露报告期。"""
    today = today or datetime.now()
    y, m = today.year, today.month
    if m <= 4:      # 1-4月: 年报未全出，用去年三季报
        return f"{y - 1}0930"
    if m <= 8:      # 5-8月: 一季报
        return f"{y}0331"
    if m <= 10:     # 9-10月: 中报
        return f"{y}0630"
    return f"{y}0930"  # 11-12月: 三季报


def accel_columns(d: pd.DataFrame, hist_years) -> list:
    """加速曲线列（旧→新），末列为当期 rev_yoy。"""
    cols = [f"rev_yoy_{y}" for y in sorted(hist_years)]
    if "rev_yoy" in d.columns:
        cols.append("rev_yoy")
    return cols


def curve_str(r, cols) -> str:
    pts = []
    for c in cols:
        v = r.get(c)
        pts.append("-" if pd.isna(v) else f"{v:.0f}")
    return "→".join(pts)


def flag_扣非(kf_pct, ratio):
    if pd.isna(kf_pct):
        return "❔"
    if kf_pct < 0:
        return "❌"
    if pd.notna(ratio) and ratio < 0.8:
        return "⚠️非经常"
    if kf_pct < 40:
        return "⚠️低"
    return "✅"


def flag(v, good, warn):
    if pd.isna(v):
        return "❔"
    return "✅" if v >= good else ("⚠️" if v >= warn else "❌")


def tenbagger_score(r, gain: float) -> float:
    """十倍股综合得分（越高越像起点形态）。"""
    s = 0.0
    s += min(r["rev_yoy"], 100) / 10          # 增速 0-10
    s += min(gain, 60) / 6                    # 加速度 0-10
    s += min(r["gross_margin"], 60) / 6       # 毛利率 0-10（生意质量，权重大）
    s += min(r["cash_ratio"], 1.5) * 2        # 现金流 0-3（轻，防应付账款噪音）
    s += min(r["roe"], 25) / 4                # ROE 0-6.25
    if pd.notna(r["扣非占比"]) and r["扣非占比"] >= 0.9:
        s += 4                                # 扣非干净
    if pd.notna(r["mcap_yi"]):
        s -= r["mcap_yi"] / 25                # 市值越小越加分(30亿-1.2 / 80亿-3.2)
    return round(s, 1)


def is_clean(r) -> bool:
    """软质量门：毛利>=25 且 现金>=0.3 且 扣非占比>=0.9 且 ROE>=5。"""
    return (pd.notna(r["gross_margin"]) and r["gross_margin"] >= 25
            and pd.notna(r["cash_ratio"]) and r["cash_ratio"] >= 0.3
            and pd.notna(r["扣非占比"]) and r["扣非占比"] >= 0.9
            and pd.notna(r["roe"]) and r["roe"] >= 5)


def find_previous(outfile: str):
    files = [f for f in glob.glob(os.path.join(OUT_DIR, "tenbagger_*.csv")) if f != outfile]
    if not files:
        return None
    return max(files)


def main() -> int:
    p = argparse.ArgumentParser(description="十倍股起点每日筛选")
    p.add_argument("--date", default="", help="报告期，默认自动识别")
    p.add_argument("--rev", type=float, default=20, help="营收同比下限(%%)")
    p.add_argument("--profit", type=float, default=20, help="净利同比下限(%%)")
    p.add_argument("--accel-gain", type=float, default=20, help="加速首末差下限(百分点)")
    p.add_argument("--accel-years", type=int, default=3, help="加速判断用历史年报数")
    p.add_argument("--mcap-min", type=float, default=30, help="总市值下限(亿)")
    p.add_argument("--mcap-max", type=float, default=80, help="总市值上限(亿)")
    p.add_argument("--min-listing", type=float, default=1, help="上市满 N 年")
    p.add_argument("--new-listing", type=float, default=5, help="上市不超过 N 年")
    p.add_argument("--top", type=int, default=30, help="打印条数")
    p.add_argument("--no-kf", action="store_true", help="跳过扣非校验")
    p.add_argument("--refresh", action="store_true", help="忽略缓存重拉")
    a = p.parse_args()

    date = a.date or detect_period()
    period = PERIOD_NAME.get(date[4:8], date)
    today = datetime.now().strftime("%Y-%m-%d")

    print(f"\n{'=' * 96}")
    print(f" 十倍股起点每日筛选  {today}    数据期: {date[:4]}年{period}    市值: {today}")
    print(f" 画像: 市值{a.mcap_min:.0f}-{a.mcap_max:.0f}亿 | 上市{a.min_listing:.0f}-{a.new_listing:.0f}年 | 加速型(末-首≥{a.accel_gain:.0f}pct) | 双增≥{a.rev:.0f}%")
    print(f"{'=' * 96}")

    # ---------- ① 双增 ----------
    df = fetch_yjbb(date, a.refresh)
    d = screen(df, a.rev, a.profit, 1.0, 2000, False, False)
    if d.empty:
        print("没有符合条件的标的。")
        return 0

    step = 9
    cur_year = int(date[:4])

    # ---------- ② 加速曲线 ----------
    hist_years = range(cur_year - 1, cur_year - 1 - a.accel_years, -1)
    for y in sorted(hist_years, reverse=True):
        hist = fetch_yjbb(f"{y}1231", a.refresh)
        hm = dict(zip(hist["code"], pd.to_numeric(hist["rev_yoy"], errors="coerce")))
        d = d.copy()
        d[f"rev_yoy_{y}"] = d["code"].map(hm)

    cols = accel_columns(d, hist_years)
    for c in cols:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d[d[cols].notna().all(axis=1)]
    mask = pd.Series(True, index=d.index)
    for lo, hi in zip(cols, cols[1:]):
        mask &= d[hi] >= d[lo]
    d = d[mask]
    d["_gain"] = d[cols[-1]] - d[cols[0]]
    d = d[d["_gain"] >= a.accel_gain]
    print(f"  {chr(0x2460 + step - 1)} 加速型曲线(增速逐年抬升)   : {len(d):>5}")
    step += 1

    # ---------- ③ 市值 ----------
    d = d.copy()
    d["mcap_yi"] = d["code"].map(fetch_mcap(d["code"].tolist(), a.refresh))
    d = d[d["mcap_yi"].notna()]
    d = d[(d["mcap_yi"] >= a.mcap_min) & (d["mcap_yi"] <= a.mcap_max)]
    print(f"  {chr(0x2460 + step - 1)} 市值 {a.mcap_min:.0f}-{a.mcap_max:.0f} 亿           : {len(d):>5}")
    step += 1

    # ---------- ④ 上市年限 ----------
    d["list_date"] = d["code"].map(fetch_listing(a.refresh))
    d = d[d["list_date"].notna()]
    d["listed_years"] = (pd.Timestamp.today().normalize()
                         - pd.to_datetime(d["list_date"])).dt.days / 365.25
    d = d[(d["listed_years"] >= a.min_listing) & (d["listed_years"] <= a.new_listing)]
    print(f"  {chr(0x2460 + step - 1)} 上市 {a.min_listing:.0f}-{a.new_listing:.0f} 年             : {len(d):>5}")
    step += 1

    if d.empty:
        print("\n没有符合条件的标的。")
        return 0

    # ---------- ⑤ 扣非校验 ----------
    if not a.no_kf:
        cache = load_cache()
        print(f"  {chr(0x2460 + step - 1)} 扣非校验 {len(d)} 家(缓存{len(cache)}条) ...")
        rows = []
        for code in d["code"]:
            r = fetch_one(code, f"{date[:4]}-{date[4:6]}-{date[6:8]}", cache)
            rows.append({"code": code, **r})
        save_cache(cache)
        d = d.merge(pd.DataFrame(rows), on="code", how="left")
        d["扣非占比"] = (d["kf_yi"] / d["np_yi"]).round(3)
        d = d[(d["扣非占比"].notna()) & (d["扣非占比"] >= 0.85) & (d["kf_pct"] >= 25)]
        print(f"  {chr(0x2460 + step - 1)} 扣非干净(占比≥0.85且增速≥25%) : {len(d):>5}")
        step += 1
    else:
        d["扣非占比"] = pd.NA
        d["kf_pct"] = pd.NA

    # ---------- ⑥ 打分排序 ----------
    d = d.copy()
    for c in ("rev_yoy", "gross_margin", "cash_ratio", "roe", "mcap_yi"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["得分"] = d.apply(lambda r: tenbagger_score(r, r["_gain"]), axis=1)
    d = d.sort_values("得分", ascending=False)

    # ---------- ⑦ 与上一交易日对比 ----------
    outfile = os.path.join(OUT_DIR, f"tenbagger_{today.replace('-', '')}.csv")
    d.to_csv(outfile, index=False, encoding="utf-8-sig")
    prev = find_previous(outfile)
    added, dropped = set(), set()
    if prev:
        pc = pd.read_csv(prev, dtype={"code": str})
        prev_codes = set(pc["code"])
        now_codes = set(d["code"])
        added = now_codes - prev_codes
        dropped = prev_codes - now_codes

    # ---------- 打印 ----------
    n = min(a.top, len(d))
    print(f"\n{'=' * 96}\n【十倍股候选】按得分排序（前 {n} 家，共 {len(d)} 家）\n{'=' * 96}")
    head = (pad("代码", 8) + pad("简称", 12) + pad("行业", 10) + pad("市值", 7, ">")
            + pad("营收%", 7, ">") + pad("净利%", 8, ">") + pad("毛利", 6, ">") + pad("ROE", 6, ">")
            + pad("现金", 7, ">") + " " + pad("扣非", 7) + pad("得分", 6, ">") + "  增速曲线(旧→新)")
    print(head)
    print("-" * 96)
    for _, r in d.head(n).iterrows():
        note = ""
        if is_clean(r):
            note = " ⭐干净"
        if r["code"] in added:
            note += " 🆕新增"
        elif r["code"] in dropped:
            note += " ⬇️掉出"
        if pd.notna(r["小基数"]) and r["小基数"]:
            note += " ⚠小基数"
        print(pad(r["code"], 8) + pad(r["name"], 12) + pad(r["industry"], 10)
              + pad(f"{r['mcap_yi']:.0f}", 7, ">") + pad(f"{r['rev_yoy']:.0f}", 7, ">")
              + pad(f"{r['profit_yoy']:.0f}", 8, ">") + pad(f"{r['gross_margin']:.0f}", 6, ">")
              + pad(f"{r['roe']:.1f}", 6, ">")               + pad(f"{r['cash_ratio']:.2f}", 7, ">") + " "
              + pad(flag_扣非(r["kf_pct"], r["扣非占比"]), 7)
              + pad(f"{r['得分']:.1f}", 6, ">") + "  " + curve_str(r, cols) + note)

    if prev:
        print(f"\n  🆕 今日新增 {len(added)} 家: " + ("、".join(sorted(added)) if added else "无"))
        print(f"  ⬇️ 今日掉出 {len(dropped)} 家: " + ("、".join(sorted(dropped)) if dropped else "无"))

    print(f"\n  → 已存: {outfile}")
    print(f"  下一步: 对得分前 3 名做深度分析(三张表+渗透率+刻意观察)，再决定是否进观察池。")
    print(f"  ⚠️ 仅为财务筛选，不构成投资建议。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
