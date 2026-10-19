#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""as-of 基本面取数（SEC XBRL companyfacts）——P1-4/G7 配套前视能力（2026-10-19）

用途：回答"**某一天**某只美股的营收增速 / ROE / 股本 / 市值是多少"，供选股池
历史回放（`pool_backtest` / 池文档 §10.5 的 Tier1/Tier2 判据）把基本面维度从
"当前快照"升级为"可回溯"。

数据源与口径（2026-10-19 实测）：
  SEC EDGAR **XBRL companyfacts API**：
    https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json
  官方、免费、单只一次取全量历史事实（AAPL 实测 3.8 MB / 503 个 us-gaap tag）。
  每条事实带 `end`（报告期）与 `filed`（申报公开日）→ 严格 point-in-time 双重门控：
      end ≤ as_of  AND  filed ≤ as_of
  与 NPORT-P 成分档案同性质：粒度为**申报期**（10-Q ~45 天 / 10-K ~90 天滞后），
  报告必须引 `revenue_period_end` / `*_filed`，不得写成"as-of 当天的值"。

指标口径：
  revenue_growth  最近一个已申报**财年**（340~400 天 duration）同比增速；
                  无财年数据时退回同长度累计季度（±25 天对年）。
  roe             同期 NetIncomeLoss ÷ 平均股东权益（期初期末两点均值；单点用该点）。
  shares_outstanding  dei.EntityCommonStockSharesOutstanding 最近一个 ≤ as_of 的申报值。
  market_cap_as_of  股本 × as-of 收盘价（价格走 get_market_data.py --end，as-of 层）。
  ⚠️ ticker→CIK 用 **现行** SEC ticker 表：改名 / 被并购 / 退市（如 HES）查不到 →
     记入 unavailable，**不可当成当时没有这家公司**（与成分档案 unmatched 同性质）。

缓存（scripts/.xbrl_fundamentals_cache/，与 .index_constituents_cache 同纪律）：
  - CIK 档案按 CIK 落盘，TTL 7 天（PIT 过滤按 filed 门控，重取不会改变 as-of 答案；
    旧申报值保留在 API 返回里，重述的新 filed 会被门控排除）；`--no-cache` 穿透。
  - ticker→CIK 表 TTL 7 天。

用法：
  python fetch_fundamentals_asof.py --as-of 2024-01-01 --tickers AAPL,MSFT,LUMN --out <f>.json
  python fetch_fundamentals_asof.py --as-of 2024-01-01 --pool <pool.json> --out <f>.json
  python fetch_fundamentals_asof.py --as-of 2024-01-01 --tickers AAPL --no-cache
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import time

# 入口强制 UTF-8（与 get_market_data.py 同口径：GBK 控制台打印中文/符号会崩）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_CACHE_DIR = os.path.join(_HERE, ".xbrl_fundamentals_cache")
_SEC_UA = {"User-Agent": "TradingResearch (research contact: tiger-stock-strategy-analysis)"}
# www.sec.gov 比 data.sec.gov 严格：实测 2026-10-19 `TradingResearch (...)` 对
# company_tickers.json 返回 403，`research test/1.0` 通过 → ticker 表用独立 UA。
_SEC_TICKERS_UA = {"User-Agent": "research test/1.0"}
_SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK%010d.json"
_TTL_DAYS = 7
_MIN_SLEEP_S = 0.12          # SEC 限速宽松（10 req/s），留足余量
_FY_DAYS = (340, 400)        # 财年 duration 范围
_QTR_TOL_DAYS = 25           # 对年匹配容差
# 标签链（按序取**第一个有数据**的）：US GAAP 优先，IFRS（外国发行人，如 BHP）兜底。
# 2026-10-19 实测：BHP 的 us-gaap 无营收/净利/权益，全部在 ifrs-full（币种 USD）。
_REV_TAGS = (
    ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),  # 新准则
    ("us-gaap", "Revenues"),                                             # 传统
    ("ifrs-full", "RevenueFromContractWithCustomerExcludingAssessedTax"),
    ("ifrs-full", "Revenue"),
)
_NI_TAGS = (
    ("us-gaap", "NetIncomeLoss"),
    ("ifrs-full", "ProfitLoss"),
    ("ifrs-full", "ProfitLossAttributableToOwnersOfParent"),
)
_EQ_TAGS = (
    ("us-gaap", "StockholdersEquity"),
    ("ifrs-full", "EquityAndReservesAttributableToOwnersOfParent"),
    ("ifrs-full", "EquityAttributableToOwnersOfParent"),
    ("ifrs-full", "Equity"),
)
_DEI_SHARES_TAG = "EntityCommonStockSharesOutstanding"


