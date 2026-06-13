"""
市场数据获取工具 — 统一数据入口，避免 AI 每次临时写脚本

功能：
  1. 按市场类型获取预设数据批次
  2. 支持自定义 ticker 列表
  3. 输出结构化 JSON，含价格、涨跌幅、均线、52 周百分位
  4. 支持缓存写入 Redis（可选）

用法：
  python get_market_data.py --market us_stocks --batch us-major-indices
  python get_market_data.py --market crypto --batch crypto-majors
  python get_market_data.py --market us_stocks --tickers AAPL,MSFT,GOOGL
  python get_market_data.py --market us_stocks --batch us-major-indices --output json

市场参数：
  us_stocks     美股（默认）
  crypto        加密货币
  china_stocks  中国 A 股
  china_futures 中国期货
  hk_stocks     港股
"""
import sys
import os
import json
import argparse
import datetime
from datetime import timezone
import math
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yfinance as yf
import requests
from bs4 import BeautifulSoup


# ==================== 预设批次 ====================

US_MAJOR_INDICES = {
    "SPY": "标普500 ETF",
    "QQQ": "纳斯达克100 ETF",
    "DIA": "道琼斯 ETF",
    "IWM": "罗素2000 ETF（小盘股）",
    "RSP": "标普等权 ETF（市场广度）",
    "^VIX": "VIX 恐慌指数",
}
US_MAJOR_INDICES_TICKERS = ["SPY", "QQQ", "DIA", "IWM", "RSP", "^VIX"]

US_MACRO = {
    "^TNX": "美国10年期国债收益率",
    "2YY.F": "美国2年期国债期货（替代 US2Y）",
    "DX-Y.NYB": "美元指数 DXY",
    "SHY": "1-3年国债 ETF（短端利率）",
    "TLT": "20+年国债 ETF（长端利率）",
    "HYG": "高收益债 ETF（信用利差）",
    "LQD": "投资级公司债 ETF（信用利差基准）",
}
US_MACRO_TICKERS = ["^TNX", "2YY.F", "DX-Y.NYB", "SHY", "TLT", "HYG", "LQD"]

US_SECTORS = {
    "XLK": "科技",
    "XLF": "金融",
    "XLV": "医疗",
    "XLI": "工业",
    "XLE": "能源",
    "XLC": "通信",
    "XLY": "可选消费",
    "XLP": "必需消费",
    "XLU": "公用事业",
    "XLB": "材料",
    "XLRE": "房地产",
}
US_SECTORS_TICKERS = list(US_SECTORS.keys())

US_STYLE = {
    "VUG": "成长股 ETF",
    "VTV": "价值股 ETF",
}
US_STYLE_TICKERS = ["VUG", "VTV"]

CRYPTO_MAJORS = {
    "BTC-USD": "比特币",
    "ETH-USD": "以太坊",
}
CRYPTO_MAJORS_TICKERS = ["BTC-USD", "ETH-USD"]

CRYPTO_ALT_L1 = {
    "SOL-USD": "Solana",
    "AVAX-USD": "Avalanche",
    "SUI-USD": "Sui（yfinance 可能无数据，fallback web_search）",
    "NEAR-USD": "NEAR Protocol",
    "APT21794-USD": "Aptos",
}
CRYPTO_ALT_L1_TICKERS = ["SOL-USD", "AVAX-USD", "SUI-USD", "NEAR-USD", "APT21794-USD"]

CRYPTO_DEFI = {
    "AAVE-USD": "Aave",
    "UNI7083-USD": "Uniswap",
    "MKR-USD": "Maker",
    "LDO-USD": "Lido DAO",
    "CRV-USD": "Curve DAO",
}
CRYPTO_DEFI_TICKERS = ["AAVE-USD", "UNI7083-USD", "MKR-USD", "LDO-USD", "CRV-USD"]

CRYPTO_MEME = {
    "DOGE-USD": "Dogecoin",
    "PEPE24478-USD": "Pepe",
    "WIF-USD": "dogwifhat",
    "BONK-USD": "Bonk",
}
CRYPTO_MEME_TICKERS = ["DOGE-USD", "PEPE24478-USD", "WIF-USD", "BONK-USD"]

