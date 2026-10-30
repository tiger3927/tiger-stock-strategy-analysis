# -*- coding: utf-8 -*-
"""screen_top100_v2.py 回归（2026-10-30）

断言：
  1) adjust_vec == v1 adjust_splits（合成：多拆股/并股/暴跌混合；真实含拆股票）
  2) **v1 字节等价**：v2 引擎 + v1 参数（扁平语义）在真实数据上，选中集与 v1 落盘
     产物（top100_20240101 / top100_20261001）逐位一致（分数容差 1e-3，浮点次序差容忍）
  3) PIT：合成 wide 追加 D 后未来数据 → D 日结果逐字节不变（v2 全窗口复权不泄漏）
  4) B1：缺基本面组 → 按可用组重归一（与 v1 常数 0.5 语义可区分）

运行：python scripts/test_screen_top100_v2.py
"""
import datetime
import io
import json
import os
import sys

import numpy as np
import pandas as pd

REPLAY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "replay")
sys.path.insert(0, REPLAY)
import screen_top100 as v1  # noqa: E402
import screen_top100_v2 as v2  # noqa: E402

CR = [1.2, 1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10, 15, 20, 25, 30]
TOL = 0.03
PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


print("== 1) adjust_vec == v1 loop ==")
rng = np.random.default_rng(7)
c = list(100 * np.cumprod(1 + rng.normal(0, 0.01, 500)))
for at, x in [(100, 0.5), (200, 2.0), (300, 0.1), (400, 0.7)]:  # 拆/并/10:1/-30%暴跌
    c[at:] = [v * x for v in c[at:]]
v = [1e6] * 500
ac1, av1, n1 = v1.adjust_splits(pd.Series(c), pd.Series(v), CR, TOL)
ac2, av2, n2 = v2.adjust_vec(c, v, CR, TOL)
check("事件数一致（3：0.5/2.0/0.1，-30% 不判）", n1 == n2 == 3, f"n1={n1} n2={n2}")
check("复权价逐点一致（rtol 1e-9）", np.allclose(ac1.to_numpy(), ac2, rtol=1e-9, atol=1e-9))
check("复权量逐点一致", np.allclose(av1.to_numpy(), av2, rtol=1e-9, atol=1e-6))
# 真实含拆股票（NVDA 2024 10:1）
p_nv = os.path.join(REPLAY, "wide_price", "NVDA_5y_to2026-10-01.pkl")
if os.path.exists(p_nv):
    df = pd.read_pickle(p_nv)
    a1, _, m1 = v1.adjust_splits(df["Close"], df.get("Volume"), CR, TOL)
    a2, _, m2 = v2.adjust_vec(df["Close"].to_numpy(), df["Volume"].to_numpy(), CR, TOL)
    check("NVDA 真实序列：事件数一致", m1 == m2 and m1 >= 1, f"{m1}/{m2}")
    check("NVDA 复权价一致", np.allclose(a1.to_numpy(), a2, rtol=1e-9, atol=1e-9))

print("\n== 2) v1 字节等价（真实数据，v2 引擎 + v1 参数）==")
p1, sha1 = v2.load_params(os.path.join(REPLAY, "screen_params.json"))
check("v1 参数无 b2_groups 开关 → 扁平语义", not p1.get("b2_groups"))
store = v2.SeriesStore(os.path.join(REPLAY, "wide_price"), "5y", "2026-10-01", CR, TOL)
for ds, saved in [("2024-01-01", "top100_20240101.json"), ("2026-10-01", "top100_20261001.json")]:
    rep = v2.screen_month(datetime.date.fromisoformat(ds), store, p1, sha1, "full", 20240101)
    ref = json.load(io.open(os.path.join(REPLAY, saved), encoding="utf-8"))
    got = [x["ticker"] for x in rep["selected"]]
    exp = [x["ticker"] for x in ref["selected"]]
    check(f"{ds} 选中集逐位一致（{len(exp)} 只）", got == exp,
          f"diff={sorted(set(got) ^ set(exp))[:6]}")
    sm = {x["ticker"]: x["score"] for x in rep["selected"]}
    dm = max(abs(sm[t] - x["score"]) for t, x in
             zip(exp, ref["selected"]) if x["score"] is not None and t in sm)
    check(f"{ds} 分数差 <1e-3（浮点次序噪声）", dm < 1e-3, f"maxdiff={dm:.6f}")

