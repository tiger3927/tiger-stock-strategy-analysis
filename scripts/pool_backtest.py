# -*- coding: utf-8 -*-
"""
pool_backtest.py — 选股池历史回放评估本体（P0-4，正式代码放 scripts/）

评估"维护后池子"相对"维护前池子 / 随机对照 / 基准"在 as-of 后 horizon 内的
表现差异——这才是测提示词（选股池维护流程）边际贡献的工具。

用法示例:
  python pool_backtest.py --before replay/pool_before_2025-06-30.json \
      --after replay/pool_2025-06-30.json --as-of 2025-06-30 --horizon 21 --out replay/bt.json
  多日期: --as-of 2025-06-30,2025-03-31,2024-12-31
  对照组: --control random:40（同 tier 分布抽样，seed 固定可复现）

关键设计（与 Task-代码取数前视能力.md 对齐）:
  - 取数走 get_market_data.get_history_dataframe(end=..., adj=True)：
    多日期一次取宽窗口、本地按 as-of 切片（每 ticker 全程一次请求，限流可控）；
  - 决策侧只用 as-of 前数据；as-of 后价格仅用于评估结果（非决策前视）；
  - --weight cap 用当前市值（info 非时点值）→ 结果标 fundamentals_point_in_time=false；
  - --missing drop|zero|carry 显式处理退市/缺数据（退市恰是选股信号，不得默认丢弃且必须留痕）；
  - stdout ≤30 行摘要，全量结果落 --out JSON（成本红线）。
"""
import argparse
import datetime
import io
import json
import math
import os
import random
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

import pandas as pd  # noqa: E402
import get_market_data as gmd  # noqa: E402
import fetch_index_constituents as fic  # noqa: E402  P1-3 接线：成员维度 as-of 对照

TRADING_DAYS_PER_YEAR = 252


def load_tickers(path):
    """池 JSON → {tier: [tickers]}"""
    with io.open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    out = {"T1": [], "T2": [], "T3": []}
    for info in data.get("sub_sectors", {}).values():
        for tier in out:
            out[tier].extend(info.get("tiers", {}).get(tier, []))
    for tier in out:
        out[tier] = sorted(set(out[tier]))
    return out


def constituents_audit(as_of, pool_map):
    """P1-3 接线：池成员 vs **as-of 当期官方成分**（SEC NPORT-P，双重门控 PIT）。

    回答的是回放里最贵的问题——"这个池子当时装的是真成员，还是今天的幸存者"。
    三态（缺第三态会把 T3 小盘误判成"不是成员"）：
      命中        ticker ∈ 当期成分
      非成员      ticker ∈ 覆盖范围参照集（当期 ∪ 最新一期）但当期不在 → 幸存者偏差信号
      unknown     覆盖范围外（小盘/板块）→ 不判
    档案不可用（as-of 早于 2019-09-30 / 取数失败）→ available=False，**不得假装有成分**。
    """
    info = fic.constituents_for_asof(as_of)
    if not info.get("available"):
        return {"available": False, "requested_as_of": as_of, "note": info.get("note")}
    cur = {str(t).upper() for t in info.get("symbols", [])}
    scope = {str(t).upper() for t in info.get("scope_symbols", [])}
    per_tier, bad_all = {}, []
    for tier, lst in pool_map.items():
        hit, bad, unk = [], [], []
        for t in lst:
            u = str(t).upper()
            if u in cur:
                hit.append(t)
            elif u in scope:
                bad.append(t)
            else:
                unk.append(t)
        per_tier[tier] = {"当期成员命中": len(hit), "覆盖范围内但当期非成员": bad,
                          "覆盖范围外_unknown": len(unk)}
        bad_all += bad
    return {
        "available": True,
        "requested_as_of": as_of,
        "source": "sec_nport_p:SPY+MDY",
        "holdings_as_of": info.get("holdings_as_of"),
        "lag_days": info.get("lag_days"),
        "match_rate": info.get("match_rate"),
        "members_total": info.get("members_total"),
        "unmatched_count": info.get("unmatched_count"),
        "per_tier": per_tier,
        "survivorship_suspects": bad_all,      # 池里有、当期官方成分没有
        "note": info.get("note"),
    }


