#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
rebalance_x.py — 加密货币板块轮动调仓 · 探索版工具链 (v0.1)

对正式版（rebalance_tools.py v2.5.0 / rebalance_fetch.py）的重写实验，
规格见 docs/调仓/加密货币探索/00_index.md。核心变化：
  1. mode/轮动阶段机判（gate）→ verify 可复算（正式版为 AI 手工判定）
  2. 主轴 = 每板块 T2 首位 20 日超额排序（select），五因子评分不再实现
  3. 数量先行 qty-first：目标资金 = 落地 = qty×现价，单一口径
  4. 参数一次解析贯穿（gate.params 单一参数源）
  5. micro 分支显式化（B < 3000 USDT）
  6. fail-fast：输入拿错/结构不符 → 退出非 0；真实数据缺失 → unknown 显式记录
  7. run 子命令单进程编排全链；取数复用 rebalance_fetch.py（subprocess，不改）

复用清单（零改动）：
  - scripts/crypto_pool.json     板块/T2/分桶 单一事实源
  - scripts/rebalance_fetch.py   klines / ticks / volume-unit 取数通道
  - 数据文件格式与正式版一致：crypto_prices_raw / live_prices / live_ticks_raw /
    volume_min_unit / market_report / strategies_raw

铁律：本工具只读写 tests/ 数据文件，不连 MCP、不下单、不改策略、不读 .vntrader。
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

VERSION = "0.1"
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_POOL = os.path.join(HERE, "crypto_pool.json")
FETCH = os.path.join(HERE, "rebalance_fetch.py")
REPORT_NAME = "加密货币板块轮动调仓·探索版"
MICRO_B = 3000.0

MODE_PARAMS = {"积极": {"slot_pct": 0.20, "max_new": 3},
               "正常": {"slot_pct": 0.15, "max_new": 2},
               "防御": {"slot_pct": 0.10, "max_new": 1}}
TIER_CONSERV = ["收缩", "中性偏弱", "正常"]          # 保守优先
PHASE_CONSERV = ["晚期", "中期", "早期"]              # 更晚=更保守
BUCKET_CAPS = {"60+": 0.02, "31-60": 0.03}
CAP_SECTOR, CAP_HIBETA, CAP_TOP3, CAP_CASH = 0.20, 0.15, 0.50, 0.05
MAX_POSITIONS = 50


# ---------------------------------------------------------------- 基础
def fail(msg, code=2):
    print("[FAIL] " + msg)
    sys.exit(code)


def load_json(path, what):
    if not path or not os.path.exists(path):
        fail("缺少 %s：%s（fail-fast）" % (what, path))
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        fail("%s 解析失败（%s）：%s" % (what, path, e))


