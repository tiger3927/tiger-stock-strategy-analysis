"""
市场数据获取工具 — 统一数据入口，避免 AI 每次临时写脚本

功能：
  1. 按市场类型获取预设数据批次
  2. 支持自定义 ticker 列表
  3. 输出结构化 JSON，含价格、涨跌幅、均线、52 周百分位
  4. 支持缓存写入 Redis（可选）
  5. --fetch-url product-all-info 获取单品种新闻+评级（替代 web_search）
  6. --fetch-url calendar 获取全局经济日历（每小时 1 次，所有策略共享）

用法：
  python get_market_data.py --market us_stocks --batch us-major-indices
  python get_market_data.py --market crypto --batch crypto-majors
  python get_market_data.py --market us_stocks --tickers AAPL,MSFT,GOOGL
  python get_market_data.py --market us_stocks --batch us-major-indices --output json
  python get_market_data.py --fetch-url product-all-info --ticker AAPL --output json
  python get_market_data.py --fetch-url calendar --output json

市场参数：
  us_stocks     美股（默认）
  crypto        加密货币
  china_stocks  中国 A 股
  china_futures 中国期货
  hk_stocks     港股

--fetch-url calendar 依赖安装（仅经济日历需要）：
  pip install -U camoufox[geoip]
  camoufox fetch
--fetch-url product-all-info 仅需标准库 urllib，无需额外安装。
"""
import sys
import os
import json
import argparse
import datetime
import time
import urllib.request
import urllib.error
from datetime import timezone
import math
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yfinance as yf
import requests
from bs4 import BeautifulSoup
import numpy as np


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

# 行业龙头个股（用于美股分析）
US_SECTOR_LEADERS = {
    "NVDA": "英伟达（AI芯片龙头）",
    "MSFT": "微软（AI+软件龙头）",
    "META": "Meta（社交媒体+AI）",
    "GOOG": "谷歌（AI+搜索）",
    "AMZN": "亚马逊（云计算+电商）",
    "SOXX": "半导体ETF",
    "AMD": "AMD（CPU/GPU）",
    "AVGO": "博通（网络/AI芯片）",
    "TSM": "台积电（晶圆代工）",
    "MU": "美光（存储芯片）",
    "JPM": "摩根大通（银行龙头）",
    "GS": "高盛（投行）",
    "BAC": "美国银行",
    "MS": "摩根士丹利",
    "AAPL": "苹果（消费电子）",
}
US_SECTOR_LEADERS_TICKERS = list(US_SECTOR_LEADERS.keys())

# 合并所有 US labels（用于 us-all 超级批次）
US_ALL_LABELS = {}
for d in [US_MAJOR_INDICES, US_MACRO, US_SECTORS, US_STYLE, US_SECTOR_LEADERS]:
    US_ALL_LABELS.update(d)
_seen = set()
US_ALL_TICKERS = []
for group in [US_MAJOR_INDICES_TICKERS, US_MACRO_TICKERS,
              US_SECTORS_TICKERS, US_STYLE_TICKERS, US_SECTOR_LEADERS_TICKERS]:
    for t in group:
        if t not in _seen:
            _seen.add(t)
            US_ALL_TICKERS.append(t)

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

# 合并所有 crypto labels（用于 crypto-all 超级批次）
CRYPTO_ALL_LABELS = {}
for d in [CRYPTO_MAJORS, CRYPTO_ALT_L1, CRYPTO_DEFI, CRYPTO_MEME, CRYPTO_INFRA]:
    CRYPTO_ALL_LABELS.update(d)
_seen_crypto = set()
CRYPTO_ALL_TICKERS = []
for group in [CRYPTO_MAJORS_TICKERS, CRYPTO_ALT_L1_TICKERS,
              CRYPTO_DEFI_TICKERS, CRYPTO_MEME_TICKERS, CRYPTO_INFRA_TICKERS]:
    for t in group:
        if t not in _seen_crypto:
            _seen_crypto.add(t)
            CRYPTO_ALL_TICKERS.append(t)