def fetch_wide(tickers, as_ofs, horizon_days, cache_dir, period="2y"):
    """一次取宽窗口（end=最晚 as-of + horizon 自然日缓冲，封顶今天），
    返回 ({ticker: adj_close Series}, failed, end_s)。多日期回放共用。"""
    gmd._RUNTIME["asof_dir"] = os.path.abspath(cache_dir)
    today = datetime.datetime.now(datetime.timezone.utc).date()
    latest = max(datetime.datetime.strptime(a, "%Y-%m-%d").date() for a in as_ofs)
    end = min(today, latest + datetime.timedelta(days=int(horizon_days * 1.6) + 10))
    end_s = end.isoformat()
    closes, failed = {}, []
    for t in tickers:
        try:
            df = gmd.get_history_dataframe(t, period, end=end_s, adj=True)
            col = "adj_close" if "adj_close" in df.columns else "Close"
            s = df[col].astype(float)
            s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
            closes[t] = s
        except Exception as e:
            failed.append({"ticker": t, "error": str(e)[:150]})
    return closes, failed, end_s


def _price_on(series, date):
    """≤date 的最近收盘。"""
    sub = series[series.index <= pd.Timestamp(date)]
    return float(sub.iloc[-1]) if len(sub) else None


def _bar_at(series, date, offset_bars):
    """date 起第 offset_bars 个交易日的收盘（entry=0；exit=horizon）。"""
    sub = series[series.index >= pd.Timestamp(date)]
    if len(sub) <= offset_bars:
        return None
    return float(sub.iloc[offset_bars])


def portfolio_metrics(closes, tickers, weights, as_of, horizon_bars, bench_series,
                      missing="drop"):
    """组合 horizon 收益 + 风险指标（日频加权漂移）。"""
    ts = pd.Timestamp(as_of)
    entries, exits, used, dropped = {}, {}, [], []
    for t in tickers:
        s = closes.get(t)
        if s is None:
            dropped.append(t)
            continue
        e = _price_on(s, ts)
        x = _bar_at(s, ts, horizon_bars)
        if e is None:
            dropped.append(t)
            continue
        if x is None:
            if missing == "drop":
                dropped.append(t)
                continue
            if missing == "zero":
                x = e  # 0% 收益
            else:  # carry：冻结在最后可得价
                x = float(s.iloc[-1])
        entries[t], exits[t] = e, x
        used.append(t)

    if not used:
        return {"error": "全部标的无可用数据", "dropped": dropped}

    w = dict(weights)
    tot = sum(w.get(t, 0.0) for t in used)
    if tot <= 0:
        w = {t: 1.0 / len(used) for t in used}
    else:
        w = {t: w.get(t, 0.0) / tot for t in used}

    ret = sum(w[t] * (exits[t] / entries[t] - 1.0) for t in used)

    end = ts + pd.Timedelta(days=int(horizon_bars * 1.6) + 10)
    px = pd.DataFrame({t: closes[t].loc[ts:end] for t in used}).dropna(how="any")
    daily = px.pct_change().dropna()
    port_daily = (daily * pd.Series(w)).sum(axis=1)

    vol = float(port_daily.std() * math.sqrt(TRADING_DAYS_PER_YEAR)) if len(port_daily) > 2 else None
    sharpe = float(port_daily.mean() / port_daily.std() * math.sqrt(TRADING_DAYS_PER_YEAR)) \
        if len(port_daily) > 2 and port_daily.std() > 0 else None
    cum = (1 + port_daily).cumprod()
    dd = float((cum / cum.cummax() - 1).min()) if len(cum) else None
    beta = None
    if bench_series is not None and len(port_daily) > 5:
        b = bench_series.loc[ts:end].pct_change().dropna()
        joined = pd.concat([port_daily, b], axis=1, join="inner").dropna()
        if len(joined) > 5:
            var = joined.iloc[:, 1].var()
            beta = float(joined.iloc[:, 0].cov(joined.iloc[:, 1]) / var) if var > 0 else None

    return {
        "horizon_return_pct": round(ret * 100, 2),
        "volatility_pct": round(vol * 100, 1) if vol is not None else None,
        "sharpe": round(sharpe, 2) if sharpe is not None else None,
        "max_drawdown_pct": round(dd * 100, 1) if dd is not None else None,
        "beta_vs_bench": round(beta, 2) if beta is not None else None,
        "n_used": len(used), "n_dropped": len(dropped), "dropped": dropped[:20],
    }


