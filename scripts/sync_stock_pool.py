r"""
校验美股选股池数据（stock_pool.json）

用法:
    python skills/tiger-stock-strategy-analysis/scripts/sync_stock_pool.py
    python .../sync_stock_pool.py --pool-file <路径> --as-of 2025-06-30 --output json

原理:
    1. 读取同目录下的 stock_pool.json（选股池数据；--pool-file 可指向回放副本）
    2. 构建 ticker → GICS 反向索引
    3. 验证 JSON 结构并输出统计
    4. v2 扩展校验（P0-2）：tier 数量区间（默认 warning，--strict-tiers 升级为 error）、
       T3 空置、meta.total_sub_sectors 一致性、tier*_updated_at 到期判定（--as-of 可锚定）、
       previous_snapshot 换手率差集

退出码语义（--output json 时为机器可读证据）:
    0 = 通过（允许有警告）
    1 = 不通过（结构错误 / 严格模式违规 / --as-of 模式时间戳在未来）

数据源:
    stock_pool.json — AI 直接修改此文件即可更新选股池，无需改 Python 代码
"""
import argparse
import datetime
import json
import sys
import os

# 强制 stdout/stderr 使用 UTF-8：避免在 GBK 控制台 / 管道（如 | Select-String）下打印 ✅ 时崩溃
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 数据文件路径（与脚本同目录，默认值 = 现有生产行为）
_DEFAULT_DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_pool.json")

# ---- v2 校验口径（与 docs/调仓/美股/美股选股池管理.md §4.2 对齐） ----
# tier 数量区间：文档设计目标（每子板块）。当前生产池存在结构性偏离
# （T2 多空板块 0 只、T3 三个子板块 2 只等），故默认只出 warning；
# --strict-tiers 升级为 error（新池验收用）。
TIER_COUNT_RANGE = {"T1": (3, 5), "T2": (5, 10), "T3": (5, 15)}
# 文档明确承认的结构性例外（美股选股池管理.md：候选本身稀少，"非遗漏"）：
# 建筑材料 / 纸与林木产品 / 电信服务 —— T3 允许低至 2 只
T3_LOW_EXCEPTION_GICS = {"15102010", "15105010", "50101010"}
T3_LOW_EXCEPTION_MIN = 2
# tier 刷新周期（天）：T1 季度 90 / T2 月度 30 / T3 月度 30
TIER_STALE_DAYS = {"tier1_updated_at": 90, "tier2_updated_at": 30, "tier3_updated_at": 30}


def load_data(path: str) -> dict:
    """从 stock_pool.json 加载选股池数据"""
    if not os.path.exists(path):
        print(f"❌ 找不到数据文件: {path}")
        print(f"   请确保 stock_pool.json 与脚本在同一目录，或用 --pool-file 指定")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_ticker_index(data: dict):
    """构建 ticker → GICS 代码的反向索引"""
    index = {}
    for gics_code, info in data["sub_sectors"].items():
        for tier in ["T1", "T2", "T3"]:
            for ticker in info["tiers"].get(tier, []):
                index[ticker] = gics_code
    data["ticker_to_sub_sector"] = index


def validate_json(data: dict) -> list:
    """验证 JSON 结构的合理性（错误级——与旧版一致）"""
    errors = []

    # 1. 检查 meta
    if "meta" not in data:
        errors.append("缺少 meta 字段")
    if "sub_sectors" not in data:
        errors.append("缺少 sub_sectors 字段")

    # 2. 检查每个子板块
    for gics_code, info in data["sub_sectors"].items():
        if "name" not in info:
            errors.append(f"子板块 {gics_code} 缺少 name")
        if "sector" not in info:
            errors.append(f"子板块 {gics_code} 缺少 sector")
        if "tiers" not in info:
            errors.append(f"子板块 {gics_code} 缺少 tiers")
            continue
        for tier in ["T1", "T2", "T3"]:
            if tier not in info["tiers"]:
                errors.append(f"子板块 {gics_code} 缺少 tier {tier}")
            elif not isinstance(info["tiers"][tier], list):
                errors.append(f"子板块 {gics_code} tier {tier} 不是列表")

    # 3. 检查 ticker 索引完整性
    index = data.get("ticker_to_sub_sector", {})
    all_tickers = set()
    for info in data["sub_sectors"].values():
        for tier in ["T1", "T2", "T3"]:
            for t in info["tiers"].get(tier, []):
                all_tickers.add(t)

    for t in all_tickers:
        if t not in index:
            errors.append(f"ticker {t} 在反向索引中缺失")

    for t in index:
        if t not in all_tickers:
            errors.append(f"反向索引中的 {t} 不在任何子板块中")

    # 4. 检查 ticker 是否唯一（不跨子板块/不跨 tier）
    ticker_to_gics = {}
    for gics_code, info in data["sub_sectors"].items():
        for tier in ["T1", "T2", "T3"]:
            for t in info["tiers"].get(tier, []):
                if t in ticker_to_gics:
                    errors.append(
                        f"ticker {t} 同时出现在子板块 {ticker_to_gics[t]} 和 {gics_code}"
                    )
                ticker_to_gics[t] = gics_code

    return errors


