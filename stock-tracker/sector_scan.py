#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主流板块扫描器 —— 用市场数据找"当下最强方向"

数据源（同花顺，akshare；东财接口常被拒连，故用 THS）:
    1. 行业板块概览   stock_board_industry_summary_ths()    当日涨跌幅/净流入/涨跌家数/领涨股
    2. 行业指数历史   stock_board_industry_index_ths()      收盘价序列 -> 算 5/10/20 日累计涨幅

输出:
    终端打印综合排名表（按 20 日累计涨幅排序）
    out/sector_scan_{date}.csv 完整数据

用法:
    python3 sector_scan.py --top 25
"""

import argparse
import datetime as dt
import os
import sys
import time
import unicodedata

import akshare as ak
import pandas as pd

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")


def dwidth(s) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(s))


def cut(s, n: int) -> str:
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


def multi_period_ret(close: pd.Series, periods=(5, 10, 20)):
    """按收盘价算最近 n 日累计涨跌幅(%)。close 按时间升序。"""
    out = {}
    for n in periods:
        if len(close) >= n + 1:
            out[f"chg{n}"] = round((close.iloc[-1] / close.iloc[-1 - n] - 1) * 100, 2)
        else:
            out[f"chg{n}"] = float("nan")
    return out


def summary() -> pd.DataFrame:
    df = ak.stock_board_industry_summary_ths()
    df = df.rename(columns=lambda c: str(c).replace(" ", ""))
    df = df.rename(columns={
        "板块": "name",
        "涨跌幅": "chg",
        "净流入": "net_in",        # 单位: 亿
        "上涨家数": "up_n",
        "下跌家数": "down_n",
        "领涨股": "leader",
        "领涨股-涨跌幅": "leader_chg",
        "总成交额": "amount",      # 单位: 亿
    })
    for c in ("chg", "net_in", "up_n", "down_n", "leader_chg", "amount"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def index_hist(name: str, days_back: int = 50):
    end = dt.date.today()
    start = end - dt.timedelta(days=days_back)
    try:
        h = ak.stock_board_industry_index_ths(
            symbol=name,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        h = h.rename(columns=lambda c: str(c).replace(" ", ""))
        if "收盘价" not in h.columns or len(h) == 0:
            return {}
        close = pd.to_numeric(h["收盘价"], errors="coerce").dropna()
        return multi_period_ret(close)
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()

    today = dt.date.today().strftime("%Y-%m-%d")
    print(f"# 数据日期: {today} (2026-09-20 为周日，行情为最近交易日 09-18)\n")

    df = summary()
    print(f"[同花顺行业概览: {len(df)} 个行业]")
    print(f"[计算 {len(df)} 个行业的 5/10/20 日累计涨幅，约需 1 分钟 ...]")

    rows = {}
    ok = 0
    for name in df["name"]:
        r = index_hist(str(name))
        rows[name] = r
        if r:
            ok += 1
        time.sleep(0.12)

    hist_df = pd.DataFrame.from_dict(rows, orient="index").reset_index()
    hist_df.columns = ["name", "chg5", "chg10", "chg20"]

    full = df.merge(hist_df, on="name", how="left")
    full["up_ratio"] = (full["up_n"] / (full["up_n"] + full["down_n"]) * 100).round(1)
    full = full.sort_values(
        ["chg20", "chg10", "chg5", "net_in"],
        ascending=[False, False, False, False],
        na_position="last",
    )

    os.makedirs(OUT_DIR, exist_ok=True)
    csv_path = os.path.join(OUT_DIR, f"sector_scan_{dt.date.today():%Y%m%d}.csv")
    full.to_csv(csv_path, index=False, encoding="utf-8-sig")

    print(f"[{ok}/{len(df)} 个行业历史数据获取成功]\n")
    print("=" * 100)
    print("【主流板块综合榜】（按 20 日累计涨幅排序：20日=中期强度，5日=近期加速）")
    print("=" * 100)
    hdr = (
        pad("板块", 13) + pad("今日%", 7, ">") + pad("5日%", 7, ">")
        + pad("10日%", 7, ">") + pad("20日%", 7, ">")
        + pad("净流入亿", 8, ">") + pad("上涨占比", 8, ">")
        + pad("领涨股", 12)
    )
    print(hdr)
    print("-" * 100)

    def g(v):
        try:
            f = float(v)
            return f"{f:>6.2f}" if f == f else "   --"
        except Exception:
            return "   --"

    def gn(v):
        try:
            f = float(v)
            return f"{f:>7.1f}" if f == f else "   --"
        except Exception:
            return "   --"

    for _, r in full.head(args.top).iterrows():
        up_s = f"{r['up_ratio']}%" if r["up_ratio"] == r["up_ratio"] else "--"
        row = (
            pad(str(r["name"]), 13) + g(r["chg"]) + " " + g(r["chg5"]) + " "
            + g(r["chg10"]) + " " + g(r["chg20"]) + " " + gn(r["net_in"])
            + " " + pad(up_s, 8, ">") + " " + pad(str(r["leader"]), 12)
        )
        print(row)

    print(f"\n# 完整数据已存: {csv_path}")


if __name__ == "__main__":
    main()
