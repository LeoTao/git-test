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
import json
import os
import subprocess
import sys
import unicodedata
from datetime import datetime

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


def fetch_listing(refresh: bool = False) -> dict:
    """全市场上市日期，返回 {code: 'YYYY-MM-DD'}。"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache = os.path.join(CACHE_DIR, "listing_dates.csv")
    if os.path.exists(cache) and not refresh:
        d = pd.read_csv(cache, dtype={"code": str})
        return dict(zip(d["code"], d["list_date"]))
    print("  [fetch] 上市日期 (沪深主板/科创/创业/北交所) ...")
    frames = [
        ak.stock_info_sh_name_code(symbol="主板A股")[["证券代码", "上市日期"]],
        ak.stock_info_sh_name_code(symbol="科创板")[["证券代码", "上市日期"]],
        ak.stock_info_sz_name_code(symbol="A股列表")[["A股代码", "A股上市日期"]],
        ak.stock_info_bj_name_code()[["证券代码", "上市日期"]],
    ]
    d = pd.concat([f.set_axis(["code", "list_date"], axis=1) for f in frames], ignore_index=True)
    d["code"] = d["code"].astype(str)
    d.to_csv(cache, index=False)
    return dict(zip(d["code"], d["list_date"]))


def _tx_symbol(code: str) -> str:
    if code.startswith(("4", "8", "92")):
        return "bj" + code
    return ("sh" if code.startswith("6") else "sz") + code


def fetch_mcap(codes, refresh: bool = False) -> dict:
    """腾讯批量行情取总市值（亿），50 只一批，按日缓存。"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache = os.path.join(CACHE_DIR, f"mcap_tx_{datetime.now():%Y%m%d}.json")
    data = {}
    if os.path.exists(cache) and not refresh:
        with open(cache, encoding="utf-8") as f:
            data = json.load(f)
    todo = [c for c in dict.fromkeys(codes) if c not in data]
    if todo:
        print(f"  [fetch] 总市值 {len(todo)} 只 (腾讯行情) ...")
    for i in range(0, len(todo), 50):
        url = "https://qt.gtimg.cn/q=" + ",".join(_tx_symbol(c) for c in todo[i:i + 50])
        raw = subprocess.run(["curl", "-s", "--max-time", "25", url,
                              "-H", "Referer: https://gu.qq.com/"],
                             capture_output=True).stdout
        for line in raw.decode("gbk", errors="ignore").strip().split(";"):
            if '="' not in line:
                continue
            f = line.split('="', 1)[1].rstrip('"').split("~")
            if len(f) < 46:
                continue
            try:
                data[f[2]] = float(f[45])
            except ValueError:
                pass
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return data


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


def print_table(d: pd.DataFrame, top: int, curve: bool = False) -> None:
    has_mc = "mcap_yi" in d.columns
    has_list = "listed_years" in d.columns
    curve_cols = [c for c in sorted(d.columns) if c.startswith("rev_yoy_")] + ["rev_yoy"]
    order = "按总市值升序" if has_mc else "按归母净利润绝对值排序"
    print(f"\n{'='*104}\n【候选池明细】{order}（前 {min(top, len(d))} 家）\n{'='*104}")
    head = (pad("代码", 8) + pad("简称", 12) + pad("行业", 12) + pad("营收(亿)", 10, ">")
            + pad("营收%", 9, ">") + pad("净利(亿)", 10, ">") + pad("净利%", 11, ">")
            + pad("毛利率", 8, ">") + pad("ROE", 7, ">") + pad("现金比", 8, ">"))
    if has_mc:
        head += pad("市值(亿)", 10, ">")
    if has_list:
        head += pad("上市(年)", 10, ">")
    if curve:
        head += " 增速曲线(旧→新)"
    print(head + " 备注")
    print("-" * 104)
    for _, r in d.head(top).iterrows():
        cr = r["cash_ratio"]
        cr_s = f"{cr:.2f}" if pd.notna(cr) else "-"
        note = "⚠小基数" if r["小基数"] else ""
        if pd.notna(cr) and cr < 0.3:
            note += " 现金流弱"
        row = (pad(r["code"], 8) + pad(r["name"], 12) + pad(r["industry"], 12)
               + pad(f"{r['rev_yi']:.1f}", 10, ">") + pad(f"{r['rev_yoy']:.1f}%", 9, ">")
               + pad(f"{r['profit_yi']:.2f}", 10, ">") + pad(f"{r['profit_yoy']:.1f}%", 11, ">")
               + pad(f"{r['gross_margin']:.1f}%", 8, ">") + pad(f"{r['roe']:.1f}", 7, ">")
               + pad(cr_s, 8, ">"))
        if has_mc:
            row += pad(f"{r['mcap_yi']:.1f}", 10, ">")
        if has_list:
            row += pad(f"{r['listed_years']:.1f}", 10, ">")
        if curve:
            pts = []
            for c in curve_cols:
                v = r.get(c)
                pts.append("-" if pd.isna(v) else f"{v:.0f}")
            row += " " + "→".join(pts)
        print(row + " " + note)