def make_weights(tier_map, weight_mode, caps_provider=None):
    """tier_map {tier:[tickers]} → {ticker: weight}"""
    tiers = [t for t in ("T1", "T2", "T3") if tier_map.get(t)]
    if weight_mode == "tier":
        share = {"T1": 0.5, "T2": 0.3, "T3": 0.2}
        w = {}
        for tier in tiers:
            per = share[tier] / len(tier_map[tier])
            for t in tier_map[tier]:
                w[t] = per
        return w
    if weight_mode == "cap":
        caps = {}
        for tier in tiers:
            for t in tier_map[tier]:
                try:
                    info = gmd._get_info_cached(t)
                    caps[t] = max(1.0, float(info.get("marketCap") or 1.0))
                except Exception:
                    caps[t] = 1.0
        tot = sum(caps.values())
        return {t: caps[t] / tot for t in caps}
    all_t = [t for tier in tiers for t in tier_map[tier]]
    return {t: 1.0 / len(all_t) for t in all_t}


def run_one_date(before_map, after_map, as_of, args, closes, bench_closes):
    horizon = args.horizon
    tiers = ["T1", "T2", "T3"] if args.tiers == "all" else \
        [x.strip().upper() for x in args.tiers.split(",")]
    am = {t: after_map.get(t, []) for t in tiers}
    bm = {t: before_map.get(t, []) for t in tiers}

    bench_series = bench_closes.get(args.beta_benchmark)
    out = {"as_of": as_of, "horizon_bars": horizon, "tiers": tiers, "pools": {}}

    w_after = make_weights(am, args.weight)
    out["pools"]["after"] = portfolio_metrics(closes, list(w_after), w_after, as_of,
                                              horizon, bench_series, args.missing)
    w_before = make_weights(bm, args.weight)
    out["pools"]["before"] = portfolio_metrics(closes, list(w_before), w_before, as_of,
                                               horizon, bench_series, args.missing)

    # 随机对照：从 before 池按 after 的 tier 分布抽样（seed 固定可复现）
    if args.control.startswith("random"):
        n_target = int(args.control.split(":")[1]) if ":" in args.control else 0
        if n_target <= 0:
            n_target = sum(len(v) for v in am.values())  # random:0 → 与 after 同规模
        rng = random.Random(args.seed)
        cm = {}
        after_total = max(1, sum(len(v) for v in am.values()))
        for tier in tiers:
            pool = bm.get(tier, [])
            k = min(len(pool), max(1, round(n_target * len(am.get(tier, [])) / after_total)))
            cm[tier] = rng.sample(pool, k) if pool else []
        w_ctl = make_weights(cm, args.weight)
        out["control"] = {"drawn": {k: len(v) for k, v in cm.items()}, "seed": args.seed}
        out["pools"]["control"] = portfolio_metrics(closes, list(w_ctl), w_ctl, as_of,
                                                    horizon, bench_series, args.missing)

    # 基准（价格收益；^GSPC 全收益差异见任务书"区分价格 vs 全收益"）
    out["benchmarks"] = {}
    for b in args.benchmarks.split(","):
        s = bench_closes.get(b.strip())
        if s is None:
            continue
        e = _price_on(s, as_of)
        x = _bar_at(s, as_of, horizon)
        if e and x:
            out["benchmarks"][b.strip()] = round((x / e - 1) * 100, 2)

    a = out["pools"]["after"].get("horizon_return_pct")
    if a is not None:
        exc = {}
        for b, v in out["benchmarks"].items():
            exc[f"vs_{b}"] = round(a - v, 2)
        for k in ("before", "control"):
            v = out["pools"].get(k, {}).get("horizon_return_pct")
            if v is not None:
                exc[f"vs_{k}"] = round(a - v, 2)
        out["excess"] = exc
    return out


