# -*- coding: utf-8 -*-
"""review_tiers_asof.py 离线回归（2026-10-19，run4 链式回放配套；无网络/无宽窗口依赖）

覆盖（与 美股选股池管理.md §2.4/§4.2/§6.3 + Task-选股池测试.md 历史回放提示词 + §10.5 对齐）：
  T1  超员按 as-of 绝对市值降级（≥5e9→T2 / 1e9~5e9→T3 / <1e9→出池）；不可得→保留+待人工确认
  T2  四门 as-of 判定（档位/增速/ROE/成交额）+ 流动去向；None→保留+待人工确认（不可得≠不达标）
  T3  剔除（市值<1e9 / 价<$3 / 成交额<5e6）+ 20% 封顶（超额→待人工确认）
  流动 T3→T2（市值>5e10 且 增速>15%）；先升级后剔除
  wide_metrics 宽窗口切片（as-of close + 近3月日均量×close）
"""
import datetime
import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pandas as pd  # noqa: E402
import review_tiers_asof as rv  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def pool_with(subs):
    return {"meta": {"tier1_updated_at": "2023-01-01"}, "sub_sectors": subs}


def fund_with(**kw):
    return {"tickers": kw}


def tiers_of(pool, tier):
    out = {}
    for k, v in pool["sub_sectors"].items():
        out[k] = list(v["tiers"].get(tier, []))
    return out


print("== wide_metrics ==")
with tempfile.TemporaryDirectory() as tmp:
    idx = pd.date_range("2023-10-02", "2026-10-01", freq="D")
    df = pd.DataFrame({"Close": [10.0] * len(idx),
                       "Volume": [100.0] * len(idx)}, index=idx)
    df["Close"] = [10.0 if d <= datetime.datetime(2024, 1, 1) else 20.0
                   for d in idx.to_pydatetime()]
    df.to_pickle(os.path.join(tmp, "AAA_3y_to2026-10-01.pkl"))
    m = rv.wide_metrics(tmp, "2024-01-01", ["AAA", "BBB"])
    check("as-of close = 10.0（≤D 末根）", m["AAA"]["close"] == 10.0, f"got={m['AAA']}")
    check("liq = 日均量×close（90 天窗口）", m["AAA"]["liq"] == 100.0 * 10.0, f"got={m['AAA']['liq']}")
    check("缺 pkl → None（不抛错）", m["BBB"] is None, f"got={m['BBB']}")

print("\n== T1 超员降级 ==")
# 6 只 T1：市值 60B/55B/52B/48B/40B/2B → 前 5 留，2B 那只 <5e9 → T3；再放一只 0.5B → 出池
subs = {"S1": {"name": "半导体", "tiers": {"T1": ["A1", "A2", "A3", "A4", "A5", "A6", "A7"],
                                            "T2": [], "T3": []}}}
fund = fund_with(
    **{f"A{i}": {"market_cap_as_of": v, "revenue_growth": 0.2, "roe": 0.3}
       for i, v in zip(range(1, 8), [60e9, 55e9, 52e9, 48e9, 40e9, 2e9, 0.5e9])})
pool = pool_with(subs)
ch, pending, watch = rv.review(pool, "2024-01-01", fund, {}, {"A1": None, "A7": None},
                               ["T1", "T2", "T3"], 0)
t1 = tiers_of(pool, "T1")["S1"]
check("T1 剩 5 只（Top5）", len(t1) == 5 and "A6" not in t1 and "A7" not in t1, f"t1={t1}")
check("A6(2B) → T3", "A6" in pool["sub_sectors"]["S1"]["tiers"]["T3"],
      f"T3={pool['sub_sectors']['S1']['tiers']['T3']}")
check("A7(0.5B) 出池且有变更记录",
      any(c["ticker"] == "A7" and c["to"] == "OUT" for c in ch), f"ch={[c['ticker'] for c in ch]}")

# 权重兜底（无绝对市值）超员 → 保留 + 待人工确认
subs2 = {"S1": {"name": "x", "tiers": {"T1": ["B1", "B2", "B3", "B4", "B5", "B6"], "T2": [], "T3": []}}}
fund2 = fund_with(
    **{f"B{i}": {"market_cap_as_of_approx": True} for i in range(1, 7)})