# 预设批次映射
BATCHES = {
    "us-all":           {"tickers": US_ALL_TICKERS,     "labels": US_ALL_LABELS,     "desc": "美股全量（指数+宏观+板块+风格+龙头）"},
    "us-major-indices": {"tickers": US_MAJOR_INDICES_TICKERS, "labels": US_MAJOR_INDICES, "desc": "美股主要指数"},
    "us-macro":         {"tickers": US_MACRO_TICKERS,             "labels": US_MACRO,             "desc": "美股宏观指标"},
    "us-sectors":       {"tickers": US_SECTORS_TICKERS,           "labels": US_SECTORS,           "desc": "美股板块 ETF"},
    "us-style":         {"tickers": US_STYLE_TICKERS,             "labels": US_STYLE,             "desc": "美股风格 ETF"},
    "crypto-all":      {"tickers": CRYPTO_ALL_TICKERS,   "labels": CRYPTO_ALL_LABELS,   "desc": "加密货币全量（核心+L1+DeFi+Meme+基础设施）"},
    "crypto-majors":    {"tickers": CRYPTO_MAJORS_TICKERS,        "labels": CRYPTO_MAJORS,        "desc": "加密货币核心"},
    "crypto-alt-l1":    {"tickers": CRYPTO_ALT_L1_TICKERS,        "labels": CRYPTO_ALT_L1,        "desc": "L1 公链"},
    "crypto-defi":      {"tickers": CRYPTO_DEFI_TICKERS,          "labels": CRYPTO_DEFI,          "desc": "DeFi 板块"},
    "crypto-meme":      {"tickers": CRYPTO_MEME_TICKERS,          "labels": CRYPTO_MEME,          "desc": "Meme 板块"},
    "crypto-infra":     {"tickers": CRYPTO_INFRA_TICKERS,         "labels": CRYPTO_INFRA,         "desc": "基础设施"},
}

