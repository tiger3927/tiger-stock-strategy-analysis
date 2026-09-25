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
import os
import re
import subprocess
import sys

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


def parse_stockanalysis_list(html):
    """stockanalysis 列表/holdings 表：提取 /stocks/TICKER/ 链接与名称"""
    out = []
    for m in re.finditer(r'href="/stocks/([A-Za-z.\-]{1,6})/?"[^>]*>([^<]{0,60})<', html):
        ticker, name = m.group(1).upper(), m.group(2).strip()
        if ticker not in {c["ticker"] for c in out}:
            out.append({"ticker": ticker, "name": name})
    return out


def parse_finviz_table(html):
    """finviz 筛选页：提取 quote.ashx?t=TICKER 链接"""
    out = []
    for m in re.finditer(r'quote\.ashx\?t=([A-Za-z.\-]{1,6})', html):
        t = m.group(1).upper()
        if t not in out:
            out.append(t)
    return out


def run_entry(entry, fetcher=None):
    """执行单个入口：fetcher 注入（测试用）或真实网络抓取。返回候选列表，失败抛异常。"""
    if fetcher is not None:
        return fetcher(entry)
    html = fetch_url(entry["url"])
    ptype = entry["type"]
    if ptype == "stockanalysis_list":
        return parse_stockanalysis_list(html)
    if ptype == "stockanalysis_etf_holdings":
        return parse_stockanalysis_list(html)
    if ptype == "finviz_screener":
        return parse_finviz_table(html)
    raise ValueError(f"未知入口类型: {ptype}")


def try_entries(entries, fetcher, tried_log):
    """按固定顺序尝试入口清单，失败降级下一个并留痕"""
    collected = []
    for entry in entries:
        name = entry.get("name", entry.get("url", "?"))
        try:
            items = run_entry(entry, fetcher)
            collected.extend(items)
            tried_log.append({"name": name, "status": f"成功({len(items)})", "count": len(items)})
        except Exception as e:  # noqa: BLE001 — 降级是设计行为，留痕即可
            tried_log.append({"name": name, "status": f"失败: {type(e).__name__}: {e}", "count": 0})
    return collected


# ====================== 指标取数 ======================

def metrics_via_script(tickers, max_per_call=40):
    """调用 get_market_data.py 批量取指标（≤40 只/次）。返回 {ticker: metrics} 或 {}"""
    result = {}
    for i in range(0, len(tickers), max_per_call):
        chunk = tickers[i:i + max_per_call]
        try:
            proc = subprocess.run(
                [sys.executable, _GET_DATA, "--market", "us_stocks",
                 "--tickers", ",".join(chunk), "--output", "json"],
                capture_output=True, text=True, timeout=180, encoding="utf-8",
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
                    }
        except Exception as e:  # noqa: BLE001 — 取数失败不打断，标 data_missing
            print(f"[WARN] get_market_data.py 批次取数失败({len(chunk)}只): {e}", file=sys.stderr)
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


def enrich_metrics(cands, provider):
    """用 provider（dict 或 callable）补齐 price/avg_volume/mcap/sector"""
    if not provider:
        return cands
    tickers = [c["ticker"] for c in cands if c.get("price") is None]
    if callable(provider):
        data = provider(tickers) or {}
    else:
        data = provider
    out = []
    for c in cands:
        m = data.get(c["ticker"])
        if m and c.get("price") is None:
            c = dict(c)
            c["price"] = m.get("price")
            c["avg_volume"] = m.get("avg_volume")
            if c.get("mcap") is None:
                c["mcap"] = m.get("mcap", m.get("market_cap"))
            if not c.get("sector"):
                c["sector"] = m.get("sector", "")
            if not c.get("name"):
                c["name"] = m.get("name", "")
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