def _pick_entries(facts, chain):
    """按 (ns, tag) 链取第一个有 entries 的单元；返回 (ns, tag, unit, entries) 或 (None,)*4。
    币种：优先 USD，否则取唯一/首个 unit（增速/ROE 是比率、股本是数量，均币种无关；
    市值 = 股本 × USD 价，也不受记账币种影响）。"""
    for ns, tag in chain:
        units = ((facts.get("facts") or {}).get(ns) or {}).get(tag, {}).get("units") or {}
        if not units:
            continue
        if "USD" in units and units["USD"]:
            return ns, tag, "USD", units["USD"]
        for unit, entries in units.items():
            if entries:
                return ns, tag, unit, entries
    return None, None, None, []


# ==================== 纯函数（PIT 选择 / 指标计算，离线可测） ====================

def _days(a, b):
    """日期差（天）。XBRL 的 start/end 偶尔带时间后缀（如 2023-11-01T00:00:00-05:00）
    或异常值 → 取前 10 位 ISO 日期；解析失败返回 None（调用方须判 None）。"""
    try:
        return (datetime.date.fromisoformat(str(b)[:10])
                - datetime.date.fromisoformat(str(a)[:10])).days
    except Exception:
        return None


def _pick_filing_year(entries, as_of):
    """从 duration 事实里挑 as-of 时点**最近一个已申报财年**：end ≤ as_of 且
    filed ≤ as_of，duration 落在 _FY_DAYS；无财年则退回同长度累计期（±_QTR_TOL_DAYS）。
    返回 (cur, prior) 两个原始 entry dict 或 (None, None)。
    同 end 多条（重述/A 类）取 **最小 filed**（当时最先公开的值，最保守 PIT）。
    """
    ok = [e for e in entries
          if e.get("end") and e.get("end") <= as_of
          and e.get("filed", "") and e["filed"] <= as_of]
    if not ok:
        return None, None

    def dedup_by_end(items):
        best = {}
        for e in items:
            k = e["end"]
            if k not in best or e.get("filed", "") < best[k].get("filed", ""):
                best[k] = e
        return list(best.values())

    fy = dedup_by_end([e for e in ok if e.get("start")
                       for d in [_days(e["start"], e["end"] or "")]
                       if d is not None and _FY_DAYS[0] <= d <= _FY_DAYS[1]])
    pool = fy
    if not pool:
        # 退回：任意累计期，对年匹配（同长度 ± 容差）
        pool = dedup_by_end(ok)
    if not pool:
        return None, None
    cur = max(pool, key=lambda e: e["end"])
    prior = None
    for e in pool:
        d = _days(e["end"], cur["end"])
        if d is not None and abs(d - 365) <= _QTR_TOL_DAYS and e["end"] < cur["end"]:
            if prior is None or e["end"] > prior["end"]:
                prior = e
    return cur, prior


def _pick_instant(entries, as_of, on_or_before=None):
    """instant 事实里挑 ≤ as_of（且 ≤ on_or_before）最近申报点；同 end 取最小 filed。"""
    cap = as_of if (not on_or_before or on_or_before >= as_of) else on_or_before
    ok = [e for e in entries
          if e.get("end") and e["end"] <= cap
          and e.get("filed", "") and e["filed"] <= as_of]
    if not ok:
        return None
    best = {}
    for e in ok:
        k = e["end"]
        if k not in best or e.get("filed", "") < best[k].get("filed", ""):
            best[k] = e
    return max(best.values(), key=lambda e: e["end"])


def compute_revenue_growth(rev_entries, as_of):
    """(growth, cur_entry, prior_entry, notes)；growth 为同比小数（0.046=+4.6%）。"""
    notes = []
    cur, prior = _pick_filing_year(rev_entries, as_of)
    if not cur or not cur.get("val"):
        return None, cur, prior, ["无 end≤as_of 且 filed≤as_of 的营收申报"]
    if not prior or not prior.get("val") or prior["val"] in (0, -0):
        notes.append("prior 期缺失/为0 → 增速不可算（当年上市或数据缺失）")
        return None, cur, prior, notes
    g = cur["val"] / prior["val"] - 1.0
    if not fy_like(cur):
        notes.append("无财年 duration，用同长度累计期对年（±%d 天）" % _QTR_TOL_DAYS)
    return g, cur, prior, notes