# 市场 → 可用批次
MARKET_BATCHES = {
    "us_stocks":  ["us-all", "us-major-indices", "us-macro", "us-sectors", "us-style"],
    "crypto":     ["crypto-all", "crypto-majors", "crypto-alt-l1", "crypto-defi", "crypto-meme", "crypto-infra"],
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


# ==================== product-all-info / calendar 数据抓取 ====================
#
# 功能：
#   --fetch-url product-all-info: 单品种新闻+评级（替代 2 次 web_search）
#   --fetch-url calendar:        全局经济日历（每小时 1 次，所有策略共享）
#
# 数据源：
#   - 新闻: Yahoo Finance RSS（urllib，无需额外安装）
#   - 评级: MarketBeat JSON-LD（urllib，无需额外安装）
#   - 经济日历: Camoufox + ForexFactory（绕过 Cloudflare）
#
# 安装依赖（仅 calendar 需要）：
#   pip install -U camoufox[geoip]
#   camoufox fetch
#
# 用法：
#   python get_market_data.py --fetch-url product-all-info --ticker AAPL --output json
#   python get_market_data.py --fetch-url calendar --output json
# ============================================================

def _fetch_url(url, headers=None, timeout=15, retries=2):
    """通用 HTTP GET 请求，带重试"""
    default_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    if headers:
        default_headers.update(headers)

    last_error = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=default_headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_error = e
            if attempt < retries:
                time.sleep(1)
    return None


def _clean_html(text):
    """去除 HTML 标签，合并空白"""
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def fetch_news(ticker):
    """
    从 Yahoo Finance RSS 获取近 7 天新闻
    URL: https://finance.yahoo.com/rss/headline?s={ticker}

    预期 RSS 结构:
      <rss><channel>
        <item>
          <title>新闻标题</title>
          <pubDate>Mon, 15 Jun 2026 08:28:56 +0000</pubDate>
          <link>https://...</link>
        </item>
        ...
      </channel></rss>

    故障排查:
      - 返回空: 检查 ticker 是否有效，URL 是否可访问
      - 解析失败: Yahoo 可能改了 RSS 格式，检查 <item> 内标签名
      - 降级: 可改用 yfinance 的 news 属性（stock.news）
    """
    url = f"https://finance.yahoo.com/rss/headline?s={ticker}"
    html = _fetch_url(url)
    if not html:
        return {"count": 0, "items": [], "error": "Yahoo RSS 无响应"}

    news = []
    current = {}
    for line in html.split("\n"):
        line = line.strip()
        if line.startswith("<title>") and "Yahoo Finance" not in line:
            title = line.replace("<title>", "").replace("</title>", "").strip()
            if title:
                current["title"] = title
        elif line.startswith("<pubDate>"):
            current["date"] = line.replace("<pubDate>", "").replace("</pubDate>", "").strip()
        elif line.startswith("<link>") and "</link>" in line:
            current["link"] = line.replace("<link>", "").replace("</link>", "").strip()
        elif line == "</item>":
            if current.get("title"):
                news.append(current)
            current = {}

    return {"count": len(news), "items": news[:10]}


def fetch_ratings(ticker):
    """
    从 MarketBeat 获取机构评级

    URL 规则:
      NASDAQ: https://www.marketbeat.com/stocks/NASDAQ/{ticker}/price-target/
      NYSE:   https://www.marketbeat.com/stocks/NYSE/{ticker}/price-target/

    提取方法（按优先级）:
      方法1: JSON-LD FAQPage → consensus_target, target_high, target_low, analyst_count
        预期 JSON-LD 结构:
        {
          "@type": "FAQPage",
          "mainEntity": [{
            "acceptedAnswer": {
              "text": "$314.59, with a high forecast of $400.00 and a low forecast of $200.00"
            }
          }]
        }
      方法2: HTML 评级分布表格 → ratings_distribution, consensus_score, consensus_rating
      方法3: JSON-LD WebPage description → consensus_target（兜底）

    故障排查:
      - 全部失败: MarketBeat 可能改版了 JSON-LD 结构
      - 方法1 失败: 检查 FAQPage 的 mainEntity 结构
      - 方法2 失败: 检查 ratings_distribution 表格的 HTML 结构
      - 降级: 可改用 TipRanks 或 Yahoo Finance 的评级数据
    """
    url = f"https://www.marketbeat.com/stocks/NASDAQ/{ticker}/price-target/"
    html = _fetch_url(url)
    if not html:
        url = f"https://www.marketbeat.com/stocks/NYSE/{ticker}/price-target/"
        html = _fetch_url(url)

    if not html:
        return {"error": "MarketBeat 无响应"}

    result = {}

    # 方法1: 从 JSON-LD FAQPage 提取
    for m in re.finditer(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        html, re.DOTALL
    ):
        try:
            data = json.loads(m.group(1))
            if isinstance(data, dict) and data.get("@type") == "FAQPage":
                for item in data.get("mainEntity", []):
                    text = item.get("acceptedAnswer", {}).get("text", "")
                    tm = re.search(r'\$?([\d,.]+)\s*,\s*with a high forecast of \$?([\d,.]+)\s*and a low forecast of \$?([\d,.]+)', text)
                    if tm:
                        result["consensus_target"] = float(tm.group(1).replace(",", "").rstrip("."))
                        result["target_high"] = float(tm.group(2).replace(",", "").rstrip("."))
                        result["target_low"] = float(tm.group(3).replace(",", "").rstrip("."))
                    am = re.search(r'(\d+)\s+Wall Street', text)
                    if am:
                        result["analyst_count"] = int(am.group(1))
        except (json.JSONDecodeError, AttributeError):
            continue

    # 方法2: 从 HTML 表格提取评级分布
    tables = re.findall(r'<table[^>]*>(.*?)</table>', html, re.DOTALL)
    for table in tables:
        ratings_dist = {}
        for rating in ["Strong Buy", "Buy", "Hold", "Sell", "Strong Sell"]:
            m = re.search(
                rf'<td[^>]*>{re.escape(rating)}</td><td[^>]*>(\d+)\s*rating',
                table
            )
            if m:
                ratings_dist[rating] = int(m.group(1))
        if ratings_dist:
            result["ratings_distribution"] = ratings_dist

        m = re.search(r'Consensus Rating Score</strong></td><td[^>]*>([\d.]+)', table)
        if m:
            result["consensus_score"] = float(m.group(1))

        m = re.search(r'Consensus Rating</strong></td><td[^>]*>(.*?)</td>', table, re.DOTALL)
        if m:
            result["consensus_rating"] = _clean_html(m.group(1))

    # 方法3: 从 JSON-LD WebPage description 提取
    for m in re.finditer(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        html, re.DOTALL
    ):
        try:
            data = json.loads(m.group(1))
            if isinstance(data, dict) and data.get("@type") == "WebPage":
                desc = data.get("description", "")
                tm = re.search(r'\$?([\d,.]+)', desc)
                if tm and "consensus_target" not in result:
                    result["consensus_target"] = float(tm.group(1).replace(",", ""))
        except (json.JSONDecodeError, AttributeError):
            continue

    return result


def fetch_economic_calendar():
    """
    获取未来一周经济日历

    源1: Camoufox + ForexFactory（主数据源，绕过 Cloudflare）
      URL: https://www.forexfactory.com/calendar
      预期 HTML 结构:
      <table class="calendar__table">
        <tr>
          <td class="calendar__date">Jun15</td>
          <td class="calendar__event">CPI m/m</td>
          <td class="calendar__currency">USD</td>
          <td class="calendar__impact icon--ff-impact-red"></td>
          <td class="calendar__actual">0.2%</td>
          <td class="calendar__forecast">0.3%</td>
          <td class="calendar__previous">0.1%</td>
        </tr>
      </table>

    源2: Fed Calendar（FOMC 会议日期，备用补充）
      URL: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
      预期 HTML 结构:
      <div class="fomc-meeting__month">June</div>
      <div class="fomc-meeting__date">17-18</div>

    故障排查:
      - Camoufox ImportError: 未安装 camoufox，执行 pip install -U camoufox[geoip] && camoufox fetch
      - ForexFactory 返回空: Cloudflare 可能更新了挑战，检查 Camoufox 版本（pip show camoufox）
      - 正则匹配 0 条: ForexFactory 改了 CSS class 名，检查页面实际 class（用浏览器 F12 查看）
      - 超时: 网络问题，检查代理设置或增加 timeout
      - 降级: 自动 fallback 到 urllib 直连（可能被 Cloudflare 拦截，但 Fed Calendar 仍可用）
    """
    results = []

    # 源1: Camoufox + ForexFactory
    try:
        from camoufox.sync_api import Camoufox

        with Camoufox(
            headless=True,
            humanize=True,
            geoip=True,
            block_webrtc=True,
        ) as browser:
            page = browser.new_page()
            page.goto("https://www.forexfactory.com/calendar", timeout=60000)
            page.wait_for_selector("table.calendar__table", timeout=30000)
            page.wait_for_timeout(2000)

            content = page.content()

        dates = re.findall(
            r'<td[^>]*class="[^"]*calendar__date[^"]*"[^>]*>(.*?)</td>',
            content, re.DOTALL
        )
        events = re.findall(
            r'<td[^>]*class="[^"]*calendar__event[^"]*"[^>]*>(.*?)</td>',
            content, re.DOTALL
        )
        currencies = re.findall(
            r'<td[^>]*class="[^"]*calendar__currency[^"]*"[^>]*>(.*?)</td>',
            content, re.DOTALL
        )
        impacts = re.findall(
            r'<td[^>]*class="[^"]*calendar__impact[^"]*"[^>]*>(.*?)</td>',
            content, re.DOTALL
        )
        actuals = re.findall(
            r'<td[^>]*class="[^"]*calendar__actual[^"]*"[^>]*>(.*?)</td>',
            content, re.DOTALL
        )
        forecasts = re.findall(
            r'<td[^>]*class="[^"]*calendar__forecast[^"]*"[^>]*>(.*?)</td>',
            content, re.DOTALL
        )
        previous = re.findall(
            r'<td[^>]*class="[^"]*calendar__previous[^"]*"[^>]*>(.*?)</td>',
            content, re.DOTALL
        )

        for i in range(min(len(events), 50)):
            results.append({
                "date": _clean_html(dates[i]) if i < len(dates) else "?",
                "currency": _clean_html(currencies[i]) if i < len(currencies) else "?",
                "event": _clean_html(events[i]) if i < len(events) else "?",
                "impact": _clean_html(impacts[i]) if i < len(impacts) else "?",
                "actual": _clean_html(actuals[i]) if i < len(actuals) else "-",
                "forecast": _clean_html(forecasts[i]) if i < len(forecasts) else "-",
                "previous": _clean_html(previous[i]) if i < len(previous) else "-",
                "source": "forexfactory"
            })

    except ImportError:
        # Camoufox 未安装，降级尝试 urllib 直连
        url = "https://www.forexfactory.com/calendar.php?month=" + datetime.datetime.now().strftime("%b%Y").lower()
        html = _fetch_url(url, timeout=10)
        if html and len(html) > 1000:
            dates = re.findall(r'<td[^>]*class="[^"]*date[^"]*"[^>]*>(.*?)</td>', html, re.DOTALL)
            events = re.findall(r'<td[^>]*class="[^"]*event[^"]*"[^>]*>(.*?)</td>', html, re.DOTALL)
            currencies = re.findall(r'<td[^>]*class="[^"]*currency[^"]*"[^>]*>(.*?)</td>', html, re.DOTALL)
            for i in range(min(len(events), 50)):
                results.append({
                    "date": _clean_html(dates[i]) if i < len(dates) else "?",
                    "currency": _clean_html(currencies[i]) if i < len(currencies) else "?",
                    "event": _clean_html(events[i]) if i < len(events) else "?",
                    "source": "forexfactory"
                })
    except Exception:
        pass

    # 源2: Fed Calendar (FOMC 会议，备用补充)
    url2 = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
    html = _fetch_url(url2, timeout=10)
    if html:
        fomc_dates = re.findall(
            r'<div[^>]*class="fomc-meeting__date[^"]*"[^>]*>(.*?)</div>',
            html, re.DOTALL
        )
        fomc_months = re.findall(
            r'<div[^>]*class="fomc-meeting__month[^"]*"[^>]*>(.*?)</div>',
            html, re.DOTALL
        )
        for i in range(min(len(fomc_dates), 8)):
            month = _clean_html(fomc_months[i]) if i < len(fomc_months) else ""
            date = _clean_html(fomc_dates[i])
            results.append({
                "date": f"{month} {date}",
                "event": "FOMC Meeting",
                "source": "federalreserve"
            })

    return results


# ==================== 技术指标计算（替代 AI 推理步骤） ====================
#
# 功能：计算 ATR(14)、CCI(14)、三级支撑/压力位、风险收益比、价格分位
# 数据源：yfinance 历史数据
# 用途：集成到 product-all-info 输出，避免 AI 每次手动计算
# ============================================================


def calc_atr(df, period=14):
    """计算 ATR(14)"""
    high = df["High"].values
    low = df["Low"].values
    close = df["Close"].values

    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1])
        )
    )
    atr = float(np.mean(tr[-period:]))
    return round(atr, 4)


