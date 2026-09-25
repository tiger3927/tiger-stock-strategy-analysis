"""
校验加密货币选币池数据（crypto_pool.json）

用法:
    python skills/tiger-stock-strategy-analysis/scripts/sync_crypto_pool.py

原理:
    1. 读取同目录下的 crypto_pool.json（选币池数据）
    2. 校验结构、分类/tier 与 coins 的一致性、排除规则、高弹性占比
    3. 输出统计信息

数据源:
    crypto_pool.json — AI 直接修改此文件即可更新选币池，无需改 Python 代码

不做什么:
    不联网、不校验 yahoo 符号是否真的有效（那需要跑
    `get_market_data.py --market crypto --tickers ...` 批量校验后再置 yahoo_verified=true）
"""
import json
import os
import sys

# 强制 stdout/stderr 使用 UTF-8：避免在 GBK 控制台 / 管道（如 | Select-String）下打印 ✅ 时崩溃
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 数据文件路径（与脚本同目录）
_DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crypto_pool.json")

_VALID_TIERS = ["T1", "T2", "T3"]
_VALID_BUCKETS = ["top10", "11-30", "31-60", "60+"]
_HIGH_BETA_BUCKETS = ["31-60", "60+"]

# 硬排除：稳定币 / 杠杆代币 / 封装币（symbol 前缀或全名匹配）
_HARD_EXCLUDED_SYMBOLS = {
    "USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "BUSD",
    "WBTC", "WETH", "STETH", "WSTETH",
}