def validate_extended(data: dict, as_of: str = None, strict: bool = False):
    """v2 扩展校验（P0-2）。返回 (errors, warnings, stats)。

    - tier 数量区间：默认 warning（当前池存在文档承认的结构性偏离）；--strict-tiers → error。
      内置例外：T3_LOW_EXCEPTION_GICS 三个子板块允许 T3 低至 2 只（文档"非遗漏"）。
    - meta.total_sub_sectors 与实际不一致 → error（基线失真）。
    - tier*_updated_at 到期：按 as_of（缺省=今天）计算距今天数；
      天数为负（时间戳在未来）→ --as-of 模式 error、生产模式 warning；
      超过刷新周期 → warning（到期提示，不判失败）。
    - previous_snapshot 差集 → 换手率统计（信息项）。
    """
    errors, warnings = [], []
    meta = data.get("meta", {})
    ss = data.get("sub_sectors", {})

    # 参考日：--as-of（回放锚定）或今天
    if as_of:
        try:
            ref = datetime.datetime.strptime(as_of, "%Y-%m-%d").date()
        except ValueError:
            errors.append(f"--as-of 日期格式非法: {as_of}（应为 YYYY-MM-DD）")
            ref = datetime.date.today()
    else:
        ref = datetime.date.today()

    # 1. tier 数量区间（每子板块）
    for gics_code, info in ss.items():
        tiers = info.get("tiers", {})
        for tier, (lo, hi) in TIER_COUNT_RANGE.items():
            n = len(tiers.get(tier, []))
            eff_lo = lo
            if tier == "T3" and gics_code in T3_LOW_EXCEPTION_GICS:
                eff_lo = T3_LOW_EXCEPTION_MIN
            msg = (f"子板块 {gics_code}({info.get('name', '?')}) {tier}={n} 只，"
                   f"低于设计下限 {eff_lo}" if n < eff_lo else
                   (f"子板块 {gics_code}({info.get('name', '?')}) {tier}={n} 只，"
                    f"高于设计上限 {hi}" if n > hi else None))
            if msg:
                (errors if strict else warnings).append(msg + ("（--strict-tiers 判失败）" if not strict else ""))
        if not tiers.get("T3"):
            (errors if strict else warnings).append(
                f"子板块 {gics_code}({info.get('name', '?')}) T3 为空")

    # 2. meta.total_sub_sectors 一致性
    declared = meta.get("total_sub_sectors")
    if declared is not None and declared != len(ss):
        errors.append(f"meta.total_sub_sectors={declared} 与实际子板块数 {len(ss)} 不一致")

    # 3. tier*_updated_at 到期判定
    staleness = {}
    for key, limit in TIER_STALE_DAYS.items():
        raw = str(meta.get(key) or "")[:10]
        try:
            upd = datetime.datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            warnings.append(f"meta.{key} 缺失或不可解析（{raw!r}），无法判定到期")
            continue
        days = (ref - upd).days
        tier_name = key.split("_")[0]
        if days < 0:
            # 时间戳在未来：--as-of 模式（回放锚定）判 error；生产模式 warning
            msg = f"meta.{key}={raw} 在参考日 {ref} 之后 {-days} 天（时间戳在未来）"
            (errors if as_of else warnings).append(msg)
            staleness[tier_name] = {"updated_at": raw, "days": days, "limit": limit, "expired": False}
        else:
            expired = days >= limit
            staleness[tier_name] = {"updated_at": raw, "days": days, "limit": limit, "expired": expired}
            if expired:
                warnings.append(f"{tier_name} 已到期：{key}={raw}，距今 {days} 天 ≥ 刷新周期 {limit} 天")

    # 4. previous_snapshot 差集（换手率，信息项）
    turnover = None
    snap = meta.get("previous_snapshot") or {}
    if snap:
        cur = {"T1": set(), "T2": set(), "T3": set()}
        for info in ss.values():
            for tier in cur:
                cur[tier].update(info.get("tiers", {}).get(tier, []))
        turnover = {}
        snap_key = {"T1": "tier1_tickers", "T2": "tier2_tickers", "T3": "tier3_tickers"}
        for tier in ("T1", "T2", "T3"):
            prev = set(snap.get(snap_key[tier]) or [])
            if not prev:
                continue
            added = sorted(cur[tier] - prev)
            removed = sorted(prev - cur[tier])
            turnover[tier] = {
                "saved_at": snap.get("saved_at"),
                "added": added, "removed": removed,
                "turnover_pct": round(len(removed) / max(1, len(prev)) * 100, 1),
            }

    # 5. conid 覆盖率（P1-2）：池 ticker ↔ vt_symbol_info.json（IB 回测取数前置）
    conid_cov = None
    vt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vt_symbol_info.json")
    if os.path.exists(vt_path):
        try:
            with open(vt_path, "r", encoding="utf-8") as f:
                vt = json.load(f)
            covered = set()
            for sym, info in (vt.get("symbols") or vt or {}).items():
                # 顶层 {vt_symbol: info}；美股 SMART 条目 key=conid.SMART，
                # ticker 在 info["ticker"]；其他市场 key 前缀即代码（AAPL.USA 等）
                if isinstance(info, dict) and info.get("ticker"):
                    covered.add(str(info["ticker"]).upper().replace("-", "."))
                covered.add(str(sym).split(".")[0].upper().replace("-", "."))
            pool_tickers = set()
            for info in ss.values():
                for tier in ("T1", "T2", "T3"):
                    pool_tickers.update(info.get("tiers", {}).get(tier, []))
            missing = sorted(t for t in pool_tickers if t not in covered)
            conid_cov = {"covered": len(pool_tickers) - len(missing),
                         "total": len(pool_tickers), "missing": missing[:60]}
            if missing:
                warnings.append(
                    f"conid 覆盖缺口：{len(missing)}/{len(pool_tickers)} 只池内 ticker 不在 "
                    f"vt_symbol_info.json（走 IB get_history_bars 回测前需 tools/ib_conid_query.py 补齐；"
                    f"仅用 yfinance 评估可忽略）：{', '.join(missing[:12])}{' ...' if len(missing) > 12 else ''}")
        except Exception as e:
            warnings.append(f"vt_symbol_info.json 解析失败，跳过 conid 覆盖率检查: {e}")

    # 统计（与旧版 text 输出同源）
    counts = {tier: sum(len(i.get("tiers", {}).get(tier, [])) for i in ss.values())
              for tier in ("T1", "T2", "T3")}
    stats = {
        "sub_sectors": len(ss),
        "tier_counts": counts,
        "total": sum(counts.values()),
        "empty_t3": sum(1 for i in ss.values() if not i.get("tiers", {}).get("T3")),
        "reference_date": ref.isoformat(),
        "as_of_mode": bool(as_of),
        "staleness": staleness,
        "turnover": turnover,
        "conid_coverage": conid_cov,
    }
    return errors, warnings, stats