print("\n== 3) PIT：全窗口复权 + 未来数据不变性（合成）==")
import tempfile
with tempfile.TemporaryDirectory() as tmp:
    def mk(tk, days, px_fn, vol=1e6):
        idx = pd.bdate_range("2021-01-04", periods=days)
        pd.DataFrame({"Close": [px_fn(i) for i in range(days)], "Volume": [vol] * days},
                     index=idx).to_pickle(os.path.join(tmp, f"{tk}_5y_to2026-10-01.pkl"))
    mk("GOOD", 700, lambda i: 50 * 1.001 ** i)
    mk("SPLIT", 700, lambda i: 50 * 1.001 ** i * (0.5 if i >= 400 else 1.0))
    mk("SPY", 900, lambda i: 400 * 1.0008 ** i)
    uni = ["GOOD", "SPLIT", "GHOST"]
    json.dump({"results": [{"symbols": uni}]}, io.open(os.path.join(tmp, "const_20240101.json"), "w", encoding="utf-8"))
    json.dump({"tickers": {t: {} for t in uni}}, io.open(os.path.join(tmp, "fund_20240101.json"), "w", encoding="utf-8"))
    p2, sha2 = v2.load_params(os.path.join(REPLAY, "screen_params_v2.json"))
    st_a = v2.SeriesStore(tmp, "5y", "2026-10-01", CR, TOL)
    repA = v2.screen_month(datetime.date(2024, 1, 1), st_a, p2, sha2, "full", 20240101, data_dir=tmp)
    # 追加荒谬未来（暴涨+天量），重建 store 重跑
    for fn in ("GOOD_5y_to2026-10-01.pkl", "SPLIT_5y_to2026-10-01.pkl"):
        df = pd.read_pickle(os.path.join(tmp, fn))
        fut_idx = pd.bdate_range("2024-01-02", periods=250)
        fut = pd.DataFrame({"Close": [df["Close"].iloc[-1] * 1.03 ** i for i in range(250)],
                            "Volume": [1e10] * 250}, index=fut_idx)
        pd.concat([df, fut]).to_pickle(os.path.join(tmp, fn))
    st_b = v2.SeriesStore(tmp, "5y", "2026-10-01", CR, TOL)
    repB = v2.screen_month(datetime.date(2024, 1, 1), st_b, p2, sha2, "full", 20240101, data_dir=tmp)
    check("PIT：追加未来数据 → 结果逐字节不变",
          json.dumps(repA["selected"], sort_keys=True) == json.dumps(repB["selected"], sort_keys=True))
    check("SPLIT 复权后动量正常（无假 -50%）",
          any(x["ticker"] == "SPLIT" and x["factors"]["mom_12_1"] and x["factors"]["mom_12_1"] > 0.1
              for x in repA["selected"]))

print("\n== 4) B1 重归一语义 ==")
rows = [("A", {"ma200_ratio": 1.0, "ma50_gt_ma200": 1.0, "dist_52w_high": 0.0,
               "mom_12_1": 0.5, "mom_6": 0.3, "rs_spy_6": 0.2, "vol_ratio_3_12": 1.5,
               "revenue_growth": 0.4, "roe": 0.3, "vol_6m_low": 0.02}),
        ("B", {"ma200_ratio": 0.2, "ma50_gt_ma200": 0.0, "dist_52w_high": -0.2,
               "mom_12_1": -0.1, "mom_6": -0.1, "rs_spy_6": -0.1, "vol_ratio_3_12": 0.8,
               "revenue_growth": None, "roe": None, "vol_6m_low": 0.03})]