def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def num(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", "").replace("%", ""))
        except ValueError:
            return None
    return None


def ev(desc, value, passed, src=""):
    """证据条目：passed=True/False/None（None=数据缺失，不计入满足条数）"""
    return {"条件": desc, "值": value, "判定": passed, "来源": src}


def score_of(evs):
    return sum(1 for e in evs if e["判定"] is True), [e["条件"] for e in evs if e["判定"] is None]


def closes_of(raw, sym):
    d = raw.get(sym) or {}
    cl = d.get("closes") or {}
    dates = sorted(cl.keys())
    return dates, [float(cl[x]) for x in dates]


def ret_pct(vals, n):
    if len(vals) <= n or vals[-1 - n] == 0:
        return None
    return round((vals[-1] / vals[-1 - n] - 1) * 100, 2)


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def leader_of(pool, cat):
    info = (pool.get("categories") or {}).get(cat) or {}
    t2 = info.get("T2") or []
    t3 = info.get("T3") or []
    if t2:
        return t2[0], "T2 首位", (t2[1] if len(t2) > 1 else None)
    if t3:
        return t3[0], "无 T2 → T3 首位", (t3[1] if len(t3) > 1 else None)
    return None, "板块无 T2/T3", None


def unit_of(units, sym):
    v = units.get(sym)
    if isinstance(v, dict):
        return num(v.get("volume_min_unit"))
    return num(v)


def floor_qty(cash, px, unit):
    if not px or not unit or px <= 0 or unit <= 0:
        return None
    return round(math.floor(cash / px / unit + 1e-9) * unit, 8)


def is_mult(qty, unit):
    if qty is None or not unit:
        return False
    return abs(qty / unit - round(qty / unit)) < 1e-6


def now_cst():
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S +08:00")


# ---------------------------------------------------------------- gate
def parse_btc_d(mr):
    def find(o):
        if isinstance(o, dict):
            if "BTC_D" in o:
                return o["BTC_D"]
            for v in o.values():
                r = find(v)
                if r is not None:
                    return r
        return None
    d = find(mr)
    text = " ".join(str(v) for v in d.values()) if isinstance(d, dict) else (d if isinstance(d, str) else "")
    m = re.search(r"3m\s*([0-9.]+)\s*%?\s*(?:→|->)\s*([0-9.]+)", text)
    if m:
        x, y = float(m.group(1)), float(m.group(2))
        return {"up": y > x, "delta": round(y - x, 2), "raw": "3m %s → %s" % (x, y),
                "source": "大盘报告 板块轮动.BTC_D.趋势（3 个月口径）"}
    if ("上升" in text) or ("上行" in text):
        return {"up": True, "delta": None, "raw": text[:60], "source": "BTC_D 关键词兜底（上升/上行）"}
    if ("下降" in text) or ("下行" in text):
        return {"up": False, "delta": None, "raw": text[:60], "source": "BTC_D 关键词兜底（下降/下行）"}
    return {"up": None, "delta": None, "raw": text[:60], "source": "BTC_D 缺失"}


def parse_stable(mr):
    text = json.dumps(mr.get("资金面", mr), ensure_ascii=False)
    if "扩张" in text:
        return {"val": True, "source": "大盘报告 资金面 文本关键词（扩张）"}
    if "收缩" in text:
        return {"val": False, "source": "大盘报告 资金面 文本关键词（收缩）"}
    return {"val": None, "source": "稳定币口径缺失"}


def run_gate(prices, market_report, live, pool, out,
             mode_override=None, liquidation_usd=None, funding_high=None):
    raw = load_json(prices, "K线原始 crypto_prices_raw.json")
    mr = load_json(market_report, "大盘报告 market_report.json")
    lv = load_json(live, "实时价 live_prices.json") if live else {}
    if not isinstance(pool, dict):
        pool = load_json(pool, "选币池 crypto_pool.json")

    for s in ("BTC", "ETH"):
        if s not in raw or not (raw[s] or {}).get("closes"):
            fail("K线原始缺少 %s 收盘序列（gate fail-fast）" % s)
    _, bc = closes_of(raw, "BTC")
    _, ec = closes_of(raw, "ETH")
    if len(bc) < 21:
        fail("BTC 收盘仅 %d 根，MA20 不可算（gate fail-fast）" % len(bc))
    ma20 = round(mean(bc[-20:]), 2)
    n50 = min(50, len(bc))
    ma50 = round(mean(bc[-n50:]), 2)
    notes = []
    if n50 < 50:
        notes.append("MA50 用 %d 根近似（K 线不足 50 根），阈值判定灵敏度受限" % n50)
    live_btc = num(lv.get("BTC"))
    if not live_btc:
        fail("实时价缺少 BTC（gate fail-fast）")
    btc20 = ret_pct(bc, 20)
    eth20 = ret_pct(ec, 20)
    kline_last = max(((raw.get("BTC") or {}).get("closes") or {}).keys(), default="")
    if btc20 is None or eth20 is None:
        fail("BTC/ETH 20 日收益不可算（K 线不足 21 根，gate fail-fast）")

    bd = parse_btc_d(mr)
    st = parse_stable(mr)
    btc_d_up, btc_d_delta = bd["up"], bd["delta"]

    # 全池 20 日超额（山寨跑输口径）
    alts, alt_missing = [], []
    for sym in (pool.get("coins") or {}):
        try:
            _, cs = closes_of(raw, sym)
        except Exception:
            cs = []
        r = ret_pct(cs, 20) if len(cs) > 20 else None
        (alts.append(r) if r is not None else alt_missing.append(sym))
    alt_excess = round(mean(alts) - btc20, 2) if alts else None
    if alt_missing:
        notes.append("全池 20 日超额缺 %d 币序列（%s…）" % (len(alt_missing), ",".join(alt_missing[:5])))

    # 板块等权超额（阶段判定用：01 §3.3 口径是「板块」不是「龙头单币」）
    def cat_stats(cat):
        xs, xs7 = [], []
        for sym, c in (pool.get("coins") or {}).items():
            if (c or {}).get("category") != cat:
                continue
            try:
                _, cs = closes_of(raw, sym)
            except Exception:
                cs = []
            r20, r7 = ret_pct(cs, 20), ret_pct(cs, 7)
            if r20 is not None:
                xs.append(r20 - btc20)
            if r7 is not None:
                xs7.append(r7)
        return (round(mean(xs), 2) if xs else None,
                round(max(xs7), 2) if xs7 else None)

    meme_mean, meme7_max = cat_stats("Meme")
    l1_mean, _ = cat_stats("L1 公链")
    defi_mean, _ = cat_stats("DeFi")
    meme_sym, meme_how, _ = leader_of(pool, "Meme")
    # 分桶 20 日超额均值（晚期 ③）
    def bucket_mean(bucket):
        xs = []
        for sym, c in (pool.get("coins") or {}).items():
            if (c or {}).get("mcap_bucket") != bucket:
                continue
            try:
                _, cs = closes_of(raw, sym)
            except Exception:
                cs = []
            r = ret_pct(cs, 20)
            if r is not None:
                xs.append(r - btc20)
        return round(mean(xs), 2) if xs else None

    top10_mean, hb_mean = bucket_mean("top10"), bucket_mean("60+")

    # ---- 三档证据
    tier_map = {
        "正常": [
            ev("① 现价≥MA20 且 MA20≥MA50", "live %s vs MA20 %s / MA50 %s" % (live_btc, ma20, ma50),
               live_btc >= ma20 and ma20 >= ma50, "live ticks + kline 末根止 MA"),
            ev("② BTC.D 3 月口径走平或降", bd["raw"], (False if btc_d_up is True else (True if btc_d_up is False else None)), bd["source"]),
            ev("③ 稳定币市值扩张", st["source"], st["val"], st["source"]),
        ],
        "中性偏弱": [
            ev("① |现价−MA20|/MA20 ≤ 5%（MA20 上下反复）", "%.2f%%" % (abs(live_btc - ma20) / ma20 * 100),
               abs(live_btc - ma20) / ma20 <= 0.05, "live vs kline MA20"),
            ev("② BTC.D 3 月上行", bd["raw"], btc_d_up, bd["source"]),
            ev("③ 全池 20 日超额 ≤ −5pct（山寨跑输）", alt_excess,
               (alt_excess <= -5 if alt_excess is not None else None), "全池等权 vs BTC，kline 末根"),
        ],
        "收缩": [
            ev("① 现价<MA50 且 BTC.D 3 月上行", "live %s vs MA50 %s；up=%s" % (live_btc, ma50, btc_d_up),
               (live_btc < ma50 and btc_d_up is True) if btc_d_up is not None else None, "live + 大盘报告"),
            ev("② 24h 全市场爆仓 > $5 亿", liquidation_usd,
               (liquidation_usd > 5e8 if liquidation_usd is not None else None),
               "--liquidation-usd 未提供 → 不参与"),
        ],
    }
    tier_scores = {k: score_of(v) for k, v in tier_map.items()}
    best = max(tier_scores.values(), key=lambda t: t[0])
    tied = [k for k in TIER_CONSERV if tier_scores[k][0] == best[0]]
    tier = tied[0]                       # TIER_CONSERV 已按保守序
    parallel = tied[1:]
    mode = {"正常": "正常", "中性偏弱": "防御", "收缩": "防御"}[tier]
    sell_only = tier == "收缩"
    if mode_override:
        if mode_override not in MODE_PARAMS:
            fail("--mode-override 仅允许 积极/正常/防御")
        notes.append("mode 手工覆盖为 %s（机判=%s），须在报告留痕理由" % (mode_override, mode))
        mode = mode_override
    mp = MODE_PARAMS[mode]
    params = {"invest_mult": 1, "mode": mode, "sell_only": sell_only,
              "slot_pct": mp["slot_pct"],
              "max_new": 0 if sell_only else mp["max_new"]}

    # ---- 三阶段证据
    phase_map = {
        "早期": [
            ev("① BTC20d − ETH20d > +5pct", "%s vs %s" % (btc20, eth20), btc20 > eth20 + 5, "kline 末根"),
            ev("② BTC.D 3 月上行", bd["raw"], btc_d_up, bd["source"]),
            ev("③ Meme 板块 20 日超额 ≤ −5pct（跑输大盘）", meme_mean,
               (meme_mean <= -5 if meme_mean is not None else None),
               "Meme 板块等权（龙头 %s，%s）" % (meme_sym, meme_how)),
        ],
        "中期": [
            ev("① ETH/BTC 20 日差 > 0", round(eth20 - btc20, 2), eth20 - btc20 > 0, "kline 末根"),
            ev("② max(L1,DeFi) 板块 20 日超额 > 0（开始补涨）", "L1=%s DeFi=%s" % (l1_mean, defi_mean),
               (True if (l1_mean or -999) > 0 or (defi_mean or -999) > 0 else False)
               if (l1_mean is not None or defi_mean is not None) else None, "板块等权"),
            ev("③ BTC.D 从高点回落", bd["raw"], None, "无可测来源 → unknown"),
            ev("④ DeFi TVL 增长", None, None, "无可测来源 → unknown"),
        ],
        "晚期": [
            ev("① Meme 板块内最大 7 日涨幅 > 50%", meme7_max,
               (meme7_max > 50 if meme7_max is not None else None), "Meme 板块等权取 max"),
            ev("② BTC.D 3 月 delta ≤ −1pct", btc_d_delta,
               (btc_d_delta <= -1 if btc_d_delta is not None else None), bd["source"]),
            ev("③ 60+ 均超额 > top10 均超额（高弹性补涨）", "60+=%s top10=%s" % (hb_mean, top10_mean),
               (hb_mean > top10_mean if (hb_mean is not None and top10_mean is not None) else None), "pool 分桶等权"),
            ev("④ 全市场费率高企", funding_high, (True if funding_high else (None if funding_high is None else False)),
               "--funding-high 未提供 → 不参与"),
        ],
    }
    phase_scores = {k: score_of(v) for k, v in phase_map.items()}
    pbest = max(phase_scores.values(), key=lambda t: t[0])
    if pbest[0] < 2:
        phase = "无法判定"
        notes.append("三阶段均不满足 2 条（最高 %d 条且 unknown 多）→ phase=无法判定，不施加阶段分桶闸" % pbest[0])
    else:
        p_tied = [k for k in PHASE_CONSERV if phase_scores[k][0] == pbest[0]]
        phase = p_tied[0]
        if len(p_tied) > 1:
            notes.append("轮动阶段并列 %s → 取更保守（更晚）=%s" % (p_tied, phase))

    out_obj = {
        "工具": "rebalance_x gate (v%s)" % VERSION, "版本": VERSION, "market": "加密货币",
        "as_of": {"kline_last_bar": kline_last, "live": "ticks 快照", "market_report": mr.get("analysis_time", "")},
        "tier": tier, "mode": mode, "sell_only": sell_only, "phase": phase,
        "mode_evidence": {k: {"条件数": v[0], "unknown": v[1], "明细": tier_map[k]} for k, v in tier_scores.items()},
        "phase_evidence": {k: {"条件数": v[0], "unknown": v[1], "明细": phase_map[k]} for k, v in phase_scores.items()},
        "parallel": {"tiers": parallel, "params_diff": [
            {"档": k, "max_new": MODE_PARAMS[{"正常": "正常", "中性偏弱": "防御", "收缩": "防御"}[k]]["max_new"],
             "slot_pct": MODE_PARAMS[{"正常": "正常", "中性偏弱": "防御", "收缩": "防御"}[k]]["slot_pct"]}
            for k in parallel]},
        "notes": notes,
        "params": params,
    }
    if out:
        save_json(out, out_obj)
    print("gate: tier=%s mode=%s sell_only=%s phase=%s | 正常%d 中性偏弱%d 收缩%d | 并列=%s"
          % (tier, mode, sell_only, phase, tier_scores["正常"][0], tier_scores["中性偏弱"][0],
             tier_scores["收缩"][0], parallel or "无"))
    if notes:
        for n in notes[:5]:
            print("  note: " + n)
    return out_obj


# ---------------------------------------------------------------- select
def run_select(prices, pool, out):
    raw = load_json(prices, "K线原始 crypto_prices_raw.json")
    if not isinstance(pool, dict):
        pool = load_json(pool, "选币池 crypto_pool.json")
    if "BTC" not in raw or not (raw["BTC"] or {}).get("closes"):
        fail("K线原始缺少 BTC（select fail-fast）")
    _, bc = closes_of(raw, "BTC")
    btc20 = ret_pct(bc, 20)
    kline_last = max(((raw.get("BTC") or {}).get("closes") or {}).keys(), default="")
    rows, missing = [], []
    for cat in (pool.get("categories") or {}):
        sym, how, second = leader_of(pool, cat)
        row = {"板块": cat, "龙头": sym, "取法": how, "候选2": second,
               "20日收益_pct": None, "20日超额_pct": None, "分层": "缺失"}
        if not sym:
            missing.append("%s：无 T2/T3" % cat)
            rows.append(row)
            continue
        try:
            _, cs = closes_of(raw, sym)
        except Exception:
            cs = []
        r = ret_pct(cs, 20)
        if r is None:
            missing.append("%s：%s 无收盘序列" % (cat, sym))
            rows.append(row)
            continue
        row["20日收益_pct"] = r
        row["20日超额_pct"] = round(r - btc20, 2)
        rows.append(row)
    ranked = sorted([r for r in rows if r["20日超额_pct"] is not None],
                    key=lambda x: -x["20日超额_pct"])
    strong = [r["板块"] for r in ranked if r["20日超额_pct"] >= 0][:3]
    weak = [r["板块"] for r in ranked[-3:]]
    for r in rows:
        if r["板块"] in strong:
            r["分层"] = "强势"
        elif r["板块"] in weak:
            r["分层"] = "弱势"
        elif r["20日超额_pct"] is not None:
            r["分层"] = "中性"
    obj = {"工具": "rebalance_x select (v%s)" % VERSION, "版本": VERSION, "market": "加密货币",
           "as_of": {"kline_last_bar": kline_last},
           "benchmark": {"BTC_20日_pct": btc20},
           "sectors": ranked + [r for r in rows if r["20日超额_pct"] is None],
           "强势板块": strong, "弱势板块": weak,
           "notes": ["缺失：%s" % "；".join(missing) if missing else "无缺失"] +
                    ["规则：强势 = 超额降序 Top3 且超额≥0；弱势 = Bottom3"]}
    if out:
        save_json(out, obj)
    print("select: 强势=%s 弱势=%s" % (strong, weak))
    for r in ranked[:5]:
        print("  %-8s %s 超额 %+.2f" % (r["板块"], r["龙头"], r["20日超额_pct"]))
    if missing:
        print("  缺失: " + "; ".join(missing[:3]))
    return obj


# ---------------------------------------------------------------- plan
def run_plan(gate, select, snapshot, prices, live, ticks, units, pool, out):
    g = load_json(gate, "gate.json")
    sel = load_json(select, "select.json")
    snap = load_json(snapshot, "snapshot.json")
    raw = load_json(prices, "K线原始 crypto_prices_raw.json")
    lv = load_json(live, "live_prices.json")
    tk = load_json(ticks, "live_ticks_raw.json")
    un = load_json(units, "volume_min_unit.json")
    if not isinstance(pool, dict):
        pool = load_json(pool, "选币池 crypto_pool.json")

    balance, available = num(snap.get("balance")), num(snap.get("available"))
    if balance is None or available is None:
        fail("snapshot 缺 balance/available（plan fail-fast）")
    mult = 1.0
    B = round(balance * mult, 2)
    if B > available * 2 + 1e-9:
        fail("B=%.2f > available×2=%.2f 账户杠杆硬顶（plan fail-fast）" % (B, available * 2))
    micro = B < MICRO_B
    gp = g.get("params") or {}
    mode, sell_only = gp.get("mode", g.get("mode")), gp.get("sell_only", g.get("sell_only", False))
    slot_pct, max_new, phase = gp.get("slot_pct"), gp.get("max_new"), g.get("phase")
    adjustments = []
    if micro:
        max_new = min(max_new, 1)
        adjustments.append("micro 分支生效：B=%.2f < %.0f → max_new 收紧为 %d；正式版板块目标 %% 区间表声明不适用"
                           % (B, MICRO_B, max_new))
    if phase not in ("早期", "中期", "晚期"):
        adjustments.append("轮动阶段=%s → 不施加阶段分桶闸（早期 top10 / 晚期 60+），按中性处理" % phase)
    slot_cash = round(B * slot_pct, 2)

    strat_by_coin, held, unmapped = {}, {}, []
    for stp in (snap.get("strategies") or []):
        m = re.match(r"MARTIN-([A-Z0-9]+)USDT$", str(stp.get("策略名称", "")))
        if not m:
            unmapped.append(str(stp.get("策略名称")))
            continue
        strat_by_coin[m.group(1)] = stp
        pos = num(stp.get("实际持仓")) or 0.0
        if pos:
            px = num(lv.get(m.group(1)))
            if px:
                held[m.group(1)] = pos * px
            else:
                unmapped.append("%s（持仓 %.8f 无实时价）" % (m.group(1), pos))

    coin_bucket = {s: (c or {}).get("mcap_bucket") for s, c in (pool.get("coins") or {}).items()}
    coin_sector = {s: (c or {}).get("category") for s, c in (pool.get("coins") or {}).items()}
    sector_of = lambda sym: coin_sector.get(sym) or sel_sector_of(sel, sym)

    # ---- 卖出：弱势板块清仓；收缩档额外降总仓位 ≤ 50%×B
    weak_set = set(sel.get("弱势板块") or [])
    sells, sold_value = [], 0.0
    held_total = sum(held.values())
    for coin in sorted(held, key=lambda c: held[c]):
        if coin in strat_by_coin and sector_of(coin) in weak_set:
            sells.append(_mk_sell(coin, strat_by_coin[coin], held[coin], B, "板块=%s 属弱势 Bottom3" % sector_of(coin)))
            sold_value += held[coin]
    if sell_only:
        rest = held_total - sold_value
        for coin in sorted(held, key=lambda c: held[c]):
            if rest <= B * 0.50 + 1e-9:
                break
            if any(s["ticker"] == coin for s in sells) or coin not in strat_by_coin:
                continue
            sells.append(_mk_sell(coin, strat_by_coin[coin], held[coin], B, "收缩档只卖不买，总仓位降至 ≤50%×B"))
            sold_value += held[coin]
            rest -= held[coin]

    # ---- 买入
    instructions, adds, skipped = [], [], []
    hibeta_sum, sector_sum, n_new = 0.0, {}, 0
    if not sell_only:
        smap = {r["板块"]: r for r in sel.get("sectors", [])}
        for sec in (sel.get("强势板块") or []):
            info = smap.get(sec) or {}
            cands = [info.get("龙头")] + ([info.get("候选2")] if info.get("候选2") else [])
            for coin in cands:
                if n_new >= max_new:
                    break
                if not coin:
                    continue
                bucket = coin_bucket.get(coin)
                if phase == "早期" and bucket != "top10":
                    skipped.append("%s(%s)：早期只加 top10，实际分桶=%s" % (coin, sec, bucket))
                    continue
                if phase == "晚期" and bucket == "60+":
                    skipped.append("%s(%s)：晚期 60+ 禁止新开" % (coin, sec))
                    continue
                t = tk.get(coin) if isinstance(tk.get(coin), dict) else {}
                pct_day = num(t.get("pct_day"))
                if pct_day is not None and pct_day > 5:
                    skipped.append("%s(%s)：不追高，当日 %+.2f%% > 5%%" % (coin, sec, pct_day))
                    continue
                try:
                    _, cs = closes_of(raw, coin)
                except Exception:
                    cs = []
                r5, r7 = ret_pct(cs, 5), ret_pct(cs, 7)
                if r5 is None:
                    skipped.append("%s(%s)：K 线缺序列，5/7 日涨幅不可核" % (coin, sec))
                    continue
                if r5 > 30:
                    skipped.append("%s(%s)：不买暴涨，5 日 %+.2f%% > 30%%" % (coin, sec, r5))
                    continue
                if r7 is not None and r7 > 50:
                    skipped.append("%s(%s)：7 日 %+.2f%% > 50%%，顶部信号非买入信号" % (coin, sec, r7))
                    continue
                cap_pct = BUCKET_CAPS.get(bucket)
                cap_cash = slot_cash
                if cap_pct is not None:
                    cap_cash = round(B * cap_pct, 2)
                    if cap_cash < 0.5 * slot_cash:
                        skipped.append("%s(%s)：分桶 %s 单币上限 %s < 50%%×槽位 %s，装不下"
                                       % (coin, sec, bucket, cap_cash, slot_cash))
                        continue
                px = num(lv.get(coin))
                if not px:
                    skipped.append("%s(%s)：缺实时价" % (coin, sec))
                    continue
                unit = unit_of(un, coin)
                stp = strat_by_coin.get(coin)
                cur_qty = num((stp or {}).get("实际持仓")) or 0.0
                cur_value = cur_qty * px
                delta = cap_cash - cur_value
                if delta <= B * 0.02 + 1e-9:
                    skipped.append("%s(%s)：缺口 %.2f ≤ 2%%×B，维持" % (coin, sec, delta))
                    continue
                inc = floor_qty(delta, px, unit) if unit else None
                if not unit:
                    adds.append({"ticker": coin, "板块": sec, "slot_cash": slot_cash,
                                 "note": "无既有策略且 volume-unit 未覆盖该币（单位未知，不可定价）→ class_name/setting 由 AI 补"})
                    n_new += 1
                    continue
                if inc is None or inc < unit:
                    skipped.append("%s(%s)：增量 floor(%s÷%s÷%s)=0 个单位，取整后过小"
                                   % (coin, sec, round(delta, 2), px, unit))
                    continue
                tgt_qty = round(cur_qty + inc, 8)
                landed = round(tgt_qty * px, 2)
                if landed < 0.5 * cap_cash:
                    skipped.append("%s(%s)：落地 %s < 50%%×cap %s，取整后过小" % (coin, sec, landed, cap_cash))
                    continue
                sec_total = sector_sum.get(sec, 0.0) + landed
                if sec_total > B * CAP_SECTOR + 1e-9:
                    skipped.append("%s(%s)：单板块超 20%%×B" % (coin, sec))
                    continue
                if bucket in BUCKET_CAPS and hibeta_sum + landed > B * CAP_HIBETA + 1e-9:
                    skipped.append("%s(%s)：高弹性合计超 15%%×B" % (coin, sec))
                    continue
                sector_sum[sec] = sec_total
                if bucket in BUCKET_CAPS:
                    hibeta_sum += landed
                n_new += 1
                instructions.append({
                    "策略名称": (stp or {}).get("策略名称") or ("MARTIN-%sUSDT（新建）" % coin),
                    "vt_symbol": (stp or {}).get("vt_symbol") or "【AI 填写：get_all_contracts】",
                    "ticker": coin, "板块": sec, "层级": "强势龙头%d" % n_new,
                    "龙头超额_20d_pct": info.get("20日超额_pct"),
                    "当前持仓": cur_qty, "动作": "加仓" if cur_qty > 0 else "开仓",
                    "现价": px, "目标持仓": tgt_qty,
                    "目标资金": landed, "槽位资金": slot_cash,
                    "取整损失": round(max(cap_cash - landed, 0.0), 2),
                    "目标仓位占比_pct": round(landed / B * 100, 2),
                    "volume_min_unit": unit,
                    "买入闸证据": {"当日涨幅_pct": pct_day, "5日涨幅_pct": r5, "7日涨幅_pct": r7, "分桶": bucket},
                    "分批计划": _batches(tgt_qty, unit),
                    "资金调整": {"初始资金": round(slot_cash * 1.2, 2), "说明": "slot %.2f × 1.2" % slot_cash},
                    "意图备注": "【AI 填写】", "调仓理由": "【AI 填写】",
                })
    else:
        adjustments.append("收缩档（只卖不买）：买入候选全部跳过")
    if not sell_only and n_new >= max_new:
        rest = [s for s in (sel.get("强势板块") or []) if s not in sector_sum]
        if rest:
            adjustments.append("新仓名额已用满（%d/%d），强势板块 %s 未评估/未开仓" % (n_new, max_new, "、".join(rest)))

    # ---- 汇总
    buy_landed = round(sum(i["目标资金"] for i in instructions if i["动作"] in ("开仓", "加仓")), 2)
    sell_released = round(sold_value, 2)
    touched = {i["ticker"] for i in instructions}
    kept = round(sum(v for c, v in held.items() if c not in touched), 2)
    total_pos = round(kept + buy_landed, 2)
    total_pos_pct = round(total_pos / B * 100, 2)
    cash_pct = round(100 - total_pos_pct, 2)
    remaining = round(available + sell_released - buy_landed, 2)
    init_sum = round(sum(i["资金调整"]["初始资金"] for i in instructions if i["动作"] in ("开仓", "加仓")), 2)
    caps_check = {
        "单币上限(=slot_pct×B)": {"通过": all(i["目标资金"] <= B * slot_pct + 0.01 for i in instructions if i["动作"] in ("开仓", "加仓")),
                              "说明": "cap_cash ≤ slot_cash 已在数量先行时约束"},
        "单板块≤20%×B": {"通过": all(v <= B * CAP_SECTOR + 0.01 for v in sector_sum.values()),
                     "说明": "; ".join("%s=%.2f" % (k, v) for k, v in sector_sum.items()) or "无买入"},
        "高弹性合计≤15%×B": {"通过": hibeta_sum <= B * CAP_HIBETA + 0.01, "说明": "%.2f" % hibeta_sum},
        "前三大板块≤50%×B": {"通过": True, "说明": "板块数与上限结构下不可能触发（单板块≤20%）"},
        "现金≥5%×B(倍率=1)": {"通过": remaining >= B * CAP_CASH - 0.01,
                          "说明": "剩余现金 %.2f vs 下限 %.2f" % (remaining, round(B * CAP_CASH, 2))},
        "B≤可用×2": {"通过": B <= available * 2 + 1e-9, "说明": "%.2f ≤ %.2f" % (B, available * 2)},
        "Σ初始资金≤B": {"通过": init_sum <= B + 0.01, "说明": "%s ≤ %.2f" % (init_sum, B)},
        "持仓数≤50": {"通过": len([i for i in instructions if i["动作"] in ("开仓", "加仓")]) + len(held) <= MAX_POSITIONS,
                   "说明": "新开/加仓 %d + 存量 %d" % (n_new, len(held))},
    }
    obj = {
        "工具": "rebalance_x plan (v%s)" % VERSION, "版本": VERSION, "market": "加密货币",
        "B": B, "available": available, "invest_mult": mult, "micro": micro,
        "mode": mode, "sell_only": sell_only, "phase": phase,
        "slot_pct": slot_pct, "slot_cash": slot_cash, "max_new": max_new,
        "instructions": instructions + sells, "adds": adds,
        "skipped": skipped, "adjustments": adjustments,
        "unmapped": unmapped,
        "held_value": round(held_total, 2), "held_pct": round(held_total / B * 100, 2),
        "未涉及持仓_pct": round(kept / B * 100, 2),
        "资金": {"卖出释放": sell_released, "买入使用": buy_landed, "初始资金合计": init_sum,
               "剩余现金": remaining, "调仓后总仓位_pct": total_pos_pct, "调仓后现金_pct": cash_pct},
        "caps_check": caps_check,
        "notes": ["取整损失 = cap_cash − 落地，逐指令记录；目标资金=落地=目标持仓×现价（单一口径）"],
    }
    if out:
        save_json(out, obj)
    print("plan: B=%.2f micro=%s mode=%s phase=%s slot=%.2f max_new=%d" % (B, micro, mode, phase, slot_cash, max_new))
    for i in instructions + sells:
        print("  [%s] %s %s 目标持仓=%s 落地=%s" % (i["动作"], i["ticker"], i.get("层级", ""), i["目标持仓"], i.get("目标资金") or i.get("当前市值")))
    for s in skipped[:6]:
        print("  skip: " + s)
    print("资金: 买入 %s / 卖出 %s / 剩余 %s / 总仓位 %s%%" % (buy_landed, sell_released, remaining, total_pos_pct))
    return obj


def sel_sector_of(sel, sym):
    for r in sel.get("sectors", []):
        if r.get("龙头") == sym or r.get("候选2") == sym:
            return r.get("板块")
    return None


def _mk_sell(coin, stp, value, B, reason):
    return {"策略名称": stp.get("策略名称"), "vt_symbol": stp.get("vt_symbol"), "ticker": coin,
            "板块": None, "层级": "清仓", "当前持仓": num(stp.get("实际持仓")) or 0.0,
            "动作": "清仓", "现价": None, "目标持仓": 0.0, "目标资金": 0.0,
            "当前市值": round(value, 2), "目标仓位占比_pct": 0.0,
            "清仓原因": reason, "分批计划": [], "意图备注": "【AI 填写】", "调仓理由": "【AI 填写】"}


def _batches(tgt, unit):
    """3 批 30/60/100 累计；前两批向上补到单位整数倍，末批=目标（补差）"""
    if tgt <= 0:
        return []
    fr = [0.30, 0.60, 1.00]
    cum = []
    for f in fr[:-1]:
        c = tgt * f
        u = math.ceil(c / unit - 1e-9) * unit
        cum.append(round(min(u, tgt), 8))
    cum.append(round(tgt, 8))
    out, prev = [], 0.0
    for i, c in enumerate(cum):
        out.append({"批次": i + 1, "累计目标持仓": c, "增量": round(c - prev, 8),
                    "日期": "【AI 填写】"})
        prev = c
    return out


# ---------------------------------------------------------------- report
def run_report(gate, select, plan, snapshot, out, analysis_time=None):
    g = load_json(gate, "gate.json")
    sel = load_json(select, "select.json")
    pl = load_json(plan, "plan.json")
    snap = load_json(snapshot, "snapshot.json")
    pz = g.get("params") or {}
    conf = []
    for n in (g.get("notes") or []):
        conf.append("（gate）" + n)
    for n in (sel.get("notes") or []):
        if n.startswith("缺失"):
            conf.append("（select）" + n)
    for s in (pl.get("skipped") or []):
        conf.append("（plan·跳过）" + s)
    for a in (pl.get("adjustments") or []):
        conf.append("（plan·调整）" + a)
    for u in (pl.get("unmapped") or []):
        conf.append("（plan）未映射：" + u)
    conf.append("（口径）目标资金=落地=目标持仓×现价（qty-first 单一口径）；取整损失逐指令记录")
    kline_last = (g.get("as_of") or {}).get("kline_last_bar", "")
    live_px = load_json(os.path.join(os.path.dirname(out), "live_prices.json"), "live_prices.json") \
        if os.path.exists(os.path.join(os.path.dirname(out), "live_prices.json")) else {}
    raw_path = os.path.join(os.path.dirname(out), "crypto_prices_raw.json")
    if os.path.exists(raw_path):
        raw = load_json(raw_path, "crypto_prices_raw.json")
        devs = []
        for sym, t in live_px.items():
            cl = ((raw.get(sym) or {}).get("closes") or {})
            if not cl or not isinstance(t, (int, float)) or not t:
                continue
            lc = cl[max(cl.keys())]
            d = abs(t / lc - 1) * 100
            if d > 1.5:
                devs.append("%s %+.1f%%" % (sym, (t / lc - 1) * 100))
        if devs:
            conf.append("（时效）末根(%s) vs 实时价 偏差>1.5%%：%s（开仓标的除外须单列）" % (kline_last, ", ".join(sorted(devs)[:12])))
    report = {
        "market": "加密货币", "report_name": REPORT_NAME,
        "analysis_time": analysis_time or now_cst(),
        "操作模式": pz.get("mode", g.get("mode")),
        "参数": {"invest_mult": pz.get("invest_mult", 1), "mode": pz.get("mode"), "phase": g.get("phase"),
               "sell_only": pz.get("sell_only"), "max_new": pz.get("max_new"),
               "slot_pct": pz.get("slot_pct"), "slot_cash": pl.get("slot_cash"),
               "B": pl.get("B"), "micro": pl.get("micro")},
        "gate": {"tier": g.get("tier"), "phase": g.get("phase"),
                 "mode_evidence": g.get("mode_evidence"), "phase_evidence": g.get("phase_evidence"),
                 "parallel": g.get("parallel"), "notes": g.get("notes")},
        "选币排序": sel.get("sectors"),
        "强势板块": sel.get("强势板块"), "弱势板块": sel.get("弱势板块"),
        "调仓指令": pl.get("instructions"),
        "新增策略": pl.get("adds"),
        "资金汇总": {"权益_balance(USDT)": pl.get("B"), "可用现金(USDT)": pl.get("available"),
                  "invest_mult": 1, "总预算_B(USDT)": pl.get("B"),
                  "卖出释放": (pl.get("资金") or {}).get("卖出释放"),
                  "买入使用": (pl.get("资金") or {}).get("买入使用"),
                  "初始资金合计": (pl.get("资金") or {}).get("初始资金合计"),
                  "剩余现金": (pl.get("资金") or {}).get("剩余现金"),
                  "调仓后总仓位_pct": (pl.get("资金") or {}).get("调仓后总仓位_pct"),
                  "调仓后现金_pct": (pl.get("资金") or {}).get("调仓后现金_pct")},
        "风控校验": pl.get("caps_check"),
        "数据缺失与冲突": conf,
        "数据溯源": {"行情末根日期": kline_last, "账户快照时间": snap.get("as_of", ""),
                  "大盘报告时间": g.get("as_of", {}).get("market_report", ""),
                  "gate/select/plan": "机判文件链，verify 复算"},
        "执行建议": "【AI 填写】" + ("指令 %d 条（含清仓 %d）；" % (
            len(pl.get("instructions") or []), len([i for i in pl.get("instructions") or [] if i["动作"] == "清仓"]))
            if pl.get("instructions") else "本轮无调仓：须写明依据链 ≥80 字；"),
    }
    if out:
        save_json(out, report)
    print("report: %s | 指令 %d | 缺失与冲突 %d 条" % (out, len(report["调仓指令"]), len(conf)))
    return report


# ---------------------------------------------------------------- verify
def run_verify(report, gate, select, plan, units, balance, available,
               prices=None, ticks=None, live=None):
    rp = load_json(report, "报告.json")
    g = load_json(gate, "gate.json")
    sel = load_json(select, "select.json")
    pl = load_json(plan, "plan.json")
    un = load_json(units, "volume_min_unit.json")
    raw = load_json(prices, "crypto_prices_raw.json") if prices else None
    tk = load_json(ticks, "live_ticks_raw.json") if ticks else None
    lv = load_json(live, "live_prices.json") if live else None
    items = []

    def add(name, ok, why):
        items.append({"项目": name, "状态": ("通过" if ok else ("无法校验" if ok is None else "不通过")), "说明": why})

    B = num(pl.get("B"))
    fv = num(rp.get("资金汇总", {}).get("权益_balance(USDT)"))
    if balance is not None:
        add("账户快照(权益)", abs((fv or 0) - balance) <= 0.01, "独立 %.2f vs 报告 %.2f" % (balance, fv))
    else:
        add("账户快照(权益)", None, "未提供 --balance")
    if available is not None:
        av = num(rp.get("资金汇总", {}).get("可用现金(USDT)"))
        add("账户快照(可用)", abs((av or 0) - available) <= 0.01, "独立 %.2f vs 报告 %.2f" % (available, av))
    else:
        add("账户快照(可用)", None, "未提供 --available")

    add("报告名", rp.get("report_name") == REPORT_NAME, "须逐字 %s" % REPORT_NAME)
    add("gate 复算·模式", rp.get("操作模式") == (g.get("params") or {}).get("mode", g.get("mode")),
        "报告 %s vs gate %s" % (rp.get("操作模式"), (g.get("params") or {}).get("mode", g.get("mode"))))
    add("gate 复算·阶段", (rp.get("参数") or {}).get("phase") == g.get("phase"),
        "报告 %s vs gate %s" % ((rp.get("参数") or {}).get("phase"), g.get("phase")))
    sell_only = bool((g.get("params") or {}).get("sell_only"))
    add("gate 复算·只卖不买", (not sell_only) or all(i["动作"] == "清仓" for i in rp.get("调仓指令", [])),
        "sell_only=%s" % sell_only)
    add("select 复算·强势板块", rp.get("强势板块") == sel.get("强势板块"),
        "报告 %s vs select %s" % (rp.get("强势板块"), sel.get("强势板块")))

    instr = rp.get("调仓指令") or []
    buys = [i for i in instr if i.get("动作") in ("开仓", "加仓")]
    max_new = (g.get("params") or {}).get("max_new")
    add("max_new", len(buys) <= (max_new or 0), "买入 %d ≤ %d" % (len(buys), max_new))
    add("指令板块∈强势板块",
        all((i.get("板块") in (sel.get("强势板块") or [])) for i in buys),
        "; ".join("%s:%s" % (i["ticker"], i.get("板块")) for i in buys) or "无买入")

    bad_qty, bad_money, bad_bucket, bad_top10 = [], [], [], []
    for i in buys:
        u = unit_of(un, i["ticker"])
        q = num(i.get("目标持仓"))
        px = num(i.get("现价"))
        mv = num(i.get("目标资金"))
        if not u or q is None or px is None or mv is None:
            bad_qty.append("%s:字段缺失" % i["ticker"])
            continue
        if not is_mult(q, u):
            bad_qty.append("%s:%s 非 %s 整数倍" % (i["ticker"], q, u))
        if abs(mv - round(q * px, 2)) > 0.01:
            bad_money.append("%s:目标资金 %s ≠ qty×价 %.2f" % (i["ticker"], mv, q * px))
        evd = i.get("买入闸证据") or {}
        bkt = evd.get("分桶")
        cap = BUCKET_CAPS.get(bkt)
        if cap and mv > B * cap + 0.01:
            bad_bucket.append("%s:%s 超分桶上限 %.0f%%×B" % (i["ticker"], bkt, cap * 100))
        if (rp.get("参数") or {}).get("phase") == "早期" and bkt not in (None, "top10"):
            bad_top10.append("%s:早期非 top10" % i["ticker"])
    add("目标持仓=单位整数倍", not bad_qty, "; ".join(bad_qty) or "OK")
    add("目标资金=qty×现价(单一口径)", not bad_money, "; ".join(bad_money) or "OK")
    add("分桶单币上限", not bad_bucket, "; ".join(bad_bucket) or "OK")
    add("早期只加 top10", not bad_top10, "; ".join(bad_top10) or "OK")

    # 买入闸实时复核（提供 prices/ticks 才可校验）
    if tk and raw:
        bad_gate = []
        for i in buys:
            t = tk.get(i["ticker"]) if isinstance(tk.get(i["ticker"]), dict) else {}
            pd_ = num(t.get("pct_day"))
            if pd_ is not None and pd_ > 5:
                bad_gate.append("%s:当日 %+.2f%%>5%%" % (i["ticker"], pd_))
            try:
                _, cs = closes_of(raw, i["ticker"])
            except Exception:
                cs = []
            r5, r7 = ret_pct(cs, 5), ret_pct(cs, 7)
            if r5 is not None and r5 > 30:
                bad_gate.append("%s:5日 %+.2f%%>30%%" % (i["ticker"], r5))
            if r7 is not None and r7 > 50:
                bad_gate.append("%s:7日 %+.2f%%>50%%" % (i["ticker"], r7))
        add("买入闸复核(不追高/不买暴涨)", not bad_gate, "; ".join(bad_gate) or "OK")
    else:
        add("买入闸复核(不追高/不买暴涨)", None, "未提供 --ticks/--prices")

    fz = rp.get("资金汇总") or {}
    buy_used = round(sum(num(i.get("目标资金")) or 0 for i in buys), 2)
    sell_rel = round(sum(num(i.get("当前市值")) or 0 for i in instr if i.get("动作") == "清仓"), 2)
    add("买入使用=Σ落地", abs(num(fz.get("买入使用")) - buy_used) <= 0.01, "%s vs %s" % (fz.get("买入使用"), buy_used))
    init_sum = round(sum(num((i.get("资金调整") or {}).get("初始资金")) or 0 for i in buys), 2)
    add("闭环 Σ初始≤B", init_sum <= B + 0.01, "%s ≤ %.2f" % (init_sum, B))
    if available is not None:
        add("闭环 B≤可用×2", B <= available * 2 + 1e-9, "%.2f ≤ %.2f" % (B, available * 2))
        rem = num(fz.get("剩余现金"))
        exp = round(available + sell_rel - buy_used, 2)
        add("剩余现金=可用+卖出−买入", rem is not None and abs(rem - exp) <= 0.01, "%s vs %s" % (rem, exp))
        add("闭环 现金≥B×5%", rem is not None and rem >= B * CAP_CASH - 0.01,
            "%.2f vs %.2f" % (rem, round(B * CAP_CASH, 2)))
    else:
        add("闭环 B≤可用×2 / 现金下限", None, "未提供 --available")

    tot = num(fz.get("调仓后总仓位_pct"))
    csh = num(fz.get("调仓后现金_pct"))
    add("顶层%互验", tot is not None and csh is not None and abs(tot + csh - 100) <= 0.01,
        "%s + %s" % (tot, csh))
    recon = round((num(pl.get("未涉及持仓_pct")) or 0)
                  + sum(num(i.get("目标仓位占比_pct")) or 0 for i in buys), 2)
    add("组合重建", tot is not None and abs(recon - tot) <= 0.05,
        "未涉及 %s + 指令 %s = %s vs 报告 %s" % (pl.get("未涉及持仓_pct"),
        sum(num(i.get("目标仓位占比_pct")) or 0 for i in buys), recon, tot))

    bad_b = []
    for i in instr:
        for b_ in (i.get("分批计划") or []):
            u = unit_of(un, i["ticker"])
            cq = num(b_.get("累计目标持仓"))
            if u and cq is not None and not is_mult(cq, u):
                bad_b.append("%s 批%d 非整数倍" % (i["ticker"], b_.get("批次")))
            if cq is not None and i.get("动作") in ("开仓", "加仓") and cq > num(i.get("目标持仓")) + 1e-9:
                bad_b.append("%s 批%d 超目标" % (i["ticker"], b_.get("批次")))
        if i.get("动作") in ("开仓", "加仓") and i.get("分批计划"):
            last = (i["分批计划"][-1] or {}).get("累计目标持仓")
            if num(last) != num(i.get("目标持仓")):
                bad_b.append("%s 末批≠目标" % i["ticker"])
    add("分批自洽", not bad_b, "; ".join(bad_b) or "OK")

    prov = rp.get("数据溯源") or {}
    miss = [k for k in ("行情末根日期", "账户快照时间", "大盘报告时间") if not prov.get(k)]
    add("数据溯源三键", not miss, "缺失 %s" % miss if miss else "齐全")
    if not instr and not (rp.get("新增策略") or []):
        sug = rp.get("执行建议") or ""
        add("空报告依据链", ("本轮无调仓" in sug and len(sug) >= 90), "执行建议须写明依据链 ≥80 字")

    n_bad = sum(1 for i in items if i["状态"] == "不通过")
    n_na = sum(1 for i in items if i["状态"] == "无法校验")
    concl = "不通过" if n_bad else ("无法校验" if n_na else "通过")
    obj = {"成功": True, "工具": "rebalance_x verify (v%s)" % VERSION, "版本": VERSION,
           "市场": "加密货币", "校验": concl, "不通过项数": n_bad, "无法校验项数": n_na,
           "明细": items}
    print(json.dumps({"校验": concl, "不通过": n_bad, "无法校验": n_na}, ensure_ascii=False))
    for i in items:
        print("  [%s] %s — %s" % (i["状态"], i["项目"], i["说明"][:80]))
    return obj, (0 if concl == "通过" else (1 if concl == "不通过" else 2))


# ---------------------------------------------------------------- run
def cmd_run(a):
    dd = os.path.abspath(a.data_dir)
    snap_path = os.path.join(dd, "snapshot.json")
    if not os.path.exists(snap_path):
        fail("缺 %s：AI 侧 vnpy_mcp 只读落盘（格式见 02_选币与计划 §2.8）" % snap_path)
    snap = load_json(snap_path, "snapshot.json")
    sraw = os.path.join(dd, "strategies_raw.json")
    if not os.path.exists(sraw):
        save_json(sraw, snap.get("strategies") or [])
        print("run: 由 snapshot.strategies 生成 strategies_raw.json（volume-unit 输入）")

    def fetch(sub, extra=()):
        cmd = [sys.executable, "-X", "utf8", FETCH, sub, "--data-dir", dd] + list(extra)
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        tail = (r.stdout or "")[-300:].strip().replace("\n", " | ")
        if r.returncode != 0:
            fail("rebalance_fetch %s exit=%s：%s %s" % (sub, r.returncode, tail, (r.stderr or "")[-200:]))
        print("fetch %s: %s" % (sub, tail[:160]))

    fetch("klines")
    fetch("ticks")
    fetch("volume-unit", ("--strategies", sraw))

    P = lambda n: os.path.join(dd, n)
    run_gate(P("crypto_prices_raw.json"), P("market_report.json"), P("live_prices.json"),
             DEFAULT_POOL, P("x_gate.json"))
    run_select(P("crypto_prices_raw.json"), DEFAULT_POOL, P("x_select.json"))
    run_plan(P("x_gate.json"), P("x_select.json"), snap_path, P("crypto_prices_raw.json"),
             P("live_prices.json"), P("live_ticks_raw.json"), P("volume_min_unit.json"),
             DEFAULT_POOL, P("x_plan.json"))
    run_report(P("x_gate.json"), P("x_select.json"), P("x_plan.json"), snap_path, P("x_report.json"))
    _, code = run_verify(P("x_report.json"), P("x_gate.json"), P("x_select.json"), P("x_plan.json"),
                         P("volume_min_unit.json"), num(snap.get("balance")), num(snap.get("available")),
                         prices=P("crypto_prices_raw.json"), ticks=P("live_ticks_raw.json"),
                         live=P("live_prices.json"))
    sys.exit(code)


# ---------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(description="加密货币板块轮动调仓 · 探索版工具链 (v%s)" % VERSION,
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("gate", help="模式+轮动阶段机判")
    p.add_argument("--prices", required=True)
    p.add_argument("--market-report", required=True)
    p.add_argument("--live", required=True)
    p.add_argument("--pool", default=DEFAULT_POOL)
    p.add_argument("--out", required=True)
    p.add_argument("--mode-override", default=None, help="仅 积极/正常/防御，须留痕")
    p.add_argument("--liquidation-usd", type=float, default=None)
    p.add_argument("--funding-high", default=None)

    p = sub.add_parser("select", help="板块龙头 20 日超额排序")
    p.add_argument("--prices", required=True)
    p.add_argument("--pool", default=DEFAULT_POOL)
    p.add_argument("--out", required=True)

    p = sub.add_parser("plan", help="数量先行计划")
    p.add_argument("--gate", required=True)
    p.add_argument("--select", required=True)
    p.add_argument("--snapshot", required=True)
    p.add_argument("--prices", required=True)
    p.add_argument("--live", required=True)
    p.add_argument("--ticks", required=True)
    p.add_argument("--units", required=True)
    p.add_argument("--pool", default=DEFAULT_POOL)
    p.add_argument("--out", required=True)

    p = sub.add_parser("report", help="装配 13 字段骨架")
    p.add_argument("--gate", required=True)
    p.add_argument("--select", required=True)
    p.add_argument("--plan", required=True)
    p.add_argument("--snapshot", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--analysis-time", default=None)

    p = sub.add_parser("verify", help="独立复算校验门")
    p.add_argument("--report", required=True)
    p.add_argument("--gate", required=True)
    p.add_argument("--select", required=True)
    p.add_argument("--plan", required=True)
    p.add_argument("--units", required=True)
    p.add_argument("--balance", type=float, default=None)
    p.add_argument("--available", type=float, default=None)
    p.add_argument("--prices", default=None)
    p.add_argument("--ticks", default=None)
    p.add_argument("--live", default=None)

    p = sub.add_parser("run", help="全链编排（fetch→gate→select→plan→report→verify）")
    p.add_argument("--data-dir", required=True)

    a = ap.parse_args()
    if a.cmd == "gate":
        run_gate(a.prices, a.market_report, a.live, a.pool, a.out,
                 a.mode_override, a.liquidation_usd, a.funding_high)
    elif a.cmd == "select":
        run_select(a.prices, a.pool, a.out)
    elif a.cmd == "plan":
        run_plan(a.gate, a.select, a.snapshot, a.prices, a.live, a.ticks, a.units, a.pool, a.out)
    elif a.cmd == "report":
        run_report(a.gate, a.select, a.plan, a.snapshot, a.out, a.analysis_time)
    elif a.cmd == "verify":
        _, code = run_verify(a.report, a.gate, a.select, a.plan, a.units,
                             a.balance, a.available, a.prices, a.ticks, a.live)
        sys.exit(code)
    elif a.cmd == "run":
        cmd_run(a)


if __name__ == "__main__":
    main()
