#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""指数/ETF 成分 as-of 取数（SEC NPORT-P）——P1-3 前视能力

用途：回答"**某一天**这个指数/ETF 的成分是谁"，供选股池回放（`pool_backtest` /
`generate_candidate_pool --as-of`）消除幸存者偏差。

数据源与口径（2026-10-18 实测）：
  SEC EDGAR **Form N-PORT（NPORT-P）**——ETF 发起人向 SEC 申报的持仓明细，官方、免费、
  **带日期档案**。每份申报含 `reportDate`（持仓期末）与 `filingDate`（公开日）。
  原始 XML 在 `Archives/edgar/data/{cik}/{accession去横线}/primary_doc.xml`（442 KB 量级）。
  ⚠️ `submissions` 里的 `primaryDocument` 字段指向 `xslFormNPORT-P_X01/primary_doc.xml`
     = **9 MB 的 XSL 渲染 HTML（无 <cusip> 标签）**，不要用那个路径。

严格 point-in-time 双重门控（关键，缺一条就是前视污染）：
      reportDate ≤ as_of  AND  filingDate ≤ as_of
  实测滞后：期末后约 2 个月才公开 → **as-of 能拿到的成分最多滞后 2~4 个月**
  （如 as_of=2025-06-30 只能取到报告期 2025-03-31；as_of=2025-08-10 仍是 03-31）。

不预回填：按需实时取 + **按 accession 落不可变缓存**（历史申报不会变），
跑过的日期自然形成档案；12 个月末 as_of 实测只需 5 份不同申报。

已知限制（诚实标记，不静默丢弃）：
  - **ETF 持仓 ≠ 指数官方成分**（跟踪误差/借券/现金拖累）→ 输出 `source=sec_nport_p:{etf}`。
  - ticker 由 **发行人名称归一化匹配** SEC `company_tickers.json` 得到（NPORT 不给 ticker）；
    匹配率与未匹配清单在输出里（`match_rate` / `unmatched`），低于 `--min-hit` 给 `mapping_warning`。
  - 同名多类别/优先股（BRK-A/BRK-B、GOOG/GOOGL）按启发式选主类，计入 `ambiguous` 供人工核对。
  - **series 制申报人**（如 IWM 的 AllSpring Funds Trust CIK 0001081400，一季多份）
    需按 series 定位，未实现 → 用 `--cik` 自行指定单基金申报人。

用法：
  python fetch_index_constituents.py --as-of 2025-06-30
  python fetch_index_constituents.py --as-of 2025-06-30 --index sp500 --output text
  python fetch_index_constituents.py --latest --out ../temp/const_now.json
  python fetch_index_constituents.py --as-of 2024-12-31 --list-filings