def calc_cci(df, period=14):
    """计算 CCI(14)"""
    high = df["High"].values
    low = df["Low"].values
    close = df["Close"].values

    tp = (high + low + close) / 3  # 典型价格
    sma = np.mean(tp[-period:])
    mad = np.mean(np.abs(tp[-period:] - sma))

    if mad == 0:
        return 0.0
    cci = float((tp[-1] - sma) / (0.015 * mad))
    return round(cci, 2)


def find_support_resistance(df, num_levels=3):
    """
    基于近 3 个月价格数据，用直方图密度峰谷检测法找支撑压力位。
    返回 (support_levels, resistance_levels)，各 num_levels 个，由近到远排列。
    """
    close = df["Close"].values
    high = df["High"].values
    low = df["Low"].values

    current_price = close[-1]

    # 用所有 high/low 作为候选点
    all_points = np.concatenate([high, low])
    all_points = np.sort(all_points)

    # 用直方图密度估计找密集区
    hist, edges = np.histogram(all_points, bins=50)

    # 找波峰（密集区 = 支撑/压力位）
    peaks = []
    for i in range(1, len(hist) - 1):
        if hist[i] > hist[i-1] and hist[i] > hist[i+1] and hist[i] > 1:
            peak_price = float((edges[i] + edges[i+1]) / 2)
            peaks.append(peak_price)

    peaks = sorted(peaks)

    # 低于当前价格的为支撑位，高于的为压力位
    supports = [p for p in peaks if p < current_price]
    resistances = [p for p in peaks if p > current_price]

    # 取最近的 num_levels 个
    supports = supports[-num_levels:] if len(supports) >= num_levels else supports
    resistances = resistances[:num_levels] if len(resistances) >= num_levels else resistances

    # 补齐到 num_levels 个（用百分比偏移）
    while len(supports) < num_levels:
        if supports:
            last = supports[0]
            supports.insert(0, round(last * 0.95, 2))
        else:
            supports.append(round(float(current_price) * 0.9, 2))

    while len(resistances) < num_levels:
        if resistances:
            last = resistances[-1]
            resistances.append(round(last * 1.05, 2))
        else:
            resistances.append(round(float(current_price) * 1.1, 2))

    # 取最近的 num_levels 个
    supports = supports[-num_levels:]
    resistances = resistances[:num_levels]

    return [round(float(s), 2) for s in supports], [round(float(r), 2) for r in resistances]