def main():
    ap = argparse.ArgumentParser(description="美股选股池数据校验（结构 + v2 扩展校验）")
    ap.add_argument("--pool-file", "--data-file", dest="pool_file", default=_DEFAULT_DATA_FILE,
                    help="选股池 JSON 路径（缺省=同目录 stock_pool.json，生产行为不变）")
    ap.add_argument("--as-of", dest="as_of", default=None, metavar="YYYY-MM-DD",
                    help="回放锚定日：到期判定按该日计算；时间戳晚于该日 → 判 error")
    ap.add_argument("--strict-tiers", action="store_true",
                    help="tier 数量区间违规升级为 error（新池验收；默认 warning）")
    ap.add_argument("--output", choices=["text", "json"], default="text",
                    help="json=机器可读校验证据（供任务书留痕）")
    args = ap.parse_args()

    if args.output != "json":
        print("=" * 60)
        print("美股选股池数据校验工具")
        print("=" * 60)

    # 1. 加载数据
    if args.output != "json":
        print(f"\n[1/4] 加载数据文件: {args.pool_file}")
    data = load_data(args.pool_file)
    if args.output != "json":
        print(f"  ✅ 加载成功")

    # 2. 构建 ticker 索引
    if args.output != "json":
        print("\n[2/4] 构建 ticker 反向索引...")
    build_ticker_index(data)
    total_tickers = len(data["ticker_to_sub_sector"])
    if args.output != "json":
        print(f"  ✅ 共 {total_tickers} 个 ticker")

    # 3. 结构验证（错误级，与旧版一致）
    if args.output != "json":
        print("\n[3/4] 验证 JSON 结构合理性...")
    struct_errors = validate_json(data)
    if struct_errors:
        if args.output == "json":
            print(json.dumps({"校验": "不通过", "错误": struct_errors, "警告": [],
                              "统计": None}, ensure_ascii=False, indent=2))
        else:
            print(f"  ❌ 发现 {len(struct_errors)} 个错误:")
            for e in struct_errors:
                print(f"     - {e}")
        sys.exit(1)
    if args.output != "json":
        print(f"  ✅ 验证通过")

    # 4. v2 扩展校验
    if args.output != "json":
        print("\n[4/4] 扩展校验（tier 数量 / 到期 / 一致性 / 换手）...")
    ext_errors, warnings, stats = validate_extended(
        data, as_of=args.as_of, strict=args.strict_tiers)

    all_errors = struct_errors + ext_errors
    verdict = "通过" if not all_errors else "不通过"

    if args.output == "json":
        print(json.dumps({
            "校验": verdict,
            "pool_file": os.path.abspath(args.pool_file),
            "错误": all_errors,
            "警告": warnings,
            "统计": stats,
        }, ensure_ascii=False, indent=2))
        sys.exit(0 if not all_errors else 1)

    for w in warnings:
        print(f"  ⚠️  {w}")
    if ext_errors:
        print(f"  ❌ 扩展校验 {len(ext_errors)} 个错误:")
        for e in ext_errors:
            print(f"     - {e}")
        print("\n" + "=" * 60)
        print("❌ 校验不通过")
        print("=" * 60)
        sys.exit(1)

    # 统计信息（旧版口径保留）
    counts = stats["tier_counts"]
    print(f"\n  子板块数:     {stats['sub_sectors']}")
    print(f"  Tier 1 龙头:  {counts['T1']} 只")
    print(f"  Tier 2 中盘:  {counts['T2']} 只")
    print(f"  Tier 3 小盘:  {counts['T3']} 只（{stats['empty_t3']} 个子板块 T3 为空）")
    print(f"  总计:         {stats['total']} 只（含 T3；去重后 {total_tickers} 只）")
    print(f"  参考日:       {stats['reference_date']}"
          + ("（--as-of 回放模式）" if stats["as_of_mode"] else ""))
    for tier, st in stats["staleness"].items():
        mark = "已到期" if st["expired"] else "未到期"
        print(f"  {tier}: {st['updated_at']} 距今 {st['days']} 天 / 周期 {st['limit']} 天（{mark}）")
    if stats["turnover"]:
        for tier, t in stats["turnover"].items():
            print(f"  {tier} 换手（vs {t['saved_at']}）: +{len(t['added'])} / -{len(t['removed'])}"
                  f" = {t['turnover_pct']}%")

    print("\n" + "=" * 60)
    print(f"✅ 校验完成！选股池数据有效（数据源: {os.path.abspath(args.pool_file)}）"
          + (f"，{len(warnings)} 条警告" if warnings else ""))
    print("=" * 60)


if __name__ == "__main__":
    main()