def fy_like(entry):
    if not entry or not entry.get("start"):
        return False
    d = _days(entry["start"], entry["end"])
    return d is not None and _FY_DAYS[0] <= d <= _FY_DAYS[1]


def compute_roe(ni_entries, eq_entries, as_of, period_end_hint=None):
    """(roe, ni_entry, eq_points, notes)。分母＝平均股东权益（两点均值；单点用该点；
    权益为负 → None + note）。"""
    notes = []
    ni = _pick_instant(ni_entries, as_of, on_or_before=period_end_hint) if period_end_hint \
        else None
    if ni is None:
        ni = _pick_instant(ni_entries, as_of)
    if ni is None or not ni.get("val"):
        return None, ni, [], ["无 as-of 时点的净利申报"]
    # 净利若是 duration（start~end），权益用期末 + 期初两点
    eq_end = _pick_instant(eq_entries, as_of, on_or_before=ni.get("end"))
    eq_start = None
    if ni.get("start"):
        eq_start = _pick_instant(eq_entries, as_of, on_or_before=ni.get("start"))
    pts = [p for p in (eq_start, eq_end) if p and p.get("val") is not None
           and p is not eq_end]
    if eq_end:
        pts = [eq_end] + pts
    if not pts:
        return None, ni, [], ["无 as-of 时点的股东权益申报 → ROE 不可算"]
    denom = sum(p["val"] for p in pts) / len(pts)
    if denom <= 0:
        notes.append("平均股东权益 ≤ 0 → ROE 无意义")
        return None, ni, pts, notes
    roe = ni["val"] / denom
    if len(pts) == 1:
        notes.append("权益仅单点（期初申报缺失）→ 用期末单点近似")
    return roe, ni, pts, notes


def compute_market_cap(shares, price):
    """shares × price；任一缺失 → (None, notes)。"""
    if not shares or not shares.get("val") or price is None:
        return None, "股本或 as-of 价格缺失"
    return shares["val"] * price, None


def pool_tickers(pool_data):
    """池 JSON → 全部 ticker 集合（T1/T2/T3 三层）。"""
    out = set()
    for info in (pool_data.get("sub_sectors") or {}).values():
        for tier, tickers in (info.get("tiers") or {}).items():
            out.update(tickers or [])
    return sorted(out)


# ==================== 取数 / 缓存 ====================

def _http_json(url, timeout=30, retries=2, headers=None):
    import urllib.request
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers or _SEC_UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            code = getattr(e, "code", None)
            if code in (429, 403, 500, 502, 503, 504) and i < retries:
                time.sleep(2.0 * (i + 1))
                continue
            raise
    raise last


def _cached_load(path, ttl_days, no_cache=False):
    if no_cache or not os.path.exists(path):
        return None, False
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        fetched = obj.get("_fetched_at")
        if fetched and (time.time() - fetched) > ttl_days * 86400:
            return None, False
        return obj, True
    except Exception:
        return None, False


def _cached_write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    obj["_fetched_at"] = time.time()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def load_ticker_cik_map(cache_dir, no_cache=False):
    """ticker(大写) → (cik_str, name)。SEC company_tickers.json。"""
    path = os.path.join(cache_dir, "_company_tickers.json")
    obj, hit = _cached_load(path, _TTL_DAYS, no_cache=no_cache)
    if hit:
        obj.pop("_fetched_at", None)
        m = {k.upper(): (v, n) for k, (v, n) in obj.items()}
        return m, hit
    raw = _http_json(_SEC_TICKERS_URL, headers=_SEC_TICKERS_UA)
    m = {}
    for v in raw.values():
        m[str(v["ticker"]).upper()] = (str(v["cik_str"]), v.get("title", ""))
    _cached_write(path, m)
    return m, False


def fetch_company_facts(cik, cache_dir, no_cache=False):
    """单只 CIK 全量 facts；按 CIK 缓存（TTL 7 天；PIT 按 filed 门控，重取不改变 as-of 答案）。"""
    path = os.path.join(cache_dir, "CIK%010d.json" % int(cik))
    obj, hit = _cached_load(path, _TTL_DAYS, no_cache=no_cache)
    if hit:
        obj.pop("_fetched_at", None)
        return obj, True
    obj = _http_json(_SEC_FACTS_URL % int(cik))
    _cached_write(path, obj)
    time.sleep(_MIN_SLEEP_S)
    return obj, False