const2 = {"results": [{"symbols": [f"B{i}" for i in range(1, 7)],
                       "member_weights": {f"B{i}": {"val_usd": (6 - i) * 1e6, "pct": 10 - i}
                                           for i in range(1, 7)}}]}
pool2 = pool_with(subs2)
ch2, pending2, _ = rv.review(pool2, "2024-01-01", fund2, const2, {}, ["T1"], 0)
check("权重兜底超员不移动（B 全在 T1）",
      len(tiers_of(pool2, "T1")["S1"]) == 6, f"t1={tiers_of(pool2, 'T1')}")
check("权重兜底超员记待人工确认", any(p["ticker"].startswith("B") for p in pending2),
      f"pending={pending2}")

print("\n== T2 四门 ==")
subs3 = {"S1": {"name": "x", "tiers": {"T1": ["T1BIG"], "T2": [
    "C_OUT", "C_T3", "C_HIGH", "C_GROWTH", "C_LIQ_OK", "C_UNGOT", "C_LIQ_NOMC"],
    "T3": []}}}
fund3 = fund_with(
    T1BIG={"market_cap_as_of": 100e9, "revenue_growth": 0.2, "roe": 0.3},
    C_OUT={"market_cap_as_of": 0.8e9, "revenue_growth": 0.3, "roe": 0.3},
    C_T3={"market_cap_as_of": 3e9, "revenue_growth": 0.3, "roe": 0.3},
    C_HIGH={"market_cap_as_of": 80e9, "revenue_growth": 0.3, "roe": 0.3},
    C_GROWTH={"market_cap_as_of": 20e9, "revenue_growth": 0.05, "roe": 0.3},
    C_LIQ_OK={"market_cap_as_of": 20e9, "revenue_growth": 0.3, "roe": 0.3},
    C_UNGOT={"market_cap_as_of": None, "revenue_growth": None, "roe": None},
    C_LIQ_NOMC={"market_cap_as_of": None, "revenue_growth": 0.3, "roe": 0.3},
)
wm3 = {"C_OUT": {"close": 1, "liq": 1e8}, "C_T3": {"close": 1, "liq": 1e8},
       "C_HIGH": {"close": 1, "liq": 1e8}, "C_GROWTH": {"close": 1, "liq": 1e8},
       "C_LIQ_OK": {"close": 1, "liq": 1e8}, "C_UNGOT": None,
       "C_LIQ_NOMC": {"close": 1, "liq": 1e6}}
pool3 = pool_with(subs3)
ch3, pending3, _ = rv.review(pool3, "2024-01-01", fund3, {}, wm3, ["T2"], 0)
t2 = tiers_of(pool3, "T2")["S1"]
t3 = pool3["sub_sectors"]["S1"]["tiers"]["T3"]
check("C_OUT(0.8B<1B) 出池", "C_OUT" not in t2 and any(c["ticker"] == "C_OUT" and c["to"] == "OUT" for c in ch3))
check("C_T3(3B<5B) → T3", "C_T3" in t3 and "C_T3" not in t2)
check("C_HIGH(80B) 进 T1（子板块 Top1）", "C_HIGH" in tiers_of(pool3, "T1")["S1"],
      f"T1={tiers_of(pool3, 'T1')}")
check("C_GROWTH(增速 5%) → T3（20B 超 T3 档？→ 出池）",
      "C_GROWTH" not in t2 and any(c["ticker"] == "C_GROWTH" for c in ch3),
      f"ch={[ (c['ticker'], c['to']) for c in ch3]}")
check("C_LIQ_OK 全过留 T2", "C_LIQ_OK" in t2)
check("C_UNGOT 全不可得 → 留 T2 + 待人工确认",
      "C_UNGOT" in t2 and any(p["ticker"] == "C_UNGOT" for p in pending3))
check("C_LIQ_NOMC（成交额不达标+市值不可得）→ 留 T2 + 待人工确认",
      "C_LIQ_NOMC" in t2 and any(p["ticker"] == "C_LIQ_NOMC" for p in pending3))

