#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""as-of 前视能力离线回归（2026-10-19，零网络、全合成 fixture）

覆盖：
  fetch_fundamentals_asof.py  PIT 选期 / 营收增速 / ROE / 市值 / 池 ticker 提取
  fetch_index_constituents.py NPORT-P 持仓解析（valUSD/pctVal）与 map_symbols 权重

运行：python scripts/test_fundamentals_asof.py
（与 scripts/test_get_market_data.py 不同：本脚本纯离线，可反复重跑）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fetch_fundamentals_asof as ff  # noqa: E402
import fetch_index_constituents as fic  # noqa: E402

_PASS = 0
_FAIL = 0


def check(name, cond, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}  {detail}")


# ==================== fetch_fundamentals_asof：PIT 选期与指标 ====================

AS_OF = "2024-01-01"

REV = [
    # FY2023（2023-02-24 申报，PIT 可用）+ FY2022（上年）
    {"start": "2022-10-01", "end": "2023-09-30", "val": 1100.0, "filed": "2023-02-24", "form": "10-K"},
    {"start": "2021-10-01", "end": "2022-09-30", "val": 1000.0, "filed": "2022-02-22", "form": "10-K"},
    # FY2024：end 超 as-of → 必须被门控排除
    {"start": "2023-10-01", "end": "2024-09-28", "val": 2000.0, "filed": "2024-02-23", "form": "10-K"},
]

print("== PIT 选期 / 营收增速 ==")
g, cur, prior, notes = ff.compute_revenue_growth(REV, AS_OF)
check("FY 同比 = +10%", g is not None and abs(g - 0.10) < 1e-9, f"g={g}")
check("cur 取 as-of 前最近财年 end=2023-09-30", cur and cur["end"] == "2023-09-30", f"cur={cur and cur['end']}")

# XBRL 偶发 start/end 带时间后缀（2026-10-19 全候选集实测触发的崩溃）
print("== 日期容错（时间后缀/异常值）==")
d = ff._days("2023-11-01T00:00:00-05:00", "2024-10-31")
check("_days 容忍时间后缀（365 天，含 2024 闰年）", d == 365, f"d={d}")
check("_days 异常值 → None（不抛错）", ff._days("garbage", "2024-01-01") is None)
REV_TZ = [
    {"start": "2022-10-01", "end": "2023-09-30", "val": 1100.0, "filed": "2023-11-01"},
    {"start": "2021-10-01T00:00:00-05:00", "end": "2022-09-30", "val": 1000.0, "filed": "2022-11-01"},
    {"start": "2021-01-01", "end": "2023-09-30", "val": 999.0, "filed": "2023-11-01"},  # 非财年时长
]
g2, cur2, prior2, _ = ff.compute_revenue_growth(REV_TZ, AS_OF)
check("带时间后缀的上年 FY 仍被选中（prior=2022-09-30）",
      prior2 and prior2["end"] == "2022-09-30" and abs(g2 - 0.10) < 1e-9,
      f"g={g2} prior={prior2 and prior2['end']}")

REV_LATE = [
    {"start": "2022-10-01", "end": "2023-09-30", "val": 1100.0, "filed": "2024-02-24", "form": "10-K"},  # 晚申报
    {"start": "2021-10-01", "end": "2022-09-30", "val": 1000.0, "filed": "2022-02-22", "form": "10-K"},
]
g2, cur2, prior2, _ = ff.compute_revenue_growth(REV_LATE, AS_OF)
check("filed > as_of 的 FY 被排除（退回更早可申报期）",
      (g2 is None and cur2 is None) or (cur2 and cur2["end"] != "2023-09-30"),
      f"g2={g2} cur2={cur2 and cur2.get('end')}")

REV_RESTATED = [
    {"start": "2022-10-01", "end": "2023-09-30", "val": 1100.0, "filed": "2023-02-24", "form": "10-K"},
    {"start": "2022-10-01", "end": "2023-09-30", "val": 999.0, "filed": "2025-02-24", "form": "10-K/A"},  # 重述
    {"start": "2021-10-01", "end": "2022-09-30", "val": 1000.0, "filed": "2022-02-22", "form": "10-K"},
]
g3, cur3, _, _ = ff.compute_revenue_growth(REV_RESTATED, AS_OF)
check("同 end 重述取最小 filed（1100 而非 999）", cur3 and cur3["val"] == 1100.0, f"cur3={cur3}")

REV_9M = [
    {"start": "2023-01-01", "end": "2023-09-30", "val": 900.0, "filed": "2023-11-10", "form": "10-Q"},
    {"start": "2022-01-01", "end": "2022-09-30", "val": 750.0, "filed": "2022-11-10", "form": "10-Q"},
]
g4, cur4, prior4, notes4 = ff.compute_revenue_growth(REV_9M, AS_OF)
check("无财年退回同长度累计期（9M 对年 +20%）",
      g4 is not None and abs(g4 - 0.20) < 1e-9 and prior4, f"g4={g4} notes={notes4}")

REV_NOPIOR = [
    {"start": "2022-10-01", "end": "2023-09-30", "val": 500.0, "filed": "2023-02-24", "form": "10-K"},
]
g5, _, _, notes5 = ff.compute_revenue_growth(REV_NOPIOR, AS_OF)
check("prior 缺失 → None + 留痕", g5 is None and any("prior" in n for n in notes5), f"notes={notes5}")