def fetch_prices_wide(tickers, as_of, price_dir, price_end, period="3y"):
    """宽窗口本地切片（批量回放用，2026-10-19）：读 fetch_wide_price.py 预取的
    pkl `{tk}_{period}_to{price_end}.pkl`，取 **≤ as_of 最后一根 bar 的未复权 Close**。
    零网络请求；缺 pkl / 无 ≤ as_of bar 记入 '失败'（不中断，该 ticker 市值判缺失）。
    与逐月取数口径一致：未复权 Close（与 get_market_data --end 的 price 字段同源同义）。"""
    import pandas as pd
    out, failed = {}, []
    for tk in tickers:
        path = os.path.join(price_dir, f"{tk}_{period}_to{price_end}.pkl")
        if not os.path.exists(path):
            failed.append({"ticker": tk, "原因": "宽窗口 pkl 缺失"})
            continue
        try:
            df = pd.read_pickle(path)
            idx = pd.to_datetime(df.index)
            sub = df.loc[idx <= pd.Timestamp(as_of)]
            if sub.empty:
                failed.append({"ticker": tk, "原因": f"窗口内无 ≤{as_of} 的 bar（窗口起太晚？）"})
                continue
            out[tk] = float(sub["Close"].iloc[-1])
        except Exception as e:
            failed.append({"ticker": tk, "原因": f"{type(e).__name__}: {e}"})
    if failed:
        out["失败"] = failed
    return out


def fetch_prices(tickers, as_of, batch=40):
    """as-of 收盘价：调 get_market_data.py --market us_stocks --end <as_of>（as-of 层缓存）。
    返回 {ticker: price}；取数失败批次记入返回 dict 的 '失败' 键（不中断）。"""
    gmd = os.path.join(_HERE, "get_market_data.py")
    out, failed = {}, []
    for i in range(0, len(tickers), batch):
        chunk = tickers[i:i + batch]
        cmd = [sys.executable, gmd, "--market", "us_stocks",
               "--tickers", ",".join(chunk), "--end", as_of, "--output", "json"]
        try:
            env = dict(os.environ)
            env.setdefault("PYTHONIOENCODING", "utf-8")
            env.setdefault("PYTHONUTF8", "1")
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=600, env=env)
            payload = json.loads(proc.stdout)
            for item in payload.get("data", []):
                t = str(item.get("ticker", "")).upper()
                p = item.get("price")
                if t and isinstance(p, (int, float)):
                    out[t] = p
        except Exception as e:
            failed.append({"tickers": chunk, "错误": f"{type(e).__name__}: {e}"})
    if failed:
        out["失败"] = failed
    return out


# ==================== 主流程 ====================

