r"""
为美股选股池（stock_pool.json）补充 Tier 3（潜力小盘）候选。

用法:
    python scripts/refill_stock_pool_t3.py                     # 预演（dry-run，不写文件）
    python scripts/refill_stock_pool_t3.py --apply             # 写回选股池 + 自动跑校验
    python scripts/refill_stock_pool_t3.py --only=15105010,50101010    # 只补指定子板块
    python scripts/refill_stock_pool_t3.py --apply --per=5      # 每个子板块补到 5 只
    python scripts/refill_stock_pool_t3.py --refresh            # 忽略行情缓存，全量重取

原理:
    1. 取维基百科 S&P 600（小盘）/ S&P 400（中盘）成分表（自带 GICS Sub-Industry）
    2. 按下方 MAP 把 GICS Sub-Industry 映射到本池的子板块（GICS 代码）
    3. 用 get_market_data.py 批量取数验证：市值 / 价格 / 日均成交额 / 行业
    4. 按 Tier 3 口径筛选（市值 1e9~5e9 美元、价格 >= 3 美元、日均成交额 >= 5e6 美元），
       每个子板块按流动性降序取前 N 只（默认 3 只）
    5. --apply 时写回 stock_pool.json 的 tiers.T3 + meta.tier3_updated_at +
       meta.previous_snapshot（保存旧版摘要，用于换手率统计），并自动运行 sync_stock_pool.py

数据源:
    - 维基百科 S&P 600 / S&P 400 成分表（唯一候选来源，避免凭记忆点名）
    - get_market_data.py（行情与基本面字段）
    - stock_pool.json（选股池本体；只改 T3 与 meta 时间戳，不动 T1/T2）

不做什么:
    - 不联网校验「是否被收购/退市」（由 §5.4 排除规则人工核对；市值/流动性异常会自动剔除）
    - 不修改 T1/T2、不改 gics_hierarchy / name / sector / version
    - 不写任何报告文件（执行报告由定时任务在回答中输出）

注意事项:
    - 依赖 pandas（+ lxml，read_html 用）；二者已随 get_market_data.py 安装
    - GICS Sub-Industry 随时间调整，MAP 与 EXTRA 需在池子子板块变化时同步复核
    - 行情缓存写在 scripts/temp/refill_t3_quotes.json，便于反复预演
"""
import io
import json
import os
import subprocess
import sys
import urllib.request

import pandas as pd

# 强制 stdout/stderr 使用 UTF-8：避免在 GBK 控制台 / 管道（如 | Select-String）下打印中文/符号时崩溃
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
POOL_FILE = os.path.join(SCRIPT_DIR, "stock_pool.json")
GETDATA = os.path.join(SCRIPT_DIR, "get_market_data.py")
SYNC = os.path.join(SCRIPT_DIR, "sync_stock_pool.py")
TEMP_DIR = os.path.join(SCRIPT_DIR, "temp")
QUOTE_CACHE = os.path.join(TEMP_DIR, "refill_t3_quotes.json")
WIKI_CACHE = os.path.join(TEMP_DIR, "refill_t3_wiki.json")
UA = {"User-Agent": "Mozilla/5.0"}

WIKI = {
    "SP600": "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
    "SP400": "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
}

# ---------------- 筛选口径（Tier 3 = 潜力小盘） ----------------
TARGET_TIER = "T3"
PER_SECTOR = 3            # 每个子板块目标只数
CAP_PER_SECTOR = 12       # 每个子板块最多送验的候选数
MCAP_MIN, MCAP_MAX = 1e9, 5e9     # 市值区间（美元）
PRICE_MIN = 3.0                   # 最低股价（排除仙股）
LIQ_MIN = 5e6                     # 日均成交额下限（avg_volume × price）
BATCH = 25                        # 单次 get_market_data 取数个数