print("== ROE / 市值 ==")
NI = [{"start": "2022-10-01", "end": "2023-09-30", "val": 200.0, "filed": "2023-02-24", "form": "10-K"}]
EQ = [
    {"end": "2022-09-30", "val": 1000.0, "filed": "2023-02-24"},
    {"end": "2023-09-30", "val": 1400.0, "filed": "2023-02-24"},
]
r, ni_e, pts, _ = ff.compute_roe(NI, EQ, AS_OF)
check("ROE = NI / 平均权益(2点) = 200/1200", r is not None and abs(r - 1 / 6.0) < 1e-9, f"r={r} pts={len(pts)}")

EQ_NEG = [{"end": "2023-09-30", "val": -500.0, "filed": "2023-02-24"}]
r2, _, _, notes_r2 = ff.compute_roe(NI, EQ_NEG, AS_OF)
check("权益为负 → None + 留痕", r2 is None and notes_r2, f"notes={notes_r2}")

mcap, note_m = ff.compute_market_cap({"val": 1e9}, 50.0)
check("市值 = 股本×价 = 5e10", mcap == 5e10, f"mcap={mcap}")
mcap2, note_m2 = ff.compute_market_cap(None, 50.0)
check("股本缺失 → None + 原因", mcap2 is None and note_m2, f"note={note_m2}")

print("== 池 ticker 提取 ==")
POOL = {"sub_sectors": {"15102010": {"tiers": {"T1": ["A"], "T2": ["B"], "T3": ["C", "A"]}}}}
check("pool_tickers 去重排序 = [A,B,C]", ff.pool_tickers(POOL) == ["A", "B", "C"],
      f"got={ff.pool_tickers(POOL)}")

print("== 宽窗口本地切片（--price-from）==")
import tempfile  # noqa: E402
import pandas as pd  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    idx = pd.date_range("2024-01-01", "2024-01-10", freq="D")
    df = pd.DataFrame({"Close": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0,
                                 16.0, 17.0, 18.0, 19.0]}, index=idx)
    df.to_pickle(os.path.join(tmp, "AAA_3y_to2026-10-01.pkl"))
    p = ff.fetch_prices_wide(["AAA", "BBB"], "2024-01-05", tmp, "2026-10-01")
    check("取 ≤as_of 末根 Close（01-05 → 14.0）", p.get("AAA") == 14.0, f"got={p.get('AAA')}")
    check("缺 pkl 记失败不中断", any(x.get("ticker") == "BBB" for x in p.get("失败", [])),
          f"失败={p.get('失败')}")
    # 全窗口在 as-of 之后 → 无 ≤as_of bar
    df_late = pd.DataFrame({"Close": [1.0]},
                           index=pd.date_range("2025-01-01", "2025-01-02"))
    df_late.to_pickle(os.path.join(tmp, "CCC_3y_to2026-10-01.pkl"))
    p2 = ff.fetch_prices_wide(["CCC"], "2024-01-01", tmp, "2026-10-01")
    check("窗口无 ≤as_of bar → 记失败", "CCC" not in p2 and p2.get("失败"), f"p2={p2}")

# ==================== fetch_index_constituents：解析与权重 ====================

XML = """<invstOrSecs>
<invstOrSec><name>APPLE INC</name><cusip>037823100</cusip><assetCat>EC</assetCat>
<valUSD>100.0</valUSD><pctVal>0.5</pctVal></invstOrSec>
<invstOrSec><name>MSFT CORP</name><cusip>594918104</cusip><assetCat>EC</assetCat>
<valUSD>200.0</valUSD><pctVal>1.0</pctVal></invstOrSec>
</invstOrSecs>"""

print("== NPORT-P 解析 / 权重 ==")
rows = fic.parse_holdings(XML)
check("解析 2 笔持仓", len(rows) == 2, f"n={len(rows)}")
check("valUSD/pctVal 解析", rows and rows[0]["valUSD"] == 100.0 and rows[0]["pctVal"] == 0.5,
      f"r0={rows[0] if rows else None}")

idx = {"norm": {}, "tokens": {}, "compact": {}}
for nm, tk, title in (("APPLE INC", "AAPL", "Apple Inc."), ("MSFT CORP", "MSFT", "Microsoft Corp.")):
    key = fic._norm_name(nm)
    for sub in ("norm", "tokens", "compact"):
        k = key if sub == "norm" else ((" ".join(sorted(key.split())) if sub == "tokens"
                                       else key.replace(" ", "")))
        idx[sub].setdefault(k, []).append((tk, title))
syms, unmatched, ambiguous, weights = fic.map_symbols(rows, idx, aliases={})
check("map_symbols 返回 4 元组且 symbols=[AAPL,MSFT]", syms == ["AAPL", "MSFT"], f"syms={syms}")
check("权重累加 AAPL={val 100,pct .5} / MSFT={200,1.0}",
      weights.get("AAPL", {}).get("val_usd") == 100.0 and weights.get("MSFT", {}).get("pct") == 1.0,
      f"w={weights}")
rows_dup = rows + [dict(rows[0])]  # AAPL 再一笔 → 权重应累加
_, _, _, w2 = fic.map_symbols(rows_dup, idx, aliases={})
check("同 ticker 多笔持仓权重累加（AAPL val=200）",
      w2.get("AAPL", {}).get("val_usd") == 200.0, f"w2={w2.get('AAPL')}")

# ==================== 汇总 ====================
print()
print(f"结果: {_PASS} 通过 / {_FAIL} 失败")
sys.exit(1 if _FAIL else 0)