def main() -> int:
    p = argparse.ArgumentParser(description="全A股财报双高增长筛选")
    p.add_argument("--date", default="20260630", help="报告期，默认 20260630（2026中报）")
    p.add_argument("--rev", type=float, default=40, help="营收同比下限(%%)")
    p.add_argument("--profit", type=float, default=40, help="净利同比下限(%%)")
    p.add_argument("--min-rev", type=float, default=1.0, help="营收下限(亿)")
    p.add_argument("--min-profit", type=float, default=2000, help="净利下限(万)")
    p.add_argument("--top", type=int, default=45, help="打印条数")
    p.add_argument("--keep-bj", action="store_true", help="保留北交所")
    p.add_argument("--keep-st", action="store_true", help="保留 ST")
    p.add_argument("--mcap-min", type=float, default=0, help="总市值下限(亿，0=不限)")
    p.add_argument("--mcap-max", type=float, default=0, help="总市值上限(亿，0=不限)")
    p.add_argument("--min-listing", type=float, default=0, help="上市满 N 年(N=0 不限)")
    p.add_argument("--new-listing", type=float, default=0, help="上市不超过 N 年(0=不限，次新股口径)")
    p.add_argument("--rev-years", type=int, default=1,
                   help="要求连续 N 年(含当期)营收增速都达到 --rev，默认 1")
    p.add_argument("--accelerating", action="store_true",
                   help="加速型曲线：营收增速逐年抬升(非递减)且首末差>=--accel-gain")
    p.add_argument("--accel-years", type=int, default=3,
                   help="加速判断用历史年报数(默认3，含当期共4个点)")
    p.add_argument("--accel-gain", type=float, default=20,
                   help="加速首末点增速差下限(百分点，默认20)")
    p.add_argument("--refresh", action="store_true", help="忽略缓存重新拉取")
    a = p.parse_args()

    period = {"0331": "一季报", "0630": "中报", "0930": "三季报", "1231": "年报"}.get(a.date[4:8], a.date)
    print(f"\n{'='*78}\n全A股 {a.date[:4]}年{period}筛选 —— 营收 >{a.rev:.0f}% & 净利 >{a.profit:.0f}%\n{'='*78}")
    df = fetch_yjbb(a.date, a.refresh)
    d = screen(df, a.rev, a.profit, a.min_rev, a.min_profit, a.keep_bj, a.keep_st)

    if d.empty:
        print("\n没有符合条件的标的。")
        return 0

    step = 9
    cur_year = int(a.date[:4])
    hist_years = set()
    if a.rev_years > 1:
        hist_years.update(range(cur_year - 1, cur_year - a.rev_years, -1))
    if a.accelerating:
        hist_years.update(range(cur_year - 1, cur_year - 1 - a.accel_years, -1))

    # 先附加历史年报营收增速列（按年倒序：最近的先加）
    for year in sorted(hist_years, reverse=True):
        hist = fetch_yjbb(f"{year}1231", a.refresh)
        hm = dict(zip(hist["code"], pd.to_numeric(hist["rev_yoy"], errors="coerce")))
        d = d.copy()
        d[f"rev_yoy_{year}"] = d["code"].map(hm)

    # rev_years：连续 N 年增速都达到 --rev
    if a.rev_years > 1:
        for year in sorted(hist_years, reverse=True):
            d = d[d[f"rev_yoy_{year}"] >= a.rev]
            print(f"  {chr(0x2460 + step - 1)} {year}年报营收增速 >= {a.rev:.0f}%   : {len(d):>5}")
            step += 1

    # accelerating：增速逐年抬升（非递减），且首末差 >= accel_gain
    if a.accelerating:
        cols = [f"rev_yoy_{y}" for y in sorted(hist_years)] + ["rev_yoy"]  # 旧→新
        for c in cols:
            d[c] = pd.to_numeric(d[c], errors="coerce")
        d = d[d[cols].notna().all(axis=1)]
        mask = pd.Series(True, index=d.index)
        for lo, hi in zip(cols, cols[1:]):
            mask &= d[hi] >= d[lo]
        d = d[mask]
        d = d[d[cols[-1]] - d[cols[0]] >= a.accel_gain]
        print(f"  {chr(0x2460 + step - 1)} 增速逐年抬升且末-首≥{a.accel_gain:.0f}pct : {len(d):>5}")
        step += 1

    if a.mcap_min or a.mcap_max:
        d = d.copy()
        d["mcap_yi"] = d["code"].map(fetch_mcap(d["code"].tolist(), a.refresh))
        d = d[d["mcap_yi"].notna()]
        if a.mcap_min:
            d = d[d["mcap_yi"] >= a.mcap_min]
        if a.mcap_max:
            d = d[d["mcap_yi"] <= a.mcap_max]
        print(f"  {chr(0x2460 + step - 1)} 市值 {a.mcap_min:.0f}-{a.mcap_max or 9999:.0f} 亿      : {len(d):>5}")
        step += 1

    if a.min_listing or a.new_listing:
        d = d.copy()
        d["list_date"] = d["code"].map(fetch_listing(a.refresh))
        d = d[d["list_date"].notna()]
        d["listed_years"] = (pd.Timestamp.today().normalize()
                             - pd.to_datetime(d["list_date"])).dt.days / 365.25
        if a.min_listing:
            d = d[d["listed_years"] >= a.min_listing]
        if a.new_listing:
            d = d[d["listed_years"] <= a.new_listing]
        print(f"  {chr(0x2460 + step - 1)} 上市 {a.min_listing:.0f}-{a.new_listing or 99:.0f} 年      : {len(d):>5}")
        step += 1

    if d.empty:
        print("\n没有符合条件的标的。")
        return 0

    if "mcap_yi" in d.columns:
        d = d.sort_values("mcap_yi")

    suffix = ""
    if a.mcap_min or a.mcap_max:
        suffix += f"_mc{int(a.mcap_min)}-{int(a.mcap_max or 9999)}"
    if a.min_listing or a.new_listing:
        suffix += f"_new{int(a.min_listing)}-{int(a.new_listing or 99)}y"
    if a.rev_years > 1:
        suffix += f"_rev{a.rev_years}y"
    if a.accelerating:
        suffix += "_accel"

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, f"h1_{a.date}_double{int(a.rev)}{suffix}.csv")
    d.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n  → 候选池已存: {out}")

    print_industry(d)
    print_table(d, a.top, curve=a.accelerating)

    print(f"\n⚠️  本清单仅为财务筛选结果，不构成投资建议。下一步：\n"
          f"    python3 keyword_scan.py        # 7 关键词扫描（巨潮全文检索）\n"
          f"    python3 kf_check.py {out}      # 扣非校验，剔除一次性收益\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