# ---------------- 子板块 → GICS Sub-Industry 映射 ----------------
# 键为 stock_pool.json 的 sub_sectors 键（GICS 代码）；按需增删
MAP = {
    "45201020": ["Technology Hardware, Storage & Peripherals", "Electronic Equipment & Instruments",
                 "Electronic Components", "Electronic Manufacturing Services", "Technology Distributors"],
    "40101010": ["Diversified Banks", "Regional Banks"],
    "40101015": ["Regional Banks"],
    "40201010": ["Asset Management & Custody Banks", "Investment Banking & Brokerage", "Consumer Finance",
                 "Financial Exchanges & Data", "Specialized Finance",
                 "Commercial & Residential Mortgage Finance", "Diversified Capital Markets"],
    "40301010": ["Property & Casualty Insurance", "Life & Health Insurance", "Multi-line Insurance",
                 "Reinsurance", "Insurance Brokers"],
    "35101010": ["Health Care Equipment", "Health Care Services", "Health Care Supplies",
                 "Health Care Facilities", "Health Care Technology", "Life Sciences Tools & Services"],
    "25504040": [],
    "25504030": ["Specialty Stores", "Apparel Retail", "Automotive Retail", "Other Specialty Retail",
                 "Computer & Electronics Retail", "Home Improvement Retail"],
    "25101010": ["Automotive Parts & Equipment", "Automobile Manufacturers", "Tires & Rubber"],
    "25201010": ["Apparel, Accessories & Luxury Goods", "Footwear", "Leisure Products", "Home Furnishings",
                 "Housewares & Specialties", "Consumer Electronics"],
    "25301010": ["Restaurants", "Casinos & Gaming", "Hotels, Resorts & Cruise Lines",
                 "Specialized Consumer Services", "Education Services", "Leisure Facilities"],
    "30201010": ["Packaged Foods & Meats", "Soft Drinks & Non-alcoholic Beverages", "Tobacco",
                 "Distillers & Vintners", "Agricultural Products"],
    "30101010": ["Food Retail", "Food Distributors", "Consumer Staples Merchandise Retail"],
    "30301010": ["Household Products", "Personal Care Products"],
    "10102010": ["Oil & Gas Exploration & Production", "Oil & Gas Refining & Marketing",
                 "Oil & Gas Storage & Transportation", "Coal & Consumable Fuels", "Integrated Oil & Gas"],
    "10101010": ["Oil & Gas Equipment & Services", "Oil & Gas Drilling"],
    "20101010": ["Industrial Machinery & Supplies & Components", "Building Products",
                 "Construction & Engineering", "Construction Machinery & Heavy Transportation Equipment",
                 "Aerospace & Defense", "Electrical Components & Equipment", "Heavy Electrical Equipment",
                 "Industrial Conglomerates", "Agricultural & Farm Machinery", "Trading Companies & Distributors"],
    "20301010": ["Cargo Ground Transportation", "Passenger Airlines", "Passenger Ground Transportation",
                 "Air Freight & Logistics", "Marine Transportation", "Railroads"],
    "20201010": ["Diversified Support Services", "Office Services & Supplies", "Environmental & Facilities Services",
                 "Human Resource & Employment Services", "Research & Consulting Services", "Commercial Printing"],
    "15101010": ["Specialty Chemicals", "Commodity Chemicals", "Diversified Chemicals",
                 "Fertilizers & Agricultural Chemicals", "Industrial Gases"],
    "15104010": ["Steel", "Aluminum", "Diversified Metals & Mining", "Gold", "Copper"],
    "15102010": ["Construction Materials"],
    "15103010": ["Metal, Glass & Plastic Containers"],
    "15105010": ["Paper Products", "Forest Products"],
    "55101010": ["Water Utilities", "Electric Utilities", "Multi-Utilities", "Gas Utilities",
                 "Renewable Electricity", "Independent Power Producers"],
    "60101010": ["Retail REITs", "Mortgage REITs", "Hotel & Resort REITs", "Office REITs", "Diversified REITs",
                 "Industrial REITs", "Health Care REITs", "Multi-Family Residential REITs",
                 "Single-Family Residential REITs", "Other Specialized REITs", "Real Estate Services",
                 "Real Estate Development"],
    "50201010": ["Interactive Media & Services", "Movies & Entertainment", "Advertising", "Publishing",
                 "Cable & Satellite", "Broadcasting"],
    "50101010": ["Alternative Carriers", "Wireless Telecommunication Services",
                 "Integrated Telecommunication Services"],
    "45301020": ["Semiconductors", "Semiconductor Materials & Equipment"],
    "45103020": ["Application Software", "Systems Software", "IT Consulting & Other Services"],
    "35202010": ["Pharmaceuticals", "Biotechnology", "Life Sciences Tools & Services"],
}

