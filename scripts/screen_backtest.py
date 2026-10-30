#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Top100 筛选组合回测（2026-10-30；消费 top100_<D8>.json，全本地零网络）

口径（事前定死，与 screen_params.json 一致）：
  持有期 = top100 调仓日 D_i → D_{i+1}（月初到月初）；等权
  价格   = wide_price 5y pkl，**拆股复权后**算区间收益（≤D_{i+1} 切片）
  缺数据 = 成员持有期内无后续 bar（退市/数据源删除）→ 冻结在最后可得价（carry，0% 后续），
           与 pool_backtest 既有约定一致；方向性偏差（幸存者）已在筛选层声明
  成本   = 单边 cost_bps_per_side × (|added|+|removed|)/top_n
  基准   = SPY、RSP（等权基准，对选中小盘公平）同区间同价格口径
  指标   = 累计/年化收益、超额、月度胜率、Sharpe(月,rf=0)、最大回撤、平均月换手
  消融   = full / momentum_only / no_fund / random100（文件存在即进表）

用法：python replay/screen_backtest.py [--variant full ...]
产物：replay/screen_backtest/{screen_returns.json, report.md, cumulative.png}
"""
import argparse
import datetime
import json
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
WIDE = os.path.join(SKILL, "screening", "wide_price")   # 可被 --wide 覆盖
OUT = os.path.join(SKILL, "screening", "backtest")      # 可被 --out 覆盖
PERIOD, WIDE_END = "5y", "2026-10-01"                   # 可被 --period/--wide-end 覆盖
VARIANTS = ["full", "momentum_only", "no_fund", "random100"]
PARAMS_PATH = os.path.join(HERE, "screen_params_v3.json")  # 可被 --params 覆盖


def load_params():
    return json.load(open(PARAMS_PATH, encoding="utf-8"))


def adj_series(tk):
    """拆股复权（screen_engine.adjust_vec——与 v1 循环版等价，回归已断言）。"""
    p_ = os.path.join(WIDE, f"{tk}_{PERIOD}_to{WIDE_END}.pkl")
    if not os.path.exists(p_):
        return None
    df = pd.read_pickle(p_)
    import screen_engine as eng
    par = load_params()
    c, _v, _n = eng.adjust_vec(df["Close"], df.get("Volume", pd.Series(1.0, index=df.index)),
                               par["split"]["candidate_ratios"], par["split"]["tol"])
    return pd.Series(c, index=df.index)


def ret_between(series, d0, d1):
    """≤d0 末根 → ≤d1 末根收益；d0 后无 bar → None（调用方 carry=0）。"""
    if series is None:
        return None
    i0 = series.index[pd.to_datetime(series.index) <= pd.Timestamp(d0)]
    i1 = series.index[pd.to_datetime(series.index) <= pd.Timestamp(d1)]
    if not len(i0) or not len(i1) or i1[-1] <= i0[-1]:
        return None
    return float(series[i1[-1]] / series[i0[-1]] - 1.0)


def months_files(variant, prefix="top100_", dir_=None):
    """full=top100_<D8>.json（严格 8 位数字，不误吞 top100_<variant>_<D8>.json）。
    prefix 可换（v2 产物 top100v2_）。"""
    pref = prefix if variant == "full" else f"{prefix}{variant}_"
    out = []
    for fn in sorted(os.listdir(dir_ or HERE)):
        if not fn.startswith(pref) or not fn.endswith(".json"):
            continue
        d8 = fn[len(pref):-len(".json")]
        if len(d8) != 8 or not d8.isdigit():
            continue
        out.append((datetime.date(int(d8[:4]), int(d8[4:6]), int(d8[6:8])), fn))
    return out


def run_variant(variant, cache, params, prefix="top100_", dir_=None):
    files = months_files(variant, prefix, dir_)
    if len(files) < 2:
        return None
    cost = params["cost_bps_per_side"] / 1e4
    top_n = params["top_n"]
    rows, prev_sel = [], set()
    for k, (D, fn) in enumerate(files[:-1]):
        rep = json.load(open(os.path.join(dir_ or HERE, fn), encoding="utf-8"))
        sel = [x["ticker"] for x in rep["selected"]]
        nxt = files[k + 1][0]
        rets, carried = [], 0
        for tk in sel:
            if tk not in cache:
                cache[tk] = adj_series(tk)
            r = ret_between(cache[tk], D.isoformat(), nxt.isoformat())
            if r is None:
                r, carried = 0.0, carried + 1   # carry：冻结最后价
            rets.append(r)
        port = sum(rets) / len(rets) if rets else 0.0
        added = set(sel) - prev_sel if prev_sel else set()
        removed = prev_sel - set(sel) if prev_sel else set()
        turn = (len(added) + len(removed)) / max(len(sel), 1)   # 实际组合规模（敏感性格 top_n≠100 时正确）
        bench = {}
        for b in ("SPY", "RSP"):
            if b not in cache:
                cache[b] = adj_series(b)
            bench[b] = ret_between(cache[b], D.isoformat(), nxt.isoformat()) or 0.0
        rows.append({"from": D.isoformat(), "to": nxt.isoformat(), "n": len(sel),
                     "carried": carried, "port_ret": port, "cost": turn * cost,
                     "net_ret": port - turn * cost, "turnover": turn, **bench})
        prev_sel = set(sel)

    def stats(series, bench_series):
        s = pd.Series(series)
        b = pd.Series(bench_series)
        ex = s - b
        eq = (1 + s).cumprod()
        eqb = (1 + b).cumprod()
        dd = float((eq / eq.cummax() - 1).min())
        sharpe = float(s.mean() / s.std()) * (12 ** 0.5) if s.std() else None
        return {"cum_pct": round((eq.iloc[-1] - 1) * 100, 2),
                "ann_pct": round(((eq.iloc[-1]) ** (12 / len(s)) - 1) * 100, 2),
                "bench_cum_pct": round((eqb.iloc[-1] - 1) * 100, 2),
                "cum_excess_pct": round((eq.iloc[-1] / eqb.iloc[-1] - 1) * 100, 2),
                "monthly_win_rate_pct": round(float((ex > 0).mean()) * 100, 1),
                "avg_month_excess_pp": round(float(ex.mean()) * 100, 3),
                "sharpe": round(sharpe, 2) if sharpe else None,
                "max_drawdown_pct": round(dd * 100, 2)}

    net = [r["net_ret"] for r in rows]
    res = {"variant": variant, "months": len(rows),
           "avg_turnover_pct": round(float(pd.Series([r["turnover"] for r in rows]).mean()) * 100, 1),
           "vs_SPY": stats(net, [r["SPY"] for r in rows]),
           "vs_RSP": stats(net, [r["RSP"] for r in rows]),
           "monthly": [{k: (round(v, 5) if isinstance(v, float) else v) for k, v in r.items()}
                       for r in rows]}
    return res


def main():
    global WIDE, PERIOD, WIDE_END, PARAMS_PATH
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--prefix", default="top100_", help="top100 文件前缀（v2=top100v2_）")
    ap.add_argument("--dir", default=None, help="名单文件目录（默认 --workspace）")
    ap.add_argument("--workspace", default=os.path.join(SKILL, "screening"),
                    help="screen_select 产物目录（名单所在）")
    ap.add_argument("--wide", default=WIDE, help="价格 pkl 目录")
    ap.add_argument("--period", default=PERIOD)
    ap.add_argument("--wide-end", default=WIDE_END)
    ap.add_argument("--params", default=PARAMS_PATH, help="成本/复权参数集（与打分同一冻结文件）")
    ap.add_argument("--out", default=OUT, help="产物目录")
    a = ap.parse_args()
    WIDE, PERIOD, WIDE_END, PARAMS_PATH = a.wide, a.period, a.wide_end, a.params
    if a.dir is None:
        a.dir = a.workspace
    out_dir = a.out
    os.makedirs(out_dir, exist_ok=True)
    params = load_params()
    cache = {}
    results = {}
    for v in a.variants.split(","):
        r = run_variant(v.strip(), cache, params, a.prefix, a.dir)
        if r:
            results[v.strip()] = r
    json.dump(results, open(os.path.join(out_dir, "screen_returns.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    first = months_files("full", a.prefix, a.dir)
    sha16 = (json.load(open(os.path.join(a.dir or HERE, first[0][1]), encoding="utf-8"))
             ["params_sha256_16"] if first else "?")
    lines = ["# Top100 传统指标筛选回测（PIT，无未来函数）\n",
             f"- 参数 sha256[:16]：{sha16}",
             f"- 口径：月初→月初等权；拆股复权；缺数据 carry=0%；成本 {params['cost_bps_per_side']}bp 单边；价格收益\n",
             "| variant | 月数 | 平均月换手 | 累计 | 年化 | vs SPY 累计超额 | SPY 月胜率 | vs RSP 累计超额 | RSP 月胜率 | Sharpe | 最大回撤 |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for v, r in results.items():
        s1, s2 = r["vs_SPY"], r["vs_RSP"]
        lines.append(f"| {v} | {r['months']} | {r['avg_turnover_pct']}% | {s1['cum_pct']}% | {s1['ann_pct']}% "
                     f"| {s1['cum_excess_pct']}pp | {s1['monthly_win_rate_pct']}% "
                     f"| {s2['cum_excess_pct']}pp | {s2['monthly_win_rate_pct']}% | {s1['sharpe']} | {s1['max_drawdown_pct']}% |")
    open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")

    # 累计曲线（full vs 基准）
    if "full" in results:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        m = results["full"]["monthly"]
        x = list(range(len(m) + 1))
        def cum(key):
            v, out = 1.0, [1.0]
            for r in m:
                v *= 1 + r[key]
                out.append(v)
            return out
        plt.figure(figsize=(9, 4.5), dpi=110)
        for key, lab in (("net_ret", "Top100 (net)"), ("SPY", "SPY"), ("RSP", "RSP")):
            plt.plot(x, [v * 100 for v in cum(key)], label=lab)
        plt.legend(); plt.grid(alpha=.3); plt.title("Top100 screening vs benchmarks (net, price return)")
        plt.savefig(os.path.join(out_dir, "cumulative.png"))
    print("\n".join(l for l in lines if l.startswith("|")))
    print(f"\n产物: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
