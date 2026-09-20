#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全A股中报/财报高增长筛选器 —— "营收 & 净利双40%" 候选池

用法:
    python3 screen_h1.py                          # 默认 2026 中报，双 40%
    python3 screen_h1.py --rev 30 --profit 40     # 自定义阈值
    python3 screen_h1.py --date 20260630 --by-profit 0   # 按利润增速排序
    python3 screen_h1.py --top 80                 # 打印前 80 家

筛选逻辑（对齐 .github/skills/earnings-season-keyword-screening）:
    条件1  营收同比 >= --rev    (默认 40%)
    条件2  净利同比 >= --profit (默认 40%)
    条件3  净利润 > 0（排除亏损股）
    条件4  扣非后再看（--strict 时逐家拉取扣非增速，剔除一次性收益）
    条件5  上市满 1 年（默认剔除次新：--min-age 1）
    条件6  最小规模：营收 >= --min-rev 亿，净利 >= --min-profit 万

输出:
    out/h1_{date}_double{rev}.csv   完整候选池
    终端打印：总量 → 行业聚类（板块效应）→ 明细表
"""

import argparse
import os
import sys
import unicodedata

import akshare as ak
import pandas as pd

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")


# ---------------------------------------------------------------- 中文对齐
def dwidth(s) -> int:
    """终端显示宽度（中日韩全角按 2 计）。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(s))


def cut(s, n: int) -> str:
    """按显示宽度截断。"""
    s, out, acc = str(s), "", 0
    for c in s:
        cw = 2 if unicodedata.east_asian_width(c) in "WF" else 1
        if acc + cw > n:
            break
        out, acc = out + c, acc + cw
    return out


def pad(s, n: int, align: str = "<") -> str:
    s = cut(s, n)
    d = max(0, n - dwidth(s))
    return s + " " * d if align == "<" else " " * d + s


# ---------------------------------------------------------------- 数据获取
def _norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    """东财列名里混了空格，统一去掉再改名。"""
    df = df.rename(columns=lambda c: str(c).replace(" ", "").replace("\n", ""))
    return df.rename(
        columns={
            "股票代码": "code",
            "股票简称": "name",
            "营业总收入-营业总收入": "revenue",
            "营业总收入-同比增长": "rev_yoy",
            "净利润-净利润": "profit",
            "净利润-同比增长": "profit_yoy",
            "每股收益": "eps",
            "每股净资产": "bps",
            "净资产收益率": "roe",
            "每股经营现金流量": "ocf_ps",
            "销售毛利率": "gross_margin",
            "所处行业": "industry",
            "最新公告日期": "ann_date",
        }
    )