def main():
    ap = argparse.ArgumentParser(description="选股池历史回放评估（P0-4）")
    ap.add_argument("--before", required=True, help="维护前池 JSON（pool_before_*.json）")
    ap.add_argument("--after", required=True, help="维护后池 JSON（回放副本或生产池）")
    ap.add_argument("--as-of", required=True, help="逗号分隔 as-of 日期（YYYY-MM-DD）")
    ap.add_argument("--horizon", type=int, default=21, help="持有期交易日数（默认 21≈1 个月）")
    ap.add_argument("--tiers", default="all", help="T1,T2,T3 或 all（评估范围=after 池这些 tier）")
    ap.add_argument("--weight", choices=["equal", "cap", "tier"], default="equal",
                    help="cap 用当前市值（info 非时点值，结果标 fundamentals_point_in_time=false）")
    ap.add_argument("--missing", choices=["drop", "zero", "carry"], default="drop",
                    help="退市/缺数据处理（必须留痕）")
    ap.add_argument("--control", default="random:0",
                    help="对照组 random:N（从 before 池按 tier 分布抽样；N=0 → 与 after 同规模）")
    ap.add_argument("--seed", type=int, default=42, help="对照组随机种子（可复现）")
    ap.add_argument("--benchmarks", default="SPY,^GSPC,IWM,MDY,QQQ,RSP")
    ap.add_argument("--beta-benchmark", default="SPY")
    ap.add_argument("--cache-dir",
                    default=os.path.join(os.path.dirname(_SCRIPT_DIR), "replay", "cache"),
                    help="历史模式不可变缓存目录（不得指向 scripts/ 生产缓存）")
    ap.add_argument("--period", default="2y", help="宽窗口周期（覆盖多日期跨度，默认 2y）")
    ap.add_argument("--constituents", dest="constituents", default="annotate",
                    choices=["off", "annotate"],
                    help="P1-3 接线：annotate=每 as-of 用 SEC NPORT-P 官方成分对照池成员，"
                         "标出「池里有、当期官方成分没有」的幸存者嫌疑（默认）；off=不做")
    ap.add_argument("--out", default=None, help="全量结果 JSON 输出路径")
    args = ap.parse_args()

    if os.path.normcase(os.path.abspath(args.cache_dir)).startswith(
            os.path.normcase(_SCRIPT_DIR) + os.sep):
        print("❌ 命名红线：--cache-dir 不得位于 scripts/（生产目录）")
        sys.exit(1)

    as_ofs = [a.strip() for a in args.as_of.split(",") if a.strip()]
    for a in as_ofs:
        try:
            gmd._validate_end_date(a)
        except ValueError as e:
            print(f"❌ {e}")
            sys.exit(1)

    before_map = load_tickers(args.before)
    after_map = load_tickers(args.after)

    universe = sorted(set(before_map["T1"] + before_map["T2"] + before_map["T3"] +
                          after_map["T1"] + after_map["T2"] + after_map["T3"]))
    benches = [b.strip() for b in args.benchmarks.split(",")]
    print(f"取数: 池 {len(universe)} 只 + 基准 {len(benches)} 只，宽窗口 period={args.period} ...")
    closes, failed, end_s = fetch_wide(universe, as_ofs, args.horizon, args.cache_dir, args.period)
    bench_closes, failed_b, _ = fetch_wide(benches, as_ofs, args.horizon, args.cache_dir, args.period)
    print(f"取数完成: 池命中 {len(closes)}/{len(universe)}，失败 {len(failed)}；"
          f"基准命中 {len(bench_closes)}/{len(benches)}")

    results = [run_one_date(before_map, after_map, a, args, closes, bench_closes)
               for a in as_ofs]

    # P1-3 接线：成员维度对照（每 as-of 一次；SEC 侧按 accession 不可变缓存，命中后近乎零成本）
    members_ok = False
    if args.constituents != "off":
        for r in results:
            r["constituents"] = constituents_audit(r["as_of"], after_map)
            members_ok = members_ok or bool(r["constituents"].get("available"))

    summary = {"dates": len(results), "win_rates": {}}
    keys = set()
    for r in results:
        keys |= set(r.get("excess", {}).keys())
    for k in sorted(keys):
        vals = [r["excess"][k] for r in results if r.get("excess", {}).get(k) is not None]
        if vals:
            summary["win_rates"][k] = {
                "win_rate_pct": round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1),
                "avg_excess_pct": round(sum(vals) / len(vals), 2),
                "n": len(vals),
            }

    payload = {
        "requested_end": as_ofs,
        "point_in_time": False,  # 价格段 as-of 可回溯；info 基本面永远当前快照
        "members_point_in_time": members_ok,   # P1-3：成员集合可回溯（SEC NPORT-P 官方档案）
        "constituents_mode": args.constituents,
        "fundamentals_point_in_time": args.weight != "cap",
        "fetch_window_end": end_s,
        "weight_mode": args.weight, "missing_mode": args.missing,
        "horizon_bars": args.horizon,
        "失败项": (failed + failed_b)[:50],
        "results": results,
        "summary": summary,
    }
    if args.out:
        with io.open(args.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"✅ 全量结果已写入: {args.out}")

    # stdout 摘要（≤30 行；全量在 --out 文件——成本红线）
    for r in results:
        print(f"\n== as-of {r['as_of']} (horizon {r['horizon_bars']} 交易日) ==")
        for k in ("after", "before", "control"):
            p = r["pools"].get(k)
            if p and "horizon_return_pct" in p:
                print(f"  {k:8s} 收益={p['horizon_return_pct']:>7}%  波动={p['volatility_pct']}%  "
                      f"Sharpe={p['sharpe']}  回撤={p['max_drawdown_pct']}%  beta={p['beta_vs_bench']}  "
                      f"n={p['n_used']}(弃{p['n_dropped']})")
        if r.get("benchmarks"):
            print("  基准: " + ", ".join(f"{k}={v}%" for k, v in r["benchmarks"].items()))
        if r.get("excess"):
            print("  超额: " + ", ".join(f"{k}={v:+}pp" for k, v in r["excess"].items()))
        ca = r.get("constituents")
        if ca:
            if ca.get("available"):
                print(f"  成分: 持仓期末={ca['holdings_as_of']} 匹配率={ca['match_rate']} "
                      f"幸存者嫌疑={len(ca['survivorship_suspects'])} 只 "
                      f"{ca['survivorship_suspects'][:8]}")
            else:
                print("  成分: 档案不可用 → 成员维度退回 point_in_time=false")
    print("\n== 多日期汇总 ==")
    for k, v in summary["win_rates"].items():
        print(f"  {k:16s} 胜率={v['win_rate_pct']}%  平均超额={v['avg_excess_pct']:+}pp  (n={v['n']})")
    if failed:
        print(f"⚠️ 取数失败 {len(failed)} 只（详见 --out JSON 失败项）")


if __name__ == "__main__":
    main()
