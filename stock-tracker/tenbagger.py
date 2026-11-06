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
import json
import os
import subprocess
import sys
import unicodedata
from datetime import datetime

import pandas as pd

from screen_h1 import (
    fetch_yjbb, fetch_listing, fetch_mcap, screen, pad, cut,
)
from kf_check import load_cache, save_cache, fetch_one

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
PERIOD_NAME = {"0331": "一季报", "0630": "中报", "0930": "三季报", "1231": "年报"}


def tx_symbol(code: str) -> str:
    if code.startswith(("4", "8", "92")):
        return "bj" + code
    return ("sh" if code.startswith("6") else "sz") + code


def fetch_gain_from_low(codes, refresh: bool = False) -> dict:
    """前复权周K：当前价 / 上市以来最低价 - 1（倍，0.5=已涨50%）。按日缓存。"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache = os.path.join(CACHE_DIR, f"gain_{datetime.now():%Y%m%d}.json")
    data = {}
    if os.path.exists(cache) and not refresh:
        with open(cache, encoding="utf-8") as f:
            data = json.load(f)
    todo = [c for c in dict.fromkeys(codes) if c not in data]
    if todo:
        print(f"  [fetch] 低点涨幅 {len(todo)} 只 (腾讯周K前复权) ...")
    for c in todo:
        sym = tx_symbol(c)
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},week,,,300,qfq"
        raw = subprocess.run(["curl", "-s", "--max-time", "20", url,
                              "-H", "Referer: https://gu.qq.com/"],
                             capture_output=True).stdout
        try:
            k = json.loads(raw)["data"][sym]
            bars = k.get("qfqweek") or k.get("week") or []
            if bars:
                low = min(float(b[4]) for b in bars)
                now = float(bars[-1][2])
                data[c] = round(now / low - 1, 3) if low > 0 else None
            else:
                data[c] = None
        except Exception:
            data[c] = None
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return data


# ---------------------------------------------------------------- M 大盘方向
INDEXES = [("上证指数", "sh000001"), ("创业板指", "sz399006"), ("沪深300", "sh000300")]


def _daily_bars(sym: str, n: int = 260):
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,{n},qfq"
    raw = subprocess.run(["curl", "-s", "--max-time", "25", url,
                          "-H", "Referer: https://gu.qq.com/"],
                         capture_output=True).stdout
    try:
        d = json.loads(raw)["data"][sym]
        return d.get("qfqday") or d.get("day") or []
    except Exception:
        return []


def fetch_market(refresh: bool = False) -> dict:
    """M 维度：各指数 收盘 vs MA20/50/200 + MA50 斜率。按日缓存。"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache = os.path.join(CACHE_DIR, f"market_{datetime.now():%Y%m%d}.json")
    if os.path.exists(cache) and not refresh:
        with open(cache, encoding="utf-8") as f:
            return json.load(f)
    out = {}
    for name, sym in INDEXES:
        bars = _daily_bars(sym, 300)
        closes = [float(b[2]) for b in bars]
        if len(closes) < 60:
            continue
        ma20 = sum(closes[-20:]) / 20
        ma50 = sum(closes[-50:]) / 50
        ma50_prev = sum(closes[-55:-5]) / 50 if len(closes) >= 55 else ma50
        ma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else None
        out[name] = {
            "close": round(closes[-1], 2),
            "ma20": round(ma20, 2), "ma50": round(ma50, 2),
            "ma200": round(ma200, 2) if ma200 else None,
            "above_ma50": closes[-1] >= ma50,
            "ma50_up": ma50 >= ma50_prev,
            "above_ma20": closes[-1] >= ma20,
            "above_ma200": (closes[-1] >= ma200) if ma200 else None,
            "date": bars[-1][0],
        }
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    return out


def market_verdict(mkt: dict) -> tuple:
    """返回 (等级, 文字)。等级: 2=牛市 / 1=中性 / 0=震荡偏弱 / -1=空仓期。"""
    if not mkt:
        return 1, "❔ 大盘数据获取失败"
    bull = sum(1 for v in mkt.values() if v["above_ma50"] and v["ma50_up"])
    bear = sum(1 for v in mkt.values() if not v["above_ma50"])
    n = len(mkt)
    if bull == n:
        return 2, "🔥 上升趋势 —— 满仓操作"
    if bear == n:
        return -1, "🔴 空仓期 —— 全部指数跌破 50 日线，本清单仅建池不建仓"
    if bear >= n - 1:
        return 0, "⚠️ 震荡偏弱 —— 轻仓，只做最强主线"
    return 1, "✅ 震荡/结构性行情 —— 3-5 成仓位"


