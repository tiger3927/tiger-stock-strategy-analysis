r"""
校验美股选股池数据（stock_pool.json）

用法:
    E:\veighna_studio_43\python.exe skills/tiger-stock-strategy-analysis/scripts/sync_stock_pool.py

原理:
    1. 读取同目录下的 stock_pool.json（选股池数据）
    2. 构建 ticker → GICS 反向索引
    3. 验证 JSON 结构并输出统计

数据源:
    stock_pool.json — AI 直接修改此文件即可更新选股池，无需改 Python 代码
"""
import json
import sys
import os

# 强制 stdout/stderr 使用 UTF-8：避免在 GBK 控制台 / 管道（如 | Select-String）下打印 ✅ 时崩溃
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 数据文件路径（与脚本同目录）
_DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_pool.json")


def load_data() -> dict:
    """从 stock_pool.json 加载选股池数据"""
    if not os.path.exists(_DATA_FILE):
        print(f"❌ 找不到数据文件: {_DATA_FILE}")
        print(f"   请确保 stock_pool.json 与脚本在同一目录")
        sys.exit(1)

    with open(_DATA_FILE, "r", encoding="utf-8") as f:
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
    """验证 JSON 结构的合理性"""
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

    # 4. 检查 ticker 是否唯一（不跨子板块）
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


def main():
    print("=" * 60)
    print("美股选股池数据校验工具")
    print("=" * 60)

    # 1. 加载数据
    print(f"\n[1/3] 加载数据文件: {_DATA_FILE}")
    data = load_data()
    print(f"  ✅ 加载成功")

    # 2. 构建 ticker 索引
    print("\n[2/3] 构建 ticker 反向索引...")
    build_ticker_index(data)
    total_tickers = len(data["ticker_to_sub_sector"])
    print(f"  ✅ 共 {total_tickers} 个 ticker")

    # 3. 验证 JSON
    print("\n[3/3] 验证 JSON 结构合理性...")
    errors = validate_json(data)
    if errors:
        print(f"  ❌ 发现 {len(errors)} 个错误:")
        for e in errors:
            print(f"     - {e}")
        sys.exit(1)
    print(f"  ✅ 验证通过")

    # 统计信息
    sub_sector_count = len(data["sub_sectors"])
    tier1_count = sum(
        len(info["tiers"]["T1"])
        for info in data["sub_sectors"].values()
    )
    tier2_count = sum(
        len(info["tiers"]["T2"])
        for info in data["sub_sectors"].values()
    )
    tier3_count = sum(
        len(info["tiers"]["T3"])
        for info in data["sub_sectors"].values()
    )
    empty_t3 = sum(
        1 for info in data["sub_sectors"].values() if not info["tiers"].get("T3")
    )
    print(f"\n  子板块数:     {sub_sector_count}")
    print(f"  Tier 1 龙头:  {tier1_count} 只")
    print(f"  Tier 2 中盘:  {tier2_count} 只")
    print(f"  Tier 3 小盘:  {tier3_count} 只（{empty_t3} 个子板块 T3 为空）")
    print(f"  总计:         {tier1_count + tier2_count + tier3_count} 只（含 T3；去重后 {total_tickers} 只）")

    print("\n" + "=" * 60)
    print("✅ 校验完成！选股池数据有效（数据源: stock_pool.json）")
    print("=" * 60)


if __name__ == "__main__":
    main()