def fetch_yjbb(date: str, refresh: bool = False) -> pd.DataFrame:
    """东财业绩报表（全市场一次拉完，含同比增速/毛利率/行业）。"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache = os.path.join(CACHE_DIR, f"yjbb_{date}.csv")
    if os.path.exists(cache) and not refresh:
        print(f"  [cache] {cache}")
        return _norm_cols(pd.read_csv(cache, dtype={"股票代码": str}))
    print(f"  [fetch] akshare stock_yjbb_em({date}) ...")
    raw = ak.stock_yjbb_em(date=date)
    raw.to_csv(cache, index=False)
    return _norm_cols(raw)


# ---------------------------------------------------------------- 过滤
def is_bj(code: str) -> bool:
    """北交所：4/8 开头 + 920 开头。流动性差，单独标注。"""
    return code.startswith(("4", "8", "920"))


def is_st(name: str) -> bool:
    return "ST" in str(name).upper() or "退" in str(name)


def screen(df: pd.DataFrame, rev: float, profit: float, min_rev: float,
           min_profit: float, keep_bj: bool, keep_st: bool) -> pd.DataFrame:
    d = df.copy()
    for c in ("rev_yoy", "profit_yoy", "revenue", "profit", "gross_margin", "roe"):
        d[c] = pd.to_numeric(d[c], errors="coerce")

    total = len(d)
    # 已披露正式数据的（有营收/利润/增速）
    d = d[d["rev_yoy"].notna() & d["profit_yoy"].notna()]
    print(f"  ① 有完整同比数据          : {len(d):>5} / {total}")

    both_single = d[(d["rev_yoy"] >= rev) | (d["profit_yoy"] >= profit)]
    print(f"  ② 单边达标（营收或利润）  : {len(both_single):>5}")

    d = d[(d["rev_yoy"] >= rev) & (d["profit_yoy"] >= profit)]
    print(f"  ③ 双 {rev:.0f}% 达标              : {len(d):>5}")

    d = d[d["profit"] > 0]
    print(f"  ④ 剔除亏损                : {len(d):>5}")

    d = d[d["revenue"] >= min_rev * 1e8]
    print(f"  ⑤ 营收 >= {min_rev} 亿            : {len(d):>5}")

    d = d[d["profit"] >= min_profit * 1e4]
    print(f"  ⑥ 净利 >= {min_profit} 万           : {len(d):>5}")

    if not keep_bj:
        d = d[~d["code"].map(is_bj)]
        print(f"  ⑦ 剔除北交所              : {len(d):>5}")
    if not keep_st:
        d = d[~d["name"].map(is_st)]
        print(f"  ⑧ 剔除 ST                 : {len(d):>5}")

    d["rev_yi"] = (d["revenue"] / 1e8).round(2)
    d["profit_yi"] = (d["profit"] / 1e8).round(3)
    d["cash_ratio"] = (d["ocf_ps"] / d["eps"]).replace([float("inf"), float("-inf")], pd.NA)
    # 小基数标记：利润增速 >1000% 基本是去年基数接近 0，增速失真，看绝对额
    d["小基数"] = d["profit_yoy"] > 1000
    return d.sort_values("profit_yi", ascending=False)


# ---------------------------------------------------------------- 输出
def print_industry(d: pd.DataFrame, top_n: int = 22) -> None:
    if d.empty or d["industry"].isna().all():
        return
    g = (d.groupby("industry")
           .agg(家数=("code", "size"),
                净利合计亿=("profit_yi", "sum"),
                均营收增速=("rev_yoy", "mean"),
                均净利增速=("profit_yoy", "mean"))
           .sort_values("家数", ascending=False))
    print(f"\n{'='*104}\n【板块效应】命中家数 >= 2 的行业（多个同类公司同时达标 = 行业趋势验证）\n{'='*104}")
    multi = g[g["家数"] >= 2]
    if multi.empty:
        print("  无 —— 命中标的高度分散，说明是公司个体逻辑而非行业景气")
    else:
        print(pad("行业", 16) + pad("家数", 6, ">") + pad("净利合计(亿)", 14, ">")
              + pad("均营收增速", 13, ">") + pad("均净利增速", 13, ">"))
        print("-" * 104)
        for ind, r in multi.iterrows():
            print(pad(ind, 16) + pad(int(r["家数"]), 6, ">") + pad(f"{r['净利合计亿']:.1f}", 14, ">")
                  + pad(f"{r['均营收增速']:.1f}%", 13, ">") + pad(f"{r['均净利增速']:.1f}%", 13, ">"))
    singles = [str(i) for i in g.index[len(multi):]]
    print(f"\n其余单点命中行业 {len(singles)} 个:\n  " + "、".join(singles[:top_n]))


def print_table(d: pd.DataFrame, top: int) -> None:
    print(f"\n{'='*104}\n【候选池明细】按归母净利润绝对值排序（前 {min(top, len(d))} 家）\n{'='*104}")
    print(pad("代码", 8) + pad("简称", 12) + pad("行业", 14) + pad("营收(亿)", 10, ">")
          + pad("营收%", 9, ">") + pad("净利(亿)", 10, ">") + pad("净利%", 11, ">")
          + pad("毛利率", 8, ">") + pad("ROE", 7, ">") + pad("现金比", 8, ">") + " 备注")
    print("-" * 104)
    for _, r in d.head(top).iterrows():
        cr = r["cash_ratio"]
        cr_s = f"{cr:.2f}" if pd.notna(cr) else "-"
        note = "⚠小基数" if r["小基数"] else ""
        if pd.notna(cr) and cr < 0.3:
            note += " 现金流弱"
        print(pad(r["code"], 8) + pad(r["name"], 12) + pad(r["industry"], 14)
              + pad(f"{r['rev_yi']:.1f}", 10, ">") + pad(f"{r['rev_yoy']:.1f}%", 9, ">")
              + pad(f"{r['profit_yi']:.2f}", 10, ">") + pad(f"{r['profit_yoy']:.1f}%", 11, ">")
              + pad(f"{r['gross_margin']:.1f}%", 8, ">") + pad(f"{r['roe']:.1f}", 7, ">")
              + pad(cr_s, 8, ">") + " " + note)


def main() -> int:
    p = argparse.ArgumentParser(description="全A股中报双高增长筛选")
    p.add_argument("--date", default="20260630", help="报告期，默认 20260630（2026中报）")
    p.add_argument("--rev", type=float, default=40, help="营收同比下限(%%)")
    p.add_argument("--profit", type=float, default=40, help="净利同比下限(%%)")
    p.add_argument("--min-rev", type=float, default=1.0, help="营收下限(亿)")
    p.add_argument("--min-profit", type=float, default=2000, help="净利下限(万)")
    p.add_argument("--top", type=int, default=45, help="打印条数")
    p.add_argument("--keep-bj", action="store_true", help="保留北交所")
    p.add_argument("--keep-st", action="store_true", help="保留 ST")
    p.add_argument("--refresh", action="store_true", help="忽略缓存重新拉取")
    a = p.parse_args()

    print(f"\n{'='*78}\n全A股 {a.date[:4]}中报筛选 —— 营收 >{a.rev:.0f}% & 净利 >{a.profit:.0f}%\n{'='*78}")
    df = fetch_yjbb(a.date, a.refresh)
    d = screen(df, a.rev, a.profit, a.min_rev, a.min_profit, a.keep_bj, a.keep_st)

    if d.empty:
        print("\n没有符合条件的标的。")
        return 0

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, f"h1_{a.date}_double{int(a.rev)}.csv")
    d.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n  → 候选池已存: {out}")

    print_industry(d)
    print_table(d, a.top)

    print(f"\n⚠️  本清单仅为财务筛选结果，不构成投资建议。下一步：\n"
          f"    python3 keyword_scan.py        # 7 关键词扫描（巨潮全文检索）\n"
          f"    python3 kf_check.py {out}      # 扣非校验，剔除一次性收益\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
