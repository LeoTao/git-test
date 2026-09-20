#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
财报季关键词扫描器 —— 巨潮资讯全文检索

实现 .github/skills/earnings-season-keyword-screening 的第二步：
把「供不应求/高景气/供给偏紧/需求旺盛/涨价/超预期」等 7 个关键表述，
变成对中报季全部公告的全文检索，找出公司自己"喊出来"的信号。

用法:
    python3 keyword_scan.py                          # 扫描 2026 中报季
    python3 keyword_scan.py --sdate 2026-07-01 --edate 2026-09-20
    python3 keyword_scan.py --cross out/h1_20260630_double40.csv
        # 与双40%候选池取交集 —— 业绩高增长 ∩ 关键词命中

输出:
    out/keyword_hits_{sdate}_{edate}.csv   全部命中记录
    out/keyword_cross_{...}.csv            交集结果（重点看这个）
"""

import argparse
import json
import os
import time

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "out")
CACHE_DIR = os.path.join(HERE, ".cache")
API = "http://www.cninfo.com.cn/new/fulltextSearch/full"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "http://www.cninfo.com.cn/new/fulltextSearch",
}

# 关键词 = 技能里的 7 个关键表述 + 权重（5=最强证据）
KEYWORDS: dict[str, int] = {
    "供不应求": 5,
    "订单饱满": 4,
    "产能利用率": 4,
    "满产": 4,
    "供需偏紧": 4,
    "供给偏紧": 4,
    "需求旺盛": 4,
    "高景气": 5,
    "价格中枢": 4,
    "产品涨价": 4,
    "提价": 3,
    "超预期": 3,
    "量价齐升": 4,
    "在手订单": 4,
    "产能爬坡": 3,
}

# 公告类型判定（技能强调：财报正文 > 交流纪要 > 调研纪要）
DOC_TYPES = [
    ("半年度报告", "📕中报正文"),
    ("业绩预告", "📗业绩预告"),
    ("业绩快报", "📗业绩快报"),
    ("投资者关系活动记录", "🎙调研纪要"),
    ("调研", "🎙调研纪要"),
    ("季度报告", "📘季报"),
    ("年报", "📙年报"),
]


def doc_type(title: str) -> str:
    for kw, tag in DOC_TYPES:
        if kw in title:
            return tag
    return "📄其他"


def search(keyword: str, sdate: str, edate: str, max_pages: int = 12,
           page_size: int = 30, sleep: float = 0.6) -> list[dict]:
    """分页拉取某个关键词的全部命中公告。"""
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        p = {
            "searchkey": keyword, "sdate": sdate, "edate": edate,
            "isfulltext": "true", "sortName": "nothing", "sortType": "desc",
            "pageNum": page, "pageSize": page_size, "type": "",
        }
        try:
            r = requests.post(API, data=p, headers=HEADERS, timeout=25)
            d = r.json()
        except Exception as e:  # noqa: BLE001
            print(f"    ! {keyword} p{page} 失败: {str(e)[:50]}")
            break
        anns = d.get("announcements") or []
        if not anns:
            break
        for a in anns:
            code = str(a.get("secCode") or "")
            title = a.get("announcementTitle") or ""
            out.append({
                "code": code,
                "name": a.get("secName"),
                "keyword": keyword,
                "weight": KEYWORDS[keyword],
                "title": title,
                "doc_type": doc_type(title),
                "date": pd.to_datetime(a.get("announcementTime"), unit="ms",
                                       errors="coerce").strftime("%Y-%m-%d")
                if a.get("announcementTime") else "",
            })
        if not d.get("hasMore"):
            break
        time.sleep(sleep)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sdate", default="2026-07-01", help="起始日（中报季）")
    ap.add_argument("--edate", default="2026-09-20", help="结束日")
    ap.add_argument("--cross", default=os.path.join(OUT_DIR, "h1_20260630_double40.csv"),
                    help="要取交集的候选池 CSV")
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)
    tag = f"{a.sdate}_{a.edate}"
    cache = os.path.join(CACHE_DIR, f"kw_{tag}.json")

    if os.path.exists(cache) and not a.refresh:
        print(f"[cache] {cache}")
        rows = json.load(open(cache, encoding="utf-8"))
    else:
        print(f"\n{'='*96}\n巨潮全文检索 {a.sdate} ~ {a.edate} —— {len(KEYWORDS)} 个关键词\n{'='*96}")
        rows = []
        for i, kw in enumerate(KEYWORDS, 1):
            hits = search(kw, a.sdate, a.edate)
            rows.extend(hits)
            print(f"  {i:>2}/{len(KEYWORDS)} {kw:<10} 命中 {len(hits):>5} 条")
        json.dump(rows, open(cache, "w", encoding="utf-8"), ensure_ascii=False)

    df = pd.DataFrame(rows).drop_duplicates(subset=["code", "keyword", "title"])
    allf = os.path.join(OUT_DIR, f"keyword_hits_{tag}.csv")
    df.to_csv(allf, index=False, encoding="utf-8-sig")
    print(f"\n  原始命中 {len(df)} 条 → {allf}")

    if not os.path.exists(a.cross):
        print(f"  未找到候选池 {a.cross}，跳过交集（先跑 screen_h1.py）")
        return 0

    pool = pd.read_csv(a.cross, dtype={"code": str})
    pool["code"] = pool["code"].str.zfill(6)
    df["code"] = df["code"].str.zfill(6)
    hit = df[df["code"].isin(set(pool["code"]))]

    # 一家公司一个信号分：各关键词权重之和（同一关键词多份文件只算一次）
    uniq = hit.drop_duplicates(subset=["code", "keyword"])
    score = (uniq.groupby("code")
                  .agg(信号分=("weight", "sum"),
                       命中词=("keyword", lambda s: "、".join(sorted(set(s)))),
                       文件数=("title", "nunique"))
                  .reset_index())

    cross = pool.merge(score, on="code", how="inner")
    cross = cross.sort_values(["信号分", "profit_yi"], ascending=[False, False])
    outf = os.path.join(OUT_DIR, f"keyword_cross_{tag}.csv")
    cross.to_csv(outf, index=False, encoding="utf-8-sig")

    print(f"\n{'='*118}")
    print(f"【交集】双40%高增长 ∩ 关键词命中 —— {len(cross)} 家（候选池 {len(pool)} 家）")
    print(f"{'='*118}")

    from screen_h1 import pad  # 复用中文对齐

    print(pad("代码", 8) + pad("简称", 12) + pad("行业", 14) + pad("营收%", 9, ">")
          + pad("净利%", 10, ">") + pad("净利(亿)", 10, ">") + pad("信号分", 8, ">")
          + " 命中关键词")
    print("-" * 118)
    for _, r in cross.iterrows():
        print(pad(r["code"], 8) + pad(r["name"], 12) + pad(r["industry"], 14)
              + pad(f"{r['rev_yoy']:.0f}%", 9, ">") + pad(f"{r['profit_yoy']:.0f}%", 10, ">")
              + pad(f"{r['profit_yi']:.1f}", 10, ">") + pad(int(r["信号分"]), 8, ">")
              + " " + str(r["命中词"]))

    print(f"\n  → 交集已存: {outf}")
    print("\n⚠️  关键词命中 ≠ 投资机会。下一步：逐家看命中出现在哪份文件、第几个季度、"
          "行业数据是否佐证（见 skill 第四步判断清单）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
