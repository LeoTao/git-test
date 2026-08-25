#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
可转债安道全策略助手
====================
每天一键：全市场筛选可买入的转债 + 监控持仓是否需要卖出

用法：
  python3 cb_tracker.py daily      每日例行（温度 + 买入候选 + 持仓卖出信号）
  python3 cb_tracker.py screen     只看买入候选
  python3 cb_tracker.py monitor    只看持仓卖出信号
  python3 cb_tracker.py status     持仓总览（含盈亏）
  python3 cb_tracker.py buy 代码   记录买入（按当前价，可用 --price 指定）
  python3 cb_tracker.py sell 代码 [价格]   记录卖出并计算盈亏
  python3 cb_tracker.py config     查看当前策略参数
  python3 cb_tracker.py help       帮助

数据源（免登录）：
  东方财富数据中心 RPT_BOND_CB_LIST —— 全市场可转债实时行情
  可选：在 cb_config.json 填入集思录 cookie 后自动切换为集思录全量数据

策略逻辑（安道全模式）：
  买入：低价（<110）+ 低溢价（<30%）+ 排除 ST/低评级/临近到期/小规模/已公告强赎
       按「双低值 = 现价 + 溢价率×100」升序排名
  卖出：现价 ≥130 强赎线 → 卖出
        公告强赎 → 强制卖出
        价格 <90 → 违约风险警报
        双低排名掉出前 N 名 → 轮动提示