# 人工补充候选（GICS 子行业承载不了的细分方向）；**优先送验**，仍全部经取数验证
EXTRA = {
    # 电商/互联网零售：GICS 把百货（KSS/BBWI）归入 Broadline Retail，需人工指定真电商
    "25504040": ["RVLV", "LQDT", "FIGS", "PRTS", "QRTEA", "OSTK", "REAL", "ETSY", "CHWY", "TDUP",
                 "EBAY", "W", "LOVE"],
    # 纸与林木（含纸包装 GPK）
    "15105010": ["SLVM", "GPK", "PTVE"],
    # 建筑材料（S&P 600 该类小盘稀少）
    "15102010": ["KNF", "USLM", "EXP"],
    # 电信服务（候选稀少）
    "50101010": ["TDS", "UNIT", "ATNI", "CCOI", "IRDM", "SHEN"],
}


def parse_args():
    opt = {"apply": False, "only": None, "per": PER_SECTOR, "cap": CAP_PER_SECTOR, "refresh": False}
    for a in sys.argv[1:]:
        if a == "--apply":
            opt["apply"] = True
        elif a == "--refresh":
            opt["refresh"] = True
        elif a.startswith("--only="):
            opt["only"] = {x.strip() for x in a.split("=", 1)[1].split(",") if x.strip()}
        elif a.startswith("--per="):
            opt["per"] = int(a.split("=", 1)[1])
        elif a.startswith("--cap="):
            opt["cap"] = int(a.split("=", 1)[1])
    return opt