def generate(mode="auto", direction="long", sector=None, user_tickers=None, *,
             entries_config=None, fetcher=None, data_provider=None, seed_pool=None,
             small_cap_source=None):
    """候选池生成主流程（§一优先级链 + §5 质量控制，全程留痕）"""
    cfg = entries_config or load_config()
    global _ALIAS
    _ALIAS = cfg.get("sector_alias", {})
    seed_pool = seed_pool or load_seed_pool()
    tried_log, degrade_log = [], []

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
        etf_cands = try_entries(_sector_etf_entries(cfg, sector), fetcher, tried_log)
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
        scan_cands = try_entries(cfg.get("scan_entries", []), fetcher, tried_log)
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

    # ---- §5.4 排除（先剔除不合格，再做质量控制补充）----
    candidates = enrich_metrics(candidates, data_provider)
    candidates, excluded = apply_exclusion_rules(candidates)

    # ---- §5.2 多样性补充 → §5.3 小盘补充（用户指定模式不做，§四仅校验）----
    audit_div, audit_small = {"added": [], "insufficient": False}, {"added": [], "insufficient": False}
    if mode != "user":
        candidates, audit_div = supplement_diversity(candidates, seed_pool["by_sector"])
        if small_cap_source is None and cfg.get("small_cap_entries"):
            small_cap_source = normalize_candidates(
                try_entries(cfg["small_cap_entries"], fetcher, tried_log), source="小盘入口")
        candidates, audit_small = supplement_small_cap(candidates, small_cap_source or [])
        # 补充进来的标的同样要过指标与排除（保持"池内全部合格"不变式）
        candidates = enrich_metrics(candidates, data_provider)
        candidates, excluded2 = apply_exclusion_rules(candidates)
        excluded = excluded + excluded2

    # ---- §5.1 数量控制（最后截断）----
    candidates, removed = enforce_max_pool(candidates)

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
        "统计": {
            "总数": len(candidates),
            "小盘占比_pct": round(stats["pct"] * 100, 1),
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
        },
    }


def probe(base_pool_tickers, *, entries_config=None, fetcher=None, data_provider=None,
          seed_pool=None, max_merge=MAX_MERGE):
    """§七 池外探测：固定入口清单取当期全市场清单 → 差集 → 指标验证 → 并入(≤10) → 留痕"""
    cfg = entries_config or load_config()
    seed_pool = seed_pool or load_seed_pool()
    tried_log = []

    all_entries = list(cfg.get("scan_entries", [])) + list(cfg.get("small_cap_entries", [])) \
        + list(cfg.get("probe_extra_entries", []))
    raw = try_entries(all_entries, fetcher, tried_log)
    outside = normalize_candidates(raw, source="池外探测")

    # 与固定池重复的 → 标注来源固定池，不重复计数
    base_set = {c["ticker"] if isinstance(c, dict) else str(c).upper() for c in base_pool_tickers}
    seed_tickers = set(seed_pool["by_ticker"].keys())
    diff = [c for c in outside if c["ticker"] not in base_set and c["ticker"] not in seed_tickers]
    seed_overlaps = [c["ticker"] for c in outside if c["ticker"] in seed_tickers]

    diff = enrich_metrics(diff, data_provider)
    diff, excluded = apply_exclusion_rules(diff)

    pool = [{"ticker": t} for t in base_pool_tickers]
    pool, merged, not_merged = merge_outside(pool, diff, max_merge=max_merge)

    return {
        "入口数": len(all_entries),
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
    }


# ====================== CLI ======================

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
    args = parser.parse_args()

    cfg = load_config(args.config)

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
    else:
        provider = metrics_via_script

    if args.probe_only:
        with open(args.probe_only, "r", encoding="utf-8") as f:
            base = load_pool_json(json.load(f))
        result = probe(base, entries_config=cfg, fetcher=fetcher, data_provider=provider)
    else:
        result = generate(
            mode=args.mode, direction=args.direction, sector=args.sector,
            user_tickers=args.tickers, entries_config=cfg, fetcher=fetcher,
            data_provider=provider, small_cap_source=None,
        )

    if args.output == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.probe_only:
        stats = result["统计"]
        print("=" * 60)
        print(f"池外探测（§七）  现有池 {len(base)} 只  入口 {result['入口数']} 个")
        print("=" * 60)
        print(f"  差集候选: {result['差集候选数']} 只  "
              f"并入: {stats['并入数']}  未并入: {stats['未并入数']}  排除: {stats['排除数']}")
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