CRYPTO_INFRA = {
    "LINK-USD": "Chainlink",
    "RENDER-USD": "Render（原 RNDR，已 rebrand）",
    "FET-USD": "Fetch.ai",
    "AR-USD": "Arweave",
}
CRYPTO_INFRA_TICKERS = ["LINK-USD", "RENDER-USD", "FET-USD", "AR-USD"]

# 预设批次映射
BATCHES = {
    "us-major-indices": {"tickers": US_MAJOR_INDICES_TICKERS, "labels": US_MAJOR_INDICES, "desc": "美股主要指数"},
    "us-macro":         {"tickers": US_MACRO_TICKERS,             "labels": US_MACRO,             "desc": "美股宏观指标"},
    "us-sectors":       {"tickers": US_SECTORS_TICKERS,           "labels": US_SECTORS,           "desc": "美股板块 ETF"},
    "us-style":         {"tickers": US_STYLE_TICKERS,             "labels": US_STYLE,             "desc": "美股风格 ETF"},
    "crypto-majors":    {"tickers": CRYPTO_MAJORS_TICKERS,        "labels": CRYPTO_MAJORS,        "desc": "加密货币核心"},
    "crypto-alt-l1":    {"tickers": CRYPTO_ALT_L1_TICKERS,        "labels": CRYPTO_ALT_L1,        "desc": "L1 公链"},
    "crypto-defi":      {"tickers": CRYPTO_DEFI_TICKERS,          "labels": CRYPTO_DEFI,          "desc": "DeFi 板块"},
    "crypto-meme":      {"tickers": CRYPTO_MEME_TICKERS,          "labels": CRYPTO_MEME,          "desc": "Meme 板块"},
    "crypto-infra":     {"tickers": CRYPTO_INFRA_TICKERS,         "labels": CRYPTO_INFRA,         "desc": "基础设施"},
}

# 市场 → 可用批次
MARKET_BATCHES = {
    "us_stocks":  ["us-major-indices", "us-macro", "us-sectors", "us-style"],
    "crypto":     ["crypto-majors", "crypto-alt-l1", "crypto-defi", "crypto-meme", "crypto-infra"],
    "china_stocks":  [],
    "china_futures": [],
    "hk_stocks":     [],
}


def safe_float(v, default=None):
    """安全转 float，None/NaN → default"""
    if v is None:
        return default
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (ValueError, TypeError):
        return default


def calc_52w_percentile(current, low_52w, high_52w):
    """计算 52 周百分位 (0-100)"""
    current = safe_float(current)
    low = safe_float(low_52w)
    high = safe_float(high_52w)
    if current is None or low is None or high is None or high == low:
        return None
    return round((current - low) / (high - low) * 100, 1)


def get_ticker_data(ticker: str) -> dict:
    """
    获取单个 ticker 的完整数据

    Returns:
        dict: {ticker, name, price, change, change_pct, ma_20, ma_50, ma_200,
               high_52w, low_52w, pct_52w, volume, as_of}
    """
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1y")
        if hist.empty:
            return {"ticker": ticker, "error": "无数据", "as_of": datetime.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}

        # 最新价格
        latest = hist.iloc[-1]
        price = safe_float(latest["Close"])
        prev_close = safe_float(hist.iloc[-2]["Close"]) if len(hist) >= 2 else None

        # 涨跌幅
        change = round(price - prev_close, 2) if price is not None and prev_close is not None else None
        change_pct = round((price - prev_close) / prev_close * 100, 2) if price is not None and prev_close is not None and prev_close != 0 else None

        # 均线
        def ma(period):
            if len(hist) >= period:
                return safe_float(hist["Close"].tail(period).mean())
            return None

        ma_20 = ma(20)
        ma_50 = ma(50)
        ma_200 = ma(200)

        # 52 周高低
        high_52w = safe_float(hist["High"].max())
        low_52w = safe_float(hist["Low"].min())
        pct_52w = calc_52w_percentile(price, low_52w, high_52w)

        # 成交量
        volume = int(latest["Volume"]) if not math.isnan(latest["Volume"]) else None

        # 名称
        try:
            info = stock.info
            name = info.get("shortName") or info.get("longName") or ticker
        except Exception:
            name = ticker

        return {
            "ticker": ticker,
            "name": name,
            "price": price,
            "change": change,
            "change_pct": change_pct,
            "ma_20": ma_20,
            "ma_50": ma_50,
            "ma_200": ma_200,
            "high_52w": high_52w,
            "low_52w": low_52w,
            "pct_52w": pct_52w,
            "volume": volume,
            "as_of": datetime.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e), "as_of": datetime.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def fetch_batch(tickers: list, labels: dict = None) -> list:
    """批量获取 ticker 数据"""
    results = []
    for t in tickers:
        data = get_ticker_data(t)
        if labels and t in labels:
            data["label"] = labels[t]
        results.append(data)
    return results