def load_data() -> dict:
    """从 crypto_pool.json 加载选币池数据"""
    if not os.path.exists(_DATA_FILE):
        print(f"❌ 找不到数据文件: {_DATA_FILE}")
        print("   请确保 crypto_pool.json 与脚本在同一目录")
        sys.exit(1)

    with open(_DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def validate(data: dict) -> list:
    """校验结构与一致性，返回错误列表"""
    errors = []

    # ---------- 1. meta ----------
    meta = data.get("meta")
    if not isinstance(meta, dict):
        errors.append("缺少 meta 字段")
        meta = {}
    for key in ["version", "tier1_updated_at", "tier2_updated_at", "tier3_updated_at",
                "tradable_source", "vt_symbol_policy", "yahoo_verified_policy",
                "unlock_risk_policy", "pool_size_target", "mcap_buckets", "high_beta_rule"]:
        if key not in meta:
            errors.append(f"meta 缺少字段: {key}")

    # ---------- 2. categories ----------
    categories = data.get("categories")
    if not isinstance(categories, dict) or not categories:
        errors.append("缺少 categories 字段或为空")
        categories = {}
    for cat, tiers in categories.items():
        if not isinstance(tiers, dict):
            errors.append(f"分类 {cat} 不是字典")
            continue
        for tier in _VALID_TIERS:
            if tier not in tiers:
                errors.append(f"分类 {cat} 缺少 tier {tier}")
            elif not isinstance(tiers[tier], list):
                errors.append(f"分类 {cat} tier {tier} 不是列表")

    # ---------- 3. coins ----------
    coins = data.get("coins")
    if not isinstance(coins, dict) or not coins:
        errors.append("缺少 coins 字段或为空")
        coins = {}

    for sym, info in coins.items():
        if not isinstance(info, dict):
            errors.append(f"coins.{sym} 不是字典")
            continue
        for key in ["symbol", "yahoo", "category", "tier", "mcap_bucket", "yahoo_verified"]:
            if key not in info:
                errors.append(f"coins.{sym} 缺少字段: {key}")
        if info.get("symbol") != sym:
            errors.append(f"coins.{sym}.symbol 与键名不一致: {info.get('symbol')}")
        if info.get("category") not in categories:
            errors.append(f"coins.{sym}.category 不在 categories 中: {info.get('category')}")
        if info.get("tier") not in _VALID_TIERS:
            errors.append(f"coins.{sym}.tier 非法: {info.get('tier')}")
        if info.get("mcap_bucket") not in _VALID_BUCKETS:
            errors.append(f"coins.{sym}.mcap_bucket 非法: {info.get('mcap_bucket')}")
        if info.get("yahoo_verified") is not True:
            errors.append(f"coins.{sym}.yahoo_verified 不为 true（未校验的标的不得入池）")

    # ---------- 4. categories 与 coins 双向一致性 + 重复检查 ----------
    seen = {}
    for cat, tiers in categories.items():
        for tier in _VALID_TIERS:
            for sym in tiers.get(tier, []):
                if sym not in coins:
                    errors.append(f"{cat}/{tier} 中的 {sym} 未在 coins 中定义")
                    continue
                if sym in seen:
                    errors.append(f"{sym} 同时出现在 {seen[sym]} 和 {cat}/{tier}")
                seen[sym] = f"{cat}/{tier}"
                # tier 声明一致性
                if coins[sym].get("tier") != tier:
                    errors.append(
                        f"{sym} 在 {cat}/{tier} 中，但 coins.{sym}.tier = {coins[sym].get('tier')}"
                    )

    for sym, info in coins.items():
        if sym not in seen:
            errors.append(f"coins 中的 {sym} 未出现在任何 categories/tier 列表中")

    # ---------- 5. 排除规则（硬排除） ----------
    for sym in coins:
        upper = sym.upper()
        if upper in _HARD_EXCLUDED_SYMBOLS:
            errors.append(f"{sym} 属于硬排除品种（稳定币/封装币）")
        if upper.endswith(("3L", "3S", "5L", "5S")):
            errors.append(f"{sym} 疑似杠杆代币（<BASE>3L/3S/5L/5S），禁止入池")
        # 旧版 Binance 杠杆代币命名为 <BASE>UP / <BASE>DOWN / <BASE>BULL / <BASE>BEAR
        # （如 BTCUP / ETHDOWN）；要求 BASE 至少 2 个字符，避免把 JUP（Jupiter）等正常币种误判
        for _suffix in ("UP", "DOWN", "BULL", "BEAR"):
            if upper.endswith(_suffix) and len(upper) - len(_suffix) >= 2:
                errors.append(f"{sym} 疑似杠杆代币（<BASE>{_suffix}），禁止入池")

    # ---------- 6. 高弹性占比 ----------
    total = len(coins)
    high_beta = sum(1 for info in coins.values() if info.get("mcap_bucket") in _HIGH_BETA_BUCKETS)
    ratio = (high_beta / total * 100) if total else 0.0
    if total and ratio < 20.0:
        errors.append(
            f"高弹性占比 {ratio:.1f}% 低于 20%（高弹性 = mcap_bucket 为 31-60 / 60+）"
        )

    # ---------- 7. 池子规模 ----------
    if total and total < 10:
        errors.append(f"池子仅 {total} 只，低于最低要求 10 只（请按选币操作手册补充）")

    return errors


def main():
    print("=" * 60)
    print("加密货币选币池数据校验工具")
    print("=" * 60)

    print(f"\n[1/3] 加载数据文件: {_DATA_FILE}")
    data = load_data()
    print("  ✅ 加载成功")

    print("\n[2/3] 校验结构 / 一致性 / 排除规则 / 高弹性占比...")
    errors = validate(data)

    coins = data.get("coins", {})
    categories = data.get("categories", {})
    total = len(coins)
    high_beta = sum(1 for i in coins.values() if i.get("mcap_bucket") in _HIGH_BETA_BUCKETS)
    ratio = (high_beta / total * 100) if total else 0.0

    print("\n[3/3] 校验结果:")
    if errors:
        print(f"  ❌ 发现 {len(errors)} 个错误:")
        for e in errors:
            print(f"     - {e}")
    else:
        print("  ✅ 校验通过")

    print("\n  分类数:       %d" % len(categories))
    for cat, tiers in categories.items():
        cnt = sum(len(tiers.get(t, [])) for t in _VALID_TIERS)
        print(f"    - {cat:14s} {cnt} 只")
    print(f"  池子总数:     {total} 只")
    for tier in _VALID_TIERS:
        cnt = sum(len(tiers.get(tier, [])) for tiers in categories.values())
        print(f"  {tier} 数量:      {cnt} 只")
    print(f"  高弹性占比:   {ratio:.1f}% ({high_beta}/{total})  要求 ≥ 20%")

    pending = data.get("pending_verification", {}).get("items", [])
    if pending:
        print(f"  待校验标的:   {len(pending)} 个（{', '.join(p.get('symbol', '?') for p in pending)}）")

    print("\n" + "=" * 60)
    if errors:
        print("❌ 校验未通过！请修正 crypto_pool.json 后重跑（数据源: crypto_pool.json）")
        print("=" * 60)
        sys.exit(1)
    print("✅ 校验完成！选币池数据有效（数据源: crypto_pool.json）")
    print("=" * 60)


if __name__ == "__main__":
    main()