# ---------------------------------------------------------------- L 相对强度
def fetch_rps(refresh: bool = False) -> dict:
    """L 维度：全市场涨幅分位（60日 60% + 年初至今 40% 的近似 RPS）。按日缓存。"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache = os.path.join(CACHE_DIR, f"rps_{datetime.now():%Y%m%d}.json")
    if os.path.exists(cache) and not refresh:
        with open(cache, encoding="utf-8") as f:
            return json.load(f)

    print("  [fetch] 全市场相对强度 (60日/年初至今) ...")
    fs = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
    rows, total, pn = [], None, 1
    while pn <= 80:
        url = (f"https://push2delay.eastmoney.com/api/qt/clist/get?pn={pn}&pz=100&po=1&np=1"
               f"&fltt=2&invt=2&fid=f12&fs={fs}&fields=f12,f24,f25")
        raw = subprocess.run(["curl", "-s", "--max-time", "30", url,
                              "-H", "Referer: https://quote.eastmoney.com/"],
                             capture_output=True).stdout
        try:
            data = json.loads(raw)["data"]
        except Exception:
            break
        if total is None:
            total = data.get("total") or 0
        diff = data.get("diff") or []
        if not diff:
            break
        rows += diff
        if total and len(rows) >= total:
            break
        pn += 1

    scored = []
    for r in rows:
        a, b = r.get("f24"), r.get("f25")
        if a is None or b is None or a == "-" or b == "-":
            continue
        scored.append((r["f12"], float(a) * 0.6 + float(b) * 0.4))
    scored.sort(key=lambda x: x[1])
    n = len(scored)
    rps = {code: round((i + 1) / n * 100, 1) for i, (code, _) in enumerate(scored)}
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(rps, f, ensure_ascii=False)
    print(f"  [fetch] RPS 基准 {n} 只，中位涨幅 {scored[n // 2][1]:.1f}%")
    return rps


# ---------------------------------------------------------------- I 机构认同
def fetch_inst(codes, refresh: bool = False) -> dict:
    """I 维度：公募基金/北向持股占总股本比（取最近一次完整披露期）。按日缓存。"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache = os.path.join(CACHE_DIR, "inst_holdings.json")
    data = {}
    if os.path.exists(cache) and not refresh:
        with open(cache, encoding="utf-8") as f:
            data = json.load(f)
    todo = [c for c in dict.fromkeys(codes) if c not in data]
    if todo:
        print(f"  [fetch] 机构持股 {len(todo)} 只 (东财机构持仓) ...")
    for c in todo:
        sfx = ".SH" if c.startswith("6") else (".BJ" if c.startswith(("4", "8", "92")) else ".SZ")
        url = ("https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_MAINDATA_MAIN_POSITIONDETAILS"
               "&columns=REPORT_DATE,HOLDER_NAME,ORG_TYPE,TOTAL_SHARES,TOTAL_SHARES_RATIO"
               f"&filter=(SECURITY_CODE%3D%22{c}%22)&pageNumber=1&pageSize=500"
               "&sortColumns=REPORT_DATE&sortTypes=-1&source=WEB&client=WEB")
        raw = subprocess.run(["curl", "-s", "--max-time", "30", url],
                             capture_output=True, text=True).stdout
        try:
            rows = json.loads(raw)["result"]["data"]
        except Exception:
            data[c] = None
            continue

        by_period = {}
        for r in rows:
            by_period.setdefault(r["REPORT_DATE"][:10], []).append(r)
        # 一季报/三季报只披露前十大，会低估；优先取基金家数>=10 的最近一期
        full = [(d, rs) for d, rs in by_period.items()
                if sum(1 for x in rs if x.get("ORG_TYPE") == "基金") >= 10]
        period, rs = (max(full) if full else max(by_period.items()))
        fund = sum((x["TOTAL_SHARES"] or 0) for x in rs if x.get("ORG_TYPE") == "基金")
        other_inst = sum((x["TOTAL_SHARES"] or 0) for x in rs
                         if x.get("ORG_TYPE") in ("保险", "QFII", "券商"))
        north = sum((x["TOTAL_SHARES"] or 0) for x in rs
                    if "香港中央结算" in (x.get("HOLDER_NAME") or ""))
        fund_pct = sum((x.get("TOTAL_SHARES_RATIO") or 0) for x in rs if x.get("ORG_TYPE") == "基金")
        inst_pct = sum((x.get("TOTAL_SHARES_RATIO") or 0) for x in rs
                       if x.get("ORG_TYPE") in ("基金", "保险", "QFII", "券商")
                       or "香港中央结算" in (x.get("HOLDER_NAME") or ""))
        data[c] = {"period": period, "fund_sh": fund, "inst_sh": other_inst, "north_sh": north,
                   "n_fund": sum(1 for x in rs if x.get("ORG_TYPE") == "基金"),
                   "fund_pct": round(fund_pct, 3), "inst_pct": round(inst_pct, 3)}
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return data


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


