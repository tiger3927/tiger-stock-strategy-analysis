"""
将美股选股池数据写入 Redis（公用 key: /vnpy:美股:板块选股池）

用法:
    E:\veighna_studio_43\python.exe skills/tiger-stock-strategy-analysis/scripts/sync_stock_pool_to_redis.py

原理:
    1. 读取同目录下的 stock_pool.json（选股池数据）
    2. 构建 ticker → GICS 反向索引
    3. 验证 JSON 结构
    4. 通过 Redis Proxy API 写入

数据源:
    stock_pool.json — AI 直接修改此文件即可更新选股池，无需改 Python 代码
"""
import json
import sys
import os
import requests

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


def write_to_redis(data: dict) -> bool:
    """通过 Redis Proxy API 写入数据"""
    # 从同目录的 setting.json 读取配置
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, script_dir)
    try:
        from tools import load_json
    except ImportError:
        setting_path = os.path.join(script_dir, "setting.json")
        if not os.path.exists(setting_path):
            print(f"❌ 找不到 setting.json: {setting_path}")
            return False
        with open(setting_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
    else:
        settings = load_json(os.path.join(script_dir, "setting.json"))

    base_url = settings.get("http_redis_proxy_url", "https://ai4.newgoai.com/")
    api_key = settings.get("http_redis_proxy_apikey", "nokey")
    db = settings.get("http_redis_proxy_db", 11)

    if not base_url.endswith("/"):
        base_url += "/"

    redis_key = "vnpy:美股:板块选股池"
    json_str = json.dumps(data, ensure_ascii=False, indent=2)

    url = f"{base_url}api/v1/redis/set"
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": api_key
    }
    payload = {
        "key": redis_key,
        "value": json_str,
        "db": db,
        "expire_seconds": None
    }

    print(f"写入 Redis...")
    print(f"  Key:    {redis_key}")
    print(f"  DB:     {db}")
    print(f"  URL:    {url}")
    print(f"  数据大小: {len(json_str)} 字符")

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        result = resp.json()
        if result.get("success"):
            print(f"  ✅ 写入成功")
            return True
        else:
            print(f"  ❌ 写入失败: {result}")
            return False
    except Exception as e:
        print(f"  ❌ 请求异常: {e}")
        return False


def main():
    print("=" * 60)
    print("美股选股池 → Redis 同步工具")
    print("=" * 60)

    # 1. 加载数据
    print(f"\n[1/4] 加载数据文件: {_DATA_FILE}")
    data = load_data()
    print(f"  ✅ 加载成功")

    # 2. 构建 ticker 索引
    print("\n[2/4] 构建 ticker 反向索引...")
    build_ticker_index(data)
    total_tickers = len(data["ticker_to_sub_sector"])
    print(f"  ✅ 共 {total_tickers} 个 ticker")

    # 3. 验证 JSON
    print("\n[3/4] 验证 JSON 结构合理性...")
    errors = validate_json(data)
    if errors:
        print(f"  ❌ 发现 {len(errors)} 个错误:")
        for e in errors:
            print(f"     - {e}")
        sys.exit(1)
    print(f"  ✅ 验证通过")

    # 4. 统计信息
    sub_sector_count = len(data["sub_sectors"])
    tier1_count = sum(
        len(info["tiers"]["T1"])
        for info in data["sub_sectors"].values()
    )
    tier2_count = sum(
        len(info["tiers"]["T2"])
        for info in data["sub_sectors"].values()
    )
    print(f"\n  子板块数:     {sub_sector_count}")
    print(f"  Tier 1 龙头:  {tier1_count} 只")
    print(f"  Tier 2 中盘:  {tier2_count} 只")
    print(f"  Tier 3 小盘:  动态更新")
    print(f"  总计:         {total_tickers} 只（不含动态 T3）")

    # 5. 写入 Redis
    print("\n[5/5] 写入 Redis...")
    success = write_to_redis(data)
    if success:
        print("\n" + "=" * 60)
        print("✅ 全部完成！选股池数据已写入 Redis")
        print(f"   Key: /vnpy:美股:板块选股池")
        print(f"   数据源: stock_pool.json")
        print("=" * 60)
    else:
        print("\n❌ 写入失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
