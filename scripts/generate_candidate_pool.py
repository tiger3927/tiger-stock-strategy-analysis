r"""
美股候选池生成工具 — 《选股操作手册.md》的代码化执行入口

把手册中已量化、但此前由 AI 手工执行的规则固化为代码：
  §5.1 数量控制（<10 补充 / >40 按市值截断至 40，硬执行）
  §5.2 多样性（板块覆盖 ≥3，不足自动从固定池补齐）
  §5.3 小盘保障（市值 $1B-$10B 占比 ≥20%，不足自动补充，仍不足标记 small_cap_insufficient）
  §5.4 排除规则（股价 <$3 / 日均成交量 <20万股，硬执行；负面新闻留给 AI 定性）
  §一 优先级链（板块衍生 → 全网扫描 → 用户指定 → 固定池兜底；"不足"硬定义为 <15 只）
  §七 池外探测（固定入口清单 + 降级序 + 差集 + 单次并入上限 10 + 全程留痕）

AI（智能体）保留的判断题：板块方向选择、负面新闻定性、命中标准的最终判定。
本脚本输出的 JSON 与选股报告的"候选池摘要"字段一一对应，可直接粘贴留痕。

用法:
  # 自动模式（默认，按优先级链生成候选池）
  python generate_candidate_pool.py --direction short --sector "半导体" --output json

  # 仅离线（固定池种子 + 已有指标文件，不访问网络，测试/重放用）
  python generate_candidate_pool.py --mode seed --data-file metrics.json --output json

  # 单独执行 §七 池外探测（对现有候选池做差集并入）
  python generate_candidate_pool.py --probe-only pool.json --output json

  # 调试单个入口
  python generate_candidate_pool.py --entry-name etf-spy --output json

  # 用户提供 ticker 列表
  python generate_candidate_pool.py --mode user --tickers AAPL,MSFT --output json

指标取数: 优先 --data-file（离线）；否则调用同目录 get_market_data.py
（返回 price/avg_volume/market_cap/sector/industry，见其 fetch_batch 输出格式）。
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys
import time

# 强制 stdout/stderr 使用 UTF-8：避免 GBK 控制台下打印中文/符号崩溃
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_FILE = os.path.join(_SCRIPT_DIR, "pool_entries.json")
_SEED_FILE = os.path.join(_SCRIPT_DIR, "stock_pool.json")
_GET_DATA = os.path.join(_SCRIPT_DIR, "get_market_data.py")

# ===== 手册硬阈值（修改须同步修改《选股操作手册.md》对应条款）=====
MIN_POOL = 10            # §5.1 候选池下限，<10 需补充
ACCEPT_MIN = 15          # §一 "候选数不足"的硬定义（方式一预期 15-30）
MAX_POOL = 40            # §5.1 候选池上限，>40 按市值截断至 40
MIN_SMALL_CAP_PCT = 0.20  # §5.3 小盘占比下限
MAX_MERGE = 10           # §七 单次池外并入上限
MIN_SECTORS = 3          # §5.2 板块覆盖下限
MIN_PRICE = 3.0          # §5.4 股价下限
MIN_AVG_VOLUME = 200_000  # §5.4 日均成交量下限
SMALL_LO, SMALL_HI = 1e9, 10e9   # 小盘市值区间
MID_HI = 200e9           # 大盘下限（>200B 为大盘）

# ===== 性能与预算阈值（2026-10-18 修复"做多做空分析缓慢"；改阈值须同步 Task 文档）=====
PREFILTER_MAX = 80          # A2: 取指标前最多保留候选数（仅 live 模式生效）
PROBE_MAX_TICKERS = 80      # A3: 池外探测差集"取指标验证"的上限（其余只留名）
PROBE_BUDGET_SECONDS = 150  # A3: 池外探测默认时间预算，超预算返回部分结果 + 未完成入口
ENTRY_TIMEOUT = 20          # A3: 单入口抓取超时（失败即降级，不重试）
ENTRY_WORKERS = 3           # A3: 抓页并发上限（对齐 get_market_data --concurrency 的限流口径）
METRICS_BATCH = 40          # A1: 每次 get_market_data 子进程取数只数
METRICS_TIMEOUT = 300       # A1: 单批取数超时（原 180s 在 40 只/批下偏短）


# ====================== 基础工具 ======================

def load_config(path=None):
    path = path or _CONFIG_FILE
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_seed_pool(path=None):
    """读取 stock_pool.json → {"by_ticker": {ticker: {sector, tier}}, "by_sector": {gics_sector: [tickers]}}"""
    path = path or _SEED_FILE
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    by_ticker, by_sector = {}, {}
    for gics_code, info in data.get("sub_sectors", {}).items():
        sector_name = info.get("sector") or info.get("name") or gics_code
        by_sector.setdefault(sector_name, [])
        for tier in ("T1", "T2", "T3"):
            for t in info.get("tiers", {}).get(tier, []):
                if t not in by_ticker:
                    by_ticker[t] = {"sector": sector_name, "tier": tier, "name": info.get("name", "")}
                    by_sector[sector_name].append(t)
    return {"by_ticker": by_ticker, "by_sector": by_sector}


def bucket_of(mcap):
    """§3.1 分层口径：大盘 >$200B；中盘 $10B-$200B；小盘 $1B-$10B；其余 微型"""
    if mcap is None:
        return "未知"
    if mcap > MID_HI:
        return "大盘"
    if mcap >= 10e9:
        return "中盘"
    if mcap >= SMALL_LO:
        return "小盘"
    return "微型"


# 模块级别名缓存（首次使用时从 pool_entries.json 懒加载）
_ALIAS = {}


def _get_alias():
    global _ALIAS
    if not _ALIAS:
        try:
            _ALIAS = load_config().get("sector_alias", {})
        except Exception:  # noqa: BLE001 — 配置缺失时退化为包含式匹配
            _ALIAS = {}
    return _ALIAS


def sector_matches(manual_name, gics_name):
    """手册板块名 ↔ 固定池 GICS 板块名 匹配（别名 + 包含式）"""
    if not manual_name or not gics_name:
        return False
    alias = _get_alias()
    if alias.get(manual_name) == gics_name:
        return True
    a = re.sub(r"[（(].*?[)）]", "", manual_name)
    b = re.sub(r"[（(].*?[)）]", "", gics_name)
    return a in b or b in a


# ====================== 候选对象与过滤 ======================

def normalize_candidates(raw, source="入口"):
    """原始条目（str 或 dict）→ 标准候选 dict 列表，大写、去重（保留首个来源）"""
    out, seen = [], set()
    for item in raw or []:
        if isinstance(item, str):
            cand = {"ticker": item.strip(), "name": "", "source": source}
        elif isinstance(item, dict):
            cand = {
                "ticker": str(item.get("ticker", "")).strip(),
                "name": item.get("name", "") or "",
                "sector": item.get("sector", ""),
                "industry": item.get("industry", ""),
                "mcap": item.get("mcap", item.get("market_cap")),
                "price": item.get("price"),
                "avg_volume": item.get("avg_volume"),
                "source": item.get("source", source),
            }
        else:
            continue
        if not cand["ticker"]:
            continue
        cand["ticker"] = cand["ticker"].upper()
        if cand["ticker"] in seen:
            continue
        seen.add(cand["ticker"])
        out.append(cand)
    return out


def apply_exclusion_rules(cands):
    """§5.4 硬排除：股价<$3 / 日均量<20万股。缺数据不排除，打 data_missing 标。"""
    kept, excluded = [], []
    for c in cands:
        price, vol = c.get("price"), c.get("avg_volume")
        if price is not None and price < MIN_PRICE:
            excluded.append({"ticker": c["ticker"], "reason": "股价<$3"})
            continue
        if vol is not None and vol < MIN_AVG_VOLUME:
            excluded.append({"ticker": c["ticker"], "reason": "日均成交量<20万股"})
            continue
        if price is None or vol is None:
            c = dict(c)
            c["data_missing"] = True
        kept.append(c)
    return kept, excluded


def enforce_max_pool(cands):
    """§5.1 >40 按市值从大到小截断至 40（None 市值排最后被优先移除）"""
    ordered = sorted(cands, key=lambda c: (c.get("mcap") is not None, c.get("mcap") or 0), reverse=True)
    return ordered[:MAX_POOL], max(0, len(cands) - MAX_POOL)


def prefilter_candidates(cands, *, point_in_time=True, limit=PREFILTER_MAX):
    """A2: 取指标**前**的低成本预筛（用入口页自带的 price/avg_volume/mcap）。
    返回 (kept, dropped)。

    前视防护（本任务新增红线 1）：入口页自带数值是**当前快照**，
    `point_in_time=False`（即 --as-of 历史模式）时**不做任何预筛/按市值截断**，原序返回。
    """
    if not point_in_time:
        return list(cands), 0
    kept = []
    for c in cands:
        price, vol = c.get("price"), c.get("avg_volume")
        if price is not None and price < MIN_PRICE:
            continue
        if vol is not None and vol < MIN_AVG_VOLUME:
            continue
        kept.append(c)
    dropped = len(cands) - len(kept)
    if limit and len(kept) > limit:
        if any(c.get("mcap") is not None for c in kept):
            kept = sorted(kept, key=lambda c: (c.get("mcap") is not None, c.get("mcap") or 0),
                          reverse=True)[:limit]
        else:
            kept = kept[:limit]
        dropped = len(cands) - len(kept)
    return kept, dropped


def enforce_max_pool_tiered(cands, small_quota=None):
    """A2b: 分层配额截断 —— 先保证 §5.3 的小盘名额，剩余名额再按市值降序填。

    修 §5.1 与 §5.3 的结构性互斥：原 enforce_max_pool 纯按市值降序截断，
    必然把小盘全部截掉（实测 小盘占比_pct=0.0 / small_cap_insufficient=True），
    导致 §5.3 的补充工作（实测补 ~470 只）注定被丢弃。
    返回 (pool, removed)；≤MAX_POOL 时原样返回。
    """
    if len(cands) <= MAX_POOL:
        return list(cands), 0
    quota = small_quota if small_quota is not None else int(math.ceil(MAX_POOL * MIN_SMALL_CAP_PCT))

    def by_mcap(seq):
        return sorted(seq, key=lambda c: (c.get("mcap") is not None, c.get("mcap") or 0), reverse=True)

    small = [c for c in cands if c.get("mcap") is not None and SMALL_LO <= c["mcap"] <= SMALL_HI]
    small_ids = {id(c) for c in small}
    rest = [c for c in cands if id(c) not in small_ids]
    small_pick = by_mcap(small)[:quota]
    pool = small_pick + by_mcap(rest)[:MAX_POOL - len(small_pick)]
    if len(pool) < MAX_POOL:  # 小盘名额用不满 → 由 rest 补齐
        rest_ids = {id(c) for c in pool}
        pool += by_mcap([c for c in rest if id(c) not in rest_ids])[:MAX_POOL - len(pool)]
    return pool, max(0, len(cands) - MAX_POOL)


def small_cap_stats(cands):
    small = [c for c in cands if c.get("mcap") is not None and SMALL_LO <= c["mcap"] <= SMALL_HI]
    pct = (len(small) / len(cands)) if cands else 0.0
    return {"count": len(small), "pct": pct}


def supplement_small_cap(cands, small_source):
    """§5.3 占比 <20% 时从 small_source 逐只补充（市值大者优先），直至达标或耗尽"""
    stats = small_cap_stats(cands)
    pool = list(cands)
    existing = {c["ticker"] for c in pool}
    added = []
    source = sorted(
        [c for c in small_source if c["ticker"] not in existing],
        key=lambda c: (c.get("mcap") or 0), reverse=True,
    )
    for c in source:
        if small_cap_stats(pool)["pct"] >= MIN_SMALL_CAP_PCT:
            break
        pool.append(dict(c, source=c.get("source", "小盘补充")))
        added.append(c["ticker"])
    insufficient = small_cap_stats(pool)["pct"] < MIN_SMALL_CAP_PCT
    return pool, {"added": added, "insufficient": insufficient}


def supplement_small_cap_quota(cands, small_source, target_small=None):
    """A2b: 按**最终池的小盘名额**补齐（而非当前池的 20%）。

    原 supplement_small_cap 以"当前池占比 ≥20%"为停止条件：候选池有几百只时，
    需要补几十上百只小盘才"达标"，而这些补充标的随后又被 §5.1 市值降序截断全部丢弃
    （实测补 ~470 只、截断移除 416 只、最终小盘占比仍 0.0）。
    本函数只补到 ceil(min(MAX_POOL, max(len,MIN_POOL)) × 20%) 的名额，
    且只选入口页**已带市值且落在小盘区间**的标的（不为选择而全量取指标）。
    """
    base = min(MAX_POOL, max(len(cands), MIN_POOL))
    target = target_small if target_small is not None else int(math.ceil(base * MIN_SMALL_CAP_PCT))
    pool = list(cands)
    existing = {c["ticker"] for c in pool}
    have_small = small_cap_stats(pool)["count"]
    need = max(0, target - have_small)
    added = []
    if need:
        eligible = [c for c in (small_source or [])
                    if c["ticker"] not in existing
                    and c.get("mcap") is not None and SMALL_LO <= c["mcap"] <= SMALL_HI]
        for c in sorted(eligible, key=lambda c: c["mcap"], reverse=True):
            if len(added) >= need:
                break
            pool.append(dict(c, source=c.get("source", "小盘补充")))
            existing.add(c["ticker"])
            added.append(c["ticker"])
    # insufficient 语义（A2b）：按"名额是否凑齐"判定，而非当前池占比——
    # 补齐后还要过 §5.1 分层截断，用截断前的池算占比会永远判为不足（原实现的假阴性来源）
    insufficient = (have_small + len(added)) < target
    return pool, {"added": added, "insufficient": insufficient,
                  "名额": target, "已有小盘": have_small, "需补": need}


def diversity_stats(cands):
    sectors = sorted({c.get("sector") for c in cands if c.get("sector")})
    return {"sectors": sectors, "count": len(sectors)}


def supplement_diversity(cands, seed_by_sector):
    """§5.2 板块覆盖 <3 时，从固定池按缺失板块每板块补 2 只"""
    pool = list(cands)
    existing = {c["ticker"] for c in pool}
    added = []
    seed_by_sector = seed_by_sector or {}
    while diversity_stats(pool)["count"] < MIN_SECTORS:
        have = {c.get("sector") for c in pool}
        progressed = False
        for sec, tickers in seed_by_sector.items():
            if sec in have:
                continue
            for t in tickers:
                cand = dict(t) if isinstance(t, dict) else {"ticker": t, "name": ""}
                cand.setdefault("sector", sec)
                cand.setdefault("source", "固定池(多样性补充)")
                if cand["ticker"] not in existing:
                    pool.append(cand)
                    existing.add(cand["ticker"])
                    added.append(cand["ticker"])
                    progressed = True
                    break
            if diversity_stats(pool)["count"] >= MIN_SECTORS:
                break
        if not progressed:
            break
    insufficient = diversity_stats(pool)["count"] < MIN_SECTORS
    return pool, {"added": added, "insufficient": insufficient}


# ====================== §七 池外探测 ======================

def diff_outside(base_cands, outside_cands):
    base_t = {c["ticker"] for c in base_cands}
    return [c for c in outside_cands if c["ticker"] not in base_t]


def merge_outside(pool, outside_new, max_merge=MAX_MERGE):
    """并入市值最大的 max_merge 只；超出部分留痕"未并入原因"（§七 单次并入上限）"""
    ordered = sorted(outside_new, key=lambda c: (c.get("mcap") is not None, c.get("mcap") or 0), reverse=True)
    merged = [dict(c, source="池外探测") for c in ordered[:max_merge]]
    not_merged = [{"ticker": c["ticker"], "原因": f"超出单次并入上限({max_merge})"} for c in ordered[max_merge:]]
    return pool + merged, merged, not_merged


def load_pool_json(obj):
    """--probe-only 输入兼容：generate 结果 / 纯列表 / {"candidates": [...]}"""
    if isinstance(obj, dict):
        if "候选池" in obj:
            return [c["ticker"] for c in obj["候选池"]]
        for key in ("candidates", "pool", "tickers"):
            if key in obj:
                v = obj[key]
                return [c["ticker"] for c in v] if v and isinstance(v[0], dict) else list(v)
        raise ValueError("无法识别的候选池 JSON 结构")
    if isinstance(obj, list):
        return [c["ticker"] for c in obj] if obj and isinstance(obj[0], dict) else list(obj)
    raise ValueError("候选池输入必须是 JSON 对象或数组")


# ====================== 入口抓取（网络 + 解析器） ======================

# SOCKS5 代理（对齐 get_market_data.py：默认 127.0.0.1:10808，YF_SOCKS5 环境变量可覆盖）
_SOCKS5_ADDR = os.environ.get("YF_SOCKS5", "127.0.0.1:10808").lstrip("socks5h://").lstrip("socks5://")
_proxy_check_cache = {"time": 0.0, "available": False}
_PROXY_CHECK_TTL = 30.0
_FETCH_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) candidate-pool/1.0"}


def _has_curl_cffi():
    try:
        import curl_cffi  # noqa: F401
        return True
    except ImportError:
        return False


def _is_socks_proxy_available():
    """探测本机 SOCKS5 代理（默认 10808）是否开放，带 30s 缓存（逻辑同 get_market_data.py）"""
    import time
    now = time.time()
    if now - _proxy_check_cache["time"] < _PROXY_CHECK_TTL:
        return _proxy_check_cache["available"]
    host, _, port = _SOCKS5_ADDR.partition(":")
    available = False
    try:
        import socket
        s = socket.create_connection((host or "127.0.0.1", int(port or "10808")), timeout=1.5)
        s.close()
        available = True
    except Exception:
        available = False
    _proxy_check_cache["time"] = now
    _proxy_check_cache["available"] = available
    return available


def _proxy_get(url, timeout):
    """优先级①：curl_cffi 走 socks5h 代理直取（绕过本机 IP 风控）"""
    from curl_cffi import requests as cfr
    proxy_url = f"socks5h://{_SOCKS5_ADDR}"
    with cfr.Session(proxies={"http": proxy_url, "https": proxy_url}, timeout=timeout) as session:
        resp = session.get(url, headers=_FETCH_HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp.text


def fetch_url(url, timeout=20):
    """入口页面抓取。优先级：
    ① curl_cffi 已装 且 10808 SOCKS5 探测可用 → 走代理（对齐 get_market_data.py 策略）
    ② requests 直取（trust_env 默认开启，环境变量代理 HTTP(S)_PROXY 仍隐式生效）
    两层失败均抛异常 → 由 try_entries 记录降级并留痕。
    """
    if _has_curl_cffi() and _is_socks_proxy_available():
        return _proxy_get(url, timeout)
    import requests
    r = requests.get(url, headers=_FETCH_HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.text


_NUM_SUFFIX = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def _parse_number(text):
    """'256.32B' / '1,234,567' / '$3.20' / '(1.5M)' → float；不可解析返回 None"""
    if not text:
        return None
    t = text.strip().replace(",", "").replace("$", "").replace("%", "")
    neg = t.startswith("(") and t.endswith(")")
    if neg:
        t = t[1:-1]
    m = re.match(r"^([+-]?\d+(?:\.\d+)?)([KMBT])?$", t)
    if not m:
        return None
    val = float(m.group(1))
    if m.group(2):
        val *= _NUM_SUFFIX[m.group(2)]
    return -val if neg else val


def _table_rows(html):
    """逐行给出 (row_html, 去标签后的单元格文本)，用于按表头列名映射数值"""
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = [re.sub(r"<[^>]+>", " ", c).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.S)]
        yield row_html, cells


def _sa_row_metrics(html):
    """A4: 从 stockanalysis 表格按表头列名提取 ticker → {mcap/price/avg_volume}。
    表头不可得（如 holdings 页无 Market Cap 列）→ 返回 {}，调用方退化为只取 ticker/name。
    注意：页面自带数值是**当前快照**，历史模式不得用于预筛/排序（见 prefilter_candidates）。
    """
    out, headers = {}, None
    for row_html, cells in _table_rows(html):
        low = [c.lower() for c in cells]
        if headers is None:
            if any("market cap" in c for c in low) or any(c in ("price", "stock price") for c in low):
                headers = low
            continue
        m = re.search(r'href="/stocks/([A-Za-z.\-]{1,6})/?', row_html)
        if not m:
            continue
        rec = {}
        for i, h in enumerate(headers):
            if i >= len(cells):
                break
            val = _parse_number(cells[i])
            if val is None:
                continue
            if "market cap" in h:
                rec["mcap"] = val
            elif h in ("price", "stock price", "close"):
                rec["price"] = val
            elif "volume" in h and "market cap" not in h:
                rec["avg_volume"] = val
        if rec:
            out[m.group(1).upper()] = rec
    return out


def parse_stockanalysis_list(html):
    """stockanalysis 列表/holdings 表：提取 /stocks/TICKER/ 链接与名称；
    表头可得时一并提取 mcap/price/avg_volume（A4，供取指标前预筛）"""
    out = []
    metrics = _sa_row_metrics(html)
    seen = set()
    for m in re.finditer(r'href="/stocks/([A-Za-z.\-]{1,6})/?"[^>]*>([^<]{0,60})<', html):
        ticker, name = m.group(1).upper(), m.group(2).strip()
        if ticker in seen:
            continue
        seen.add(ticker)
        item = {"ticker": ticker, "name": name}
        item.update(metrics.get(ticker, {}))
        out.append(item)
    return out


def parse_finviz_table(html):
    """finviz 筛选页：提取 quote.ashx?t=TICKER 链接"""
    out = []
    for m in re.finditer(r'quote\.ashx\?t=([A-Za-z.\-]{1,6})', html):
        t = m.group(1).upper()
        if t not in out:
            out.append(t)
    return out


def run_entry(entry, fetcher=None, timeout=ENTRY_TIMEOUT):
    """执行单个入口：fetcher 注入（测试用）或真实网络抓取。返回候选列表，失败抛异常。"""
    if fetcher is not None:
        return fetcher(entry)
    html = fetch_url(entry["url"], timeout=timeout)
    ptype = entry["type"]
    if ptype == "stockanalysis_list":
        return parse_stockanalysis_list(html)
    if ptype == "stockanalysis_etf_holdings":
        return parse_stockanalysis_list(html)
    if ptype == "finviz_screener":
        return parse_finviz_table(html)
    raise ValueError(f"未知入口类型: {ptype}")


def dedup_entries(entries):
    """A3: 入口去重（etf-iwm 同时出现在 scan_entries 与 small_cap_entries → 同页抓两次）"""
    out, seen = [], set()
    for e in entries:
        key = (e.get("name"), e.get("url"), e.get("type"))
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def enabled_entries(entries):
    """C5（2026-10-18）：跳过 pool_entries.json 里标了 `"disabled": true` 的入口。

    实测 `finviz-screener`：页面能抓到（354 KB、无 Cloudflare 拦截），但表格已改为**客户端渲染**，
    HTML 里 0 个 quote.ashx 链接（带筛选参数也返回同一页）→ 解析器必然 0 条，白占一次抓取。
    做空向差异化不再依赖入口页，改由 get_market_data 的新字段承担
    （short_pct_of_float / short_days_to_cover / ps_ratio / ev_ebitda / fcf_yield / rsi_14）。
    """
    return [e for e in entries if not e.get("disabled")]


def _is_parallel_safe(entry):
    """A3: 仅 stockanalysis 类入口可并发；finviz 有反爬历史（实测返回 0 条），保持单发"""
    return str(entry.get("type", "")).startswith("stockanalysis_")


def try_entries(entries, fetcher, tried_log, *, concurrency=1, budget_seconds=0, t0=None):
    """按清单尝试入口，失败降级下一个并留痕（含每入口耗时）。
    A3: concurrency>1 且为真实网络时，对 stockanalysis 类入口分批并发（上限由调用方给）；
        budget_seconds>0 时超预算不再抓后续入口，记 "跳过(时间预算)" 并返回已抓到的部分结果。
    """
    t0 = time.time() if t0 is None else t0
    collected = []

    def _one(entry):
        name = entry.get("name", entry.get("url", "?"))
        started = time.time()
        try:
            items = run_entry(entry, fetcher) or []
            tried_log.append({"name": name, "status": f"成功({len(items)})", "count": len(items),
                              "elapsed_s": round(time.time() - started, 1)})
            return items
        except Exception as e:  # noqa: BLE001 — 降级是设计行为，留痕即可
            tried_log.append({"name": name, "status": f"失败: {type(e).__name__}: {e}", "count": 0,
                              "elapsed_s": round(time.time() - started, 1)})
            return []

    i = 0
    while i < len(entries):
        if budget_seconds and (time.time() - t0) > budget_seconds:
            for e in entries[i:]:
                tried_log.append({"name": e.get("name", "?"), "status": "跳过(时间预算)", "count": 0})
            break
        entry = entries[i]
        if concurrency > 1 and fetcher is None and _is_parallel_safe(entry):
            batch = []
            while i < len(entries) and len(batch) < concurrency and _is_parallel_safe(entries[i]):
                batch.append(entries[i])
                i += 1
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=len(batch)) as ex:
                for items in ex.map(_one, batch):
                    collected.extend(items)
        else:
            collected.extend(_one(entry))
            i += 1
    return collected


# ====================== 指标取数 ======================

# 取数埋点（D3）：只增字段，供 审计 输出；进程内累计，可用 reset_fetch_stats() 清零
_FETCH_STATS = {"tickers": 0, "calls": 0, "elapsed_s": 0.0, "failures": []}


def reset_fetch_stats():
    _FETCH_STATS.update({"tickers": 0, "calls": 0, "elapsed_s": 0.0, "failures": []})


def _child_env():
    """A1: 给子进程显式 UTF-8，避免 GBK 控制台下中文日志在父进程解码崩溃"""
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    return env


def metrics_via_script(tickers, max_per_call=METRICS_BATCH, end=None, no_cache=False):
    """调用 get_market_data.py 批量取指标（≤40 只/次）。返回 {ticker: metrics} 或 {}

    end/no_cache（P2-1）：透传 --end/--no-cache —— 历史模式取数走不可变窗口缓存，
    与 live 30 分钟缓存物理隔离；mcap 等 info 字段仍是当前快照（结果标
    fundamentals_point_in_time=false 由调用方负责）。

    A1（2026-10-18）：`errors="replace"` + 子进程 UTF-8 环境 —— 原严格 UTF-8 解码在
    子进程输出 GBK 中文日志时使 `_readerthread` 崩溃、整批结果丢弃（实测首跑 423s 全废）。
    失败批次记入 _FETCH_STATS["failures"]，已成功批次照常保留。
    """
    result = {}
    for i in range(0, len(tickers), max_per_call):
        chunk = tickers[i:i + max_per_call]
        started = time.time()
        _FETCH_STATS["calls"] += 1
        _FETCH_STATS["tickers"] += len(chunk)
        try:
            cmd = [sys.executable, _GET_DATA, "--market", "us_stocks",
                   "--tickers", ",".join(chunk), "--output", "json"]
            if end:
                cmd += ["--end", end]
            if no_cache:
                cmd += ["--no-cache"]
            proc = subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=METRICS_TIMEOUT,
                encoding="utf-8", errors="replace", env=_child_env(),
            )
            payload = json.loads(proc.stdout)
            for item in payload.get("data", []):
                t = str(item.get("ticker", "")).upper()
                if t:
                    result[t] = {
                        "price": item.get("price"),
                        "avg_volume": item.get("avg_volume"),
                        "mcap": item.get("market_cap"),
                        "sector": item.get("sector", ""),
                        "industry": item.get("industry", ""),
                        "name": item.get("name", ""),
                        # C1/C3/C4（2026-10-18 只增字段）：把做空 / 估值 / 技术面一并带进候选池，
                        # 让 00_index Step 3+4 不必为这些口径再调一次 get_market_data 或 web_fetch
                        "pe_ratio": item.get("pe_ratio"),
                        "pb_ratio": item.get("pb_ratio"),
                        "roe": item.get("roe"),
                        "revenue_growth": item.get("revenue_growth"),
                        "earnings_growth": item.get("earnings_growth"),
                        "profit_margins": item.get("profit_margins"),
                        "ps_ratio": item.get("ps_ratio"),
                        "peg_ratio": item.get("peg_ratio"),
                        "ev_ebitda": item.get("ev_ebitda"),
                        "fcf_yield": item.get("fcf_yield"),
                        "net_debt_to_ebitda": item.get("net_debt_to_ebitda"),
                        "short_interest": item.get("short_interest"),
                        "short_days_to_cover": item.get("short_days_to_cover"),
                        "short_pct_of_float": item.get("short_pct_of_float"),
                        "rsi_14": item.get("rsi_14"),
                        "macd_hist": item.get("macd_hist"),
                    }
        except Exception as e:  # noqa: BLE001 — 取数失败不打断，标 data_missing
            stderr_tail = (getattr(e, "stderr", None) or "")[-300:]
            _FETCH_STATS["failures"].append({
                "只数": len(chunk), "错误": f"{type(e).__name__}: {e}",
                "stderr摘要": stderr_tail,
            })
            print(f"[WARN] get_market_data.py 批次取数失败({len(chunk)}只): {e}", file=sys.stderr)
        finally:
            _FETCH_STATS["elapsed_s"] += time.time() - started
    return result


def load_data_file(path):
    """--data-file：离线指标文件。兼容 get_market_data 输出或 {ticker: metrics}"""
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if isinstance(payload, dict) and "data" in payload:
        payload = payload["data"]
    out = {}
    if isinstance(payload, list):
        for item in payload:
            t = str(item.get("ticker", "")).upper()
            if t:
                out[t] = item
    elif isinstance(payload, dict):
        out = {str(k).upper(): v for k, v in payload.items()}
    return out


def enrich_metrics(cands, provider, fill_all=False):
    """用 provider（dict 或 callable）补齐 price/avg_volume/mcap/sector/industry/name。

    fill_all=False（原语义）：只请求 price 缺失的候选。
    fill_all=True（A2 收尾补全）：核心字段任一缺失即请求，并**逐字段**补齐——
    入口页自带的 price/avg_volume/mcap 保留，缺的（多为 avg_volume/mcap/sector）由取数补上。
    没有这一步，A4 的页面自带 price 会让这些标的被跳过，池内出现大量缺字段（实测 28/40）。
    """
    if not provider:
        return cands
    core = ("price", "avg_volume", "mcap")
    if fill_all:
        tickers = [c["ticker"] for c in cands if any(c.get(k) is None for k in core)]
    else:
        tickers = [c["ticker"] for c in cands if c.get("price") is None]
    if callable(provider):
        data = provider(tickers) or {}
    else:
        data = provider
    out = []
    for c in cands:
        m = data.get(c["ticker"])
        if m:
            c = dict(c)
            for key in ("price", "avg_volume", "mcap", "sector", "industry", "name",
                        # C1/C3/C4（2026-10-18 只增）做空 / 估值 / 技术面
                        "pe_ratio", "pb_ratio", "roe", "revenue_growth", "earnings_growth",
                        "profit_margins", "ps_ratio", "peg_ratio", "ev_ebitda", "fcf_yield",
                        "net_debt_to_ebitda", "short_interest", "short_days_to_cover",
                        "short_pct_of_float", "rsi_14", "macd_hist"):
                if c.get(key) in (None, ""):
                    val = m.get(key)
                    if val is None and key == "mcap":
                        val = m.get("market_cap")
                    if val is not None:
                        c[key] = val
        out.append(c)
    return out


# ====================== 主流程 ======================

def _sector_seed_candidates(cfg, sector):
    """方式一保底：手册 §2.3 板块核心+卫星种子表（core+satellite 各 3-5 只）"""
    node = cfg.get("sector_seed", {}).get(sector)
    if not node:
        return []
    out = []
    for kind in ("core", "satellite"):
        for t in node.get(kind, [])[:5]:
            out.append({"ticker": t, "name": "", "sector": sector, "source": f"种子表-{kind}"})
    return out


def _sector_etf_entries(cfg, sector):
    """方式一入口：板块 ETF holdings（固定映射，不自造入口）"""
    out = []
    for sym in cfg.get("sector_etfs", {}).get(sector, []):
        out.append({"name": f"etf-{sym.lower()}", "type": "stockanalysis_etf_holdings",
                    "url": f"https://stockanalysis.com/etf/{sym.lower()}/holdings/"})
    return out


# ===== P1-3 接线（2026-10-18）：--as-of 下把"成员维度"从不可回溯升级为可回溯 =====
# 数据源＝同目录 fetch_index_constituents.py（SEC NPORT-P 官方档案，双重门控 PIT）。
# **延迟加载**：live 路径（as_of=None）完全不 import、不取数，生产行为零变化。
_CONSTITUENTS_SCRIPT = os.path.join(_SCRIPT_DIR, "fetch_index_constituents.py")


def constituents_layer(as_of):
    """取 as-of 当期官方成分档案；任何异常都降级为 available=False（不抛、不假装有成分）。"""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("fetch_index_constituents",
                                                      _CONSTITUENTS_SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.constituents_for_asof(as_of)
    except Exception as e:
        return {"available": False, "requested_as_of": as_of,
                "note": f"成分层不可用（{type(e).__name__}: {e}）→ 成员维度退回 point_in_time=false"}


def apply_constituents(cands, info, mode="annotate"):
    """按当期官方成分给候选打 `in_constituents` 标记；mode=filter 时剔除明确 False。

    三态判定（**没有第三态就会把小盘/板块候选误判成"不是成员"**）：
      True    ticker ∈ 当期成分
      False   ticker ∈ 覆盖范围参照集（当期 ∪ 最新一期）但当期不在 → 幸存者偏差信号
      None    覆盖范围外（小盘 / 板块 ETF 候选）→ unknown，不参与判定、filter 模式不剔除
    返回 (candidates, stats)。
    """
    cur = {str(t).upper() for t in info.get("symbols", [])}
    scope = {str(t).upper() for t in info.get("scope_symbols", [])}
    kept, dropped, n_t, n_f, n_u = [], [], 0, 0, 0
    for c in cands:
        t = str(c.get("ticker") or "").upper()
        if t in cur:
            flag = True
            n_t += 1
        elif t in scope:
            flag = False
            n_f += 1
        else:
            flag = None
            n_u += 1
        c["in_constituents"] = flag
        if mode == "filter" and flag is False:
            dropped.append(c.get("ticker"))
            continue
        kept.append(c)
    stats = {"mode": mode, "当期成员命中": n_t, "覆盖范围内但当期非成员": n_f,
             "覆盖范围外(unknown)": n_u, "filter剔除数": len(dropped),
             "filter剔除样本": dropped[:12]}
    return kept, stats


def _constituents_brief(info, stats):
    """结果 JSON 里只留口径与计数，**不留 symbols 明细**（900 个 ticker 会撑爆报告）。"""
    if info is None:
        return None
    return {
        "available": bool(info.get("available")),
        "source": "sec_nport_p:SPY+MDY",
        "requested_as_of": info.get("requested_as_of"),
        "holdings_as_of": info.get("holdings_as_of"),
        "lag_days": info.get("lag_days"),
        "match_rate": info.get("match_rate"),
        "members_total": info.get("members_total"),
        "unmatched_count": info.get("unmatched_count"),
        "failed_indexes": info.get("failed_indexes", []),
        "note": info.get("note"),
        "判定": stats,
    }


def generate(mode="auto", direction="long", sector=None, user_tickers=None, *,
             entries_config=None, fetcher=None, data_provider=None, seed_pool=None,
             small_cap_source=None, as_of=None, prefilter_max=PREFILTER_MAX,
             budget_seconds=0, constituents="annotate"):
    """候选池生成主流程（§一优先级链 + §5 质量控制，全程留痕）

    as_of（P2-1/A2）：历史模式标记。非 None 时**禁用取指标前的预筛与按市值截断**
    ——入口榜单页自带 price/avg_volume/mcap 是当前快照，用它筛＝引入前视。

    constituents（P1-3 接线，仅 as_of 非 None 时生效）：`off` 不做成分对照；
    `annotate`（默认）给候选打 in_constituents 三态标记并留痕；`filter` 额外剔除
    "覆盖范围内但当期非成员"的候选（＝今天榜单有、as-of 当期官方成分没有）。
    """
    cfg = entries_config or load_config()
    global _ALIAS
    _ALIAS = cfg.get("sector_alias", {})
    seed_pool = seed_pool or load_seed_pool()
    tried_log, degrade_log = [], []
    t0 = time.time()
    conc = ENTRY_WORKERS if fetcher is None else 1

    candidates = []
    mode_used = None

    # ---- 方式三：用户指定 ----
    if mode == "user":
        if not user_tickers:
            raise ValueError("mode=user 需要 --tickers")
        candidates = normalize_candidates(user_tickers.split(","), source="用户指定")
        mode_used = "用户指定"

    # ---- 方式一：板块衍生 ----
    if mode_used is None and sector:
        local = _sector_seed_candidates(cfg, sector)
        etf_cands = try_entries(dedup_entries(enabled_entries(_sector_etf_entries(cfg, sector))),
                                fetcher, tried_log,
                                concurrency=conc, budget_seconds=budget_seconds, t0=t0)
        candidates = normalize_candidates(
            [c if isinstance(c, dict) else c for c in local] + etf_cands,
            source="板块衍生",
        )
        if len(candidates) < ACCEPT_MIN:
            degrade_log.append(f"板块衍生候选 {len(candidates)} < {ACCEPT_MIN}，降级方式二")
        else:
            mode_used = "板块衍生"

    # ---- 方式二：全网扫描 ----
    if mode_used is None and mode in ("auto", "scan"):
        scan_cands = try_entries(dedup_entries(enabled_entries(cfg.get("scan_entries", []))),
                                 fetcher, tried_log,
                                 concurrency=conc, budget_seconds=budget_seconds, t0=t0)
        merged = candidates + scan_cands
        candidates = normalize_candidates(merged, source="全网扫描")
        if len(candidates) < ACCEPT_MIN:
            degrade_log.append(f"全网扫描后候选 {len(candidates)} < {ACCEPT_MIN}，降级固定池")
        else:
            mode_used = mode_used or "全网扫描"

    # ---- 兜底：固定池 ----
    if (mode_used is None or len(candidates) < MIN_POOL) and mode != "user":
        seed_cands = []
        for t, info in seed_pool["by_ticker"].items():
            if sector and not sector_matches(sector, info["sector"]):
                continue
            seed_cands.append({"ticker": t, "name": "", "sector": info["sector"],
                               "tier": info["tier"], "source": "固定池"})
        candidates = normalize_candidates(candidates + seed_cands, source="固定池")
        if mode_used is None:
            mode_used = "固定池兜底"

    # ---- P1-3 接线：成员维度分段标记（仅 --as-of；live 完全不碰这层）----
    # 档案可用 → 成员维度可回溯（members_point_in_time=true，引 holdings_as_of）；
    # 档案不可用（as-of 早于 2019-09-30 / 取数失败）→ 退回原语义，只留 note。
    const_info = const_stats = None
    if as_of and constituents != "off":
        const_info = constituents_layer(as_of)
        if const_info.get("available"):
            candidates, const_stats = apply_constituents(candidates, const_info,
                                                         mode=constituents)

    # ---- A2: 取指标**前**预筛截断（仅 live；--as-of 历史模式禁用，防前视）----
    point_in_time = as_of is None
    entry_count = len(candidates)
    candidates, prefilter_dropped = prefilter_candidates(
        candidates, point_in_time=point_in_time, limit=prefilter_max)

    # ---- §5.4 排除（先剔除不合格，再做质量控制补充）----
    candidates = enrich_metrics(candidates, data_provider)
    candidates, excluded = apply_exclusion_rules(candidates)

    # ---- §5.2 多样性补充 → §5.3 小盘补充（用户指定模式不做，§四仅校验）----
    audit_div, audit_small = {"added": [], "insufficient": False}, {"added": [], "insufficient": False}
    if mode != "user":
        candidates, audit_div = supplement_diversity(candidates, seed_pool["by_sector"])
        if small_cap_source is None and cfg.get("small_cap_entries"):
            small_cap_source = normalize_candidates(
                try_entries(dedup_entries(enabled_entries(cfg["small_cap_entries"])),
                            fetcher, tried_log,
                            concurrency=conc, budget_seconds=budget_seconds, t0=t0),
                source="小盘入口")
        # A2b: 按最终池的小盘名额补齐（原按"当前池 20%"补齐 → 补几百只又被 §5.1 截断丢弃）
        candidates, audit_small = supplement_small_cap_quota(candidates, small_cap_source or [])
        # 补充标的同样要过指标与排除（enrich_metrics 只请求缺 price 的增量，不重取全池）
        candidates = enrich_metrics(candidates, data_provider)
        candidates, excluded2 = apply_exclusion_rules(candidates)
        excluded = excluded + excluded2

    # ---- §5.1 数量控制（A2b: 分层配额截断，先保小盘名额）----
    candidates, removed = enforce_max_pool_tiered(candidates)

    # ---- A2 收尾补全：只对最终池（≤40）补全缺字段，再过一次 §5.4 保持"池内全部合格"----
    # （A4 让入口页自带 price/avg_volume/mcap，中段 enrich 会跳过它们 → 这里逐字段补齐，
    #   否则池内会出现大量缺字段；只对 ≤40 只做，成本 1-2 批）
    candidates = enrich_metrics(candidates, data_provider, fill_all=True)
    for c in candidates:
        c.pop("data_missing", None)
    candidates, excluded3 = apply_exclusion_rules(candidates)
    excluded = excluded + excluded3

    # ---- 汇总输出（字段与报告"候选池摘要"对应）----
    for c in candidates:
        c["bucket"] = bucket_of(c.get("mcap"))
    stats = small_cap_stats(candidates)
    div = diversity_stats(candidates)
    data_missing = [c["ticker"] for c in candidates if c.get("data_missing")]

    return {
        "mode_used": mode_used or "固定池兜底",
        "direction": direction,
        "sector": sector,
        "entries_tried": tried_log,
        "候选池": candidates,
        "排除": excluded,
        # P1-3 接线：成员维度独立标记（None=未启用，即 live 或 --constituents off）
        "constituents": _constituents_brief(const_info, const_stats),
        "members_point_in_time": bool(const_info and const_info.get("available")),
        "统计": {
            "总数": len(candidates),
            "小盘占比_pct": round(stats["pct"] * 100, 1),
            # 只增字段：最终池占比是否达标（small_cap_insufficient 语义＝小盘"名额"未凑齐；
            # 二者可能不一致，例如收尾 §5.4 又排掉一只小盘 → 名额凑齐但占比略低于 20%）
            "小盘占比达标": stats["pct"] >= MIN_SMALL_CAP_PCT,
            "small_cap_insufficient": audit_small["insufficient"],
            "板块数": div["count"],
            "板块": div["sectors"],
            "data_missing": data_missing,
            "diversity_insufficient": audit_div["insufficient"],
        },
        "审计": {
            "补充小盘": audit_small["added"],
            "多样性补充": audit_div["added"],
            "截断移除数": removed,
            "降级记录": degrade_log,
            # ---- 2026-10-18 性能修复新增（只增字段，不改既有键）----
            "入口原始数": entry_count,
            "预筛剔除数": prefilter_dropped,
            "预筛生效": point_in_time,
            "小盘名额": audit_small.get("名额"),
            "小盘需补": audit_small.get("需补"),
            "指标取数只数": _FETCH_STATS["tickers"],
            "指标取数批次": _FETCH_STATS["calls"],
            "指标取数耗时_s": round(_FETCH_STATS["elapsed_s"], 1),
            "取数失败批次": _FETCH_STATS["failures"],
            "收尾补全排除数": len(excluded3),
            "elapsed_s": round(time.time() - t0, 1),
        },
    }


def probe(base_pool_tickers, *, entries_config=None, fetcher=None, data_provider=None,
          seed_pool=None, max_merge=MAX_MERGE, max_tickers=PROBE_MAX_TICKERS,
          budget_seconds=PROBE_BUDGET_SECONDS, entry_names=None, concurrency=ENTRY_WORKERS):
    """§七 池外探测：固定入口清单取当期全市场清单 → 差集 → 指标验证 → 并入(≤10) → 留痕

    A3（2026-10-18）：
      - 入口去重（etf-iwm 重复）+ 可选 entry_names 子集；
      - stockanalysis 类入口分批并发（上限 ENTRY_WORKERS），finviz 单发；
      - budget_seconds 超预算 → 返回已抓到的部分结果 + 未完成入口留痕（不再被外层 timeout 全杀）；
      - 差集按市值排序只验证前 max_tickers 只（实测 802 只全验证只为并入 10 只），
        其余只留名并记 未验证候选数。
    """
    cfg = entries_config or load_config()
    seed_pool = seed_pool or load_seed_pool()
    tried_log = []
    t0 = time.time()

    configured = list(cfg.get("scan_entries", [])) + list(cfg.get("small_cap_entries", [])) \
        + list(cfg.get("probe_extra_entries", []))
    active = enabled_entries(configured)
    disabled_names = [e.get("name") for e in configured if e.get("disabled")]
    all_entries = dedup_entries(active)
    if entry_names:
        wanted = {str(n).strip() for n in entry_names if str(n).strip()}
        all_entries = [e for e in all_entries if e.get("name") in wanted]
    raw = try_entries(all_entries, fetcher, tried_log,
                      concurrency=concurrency if fetcher is None else 1,
                      budget_seconds=budget_seconds, t0=t0)
    outside = normalize_candidates(raw, source="池外探测")

    # 与固定池重复的 → 标注来源固定池，不重复计数
    base_set = {c["ticker"] if isinstance(c, dict) else str(c).upper() for c in base_pool_tickers}
    seed_tickers = set(seed_pool["by_ticker"].keys())
    diff = [c for c in outside if c["ticker"] not in base_set and c["ticker"] not in seed_tickers]
    seed_overlaps = [c["ticker"] for c in outside if c["ticker"] in seed_tickers]

    # A3: 只对市值可得的差集前 N 只取指标验证，其余留名（低成本预筛，仅 live）
    unverified = []
    if max_tickers and len(diff) > max_tickers:
        ranked = sorted(diff, key=lambda c: (c.get("mcap") is not None, c.get("mcap") or 0),
                        reverse=True)
        diff, unverified = ranked[:max_tickers], ranked[max_tickers:]

    diff = enrich_metrics(diff, data_provider)
    diff, excluded = apply_exclusion_rules(diff)

    pool = [{"ticker": t} for t in base_pool_tickers]
    pool, merged, not_merged = merge_outside(pool, diff, max_merge=max_merge)
    # 收尾补全：只补真正并入的 ≤max_merge 只（页面自带字段保留，缺的 sector/mcap 等补齐）
    merged = enrich_metrics(merged, data_provider, fill_all=True)

    return {
        "入口数": len(configured),
        "去重后入口数": len(all_entries),
        "禁用入口": disabled_names,          # C5：pool_entries.json 里 disabled=true 的入口
        "入口尝试": tried_log,
        "差集候选数": len(diff),
        "并入": merged,
        "未并入": not_merged,
        "排除": excluded,
        "与固定池重复": seed_overlaps,
        "统计": {
            "并入数": len(merged),
            "未并入数": len(not_merged),
            "排除数": len(excluded),
            "单次并入上限": max_merge,
        },
        # ---- 2026-10-18 性能修复新增（只增字段）----
        "审计": {
            "原始条目数": len(raw),
            "差集全量数": len(diff) + len(excluded) + len(unverified),
            "未验证候选数": len(unverified),
            "未验证候选": [c["ticker"] for c in unverified[:50]],
            "指标取数只数": _FETCH_STATS["tickers"],
            "指标取数批次": _FETCH_STATS["calls"],
            "指标取数耗时_s": round(_FETCH_STATS["elapsed_s"], 1),
            "取数失败批次": _FETCH_STATS["failures"],
            "时间预算_s": budget_seconds,
            "elapsed_s": round(time.time() - t0, 1),
        },
    }


# ====================== CLI ======================

def _summary_line(result, is_probe, out_path):
    """A6: --out 模式下 stdout 只打印一行摘要（完整结果已落盘，避免大 JSON 撑爆上下文）"""
    audit = result.get("审计", {})
    st = result.get("统计", {})
    parts = []
    if is_probe:
        parts.append(f"池外探测 入口{result.get('入口数')}（去重后 {result.get('去重后入口数')}）")
        parts.append(f"差集{result.get('差集候选数')} 并入{st.get('并入数')} "
                     f"未并入{st.get('未并入数')} 排除{st.get('排除数')}")
        parts.append(f"未验证{audit.get('未验证候选数', 0)}")
    else:
        parts.append(f"候选池 mode={result.get('mode_used')} 方向={result.get('direction')} "
                     f"板块={result.get('sector')}")
        parts.append(f"候选{st.get('总数')} 小盘{st.get('小盘占比_pct')}% 板块{st.get('板块数')}")
        parts.append(f"预筛剔除{audit.get('预筛剔除数', 0)} 截断移除{audit.get('截断移除数', 0)}")
    parts.append(f"取数{audit.get('指标取数只数', 0)}只/{audit.get('指标取数批次', 0)}批"
                 f"/{audit.get('指标取数耗时_s', 0)}s")
    if audit.get("取数失败批次"):
        parts.append(f"取数失败{len(audit['取数失败批次'])}批")
    parts.append(f"总耗时{audit.get('elapsed_s', 0)}s")
    parts.append(f"已写入 {out_path}")
    return " | ".join(parts)


def main():
    parser = argparse.ArgumentParser(
        description="美股候选池生成工具（《选股操作手册.md》代码化）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--mode", default="auto",
                        choices=["auto", "sector", "scan", "user", "seed"],
                        help="生成方式（默认 auto 按优先级链）")
    parser.add_argument("--direction", default="long", choices=["long", "short"])
    parser.add_argument("--sector", default=None, help="手册板块名，如 半导体 / AI / 科技")
    parser.add_argument("--tickers", default=None, help="mode=user 时的 ticker 列表，逗号分隔")
    parser.add_argument("--probe-only", dest="probe_only", default=None,
                        help="仅执行 §七 池外探测，输入现有候选池 JSON 文件")
    parser.add_argument("--entry-name", dest="entry_name", default=None,
                        help="调试单个入口（按 pool_entries.json 的 name）")
    parser.add_argument("--data-file", dest="data_file", default=None,
                        help="离线指标文件（跳过 get_market_data.py 取数）")
    parser.add_argument("--no-network", action="store_true",
                        help="禁用网络入口（仅固定池/用户列表/离线指标）")
    parser.add_argument("--config", default=None, help="入口配置文件（默认同目录 pool_entries.json）")
    parser.add_argument("--output", default="text", choices=["text", "json"])
    # ---------- 历史模式 / 沙箱（P2-1；缺省=现有生产行为） ----------
    parser.add_argument("--seed-pool", dest="seed_pool", default=None,
                        help="种子池 JSON 路径（回放副本沙箱化；缺省=同目录 stock_pool.json）")
    parser.add_argument("--as-of", dest="as_of", default=None, metavar="YYYY-MM-DD",
                        help="历史模式：向 get_market_data 透传 --end（入口榜单页仍是当前页，结果标 point_in_time=false）")
    parser.add_argument("--constituents", dest="constituents", default="annotate",
                        choices=["off", "annotate", "filter"],
                        help="仅 --as-of 生效（P1-3 接线）：annotate=按 SEC NPORT-P 官方成分打 "
                             "in_constituents 三态标记（默认）；filter=额外剔除「覆盖范围内但当期非成员」"
                             "的候选；off=不做成分对照（成员维度退回不可回溯）")
    parser.add_argument("--no-cache", dest="no_cache", action="store_true",
                        help="透传 get_market_data --no-cache")
    # ---------- 性能与预算（2026-10-18 修复"做多做空分析缓慢"） ----------
    parser.add_argument("--out", dest="out", default=None,
                        help="结果 JSON 落盘路径；给了 --out 时 stdout 只打印一行摘要（A6）")
    parser.add_argument("--prefilter-max", dest="prefilter_max", type=int, default=PREFILTER_MAX,
                        help=f"取指标前最多保留候选数（默认 {PREFILTER_MAX}，0=不截断；--as-of 下自动禁用）")
    parser.add_argument("--probe-max-tickers", dest="probe_max_tickers", type=int,
                        default=PROBE_MAX_TICKERS,
                        help=f"池外探测差集取指标上限（默认 {PROBE_MAX_TICKERS}，0=全量验证）")
    parser.add_argument("--probe-entries", dest="probe_entries", default=None,
                        help="池外探测入口子集，逗号分隔（如 sa-sp500,sa-smallcap）")
    parser.add_argument("--budget-seconds", dest="budget_seconds", type=int, default=None,
                        help=f"抓页时间预算，超预算返回部分结果+未完成入口"
                             f"（generate 默认 0=不限，probe 默认 {PROBE_BUDGET_SECONDS}）")
    args = parser.parse_args()

    cfg = load_config(args.config)
    seed_pool = load_seed_pool(args.seed_pool) if args.seed_pool else None

    if args.entry_name:
        all_entries = (cfg.get("scan_entries", []) + cfg.get("small_cap_entries", [])
                       + cfg.get("probe_extra_entries", []))
        for sec, etfs in cfg.get("sector_etfs", {}).items():
            for sym in etfs:
                all_entries.append({"name": f"etf-{sym.lower()}",
                                    "type": "stockanalysis_etf_holdings",
                                    "url": f"https://stockanalysis.com/etf/{sym.lower()}/holdings/"})
        entry = next((e for e in all_entries if e.get("name") == args.entry_name), None)
        if not entry:
            print(f"未找到入口: {args.entry_name}")
            sys.exit(1)
        try:
            items = run_entry(entry)
        except Exception as e:  # noqa: BLE001 — 调试模式直接报错退出
            print(f"❌ 入口 {args.entry_name} 失败: {type(e).__name__}: {e}")
            sys.exit(1)
        print(json.dumps(items, ensure_ascii=False, indent=2)[:4000])
        return

    fetcher = None
    if args.no_network:
        def fetcher(entry):
            raise RuntimeError("no-network 模式")
    provider = None
    if args.data_file:
        provider = load_data_file(args.data_file)
    elif args.as_of or args.no_cache:
        def provider(tickers, max_per_call=40):
            return metrics_via_script(tickers, max_per_call,
                                      end=args.as_of, no_cache=args.no_cache)
    else:
        provider = metrics_via_script

    reset_fetch_stats()
    budget = args.budget_seconds if args.budget_seconds is not None else (
        PROBE_BUDGET_SECONDS if args.probe_only else 0)
    if args.probe_only:
        with open(args.probe_only, "r", encoding="utf-8") as f:
            base = load_pool_json(json.load(f))
        result = probe(base, entries_config=cfg, fetcher=fetcher,
                       data_provider=provider, seed_pool=seed_pool,
                       max_tickers=args.probe_max_tickers, budget_seconds=budget,
                       entry_names=(args.probe_entries.split(",") if args.probe_entries else None))
    else:
        result = generate(
            mode=args.mode, direction=args.direction, sector=args.sector,
            user_tickers=args.tickers, entries_config=cfg, fetcher=fetcher,
            data_provider=provider, small_cap_source=None, seed_pool=seed_pool,
            as_of=args.as_of, prefilter_max=args.prefilter_max, budget_seconds=budget,
            constituents=args.constituents,
        )

    # 历史模式元信息（只增字段）。两个维度分开标：
    #   point_in_time        入口榜单页本身能否回溯（stockanalysis/ETF holdings 页只有当前页 → False）
    #   members_point_in_time 成员集合能否回溯（P1-3：SEC NPORT-P 档案可用 → True）
    if args.as_of:
        result["requested_end"] = args.as_of
        result["point_in_time"] = False
        if result.get("members_point_in_time"):
            c = result.get("constituents") or {}
            result["point_in_time_note"] = (
                "入口榜单页仍为当前页（point_in_time=false），但**成员集合已按 SEC NPORT-P 官方档案"
                f"回溯**：持仓期末 {c.get('holdings_as_of')}（滞后 {c.get('lag_days')} 天，"
                f"匹配率 {c.get('match_rate')}）——榜单里「当期有、当时不是成员」的标的已在 "
                "in_constituents=false 标出（--constituents filter 可直接剔除）")
        else:
            result["point_in_time_note"] = ("入口榜单（指数/ETF holdings 页）为当前页，"
                                            "价格/流动性指标已按 --end 回溯，榜单成员不可回溯"
                                            "（成分档案不可用："
                                            + str((result.get("constituents") or {}).get("note")) + "）")

    # A6: 显式 --out 时完整结果落盘，stdout 只给一行摘要（默认行为不变，保护 subprocess 消费者）
    if args.out:
        out_dir = os.path.dirname(os.path.abspath(args.out))
        os.makedirs(out_dir, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(_summary_line(result, bool(args.probe_only), args.out))
        return

    if args.output == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.probe_only:
        stats = result["统计"]
        print("=" * 60)
        print(f"池外探测（§七）  现有池 {len(base)} 只  入口 {result['入口数']} 个")
        print("=" * 60)
        print(f"  差集候选: {result['差集候选数']} 只  "
              f"并入: {stats['并入数']}  未并入: {stats['未并入数']}  排除: {stats['排除数']}")
        audit = result.get("审计", {})
        print(f"  取数: {audit.get('指标取数只数', 0)}只/{audit.get('指标取数批次', 0)}批"
              f"  未验证: {audit.get('未验证候选数', 0)}  时间预算: {audit.get('时间预算_s')}s"
              f"  总耗时: {audit.get('elapsed_s', 0)}s")
        for e in result.get("入口尝试", []):
            print(f"    {e['name']:18s} {e['status']}")
        for n in result.get("未并入", [])[:10]:
            print(f"    [未并入] {n['ticker']:8s} {n['原因']}")
    else:
        stats = result["统计"]
        print("=" * 60)
        print(f"候选池生成  mode={result.get('mode_used')}  方向={result.get('direction')}  板块={result.get('sector')}")
        print("=" * 60)
        print(f"  候选总数: {stats['总数']}  小盘占比: {stats['小盘占比_pct']}%  板块数: {stats['板块数']}")
        _a = result.get("审计", {})
        print(f"  取数: {_a.get('指标取数只数', 0)}只/{_a.get('指标取数批次', 0)}批"
              f"/{_a.get('指标取数耗时_s', 0)}s  预筛剔除: {_a.get('预筛剔除数', 0)}"
              f"  小盘名额: {_a.get('小盘名额')}  总耗时: {_a.get('elapsed_s', 0)}s")
        if result.get("排除"):
            print(f"  排除 {len(result['排除'])} 只:")
            for e in result["排除"][:10]:
                print(f"    {e['ticker']:8s} {e['reason']}")
        audit = result.get("审计", {})
        if audit.get("截断移除数"):
            print(f"  截断移除: {audit['截断移除数']} 只")
        for line in audit.get("降级记录", []):
            print(f"  [降级] {line}")
        print(f"\n  入口尝试:")
        for e in result.get("entries_tried", []):
            print(f"    {e['name']:18s} {e['status']}")


if __name__ == "__main__":
    main()