"""
import argparse
import datetime
import html
import json
import os
import re
import sys
import time

# 入口强制 UTF-8（与 get_market_data.py 同口径：GBK 控制台打印中文/符号会崩）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

import gzip  # noqa: E402

try:
    from curl_cffi import requests as cfr
    _HAS_CURL_CFFI = True
except ImportError:  # pragma: no cover
    import requests as cfr  # type: ignore
    _HAS_CURL_CFFI = False

import requests  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_CACHE_DIR = os.path.join(_HERE, ".index_constituents_cache")

# SEC 要求申报数据检索带可识别 UA（不带会被 403）
_SEC_UA = {"User-Agent": "TradingResearch (research contact: tiger-stock-strategy-analysis)"}
_SOCKS5 = os.environ.get("YF_SOCKS5", "127.0.0.1:10808").lstrip("socks5h://").lstrip("socks5://")

# 已验证的单基金申报人（CIK 来自 SEC company_tickers_exchange.json，2026-10-18 实测）
ETF_FILERS = {
    "sp500": {"cik": "0000884394", "etf": "SPY", "index": "S&P 500"},
    "midcap400": {"cik": "0000936958", "etf": "MDY", "index": "S&P MidCap 400"},
}
# 权益类 assetCat：EC=普通股, DE=存托凭证, PF=优先股, NN=场外基金, CP=可转债
_EQUITY_CATS = {"EC", "DE", "PF", "NN", "CP"}

_SUBMISSIONS_TTL = 6 * 3600      # 申报清单会变（新申报陆续公开）
_TICKERS_TTL = 12 * 3600         # ticker 全集变化慢

_JURIS_RE = re.compile(r"\s*/[A-Za-z]{2,4}/?")          # "AMPHENOL CORP /DE/"
_PAREN_RE = re.compile(r"\(([^()]*)\)")                 # "SLM CORP (SALLIE MAE)"
_SUFFIX_RE = re.compile(
    r"\b(inc|incorporated|corp|corporation|co|cos|company|companies|llc|ltd|limited|plc|lp|llp|gp|"
    r"trust|fund|funds|group|holdings|holding|s a|sa|nv|ag|bv|n v|ucits|etf|series|"
    r"class a|class b|class c|shares|common stock|stock|new|the|companies)\b",
    re.IGNORECASE)
_STATE_TOKENS = {
    "DE", "MA", "MO", "MD", "NY", "PA", "NV", "CA", "TX", "IL", "NJ", "CT", "VA", "WA",
    "FL", "GA", "NC", "OH", "MI", "MN", "CO", "WI", "IN", "UT", "AZ", "OR", "KS", "KY",
    "LA", "ME", "NH", "RI", "VT", "AL", "AR", "IA", "ID", "MS", "MT", "ND", "NE", "NM",
    "OK", "SC", "SD", "TN", "WV", "WY", "DC", "AK", "HI",
}


# ==================== HTTP ====================

def _http_get(url, timeout=40):
    """SEC 取数：curl_cffi 直连优先 → 走 SOCKS5 代理 → requests 兜底；失败 None。"""
    if _HAS_CURL_CFFI:
        for proxies in (None, {"http": f"socks5h://{_SOCKS5}", "https": f"socks5h://{_SOCKS5}"}):
            try:
                r = cfr.Session(proxies=proxies, timeout=timeout).get(
                    url, headers=_SEC_UA, impersonate="chrome")
                if r.status_code == 200:
                    raw = r.content
                    if raw[:2] == b"\x1f\x8b":
                        raw = gzip.decompress(raw)
                    return raw.decode("utf-8", "replace")
            except Exception:
                continue
    try:
        r = requests.get(url, headers=_SEC_UA, timeout=(5, timeout))
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    return None


# ==================== 缓存（按 accession 不可变） ====================

def _cache_get_json(path, ttl=None):
    """ttl=None → 不可变缓存（无 TTL）"""
    try:
        if not os.path.exists(path):
            return None
        if ttl is not None and time.time() - os.path.getmtime(path) > ttl:
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _cache_put(path, obj):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


# ==================== 申报清单与 as-of 选文件 ====================

def list_filings(cik, cache_dir, no_cache=False):
    """SEC submissions → NPORT-P 申报列表 [{period, filed, accession, form}]（升序）。

    清单缓存 TTL 6 小时（新申报陆续公开，不能永久缓存）。
    """
    path = os.path.join(cache_dir, f"_submissions_{cik}.json")
    if not no_cache:
        hit = _cache_get_json(path, _SUBMISSIONS_TTL)
        if hit:
            return hit
    text = _http_get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
    if not text:
        return []
    d = json.loads(text)
    rec = d.get("filings", {}).get("recent", {})
    forms = rec.get("form") or []
    out = []
    for i, form in enumerate(forms):
        if form != "NPORT-P":
            continue
        period = (rec.get("reportDate") or [""] * len(forms))[i] or ""
        filed = (rec.get("filingDate") or [""] * len(forms))[i] or ""
        acc = (rec.get("accessionNumber") or [""] * len(forms))[i] or ""
        if period and filed and acc:
            out.append({"period": period, "filed": filed, "accession": acc, "form": form})
    out.sort(key=lambda r: (r["period"], r["filed"]))
    _cache_put(path, out)
    return out


def pick_filing(filings, as_of):
    """严格 PIT 双重门控：period ≤ as_of 且 filed ≤ as_of，取 period 最新的一份。

    as_of=None → 最新已公开的一份（live 语义）。
    """
    ok = [r for r in filings if not as_of or (r["period"] <= as_of and r["filed"] <= as_of)]
    return max(ok, key=lambda r: (r["period"], r["filed"])) if ok else None


# ==================== 持仓 XML 解析 ====================

def _fetch_holdings_xml(cik, filing, cache_dir, no_cache=False):
    """取原始 primary_doc.xml（根路径，**不是** xslFormNPORT-P_X01/ 那份 9 MB HTML）。

    历史申报不可变 → 无 TTL 缓存。
    """
    acc_nd = filing["accession"].replace("-", "")
    path = os.path.join(cache_dir, f"raw_{acc_nd}.xml")
    if not no_cache and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read(), True
        except Exception:
            pass
    url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}"
           f"/{acc_nd}/primary_doc.xml")
    text = _http_get(url)
    if text:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            pass
    return text, False


def parse_holdings(xml):
    """NPORT-P XML → [{name, title, cusip, assetCat, valUSD, pctVal}]。

    实测结构（默认命名空间、无前缀）：`<invstOrSecs>` 下每笔 `<invstOrSec>`，
    字段 `<name>`(发行人) / `<lei>` / `<title>`(证券描述) / `<cusip>` /
    `<assetCat>`(EC=普通股) / `<valUSD>`(持仓美元值) / `<pctVal>`(占组合百分比，
    全部持仓之和≈100) 等。

    权重口径（2026-10-19 实测）：`pctVal` 是**百分数**（SPY 503 笔和=99.99、MDY 400 笔
    和=99.4）；`valUSD` 合计≈该申报 series 的组合总值（MDY 申报 series ≈ $23.5B）。
    同一 CIK 可能按多个 series 申报 → **相对权重（pctVal / valUSD 排序）不受 series
    规模影响，可直接用于 as-of 相对市值排名；绝对美元值只在该 series 为主系列时才有意义**。
    """
    out = []
    for blk in re.split(r"<invstOrSec[ >/]", xml)[1:]:
        def g(pat, cast=str):
            m = re.search(pat, blk)
            if not m:
                return None
            try:
                return cast(m.group(1))
            except Exception:
                return None
        rec = {
            "name": (html.unescape(g(r"<name>([^<]+)</name>", str) or "") or "").strip(),
            "title": (html.unescape(g(r"<title>([^<]+)</title>", str) or "") or "").strip(),
            "cusip": g(r"<cusip>([A-Z0-9]{6,9})</cusip>") or "",
            "assetCat": g(r"<assetCat>([^<]+)</assetCat>") or "",
            "valUSD": g(r"<valUSD>([\d.]+)</valUSD>", float),
            "pctVal": g(r"<pctVal>([\d.]+)</pctVal>", float),
        }
        if rec["name"] or rec["cusip"]:
            out.append(rec)
    return out


# ==================== 名称 → ticker 映射 ====================

_STOP_TOKENS = {"AND", "OR", "OF", "FOR", "THE", "A", "AN", "TO", "IN"}

# 改名别名（键＝归一化后的 NPORT 发行人名）——**只收 SEC 现行 ticker 表里已验证存在的映射**
# （2026-10-18 实测：Schlumberger 已改名 SLB；Franklin Resources 已改名 Franklin Templeton(BEN)；
#  Western Alliance Bancorp 的 SEC 注册名是 WESTERN ALLIANCE BANCORPORATION(WAL)）。
# 被并购/退市（Discover/Hess/Kellanova/Juniper/Paramount…）故意不收——它们本就不该有现行 ticker。
_NAME_ALIASES = {
    "SCHLUMBERGER": "SLB",
    "FRANKLIN RESOURCES": "BEN",
    "WESTERN ALLIANCE BANCORP": "WAL",
}


def _norm_name(s):
    """归一化：解 HTML 实体 → 去撇号 → 去 /DE/ 辖区后缀与括号别名 → 去公司后缀词、州名、连接词。

    实测断点（2026-10-18）：不解 `&amp;` 则 AT&T / Bath & Body Works 全断；
    不去 `/DE/`、`/MA/` 则 `AMPHENOL CORP /DE/` vs `Amphenol Corp` 全断；
    不去 `AND` 则 `Air Products and Chemicals` vs `AIR PRODUCTS & CHEMICALS INC` 断。
    """
    s = html.unescape(s or "")
    s = s.replace("'", "").replace("\u2019", "")
    s = _JURIS_RE.sub(" ", s)
    s = _PAREN_RE.sub(" ", s)
    s = re.sub(r"[^A-Za-z0-9 ]", " ", s)
    s = _SUFFIX_RE.sub(" ", s)
    toks = [t for t in s.split() if t.upper() not in _STATE_TOKENS | _STOP_TOKENS]
    return " ".join(toks).upper()


def _pick_ticker(cands):
    """同名多 ticker（优先股/多类别/权证）里选主类：无 '-' 优先、标题不含 PREF/CLASS 优先、短优先。"""
    def score(item):
        t, title = item
        s = 0
        if "-" in t:
            s += 3
        if re.search(r"PREF|CLASS|WARRANT|UNIT|RIGHT|RTS", title or "", re.IGNORECASE):
            s += 2
        if len(t) >= 5:
            s += 1
        return (s, len(t), t)

    return sorted(cands, key=score)[0][0]


def ticker_index(cache_dir, no_cache=False):
    """SEC company_tickers.json → 三套索引（缓存 12h）：

    `norm`（去后缀/州名/连接词）、`tokens`（token 排序，解 "CHARLES SCHWAB" vs "SCHWAB CHARLES"）、
    `compact`（去空格，解 "EXXON MOBIL" vs "ExxonMobil Holdings"）。
    返回 {"norm": {...}, "tokens": {...}, "compact": {...}}。
    """
    path = os.path.join(cache_dir, "_tickers.json")
    if not no_cache:
        hit = _cache_get_json(path, _TICKERS_TTL)
        if hit and "norm" in hit:
            return hit
    text = _http_get("https://www.sec.gov/files/company_tickers.json")
    if not text:
        return {}
    idx = {"norm": {}, "tokens": {}, "compact": {}}
    for v in json.loads(text).values():
        k = _norm_name(v.get("title", ""))
        if not k or not v.get("ticker"):
            continue
        pair = (v["ticker"], v.get("title", ""))
        idx["norm"].setdefault(k, []).append(pair)
        idx["tokens"].setdefault(" ".join(sorted(k.split())), []).append(pair)
        idx["compact"].setdefault(k.replace(" ", ""), []).append(pair)
    _cache_put(path, idx)
    return idx


def _lookup(name, idx, aliases=None):
    """查 ticker 候选：改名别名 → norm → tokens（解词序）→ compact（解空格）。"""
    k = _norm_name(name)
    if not k:
        return []
    alias = (aliases or _NAME_ALIASES).get(k)
    if alias:
        return [(alias, "alias")]
    for sub, key in (("norm", k),
                     ("tokens", " ".join(sorted(k.split()))),
                     ("compact", k.replace(" ", ""))):
        cands = (idx.get(sub) or {}).get(key)
        if cands:
            return cands
    return []


def load_aliases(path):
    """别名文件：JSON {归一化或原始发行人名: ticker}；失败返回内置别名表。"""
    if not path:
        return dict(_NAME_ALIASES)
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        out = dict(_NAME_ALIASES)
        for k, v in (raw.items() if isinstance(raw, dict) else []):
            if isinstance(v, str) and k:
                out[_norm_name(k)] = v.upper()
        return out
    except Exception:
        return dict(_NAME_ALIASES)


def map_symbols(holdings, idx, aliases=None):
    """持仓 → (symbols, unmatched, ambiguous)。未匹配一律留痕（含 name+cusip），不静默丢弃。

    未匹配通常是**改名 / 被并购 / 退市**（SEC 现行 ticker 表里没有）——这类恰恰是回放
    最需要的信息，因此保留发行人名 + CUSIP 作为成员身份，不当作"没有这个成员"。
    """
    symbols, unmatched, ambiguous = [], [], []
    weights = {}
    seen = set()
    for h in holdings:
        cands = _lookup(h["name"], idx) or _lookup(h["title"], idx)
        if not cands:
            unmatched.append({"name": h["name"] or h["title"], "cusip": h["cusip"],
                              "assetCat": h["assetCat"]})
            continue
        tk = _pick_ticker(cands)
        if len(cands) > 1:
            ambiguous.append({"name": h["name"] or h["title"], "picked": tk,
                              "candidates": [c[0] for c in cands][:6]})
        if tk not in seen:
            seen.add(tk)
            symbols.append(tk)
        # 权重累加（同一 ticker 多笔持仓时求和）——as-of 相对市值排名的数据源
        w = weights.setdefault(tk, {"val_usd": 0.0, "pct": 0.0})
        w["val_usd"] += h.get("valUSD") or 0.0
        w["pct"] += h.get("pctVal") or 0.0
    return sorted(symbols), unmatched, ambiguous, weights


# ==================== 主流程 ====================

def fetch_one(index_key, cfg, as_of, cache_dir, no_cache=False, with_diff=True,
              aliases=None, min_hit=0.9):
    t0 = time.time()
    cik = cfg["cik"]
    filings = list_filings(cik, cache_dir, no_cache=no_cache)
    if not filings:
        return {"index": cfg["index"], "etf": cfg["etf"], "cik": cik,
                "error": "SEC submissions 无 NPORT-P 申报或取数失败", "time_s": round(time.time() - t0, 2)}
    cur = pick_filing(filings, as_of)
    if not cur:
        return {"index": cfg["index"], "etf": cfg["etf"], "cik": cik,
                "error": f"as_of={as_of} 早于档案起点 {filings[0]['period']}",
                "archive_start": filings[0]["period"], "time_s": round(time.time() - t0, 2)}

    xml, cache_hit = _fetch_holdings_xml(cik, cur, cache_dir, no_cache=no_cache)
    if not xml:
        return {"index": cfg["index"], "etf": cfg["etf"], "cik": cik,
                "error": f"primary_doc.xml 取数失败（accession={cur['accession']}）",
                "time_s": round(time.time() - t0, 2)}

    all_rows = parse_holdings(xml)
    eq_rows = [h for h in all_rows if h["assetCat"] in _EQUITY_CATS]
    idx = ticker_index(cache_dir, no_cache=no_cache)
    symbols, unmatched, ambiguous, weights = map_symbols(eq_rows or all_rows, idx, aliases)
    total = len(eq_rows) or len(all_rows)
    rate = round(len(symbols) / total, 4) if total else 0.0

    lag_days = None
    if as_of:
        try:
            lag_days = (datetime.date.fromisoformat(as_of)
                        - datetime.date.fromisoformat(cur["period"])).days
        except Exception:
            pass

    out = {
        "index": cfg["index"], "etf": cfg["etf"], "cik": cik,
        "source": f"sec_nport_p:{cfg['etf']}",
        "requested_as_of": as_of,
        "holdings_as_of": cur["period"],          # 持仓期末（真正的成分日期）
        "filed": cur["filed"],                    # 公开日（PIT 门控依据）
        "accession": cur["accession"],
        "lag_days": lag_days,
        "point_in_time": True,
        "pit_note": ("成分取自 reportDate ≤ as_of 且 filingDate ≤ as_of 的申报 → 严格 point-in-time；"
                     f"代价是成分滞后持仓期末约 {lag_days or '—'} 天（期末后 ~2 个月才公开）"),
        "archive_start": filings[0]["period"],
        "archive_end": filings[-1]["period"],
        "holdings_rows": len(all_rows), "equity_rows": total,
        "members_total": total,                    # 成员全集（含无法映射 ticker 的）
        "symbols_count": len(symbols), "match_rate": rate,
        "symbols": symbols,
        # as-of 相对市值权重（P2-1，2026-10-19）：供 T1「子板块市值 Top5」等判据在
        # 历史模式下用**持仓期末**的相对市值排序。pct＝占组合百分比（全组合和≈100）；
        # val_usd＝该 series 组合内美元值（跨 series 不可横向比，只用于排序/同 series 内比较）。
        # 注意：这是**相对权重**，不等于公司绝对市值；绝对市值用 fetch_fundamentals_asof.py
        #（SEC XBRL 股本 × as-of 价）。
        "member_weights": weights,
        "total_val_usd": round(sum(w["val_usd"] for w in weights.values()), 2),
        "weights_note": ("member_weights 取自持仓期末（holdings_as_of）申报：pct＝占组合百分比"
                         "（全部持仓和≈100）、val_usd＝该申报 series 内美元值。"
                         "同一 CIK 可能多 series 申报 → 只用于**相对市值排名**（T1 Top5 判据），"
                         "不得当公司绝对市值；绝对市值走 fetch_fundamentals_asof.py"),
        "unmatched": unmatched[:40], "unmatched_count": len(unmatched),
        "unmatched_note": ("未匹配＝SEC 现行 ticker 表里查不到（多为**改名/被并购/退市**或法定名差异）；"
                           "这些仍是当期成员，成员身份用 name+cusip，不可当"
                           "\"当时没有这只股票\""),
        "ambiguous": ambiguous[:20], "ambiguous_count": len(ambiguous),
        "cache_hit": cache_hit, "time_s": round(time.time() - t0, 2),
    }
    if rate < min_hit:
        out["mapping_warning"] = (
            f"名称→ticker 匹配率 {rate:.1%} < {min_hit:.0%}：部分成员映射不到现行 ticker"
            "（改名/被并购/退市，或 SEC ticker 表不全）。成员全集在 members_total/equity_rows，"
            "未匹配者保留 name+cusip——**不可把未匹配当成当时没有这只股票**")

    if with_diff:
        prev = None
        for r in filings:
            if r["period"] < cur["period"] and (not as_of or r["filed"] <= as_of):
                prev = r
        if prev:
            pxml, _ = _fetch_holdings_xml(cik, prev, cache_dir, no_cache=no_cache)
            if pxml:
                psym, _, _, _ = map_symbols(
                    [h for h in parse_holdings(pxml) if h["assetCat"] in _EQUITY_CATS] or [],
                    idx, aliases)
                out["prev_holdings_as_of"] = prev["period"]
                out["added"] = sorted(set(symbols) - set(psym))
                out["removed"] = sorted(set(psym) - set(symbols))
    return out


def constituents_for_asof(as_of, cache_dir=None, indexes=None, no_cache=False,
                          aliases=None, min_hit=0.9, with_scope=True):
    """回放链共享的**成分档案层**（P1-3 接线，2026-10-18）——供 generate_candidate_pool /
    pool_backtest 在 `--as-of` 下把"成员维度"从不可回溯升级为可回溯。

    返回 dict（**不含 symbols 明细**，避免调用方结果 JSON 膨胀；明细走本模块 CLI --out）：
      available          至少一个指数当期成分取到 → True
      requested_as_of    请求日
      holdings_as_of     {index: 持仓期末}——**报告必须引这个**，不是 requested_as_of
      lag_days           {index: 滞后天数}（实测 ~91 天）
      match_rate / members_total / unmatched_count   {index: ...}
      symbols            当期成员 ticker 并集（排序）
      scope_symbols      覆盖范围参照集＝当期 ∪ 最新一期（判"指数外标的"用）
      failed_indexes     [{index, error}]——部分失败不判整体失败

    调用方红线：
      1. `available=False` 必须退回"当前榜单 + point_in_time=false"，**不得假装有成分**；
      2. 候选 ticker 不在 `symbols` 也不在 `scope_symbols` → 属指数覆盖范围外（小盘/板块），
         判 **unknown** 而非 False；
      3. `unmatched_count > 0` 表示当期有成员映射不到现行 ticker（改名/被并购/退市），
         ticker 级对照存在盲区，**不可当成当时没有这只股票**。
    """
    cache_dir = cache_dir or _DEFAULT_CACHE_DIR
    if aliases is None:
        aliases = load_aliases(None)
    keys = list(indexes) if indexes else list(ETF_FILERS)
    out = {"requested_as_of": as_of, "available": False, "symbols": [], "scope_symbols": [],
           "holdings_as_of": {}, "lag_days": {}, "members_total": {}, "match_rate": {},
           "unmatched_count": {}, "failed_indexes": []}
    per = {}
    for k in keys:
        r = fetch_one(k, ETF_FILERS[k], as_of, cache_dir, no_cache=no_cache,
                      with_diff=False, aliases=aliases, min_hit=min_hit)
        if "error" in r:
            out["failed_indexes"].append({"index": k, "error": r["error"]})
            continue
        per[k] = r
        out["holdings_as_of"][k] = r.get("holdings_as_of")
        out["lag_days"][k] = r.get("lag_days")
        out["members_total"][k] = r.get("members_total")
        out["match_rate"][k] = r.get("match_rate")
        out["unmatched_count"][k] = r.get("unmatched_count", 0)
    if not per:
        out["note"] = ("成分档案不可用：" + "；".join(
            f"{f['index']}={f['error']}" for f in out["failed_indexes"])
            + " → 调用方必须退回当前榜单并标 point_in_time=false")
        return out

    cur = set()
    for r in per.values():
        cur |= set(r["symbols"])
    out["symbols"] = sorted(cur)
    out["available"] = True

    # 覆盖范围参照集：并入"最新一期"成员（缓存命中 ~0.06 s）。
    # 用途：区分"当时不是成员"（幸存者偏差信号）与"本就不在这两个指数里"（判 unknown）。
    scope = set(cur)
    if with_scope:
        for k in per:
            lr = fetch_one(k, ETF_FILERS[k], None, cache_dir, with_diff=False,
                           aliases=aliases, min_hit=min_hit)
            if "error" not in lr:
                scope |= set(lr["symbols"])
                out.setdefault("latest_holdings_as_of", {})[k] = lr.get("holdings_as_of")
    out["scope_symbols"] = sorted(scope)
    out["note"] = ("成分取自 SEC NPORT-P（**ETF 持仓 ≠ 指数官方成分**），按 reportDate ≤ as_of 且 "
                   "filingDate ≤ as_of 双重门控 → 严格 point-in-time，代价是成分滞后持仓期末约 "
                   f"{max([v for v in out['lag_days'].values() if v] or [0])} 天；"
                   "ticker 靠名称匹配，未匹配成员保留 name+cusip（改名/被并购/退市），"
                   "ticker 级对照有盲区")
    return out


def main():
    ap = argparse.ArgumentParser(description="SEC NPORT-P 指数/ETF 成分 as-of 取数（P1-3）")
    ap.add_argument("--as-of", dest="as_of", default=None, metavar="YYYY-MM-DD",
                    help="严格 point-in-time 截止日（双重门控 reportDate/filingDate）")
    ap.add_argument("--latest", action="store_true", help="取最新已公开的一份（不传 --as-of 时默认行为）")
    ap.add_argument("--index", default="all", choices=list(ETF_FILERS) + ["all"],
                    help="sp500 / midcap400 / all（默认 all）")
    ap.add_argument("--cik", default=None, help="自定义申报人 CIK（series 制申报人需自行定位）")
    ap.add_argument("--label", default="custom", help="自定义 CIK 时的标签（默认 custom）")
    ap.add_argument("--list-filings", action="store_true", help="只列申报清单（不取持仓）")
    ap.add_argument("--no-diff", action="store_true", help="不比对上一期（省一次取数）")
    ap.add_argument("--min-hit", type=float, default=0.9, help="匹配率告警阈值（默认 0.9）")
    ap.add_argument("--alias-file", default=None,
                    help="额外 name→ticker 别名 JSON（键为发行人名，大小写/后缀不敏感）")
    ap.add_argument("--cache-dir", default=_DEFAULT_CACHE_DIR)
    ap.add_argument("--no-cache", action="store_true", help="穿透缓存重取（仍写入）")
    ap.add_argument("--output", choices=["json", "text"], default="json")
    ap.add_argument("--out", default=None, help="结果落盘路径；stdout 只回一行摘要")
    args = ap.parse_args()

    if args.as_of:
        try:
            datetime.date.fromisoformat(args.as_of)
        except ValueError:
            print(json.dumps({"error": f"--as-of 日期格式非法: {args.as_of}"}, ensure_ascii=False))
            return 1

    targets = dict(ETF_FILERS)
    if args.cik:
        targets["custom"] = {"cik": args.cik, "etf": args.label, "index": args.label}
        targets = {"custom": targets["custom"]}
    if args.index != "all":
        targets = {args.index: targets[args.index]}

    aliases = load_aliases(args.alias_file)
    results, t0 = [], time.time()
    for key, cfg in targets.items():
        if args.list_filings:
            fl = list_filings(cfg["cik"], args.cache_dir, no_cache=args.no_cache)
            results.append({"index": cfg["index"], "etf": cfg["etf"], "cik": cfg["cik"],
                            "filings": len(fl),
                            "first": fl[0]["period"] if fl else None,
                            "last": fl[-1]["period"] if fl else None,
                            "recent": fl[-3:]})
            continue
        results.append(fetch_one(key, cfg, args.as_of, args.cache_dir,
                                 no_cache=args.no_cache, with_diff=not args.no_diff,
                                 aliases=aliases, min_hit=args.min_hit))

    payload = {"requested_as_of": args.as_of, "results": results,
               "time_s": round(time.time() - t0, 2)}
    text = json.dumps(payload, ensure_ascii=False, indent=2 if args.output == "text" else None)

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        for r in results:
            print(f"[{r.get('etf')}] 成分 {r.get('symbols_count', 'ERR')}/{r.get('equity_rows', '?')} "
                  f"持仓期末={r.get('holdings_as_of')} 申报={r.get('filed')} "
                  f"匹配率={r.get('match_rate', 0):.1%} 缓存={r.get('cache_hit')} "
                  f"{r.get('error', '')}")
        print(f"已写入 {args.out} | 总耗时 {payload['time_s']}s")
    elif args.output == "text":
        for r in results:
            print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
