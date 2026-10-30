#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""选股打分引擎（正式版，2026-10-31 自 replay/screen_top100_v2.py 晋升；打分逻辑逐行一致）

正式入口是 screen_select.py（宇宙=选股池/指数成分/自定义清单，用户设定 top_n）。
本文件也可独立 CLI 跑批（需 workspace 里有 const_{D8}.json / fund_{D8}.json / 价格 pkl）。
宇宙来源标记从 const 文件的 "source" 字段读取（留痕进产物）。

—— 以下为 replay 版原始说明 ——
v2 筛选引擎（2026-10-30；PIT 不变，工程重构 + 打分结构修复）

相对 v1（screen_top100.py，保留原样供复现）的改动：
  A1 每月单趟因子表：变体从同一张表打分选择（v1 每变体全量重算 4 遍）
  A2 序列常驻内存：全部 pkl 一次载入（~20MB），逐月 searchsorted 切片
  A3 复权向量化 + 全窗口复权一次：D 之后的拆股对 ≤D 所有 bar 乘**同一常数**，
     而全部因子都是 ≤D 内的比值/均值比/分位数 → 数值不变（PIT 等价，回归实测断言）
  B1 缺因子按可用权重重归一（v1 缺→0.5 把权重变常数，无基本面票得分范围被压缩）
  B2 共线因子组内平均 rank → 组间加权（mom_6/rs/新高/MA200 高度相关，去名义权重虚增）
  行为开关来自参数文件：b2_groups=true 走组打分；否则走 **v1 扁平语义**（字节等价回归用）

PIT 纪律与 v1 完全一致：所有序列先切片 ≤D 再算因子；参数先验冻结（sha 入报告）；
随机对照固定种子。回归 scripts/test_screen_top100_v2.py：
  复权 vec==loop / v1 等价（选中集与 v1 落盘产物一致）/ 未来数据不变性 / B1 重归一。

用法：
  python replay/screen_top100_v2.py --start 2024-01-01 --end 2026-10-01
  python replay/screen_top100_v2.py ... --variant momentum_only
  python replay/screen_top100_v2.py ... --params replay/screen_params.json --out-prefix top100v1eq_   # 等价回归