g = json.load(io.open(os.path.join(REPLAY, "screen_params_v2.json"), encoding="utf-8"))["groups"]
sc = v2.score_grouped(rows, g)
# B 缺整个 fundamental 组：得分应为其余 4 组（w 和=0.75）的加权平均/0.75，而非被 0.5 常数稀释
num = den = 0.0
ranks = {}
for it in sorted({i for gg in g.values() for i in gg["items"]}):
    s = pd.Series([r[1].get(it) for r in rows], dtype="float64").rank(pct=True)
    if it == "vol_6m_low":
        s = 1.0 - s
    ranks[it] = s
for i, (tk, _) in enumerate(rows):
    num = den = 0.0
    for gname, gg in g.items():
        vals = [ranks[it][i] for it in gg["items"] if not np.isnan(ranks[it][i])]
        if vals:
            num += gg["w"] * float(np.mean(vals))
            den += gg["w"]
    check(f"{tk} 得分=可用组加权/可用权重（B1）", abs(sc[tk][0] - num / den) < 1e-12,
          f"{sc[tk][0]} vs {num/den}")
check("B 缺基本面组仍可得满分区间（不被常数稀释）", sc["B"][0] > 0.0 and sc["B"][0] < 1.0)

print("\n== 5) buffer 带（v3 补实现）==")
ranked = ["A", "B", "C", "D", "E", "F", "G", "H"]
sel = v2.apply_buffer(ranked, ["E", "F", "G", "H", "X"], 5, 6)
check("上月持仓排名<6 保留（E,F），X 出局，按分补足 A,B,C", sel == ["E", "F", "A", "B", "C"], str(sel))
sel2 = v2.apply_buffer(ranked, ["A", "B", "C", "D", "E"], 5, 6)
check("上月全部在带内 → 名单不变（换手=0）", sel2 == ["A", "B", "C", "D", "E"], str(sel2))
sel3 = v2.apply_buffer(ranked, ["G", "H"], 5, 6)
check("上月全部出带 → 纯按分选", sel3 == ["A", "B", "C", "D", "E"], str(sel3))
with tempfile.TemporaryDirectory() as tmp:
    def mk2(tk, days, px_fn, vol=1e6):
        idx = pd.bdate_range("2021-01-04", periods=days)
        pd.DataFrame({"Close": [px_fn(i) for i in range(days)], "Volume": [vol] * days},
                     index=idx).to_pickle(os.path.join(tmp, f"{tk}_5y_to2026-10-01.pkl"))
    for tk, g in [("U1", 1.0012), ("U2", 1.0010), ("U3", 1.0008), ("U4", 1.0006),
                  ("U5", 1.0004), ("U6", 1.0002), ("SPY", 1.0008)]:
        mk2(tk, 700, (lambda gg: (lambda i: 50 * gg ** i))(g))
    json.dump({"results": [{"symbols": ["U1", "U2", "U3", "U4", "U5", "U6"]}]},
              io.open(os.path.join(tmp, "const_20240101.json"), "w", encoding="utf-8"))
    json.dump({"tickers": {}}, io.open(os.path.join(tmp, "fund_20240101.json"), "w", encoding="utf-8"))
    p3, sha3 = v2.load_params(os.path.join(REPLAY, "screen_params_v2.json"))
    p3 = dict(p3, buffer_mult=1.3, top_n=4)
    st_a = v2.SeriesStore(tmp, "5y", "2026-10-01", CR, TOL)
    repA = v2.screen_month(datetime.date(2024, 1, 1), st_a, p3, sha3, "full", 20240101,
                           data_dir=tmp, prev_sel=["U6", "U5"])
    for fn in ("U1_5y_to2026-10-01.pkl", "U6_5y_to2026-10-01.pkl"):
        df = pd.read_pickle(os.path.join(tmp, fn))
        fut = pd.DataFrame({"Close": [df["Close"].iloc[-1] * 1.03 ** i for i in range(250)],
                            "Volume": [1e10] * 250},
                           index=pd.bdate_range("2024-01-02", periods=250))
        pd.concat([df, fut]).to_pickle(os.path.join(tmp, fn))
    st_b = v2.SeriesStore(tmp, "5y", "2026-10-01", CR, TOL)
    repB = v2.screen_month(datetime.date(2024, 1, 1), st_b, p3, sha3, "full", 20240101,
                           data_dir=tmp, prev_sel=["U6", "U5"])
    check("buffer 开启下 PIT：追加未来数据 → 结果逐字节不变",
          json.dumps(repA["selected"], sort_keys=True) == json.dumps(repB["selected"], sort_keys=True))
    got = [x["ticker"] for x in repA["selected"]]
    # 6 只排名 U1..U6=0..5；buffer_rank=round(4×1.3)=5 → 带内旧持仓=U5（U6 出带）
    check("buffer 生效：带内旧持仓 U5 保留、U6 出局、补足 U1..U3（输出按分数降序）",
          got == ["U1", "U2", "U3", "U5"], str(got))