def format_text_output(data: list, title: str = "") -> str:
    """格式化为可读文本"""
    lines = []
    if title:
        lines.append(f"=== {title} ===")
        lines.append("")

    for item in data:
        if "error" in item:
            lines.append(f"  ❌ {item['ticker']}: {item['error']}")
            continue

        ticker = item["ticker"]
        name = item.get("label") or item.get("name", "")
        price = item["price"]
        change = item.get("change")
        change_pct = item.get("change_pct")

        # 涨跌幅符号
        if change is not None and change_pct is not None:
            sign = "+" if change >= 0 else ""
            change_str = f"{sign}{change:.2f} ({sign}{change_pct:.2f}%)"
        else:
            change_str = "N/A"

        label = f"  {ticker:12s}  {price:>10.2f}  {change_str:>18s}" if price is not None else f"  {ticker:12s}  N/A"
        if name:
            label += f"  | {name}"
        lines.append(label)

        # 均线
        ma_parts = []
        for period in ["ma_20", "ma_50", "ma_200"]:
            v = item.get(period)
            if v is not None:
                ma_parts.append(f"{period.replace('ma_', 'MA')}={v:.2f}")
        if ma_parts:
            lines.append(f"  {'':12s}  {'':>10s}  {'':>18s}  {', '.join(ma_parts)}")

        # 52 周百分位
        pct = item.get("pct_52w")
        if pct is not None:
            lines.append(f"  {'':12s}  {'':>10s}  {'':>18s}  52w分位={pct}%  (高={item['high_52w']:.2f}, 低={item['low_52w']:.2f})")

        lines.append("")

    return "\n".join(lines)


# ==================== ICI 数据抓取 ====================

ICI_URLS = {
    "ici-equity-flows": {
        "url": "https://www.ici.org/research/stats/combined_flows",
        "desc": "ICI 股基+ETF 综合净流入（周度）",
    },
    "ici-mmf-assets": {
        "url": "https://www.ici.org/research/stats/mmf",
        "desc": "ICI 货币基金 AUM（周度）",
    },
}