def load_json(path, default=None):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def fetch_wiki(refresh: bool):
    """取维基百科成分表（含 GICS Sub-Industry），并缓存到 temp"""
    cached = None if refresh else load_json(WIKI_CACHE)
    if cached:
        return cached
    out = {}
    for name, url in WIKI.items():
        html = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read().decode("utf-8")
        df = pd.read_html(io.StringIO(html))[0]
        df.columns = [c.replace(" ", "_") for c in df.columns]
        out[name] = [{"symbol": r["Symbol"], "name": r["Security"], "sub": r["GICS_Sub-Industry"]}
                     for r in df.to_dict("records")]
    os.makedirs(TEMP_DIR, exist_ok=True)
    with open(WIKI_CACHE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    return out


def yahoo_symbol(sym: str) -> str:
    return sym.replace(".", "-")


def build_candidates(pool, wiki, only, cap, per):
    """按子板块组候选：人工清单优先，再按 GICS Sub-Industry 铺开（已达标子板块跳过）"""
    tiers = pool["sub_sectors"]
    existing = {t for info in tiers.values() for tr in ("T1", "T2", "T3") for t in info["tiers"].get(tr, [])}
    by_sub = {}
    for r in wiki["SP600"] + wiki["SP400"]:          # S&P 600 优先（真小盘）
        by_sub.setdefault(r["sub"], []).append(r["symbol"])

    cand, cand_sector, skipped = [], {}, []
    for key, info in tiers.items():
        if only and key not in only:
            continue
        if key not in MAP:
            skipped.append((key, info["name"], "MAP 未定义"))
            continue
        if len(info["tiers"].get(TARGET_TIER, [])) >= per:
            continue                                   # 该子板块已达标，跳过取数
        picked = 0
        for sym in EXTRA.get(key, []):
            if sym in existing or sym in cand_sector:
                continue
            cand.append(sym)
            cand_sector[sym] = (key, "人工补充")
            picked += 1
        for sub in MAP[key]:
            if picked >= cap:
                break
            for sym in by_sub.get(sub, []):
                if sym in existing or sym in cand_sector:
                    continue
                cand.append(sym)
                cand_sector[sym] = (key, sub)
                picked += 1
                if picked >= cap:
                    break
    return cand, cand_sector, existing, skipped


def fetch_quotes(cands, refresh):
    """批量取数（带缓存；失败项重试一次）"""
    os.makedirs(TEMP_DIR, exist_ok=True)
    cache = {} if refresh else (load_json(QUOTE_CACHE) or {})
    todo = [s for s in cands if yahoo_symbol(s) not in cache]
    print(f"  候选 {len(cands)} 只，缓存命中 {len(cands) - len(todo)} 只，需取数 {len(todo)} 只")

    def run(batch):
        p = subprocess.run(
            [sys.executable, GETDATA, "--market", "us_stocks", "--tickers", ",".join(batch), "--output", "json"],
            capture_output=True, text=True, encoding="utf-8",
        )
        try:
            return json.loads(p.stdout).get("data") or []
        except Exception:
            print("    批次解析失败:", (p.stdout or "")[:160], (p.stderr or "")[:160])
            return []

    for i in range(0, len(todo), BATCH):
        rows = run([yahoo_symbol(s) for s in todo[i:i + BATCH]])
        for r in rows:
            cache[r.get("ticker")] = r
        print(f"    批次 {i // BATCH + 1}: 请求 {len(todo[i:i + BATCH])} 只，返回 {len(rows)} 行")

    # 限流重试一次
    failed = [s for s in todo if (cache.get(yahoo_symbol(s)) or {}).get("error")]
    if failed:
        print(f"  限流/失败重试 {len(failed)} 只 ...")
        for i in range(0, len(failed), BATCH):
            rows = run([yahoo_symbol(s) for s in failed[i:i + BATCH]])
            for r in rows:
                if not r.get("error"):
                    cache[r.get("ticker")] = r
    with open(QUOTE_CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    return cache


def select(pool, cand_sector, quotes, per):
    """按 T3 口径筛选，每子板块按流动性降序取前 per 只"""
    tiers = pool["sub_sectors"]
    chosen, rejects = {}, {}
    for sym, (key, sub) in cand_sector.items():
        need = max(0, per - len(tiers[key]["tiers"].get(TARGET_TIER, [])))
        q = quotes.get(yahoo_symbol(sym))
        if not q:
            rejects.setdefault(key, []).append((sym, "取数失败"))
            continue
        if q.get("error"):
            rejects.setdefault(key, []).append((sym, f"取数 error: {q['error']}"))
            continue
        mc, px, av = q.get("market_cap"), q.get("price"), q.get("avg_volume")
        liq = (av or 0) * (px or 0)
        if not mc or mc < MCAP_MIN:
            rejects.setdefault(key, []).append((sym, f"市值 {mc}"))
            continue
        if mc > MCAP_MAX:
            rejects.setdefault(key, []).append((sym, f"市值超上限 {mc / 1e9:.1f}B"))
            continue
        if (px or 0) < PRICE_MIN:
            rejects.setdefault(key, []).append((sym, f"价格 {px}"))
            continue
        if liq < LIQ_MIN:
            rejects.setdefault(key, []).append((sym, f"日均成交额 {liq / 1e6:.1f}M"))
            continue
        chosen.setdefault(key, []).append((sym, mc, liq, px, q.get("industry")))
    for key in list(chosen):
        need = max(0, per - len(tiers[key]["tiers"].get(TARGET_TIER, [])))
        chosen[key].sort(key=lambda x: -x[2])
        chosen[key] = chosen[key][:need]
    return chosen, rejects


def report(pool, chosen, rejects, skipped, per):
    tiers = pool["sub_sectors"]
    print(f"\n=== 补池方案（{TARGET_TIER}）===")
    total = 0
    short = []
    for key in sorted(set(chosen) | set(rejects)):          # 无入选也打印剔除原因，便于排查
        picks = chosen.get(key, [])
        name = tiers[key]["name"]
        cur = len(tiers[key]["tiers"].get(TARGET_TIER, []))
        need = max(0, per - cur)
        detail = " | ".join(f"{s}(市值{mc / 1e9:.1f}B, 成交额{liq / 1e6:.0f}M)" for s, mc, liq, px, ind in picks)
        print(f"{key} {name}（现有 {TARGET_TIER} {cur}，需 {need}）: {detail or '无可用候选'}")
        total += len(picks)
        if len(picks) < need:
            short.append(f"{key} {name}")
        for r in rejects.get(key, []):
            print(f"    剔除 {r[0]}: {r[1]}")
    print(f"\n合计新增: {total} 只；覆盖子板块: {len(chosen)}")
    if short:
        print("未补齐（候选本身不足）:", short)
    if skipped:
        print("跳过（MAP 未定义，未补）:", [f"{k} {n}" for k, n, _ in skipped])


def main():
    opt = parse_args()
    pool = load_json(POOL_FILE)
    if not pool:
        print(f"❌ 找不到 {POOL_FILE}")
        sys.exit(1)

    print("=" * 60)
    print("美股选股池 T3 补池工具" + ("（APPLY 写回）" if opt["apply"] else "（dry-run 预演）"))
    print("=" * 60)

    print("\n[1/5] 取候选来源（维基百科 S&P 600 / 400）...")
    wiki = fetch_wiki(opt["refresh"])
    print(f"  S&P600 {len(wiki['SP600'])} 家 / S&P400 {len(wiki['SP400'])} 家")

    print("\n[2/5] 组候选（人工清单优先 + GICS 子行业铺开）...")
    cand, cand_sector, existing, skipped = build_candidates(pool, wiki, opt["only"], opt["cap"], opt["per"])
    print(f"  候选 {len(cand)} 只，覆盖子板块 {len({v[0] for v in cand_sector.values()})} 个，"
          f"池内已有 {len(existing)} 只不重复")

    print("\n[3/5] 批量取数验证（get_market_data.py）...")
    quotes = fetch_quotes(cand, opt["refresh"])

    print("\n[4/5] 按 T3 口径筛选（市值 $1B-$5B / 价格 ≥ $3 / 日均成交额 ≥ $5M）...")
    chosen, rejects = select(pool, cand_sector, quotes, opt["per"])
    report(pool, chosen, rejects, skipped, opt["per"])

    if not opt["apply"]:
        print("\n[dry-run] 未写回。加 --apply 执行写入。")
        return

    print("\n[5/5] 写回 stock_pool.json ...")
    tiers = pool["sub_sectors"]
    before = {t: sum(len(i["tiers"].get(t, [])) for i in tiers.values()) for t in ("T1", "T2", "T3")}
    for key, picks in chosen.items():
        tiers[key]["tiers"].setdefault(TARGET_TIER, [])
        tiers[key]["tiers"][TARGET_TIER].extend(s[0] for s in picks)
    # previous_snapshot 记录**更新前**的摘要（换手率统计用）
    pool["meta"]["previous_snapshot"] = {
        "saved_at": pool["meta"].get("tier3_updated_at", ""),
        "tier_counts_before": before,
        "tier1_tickers": sorted({x for i in tiers.values() for x in i["tiers"].get("T1", [])}),
        "tier2_tickers": sorted({x for i in tiers.values() for x in i["tiers"].get("T2", [])}),
        "tier3_tickers_before": sorted(
            {x for i in tiers.values() for x in i["tiers"].get("T3", [])}
            - {s[0] for picks in chosen.values() for s in picks}
        ),
    }
    import datetime
    pool["meta"]["tier3_updated_at"] = datetime.date.today().isoformat()
    with open(POOL_FILE, "w", encoding="utf-8") as f:
        json.dump(pool, f, ensure_ascii=False, indent=2)
    after = {t: sum(len(i["tiers"].get(t, [])) for i in tiers.values()) for t in ("T1", "T2", "T3")}
    print(f"  已写入：T1 {before['T1']}→{after['T1']} / T2 {before['T2']}→{after['T2']} / "
          f"T3 {before['T3']}→{after['T3']}，tier3_updated_at={pool['meta']['tier3_updated_at']}")

    print("\n  运行校验脚本 sync_stock_pool.py ...")
    p = subprocess.run([sys.executable, SYNC], capture_output=True, text=True, encoding="utf-8")
    print(p.stdout or p.stderr)
    print("=" * 60)


if __name__ == "__main__":
    main()