print("\n== 6) 正式引擎 scripts/screen_engine.py == replay 引擎（晋升零漂移）==")
HERE_S = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE_S)
import screen_engine as eng  # noqa: E402

sha_pairs = []
for fn in ("screen_params_v3.json", "screen_params_v4.json"):
    a = open(os.path.join(HERE_S, fn), "rb").read()
    b = open(os.path.join(REPLAY, fn), "rb").read()
    sha_pairs.append((fn, a == b))
check("参数集 scripts/ == replay/ 逐字节", all(ok for _, ok in sha_pairs),
      str([f for f, ok in sha_pairs if not ok]))

with tempfile.TemporaryDirectory() as tmp:
    idx = pd.bdate_range("2021-01-04", periods=700)
    for tk, g in [("U1", 1.0012), ("U2", 1.0010), ("U3", 1.0008), ("U4", 1.0006),
                  ("U5", 1.0004), ("U6", 1.0002), ("SPY", 1.0008)]:
        pd.DataFrame({"Close": [50 * g ** i for i in range(700)], "Volume": [1e6] * 700},
                     index=idx).to_pickle(os.path.join(tmp, f"{tk}_5y_to2026-10-01.pkl"))
    json.dump({"results": [{"symbols": ["U1", "U2", "U3", "U4", "U5", "U6"]}]},
              io.open(os.path.join(tmp, "const_20240101.json"), "w", encoding="utf-8"))
    json.dump({"tickers": {}}, io.open(os.path.join(tmp, "fund_20240101.json"), "w", encoding="utf-8"))
    p_e, sha_e = eng.load_params(os.path.join(HERE_S, "screen_params_v3.json"))
    p_r, sha_r = v2.load_params(os.path.join(REPLAY, "screen_params_v3.json"))
    check("load_params sha 一致", sha_e == sha_r)
    st_e = eng.SeriesStore(tmp, "5y", "2026-10-01", CR, TOL)
    st_r = v2.SeriesStore(tmp, "5y", "2026-10-01", CR, TOL)
    kw = dict(top_n_override=4, data_dir=tmp, prev_sel=["U6", "U5"])
    rep_e = eng.screen_month(datetime.date(2024, 1, 1), st_e, p_e, sha_e, "full", 20240101, **kw)
    rep_r = v2.screen_month(datetime.date(2024, 1, 1), st_r, p_r, sha_r, "full", 20240101, **kw)
    check("screen_month selected+filtered 逐字节等价",
          json.dumps(rep_e["selected"], sort_keys=True) == json.dumps(rep_r["selected"], sort_keys=True)
          and json.dumps(rep_e["filtered_out"], sort_keys=True) == json.dumps(rep_r["filtered_out"], sort_keys=True))
    check("adjust_vec 两实现等价（合成含拆股）",
          np.allclose(eng.adjust_vec(np.array(c, dtype=float), np.ones(len(c)), CR, TOL)[0],
                      v2.adjust_vec(np.array(c, dtype=float), np.ones(len(c)), CR, TOL)[0]))

print(f"\n结果: {PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
