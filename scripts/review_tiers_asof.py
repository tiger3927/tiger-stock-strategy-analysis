#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""as-of 回放层审查（T1/T2/T3 剔除 + 层间流动）——链式回放 run4（2026-10-19）

把 Task-选股池测试.md 历史回放提示词的审查规程落成**确定性脚本**（同参数可复现）：

T1（季度；回放 --expire all 下每月到期）：
    子板块 T1 > 5 只时按 as-of 市值排名，Top 5 之外降级：
      排名源（§10.5 层级）：fund 绝对 market_cap_as_of > NPORT-P 相对权重（只可排序）
      > as-of 成交额近似；**不可得 → 排末尾、永不剔除**（不可得 ≠ 不达标）
      降级去向（仅绝对市值可判档位）：≥5e9→T2；1e9~5e9→T3；<1e9→出池；
      仅权重/近似可排名的超员 → 留 T1 + 待人工确认（权重只可排序、不可判档位）
    子板块 T1 ≤ 5 只：不剔除（池外探测/入池新增不在批量回放范围）
T2（月度，四项均用 fund_D as-of 值；None/unavailable/仅权重兜底 → "不可得" → 保留+待人工确认）：
    mcap ∈ [5e9,5e10] / revenue_growth > 15% / roe > 10% / 成交额 > 5e7
    任一**可判**项不达标（按 市值低 > 市值高 > 基本面 > 成交额 顺序取首个命中分支）：
      mcap < 1e9            → 出池
      1e9 ≤ mcap < 5e9      → T3（T2→T3 流动规则）
      mcap > 5e10           → 并入现有 T1 重排后 as-of 市值 Top5 → T1，否则出池+留痕
      growth/roe 不达标（基本面恶化）→ mcap<5e9(有数据)→T3；mcap 不可得→T3（流动规则原文）；
                                       mcap≥5e9→出池+留痕
      成交额 <5e7           → mcap<5e9(有数据)→T3；否则保留 + 待人工确认（缺档位依据不移动）
T3（月度，全部 as-of 口径，值须可得才可剔除）：
    剔除：mcap <1e9（fund 绝对值）/ 价格 <$3（fund price_as_of）/ 成交额 <5e6（宽窗口 as-of）
    封顶：剔除 ≤ T3 总数 20%（「单次增删不超过 Tier3 总数 20%」；与 refill 新增的合并额度由 driver 核算）；
          超额按严重度（市值<1e9 > 价格<3 > 成交额最低）之外的部分保留为待人工确认
    基本面恶化（growth<0 或 roe<0，有数据）→ 记「关注」**不自动剔除**（原规则要新闻综合判断=AI）
    流动 T3→T2：mcap >5e10 且 growth >15%（两项均可得）→ 升入 T2（先升级后剔除）
成交额（as-of）= 宽窗口 pkl 最近 3 个日历月 Volume 均值 × ≤D 末根未复权 Close（本地切片，PIT，零网络）

写回（--apply，只写指定池副本，绝不写生产池）：
    sub_sectors.*.tiers + meta.tier{1,2,3}_updated_at = D
    + meta.previous_snapshot（**变更前**摘要）+ meta.snapshots[]（保留最近 12，同 refill 契约）
报告：--out（默认池同目录 review_<D8>.json）：逐标的判据 / 待人工确认 / 关注 / 统计

不做什么：
    - 池外探测 / 池外新增（AI 规程；批量回放只有 T3 refill 新增）
    - GICS 同步（gics_etf_map 无 as-of 版本，§10.5 已声明按当前口径）
    - 「上市 <6 个月」/「已被收购」（仅线索，须人工确认，§10.5）

用法：
  python scripts/review_tiers_asof.py --pool replay/pool_2024-01-01.json --as-of 2024-01-01 \
      --fund replay/fund_20240101.json --const replay/const_20240101.json            # 预演
  python scripts/review_tiers_asof.py ... --apply                                     # 写回副本