print("\n== T3 剔除 + 20% 封顶 + T3→T2 升级 ==")
# T3 共 10 只：1 只升级、6 只触发剔除、3 只干净 → 封顶 2（20% of 10）
subs4 = {"S1": {"name": "x", "tiers": {"T1": [], "T2": ["T1BIG"], "T3": [
    "D_PROMO", "D_M1", "D_M2", "D_P1", "D_P2", "D_L1", "D_L2", "D_OK1", "D_OK2", "D_OK3"]}}}
fund4 = fund_with(
    T1BIG={"market_cap_as_of": 100e9},
    D_PROMO={"market_cap_as_of": 80e9, "revenue_growth": 0.3, "roe": 0.3, "price_as_of": 10.0},
    D_M1={"market_cap_as_of": 0.5e9, "revenue_growth": 0.3, "roe": 0.3, "price_as_of": 10.0},
    D_M2={"market_cap_as_of": 0.6e9, "revenue_growth": 0.3, "roe": 0.3, "price_as_of": 10.0},
    D_P1={"market_cap_as_of": 2e9, "revenue_growth": 0.3, "roe": 0.3, "price_as_of": 1.0},
    D_P2={"market_cap_as_of": 2e9, "revenue_growth": 0.3, "roe": 0.3, "price_as_of": 2.0},
    D_L1={"market_cap_as_of": 2e9, "revenue_growth": 0.3, "roe": 0.3, "price_as_of": 10.0},
    D_L2={"market_cap_as_of": 2e9, "revenue_growth": 0.3, "roe": 0.3, "price_as_of": 10.0},
    D_OK1={"market_cap_as_of": 2e9, "revenue_growth": 0.3, "roe": 0.3, "price_as_of": 10.0},
    D_OK2={"market_cap_as_of": 2e9, "revenue_growth": 0.3, "roe": 0.3, "price_as_of": 10.0},
    D_OK3={"market_cap_as_of": 2e9, "revenue_growth": -0.1, "roe": None, "price_as_of": 10.0},
)
wm4 = {k: {"close": 10.0, "liq": 1e7} for k in ["D_M1", "D_M2", "D_P1", "D_P2", "D_OK1", "D_OK2", "D_OK3"]}
wm4["D_L1"] = {"close": 10.0, "liq": 1e5}
wm4["D_L2"] = {"close": 10.0, "liq": 2e5}
pool4 = pool_with(subs4)
ch4, pending4, watch4 = rv.review(pool4, "2024-01-01", fund4, {}, wm4, ["T3"], 0)
t3l = pool4["sub_sectors"]["S1"]["tiers"]["T3"]
t2l = pool4["sub_sectors"]["S1"]["tiers"]["T2"]
check("D_PROMO(80B+30% 增速) → T2", "D_PROMO" in t2l and "D_PROMO" not in t3l, f"T2={t2l} T3={t3l}")
removed = [c["ticker"] for c in ch4 if c["from"] == "T3" and c["to"] == "OUT"]
check("剔除受 20% 封顶（10 只→最多 2 只；触发 6 只只剔 2 只）", len(removed) == 2, f"removed={removed}")
check("严重度优先：市值<1e9 先于 价格/成交额", set(removed) == {"D_M1", "D_M2"}, f"removed={removed}")
check("超额触发者记待人工确认（非静默）",
      len([p for p in pending4 if p["item"] == "剔除超 20% 封顶"]) == 4, f"pending={pending4}")
check("干净者保留（D_OK1/2/3）", all(k in t3l for k in ("D_OK1", "D_OK2", "D_OK3")))
check("负增速记「关注」不剔除（D_OK3）", any(w["ticker"] == "D_OK3" for w in watch4), f"watch={watch4}")

print("\n== 不越权：未到期层不动（tiers 指定） ==")
subs5 = {"S1": {"name": "x", "tiers": {"T1": ["E1"], "T2": ["C_OUT"], "T3": []}}}
fund5 = fund_with(C_OUT={"market_cap_as_of": 0.8e9, "revenue_growth": 0.3, "roe": 0.3})
pool5 = pool_with(subs5)
ch5, p5, w5 = rv.review(pool5, "2024-01-01", fund5, {}, {}, ["T1"], 0)
check("只审 T1 时 T2 不动（C_OUT 留在 T2）", "C_OUT" in tiers_of(pool5, "T2")["S1"] and not ch5)

print(f"\n结果: {PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