"""

import json
import re
import sys
import time
from datetime import datetime, date, timedelta
from pathlib import Path

import requests

# ============================================================
# 配置
# ============================================================
BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "cb_config.json"
PORTFOLIO_FILE = BASE_DIR / "cb_portfolio.json"
SCREEN_LOG_FILE = BASE_DIR / "cb_screen_log.json"
TRADE_LOG_FILE = BASE_DIR / "cb_trade_log.json"
CACHE_FILE = BASE_DIR / "cb_market_cache.json"

EM_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"      # 基础信息表(带行情合并)
# 服务器端实时行情合并参数：字段~类型~代码列~输出列名（与东方财富网页端同款）
QUOTE_COLUMNS = (
    "f2~01~CONVERT_STOCK_CODE~STOCK_PRICE,"      # 正股最新价
    "f23~01~CONVERT_STOCK_CODE~STOCK_PB,"        # 正股PB
    "f235~10~SECURITY_CODE~CONVERT_PRICE,"       # 转股价
    "f236~10~SECURITY_CODE~CONVERT_VALUE,"       # 转股价值
    "f2~10~SECURITY_CODE~PRICE,"                 # 转债最新价
    "f237~10~SECURITY_CODE~PREMIUM,"             # 转股溢价率
    "f239~10~SECURITY_CODE~RESALE_TRIG,"         # 回售触发价
    "f240~10~SECURITY_CODE~REDEEM_TRIG"          # 强赎触发价
)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://data.eastmoney.com/",
}

SINA_QUOTE_URL = "https://hq.sinajs.cn/list={}"
SINA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://finance.sina.com.cn/",
}

# 评级排序：数字越小越好
RATING_ORDER = {
    "AAA": 0, "AA+": 1, "AA": 2, "AA-": 3,
    "A+": 4, "A": 5, "A-": 6,
    "BBB+": 7, "BBB": 8, "BBB-": 9,
    "BB+": 10, "BB": 11, "BB-": 12,
    "B+": 13, "B": 14, "B-": 15,
    "CCC": 16, "CC": 17, "C": 18,
}

DEFAULT_CONFIG = {
    "buy": {
        "max_price": 110,          # 最高买入价
        "max_premium": 30,         # 最高转股溢价率(%)
        "min_scale_yi": 2,         # 最小发行规模(亿)，过滤流动性差的
        "min_remain_years": 0.5,   # 最短剩余年限
        "min_rating": "A-",        # 最低债券评级(排除 BBB+ 及以下)
        "min_ytm": -3,             # 最低到期收益率(%，估算值，-3 表示最多亏3%/年到到期)
        "exclude_st": True,        # 排除 ST 正股
        "top_n": 15,               # 最多展示前 N 名
    },
    "sell": {
        "redeem_target_price": 130,  # 强赎线：到了就卖
        "risk_price": 90,            # 违约警戒线：跌破警报
        "risk_ytm": 8,               # 到期收益率超过该值视为"高收益债"警报
        "high_premium_warn": 15,     # 高价+高溢价杀溢价警报阈值(%)
        "rotate_top_n": 200,         # 双低排名掉出全市场前 N 名 → 轮动提示
    },
    "data": {
        "jisilu_cookie": "",         # 可选：填入集思录登录 cookie 后使用集思录全量数据
        "redemption_price_estimate": 108,  # 到期赎回价估算(多数转债为105~115)
        "timeout": 15,
    },
}


# ============================================================
# 基础工具
# ============================================================

def load_config():
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # 合并缺省键
    for section, kv in DEFAULT_CONFIG.items():
        cfg.setdefault(section, {})
        for k, v in kv.items():
            cfg[section].setdefault(k, v)
    return cfg


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def load_portfolio():
    if not PORTFOLIO_FILE.exists():
        return {"holdings": []}
    with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_portfolio(pf):
    with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
        json.dump(pf, f, ensure_ascii=False, indent=2)


def load_trade_log():
    if not TRADE_LOG_FILE.exists():
        return []
    with open(TRADE_LOG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_trade_log(log):
    with open(TRADE_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def load_screen_log():
    if not SCREEN_LOG_FILE.exists():
        return []
    with open(SCREEN_LOG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_screen_log(log):
    # 只保留最近 30 天
    log = log[-30:]
    with open(SCREEN_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def to_float(v, default=None):
    """安全转 float，None / '-' / 空 均返回 default"""
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("%", "")
    if s in ("", "-", "--", "None", "nan"):
        return default
    try:
        return float(s)
    except ValueError:
        return default


def rating_rank(rating):
    """评级字符串 → 数值排名，未知评级返回 99（最差）"""
    if not rating:
        return 99
    r = str(rating).strip().upper()
    return RATING_ORDER.get(r, 99)


def parse_interest_rates(text):
    """从利率说明文本中解析各年票面利率，如 '第一年0.2%、第二年0.4%...'"""
    if not text:
        return []
    nums = re.findall(r"([\d.]+)\s*%", text)
    return [float(x) for x in nums]


def fmt_pct(v, nd=1):
    return "—" if v is None else f"{v:.{nd}f}%"


def fmt_num(v, nd=2):
    return "—" if v is None else f"{v:.{nd}f}"


def fmt_money(v):
    return "—" if v is None else f"{v:.2f}"


# ============================================================
# 数据获取（东方财富为主，集思录为可选增强）
# ============================================================

def _http_get_json(url, params, tries=3):
    """带重试的 JSON GET，应对偶发限流/断连"""
    last_err = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            time.sleep(1.0 * (i + 1))
    raise RuntimeError(f"请求失败（已重试 {tries} 次）：{last_err}")


def fetch_eastmoney():
    """东方财富数据中心：基础信息表 + 服务器端实时行情合并（免登录，稳定）"""
    items = []
    page = 1
    while page <= 20:
        params = {
            "sortColumns": "PUBLIC_START_DATE",
            "sortTypes": "-1",
            "pageSize": "500",
            "pageNumber": str(page),
            "reportName": "RPT_BOND_CB_LIST",
            "columns": "ALL",
            "quoteColumns": QUOTE_COLUMNS,
            "source": "WEB",
            "client": "WEB",
        }
        j = _http_get_json(EM_URL, params)
        rows = (j.get("result") or {}).get("data") or []
        if not rows:
            break
        items.extend(rows)
        total_pages = int((j.get("result") or {}).get("pages") or 1)
        if page >= total_pages or len(rows) < 500:
            break
        page += 1
        time.sleep(0.3)
    if not items:
        raise RuntimeError("东方财富可转债数据为空")
    return items


# ---------------- 备用源：新浪批量报价 ----------------

def fetch_basic_plain():
    """不带行情合并的基础信息表（供备用数据源使用）"""
    items = []
    page = 1
    while page <= 20:
        params = {
            "sortColumns": "PUBLIC_START_DATE",
            "sortTypes": "-1",
            "pageSize": "500",
            "pageNumber": str(page),
            "reportName": "RPT_BOND_CB_LIST",
            "columns": "ALL",
            "source": "WEB",
            "client": "WEB",
        }
        j = _http_get_json(EM_URL, params)
        rows = (j.get("result") or {}).get("data") or []
        if not rows:
            break
        items.extend(rows)
        total_pages = int((j.get("result") or {}).get("pages") or 1)
        if page >= total_pages or len(rows) < 500:
            break
        page += 1
        time.sleep(0.3)
    return items


def _sina_symbol(secucode):
    """'123284.SZ' → 'sz123284'；北交所等不支持返回 None"""
    if not secucode or "." not in secucode:
        return None
    code, mkt = str(secucode).split(".", 1)
    m = mkt.strip().upper()
    return (m.lower() + code) if m in ("SH", "SZ") else None


def _stock_sina_symbol(stock_code):
    """股票代码 → 新浪符号：6→sh，0/3→sz，4/8/92→bj"""
    if not stock_code:
        return None
    s = str(stock_code).strip()
    if s.startswith("6"):
        return "sh" + s
    if s.startswith(("0", "3")):
        return "sz" + s
    if s.startswith(("4", "8", "92")):
        return "bj" + s
    return None


def _sina_batch_quotes(symbols, chunk=120):
    """批量实时报价，返回 {symbol: {'name','prev_close','price'} 或 None}"""
    out = {}
    symbols = [s for s in symbols if s]
    for i in range(0, len(symbols), chunk):
        batch = symbols[i:i + chunk]
        url = SINA_QUOTE_URL.format(",".join(batch))
        r = None
        for _ in range(2):
            try:
                r = requests.get(url, headers=SINA_HEADERS, timeout=15)
                r.encoding = "gbk"
                break
            except Exception:
                time.sleep(1)
        if r is None:
            continue
        for line in r.text.split("\n"):
            m = re.match(r'var hq_str_(\w+)="(.*)";?', line.strip())
            if not m:
                continue
            sym, payload = m.group(1), m.group(2)
            if not payload:
                out[sym] = None
                continue
            f = payload.split(",")
            if len(f) < 4:
                out[sym] = None
                continue
            # 转债与股票前四字段一致：名称,今开,昨收,最新
            out[sym] = {"name": f[0], "prev_close": to_float(f[2]), "price": to_float(f[3])}
        time.sleep(0.2)
    return out


def fetch_sina_data():
    """备用数据源：基础信息表 + 新浪实时报价，自行计算转股价值与溢价率"""
    basics = fetch_basic_plain()
    # 转债报价
    bond_map = {}
    for b in basics:
        sym = _sina_symbol(b.get("SECUCODE"))
        if sym and b.get("SECURITY_CODE"):
            bond_map[sym] = b
    bq = _sina_batch_quotes(list(bond_map.keys()))
    # 正股报价
    stock_syms = set()
    for b in basics:
        s = _stock_sina_symbol(b.get("CONVERT_STOCK_CODE"))
        if s:
            stock_syms.add(s)
    sq = _sina_batch_quotes(list(stock_syms))
    # 组装
    rows = []
    for sym, b in bond_map.items():
        q = bq.get(sym)
        if not q or not q.get("price"):
            continue
        price = q["price"]
        # 转股价：优先已更新值，其次初始转股价
        convert_price = to_float(b.get("CONVERT_STOCK_PRICE")
                                 or b.get("TRANSFER_PRICE")
                                 or b.get("INITIAL_TRANSFER_PRICE"))
        stock_sym = _stock_sina_symbol(b.get("CONVERT_STOCK_CODE"))
        sqq = sq.get(stock_sym) if stock_sym else None
        stock_price = sqq.get("price") if sqq else None
        convert_value = premium = None
        if stock_price and convert_price:
            convert_value = round(stock_price / convert_price * 100, 4)
            premium = round((price / convert_value - 1) * 100, 2)
        rows.append({
            "code": str(b.get("SECURITY_CODE") or "").strip(),
            "name": b.get("SECURITY_NAME_ABBR") or "",
            "price": price,
            "chg": None,
            "premium": premium,
            "convert_value": convert_value,
            "convert_price": convert_price,
            "stock_code": str(b.get("CONVERT_STOCK_CODE") or "").strip(),
            "stock_name": b.get("SECURITY_SHORT_NAME") or "",
            "stock_price": stock_price,
            "pb": to_float(b.get("PBV_RATIO")),
            "rating": b.get("RATING") or "",
            "scale_yi": to_float(b.get("ACTUAL_ISSUE_SCALE")),
            "redeem_trigger": to_float(b.get("REDEEM_TRIG_PRICE")),
            "resale_trigger": to_float(b.get("RESALE_TRIG_PRICE")),
            "redemption_price": None,
            "expire_date": str(b.get("EXPIRE_DATE") or "")[:10],
            "listing_date": str(b.get("LISTING_DATE") or "")[:10],
            "delist_date": str(b.get("DELIST_DATE") or "")[:10],
            "is_redeem": _truthy(b.get("IS_REDEEM")),
            "is_sellback": _truthy(b.get("IS_SELLBACK")),
            "interest_text": b.get("INTEREST_RATE_EXPLAIN") or "",
            "value_date": str(b.get("VALUE_DATE") or "")[:10],
            "bond_expire_years": to_float(b.get("BOND_EXPIRE")),
        })
    if not rows:
        raise RuntimeError("新浪备用数据源未获取到有效行情")
    return rows


def save_market_cache(rows, source):
    """保存最近一次成功行情，作为全部数据源不可用时的兜底"""
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "source": source,
            "rows": rows,
        }, f, ensure_ascii=False)


def load_market_cache():
    if not CACHE_FILE.exists():
        return None
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_eastmoney(r):
    """东方财富原始行（含 quoteColumns 行情列）→ 统一字段"""
    return {
        "code": str(r.get("SECURITY_CODE") or "").strip(),
        "name": r.get("SECURITY_NAME_ABBR") or "",
        "price": to_float(r.get("PRICE")),
        "chg": None,
        "premium": to_float(r.get("PREMIUM")),
        "convert_value": to_float(r.get("CONVERT_VALUE")),
        "convert_price": to_float(r.get("CONVERT_PRICE")),
        "stock_code": str(r.get("CONVERT_STOCK_CODE") or "").strip(),
        "stock_name": r.get("SECURITY_SHORT_NAME") or "",
        "stock_price": to_float(r.get("STOCK_PRICE")),
        "pb": to_float(r.get("STOCK_PB")) or to_float(r.get("PBV_RATIO")),
        "rating": r.get("RATING") or "",
        "scale_yi": to_float(r.get("ACTUAL_ISSUE_SCALE")),
        "redeem_trigger": to_float(r.get("REDEEM_TRIG")),
        "resale_trigger": to_float(r.get("RESALE_TRIG")),
        "redemption_price": None,
        "expire_date": str(r.get("EXPIRE_DATE") or "")[:10],
        "listing_date": str(r.get("LISTING_DATE") or "")[:10],
        "delist_date": str(r.get("DELIST_DATE") or "")[:10],
        "is_redeem": _truthy(r.get("IS_REDEEM")),
        "is_sellback": _truthy(r.get("IS_SELLBACK")),
        "interest_text": r.get("INTEREST_RATE_EXPLAIN") or "",
        "value_date": str(r.get("VALUE_DATE") or "")[:10],
        "bond_expire_years": to_float(r.get("BOND_EXPIRE")),
    }


def fetch_jisilu(cookie):
    """集思录全量数据（需要登录 cookie，返回字段更全）"""
    import akshare as ak
    df = ak.bond_cb_jsl(cookie=cookie)
    return df.to_dict("records")


def normalize_jisilu(r):
    """集思录原始行 → 统一字段"""
    return {
        "code": str(r.get("代码") or "").strip(),
        "name": r.get("转债名称") or "",
        "price": to_float(r.get("现价")),
        "premium": to_float(r.get("转股溢价率")),
        "convert_value": to_float(r.get("转股价值")),
        "convert_price": to_float(r.get("转股价")),
        "stock_code": str(r.get("正股代码") or "").strip(),
        "stock_name": r.get("正股名称") or "",
        "stock_price": to_float(r.get("正股价")),
        "pb": to_float(r.get("正股PB")),
        "rating": r.get("债券评级") or "",
        "scale_yi": to_float(r.get("剩余规模")),
        "redeem_trigger": to_float(r.get("强赎触发价")),
        "resale_trigger": to_float(r.get("回售触发价")),
        "expire_date": str(r.get("到期时间") or "")[:10],
        "listing_date": "",
        "delist_date": "",
        "is_redeem": False,
        "is_sellback": False,
        "interest_text": "",
        "value_date": "",
        "bond_expire_years": None,
        "redemption_price": None,
        "ytm": to_float(r.get("到期税前收益")),
        "double_low": to_float(r.get("双低")),
    }


def _truthy(v):
    if v is None:
        return False
    return str(v).strip().upper() in ("Y", "YES", "1", "TRUE")


def enrich(row, cfg):
    """计算派生字段：双低值、剩余年限、到期收益率估算"""
    today = date.today()
    # 双低值 = 现价 + 溢价率（溢价率已是百分数，如 10.15 代表 10.15%）
    if row.get("double_low") is None and row.get("price") is not None \
            and row.get("premium") is not None:
        row["double_low"] = round(row["price"] + row["premium"], 2)
    # 剩余年限
    if row.get("remain_years") is None and row.get("expire_date"):
        try:
            exp = datetime.strptime(row["expire_date"], "%Y-%m-%d").date()
            row["remain_years"] = round((exp - today).days / 365.25, 2)
        except ValueError:
            row["remain_years"] = None
    # 到期收益率估算（东方财富数据无 YTM 字段，自行估算）
    if row.get("ytm") is None and row.get("price"):
        row["ytm"] = estimate_ytm(row, cfg)
    return row


def estimate_ytm(row, cfg):
    """估算到期税前年化收益率(%)：
    约 = ((到期赎回价 + 剩余各年利息) / 现价 - 1) / 剩余年限 × 100
    到期赎回价优先用接口真实值(f241)，缺失时用配置估算值 108。
    仅为粗估，用于过滤"高收益债"风险，不作精确收益计算。"""
    price = row.get("price")
    remain = row.get("remain_years")
    if not price or not remain or remain <= 0:
        return None
    redeem = row.get("redemption_price") or cfg["data"].get("redemption_price_estimate", 108)
    rates = parse_interest_rates(row.get("interest_text"))
    # 计算剩余可收利息：已过完的年份不再收息
    elapsed = 0.0
    if row.get("value_date"):
        try:
            vd = datetime.strptime(row["value_date"], "%Y-%m-%d").date()
            elapsed = max(0.0, (date.today() - vd).days / 365.25)
        except ValueError:
            pass
    coupon = 0.0
    if rates:
        n = len(rates)
        for i in range(n):
            if i + 1 > elapsed:  # 第 i+1 年尚未过完，剩余可收
                coupon += rates[i]
    total_back = redeem + coupon
    return round(((total_back / price - 1) / remain) * 100, 2)


def fetch_market(cfg):
    """获取全市场转债数据（统一字段 + 派生字段），返回 (rows, source)
    数据源优先级：集思录(cookie) → 东方财富 push2 → 新浪备用 → 本地缓存兜底"""
    cookie = (cfg.get("data") or {}).get("jisilu_cookie", "").strip()
    if cookie:
        try:
            raw = fetch_jisilu(cookie)
            rows = [enrich(normalize_jisilu(r), cfg) for r in raw]
            save_market_cache(rows, "集思录")
            return rows, "集思录"
        except Exception as e:
            print(f"⚠️  集思录数据获取失败({e})，回退到东方财富数据源")
    try:
        raw = fetch_eastmoney()
        rows = [enrich(normalize_eastmoney(r), cfg) for r in raw]
        save_market_cache(rows, "东方财富")
        return rows, "东方财富"
    except Exception as e:
        print(f"⚠️  东方财富行情获取失败({e})，尝试新浪备用源…")
    try:
        raw = fetch_sina_data()
        rows = [enrich(r, cfg) for r in raw]
        save_market_cache(rows, "新浪(备用)")
        return rows, "新浪(备用)"
    except Exception as e:
        print(f"⚠️  新浪备用源失败({e})，尝试本地缓存…")
    cached = load_market_cache()
    if cached and cached.get("rows"):
        print("🚨 全部实时数据源不可用！以下为缓存数据，可能有延迟，仅供持仓监控参考：")
        return cached["rows"], f"缓存({cached.get('time')})"
    raise RuntimeError("全部数据源不可用，且本地无缓存")


def market_tradable(rows):
    """只保留已上市、未摘牌、有有效价格的转债"""
    today = date.today()
    out = []
    for r in rows:
        code = r.get("code") or ""
        if not code or code == "None":
            continue
        # 未上市
        if r.get("listing_date") and r["listing_date"] > str(today):
            continue
        # 已摘牌/已退市
        if r.get("delist_date") and r["delist_date"] <= str(today):
            continue
        price = r.get("price")
        if price is None or price <= 0:
            continue
        out.append(r)
    return out


# ============================================================
# 筛选：买入候选
# ============================================================

def screen_buy(rows, cfg):
    """安道全式低价筛选，返回 (candidates, excluded 统计)"""
    b = cfg["buy"]
    reasons = {}
    today = date.today()

    def reject(code, reason):
        reasons.setdefault(reason, 0)
        reasons[reason] += 1

    cands = []
    for r in rows:
        code = r.get("code", "?")
        price = r.get("price")
        premium = r.get("premium")
        # —— 硬性排除 ——
        if b.get("exclude_st") and "ST" in (r.get("stock_name") or "").upper():
            reject(code, "正股ST"); continue
        if r.get("is_redeem"):
            reject(code, "已公告强赎"); continue
        rating = rating_rank(r.get("rating"))
        min_r = rating_rank(b.get("min_rating"))
        if r.get("rating") and rating > min_r:
            reject(code, "评级过低"); continue
        remain = r.get("remain_years")
        if remain is not None and remain < b.get("min_remain_years"):
            reject(code, "临近到期"); continue
        scale = r.get("scale_yi")
        if scale is not None and scale < b.get("min_scale_yi"):
            reject(code, "规模过小"); continue
        if premium is None:
            reject(code, "无溢价数据"); continue
        # —— 价格条件 ——
        if price > b["max_price"]:
            reject(code, "价格过高"); continue
        if premium > b["max_premium"]:
            reject(code, "溢价过高"); continue
        ytm = r.get("ytm")
        if ytm is not None and ytm < b["min_ytm"]:
            reject(code, "到期收益过低"); continue
        cands.append(r)

    # 按双低值升序
    cands.sort(key=lambda x: (x.get("double_low") if x.get("double_low") is not None else 9999))
    top = cands[: b["top_n"]]
    return top, reasons


# ============================================================
# 监控：持仓卖出信号
# ============================================================

def monitor_holdings(rows, rows_raw, cfg, portfolio):
    """对每只持仓生成卖出/风险信号"""
    s = cfg["sell"]
    # 全量市场（含停牌/未上市），避免持仓误报“已摘牌”
    market = {r["code"]: r for r in rows_raw}
    # 全市场双低排名（用于轮动提示）
    ranked = sorted(
        (r for r in rows if r.get("double_low") is not None),
        key=lambda x: x["double_low"],
    )
    rank_map = {r["code"]: i + 1 for i, r in enumerate(ranked)}

    signals = []
    for h in portfolio["holdings"]:
        code = h["code"]
        r = market.get(code)
        sig = {
            "code": code, "name": h.get("name", ""), "buy_price": h.get("buy_price"),
            "buy_date": h.get("buy_date", ""), "signals": [], "action": "持有",
        }
        if r is None:
            sig["signals"].append("🚨 已不在市场交易（可能已强赎摘牌/退市），请立即核实！")
            sig["action"] = "核实"
            signals.append(sig)
            continue
        price = r.get("price") or 0
        premium = r.get("premium")
        sig["price"] = price
        sig["double_low"] = r.get("double_low")
        sig["dl_rank"] = rank_map.get(code)
        sig["rating"] = r.get("rating")
        sig["ytm"] = r.get("ytm")

        if price <= 0:
            sig["signals"].append("⏸ 当前无行情（停牌/未开盘），关注复盘后的价格变化")
            sig["action"] = "关注"
            signals.append(sig)
            continue
        if r.get("is_redeem"):
            sig["signals"].append(f"🚨 公司已公告强赎！必须在最后交易日前卖出或转股")
            sig["action"] = "立即卖出"
        if price >= s["redeem_target_price"]:
            sig["signals"].append(f"✅ 触及强赎线 {s['redeem_target_price']} 元，按纪律卖出")
            if sig["action"] != "立即卖出":
                sig["action"] = "卖出"
        if price >= s["redeem_target_price"] and premium is not None \
                and premium > s["high_premium_warn"]:
            sig["signals"].append(f"⚠️ 高价+高溢价({fmt_pct(premium)})，有杀溢价风险")
        if price < s["risk_price"]:
            sig["signals"].append(f"🚨 跌破违约警戒线 {s['risk_price']} 元，核查正股是否有雷")
            if sig["action"] == "持有":
                sig["action"] = "核查风险"
        if "ST" in (r.get("stock_name") or "").upper():
            sig["signals"].append("🚨 正股已被 ST，退市违约风险骤升")
            if sig["action"] == "持有":
                sig["action"] = "核查风险"
        rating = rating_rank(r.get("rating"))
        if r.get("rating") and rating > rating_rank("A-"):
            sig["signals"].append(f"⚠️ 评级已降至 {r.get('rating')}，信用风险上升")
        ytm = r.get("ytm")
        remain = r.get("remain_years")
        if ytm is not None and remain is not None and remain >= 0.5 \
                and ytm > s["risk_ytm"]:
            sig["signals"].append(f"🚨 到期收益率 {fmt_pct(ytm)} > {s['risk_ytm']}%，市场已按违约风险定价")
            if sig["action"] == "持有":
                sig["action"] = "核查风险"
        if ytm is not None and remain is not None and remain < 0.5 and ytm > 0:
            sig["signals"].append(f"ℹ️ 剩余 {fmt_num(remain, 2)} 年即将到期，持有到期可获约 {fmt_pct(ytm)}（年化，临近到期会被放大）")
        rank = rank_map.get(code)
        if rank and rank > s["rotate_top_n"] and price <= 115:
            sig["signals"].append(f"🔄 双低排名第 {rank} 名（> {s['rotate_top_n']}），可轮动到更优标的")
        if not sig["signals"]:
            sig["signals"].append("无异常，继续持有")
        signals.append(sig)
    return signals


# ============================================================
# 市场温度
# ============================================================

def market_temperature(rows):
    prices = sorted(r["price"] for r in rows if r.get("price") is not None)
    n = len(prices)
    if n == 0:
        return None
    median = prices[n // 2]
    lows = {
        "<=100": sum(1 for p in prices if p <= 100),
        "<=105": sum(1 for p in prices if p <= 105),
        "<=110": sum(1 for p in prices if p <= 110),
    }
    dls = sorted(r.get("double_low") for r in rows if r.get("double_low") is not None)
    med_dl = dls[len(dls) // 2] if dls else None
    advice = ""
    if median < 110:
        advice = "❄️  寒冬：低价标的充足，可大胆建仓、越跌越买"
    elif median < 120:
        advice = "🌤️  温和：正常持有，按双低轮动"
    else:
        advice = "🔥 盛夏：低价债稀缺、强赎潮临近，只卖不买、逐步减仓"
    return {
        "count": n, "median_price": median, "median_double_low": med_dl,
        "lows": lows, "advice": advice,
    }


# ============================================================
# 输出
# ============================================================

def print_screen(top, temp, source, reasons):
    print("=" * 78)
    print("📊 可转债市场温度")
    print("=" * 78)
    if temp:
        print(f"  数据源：{source}   在交易转债：{temp['count']} 只")
        print(f"  中位价格：{temp['median_price']:.2f}   中位双低：{fmt_num(temp['median_double_low'])}")
        print(f"  ≤100 元：{temp['lows']['<=100']} 只   ≤105 元：{temp['lows']['<=105']} 只   ≤110 元：{temp['lows']['<=110']} 只")
        print(f"  {temp['advice']}")
    print()
    print("=" * 78)
    print(f"🎯 买入候选（双低排名前 {len(top)}）")
    print("=" * 78)
    if not top:
        print("  😔 今日无符合条件标的，宁可空仓等待。")
        if reasons:
            print("  被过滤分布：" + " | ".join(f"{k}×{v}" for k, v in
                    sorted(reasons.items(), key=lambda kv: -kv[1])))
        return
    print("  排名  代码     名称        现价   溢价率  双低    YTM(估) 评级  剩余年限 规模(亿) 正股")
    for i, r in enumerate(top, 1):
        print(f"  {i:<4} {r['code']:<8} {r['name'][:10]:<10} "
              f"{fmt_money(r.get('price')):<6} {fmt_pct(r.get('premium')):<7} "
              f"{fmt_num(r.get('double_low')):<7} {fmt_pct(r.get('ytm')):<7} "
              f"{(r.get('rating') or '—'):<5} {fmt_num(r.get('remain_years'), 1):<8} "
              f"{fmt_num(r.get('scale_yi'), 1):<8} {r.get('stock_name') or '—'}")
    if reasons:
        print(f"\n  📋 被过滤：{' | '.join(f'{k}×{v}' for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]))}")


def print_monitor(signals, sell_cfg):
    print("=" * 78)
    print("🔍 持仓卖出信号")
    print("=" * 78)
    if not signals:
        print("  📭 当前无持仓。用 `python3 cb_tracker.py buy 代码` 记录买入后开始监控。")
        return
    for sig in signals:
        print(f"\n  [{sig['code']}] {sig['name']}   买入 {fmt_money(sig.get('buy_price'))} ({sig.get('buy_date', '—')})"
              f" → 现价 {fmt_money(sig.get('price'))}")
        if sig.get("double_low") is not None:
            print(f"     双低 {fmt_num(sig.get('double_low'))}（全市场第 {sig.get('dl_rank', '—')} 名）  "
                  f"评级 {sig.get('rating') or '—'}  YTM(估) {fmt_pct(sig.get('ytm'))}")
        for m in sig["signals"]:
            print(f"     {m}")
        print(f"     👉 建议：{sig['action']}")


def print_status(rows, portfolio, trade_log):
    print("=" * 78)
    print("💼 持仓总览")
    print("=" * 78)
    holdings = portfolio["holdings"]
    if not holdings:
        print("  📭 当前无持仓。")
    market = {r["code"]: r for r in rows}
    total_cost = total_value = 0.0
    for h in holdings:
        r = market.get(h["code"])
        cost = float(h.get("buy_price") or 0)
        price = r.get("price") if r else None
        pnl = (price - cost) / cost * 100 if (price and cost) else None
        print(f"  {h['code']} {h.get('name','')}  买入价 {cost:.2f}"
              f"  现价 {fmt_money(price)}  盈亏 {fmt_pct(pnl)}  买入日 {h.get('buy_date','—')}")
        total_cost += cost
        total_value += price or 0
    if holdings:
        tp = (total_value - total_cost) / total_cost * 100 if total_cost else None
        print(f"\n  （按 1 手计）总盈亏：{fmt_pct(tp)}")
    # 已实现盈亏
    sells = [t for t in trade_log if t.get("action") == "卖出"]
    if sells:
        print("\n  📜 最近卖出记录：")
        for t in sells[-10:]:
            pnl = t.get("pnl_pct")
            print(f"    {t.get('date','')}  {t.get('code','')} {t.get('name','')}  "
                  f"买 {fmt_money(t.get('buy_price'))} → 卖 {fmt_money(t.get('sell_price'))}  "
                  f"盈亏 {fmt_pct(pnl)}")


# ============================================================
# 持仓操作
# ============================================================

def do_buy(code, price_override, rows, portfolio, trade_log):
    r = next((x for x in rows if x["code"] == code), None)
    if r is None:
        print(f"❌ 未找到代码 {code}（可能未上市或已摘牌）")
        sys.exit(1)
    price = price_override if price_override is not None else r.get("price")
    pf = load_portfolio()
    for h in pf["holdings"]:
        if h["code"] == code:
            print(f"⚠️  {code} 已在持仓中，如需加仓请用 sell 后重新 buy，或直接编辑 cb_portfolio.json")
            sys.exit(1)
    h = {
        "code": code, "name": r.get("name"), "buy_date": str(date.today()),
        "buy_price": price, "note": f"买入时双低 {r.get('double_low')} 溢价 {r.get('premium')}%",
    }
    pf["holdings"].append(h)
    save_portfolio(pf)
    trade_log.append({
        "date": str(date.today()), "action": "买入", "code": code,
        "name": r.get("name"), "buy_price": price, "sell_price": None, "pnl_pct": None,
        "note": h["note"],
    })
    save_trade_log(trade_log)
    print(f"✅ 已记录买入 {code} {r.get('name')} @ {price:.2f}")
    print(f"   买入后即可每天运行 `python3 cb_tracker.py daily` 自动监控卖出信号。")


def do_sell(code, sell_price, rows, portfolio, trade_log):
    pf = load_portfolio()
    r = next((x for x in rows if x["code"] == code), None)
    market_price = r.get("price") if r else None
    price = sell_price if sell_price is not None else market_price
    for i, h in enumerate(pf["holdings"]):
        if h["code"] == code:
            buy_price = float(h.get("buy_price") or 0)
            pnl = (price - buy_price) / buy_price * 100 if (buy_price and price) else None
            pf["holdings"].pop(i)
            save_portfolio(pf)
            trade_log.append({
                "date": str(date.today()), "action": "卖出", "code": code,
                "name": h.get("name"), "buy_price": buy_price, "sell_price": price,
                "pnl_pct": round(pnl, 2) if pnl is not None else None,
                "note": h.get("note", ""),
            })
            save_trade_log(trade_log)
            print(f"✅ 已卖出 {code} {h.get('name')} @ {price:.2f}")
            print(f"   买入价 {buy_price:.2f}  盈亏 {fmt_pct(pnl)}")
            return
    print(f"❌ 持仓中没有 {code}")


# ============================================================
# 主流程
# ============================================================

def run_daily(screen_only=False, monitor_only=False):
    cfg = load_config()
    print(f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}  可转债安道全策略助手")
    try:
        rows_raw, source = fetch_market(cfg)
    except Exception as e:
        print(f"❌ 获取行情失败：{e}")
        print("   （可能网络问题或接口变更，稍后重试）")
        sys.exit(1)
    rows = market_tradable(rows_raw)
    temp = market_temperature(rows)

    if not monitor_only:
        top, reasons = screen_buy(rows, cfg)
        print_screen(top, temp, source, reasons)
        # 存快照
        log = load_screen_log()
        log.append({
            "date": str(date.today()),
            "source": source,
            "temperature": temp,
            "candidates": [
                {k: r.get(k) for k in
                 ("code", "name", "price", "premium", "double_low", "ytm",
                  "rating", "remain_years", "scale_yi", "stock_name")}
                for r in top
            ],
            "filter_stats": reasons,
        })
        save_screen_log(log)
        print()

    if not screen_only:
        pf = load_portfolio()
        signals = monitor_holdings(rows, rows_raw, cfg, pf)
        print_monitor(signals, cfg["sell"])


def cmd_help():
    print(__doc__)


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("help", "-h", "--help"):
        cmd_help()
        return
    cmd = args[0]
    if cmd == "daily":
        run_daily()
    elif cmd == "screen":
        run_daily(screen_only=True)
    elif cmd == "monitor":
        run_daily(monitor_only=True)
    elif cmd == "config":
        cfg = load_config()
        print(json.dumps(cfg, ensure_ascii=False, indent=2))
    elif cmd == "buy":
        if len(args) < 2:
            print("用法：python3 cb_tracker.py buy <转债代码> [--price 价格]")
            sys.exit(1)
        code = args[1]
        override = None
        if "--price" in args:
            override = to_float(args[args.index("--price") + 1])
        cfg = load_config()
        rows_raw, _ = fetch_market(cfg)
        rows = market_tradable(rows_raw)
        do_buy(code, override, rows, load_portfolio(), load_trade_log())
    elif cmd == "sell":
        if len(args) < 2:
            print("用法：python3 cb_tracker.py sell <转债代码> [卖出价]")
            sys.exit(1)
        code = args[1]
        price = to_float(args[2]) if len(args) > 2 else None
        cfg = load_config()
        rows_raw, _ = fetch_market(cfg)
        rows = market_tradable(rows_raw)
        do_sell(code, price, rows, load_portfolio(), load_trade_log())
    elif cmd == "status":
        cfg = load_config()
        rows_raw, _ = fetch_market(cfg)
        rows = market_tradable(rows_raw)
        print_status(rows, load_portfolio(), load_trade_log())
    else:
        print(f"❌ 未知命令：{cmd}，运行 python3 cb_tracker.py help 查看用法")


if __name__ == "__main__":
    main()