def main():
    ap = argparse.ArgumentParser(description="as-of 基本面取数（SEC XBRL companyfacts，P1-4/G7 配套）")
    ap.add_argument("--as-of", dest="as_of", required=True, metavar="YYYY-MM-DD")
    ap.add_argument("--tickers", default=None, help="逗号分隔 ticker 列表")
    ap.add_argument("--pool", default=None, help="池 JSON（取 sub_sectors 全部 ticker，可与 --tickers 并集）")
    ap.add_argument("--extra-tickers", default=None, metavar="FILE",
                    help="额外 ticker 文件（每行一个或逗号分隔）：池外候选全集（T3 补池候选），"
                         "并入取数范围——refill --fund 需要候选的 as-of 市值")
    ap.add_argument("--out", default=None, help="结果落盘路径；缺省打印到 stdout")
    ap.add_argument("--const", default=None, metavar="F1[,F2]",
                    help="成分档案 JSON（fetch_index_constituents.py --out 的产物）："
                         "market_cap_as_of 缺失的指数成分股，用该档案的 member_weights 相对权重"
                         "做近似市值（口径见 weight_fallback_note）。纯本地解析，不发请求")
    ap.add_argument("--no-cache", action="store_true", help="穿透缓存重取（仍写入）")
    ap.add_argument("--price-from", default=None, metavar="DIR",
                    help="宽窗口价格缓存目录（replay/fetch_wide_price.py 预取）：本地切片取价，"
                         "不逐 ticker 取数（批量回放用；缺 pkl 的 ticker 记失败、市值判缺失）")
    ap.add_argument("--price-end", default="2026-10-01", metavar="YYYY-MM-DD",
                    help="宽窗口终点日（拼 pkl 文件名，须与预取 --end 一致，默认 2026-10-01）")
    ap.add_argument("--price-period", default="3y", help="宽窗口 period（与预取 --period 一致，默认 3y）")
    ap.add_argument("--max", type=int, default=0, help="限前 N 只（调试用，0=不限）")
    ap.add_argument("--cache-dir", default=_CACHE_DIR)
    args = ap.parse_args()

    try:
        datetime.date.fromisoformat(args.as_of)
    except ValueError:
        print(json.dumps({"error": f"--as-of 日期格式非法: {args.as_of}"}, ensure_ascii=False))
        return 1

    tickers = []
    if args.tickers:
        tickers += [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if args.pool:
        with open(args.pool, "r", encoding="utf-8") as f:
            tickers += pool_tickers(json.load(f))
    if args.extra_tickers:
        with open(args.extra_tickers, "r", encoding="utf-8") as f:
            for line in f:
                tickers += [t.strip().upper() for t in line.replace(",", " ").split() if t.strip()]
    tickers = sorted(set(tickers))
    if not tickers:
        print(json.dumps({"error": "没有 ticker（需 --tickers 或 --pool）"}, ensure_ascii=False))
        return 1
    if args.max > 0:
        tickers = tickers[:args.max]

    cik_map, cik_hit = load_ticker_cik_map(args.cache_dir, no_cache=args.no_cache)
    if args.price_from:
        prices = fetch_prices_wide(tickers, args.as_of, args.price_from,
                                   args.price_end, period=args.price_period)
        price_source = (f"wide_window_local_slice（{args.price_from}，{args.price_period} "
                        f"to {args.price_end}，≤as-of 末根未复权 Close）")
    else:
        prices = fetch_prices(tickers, args.as_of)
        price_source = "get_market_data --end（as-of 层缓存，逐 ticker 取数）"
    price_fail = prices.pop("失败", None)

    results, unavailable = {}, {}
    n_fetch = n_cache = 0
    for tk in tickers:
        entry = cik_map.get(tk)
        if not entry:
            unavailable[tk] = ("ticker_not_in_current_sec_table（现行 SEC 表查不到：改名/被并购/"
                               "退市或表不全）→ **不可当成当时没有这家公司**，需人工核 CUSIP")
            continue
        cik, _name = entry
        try:
            facts, hit = fetch_company_facts(cik, args.cache_dir, no_cache=args.no_cache)
            n_cache += hit
            n_fetch += (not hit)
        except Exception as e:
            unavailable[tk] = f"companyfacts 取数失败（{type(e).__name__}: {e}）"
            continue

        notes = []

        # 营收增速（US GAAP → IFRS 标签链；币种自适应）
        rev_ns, rev_tag, rev_unit, rev_entries = _pick_entries(facts, _REV_TAGS)
        growth = cur_rev = prior_rev = None
        if rev_entries:
            growth, cur_rev, prior_rev, gnotes = compute_revenue_growth(rev_entries, args.as_of)
            notes += gnotes

        # ROE（净利与权益；净利期对齐营收期）
        ni_ns, ni_tag, _, ni_entries = _pick_entries(facts, _NI_TAGS)
        eq_ns, eq_tag, _, eq_entries = _pick_entries(facts, _EQ_TAGS)
        hint = cur_rev.get("end") if cur_rev else None
        roe, ni_e, eq_pts, rnotes = compute_roe(ni_entries, eq_entries, args.as_of,
                                                period_end_hint=hint)
        notes += rnotes

        # 股本 + 市值
        dei = facts.get("facts", {}).get("dei", {})
        sh_units = dei.get(_DEI_SHARES_TAG, {}).get("units") or {}
        sh = sh_units.get("shares") or (list(sh_units.values())[0] if sh_units else [])
        shares = _pick_instant(sh, args.as_of)
        price = prices.get(tk)
        mcap, mcap_note = compute_market_cap(shares, price)
        if mcap_note:
            notes.append(mcap_note)
        reporting = {"revenue": f"{rev_ns}:{rev_tag}" if rev_ns else None,
                     "net_income": f"{ni_ns}:{ni_tag}" if ni_ns else None,
                     "equity": f"{eq_ns}:{eq_tag}" if eq_ns else None}

        results[tk] = {
            "cik": int(cik),
            "available": True,
            "reporting": reporting,
            "revenue_growth": round(growth, 6) if growth is not None else None,
            "revenue_growth_note": "最近已申报财年同比（%s）" % (
                f"FY end={cur_rev['end']} filed={cur_rev['filed']} vs 上年 end={prior_rev['end']}"
                if prior_rev else "prior 缺失") if growth is not None else None,
            "roe": round(roe, 6) if roe is not None else None,
            "roe_note": (f"NI end={ni_e['end']} filed={ni_e['filed']} ÷ 平均权益"
                         f"({len(eq_pts)}点)") if roe is not None else None,
            "shares_outstanding": shares["val"] if shares else None,
            "shares_as_of_date": shares["end"] if shares else None,
            "shares_filed": shares["filed"] if shares else None,
            "price_as_of": price,
            "market_cap_as_of": round(mcap, 2) if mcap else None,
            "notes": notes,
        }

    out = {
        "requested_as_of": args.as_of,
        "source": "sec_xbrl_companyfacts",
        "price_source": price_source,
        "fundamentals_point_in_time": True,
        "pit_note": ("所有指标按 end ≤ as_of 且 filed ≤ as_of 双重门控（同 end 取最小 filed）；"
                     "粒度为申报期（10-Q ~45 天 / 10-K ~90 天滞后），报告必须引 "
                     "revenue_growth_note / roe_note / shares_as_of_date，不得写成 as-of 当天的值"),
        "ticker_cik_cache_hit": cik_hit,
        "facts_cache_hits": n_cache, "facts_fetched": n_fetch,
        "tickers": results,
        "unavailable": unavailable,
        "stats": {
            "requested": len(tickers), "ok": len(results), "unavailable": len(unavailable),
        },
    }
    if price_fail:
        out["price_fetch_failures"] = price_fail
        out["pit_note"] += "；as-of 价格走 get_market_data.py --end（as-of 层缓存）"

    # NPORT-P 权重兜底：market_cap_as_of 缺失的指数成分股 → 用成分档案的相对权重近似
    # （companyfacts 的 dei 股本只留近 3 期、原始申报会被重述覆盖 → 早于 ~3 季度的 as-of
    #  常无股本；权重档案按持仓期末可回溯更久，但只是**相对**市值，见 note）
    if args.const:
        weights = {}
        src_info = {}
        for cf in [c.strip() for c in args.const.split(",") if c.strip()]:
            try:
                with open(cf, "r", encoding="utf-8") as f:
                    cdoc = json.load(f)
                for r in cdoc.get("results", []):
                    if "member_weights" in r:
                        for tk, w in r["member_weights"].items():
                            weights[tk] = w
                        src_info.setdefault(r.get("etf", "?"), {}).update(
                            {"holdings_as_of": r.get("holdings_as_of"),
                             "total_val_usd": r.get("total_val_usd")})
            except Exception as e:
                out.setdefault("weight_fallback_failures", []).append(
                    {"file": cf, "错误": f"{type(e).__name__}: {e}"})
        n_fb = 0
        for tk, v in results.items():
            if v["market_cap_as_of"] is None and tk in weights:
                v["market_cap_as_of_approx"] = True
                v["weight_pct"] = round(weights[tk]["pct"], 4)
                v["weight_val_usd"] = round(weights[tk]["val_usd"], 2)
                v["notes"].append(
                    "market_cap_as_of 缺失（as-of 时点无股本申报）→ 规模判据用 NPORT-P 相对权重"
                    "（weight_pct，见 weight_fallback_note）；**只可排序/看档位，不可当绝对市值**")
                n_fb += 1
        out["weight_fallback"] = {"applied": n_fb, "sources": src_info}
        out["weight_fallback_note"] = (
            "weight_pct 取自 NPORT-P 持仓期末（见 sources.holdings_as_of，滞后 as-of 0~92 天）："
            "＝该申报 series 组合内权重，**≈指数成分内的相对市值份额**。用途边界：T1「子板块市值 "
            "Top5」等**排序类**判据可直接用；T2「50~500 亿」**绝对档位**不能直接判（缺指数总市值"
            "校准），只能作\"该标的在指数内的规模位次\"佐证并记入待人工确认；**不是公司绝对市值**"
            "（series 规模/跟踪误差/现金拖累），且只覆盖 SPY/MDY 成分——指数外小盘没有此兜底")

    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"已写入 {args.out} | ok {len(results)}/{len(tickers)} "
              f"unavailable {len(unavailable)}（缓存命中 {n_cache} / 新取 {n_fetch}）")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