def calc_price_percentile(df, period_days):
    """计算当前价格在指定周期内的分位"""
    close = df["Close"].values
    current = close[-1]
    window = close[-period_days:] if len(close) >= period_days else close
    low, high = float(np.min(window)), float(np.max(window))
    if high == low:
        return 0.5
    return round(float((current - low) / (high - low)), 4)


def fetch_technical_indicators(ticker):
    """
    获取单品种技术指标（ATR、CCI、支撑压力位、风险收益比、价格分位）
    替代 AI 的 ~8 步推理计算
    """
    stock = yf.Ticker(ticker)

    # 近 3 个月日线
    df_3mo = stock.history(period="3mo")
    if df_3mo.empty:
        return {"error": f"无法获取 {ticker} 的 3 个月数据"}

    current_price = round(float(df_3mo["Close"].iloc[-1]), 2)

    # 近 5 日（用于 24h 分位近似）
    df_5d = stock.history(period="5d")
    if df_5d.empty:
        df_5d = df_3mo.tail(5)

    # 计算各项指标
    atr = calc_atr(df_3mo)
    atr_pct = round(atr / current_price * 100, 2) if current_price > 0 else 0
    cci = calc_cci(df_3mo)
    supports, resistances = find_support_resistance(df_3mo)

    # 风险收益比
    risk_reward_long = None
    risk_reward_short = None
    if supports and resistances:
        s1, r1 = supports[-1], resistances[0]
        if (current_price - s1) > 0:
            risk_reward_long = round((r1 - current_price) / (current_price - s1), 2)
        if (r1 - current_price) > 0:
            risk_reward_short = round((current_price - s1) / (r1 - current_price), 2)

    # 价格分位
    percentile_24h = calc_price_percentile(df_5d, min(len(df_5d), 5))
    percentile_3mo = calc_price_percentile(df_3mo, len(df_3mo))

    return {
        "current_price": current_price,
        "atr_14": atr,
        "atr_pct": atr_pct,
        "cci_14": cci,
        "support_levels": supports,
        "resistance_levels": resistances,
        "risk_reward_long": risk_reward_long,
        "risk_reward_short": risk_reward_short,
        "近24h价格分位": percentile_24h,
        "近3月价格分位": percentile_3mo,
    }