def tenbagger_score(r) -> float:
    """十倍股综合得分（C/A/S 财务 + L/I 市场认同）。"""
    s = 0.0
    s += min(r["rev_yoy"], 100) / 10          # C 当前增速 0-10
    s += min(r.get("_rev_avg", 0), 60) / 6    # A 连续N年增速均值 0-10
    s += min(r["gross_margin"], 60) / 6       # 生意质量 0-10
    s += min(r["cash_ratio"], 1.5) * 2        # 现金流 0-3
    s += min(r["roe"], 25) / 4                # ROE 0-6.25
    if pd.notna(r["扣非占比"]) and r["扣非占比"] >= 0.9:
        s += 4                                # 扣非干净
    if pd.notna(r.get("rps")):
        s += r["rps"] / 10                    # L 相对强度 0-10
    if pd.notna(r.get("fund_pct")):
        s += min(r["fund_pct"], 8)            # I 公募持股% 0-8（8%以上封顶）
    if pd.notna(r["mcap_yi"]):
        s -= r["mcap_yi"] / 25                # 市值越小越加分
    if pd.notna(r.get("gain_low")):
        s -= r["gain_low"] * 2                # 软排序：已涨越多越靠后
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
    p.add_argument("--grow-years", type=int, default=3,
                   help="连续 N 年(含当期)营收&净利从未下滑，默认3")
    p.add_argument("--min-grow", type=float, default=0,
                   help="每年营收增速下限(%%，默认0=只要不下降)")
    p.add_argument("--accelerating", action="store_true",
                   help="额外要求营收增速逐年抬升(加速型，可选)")
    p.add_argument("--accel-gain", type=float, default=20, help="加速首末差下限(百分点)")
    p.add_argument("--mcap-min", type=float, default=30, help="总市值下限(亿)")
    p.add_argument("--mcap-max", type=float, default=80, help="总市值上限(亿)")
    p.add_argument("--min-listing", type=float, default=1, help="上市满 N 年")
    p.add_argument("--new-listing", type=float, default=5, help="上市不超过 N 年")
    p.add_argument("--max-gain-low", type=float, default=None,
                   help="可选起点过滤：从低点涨幅≤N倍(如1.0=还没翻倍)，默认不过滤")
    p.add_argument("--top", type=int, default=30, help="打印条数")
    p.add_argument("--no-kf", action="store_true", help="跳过扣非校验")
    p.add_argument("--require-rs", type=float, default=0,
                   help="L: 近似RPS 下限(0=不过滤，80=CAN SLIM 及格线)")
    p.add_argument("--require-inst", type=float, default=0,
                   help="I: 公募基金持股占总股本下限(%%，0=不过滤，1.0=有一定机构认同)")
    p.add_argument("--strict-market", action="store_true",
                   help="M: 空仓期直接退出，不输出候选")
    p.add_argument("--refresh", action="store_true", help="忽略缓存重拉")
    a = p.parse_args()

    date = a.date or detect_period()
    period = PERIOD_NAME.get(date[4:8], date)
    today = datetime.now().strftime("%Y-%m-%d")

    print(f"\n{'=' * 96}")
    print(f" 十倍股起点每日筛选  {today}    数据期: {date[:4]}年{period}    市值: {today}")
    mode = f"连续{a.grow_years + 1}年营收&净利从未下滑"
    if a.accelerating:
        mode += " + 加速"
    pos = f" | 从低点≤{a.max_gain_low * 100:.0f}%" if a.max_gain_low is not None else ""
    print(f" 画像: 市值{a.mcap_min:.0f}-{a.mcap_max:.0f}亿 | 上市{a.min_listing:.0f}-{a.new_listing:.0f}年 | {mode} | 双增≥{a.rev:.0f}%{pos}")
    print(f"{'=' * 96}")

    # ---------- ⓪ M 大盘方向（一票否决式门控） ----------
    mkt = fetch_market(a.refresh)
    mlevel, mtext = market_verdict(mkt)
    for name, v in mkt.items():
        flag = "🟢" if (v["above_ma50"] and v["ma50_up"]) else ("🔴" if not v["above_ma50"] else "🟡")
        slope = "↑" if v["ma50_up"] else "↓"
        m200 = f"{v['ma200']:.0f}" if v["ma200"] else "-"
        print(f"  {flag} {name:<8} {v['close']:>9.2f}  MA20 {v['ma20']:>9.2f}  "
              f"MA50 {v['ma50']:>9.2f}{slope}  MA200 {m200:>9}  距50日线 {(v['close'] / v['ma50'] - 1) * 100:>+6.1f}%")
    print(f"\n  【M 大盘方向】{mtext}")
    if mlevel == -1:
        print("  " + "!" * 88)
        print("  ⚠️  当前处于空仓期：历史规律下，此时买入最优质的公司也可能先跌 30-50%。")
        print("  ⚠️  下面输出的候选仅用于【建池观察】，不是建仓建议。等指数重回 50 日线再看。")
        print("  " + "!" * 88)
        if a.strict_market:
            print("\n  --strict-market 已开启，空仓期退出。")
            return 0
    print(f"{'=' * 96}")

    # ---------- ① 双增 ----------
    df = fetch_yjbb(date, a.refresh)
    d = screen(df, a.rev, a.profit, 1.0, 2000, False, False)
    if d.empty:
        print("没有符合条件的标的。")
        return 0

    step = 9
    cur_year = int(date[:4])

    # ---------- ② 持续增长：连续 N 年营收&净利从未下滑 ----------
    hist_years = list(range(cur_year - 1, cur_year - 1 - a.grow_years, -1))
    for y in sorted(hist_years, reverse=True):
        hist = fetch_yjbb(f"{y}1231", a.refresh)
        d = d.copy()
        d[f"rev_yoy_{y}"] = d["code"].map(dict(zip(hist["code"], pd.to_numeric(hist["rev_yoy"], errors="coerce"))))
        d[f"np_yoy_{y}"] = d["code"].map(dict(zip(hist["code"], pd.to_numeric(hist["profit_yoy"], errors="coerce"))))

    rev_cols = [f"rev_yoy_{y}" for y in sorted(hist_years)] + ["rev_yoy"]
    np_cols = [f"np_yoy_{y}" for y in sorted(hist_years)] + ["profit_yoy"]
    for c in rev_cols + np_cols:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d[d[rev_cols + np_cols].notna().all(axis=1)]
    mask = pd.Series(True, index=d.index)
    for c in rev_cols:
        mask &= d[c] >= a.min_grow
    for c in np_cols:
        mask &= d[c] > 0
    d = d[mask]
    d["_rev_avg"] = d[rev_cols].mean(axis=1)
    d["_gain"] = d[rev_cols[-1]] - d[rev_cols[0]]
    print(f"  {chr(0x2460 + step - 1)} 连续{a.grow_years + 1}年营收≥{a.min_grow:.0f}%且净利>0 : {len(d):>5}")
    step += 1

    if a.accelerating:
        mask = pd.Series(True, index=d.index)
        for lo, hi in zip(rev_cols, rev_cols[1:]):
            mask &= d[hi] >= d[lo]
        d = d[mask & (d["_gain"] >= a.accel_gain)]
        print(f"  {chr(0x2460 + step - 1)} 加速型(增速逐年抬升)         : {len(d):>5}")
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

    # ---------- ⑤ 低点位置（展示 + 可选过滤） ----------
    if not d.empty:
        d = d.copy()
        d["gain_low"] = d["code"].map(fetch_gain_from_low(d["code"].tolist(), a.refresh))
        d = d[d["gain_low"].notna()]
        if a.max_gain_low is not None:
            before = len(d)
            d = d[d["gain_low"] <= a.max_gain_low]
            print(f"  {chr(0x2460 + step - 1)} 从低点涨幅 ≤ {a.max_gain_low * 100:.0f}% (可选起点) : {before} → {len(d):>5}")
            step += 1

    if d.empty:
        print("\n没有符合条件的标的。")
        return 0

    # ---------- ⑤ 扣非校验 ----------
    if not a.no_kf:
        cache = load_cache()
        print(f"  扣非校验 {len(d)} 家(缓存{len(cache)}条) ...")
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

    if d.empty:
        print("\n没有符合条件的标的。")
        return 0

    # ---------- ⑥ L 相对强度 / I 机构认同 ----------
    d = d.copy()
    d["rps"] = d["code"].map(fetch_rps(a.refresh))
    inst = fetch_inst(d["code"].tolist(), a.refresh)
    d["fund_sh"] = d["code"].map(lambda c: (inst.get(c) or {}).get("fund_sh"))
    d["inst_sh"] = d["code"].map(lambda c: (inst.get(c) or {}).get("inst_sh"))
    d["north_sh"] = d["code"].map(lambda c: (inst.get(c) or {}).get("north_sh"))
    d["n_fund"] = d["code"].map(lambda c: (inst.get(c) or {}).get("n_fund"))
    d["inst_period"] = d["code"].map(lambda c: (inst.get(c) or {}).get("period"))
    d["fund_pct"] = d["code"].map(lambda c: (inst.get(c) or {}).get("fund_pct"))
    d["inst_pct"] = d["code"].map(lambda c: (inst.get(c) or {}).get("inst_pct"))

    d["rps"] = pd.to_numeric(d["rps"], errors="coerce")
    d["fund_pct"] = pd.to_numeric(d["fund_pct"], errors="coerce")

    if a.require_rs > 0:
        before = len(d)
        d = d[d["rps"].notna() & (d["rps"] >= a.require_rs)]
        print(f"  {chr(0x2460 + step - 1)} L 相对强度 RPS ≥ {a.require_rs:.0f}      : {before} → {len(d):>5}")
        step += 1
    if a.require_inst > 0:
        before = len(d)
        d = d[d["fund_pct"].notna() & (d["fund_pct"] >= a.require_inst)]
        print(f"  {chr(0x2460 + step - 1)} I 公募持股 ≥ {a.require_inst:.1f}%          : {before} → {len(d):>5}")
        step += 1

    if d.empty:
        print("\n没有符合条件的标的（L/I 过滤后为空 —— 这本身就是一个信号：当前没有'机构认同+相对强势'的标的）。")
        return 0

    # ---------- ⑦ 打分排序 ----------
    d = d.copy()
    for c in ("rev_yoy", "gross_margin", "cash_ratio", "roe", "mcap_yi", "rps", "fund_pct"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["得分"] = d.apply(tenbagger_score, axis=1)
    d = d.sort_values("得分", ascending=False)

    # ---------- ⑧ 与上一交易日对比 ----------
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
    print(f"\n{'=' * 108}\n【十倍股候选】按得分排序（前 {n} 家，共 {len(d)} 家）\n{'=' * 108}")
    head = (pad("代码", 8) + pad("简称", 12) + pad("市值", 6, ">") + pad("营收%", 7, ">")
            + pad("净利%", 8, ">") + pad("毛利", 6, ">") + pad("现金", 6, ">") + " "
            + pad("扣非", 7) + pad("L:RPS", 7, ">") + pad("I:公募%", 8, ">")
            + pad("低点涨", 7, ">") + pad("得分", 6, ">") + "  增速曲线(旧→新)")
    print(head)
    print("-" * 108)
    for _, r in d.head(n).iterrows():
        note = ""
        if is_clean(r):
            note = " ⭐干净"
        if r["code"] in added:
            note += " 🆕新增"
        if pd.notna(r.get("rps")):
            note += " 🔥强势" if r["rps"] >= 80 else (" ⚠️落后" if r["rps"] < 50 else "")
        if pd.notna(r.get("fund_pct")) and r["fund_pct"] < 0.5:
            note += " ❗机构缺失"
        print(pad(r["code"], 8) + pad(r["name"], 12) + pad(f"{r['mcap_yi']:.0f}", 6, ">")
              + pad(f"{r['rev_yoy']:.0f}", 7, ">") + pad(f"{r['profit_yoy']:.0f}", 8, ">")
              + pad(f"{r['gross_margin']:.0f}", 6, ">") + pad(f"{r['cash_ratio']:.2f}", 6, ">") + " "
              + pad(flag_扣非(r["kf_pct"], r["扣非占比"]), 7)
              + pad(f"{r['rps']:.0f}" if pd.notna(r["rps"]) else "-", 7, ">")
              + pad(f"{r['fund_pct']:.2f}" if pd.notna(r["fund_pct"]) else "-", 8, ">")
              + pad(f"{r['gain_low']*100:.0f}%" if pd.notna(r["gain_low"]) else "-", 7, ">")
              + pad(f"{r['得分']:.1f}", 6, ">") + "  " + curve_str(r, rev_cols) + note)

    if prev:
        print(f"\n  🆕 今日新增 {len(added)} 家: " + ("、".join(sorted(added)) if added else "无"))
        print(f"  ⬇️ 今日掉出 {len(dropped)} 家: " + ("、".join(sorted(dropped)) if dropped else "无"))

    print(f"\n  → 已存: {outfile}")
    if mlevel == -1:
        print(f"  ⛔ M=空仓期：本清单仅供建池，不建仓。等指数重回 50 日线再启动。")
    else:
        print(f"  ✅ M 允许操作：从「RPS≥80 + 公募持股≥1% + ⭐干净」的标的里选，做深度分析后进池。")
    print(f"  ⚠️ 仅为筛选结果，不构成投资建议。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
