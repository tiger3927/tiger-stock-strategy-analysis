#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""选股池量化打分与 Top-N 选出（2026-10-31 正式入口）

把 replay 轮验证过的 v2-v4 打分引擎接到选股池功能上：
  宇宙（--universe）：
    pool   = scripts/stock_pool.json 全池 ticker（默认；守"池子=可买边界"哲学）
    index  = SEC NPORT-P as-of 成分（SPY∪MDY，走 fetch_index_constituents.py）
    file   = 自定义清单（--universe-file，一行/逗号分隔）
  数量（--top-n）：用户设定。证据档位（33 月回测，index 宇宙）：
    20=高辛烷值（单月可 -30%）/ 50=WF 双段第一 / 75=平衡 / 100=保守；n<20 拒绝
  参数（--params）：冻结参数集（默认 screen_params_v3.json；v4=screen_params_v4.json），
    sha 写入产物；**改参数=新文件新版本，禁止原地改**

产物（全部落 <skill>/screening/，不落 scripts/）：
  screen_<D8>.json          选出名单（因子/得分/剔除留痕/参数 sha/宇宙来源）
  workspace/const_{D8}.json 宇宙快照（含 source 与结构声明）
  workspace/fund_{D8}.json  基本面壳（v3/v4 组不含基本面 → 空壳即可；--fund-file 可喂 as-of 基本面）
  wide_price/{TK}_{period}_to{end}.pkl  价格宽窗口（自动补缺，走 get_market_data 缓存层）

PIT：打分只用 ≤as-of 数据；buffer 带用上月名单（workspace 里上月的 screen_*.json 自动接续）。
红线：回测超额全部 t<2 且带幸存者偏差折扣；本工具输出=候选证据，不是收益承诺；
      调仓仍按 00_index 双周期节点与板块仓位框架执行，量化名单只是买入候选一路输入。

用法：
  python scripts/screen_select.py                          # 今天、池宇宙、v3、n=50
  python scripts/screen_select.py --top-n 20 --params scripts/screen_params_v4.json
  python scripts/screen_select.py --universe index --as-of 2025-06-01   # 历史锚定（回放证据）
  python scripts/screen_select.py --months 34 --start 2024-01-01 --end 2026-10-01  # 批产（回测用）
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
WS_DEFAULT = os.path.join(SKILL, "screening")
BENCH = ("SPY", "RSP")          # 基准/相对强度所需，不进宇宙
N_MIN, N_WARN = 20, 15

DISCLAIMER = ("⚠️ 打分与历史超额基于 33 月单市况样本（t<2）且带幸存者偏差折扣；"
              "输出=买入候选证据，非收益承诺；调仓按双周期节点+板块仓位框架执行。")


def pool_tickers():
    d = json.load(open(os.path.join(HERE, "stock_pool.json"), encoding="utf-8"))
    out = set()
    for v in d["sub_sectors"].values():
        for tier, tickers in (v.get("tiers") or {}).items():
            out.update(tickers or [])
    return sorted(out)


def read_ticker_file(path):
    out = set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            out.update(t.strip().upper() for t in line.replace(",", " ").split() if t.strip())
    return sorted(out)


def ensure_prices(tickers, wide_dir, period, end):
    """缺啥补啥：价格宽窗口走 get_market_data 的 as-of 历史层（同 fetch_wide_price 机制）。"""
    import get_market_data as gmd
    gmd._RUNTIME["asof_dir"] = os.path.abspath(wide_dir)
    missing = [tk for tk in tickers
               if not os.path.exists(os.path.join(wide_dir, f"{tk}_{period}_to{end}.pkl"))]
    if not missing:
        print(f"价格宽窗口齐备（{len(tickers)} 只）")
        return 0
    print(f"取价格 {len(missing)}/{len(tickers)} 只（{period} to {end}，并发 3）…", flush=True)
    import concurrent.futures
    import time
    t0 = time.time()

    def one(tk):
        try:
            df = gmd.get_history_dataframe(tk, period=period, end=end)
            return tk, df is not None and not df.empty
        except Exception:
            return tk, False

    failed = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        for i, (tk, ok) in enumerate(ex.map(one, missing), 1):
            if not ok:
                failed.append(tk)
            if i % 40 == 0 or i == len(missing):
                print(f"  进度 {i}/{len(missing)}  {time.time() - t0:.0f}s", flush=True)
    if failed:
        print(f"取数失败 {len(failed)}: {','.join(failed[:20])}（引擎会按'无价格数据'诚实留痕，重跑自动补）")
    return 0