def fetch_product_all_info(ticker):
    """
    一站式获取单品种信息（新闻+评级+技术指标）
    替代 2 次 web_search + ~8 步 AI 推理
    """
    result = {
        "ticker": ticker.upper(),
        "fetched_at": datetime.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    result["news"] = fetch_news(ticker)
    result["ratings"] = fetch_ratings(ticker)
    result["technical"] = fetch_technical_indicators(ticker)

    return result


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
                        choices=list(ICI_URLS.keys()) + ["all", "product-all-info", "calendar"],
                        help=f"抓取数据: {', '.join(ICI_URLS.keys())}, all(全部ICI), product-all-info(单品种新闻+评级), 或 calendar(全局经济日历)")
    parser.add_argument("--ticker", type=str, default=None,
                        help="用于 --fetch-url product-all-info 的 ticker，如 AAPL")

    args = parser.parse_args()

    # 抓取数据
    if args.fetch_url:
        if args.fetch_url == "product-all-info":
            if not args.ticker:
                print("❌ --fetch-url product-all-info 需要 --ticker 参数")
                sys.exit(1)
            data = fetch_product_all_info(args.ticker)
            if args.output == "json":
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                print(f"=== {args.ticker.upper()} 信息汇总 ===")
                print(f"\n--- 新闻 ({data['news']['count']} 条) ---")
                for item in data['news']['items'][:5]:
                    print(f"  [{item.get('date','?')}] {item['title'][:80]}")
                print(f"\n--- 评级 ---")
                r = data['ratings']
                if 'error' in r:
                    print(f"  {r['error']}")
                else:
                    print(f"  共识评级: {r.get('consensus_rating', '?')}")
                    print(f"  目标价: ${r.get('consensus_target', '?')}")
                    print(f"  分析师: {r.get('analyst_count', '?')}人")
                print(f"\n--- 技术指标 ---")
                t = data['technical']
                if 'error' in t:
                    print(f"  {t['error']}")
                else:
                    print(f"  当前价格: ${t.get('current_price', '?')}")
                    print(f"  ATR(14): ${t.get('atr_14', '?')} ({t.get('atr_pct', '?')}%)")
                    print(f"  CCI(14): {t.get('cci_14', '?')}")
                    print(f"  三级支撑位: {t.get('support_levels', '?')}")
                    print(f"  三级压力位: {t.get('resistance_levels', '?')}")
                    print(f"  做多风险收益比: {t.get('risk_reward_long', '?')}")
                    print(f"  做空风险收益比: {t.get('risk_reward_short', '?')}")
                    print(f"  近24h价格分位: {t.get('近24h价格分位', '?')}")
                    print(f"  近3月价格分位: {t.get('近3月价格分位', '?')}")
            return

        if args.fetch_url == "calendar":
            data = fetch_economic_calendar()
            if args.output == "json":
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                print(f"=== 全局经济日历 ({len(data)} 条) ===")
                for item in data[:15]:
                    print(f"  {item.get('date','?'):12s} | {item.get('event','?'):45s} | {item.get('source','?')}")
            return

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
