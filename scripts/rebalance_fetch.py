# -*- coding: utf-8 -*-
"""rebalance_fetch.py — 加密货币板块轮动调仓「取数层」(v1.0，2026-09-16)

定位（docs/调仓/加密货币/12 §13.7）：
  把上一轮散落在 tests/ 的取数临时脚本晋升为正式工具，固定「stdout ≤30 行摘要、全量数据只落盘」契约，
  让 AI 上下文不再出现 430KB K 线 / 100KB 合约响应，并消灭「硬编码 END 边界 → 全量重拉」与「结构试错」两类浪费。

数据通道（零 MCP、零 token）：
  - K 线 / 实时价 / 最小交易单位：币安 USDT 永续公共 REST（api.binance.com/fapi，无需 API key），
    与策略所交易合约（SYMBOL_SWAP_BINANCE.GLOBAL）100% 同口径；
  - 策略状态 / 账户 / 三份报告：AI 经 vnpy_mcp 工具调用（mcp_call_tool）落盘到 --data-dir 后，
    由本脚本归一化（本脚本不直连 MCP，也不含任何 token）。

子命令：
  klines       日 K 线（探测→增量）+ 板块/分桶合成序列
  ticks        全池实时价（当日涨幅告警）
  strategies   策略原始 JSON → map 入参 +（可选）分层
  volume-unit  最小交易单位表（exchangeInfo，兼容 rebalance_audit --units）
  reports      三份报告摘要（as_of / 日龄 / 顶层键）
  status       一屏管线状态（上下文压缩后恢复用）

用法：
  E:\\veighna_studio_43\\python.exe -X utf8 scripts\\rebalance_fetch.py klines
  E:\\veighna_studio_43\\python.exe -X utf8 scripts\\rebalance_fetch.py status

约定文件名（与 rebalance_acceptance.py FILES 对齐，均在 --data-dir）：
  crypto_prices_raw.json / score_prices.json / score2_prices.json / live_prices.json /
  strategies_raw.json / map_strategies.json / map_classifications.json / volume_min_unit.json /
  market_report.json（大盘）/ long_report.json（做多）/ short_report.json（做空）
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:  # pragma: no cover
    print("ERROR: 缺少 requests 库（E:\\veighna_studio_43\\python.exe -m pip install requests）")
    sys.exit(1)

HERE = os.path.dirname(os.path.abspath(__file__))
POOL_PATH = os.path.join(HERE, "crypto_pool.json")
DEFAULT_DATA_DIR = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", "tests"))
FAPI_HOSTS = ("https://fapi.binance.com", "https://api.binance.com", "https://api1.binance.com", "https://api2.binance.com")
DAY_MS = 86400000
BENCHMARK = "BTC"
BUCKETS = ("top10", "11-30", "31-60", "60+")
_PROXY_CLI = None  # --proxy 覆盖值（main 里设置）


# ---------------------------------------------------------------- 基础设施

class FapiError(RuntimeError):
    pass


def die(msg):
    print("ERROR: %s" % msg)
    sys.exit(1)


def load_json(path, label):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        die("%s 不可读: %s" % (label, e))


def save_json(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


_PROXY_STATE = {"url": None, "done": False}
_SESSION = None


def _proxy_candidates():
    """代理候选顺序：--proxy > 环境变量 FAPI_PROXY > 本机 10808（存在则优先）> 10809 > 直连。
    本技能为独立运行代码，不读取 .vntrader 等任何项目配置。"""
    cands = []
    if _PROXY_CLI:
        cands.append(_PROXY_CLI)
    env = os.environ.get("FAPI_PROXY")
    if env:
        cands.append(env)
    cands.append("socks5h://127.0.0.1:10808")
    cands.append("socks5h://127.0.0.1:10809")
    cands.append(None)  # 直连（境外环境）
    # 去重保序
    seen, out = set(), []
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _get_session():
    """首次调用时探测可用通道并缓存（/fapi/v1/ping 轻量探测）；全失败时列出各通道实测结果。"""
    global _SESSION
    if _SESSION is not None:
        return _SESSION
    tried = []
    for url in _proxy_candidates():
        s = requests.Session()
        if url:
            s.proxies = {"http": url, "https": url}
            s.trust_env = False
        try:
            r = s.get(FAPI_HOSTS[0] + "/fapi/v1/ping", timeout=10)
            if r.status_code == 200:
                _PROXY_STATE["url"] = url
                _PROXY_STATE["done"] = True
                _SESSION = s
                return s
            tried.append("%-28s -> HTTP %d" % (url or "直连", r.status_code))
        except requests.RequestException as e:
            tried.append("%-28s -> %s" % (url or "直连", str(e).splitlines()[0][:70]))
    _PROXY_STATE["done"] = True
    die("无可用通道到达币安 fapi:\n  " + "\n  ".join(tried) +
        "\n建议: 检查 10808 代理出口节点是否被币安屏蔽，或用 --proxy socks5h://host:port 指定可用代理")


def http_get(path, params=None, timeout=20, retries=3):
    """币安 fapi 公共 REST：自动代理 + 多镜像 + 限速/5xx 退避。4xx(非429/418) 抛 FapiError（单币失败不炸全场）。"""
    s = _get_session()
    last = "未知错误"
    for host in FAPI_HOSTS:
        for attempt in range(retries):
            try:
                r = s.get(host + path, params=params or {}, timeout=timeout)
                if r.status_code == 200:
                    return r.json()
                if r.status_code in (429, 418):
                    last = "HTTP %d 限速/临时封禁（%s）" % (r.status_code, host)
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if 500 <= r.status_code < 600:
                    last = "HTTP %d 服务端错误（%s）" % (r.status_code, host)
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise FapiError("HTTP %d: %s" % (r.status_code, r.text[:150]))
            except requests.RequestException as e:
                last = "%s: %s" % (host, str(e)[:120])
                time.sleep(1.0 * (attempt + 1))
    raise FapiError("fapi 请求失败 %s: %s" % (path, last))


def proxy_note():
    if _PROXY_STATE["done"]:
        return "直连" if not _PROXY_STATE["url"] else _PROXY_STATE["url"]
    return "未探测"


def day_ms(d):
    """'YYYY-MM-DD' → 00:00 UTC 毫秒"""
    return int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def ms_day(ms):
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


def load_pool():
    pool = load_json(POOL_PATH, "crypto_pool.json")
    coins = pool.get("coins") or {}
    cats = pool.get("categories") or {}
    if not coins:
        die("crypto_pool.json 无 coins 条目")
    return pool, coins, cats


def fapi_symbol_map(coins):
    """池内币 → 币安 USDT 永续符号（含 1000 前缀变体）。1 次 exchangeInfo 全量校验。"""
    try:
        info = http_get("/fapi/v1/exchangeInfo")
    except FapiError as e:
        die("exchangeInfo 不可达（网络/地区限制？）: %s" % e)
    valid = {s.get("symbol") for s in info.get("symbols", []) if s.get("status") == "TRADING"}
    out, missing = {}, []
    for sym in coins:
        hit = None
        for cand in (sym + "USDT", "1000" + sym + "USDT"):
            if cand in valid:
                hit = cand
                break
        if hit:
            out[sym] = hit
        else:
            missing.append(sym)
    return out, missing


def num0(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def ticker_of(name):
    s = str(name)
    code = s[len("MARTIN-"):] if s.startswith("MARTIN-") else s
    code = code.split("_")[0].split("-")[0]
    return code[:-4] if code.endswith("USDT") else code


# ---------------------------------------------------------------- klines

def fetch_klines(coins, fapi_map, data_dir, days, full):
    """探测（基准币定末根完整日）→ 逐币增量。返回 (raw, last_day, fetched, cached, failed)。"""
    raw_path = os.path.join(data_dir, "crypto_prices_raw.json")
    raw = {}
    if not full and os.path.exists(raw_path):
        raw = load_json(raw_path, "K线缓存")

    bench = fapi_map.get(BENCHMARK)
    if not bench:
        die("基准币 %s 不在币安 USDT 永续（池配置问题）" % BENCHMARK)
    now_ms = int(time.time() * 1000)
    try:
        bars = http_get("/fapi/v1/klines", {"symbol": bench, "interval": "1d", "limit": 5})
    except FapiError as e:
        die("基准币探测失败: %s" % e)
    completed = [b for b in bars if b[6] <= now_ms]  # closeTime <= now → 已收盘
    if not completed:
        die("基准币无已收盘日 K")
    last_day = ms_day(completed[-1][0])

    fetched, cached, failed = 0, 0, []
    for i, sym in enumerate(coins, 1):
        fs = fapi_map.get(sym)
        if fs is None:
            failed.append("%s: 币安无对应 USDT 永续" % sym)
            continue
        entry = raw.get(sym) or {}
        cur = dict(entry.get("closes") or {})
        turns = dict(entry.get("turnovers") or {})
        if cur and max(cur.keys()) >= last_day:
            cached += 1
            continue
        if cur:
            start = (datetime.strptime(max(cur.keys()), "%Y-%m-%d")
                     + timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            start = ms_day(now_ms - days * DAY_MS)
        try:
            bars = http_get("/fapi/v1/klines", {
                "symbol": fs, "interval": "1d",
                "startTime": day_ms(start), "endTime": now_ms, "limit": 1000})
        except FapiError as e:
            failed.append("%s: %s" % (sym, str(e)[:80]))
            continue
        adds = 0
        for b in bars:
            if b[6] > now_ms:  # 当日未收盘 bar 丢弃
                continue
            d = ms_day(b[0])
            if d not in cur:
                adds += 1
            cur[d] = float(b[4])
            if b[7] is not None:
                turns[d] = float(b[7])
        if adds:
            fetched += 1
        raw[sym] = {"vt_symbol": fs + "_SWAP_BINANCE.GLOBAL", "closes": cur, "turnovers": turns}
        if i % 10 == 0 or i == len(coins):
            save_json(raw, raw_path)
        time.sleep(0.12)

    save_json(raw, raw_path)
    return raw, last_day, fetched, cached, failed


def build_composites(raw, cats, coins, data_dir):
    """板块等权合成（score）+ 板块-分桶 流动性 Top3 合成（score2）。逻辑与旧 fetch_crypto_prices.py 逐字一致。"""
    def common_dates(syms, need=BENCHMARK):
        sets = [set(raw[s]["closes"].keys()) for s in syms if s in raw]
        if not sets:
            return set()
        ds = set.intersection(*sets)
        if need in raw:
            ds &= set(raw[need]["closes"].keys())
        return ds

    def composite(syms, dates):
        # 等权合成：各币归一到首日=100 后取均值
        out = {}
        for d in sorted(dates):
            vals = []
            for s in syms:
                if s not in raw or d not in raw[s]["closes"]:
                    continue
                v0 = raw[s]["closes"][min(raw[s]["closes"].keys())]
                if v0 and v0 > 0:
                    vals.append(raw[s]["closes"][d] / v0 * 100.0)
            if vals:
                out[d] = round(sum(vals) / len(vals), 6)
        return out

    btc = raw.get(BENCHMARK, {}).get("closes", {})
    out_scores = {BENCHMARK: {d: round(v, 6) for d, v in btc.items()}}
    score_meta = {}
    for cat in cats:
        t12 = [c for c in cats[cat].get("T1", []) + cats[cat].get("T2", []) if c in raw]
        used = t12
        if not used:  # GameFi/DePIN 无 T1/T2 → 用全部池内币（留痕）
            used = [c for c in cats[cat].get("T3", []) if c in raw]
        score_meta[cat] = used
        if used:
            out_scores[cat] = composite(used, common_dates(used))
    save_json(out_scores, os.path.join(data_dir, "score_prices.json"))
    save_json(score_meta, os.path.join(data_dir, "score_meta.json"))

    def liq(sym):
        ts = sorted(raw.get(sym, {}).get("turnovers", {}).items())[-20:]
        return (sum(v for _, v in ts) / len(ts)) if ts else 0.0

    out2 = {BENCHMARK: {d: round(v, 6) for d, v in btc.items()}}
    score2_meta = {}
    for cat in cats:
        for b in BUCKETS:
            members = [c for c, m in coins.items()
                       if m.get("category") == cat and m.get("mcap_bucket") == b and c in raw]
            top3 = sorted(members, key=liq, reverse=True)[:3]
            if not top3:
                continue
            name = "%s-%s" % (cat, b)
            score2_meta[name] = {"members": members, "top3": top3,
                                 "liq20d": {c: round(liq(c), 1) for c in top3}}
            out2[name] = composite(top3, common_dates(top3))
    save_json(out2, os.path.join(data_dir, "score2_prices.json"))
    save_json(score2_meta, os.path.join(data_dir, "score2_meta.json"))
    return out_scores, out2


def cmd_klines(a):
    _, coins, cats = load_pool()
    fapi_map, missing = fapi_symbol_map(coins)
    raw, last_day, fetched, cached, failed = fetch_klines(coins, fapi_map, a.data_dir, a.days, a.full)
    out_scores, out2 = build_composites(raw, cats, coins, a.data_dir)
    bench_days = len((raw.get(BENCHMARK) or {}).get("closes", {}))
    print("klines: 币 %d（缓存 %d / 增量 %d / 失败 %d）| 末根 %s | 基准 %s 天数 %d | 通道 %s"
          % (len(coins), cached, fetched, len(failed), last_day, BENCHMARK, bench_days, proxy_note()))
    if missing:
        print("  币安缺失（不拉取，进 数据缺失与冲突）: %s" % ",".join(missing))
    for f in failed:
        print("  FAIL", f)
    print("  score 板块合成 %d 项: %s" % (len(out_scores) - 1,
          json.dumps({k: len(v) for k, v in out_scores.items() if k != BENCHMARK}, ensure_ascii=False)))
    print("  score2 组合 %d 项: %s" % (len(out2) - 1,
          json.dumps({k: len(v) for k, v in out2.items() if k != BENCHMARK}, ensure_ascii=False)))
    print("  文件: crypto_prices_raw.json / score_prices.json / score2_prices.json（%s）" % a.data_dir)


# ---------------------------------------------------------------- ticks

def cmd_ticks(a):
    _, coins, _ = load_pool()
    fapi_map, missing = fapi_symbol_map(coins)
    try:
        allp = http_get("/fapi/v1/ticker/price")
    except FapiError as e:
        die("实时价获取失败: %s" % e)
    px_by_fs = {p.get("symbol"): p.get("price") for p in allp}
    prev = {}
    raw_path = os.path.join(a.data_dir, "crypto_prices_raw.json")
    if os.path.exists(raw_path):
        raw = load_json(raw_path, "K线缓存")
        for sym, c in raw.items():
            cl = (c or {}).get("closes") or {}
            if cl:
                prev[sym] = (max(cl.keys()), cl[max(cl.keys())])
    out, details = {}, {}
    for sym, fs in fapi_map.items():
        p = px_by_fs.get(fs)
        if p is None:
            continue
        try:
            price = float(p)
        except (TypeError, ValueError):
            continue
        out[sym] = price
        pc = prev.get(sym)
        details[sym] = {
            "price": price,
            "prev_close": pc[1] if pc else None,
            "prev_close_date": pc[0] if pc else None,
            "pct_day": round((price / pc[1] - 1) * 100, 3) if (pc and pc[1]) else None,
        }
    if not out:
        die("未取到任何实时价（fapi 符号映射全失败？）")
    save_json(out, os.path.join(a.data_dir, "live_prices.json"))
    asof = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    details["__asof_utc__"] = asof
    save_json(details, os.path.join(a.data_dir, "live_ticks_raw.json"))
    pcts = {s: d["pct_day"] for s, d in details.items()
            if isinstance(d, dict) and d["pct_day"] is not None}
    hot = sorted((s for s, v in pcts.items() if v > 5.0), key=lambda s: -pcts[s])
    print("ticks: 币 %d | asof %s UTC | 对比末根 %s"
          % (len(out), asof, (max(k for s, k in prev.values()) if prev else "无K线缓存")))
    if pcts:
        print("  当日涨幅 区间 %.2f%% ~ %.2f%% | >5%%（不追高线）: %s"
              % (min(pcts.values()), max(pcts.values()),
                 ("、".join("%s %.2f%%" % (s, pcts[s]) for s in hot) if hot else "无")))
    if missing:
        print("  币安缺失: %s" % ",".join(missing))
    print("  文件: live_prices.json / live_ticks_raw.json（%s）" % a.data_dir)


# ---------------------------------------------------------------- strategies

def cmd_strategies(a):
    _, coins, _ = load_pool()
    meta = {c["symbol"]: {"板块": c["category"], "分桶": c["mcap_bucket"],
                          "层级": c.get("tier"), "在池": True} for c in coins.values()}
    for item in a.out_of_pool or []:
        if ":" in item:
            sym, sec = item.split(":", 1)
            meta[sym.strip()] = {"板块": sec.strip(), "分桶": "池外", "层级": None, "在池": False}
    raw = load_json(a.raw, "strategies_raw.json")
    if isinstance(raw, dict):
        raw = [{"策略名称": k, **v} for k, v in raw.items() if isinstance(v, dict)]
    if not isinstance(raw, list) or not raw:
        die("strategies_raw.json 须为 cta_strategies_get_all 的 JSON 数组")

    map_in, unowned = [], []
    for s in raw:
        name = s.get("策略名称")
        if not name:
            unowned.append("(无策略名称)")
            continue
        code = ticker_of(name)
        if code not in meta:
            unowned.append("%s(%s)" % (name, code))
            continue
        e = {"策略名称": name, "ticker": code, "vt_symbol": s.get("vt_symbol"),
             "实际持仓": s.get("实际持仓"), "当前行情价格": s.get("当前行情价格"),
             "目标持仓": s.get("持仓目标", s.get("目标持仓", 0)),
             "运行状态": s.get("运行状态", s.get("状态"))}
        if not meta[code]["在池"]:
            e["板块"] = meta[code]["板块"]
        map_in.append(e)
    save_json(map_in, os.path.join(a.data_dir, "map_strategies.json"))

    stat = {}
    for s in raw:
        st = s.get("运行状态") or s.get("状态") or "未知"
        stat[st] = stat.get(st, 0) + 1
    print("strategies: 策略 %d | 状态分布 %s" % (len(raw), json.dumps(stat, ensure_ascii=False)))
    print("  总实际持仓 %s | 总持仓目标 %s | map_strategies.json 已写（%d 条）"
          % (num0(sum(num0(s.get("实际持仓")) for s in raw)),
             num0(sum(num0(s.get("持仓目标", s.get("目标持仓", 0))) for s in raw)), len(map_in)))
    if unowned:
        print("  未归属（不在池且未给 --out-of-pool）: %s" % "、".join(unowned))

    if a.score and a.score2:
        score = load_json(a.score, "score.json")
        score2 = load_json(a.score2, "score2.json")
        sec_tier = {d["板块"]: d["分层"] for d in score.get("明细", []) if isinstance(d, dict)}
        if a.leaders and os.path.exists(a.leaders):
            leaders = load_json(a.leaders, "leaders.json")
            for sec in leaders.get("降档板块", []):
                sec_tier[sec] = "中性"
        sub_tier = {d["子板块"]: d["分层"] for d in score2.get("明细", []) if isinstance(d, dict)}
        cls = {"板块": sec_tier, "子板块": sub_tier}
        save_json(cls, os.path.join(a.data_dir, "map_classifications.json"))
        print("  map_classifications.json: 板块 %d（%s）| 子板块 %d"
              % (len(sec_tier), json.dumps(sec_tier, ensure_ascii=False), len(sub_tier)))


# ---------------------------------------------------------------- volume-unit

def cmd_volume_unit(a):
    try:
        info = http_get("/fapi/v1/exchangeInfo")
    except FapiError as e:
        die("exchangeInfo 不可达: %s" % e)
    fs_info = {}
    for s in info.get("symbols", []):
        if s.get("status") != "TRADING" or not str(s.get("symbol", "")).endswith("USDT"):
            continue
        flt = {f.get("filterType"): f for f in s.get("filters", [])}
        lot, pf = flt.get("LOT_SIZE") or {}, flt.get("PRICE_FILTER") or {}
        try:
            fs_info[s["symbol"]] = (float(lot.get("minQty", 0)), float(pf.get("tickSize", 0)))
        except (TypeError, ValueError):
            continue

    tickers, names = [], {}
    if a.strategies and os.path.exists(a.strategies):
        raw = load_json(a.strategies, "strategies")
        if isinstance(raw, dict):
            raw = [{"策略名称": k} for k in raw]
        for s in raw:
            nm = s.get("策略名称")
            if not nm:
                continue
            t = ticker_of(nm)
            tickers.append(t)
            names[t] = nm
    if not tickers:
        _, coins, _ = load_pool()
        tickers = list(coins.keys())
        names = {t: "MARTIN-%sUSDT" % t for t in tickers}

    out, missing = {}, []
    for t in tickers:
        hit = None
        for cand in (t + "USDT", "1000" + t + "USDT"):
            if cand in fs_info:
                hit = (cand, fs_info[cand])
                break
        if not hit:
            missing.append(t)
            continue
        fs, (mq, tk) = hit
        out[t] = {"strategy": names.get(t, "MARTIN-%sUSDT" % t), "fapi_symbol": fs,
                  "volume_min_unit": mq, "min_volume": mq, "pricetick": tk, "match": True}
    save_json(out, os.path.join(a.data_dir, "volume_min_unit.json"))
    print("volume-unit: 币 %d（exchangeInfo 权威值，与策略运行时自动获取同源）| 非 1 单位 %d 个"
          % (len(out), sum(1 for v in out.values() if v["volume_min_unit"] != 1)))
    for t in sorted(out):
        v = out[t]
        print("  %-6s min=%-8s tick=%-10s (%s)" % (t, v["volume_min_unit"], v["pricetick"], v["fapi_symbol"]))
    if missing:
        print("  缺失: %s" % ",".join(missing))


# ---------------------------------------------------------------- reports

def _find_asof(obj):
    if isinstance(obj, dict):
        for k in ("analysis_time", "as_of", "分析时间"):
            if isinstance(obj.get(k), str):
                return obj[k]
        for v in obj.values():
            r = _find_asof(v)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj[:5]:
            r = _find_asof(v)
            if r:
                return r
    return None


def cmd_reports(a):
    now = datetime.now(timezone.utc)
    found = 0
    for label, fname in (("大盘", "market_report.json"), ("做多", "long_report.json"),
                         ("做空", "short_report.json")):
        p = os.path.join(a.data_dir, fname)
        if not os.path.exists(p):
            print("  %-4s %-22s 缺失（AI 未落盘，按 数据缺失与冲突 处理）" % (label, fname))
            continue
        r = load_json(p, fname)
        asof = _find_asof(r) if isinstance(r, (dict, list)) else None
        age = ""
        if asof:
            try:
                t = datetime.fromisoformat(asof.replace("+00:00", ""))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                age = "日龄 %.1f" % max(0.0, (now - t).total_seconds() / 3600.0 / 24.0)
            except ValueError:
                age = "as_of 不可解析"
        keys = list(r.keys())[:10] if isinstance(r, dict) else ("数组 %d 项" % len(r))
        print("  %-4s %-22s as_of=%s %s | 顶层键 %s" % (label, fname, asof or "—", age, keys))
        found += 1
    if found == 0:
        die("三份报告均未落盘（AI 先经 cta_report_get 写入 market_report.json / long_report.json / short_report.json）")
    print("reports: 落盘 %d/3（%s）" % (found, a.data_dir))


# ---------------------------------------------------------------- status

def _fact(fname, obj):
    try:
        if fname == "crypto_prices_raw.json":
            allc = [max(c["closes"].keys()) for c in obj.values()
                    if isinstance(c, dict) and c.get("closes")]
            if not allc:
                return "空"
            return "币 %d | 末根 %s" % (len(obj), max(allc))
        if fname in ("score_prices.json", "score2_prices.json"):
            ks = list(obj.keys())
            return "%d 键（%s…）" % (len(obj), ",".join(ks[:4])) if len(ks) > 4 else "%d 键" % len(obj)
        if fname == "live_prices.json":
            return "%d 币" % len(obj)
        if fname == "strategies_raw.json":
            return "%d 策略 | 总持仓 %s" % (len(obj), sum(num0(x.get("实际持仓")) for x in obj if isinstance(x, dict)))
        if fname == "volume_min_unit.json":
            return "%d 币" % len(obj)
        if fname in ("fund.json", "gap.json", "score.json", "score2.json",
                     "map.json", "cycle.json", "leaders.json"):
            keys = {}
            for k in ("总预算B", "校验", "周期", "成功", "全部通过", "强势板块"):
                if isinstance(obj, dict) and k in obj:
                    keys[k] = obj[k]
            return json.dumps(keys, ensure_ascii=False) if keys else "%d 键" % len(obj)
        if fname == "报告.json":
            return "%d 顶层键 | %s" % (len(obj), obj.get("report_name") or obj.get("报告名") or "—")
        if fname in ("market_report.json", "long_report.json", "short_report.json"):
            return "as_of=%s" % (_find_asof(obj) or "—")
        return "%d 键" % (len(obj) if hasattr(obj, "__len__") else 0)
    except Exception:
        return "解析失败"


def cmd_status(a):
    order = [
        ("K线", "crypto_prices_raw.json"), ("板块价", "score_prices.json"),
        ("分桶价", "score2_prices.json"), ("实时价", "live_prices.json"),
        ("策略", "strategies_raw.json"), ("单位表", "volume_min_unit.json"),
        ("fund", "fund.json"), ("score", "score.json"), ("score2", "score2.json"),
        ("leaders", "leaders.json"), ("map", "map.json"), ("gap", "gap.json"),
        ("cycle", "cycle.json"), ("报告", "报告.json"),
        ("大盘报告", "market_report.json"), ("做多", "long_report.json"), ("做空", "short_report.json"),
    ]
    print("status: %s（%s UTC）" % (a.data_dir, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")))
    klast = None
    for label, fname in order:
        p = os.path.join(a.data_dir, fname)
        if not os.path.exists(p):
            print("  %-6s %-22s — 缺失" % (label, fname))
            continue
        try:
            obj = json.load(open(p, encoding="utf-8"))
            fact = _fact(fname, obj)
        except Exception as e:
            fact = "损坏 %s" % str(e)[:40]
        mt = datetime.fromtimestamp(os.path.getmtime(p), tz=timezone.utc).strftime("%m-%d %H:%M")
        print("  %-6s %-22s %s UTC | %s" % (label, fname, mt, fact))
        if fname == "crypto_prices_raw.json" and isinstance(obj, dict):
            allc = [max(c["closes"].keys()) for c in obj.values()
                    if isinstance(c, dict) and c.get("closes")]
            if allc:
                klast = max(allc)
    if klast:
        lag = (datetime.now(timezone.utc)
               - datetime.strptime(klast, "%Y-%m-%d").replace(tzinfo=timezone.utc)).days
        print("K线滞后: 末根 %s，距今天 %d 日（报告须写「截至 %s」）" % (klast, lag, klast))


# ---------------------------------------------------------------- 入口

def main():
    ap = argparse.ArgumentParser(prog="rebalance_fetch", description="加密货币调仓取数层")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_data_dir(p):
        p.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                       help="数据落盘目录（默认 工作区 tests/）")
        p.add_argument("--proxy", default=None,
                       help="SOCKS5 代理（如 socks5h://127.0.0.1:10808）；缺省自动探测（10808>10809>直连）")

    p = sub.add_parser("klines", help="币安 fapi 日K线（探测→增量）+ 板块/分桶合成")
    add_data_dir(p)
    p.add_argument("--days", type=int, default=45, help="无缓存时全量窗口（自然日）")
    p.add_argument("--full", action="store_true", help="忽略缓存全量重拉（禁止常规使用，见 12 §13.7）")
    p.set_defaults(fn=cmd_klines)

    p = sub.add_parser("ticks", help="全池实时价 + 当日涨幅告警")
    add_data_dir(p)
    p.set_defaults(fn=cmd_ticks)

    p = sub.add_parser("strategies", help="策略原始 JSON → map 入参（+可选分层）")
    add_data_dir(p)
    p.add_argument("--raw", default=None, help="strategies_raw.json 路径（默认 data-dir/strategies_raw.json）")
    p.add_argument("--out-of-pool", action="append",
                   help="池外策略归属，格式 SYM:板块（可重复，如 KAVA:L1 公链）")
    p.add_argument("--score", default=None, help="score.json（与 --score2 同给时产出 map_classifications.json）")
    p.add_argument("--score2", default=None, help="score2.json")
    p.add_argument("--leaders", default=None, help="leaders.json（降档板块 → 中性）")
    p.set_defaults(fn=cmd_strategies)

    p = sub.add_parser("volume-unit", help="最小交易单位表（exchangeInfo，兼容 audit --units）")
    add_data_dir(p)
    p.add_argument("--strategies", default=None, help="strategies_raw.json（给定则只输出其策略币，否则全池）")
    p.set_defaults(fn=cmd_volume_unit)

    p = sub.add_parser("reports", help="三份报告摘要")
    add_data_dir(p)
    p.set_defaults(fn=cmd_reports)

    p = sub.add_parser("status", help="一屏管线状态（压缩后恢复用）")
    add_data_dir(p)
    p.set_defaults(fn=cmd_status)

    a = ap.parse_args()
    global _PROXY_CLI
    _PROXY_CLI = getattr(a, "proxy", None)
    if a.cmd == "strategies" and not a.raw:
        a.raw = os.path.join(a.data_dir, "strategies_raw.json")
    a.fn(a)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        die("%s: %s" % (type(e).__name__, e))
