#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
扣非校验 —— 剔除"靠一次性收益撑起来"的伪高增长

实现 skill 条件2「排除一次性收益（卖资产、政府补贴、投资收益）」。
对候选池逐家拉同花顺财务摘要，取最新报告期的：
    扣非净利润同比增长率   ← 真实主营增长
    扣非净利润 / 净利润    ← 非经常性损益占比

用法:
    python3 kf_check.py out/keyword_cross_2026-07-01_2026-09-20.csv
    python3 kf_check.py out/h1_20260630_double40.csv --period 2026-06-30 --limit 100

输出:
    out/<原名>_kf.csv  —— 增加 扣非增速 / 扣非净利 / 扣非占比 / 判定 四列
"""

import argparse
import json
import os
import re
import sys
import time

import akshare as ak
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, ".cache", "kf_cache.json")
PCT = re.compile(r"(-?[\d.]+)\s*亿")


def to_yi(s):
    """'130.92亿' -> 130.92 ; '8300万' -> 0.83 ; '--' -> None"""
    if s is None:
        return None
    t = str(s).replace(",", "").strip()
    if t in ("--", "nan", "", "None"):
        return None
    m = PCT.search(t)
    if m and "亿" in t:
        return float(m.group(1))
    m = re.search(r"(-?[\d.]+)\s*万", t)
    if m:
        return float(m.group(1)) / 1e4
    try:
        return float(t)
    except ValueError:
        return None


def to_pct(s):
    if s is None:
        return None
    t = str(s).replace(",", "").strip().rstrip("%")
    if t in ("--", "nan", "", "None", "False"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def load_cache() -> dict:
    if os.path.exists(CACHE):
        return json.load(open(CACHE, encoding="utf-8"))
    return {}


def save_cache(c: dict) -> None:
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(c, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)


def fetch_one(code: str, period: str, cache: dict, sleep: float = 0.7) -> dict:
    key = f"{code}|{period}"
    if key in cache:
        return cache[key]
    res = {"kf_pct": None, "kf_yi": None, "np_yi": None, "period_used": None}
    try:
        df = ak.stock_financial_abstract_ths(symbol=code, indicator="按报告期")
        df = df.rename(columns=lambda c: str(c).replace(" ", ""))
        hit = df[df["报告期"].astype(str).str.startswith(period)]
        if hit.empty:
            hit = df.tail(1)
        row = hit.iloc[-1]
        res = {
            "kf_pct": to_pct(row.get("扣非净利润同比增长率")),
            "kf_yi": to_yi(row.get("扣非净利润")),
            "np_yi": to_yi(row.get("净利润")),
            "period_used": str(row.get("报告期")),
        }
    except Exception as e:  # noqa: BLE001
        res["err"] = str(e)[:60]
    cache[key] = res
    time.sleep(sleep)
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", help="候选池 CSV（需含 code 列）")
    ap.add_argument("--period", default="2026-06-30", help="报告期前缀")
    ap.add_argument("--limit", type=int, default=0, help="只校验前 N 家（0=全部）")
    ap.add_argument("--sleep", type=float, default=0.7)
    a = ap.parse_args()

    d = pd.read_csv(a.csv, dtype={"code": str})
    d["code"] = d["code"].str.zfill(6)
    if a.limit:
        d = d.head(a.limit)

    cache = load_cache()
    print(f"\n{'='*96}\n扣非校验：{len(d)} 家  (缓存 {len(cache)} 条)\n{'='*96}")

    rows = []
    for i, code in enumerate(d["code"], 1):
        r = fetch_one(code, a.period, cache)
        rows.append({"code": code, **r})
        if i % 10 == 0 or i == len(d):
            save_cache(cache)
            print(f"  {i}/{len(d)} ...", end="\r")
    save_cache(cache)
    print()

    d = d.merge(pd.DataFrame(rows), on="code", how="left")
    d["扣非占比"] = (d["kf_yi"] / d["np_yi"]).round(3)

    def verdict(r):
        if pd.isna(r["kf_pct"]):
            return "❔无数据"
        if r["kf_pct"] < 0:
            return "❌扣非亏损/大降"
        if r["kf_pct"] < 40:
            return "⚠️扣非不达标"
        if pd.notna(r["扣非占比"]) and r["扣非占比"] < 0.8:
            return "⚠️非经常性占比高"
        return "✅真主营增长"

    d["判定"] = d.apply(verdict, axis=1)
    d = d.sort_values(["判定", "kf_pct"], ascending=[True, False])

    print(f"{'代码':<8}{'简称':<12}{'净利增速':>10}{'扣非增速':>10}{'扣非占比':>10}  判定")
    print("-" * 96)
    from screen_h1 import pad
    for _, r in d.iterrows():
        kf = f"{r['kf_pct']:.0f}%" if pd.notna(r["kf_pct"]) else "-"
        rt = f"{r['扣非占比']:.2f}" if pd.notna(r["扣非占比"]) else "-"
        print(pad(r["code"], 8) + pad(r["name"], 12)
              + pad(f"{r['profit_yoy']:.0f}%", 10, ">") + pad(kf, 10, ">")
              + pad(rt, 10, ">") + "  " + r["判定"])

    print(f"\n判定分布:\n{d['判定'].value_counts().to_string()}")
    out = a.csv.replace(".csv", "_kf.csv")
    d.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n  → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