产物：replay/{top100v2_<D8>.json | top100v2_<variant>_<D8>.json}
"""
import argparse
import datetime
import hashlib
import json
import os
import random
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
WIDE_DEFAULT = os.path.join(SKILL, "screening", "wide_price")
PERIOD_DEFAULT = "5y"
WIDE_END_DEFAULT = "2026-10-01"


def load_params(path):
    raw = open(path, "rb").read()
    return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()[:16]


def months_between(start, end):
    out, y, m = [], start.year, start.month
    while datetime.date(y, m, 1) <= end:
        out.append(datetime.date(y, m, 1))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def adjust_vec(close, vol, cands, tol):
    """向量化干净比率后复权（与 v1 adjust_splits 循环版等价，回归断言）。
    事件 i 的比率 x 乘到 i 之前所有 bar：f[t] = Π_{i>t} x_i（反向 cumprod）。"""
    c = np.asarray(close, dtype=float)
    v = np.asarray(vol, dtype=float)
    n = len(c)
    e = np.ones(n)
    if n >= 2:
        prev = c[:-1]
        r = np.where(prev != 0, c[1:] / np.where(prev != 0, prev, 1.0), np.nan)
        matched = np.ones(n - 1)
        done = np.zeros(n - 1, dtype=bool)
        for k in cands:
            for x in (k, 1.0 / k):
                m = (~done) & (np.abs(r - x) <= tol * x)
                matched[m] = x
                done[m] = True
        e[1:] = matched
    g = np.cumprod(e[::-1])[::-1]
    f = np.append(g[1:], 1.0)
    return c * f, v / f, int((e != 1.0).sum())


class SeriesStore:
    """一次载入全部 pkl 并全窗口复权（PIT 等价见 docstring A3）。"""

    def __init__(self, wide_dir, period, wide_end, cands, tol):
        self.wide_dir, self.period, self.wide_end = wide_dir, period, wide_end
        self.cands, self.tol = cands, tol
        self.cache = {}

    def get(self, tk):
        if tk in self.cache:
            return self.cache[tk]
        p = os.path.join(self.wide_dir, f"{tk}_{self.period}_to{self.wide_end}.pkl")
        out = None
        if os.path.exists(p):
            try:
                df = pd.read_pickle(p)
                if df is not None and not df.empty and "Close" in df.columns:
                    idx = pd.to_datetime(df.index)
                    order = np.argsort(idx, kind="stable")
                    idx = idx[order]
                    raw_c = df["Close"].to_numpy(dtype=float)[order]
                    raw_v = (df["Volume"].to_numpy(dtype=float)[order]
                             if "Volume" in df.columns else np.ones(len(raw_c)))
                    adj_c, adj_v, ns = adjust_vec(raw_c, raw_v, self.cands, self.tol)
                    out = (idx, adj_c, adj_v, raw_c, ns)
            except Exception:
                out = None
        self.cache[tk] = out
        return out


def mom_arr(a, cur_i, n):
    prev = a[cur_i - n] if cur_i - n >= 0 else 0.0
    return (a[cur_i] / prev - 1.0) if prev else None


def compute_factors2(adj_c, adj_v, raw_c, pos, fund_e, sm6, p, skip, lb):
    flt = p["filters"]
    if pos < flt["min_bars"]:
        return None, f"历史 bar 不足（{pos}<{flt['min_bars']}，长动量不可算）"
    if raw_c[pos - 1] < flt["min_price"]:
        return None, f"价格 ${raw_c[pos - 1]:.2f} <${flt['min_price']}"
    lbars = flt["min_liq_bars"]
    liq = float(adj_v[pos - lbars:pos].mean() * adj_c[pos - 1])
    if liq < flt["min_liq"]:
        return None, f"近 {lbars} bar 日均成交额 {liq/1e6:.1f}M <{flt['min_liq']/1e6:.0f}M"
    a, v = adj_c[:pos], adj_v[:pos]
    f = {}
    f["ma200_ratio"] = float(a[pos - 1] / a[pos - 200:pos].mean() - 1.0)
    f["ma50_gt_ma200"] = 1.0 if a[pos - 50:pos].mean() > a[pos - 200:pos].mean() else 0.0
    f["dist_52w_high"] = float(a[pos - 1] / a[pos - 252:pos].max() - 1.0)
    cur = pos - 1 - skip
    f["mom_12_1"] = mom_arr(a, cur, lb)
    f["mom_6"] = mom_arr(a, pos - 1, 126)
    f["rs_spy_6"] = (f["mom_6"] - sm6) if (f["mom_6"] is not None and sm6 is not None) else None
    v12 = float(v[pos - 252:pos].mean())
    f["vol_ratio_3_12"] = float(v[pos - 63:pos].mean() / v12) if v12 else None
    r = np.diff(a[pos - 127:pos]) / a[pos - 127:pos - 1]
    f["vol_6m_low"] = float(np.std(r, ddof=1)) if len(r) > 30 else None
    f["revenue_growth"] = fund_e.get("revenue_growth")
    f["roe"] = fund_e.get("roe")
    return f, None


def score_flat(rows, w):
    """v1 扁平语义（字节等价用）：缺因子 rank=0.5，vol_6m_low 取反。"""
    names = list(w.keys())
    cols = {k: [r[1].get(k) for r in rows] for k in names}
    ranks = {}
    for k in names:
        s = pd.Series(cols[k], dtype="float64")
        rk = s.rank(pct=True).fillna(0.5)
        if k == "vol_6m_low":
            rk = 1.0 - rk.where(s.notna(), other=0.5)
        ranks[k] = rk
    total = None
    for k in names:
        contrib = ranks[k] * w[k]
        total = contrib if total is None else total + contrib
    return {rows[i][0]: (float(total.iloc[i]),
                         {k: (None if cols[k][i] is None else round(float(cols[k][i]), 4))
                          for k in names})
            for i in range(len(rows))}


def score_grouped(rows, groups):
    """B2 组内平均 rank + B1 可用权重重归一。组全缺 → 该组退出。"""
    items = sorted({it for g in groups.values() for it in g["items"]})
    cols = {k: [r[1].get(k) for r in rows] for k in items}
    ranks = {}
    for k in items:
        s = pd.Series(cols[k], dtype="float64")
        rk = s.rank(pct=True)
        if k == "vol_6m_low":
            rk = 1.0 - rk
        ranks[k] = rk.to_numpy()
    out = {}
    for i, (tk, fac) in enumerate(rows):
        num = den = 0.0
        for gname, g in groups.items():
            vals = [ranks[it][i] for it in g["items"] if not np.isnan(ranks[it][i])]
            if vals:
                num += g["w"] * float(np.mean(vals))
                den += g["w"]
        score = num / den if den else 0.5
        out[tk] = (score, {k: (None if cols[k][i] is None else round(float(cols[k][i]), 4))
                           for k in items})
    return out


def spy_m6(store, as_of):
    s = store.get("SPY")
    if s is None:
        return None
    idx, adj_c, _, _, _ = s
    pos = int(np.searchsorted(idx, np.datetime64(as_of), side="right"))
    return mom_arr(adj_c, pos - 1, 126) if pos >= 127 else None


def apply_buffer(ranked, prev_sel, top_n, buffer_rank):
    """buffer 带（2026-10-30 补实现；params 早有 buffer_rank 但 v1/v2 代码从未用上）：
    上月持仓若本月排名仍 < buffer_rank 则优先保留，再按分数补足 top_n。
    PIT：prev_sel 是上一调仓日的名单（D 时点已知），排名只用 ≤D 数据。"""
    pos = {tk: i for i, tk in enumerate(ranked)}
    keep = [tk for tk in prev_sel if pos.get(tk, 10 ** 9) < buffer_rank]
    keep = keep[:top_n]
    ks = set(keep)
    fill = [tk for tk in ranked if tk not in ks]
    return (keep + fill)[:top_n]


def screen_month(D, store, p, sha, variant, seed, mom_lb_override=None, top_n_override=None,
                 data_dir=None, prev_sel=None):
    data_dir = data_dir or HERE
    d8 = D.strftime("%Y%m%d")
    const = json.load(open(os.path.join(data_dir, f"const_{d8}.json"), encoding="utf-8"))
    universe = set()
    for r in const.get("results", []):
        universe.update(r.get("symbols") or [])
    fund = json.load(open(os.path.join(data_dir, f"fund_{d8}.json"), encoding="utf-8")).get("tickers", {})
    p = dict(p)
    if top_n_override:
        p["top_n"] = top_n_override
    skip = p.get("momentum", {}).get("skip_bars", 21)
    lb = mom_lb_override or p.get("momentum", {}).get("lookback_bars", 231)

    sm6 = spy_m6(store, D.isoformat())
    rows, filtered, n_splits = [], [], 0
    for tk in sorted(universe):
        s = store.get(tk)
        if s is None:
            filtered.append({"ticker": tk, "reason": "无价格数据（数据源已删除/退市，诚实缺失）"})
            continue
        idx, adj_c, adj_v, raw_c, ns = s
        n_splits += ns
        pos = int(np.searchsorted(idx, np.datetime64(D.isoformat()), side="right"))
        if pos == 0:
            filtered.append({"ticker": tk, "reason": "无 ≤as-of 的 bar（当时尚未上市）"})
            continue
        f, reason = compute_factors2(adj_c, adj_v, raw_c, pos, fund.get(tk) or {}, sm6, p, skip, lb)
        if reason is None:
            g = p.get("gates") or {}   # v4 加严门：因子值 ≤ 阈值直接剔除（留痕；params 驱动，默认空=v2/v3 不变）
            for k, lo in g.items():
                v = f.get(k)
                if v is not None and v <= lo:
                    reason = f"gate: {k}={v:.3f} ≤ {lo}"
                    break
        if reason:
            filtered.append({"ticker": tk, "reason": reason})
            continue
        if variant == "no_fund":
            f["revenue_growth"] = None
            f["roe"] = None
        rows.append((tk, f))

    grouped = bool(p.get("b2_groups"))
    if grouped:
        groups = {k: dict(v) for k, v in p["groups"].items()}
        if variant == "momentum_only":
            groups.pop("fundamental", None)
        scored = score_grouped(rows, groups)
    else:
        w = dict(p["factors"])
        if variant == "momentum_only":
            w = {k: (0.0 if k in ("revenue_growth", "roe") else v) for k, v in w.items()}
            s = sum(w.values())
            w = {k: v / s for k, v in w.items()}
        scored = score_flat(rows, w)

    order = sorted(scored.items(), key=lambda kv: (-kv[1][0], kv[0]))
    if variant == "random100":
        rng = random.Random(seed + int(d8))
        sel = sorted(rng.sample([tk for tk, _ in rows], min(p["top_n"], len(rows))))
        order = [(tk, (None, scored[tk][1])) for tk in sel]
    else:
        mult = p.get("buffer_mult")
        if mult and prev_sel:
            ranked = [tk for tk, _ in order]
            sel = apply_buffer(ranked, prev_sel, p["top_n"], int(round(p["top_n"] * mult)))
            order = sorted([(tk, scored[tk]) for tk in sel], key=lambda kv: (-kv[1][0], kv[0]))
        else:
            order = order[:p["top_n"]]

    return {
        "as_of": D.isoformat(), "variant": variant,
        "engine": "v2", "params_sha256_16": sha,
        "universe_source": const.get("source") or f"const_{d8}",
        "universe_n": len(universe), "scored_n": len(rows), "filtered_n": len(filtered),
        "splits_adjusted": n_splits,
        "spy_mom_6m": round(sm6, 4) if sm6 is not None else None,
        "selected": [{"ticker": tk, "score": (round(sc, 4) if sc is not None else None),
                      "factors": fac} for tk, (sc, fac) in order],
        "filtered_out": filtered,
    }


def main():
    ap = argparse.ArgumentParser(description="v2 筛选引擎（PIT）")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--params", default=os.path.join(HERE, "screen_params_v3.json"))
    ap.add_argument("--period", default=PERIOD_DEFAULT)
    ap.add_argument("--wide-end", default=WIDE_END_DEFAULT)
    ap.add_argument("--wide", default=WIDE_DEFAULT)
    ap.add_argument("--variant", default="full",
                    choices=["full", "momentum_only", "no_fund", "random100"])
    ap.add_argument("--out-prefix", default="top100v2_")
    ap.add_argument("--mom-lookback", type=int, default=None, help="敏感性：覆盖 12-1M 回看 bar 数")
    ap.add_argument("--top-n", type=int, default=None, help="敏感性：覆盖 top_n")
    ap.add_argument("--seed", type=int, default=20240101)
    ap.add_argument("--out-dir", default=HERE)
    a = ap.parse_args()

    p, sha = load_params(a.params)
    months = months_between(datetime.date.fromisoformat(a.start), datetime.date.fromisoformat(a.end))
    store = SeriesStore(a.wide, a.period, a.wide_end,
                        p["split"]["candidate_ratios"], p["split"]["tol"])
    prev_sel = None
    for D in months:
        rep = screen_month(D, store, p, sha, a.variant, a.seed, a.mom_lookback, a.top_n,
                           prev_sel=sorted(prev_sel) if prev_sel else None)
        cur = {x["ticker"] for x in rep["selected"]}
        if prev_sel is not None:
            rep["vs_prev"] = {"added": sorted(cur - prev_sel), "removed": sorted(prev_sel - cur),
                              "turnover_pct": round(len(cur - prev_sel) / max(len(cur), 1) * 100, 1)}
        prev_sel = cur
        base = a.out_prefix if a.variant == "full" else a.out_prefix + a.variant + "_"
        out = os.path.join(a.out_dir, f"{base}{D.strftime('%Y%m%d')}.json")
        json.dump(rep, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"{D}: universe={rep['universe_n']} 可评分={rep['scored_n']} "
              f"剔除={rep['filtered_n']} 选中={len(rep['selected'])} "
              f"换手={rep.get('vs_prev', {}).get('turnover_pct', '-')}% → {os.path.basename(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