"""
import argparse
import json
import os
import sys

import pandas as pd

WIDE_PERIOD = "3y"
WIDE_END_DEFAULT = "2026-10-01"
T1_TOP_N = 5
T1_CAP_FLOOR = 5e9      # T1 降级去 T2 的市值下界
T3_CAP_CEIL = 5e9       # T2/T3 档位上界
T3_CAP_FLOOR = 1e9      # T3 剔除线（市值）
T2_CAP_MIN, T2_CAP_MAX = 5e9, 5e10
GROWTH_MIN = 0.15
ROE_MIN = 0.10
LIQ_T2, LIQ_T3 = 5e7, 5e6   # T2 成交额门槛 / T3 成交额剔除线
PRICE_T3_MIN = 3.0
CAP_T3_REMOVE_RATIO = 0.20


def load_json(path, default=None):
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def wide_metrics(wide_dir, as_of, tickers, period=WIDE_PERIOD, wide_end=WIDE_END_DEFAULT):
    """宽窗口本地切片 → {tk: {"close","liq"}|None}；liq = 近 3 日历月日均 Volume × as-of Close。"""
    out = {}
    cutoff = pd.Timestamp(as_of)
    win_start = cutoff - pd.Timedelta(days=90)
    for tk in tickers:
        path = os.path.join(wide_dir, f"{tk}_{period}_to{wide_end}.pkl")
        if not os.path.exists(path):
            out[tk] = None
            continue
        try:
            df = pd.read_pickle(path)
            idx = pd.to_datetime(df.index)
            sub = df.loc[idx <= cutoff]
            if sub.empty:
                out[tk] = None
                continue
            close = float(sub["Close"].iloc[-1])
            win = sub.loc[pd.to_datetime(sub.index) >= win_start]
            av = float(win["Volume"].mean()) if len(win) else None
            out[tk] = {"close": close, "liq": (av * close) if av else None}
        except Exception:
            out[tk] = None
    return out


def weight_map(const):
    """const results[]（SPY+MDY）→ {tk: pct 合计} + 成员集合。"""
    w, idx_set = {}, set()
    for r in (const or {}).get("results", []):
        for tk, v in (r.get("member_weights") or {}).items():
            w[tk] = w.get(tk, 0.0) + float((v or {}).get("pct") or 0.0)
        idx_set.update(r.get("symbols") or [])
    return w, idx_set


def classify(fund_t, weight, wide_m):
    """单标的判据 → dict。mcap_kind: 'abs'（fund 绝对市值）| 'approx'（权重兜底，只可排序）| None。"""
    fund_t = fund_t or {}
    mcap, kind = fund_t.get("market_cap_as_of"), None
    if mcap is not None:
        kind = "abs"
    elif fund_t.get("market_cap_as_of_approx"):
        kind = "approx"
    return {
        "mcap": mcap, "mcap_kind": kind,
        "growth": fund_t.get("revenue_growth"),
        "roe": fund_t.get("roe"),
        "price": fund_t.get("price_as_of"),
        "liq": (wide_m or {}).get("liq"),
        "weight": weight,
        "unavailable": fund_t.get("available") is False,
    }


def t1_rank_key(c):
    """T1 排名键：绝对市值 > 权重 > 成交额近似 > 不可得（组内降序）。"""
    if c["mcap_kind"] == "abs" and c["mcap"] is not None:
        return (0, -c["mcap"])
    if c["mcap_kind"] == "approx" and (c["weight"] or 0) > 0:
        return (1, -c["weight"])
    if c["liq"] is not None:
        return (2, -c["liq"])
    return (3, 0)


def review(pool, as_of, fund, const, wm, tiers_review, max_remove):
    tiers = pool["sub_sectors"]
    fund_tickers = fund.get("tickers") or {}
    wt, in_index = weight_map(const)
    changes, pending, watch = [], [], []

    def mv(ticker, sub, frm, to, reason, ev):
        changes.append({"ticker": ticker, "sub_sector": sub, "from": frm, "to": to,
                        "reason": reason, "evidence": ev})

    def ev_of(ticker, extra=None):
        c = classify(fund_tickers.get(ticker), wt.get(ticker, 0.0), wm.get(ticker))
        c["in_index"] = ticker in in_index
        c["weight"] = round(c["weight"], 4) if c["weight"] else 0.0
        if c.get("liq"):
            c["liq"] = round(c["liq"])
        if extra:
            c.update(extra)
        return c

    all_t3 = [t for v in tiers.values() for t in (v.get("tiers") or {}).get("T3", [])]
    cap_remove = int(len(all_t3) * CAP_T3_REMOVE_RATIO) if max_remove <= 0 else max_remove

    # ---------------- T1：子板块 Top 5 重排，超员降级 ----------------
    if "T1" in tiers_review:
        for key, info in tiers.items():
            members = list((info.get("tiers") or {}).get("T1", []))
            if len(members) <= T1_TOP_N:
                continue
            cs = {t: classify(fund_tickers.get(t), wt.get(t, 0.0), wm.get(t)) for t in members}
            data = sorted([t for t in members if t1_rank_key(cs[t])[0] < 3],
                          key=lambda t: t1_rank_key(cs[t]))
            drop = data[T1_TOP_N:]
            for t in drop:
                c = cs[t]
                if c["mcap_kind"] == "abs" and c["mcap"] is not None:
                    if c["mcap"] >= T1_CAP_FLOOR:
                        info["tiers"].setdefault("T2", [])
                        if t not in info["tiers"]["T2"]:
                            info["tiers"]["T2"].append(t)
                        mv(t, key, "T1", "T2",
                           f"子板块 T1 超员（as-of 市值第 {data.index(t) + 1} 名 >{T1_TOP_N}）", ev_of(t))
                    elif c["mcap"] >= T3_CAP_FLOOR:
                        info["tiers"].setdefault("T3", [])
                        if t not in info["tiers"]["T3"]:
                            info["tiers"]["T3"].append(t)
                        mv(t, key, "T1", "T3", f"子板块 T1 超员且 as-of 市值 {c['mcap']/1e9:.1f}B <50 亿", ev_of(t))
                    else:
                        mv(t, key, "T1", "OUT", f"子板块 T1 超员且 as-of 市值 {c['mcap']/1e9:.2f}B <10 亿", ev_of(t))
                else:
                    pending.append({"ticker": t, "tier": "T1", "item": "档位",
                                    "reason": f"T1 超员但市值档位不可得（kind={c['mcap_kind']}）→ 保留+待人工确认"})
            # 只移除"已移动"的超员（abs 市值者）；不可得者留在 T1
            moved_abs = {t for t in drop if cs[t]["mcap_kind"] == "abs" and cs[t]["mcap"] is not None}
            info["tiers"]["T1"] = [t for t in members if t not in moved_abs]

    # ---------------- T2：四门 as-of 判定 + 流动 ----------------
    if "T2" in tiers_review:
        for key, info in tiers.items():
            members = list((info.get("tiers") or {}).get("T2", []))
            t1_members = list((info.get("tiers") or {}).get("T1", []))
            for t in members:
                c = classify(fund_tickers.get(t), wt.get(t, 0.0), wm.get(t))
                c["in_index"] = t in in_index
                mcap = c["mcap"] if c["mcap_kind"] == "abs" else None
                g, r, lq = c["growth"], c["roe"], c.get("liq")
                fails = []
                if mcap is not None and mcap < T2_CAP_MIN:
                    fails.append("mcap_low")
                if mcap is not None and mcap > T2_CAP_MAX:
                    fails.append("mcap_high")
                if g is not None and g <= GROWTH_MIN:
                    fails.append("growth")
                if r is not None and r <= ROE_MIN:
                    fails.append("roe")
                if lq is not None and lq <= LIQ_T2:
                    fails.append("liq")
                ungot = [n for n, v in (("市值", mcap), ("增速", g), ("ROE", r), ("成交额", lq)) if v is None]
                if not fails:
                    if ungot:
                        pending.append({"ticker": t, "tier": "T2", "item": "/".join(ungot),
                                        "reason": f"{','.join(ungot)} 不可得 → 保留+待人工确认"})
                    continue
                ev = ev_of(t)
                if "mcap_low" in fails:
                    if mcap < T3_CAP_FLOOR:
                        mv(t, key, "T2", "OUT", f"as-of 市值 {mcap/1e9:.2f}B <10 亿", ev)
                    else:
                        info["tiers"].setdefault("T3", [])
                        if t not in info["tiers"]["T3"]:
                            info["tiers"]["T3"].append(t)
                        mv(t, key, "T2", "T3", f"as-of 市值 {mcap/1e9:.1f}B <50 亿（T2→T3 流动）", ev)
                elif "mcap_high" in fails:
                    t1cs = {x: classify(fund_tickers.get(x), wt.get(x, 0.0), wm.get(x)) for x in t1_members}
                    t1cs[t] = c
                    ranked = sorted(t1_members + [t], key=lambda x: t1_rank_key(t1cs[x]))
                    if c["mcap_kind"] == "abs" and ranked.index(t) < T1_TOP_N:
                        info["tiers"].setdefault("T1", [])
                        if t not in info["tiers"]["T1"]:
                            info["tiers"]["T1"].append(t)
                        mv(t, key, "T2", "T1",
                           f"as-of 市值 {mcap/1e9:.0f}B >500 亿且子板块 Top{ranked.index(t) + 1}", ev)
                    else:
                        mv(t, key, "T2", "OUT", f"as-of 市值 {mcap/1e9:.0f}B 超 T2 上限且非子板块 as-of Top5", ev)
                elif "growth" in fails or "roe" in fails:
                    why = "增速 %.1f%%≤15%%" % (g * 100) if "growth" in fails else "ROE %.1f%%≤10%%" % (r * 100)
                    if mcap is None or mcap < T3_CAP_CEIL:
                        info["tiers"].setdefault("T3", [])
                        if t not in info["tiers"]["T3"]:
                            info["tiers"]["T3"].append(t)
                        suffix = "；市值不可得 → 按流动规则入 T3（下月 T3 审查按 as-of 复核档位）" if mcap is None \
                            else f"且市值 {mcap/1e9:.1f}B 在 T3 档"
                        mv(t, key, "T2", "T3", f"基本面恶化（{why}）{suffix}", ev)
                    else:
                        mv(t, key, "T2", "OUT", f"基本面恶化（{why}）且市值 {mcap/1e9:.0f}B 超 T3 档 → 出池", ev)
                else:  # 仅 liq 不达标
                    if mcap is not None and mcap < T3_CAP_CEIL:
                        info["tiers"].setdefault("T3", [])
                        if t not in info["tiers"]["T3"]:
                            info["tiers"]["T3"].append(t)
                        mv(t, key, "T2", "T3", f"as-of 成交额 {lq/1e6:.0f}M ≤50M 且市值 {mcap/1e9:.1f}B 在 T3 档", ev)
                    else:
                        pending.append({"ticker": t, "tier": "T2", "item": "成交额",
                                        "reason": f"成交额 {lq/1e6:.0f}M 不达标但市值档位不可判 → 保留+待人工确认"})
            moved = {x["ticker"] for x in changes if x["from"] == "T2" and x["to"] != "T2"}
            info["tiers"]["T2"] = [t for t in members if t not in moved]

    # ---------------- T3：先升级（T3→T2），后剔除（带 20% 封顶） ----------------
    if "T3" in tiers_review:
        for key, info in tiers.items():
            members = list((info.get("tiers") or {}).get("T3", []))
            for t in members:
                c = classify(fund_tickers.get(t), wt.get(t, 0.0), wm.get(t))
                c["in_index"] = t in in_index
                mcap = c["mcap"] if c["mcap_kind"] == "abs" else None
                g = c["growth"]
                if mcap is not None and mcap > T2_CAP_MAX and g is not None and g > GROWTH_MIN:
                    info["tiers"].setdefault("T2", [])
                    if t not in info["tiers"]["T2"]:
                        info["tiers"]["T2"].append(t)
                    mv(t, key, "T3", "T2",
                       f"as-of 市值 {mcap/1e9:.0f}B >500 亿且增速 {g*100:.1f}% >15% → T3→T2 流动", ev_of(t))
            promoted = {x["ticker"] for x in changes if x["from"] == "T3" and x["to"] == "T2"}
            info["tiers"]["T3"] = [t for t in members if t not in promoted]

        removals = []
        for key, info in tiers.items():
            for t in list((info.get("tiers") or {}).get("T3", [])):
                c = classify(fund_tickers.get(t), wt.get(t, 0.0), wm.get(t))
                c["in_index"] = t in in_index
                mcap = c["mcap"] if c["mcap_kind"] == "abs" else None
                px, lq = c.get("price"), c.get("liq")
                trig = []
                if mcap is not None and mcap < T3_CAP_FLOOR:
                    trig.append(("mcap", f"as-of 市值 {mcap/1e9:.2f}B <10 亿"))
                if px is not None and px < PRICE_T3_MIN:
                    trig.append(("price", f"as-of 价格 ${px:.2f} <$3"))
                if lq is not None and lq < LIQ_T3:
                    trig.append(("liq", f"as-of 成交额 {lq/1e6:.1f}M <5M"))
                if trig:
                    kinds = [x[0] for x in trig]
                    sev = 0 if "mcap" in kinds else (1 if "price" in kinds else 2)
                    removals.append((sev, -(lq or 0.0), t, key, trig, c))
                elif (c["growth"] is not None and c["growth"] < 0) or (c["roe"] is not None and c["roe"] < 0):
                    watch.append({"ticker": t, "tier": "T3",
                                  "reason": f"增速 {c['growth']} / ROE {c['roe']} 为负 → 关注（不自动剔除，需新闻综合判断）"})
        removals.sort(key=lambda x: (x[0], x[1]))
        for sev, _, t, key, trig, c in removals:
            if cap_remove > 0:
                cap_remove -= 1
                info = tiers[key]
                info["tiers"]["T3"] = [x for x in info["tiers"].get("T3", []) if x != t]
                mv(t, key, "T3", "OUT", "；".join(x[1] for x in trig), ev_of(t))
            else:
                pending.append({"ticker": t, "tier": "T3", "item": "剔除超 20% 封顶",
                                "reason": "；".join(x[1] for x in trig) + " → 保留+待人工确认"})
    return changes, pending, watch


def tier_counts(pool):
    tiers = pool["sub_sectors"]
    return {k: sum(len(v["tiers"].get(k, [])) for v in tiers.values()) for k in ("T1", "T2", "T3")}


def tier_sets(pool):
    tiers = pool["sub_sectors"]
    return {k: sorted({x for v in tiers.values() for x in v["tiers"].get(k, [])}) for k in ("T1", "T2", "T3")}


def main():
    ap = argparse.ArgumentParser(description="as-of 回放层审查（T1/T2/T3 + 流动；只写池副本）")
    ap.add_argument("--pool", required=True, help="回放池副本（replay/pool_<D>.json）")
    ap.add_argument("--as-of", dest="as_of", required=True)
    ap.add_argument("--fund", required=True, help="fund_<D>.json（as-of 增速/ROE/股本/市值）")
    ap.add_argument("--const", required=True, help="const_<D>.json（SPY/MDY member_weights 兜底）")
    ap.add_argument("--wide", default=None, help="宽窗口目录（默认 <skill>/replay/wide_price）")
    ap.add_argument("--wide-end", default=WIDE_END_DEFAULT)
    ap.add_argument("--wide-period", default=WIDE_PERIOD)
    ap.add_argument("--tiers", default="T1,T2,T3")
    ap.add_argument("--max-remove", type=int, default=0, help="T3 剔除封顶（0=自动 20%%）")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out", default=None, help="报告路径（默认池同目录 review_<D8>.json）")
    a = ap.parse_args()

    skill = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    wide_dir = a.wide or os.path.join(skill, "replay", "wide_price")

    pool = load_json(a.pool)
    if not pool:
        print(json.dumps({"error": f"池不存在: {a.pool}"}, ensure_ascii=False))
        return 1
    fund = load_json(a.fund) or {}
    const = load_json(a.const) or {}
    tiers_review = [x.strip().upper() for x in a.tiers.split(",") if x.strip()]

    all_pool = sorted({t for v in pool["sub_sectors"].values()
                       for ts in (v.get("tiers") or {}).values() for t in (ts or [])})
    wm = wide_metrics(wide_dir, a.as_of, all_pool, a.wide_period, a.wide_end)

    # **变更前**快照（previous_snapshot 语义）必须在 review 改 tiers 之前取
    cnt_before = tier_counts(pool)
    sets_before = tier_sets(pool)
    snap_before = {
        "saved_at": pool["meta"].get("tier1_updated_at", ""),
        "tier_counts": cnt_before,
        "tier1_tickers": sets_before["T1"],
        "tier2_tickers": sets_before["T2"],
        "tier3_tickers": sets_before["T3"],
    }

    changes, pending, watch = review(pool, a.as_of, fund, const, wm, tiers_review, a.max_remove)
    cnt_after = tier_counts(pool)

    report = {
        "requested_as_of": a.as_of,
        "pool": os.path.abspath(a.pool),
        "tiers_reviewed": tiers_review,
        "rules_source": "美股选股池管理.md §2.4/§4.2/§6.3 + Task-选股池测试.md 历史回放提示词 + §10.5 降级",
        "changes": changes,
        "pending_manual": pending,
        "watch": watch,
        "stats": {
            "T1": {"before": cnt_before["T1"], "after": cnt_after["T1"]},
            "T2": {"before": cnt_before["T2"], "after": cnt_after["T2"]},
            "T3": {"before": cnt_before["T3"], "after": cnt_after["T3"]},
            "t3_removed": sum(1 for c in changes if c["from"] == "T3" and c["to"] == "OUT"),
            "changed_total": len(changes), "pending_total": len(pending),
        },
        "gics_sync": "跳过（gics_etf_map 无 as-of 版本，按当前口径，§10.5 声明）",
        "point_in_time": {
            "fundamentals": True,
            "liquidity": "wide_window_local_slice（近3月日均量×as-of价）",
            "candidate_source": "本脚本不涉及候选源（仅池内审查；T3 新增走 refill）",
        },
    }
    if a.apply:
        pool["meta"]["previous_snapshot"] = snap_before
        snaps = pool["meta"].setdefault("snapshots", [])
        snaps.append(dict(snap_before, appended_at=a.as_of))
        snaps[:] = snaps[-12:]
        for k in ("tier1", "tier2", "tier3"):
            pool["meta"][f"{k}_updated_at"] = a.as_of
        with open(a.pool, "w", encoding="utf-8") as f:
            json.dump(pool, f, ensure_ascii=False, indent=2)
        report["applied"] = True
    else:
        report["applied"] = False

    out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.pool)),
                                f"review_{a.as_of.replace('-', '')}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"{'APPLY' if a.apply else 'DRY-RUN'}: 变更 {len(changes)} | 待人工确认 {len(pending)} | 关注 {len(watch)}")
    for k in ("T1", "T2", "T3"):
        print(f"  {k}: {cnt_before[k]} → {cnt_after[k]}")
    for c in changes[:40]:
        print(f"  {c['from']}→{c['to']} {c['ticker']} [{c['sub_sector']}] {c['reason'][:90]}")
    if len(changes) > 40:
        print(f"  … 其余 {len(changes) - 40} 条见 {out}")
    print(f"  报告: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