def fetch_ici_table(url: str) -> dict:
    """
    抓取 ICI 页面中的表格数据，提取 Equity 和 Total 行
    """
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # 查找所有表格
        tables = soup.find_all("table")
        result = {"url": url, "fetched_at": datetime.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "tables": []}

        for table_idx, table in enumerate(tables):
            rows = table.find_all("tr")
            table_data = []
            headers = []

            # 提取表头
            th_cells = rows[0].find_all(["th", "td"]) if rows else []
            headers = [h.get_text(strip=True) for h in th_cells]

            # 提取数据行
            for row in rows[1:]:
                cells = row.find_all(["td", "th"])
                row_data = [c.get_text(strip=True) for c in cells]
                if row_data:
                    table_data.append(row_data)

            if headers or table_data:
                result["tables"].append({
                    "table_index": table_idx,
                    "headers": headers,
                    "rows": table_data,
                })

        return result
    except Exception as e:
        return {"url": url, "error": str(e), "fetched_at": datetime.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def format_ici_output(data: dict, source_name: str = "") -> str:
    """格式化 ICI 数据为可读文本"""
    lines = []
    if source_name:
        lines.append(f"=== {source_name} ===")
        lines.append("")

    if "error" in data:
        lines.append(f"  ❌ 抓取失败: {data['error']}")
        return "\n".join(lines)

    lines.append(f"  抓取时间: {data['fetched_at']}")
    lines.append("")

    for tbl in data.get("tables", []):
        headers = tbl.get("headers", [])
        rows = tbl.get("rows", [])

        if headers:
            lines.append(f"  {' | '.join(headers)}")
            lines.append(f"  {'-' * 60}")

        for row in rows[:15]:  # 最多显示 15 行
            lines.append(f"  {' | '.join(row)}")

        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="市场数据获取工具 — 统一数据入口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 美股主要指数
  python get_market_data.py --market us_stocks --batch us-major-indices

  # 美股板块 ETF
  python get_market_data.py --market us_stocks --batch us-sectors --output json

  # 加密货币核心 + L1 公链
  python get_market_data.py --market crypto --batch crypto-majors
  python get_market_data.py --market crypto --batch crypto-alt-l1

  # 自定义 ticker
  python get_market_data.py --market us_stocks --tickers AAPL,MSFT,GOOGL

  # 列出可用批次
  python get_market_data.py --list-batches
        """,
    )

    parser.add_argument("--market", default="us_stocks",
                        choices=["us_stocks", "crypto", "china_stocks", "china_futures", "hk_stocks"],
                        help="市场类型（默认 us_stocks）")
    parser.add_argument("--batch", type=str, default=None,
                        help="预设数据批次名称，如 us-major-indices, crypto-majors")
    parser.add_argument("--tickers", type=str, default=None,
                        help="自定义 ticker 列表，逗号分隔，如 AAPL,MSFT")
    parser.add_argument("--output", choices=["text", "json"], default="text",
                        help="输出格式（默认 text）")
    parser.add_argument("--list-batches", action="store_true",
                        help="列出当前市场可用的预设批次")
    parser.add_argument("--fetch-url", type=str, default=None,
                        choices=list(ICI_URLS.keys()) + ["all"],
                        help=f"抓取 ICI 数据源表格: {', '.join(ICI_URLS.keys())}, 或 all 抓取全部")

    args = parser.parse_args()

    # 抓取 ICI 数据
    if args.fetch_url:
        if args.fetch_url == "all":
            sources = list(ICI_URLS.keys())
        else:
            sources = [args.fetch_url]

        for src in sources:
            info = ICI_URLS[src]
            data = fetch_ici_table(info["url"])
            if args.output == "json":
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                print(format_ici_output(data, source_name=info["desc"]))
        return

    # 列出可用批次
    if args.list_batches:
        print(f"市场: {args.market}")
        print(f"可用批次:")
        batches = MARKET_BATCHES.get(args.market, [])
        if not batches:
            print("  (暂无预设批次)")
        else:
            for b in batches:
                info = BATCHES.get(b, {})
                desc = info.get("desc", b)
                tickers = ", ".join(info.get("tickers", []))
                print(f"  {b:25s}  {desc:20s}  [{tickers}]")
        return

    # 确定 ticker 列表
    tickers = []
    labels = {}

    if args.batch:
        batch_info = BATCHES.get(args.batch)
        if not batch_info:
            print(f"❌ 未知批次: {args.batch}")
            print(f"   可用批次: {', '.join(MARKET_BATCHES.get(args.market, []))}")
            sys.exit(1)
        tickers = batch_info["tickers"]
        labels = batch_info.get("labels", {})

    if args.tickers:
        custom = [t.strip() for t in args.tickers.split(",") if t.strip()]
        tickers.extend(custom)

    if not tickers:
        print("❌ 未指定 ticker。使用 --batch 或 --tickers 指定。")
        print(f"   市场 '{args.market}' 可用批次: {', '.join(MARKET_BATCHES.get(args.market, []))}")
        sys.exit(1)

    # 去重
    seen = set()
    unique_tickers = []
    for t in tickers:
        if t not in seen:
            seen.add(t)
            unique_tickers.append(t)

    # 获取数据
    data = fetch_batch(unique_tickers, labels)

    # 输出
    if args.output == "json":
        result = {
            "market": args.market,
            "batch": args.batch,
            "fetched_at": datetime.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "count": len(data),
            "data": data,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        batch_desc = BATCHES.get(args.batch, {}).get("desc", "") if args.batch else ""
        title = f"[{args.market}] {batch_desc}" if batch_desc else f"[{args.market}] 自定义"
        print(format_text_output(data, title=title))


if __name__ == "__main__":
    main()
