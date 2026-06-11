#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全球信息差监控 - 每日信息扫描脚本
==========================================
用法：
  python3 discovery.py scan         每日信息扫描（RSS + 关键词）
  python3 discovery.py source list  查看信息源列表
  python3 discovery.py source add   添加新信息源
  python3 discovery.py source check 检查信息源有效性
  python3 discovery.py signal       查看最近发现的信号
  python3 discovery.py help         查看帮助

设计理念：
  - 不需要 API key
  - 优先使用 RSS（免费、标准化）
  - 关键词匹配在本地完成
  - 输出简洁，5分钟扫完
"""

import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# ============================================================
# 配置
# ============================================================
BASE_DIR = Path(__file__).parent
SOURCES_FILE = BASE_DIR / "info_sources.json"
DISCOVERIES_FILE = BASE_DIR / "discoveries_log.json"
CACHE_FILE = BASE_DIR / "rss_cache.json"

# 请求头（避免被拒）
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "StockDiscovery/1.0 (Individual Investor Research Tool)"
}

# ============================================================
# 工具函数
# ============================================================

def load_sources():
    """加载信息源配置"""
    if not SOURCES_FILE.exists():
        print("❌ info_sources.json 不存在")
        sys.exit(1)
    with open(SOURCES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_discoveries():
    """加载历史发现"""
    if not DISCOVERIES_FILE.exists():
        return []
    with open(DISCOVERIES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_discoveries(log):
    """保存发现记录"""
    with open(DISCOVERIES_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def load_cache():
    """加载 RSS 缓存（避免重复拉取）"""
    if not CACHE_FILE.exists():
        return {}
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_cache(cache):
    """保存 RSS 缓存"""
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def print_header(title):
    """打印标题"""
    print()
    print("=" * 64)
    print(f"  {title}")
    print("=" * 64)


def print_sep():
    print("-" * 64)


# ============================================================
# RSS 抓取和关键词扫描
# ============================================================

def fetch_rss(url, timeout=10):
    """抓取 RSS feed，返回条目列表 [{title, link, summary, published}]"""
    try:
        req = Request(url, headers=HEADERS)
        with urlopen(req, timeout=timeout) as resp:
            content = resp.read()
        root = ET.fromstring(content)

        items = []
        # 兼容 RSS 2.0 和 Atom
        ns = {"atom": "http://www.w3.org/2005/Atom"}

        # RSS 2.0
        for item in root.iter("item"):
            title = item.findtext("title", "")
            link = item.findtext("link", "")
            summary = item.findtext("description", "")
            pub_date = item.findtext("pubDate", "")
            items.append({
                "title": title.strip(),
                "link": link.strip(),
                "summary": _strip_html(summary.strip()),
                "published": pub_date.strip(),
            })

        # Atom
        if not items:
            for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
                title = entry.findtext("{http://www.w3.org/2005/Atom}title", "")
                link_el = entry.find("{http://www.w3.org/2005/Atom}link")
                link = link_el.get("href", "") if link_el is not None else ""
                summary = entry.findtext("{http://www.w3.org/2005/Atom}summary", "")
                pub_date = entry.findtext("{http://www.w3.org/2005/Atom}updated", "")
                items.append({
                    "title": title.strip(),
                    "link": link.strip(),
                    "summary": _strip_html(summary.strip()),
                    "published": pub_date.strip(),
                })

        return items
    except ET.ParseError as e:
        return []
    except (URLError, HTTPError, OSError) as e:
        return []
    except Exception as e:
        return []


def _strip_html(text):
    """去除 HTML 标签"""
    import re
    return re.sub(r"<[^>]+>", "", text)


def match_keywords(text, keywords):
    """检查文本是否命中关键词（大小写不敏感）"""
    text_lower = text.lower()
    matched = []
    for kw in keywords:
        if kw.lower() in text_lower:
            matched.append(kw)
    return matched


def scan_source(source, cache):
    """扫描单个信息源，返回命中条目列表"""
    source_id = source["id"]
    feed_url = source.get("feed", "")
    keywords = source.get("keywords", [])

    if not feed_url or not keywords:
        return []

    # 检查缓存（同一天不重复拉取）
    today = datetime.now().strftime("%Y-%m-%d")
    if source_id in cache:
        cached_date = cache[source_id].get("date", "")
        if cached_date == today:
            return cache[source_id].get("hits", [])

    # 抓取 RSS
    items = fetch_rss(feed_url)
    if not items:
        return []

    # 扫描关键词
    hits = []
    for item in items:
        text = f"{item['title']} {item['summary']}"
        matched = match_keywords(text, keywords)
        if matched:
            hits.append({
                "source_id": source_id,
                "source_name": source["name"],
                "sector": source.get("sector", ""),
                "title": item["title"],
                "link": item["link"],
                "summary": item["summary"][:300],
                "published": item["published"],
                "matched_keywords": matched,
                "priority": _judge_priority(matched, source.get("priority", 3)),
                "scanned_at": today,
            })

    # 更新缓存
    cache[source_id] = {"date": today, "hits": hits}
    return hits


def _judge_priority(matched_keywords, source_priority):
    """根据命中关键词判断优先级"""
    P1_KEYWORDS = [
        "shortage", "supply crunch", "force majeure", "plant shutdown",
        "fda approval", "breakthrough device",
        "export ban", "sanction"
    ]
    P2_KEYWORDS = [
        "demand surge", "record orders", "backlog",
        "capacity expansion", "price hike", "supply tight"
    ]

    for kw in matched_keywords:
        if any(p1 in kw.lower() for p1 in P1_KEYWORDS):
            return "🔴 P1"
    for kw in matched_keywords:
        if any(p2 in kw.lower() for p2 in P2_KEYWORDS):
            return "🟡 P2"
    return "🟢 P3"


# ============================================================
# 命令: scan —— 每日信息扫描
# ============================================================

def cmd_scan():
    """每日信息扫描主命令"""
    print_header(f"🌍 全球信息差扫描 - {datetime.now().strftime('%Y-%m-%d %A')}")
    print()
    print("  理念：英文行业期刊/贸易媒体 → 中文财经媒体")
    print("        信息传播时间差通常 2-8 周，这就是你的窗口。")
    print()

    data = load_sources()
    sources = data.get("sources", {})
    cache = load_cache()

    all_hits = []
    total_sources = 0
    ok_sources = 0

    # 只扫描有 RSS feed 的源
    scan_categories = ["行业贸易期刊", "英文财经深度"]
    for category in scan_categories:
        cat_sources = sources.get(category, [])
        for src in cat_sources:
            total_sources += 1
            feed = src.get("feed", "")
            if not feed:
                continue

            print(f"  🔍 扫描: {src['name']} ...", end=" ")
            hits = scan_source(src, cache)
            if hits:
                print(f"⚡ {len(hits)} 条命中!")
                all_hits.extend(hits)
                ok_sources += 1
            else:
                print("—")
            time.sleep(0.3)  # 礼貌延迟

    save_cache(cache)

    # 结果展示
    print_sep()
    print(f"📊 扫描结果: {ok_sources}/{total_sources} 源可用，发现 {len(all_hits)} 条信号")
    print()

    if not all_hits:
        print("  😴 今日无新信号。")
        print()
        print("  💡 建议：")
        print("     1. 手动检查 Twitter List / Reddit")
        print("     2. 看一下 Freightos Baltic Index 是否有异动")
        print("     3. 如果连续 3 天无信号，你的信息源可能需要更新")
        print()
        return

    # 按优先级排序
    priority_order = {"🔴 P1": 0, "🟡 P2": 1, "🟢 P3": 2}
    all_hits.sort(key=lambda h: (priority_order.get(h["priority"], 99), h["source_name"]))

    # 展示信号
    print("  📡 发现的信号（按优先级排列）：")
    print()
    for i, hit in enumerate(all_hits, 1):
        p_icon = hit["priority"]
        sector = hit.get("sector", "")
        print(f"  [{i}] {p_icon} | {sector}")
        print(f"      来源: {hit['source_name']}")
        print(f"      标题: {hit['title'][:100]}")
        print(f"      命中: {', '.join(hit['matched_keywords'])}")
        if hit.get("link"):
            print(f"      链接: {hit['link'][:80]}")
        print()

    # 保存到发现日志
    if all_hits:
        log = load_discoveries()
        for hit in all_hits:
            # 避免重复记录
            is_dup = any(
                l.get("title") == hit["title"]
                and l.get("source_id") == hit["source_id"]
                for l in log
            )
            if not is_dup:
                log.append(hit)
        # 只保留最近 500 条
        save_discoveries(log[-500:])
        print(f"  💾 已保存到 discoveries_log.json（共 {len(log)} 条历史记录）")

    # 行动建议
    print_sep()
    print("  🎯 现在该做什么：")
    print()
    print("     1. 对 P1/P2 信号，打开链接读原文（5 分钟）")
    print("     2. 做 A 股映射：这个行业 → 哪些 A 股公司受益？")
    print("     3. 检查观察池：我的 watchlist 里有相关标的吗？")
    print("     4. 如有发现，录入系统：python3 tracker.py update")
    print()
    print("  🗺️  行业映射参考（info_sources.json → sector_mapping）：")
    mapping = data.get("sector_mapping", {})
    for eng, a_stock in list(mapping.items())[:5]:
        print(f"     {eng} → A股: {', '.join(a_stock)}")
    print(f"     ...（共 {len(mapping)} 条映射）")
    print()


# ============================================================
# 命令: source —— 管理信息源
# ============================================================

def cmd_source_list():
    """列出所有信息源"""
    data = load_sources()
    sources = data.get("sources", {})

    print_header("📡 信息源列表")
    for category, src_list in sources.items():
        print(f"\n  [{category}] ({len(src_list)} 个源)")
        print(f"  {'名称':<30} {'行业':<20} {'优先级':<8} {'监控'}")
        print(f"  {'-'*60}")
        for src in src_list:
            name = src["name"][:28]
            sector = src.get("sector", "")[:18]
            priority = "⭐" * src.get("priority", 1)
            monitor = src.get("monitor", "?")
            print(f"  {name:<30} {sector:<20} {priority:<8} {monitor}")
    print()


def cmd_source_add():
    """交互式添加信息源"""
    print_header("➕ 添加信息源")
    print()
    name = input("  源名称: ").strip()
    url = input("  网址: ").strip()
    feed = input("  RSS feed URL（可选）: ").strip()
    sector = input("  行业（如 半导体/医疗器械/新能源）: ").strip()
    priority_str = input("  优先级 1-5（默认 3）: ").strip()
    try:
        priority = int(priority_str) if priority_str else 3
    except ValueError:
        priority = 3
    keywords_str = input("  监控关键词（逗号分隔，英文）: ").strip()
    keywords = [k.strip() for k in keywords_str.split(",") if k.strip()] if keywords_str else []

    category = input("  类别 [行业贸易期刊/英文财经深度/社交媒体与论坛/硬数据监控]: ").strip()
    if not category:
        category = "行业贸易期刊"

    new_src = {
        "id": f"custom-{int(time.time())}",
        "name": name,
        "url": url,
        "feed": feed if feed else None,
        "sector": sector,
        "priority": priority,
        "why": "用户自定义",
        "language": "en",
        "monitor": "rss" if feed else "manual",
        "keywords": keywords,
    }

    data = load_sources()
    if category not in data["sources"]:
        data["sources"][category] = []
    data["sources"][category].append(new_src)

    with open(SOURCES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n  ✅ 已添加: {name} → {category}")
    print(f"  💡 运行 'python3 discovery.py source list' 查看")


def cmd_source_check():
    """检查所有 RSS 源的有效性"""
    data = load_sources()
    print_header("🔧 信息源有效性检查")
    print()

    for category, src_list in data["sources"].items():
        for src in src_list:
            feed = src.get("feed", "")
            if not feed:
                continue
            print(f"  检查 {src['name']} ...", end=" ")
            items = fetch_rss(feed, timeout=8)
            if items:
                print(f"✅ ({len(items)} 条)")
            else:
                print(f"❌ 无法访问")
            time.sleep(0.2)
    print()


# ============================================================
# 命令: signal —— 查看最近发现的信号
# ============================================================

def cmd_signal():
    """查看最近发现的信号"""
    log = load_discoveries()
    if not log:
        print("  📭 暂无发现的信号。先运行 scan。")
        return

    print_header(f"📡 最近发现的信号（共 {len(log)} 条）")
    # 最近 20 条
    recent = log[-20:][::-1]
    for i, hit in enumerate(recent, 1):
        date = hit.get("scanned_at", "?")
        print(f"\n  [{i}] {hit.get('priority','?')} | {date}")
        print(f"      源: {hit.get('source_name','?')}")
        print(f"      标题: {hit.get('title','?')[:100]}")
        print(f"      关键词: {', '.join(hit.get('matched_keywords',[]))}")
    print()


# ============================================================
# 帮助
# ============================================================

def cmd_help():
    print("""
  全球信息差监控工具 —— 命令一览

  python3 discovery.py scan          每日信息扫描（主命令）
  python3 discovery.py source list   查看所有信息源
  python3 discovery.py source add    添加新信息源
  python3 discovery.py source check  检查 RSS 源有效性
  python3 discovery.py signal        查看最近发现的信号
  python3 discovery.py help          显示此帮助

  工作流:
  1. 每天早晨运行 scan
  2. 对 P1/P2 信号做 A 股映射
  3. 将发现录入 tracker.py update
  4. 周末运行 source check 维护信息源
""")


# ============================================================
# 主入口
# ============================================================

def main():
    if len(sys.argv) < 2:
        cmd_help()
        return

    cmd = sys.argv[1]
    sub = sys.argv[2] if len(sys.argv) > 2 else None

    if cmd == "scan":
        cmd_scan()
    elif cmd == "source":
        if sub == "list":
            cmd_source_list()
        elif sub == "add":
            cmd_source_add()
        elif sub == "check":
            cmd_source_check()
        else:
            print("❌ 未知子命令。可用: list / add / check")
    elif cmd == "signal":
        cmd_signal()
    elif cmd in ("help", "-h", "--help"):
        cmd_help()
    else:
        print(f"❌ 未知命令: {cmd}")
        cmd_help()


if __name__ == "__main__":
    main()