def main():
    ap = argparse.ArgumentParser(description="选股池量化打分 Top-N（PIT）")
    ap.add_argument("--universe", default="pool", choices=["pool", "index", "file"])
    ap.add_argument("--universe-file", default=None, help="file 模式的 ticker 清单")
    ap.add_argument("--as-of", default=None, help="打分日（默认今天）；批产模式用 --start/--end")
    ap.add_argument("--start", default=None, help="批产起点（与 --end 同给则逐月批产）")
    ap.add_argument("--end", default=None, help="批产终点")
    ap.add_argument("--params", default=os.path.join(HERE, "screen_params_v3.json"))
    ap.add_argument("--top-n", type=int, default=50, help="选出数量（用户设定；<20 拒绝）")
    ap.add_argument("--workspace", default=WS_DEFAULT)
    ap.add_argument("--period", default="5y")
    ap.add_argument("--fund-file", default=None, help="as-of 基本面 JSON（fetch_fundamentals_asof 产物）；默认空壳")
    ap.add_argument("--out-prefix", default="screen_")
    ap.add_argument("--no-fetch", action="store_true", help="不自动取价格（离线/已预取）")
    a = ap.parse_args()

    if a.top_n < N_MIN:
        print(f"拒绝：top_n={a.top_n} < {N_MIN}（集中悬崖已实测；如确需更小仓位请用现有 04/08 层）")
        return 2
    if a.top_n < N_WARN * 2:
        print(f"提示：n={a.top_n} 属集中组合——实测单月回撤可达 -27%（v4 n=20），仓位框架必须同步收紧")

    ws = os.path.abspath(a.workspace)
    ws_ws = os.path.join(ws, "workspace")
    wide = os.path.join(ws, "wide_price")
    for d in (ws, ws_ws, wide):
        os.makedirs(d, exist_ok=True)

    batch = bool(a.start and a.end)
    if batch:
        months = [datetime.date.fromisoformat(a.start)]
        cur = months[0]
        endd = datetime.date.fromisoformat(a.end)
        while True:
            cur = datetime.date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
            if cur > endd:
                break
            months.append(cur)
    else:
        as_of = datetime.date.fromisoformat(a.as_of) if a.as_of else datetime.date.today()
        months = [as_of]
    wide_end = months[-1].isoformat()

    # 宇宙快照（每月一份；pool 模式=当前池结构锚定，**必须带结构声明**）
    import screen_engine as eng
    p, sha = eng.load_params(a.params)
    prev_sel = None
    if not batch:
        # 单月运行也接 buffer：找同前缀的上月名单文件
        m0 = months[0].replace(day=1)
        pm = (m0.replace(year=m0.year - 1, month=12) if m0.month == 1
              else m0.replace(month=m0.month - 1))
        prev_p = os.path.join(ws, f"{a.out_prefix}{pm.strftime('%Y%m%d')}.json")
        if os.path.exists(prev_p):
            prev_sel = {x["ticker"] for x in
                        json.load(open(prev_p, encoding="utf-8"))["selected"]}
            print(f"buffer 接续上月名单：{os.path.basename(prev_p)}（{len(prev_sel)} 只）")
    for D in months:
        d8 = D.strftime("%Y%m%d")
        const_p = os.path.join(ws_ws, f"const_{d8}.json")
        if a.universe == "pool":
            uni = pool_tickers()
            src = (f"stock_pool.json 全池 {len(uni)} 只（当前池结构锚定，point_in_time=false——"
                   "历史批产时含'当时不在池、后入池'标的与'当时在池、已剔除'缺失，池成员前视已声明）")
            if not os.path.exists(const_p):
                json.dump({"source": src, "results": [{"symbols": uni}]},
                          open(const_p, "w", encoding="utf-8"), ensure_ascii=False)
        elif a.universe == "file":
            uni = read_ticker_file(a.universe_file)
            src = f"自定义清单 {a.universe_file}（{len(uni)} 只）"
            json.dump({"source": src, "results": [{"symbols": uni}]},
                      open(const_p, "w", encoding="utf-8"), ensure_ascii=False)
        else:  # index：走正式 as-of 成分通道
            if not os.path.exists(const_p):
                r = subprocess.run([sys.executable, os.path.join(HERE, "fetch_index_constituents.py"),
                                    "--as-of", D.isoformat(), "--index", "all", "--out", const_p],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace")
                if r.returncode or not os.path.exists(const_p):
                    print("成分取数失败：", (r.stderr or r.stdout)[-400:])
                    return 1
            uni = sorted({tk for rr in json.load(open(const_p, encoding="utf-8"))["results"]
                          for tk in (rr.get("symbols") or [])})
        # 基本面壳
        fund_p = os.path.join(ws_ws, f"fund_{d8}.json")
        if a.fund_file:
            import shutil
            shutil.copyfile(a.fund_file, fund_p)
        elif not os.path.exists(fund_p):
            json.dump({"tickers": {}}, open(fund_p, "w", encoding="utf-8"))

        if not a.no_fetch:
            ensure_prices(sorted(set(uni) | set(BENCH)), wide, a.period, wide_end)

        store = eng.SeriesStore(wide, a.period, wide_end, p["split"]["candidate_ratios"], p["split"]["tol"])
        rep = eng.screen_month(D, store, p, sha, "full", 20240101,
                               top_n_override=a.top_n, data_dir=ws_ws,
                               prev_sel=sorted(prev_sel) if prev_sel else None)
        cur_set = {x["ticker"] for x in rep["selected"]}
        if prev_sel is not None:
            rep["vs_prev"] = {"added": sorted(cur_set - prev_sel), "removed": sorted(prev_sel - cur_set),
                              "turnover_pct": round(len(cur_set - prev_sel) / max(len(cur_set), 1) * 100, 1)}
        prev_sel = cur_set
        out = os.path.join(ws, f"{a.out_prefix}{d8}.json")
        json.dump(rep, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"{D}: 宇宙={rep['universe_n']} 可评分={rep['scored_n']} 剔除={rep['filtered_n']} "
              f"选中={len(rep['selected'])} 换手={rep.get('vs_prev', {}).get('turnover_pct', '-')}% "
              f"→ {os.path.relpath(out, SKILL)}")
    print(DISCLAIMER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
