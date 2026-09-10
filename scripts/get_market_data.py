"""
市场数据获取工具 — 统一数据入口，避免 AI 每次临时写脚本

功能：
  1. 按市场类型获取预设数据批次
  2. 支持自定义 ticker 列表
  3. 输出结构化 JSON，含价格、涨跌幅、均线、52 周百分位
  4. --fetch-url product-all-info 获取单品种新闻+评级（替代 web_search）
  5. --fetch-url calendar 获取全局经济日历（每小时 1 次，所有策略共享）
  6. --fetch-url web-indicators 获取 14 项市场指标（OrioSearch，替代 AI web_search）

用法：
  python get_market_data.py --market us_stocks --batch us-major-indices
  python get_market_data.py --market crypto --batch crypto-majors
  python get_market_data.py --market us_stocks --tickers AAPL,MSFT,GOOGL
  python get_market_data.py --market us_stocks --batch us-major-indices --output json
  python get_market_data.py --fetch-url product-all-info --ticker AAPL --output json
  python get_market_data.py --fetch-url calendar --output json
  python get_market_data.py --fetch-url web-indicators --output json

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
import html
import hashlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yfinance as yf
import requests
from requests_cache import CachedSession
from bs4 import BeautifulSoup
import numpy as np
import random
import pickle
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

# curl_cffi 可选导入（用于走代理直调 Yahoo chart API，替代被风控的 yfinance 内部请求）
try:
    from curl_cffi import requests as cfr
    _HAS_CURL_CFFI = True
except ImportError:
    _HAS_CURL_CFFI = False


# ==================== 全局常量 ====================

# ---------- 三层缓存架构（理解数据获取与缓存的关键） ----------
# 1) 历史行情缓存（_HISTORY_CACHE_DIR，本次新增）:
#    K线 DataFrame 文件缓存（.pkl），key = {ticker}_{period}，TTL 30 分钟。
#    挂在 get_history_dataframe 统一出口，代理 / yfinance 两条路径都命中。
#    目的: 多个策略在 30 分钟内先后拉取同一批次（如 us-all）时秒回，避免重复请求。
# 2) info 元数据缓存（_INFO_CACHE_DIR，原有）:
#    基本面元数据（名称/PE/市值/行业等）JSON 文件缓存，TTL 12 小时。
#    变化慢，长缓存减少请求。
# 3) requests_cache HTTP 缓存（_CACHE_DB，原有但此前未启用）:
#    yfinance 回落路径的原始 HTTP 响应缓存（sqlite 持久化，跨进程复用）。
#    仅当代理不可用、回落 yfinance 时才起作用。

# requests_cache 持久缓存路径（固定到脚本所在目录）
_CACHE_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "yf_cache_db")

# yfinance info 数据文件缓存（info 无内置缓存，用文件缓存避免重复请求）
_INFO_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".yf_info_cache")
_INFO_CACHE_TTL = 12 * 3600  # 12 小时

# 历史行情 DataFrame 文件缓存（统一出口缓存，代理 / yfinance 路径均命中）
_HISTORY_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".yf_history_cache")
_HISTORY_CACHE_TTL = 1800  # 30 分钟


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


# ==================== 代理检测 + 智能数据获取 ====================
#
# 背景：yfinance 1.3.0 内部请求不走代理（即使传了代理 session 也 429/403），
#       而 Yahoo Finance 对本机直连 IP 做了反爬风控（HTTP 403 / RateLimited）。
# 策略：先探测本机 SOCKS5 代理 (127.0.0.1:10808) 是否可用；
#       可用 → 用 curl_cffi 走 socks5h 直调 Yahoo chart API（返回 DataFrame）；
#       不可用 → 回落到 yfinance 原生调用。
#

# 默认 SOCKS5 代理（Windows 宿主 V2Ray），可用环境变量覆盖
_SOCKS5_DEFAULT = "127.0.0.1:10808"
_SOCKS5_ADDR = os.environ.get("YF_SOCKS5", _SOCKS5_DEFAULT).lstrip("socks5h://").lstrip("socks5://")

# 代理可用性缓存（探测一次即可，30 秒内不重复探测）
_proxy_check_cache = {"time": 0.0, "available": False}
_proxy_check_ttl = 30.0

# Yahoo crumb 缓存（带 cookie 的 curl_cffi Session + crumb，避免每次重新获取）
_proxy_crumb_cache = {"session": None, "crumb": None, "time": 0.0}
_proxy_crumb_ttl = 1800.0  # 30 分钟

# Yahoo chart API 请求头（模拟浏览器，降低风控概率）
_YAHOO_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# 已确认的 Yahoo 查询主机（yfinance 底层，wind 也走这两个）
_YAHOO_HOSTS = ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]


# 周期 → chart API range 参数映射
def _period_to_range(period: str) -> str:
    """把 yfinance 的 period 参数映射为 Yahoo chart API 的 range 参数"""
    m = re.match(r"(\d+)([a-zA-Z]+)", str(period).strip())
    if m:
        num, unit = m.group(1), m.group(2).lower()
        if unit in ("d", "mo", "y"):
            return f"{num}{'d' if unit=='d' else ('mo' if unit=='mo' else 'y')}"
    # 常见命名
    return {
        "1d": "1d", "5d": "5d", "1mo": "1mo", "3mo": "3mo",
        "6mo": "6mo", "1y": "1y", "2y": "2y", "5y": "5y", "max": "max",
    }.get(period, "1y")


def _is_socks_proxy_available() -> bool:
    """探测 127.0.0.1:10808 是否开放（带 30s 缓存）"""
    now = time.time()
    if now - _proxy_check_cache["time"] < _proxy_check_ttl:
        return _proxy_check_cache["available"]

    host, _, port = _SOCKS5_ADDR.partition(":")
    available = False
    try:
        import socket
        s = socket.create_connection((host or "127.0.0.1", int(port or "10808")), timeout=1.5)
        s.close()
        available = True
    except Exception:
        available = False

    _proxy_check_cache["time"] = now
    _proxy_check_cache["available"] = available
    return available


def _make_proxy_cfr_session():
    """构造走 socks5h 代理的 curl_cffi Session"""
    proxy_url = f"socks5h://{_SOCKS5_ADDR}"
    return cfr.Session(proxies={"http": proxy_url, "https": proxy_url}, timeout=15)


def _get_proxy_session_with_crumb():
    """返回(走代理的 curl_cffi Session, Yahoo crumb)。带 30 分钟缓存。"""
    import time as _t
    now = _t.time()
    cache = _proxy_crumb_cache
    if cache["session"] is not None and now - cache["time"] < _proxy_crumb_ttl\
            and cache["crumb"] is not None:
        return cache["session"], cache["crumb"]

    session = _make_proxy_cfr_session()
    session.headers.setdefault("User-Agent", _YAHOO_UA)
    crumb = None
    try:
        # 1. 访问 fc.yahoo.com 拿 A3 cookie
        session.get("https://fc.yahoo.com", timeout=15)
        # 2. 带 cookie 拿 crumb
        r = session.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=15)
        if r.status_code == 200 and r.text.strip():
            crumb = r.text.strip()
    except Exception:
        crumb = None

    cache["session"] = session
    cache["crumb"] = crumb
    cache["time"] = now
    return session, crumb


def _fetch_proxy_json(url: str, params: dict = None) -> dict:
    """
    走 socks5h 代理 + crumb 请求 Yahoo JSON API。
    成功返回 dict，失败返回 None（不抛异常）。
    """
    if not (_HAS_CURL_CFFI and _is_socks_proxy_available()):
        return None
    try:
        session, crumb = _get_proxy_session_with_crumb()
        if crumb is None:
            return None
        if params is None:
            params = {}
        else:
            params = dict(params)
        params["crumb"] = crumb
        resp = session.get(url, params=params, timeout=20)
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:
        return None


def _fetch_proxy_text(url: str, headers: dict = None, timeout: int = 20) -> str:
    """
    走 socks5h 代理的通用 GET（返回文本）。
    失败返回 None。
    """
    if not (_HAS_CURL_CFFI and _is_socks_proxy_available()):
        return None
    try:
        session = _make_proxy_cfr_session()
        if headers:
            session.headers.update(headers)
        else:
            session.headers.setdefault("User-Agent", _YAHOO_UA)
        resp = session.get(url, timeout=timeout)
        if resp.status_code != 200:
            return None
        return resp.text
    except Exception:
        return None


def _fetch_chart_via_proxy(ticker: str, period: str) -> pd.DataFrame:
    """
    用 curl_cffi + socks5 代理直调 Yahoo chart API，返回与 yfinance 兼容的 DataFrame。
    返回列：Date(index, TZ-aware), Open, High, Low, Close, Volume
    """
    rng = _period_to_range(period)
    last_err = None
    for host in _YAHOO_HOSTS:
        url = f"https://{host}/v8/finance/chart/{ticker}"
        try:
            session = _make_proxy_cfr_session()
            resp = session.get(
                url,
                params={"range": rng, "interval": "1d", "includePrePost": "false"},
                headers={"User-Agent": _YAHOO_UA},
            )
            if resp.status_code != 200:
                last_err = f"HTTP {resp.status_code}"
                continue
            data = resp.json()
            result = (data.get("chart") or {}).get("result")
            if not result:
                last_err = "无 result"
                continue
            result = result[0]
            ts = result.get("timestamp") or []
            quote = (result.get("indicators") or {}).get("quote") or [{}]
            quote = quote[0] if quote else {}
            if not ts:
                last_err = "无时间戳"
                continue
            rows = {
                "Date": pd.to_datetime(ts, unit="s"),
                "Open": quote.get("open"),
                "High": quote.get("high"),
                "Low": quote.get("low"),
                "Close": quote.get("close"),
                "Volume": quote.get("volume"),
            }
            df = pd.DataFrame(rows).set_index("Date")
            # 去掉全 NaN / 无收盘价的行（非交易日或停牌）
            df = df.dropna(subset=["Close"])
            if df.empty:
                last_err = "数据为空"
                continue
            return df
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            continue
    raise RuntimeError(f"走代理获取 {ticker} 失败: {last_err}")


def _load_history_cache(ticker: str, period: str):
    """
    读取历史行情文件缓存。

    key = {ticker}_{period}（如 QQQ_1y.pkl），TTL = _HISTORY_CACHE_TTL（30 分钟）。
    命中且未过期返回 DataFrame，否则返回 None（由 get_history_dataframe 重新拉取）。
    缓存文件缺失 / 过期 / 损坏 / 读取异常，一律返回 None，保证不影响正常拉取流程。
    """
    try:
        path = os.path.join(_HISTORY_CACHE_DIR, f"{ticker}_{period}.pkl")
        if not os.path.exists(path):
            return None
        if time.time() - os.path.getmtime(path) > _HISTORY_CACHE_TTL:
            return None
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def _save_history_cache(ticker: str, period: str, df: pd.DataFrame):
    """
    保存历史行情到文件缓存。

    写入采用 tmp 文件 + os.replace 原子替换，避免多线程（fetch_batch 3 并发）
    或多进程并发读写出错导致缓存文件损坏。
    写入失败静默忽略，不影响主流程。
    """
    try:
        os.makedirs(_HISTORY_CACHE_DIR, exist_ok=True)
        path = os.path.join(_HISTORY_CACHE_DIR, f"{ticker}_{period}.pkl")
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            pickle.dump(df, f)
        os.replace(tmp, path)
    except Exception:
        pass


def get_history_dataframe(ticker: str, period: str = "1y", session=None) -> pd.DataFrame:
    """
    统一历史数据入口（带文件缓存）。

    拉取顺序（缓存优先，逐级回落）:
      1. 历史行情文件缓存命中（30 分钟 TTL）→ 直接返回，秒级响应
      2. socks5 代理直调 Yahoo chart API（curl_cffi），结果写入缓存
      3. 回落 yfinance 原生 history（yfinance 1.5.2 不接受 requests_cache 缓存会话，
         由 yfinance 自建 session 实时请求）

    返回与 yfinance history() 兼容的 DataFrame（列 Open/High/Low/Close/Volume）。
    失败抛异常。
    """
    # 1. 文件缓存命中直接返回（多策略短时间重复拉同一批次时秒回）
    cached = _load_history_cache(ticker, period)
    if cached is not None and not cached.empty:
        return cached

    # 2. 优先走代理（可用时），结果写入缓存
    if _HAS_CURL_CFFI and _is_socks_proxy_available():
        try:
            df = _fetch_chart_via_proxy(ticker, period)
            if not df.empty:
                _save_history_cache(ticker, period, df)
            return df
        except Exception:
            pass  # 走代理失败，回落到 yfinance

    # 3. 回落：yfinance 原生。yfinance 1.5.2 明确拒绝带缓存的会话
    #    （"Caching sessions (e.g. requests_cache) are not supported"），
    #    故不向 yfinance 传 session，由 yfinance 自建 session（curl_cffi chrome 指纹）实时请求；
    #    重复拉取去重靠第 1 层文件缓存（30 分钟 TTL）。session 参数仅保留调用链兼容。
    df = yf.Ticker(ticker).history(period=period)
    if not df.empty:
        _save_history_cache(ticker, period, df)
    return df


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


def _get_info_cached(ticker: str) -> dict:
    """带文件缓存的 yfinance info 获取，避免每次实时请求 Yahoo"""
    os.makedirs(_INFO_CACHE_DIR, exist_ok=True)
    cache_key = hashlib.md5(ticker.upper().encode()).hexdigest()
    cache_path = os.path.join(_INFO_CACHE_DIR, f"{cache_key}.json")

    # 缓存命中且未过期
    if os.path.exists(cache_path):
        mtime = os.path.getmtime(cache_path)
        if time.time() - mtime < _INFO_CACHE_TTL:
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass  # 缓存损坏，重新获取

    # 实时获取
    info = None
    # 优先：走 socks5h 代理 + crumb 调 quoteSummary（规避 yfinance info 429 风控）
    if _HAS_CURL_CFFI and _is_socks_proxy_available():
        quote_summary = _fetch_proxy_json(
            "https://query1.finance.yahoo.com/v10/finance/quoteSummary/" + ticker,
            {"modules": "assetProfile,price,summaryDetail,financialData,defaultKeyStatistics"},
        )
        if quote_summary:
            res = ((quote_summary.get("quoteSummary") or {}).get("result") or [None])[0]
        else:
            res = None
        if res:
            info = {}
            sd = res.get("summaryDetail") or {}
            fin = res.get("financialData") or {}
            ap = res.get("assetProfile") or {}
            pr = res.get("price") or {}
            dks = res.get("defaultKeyStatistics") or {}
            for k in ["marketCap", "trailingPE", "priceToBook", "sharesOutstanding",
                      "regularMarketVolume", "floatShares"]:
                v = ((sd.get(k) or {}).get("raw"))
                if v is None:
                    v = ((dks.get(k) or {}).get("raw"))
                info[k] = v
            info["returnOnEquity"] = (fin.get("returnOnEquity") or {}).get("raw")
            info["revenueGrowth"] = (fin.get("revenueGrowth") or {}).get("raw")
            info["totalRevenue"] = (fin.get("totalRevenue") or {}).get("raw")
            info["sector"] = ap.get("sector")
            info["industry"] = ap.get("industry")
            info["shortName"] = (pr.get("shortName") or "")
            info["longName"] = (pr.get("longName") or "")
            info["regularMarketPrice"] = ((pr.get("regularMarketPrice") or {}).get("raw"))
            info["regularMarketPreviousClose"] = ((pr.get("regularMarketPreviousClose") or {}).get("raw"))

    # 回落：yfinance 原生 info（代理不可用或走代理失败时）
    if info is None:
        stock = yf.Ticker(ticker)
        try:
            info = stock.info
        except Exception:
            info = {}

    # 写入缓存（仅写入非空结果）
    if info:
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(info, f, ensure_ascii=False)
        except Exception:
            pass
    return info


def get_ticker_data(ticker: str, session: CachedSession = None) -> dict:
    """
    获取单个 ticker 的完整数据

    Args:
        ticker: 股票代码
        session: 保留参数（yfinance 1.5.2 拒绝 requests_cache 缓存会话，不再传给 yfinance）

    Returns:
        dict: {ticker, name, price, change, change_pct, ma_20, ma_50, ma_200,
               high_52w, low_52w, pct_52w, volume, avg_volume, market_cap,
               shares_outstanding, pe_ratio, pb_ratio, roe, revenue_growth,
               sector, industry, as_of}
    """
    max_retries = 3
    for attempt in range(max_retries):
        try:
            stock = yf.Ticker(ticker)
            hist = get_history_dataframe(ticker, "1y", session=session)
            if hist.empty:
                if attempt < max_retries - 1:
                    wait = (2 ** attempt) + random.uniform(0, 1)
                    time.sleep(wait)
                    continue
                return {"ticker": ticker, "error": "无数据", "as_of": datetime.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}

            # 统一获取 info（带文件缓存，避免重复请求）
            info = _get_info_cached(ticker)

            # 最新价格（含非交易时段 fallback）
            latest = hist.iloc[-1]
            price = safe_float(latest["Close"])

            # 非交易时段 fallback 链：Close 不可用 → info 前收盘 → 前日 Close
            if price is None:
                price = safe_float(info.get("regularMarketPreviousClose")) or \
                        safe_float(info.get("previousClose"))
            if price is None and len(hist) >= 2:
                price = safe_float(hist.iloc[-2]["Close"])

            prev_close = safe_float(hist.iloc[-2]["Close"]) if len(hist) >= 2 else None

            # 涨跌幅（price 来自 fallback 时 change 为 0）
            if price is not None and prev_close is not None:
                change = round(price - prev_close, 2)
                change_pct = round((price - prev_close) / prev_close * 100, 2) if prev_close != 0 else None
            else:
                change = None
                change_pct = None

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

            # 20日均成交量
            avg_volume = int(hist["Volume"].tail(20).mean()) if len(hist) >= 20 else None

            # 名称
            name = info.get("shortName") or info.get("longName") or ticker

            # 基本面数据（从 info 提取，安全兜底）
            market_cap = safe_float(info.get("marketCap"))
            shares_outstanding = safe_float(info.get("sharesOutstanding"))
            pe_ratio = safe_float(info.get("trailingPE"))
            pb_ratio = safe_float(info.get("priceToBook"))
            roe = safe_float(info.get("returnOnEquity"))
            revenue_growth = safe_float(info.get("revenueGrowth"))
            sector = info.get("sector")
            industry = info.get("industry")

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
                "avg_volume": avg_volume,
                "market_cap": market_cap,
                "shares_outstanding": shares_outstanding,
                "pe_ratio": pe_ratio,
                "pb_ratio": pb_ratio,
                "roe": roe,
                "revenue_growth": revenue_growth,
                "sector": sector,
                "industry": industry,
                "as_of": datetime.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        except Exception as e:
            # 遇到 429 限流时指数退避重试
            if "429" in str(e) and attempt < max_retries - 1:
                wait = (2 ** attempt) * 5 + random.uniform(0, 2)
                time.sleep(wait)
                continue
            if attempt == max_retries - 1:
                return {"ticker": ticker, "error": str(e), "as_of": datetime.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
            wait = (2 ** attempt) + random.uniform(0, 1)
            time.sleep(wait)


def _fetch_single(ticker, labels=None, session=None):
    """线程池包装器：获取单个 ticker 数据并附加 label"""
    data = get_ticker_data(ticker, session=session)
    if labels and ticker in labels:
        data["label"] = labels[ticker]
    return data


def fetch_batch(tickers: list, labels: dict = None, cache_ttl: int = 1800) -> list:
    """
    批量获取 ticker 数据（带线程池 + 随机延迟）。

    3 并发线程 + 0.5-1.5s 随机间隔，降低 yfinance 限流概率。
    缓存（三层架构详见文件头部全局常量区注释）:
      - 历史行情 K 线 : get_history_dataframe 内文件缓存，TTL = _HISTORY_CACHE_TTL（30 分钟）
      - info 元数据    : _get_info_cached 文件缓存，TTL = 12 小时
      - yfinance HTTP : yfinance 1.5.2 无 HTTP 响应缓存（拒绝 requests_cache），实时请求
    多个策略在缓存窗口内先后拉取同一批次时直接命中，避免重复请求。

    Args:
        tickers: 需要获取的 ticker 列表
        labels: ticker → 中文名映射（可选），命中时附加到结果
        cache_ttl: 保留参数（yfinance 1.5.2 已无 HTTP 缓存，去重靠文件缓存），默认 1800
    """
    if not tickers:
        return []

    # 注意：yfinance 1.5.2 拒绝 requests_cache 缓存会话（HTTP 层不再缓存），
    # 跨进程去重由 get_history_dataframe 的文件缓存（30 分钟 TTL）承担。
    results = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(_fetch_single, t, labels, None): t for t in tickers}
        for future in as_completed(futures):
            data = future.result()
            results.append(data)
            time.sleep(random.uniform(0.5, 1.5))
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

# OrioSearch 市场指标查询（替代 web_search）
ORIO_URL = "https://search.my-gun.top"

# 查询模板（日期在函数内动态计算）
WEB_INDICATOR_QUERIES_TEMPLATE = {
    "HY_OAS": "high yield OAS FRED current level basis points",
    "MOVE": "MOVE index today value level",
    "FearGreed": "CNN Business fear greed index today",
    "MarginDebt": "FINRA margin debt statistics {year} latest",
    "Buyback": "S&P 500 buyback {year} year to date volume Goldman Sachs",
    "IPO": "US IPO activity {year} total raised year to date",
    "Secondary": "US equity secondary offering {year} year to date",
    "FOMC": "Fed {month_name} {year} rate decision result hawkish or dovish summary statement",
    "CPI": "US consumer price index {prev_month_name} {year} inflation rate",
    "FedWatch": "Fed funds rate probability {month_name} {year} CME",
    "PutCallRatio": "CBOE equity put call ratio today level",
    "Liquidity": "Fed RRP facility balance {month_name} {year} billion",
    "Calendar": "US economic data releases calendar this week {today_str}",
    "Earnings": "big tech earnings calendar {year} Q3 Apple Microsoft NVIDIA",
}


def _clean_incomplete_camoufox_addons():
    """清理 camoufox 数据目录下不完整的 addon 目录（缺少 manifest.json）。

    背景：UBO 等 addon 下载失败（如 addons.mozilla.org 返回 451）时，
    camoufox 会留下空/残缺目录；下次浏览器启动会检测到该目录存在并尝试
    加载，直接报 "manifest.json is missing. Addon path must be a path to an
    extracted addon."，表现为隔次交替失败（一次成功一次失败）。
    每次启动 Camoufox 前调用本函数删除此类不完整目录；目录不存在时
    camoufox 可容忍（跳过该 addon 继续运行）。
    """
    try:
        import shutil
        from camoufox import pkgman
    except Exception:
        return  # camoufox 未安装，无需清理
    try:
        addons_dir = os.path.join(pkgman.INSTALL_DIR, "addons")
        if not os.path.isdir(addons_dir):
            return
        for name in os.listdir(addons_dir):
            addon_path = os.path.join(addons_dir, name)
            if os.path.isdir(addon_path) and not os.path.isfile(
                os.path.join(addon_path, "manifest.json")
            ):
                shutil.rmtree(addon_path, ignore_errors=True)
                print(f"  [camoufox] 已清理不完整 addon 目录: {addon_path}（无 manifest.json）")
    except Exception as e:
        print(f"  [camoufox] 清理不完整 addon 目录失败: {e}")


def fetch_ici_table(url: str) -> dict:
    """
    抓取 ICI 页面中的表格数据，提取 Equity 和 Total 行

    使用 Camoufox 浏览器渲染（ICI 表格为 JS 动态加载，requests 拿不到且被 403 拦截）。
    若本机 SOCKS5 代理(10808)可用，自动走代理，避免 IP 风控。

    安装依赖：
      pip install -U camoufox[geoip]
      camoufox fetch

    故障排查：
      - ImportError: 未安装 camoufox，执行上面两条命令
      - 返回 error: 页面结构变更或代理不可用，用浏览器 F12 检查实际 DOM
    """
    now_utc = datetime.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        from camoufox.sync_api import Camoufox
    except ImportError:
        return {
            "url": url,
            "error": "camoufox 未安装，执行 pip install -U camoufox[geoip] && camoufox fetch",
            "fetched_at": now_utc,
        }

    # 启动前清理不完整的 addon 目录（UBO 下载失败会留空目录，导致下次启动报
    # "manifest.json is missing"），见 _clean_incomplete_camoufox_addons 说明
    _clean_incomplete_camoufox_addons()

    _script_dir = os.path.dirname(os.path.abspath(__file__))
    _user_data_dir = os.path.join(_script_dir, "temp", "browser_data_ici")

    # 若 10808 SOCKS5 代理可用则走代理，否则直连
    proxy_cfg = None
    if _is_socks_proxy_available():
        proxy_cfg = {"server": f"socks5://{_SOCKS5_ADDR}"}

    html_content = None
    last_err = None
    for headless in (True, False):
        try:
            with Camoufox(
                headless=headless,
                humanize=True,
                geoip=True,
                block_webrtc=True,
                persistent_context=True,
                user_data_dir=_user_data_dir,
                proxy=proxy_cfg,
            ) as browser:
                page = browser.new_page()
                page.goto(url, timeout=60000)
                # 等待 JS 渲染出表格
                page.wait_for_selector("table", timeout=30000)
                page.wait_for_timeout(2000)
                html_content = page.content()
                break
        except Exception as e:
            last_err = e
            continue

    if not html_content:
        return {
            "url": url,
            "error": f"Camoufox 抓取失败: {last_err}",
            "fetched_at": now_utc,
        }

    soup = BeautifulSoup(html_content, "html.parser")

    # 查找所有表格
    tables = soup.find_all("table")
    result = {"url": url, "fetched_at": now_utc, "tables": []}

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

    # 优先：走 socks5h 代理（规避直连被 Yahoo/目标站点封锁）
    if _HAS_CURL_CFFI and _is_socks_proxy_available():
        proxied = _fetch_proxy_text(url, headers=default_headers, timeout=timeout)
        if proxied is not None:
            return proxied

    # 回落：urllib 直连
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
    """去除 HTML 标签，合并空白，解码 HTML 实体"""
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text)
    text = html.unescape(text)
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

    # 方法4: 从 HTML <dl> 描述列表提取（部分股票如 XOM 用 <dl> 而非 <table>）
    # 定位 "Price Target and Rating" 区域
    if "consensus_target" not in result or "consensus_rating" not in result:
        m = re.search(r'Price Target and Rating</h2>(.*?)</dl>', html, re.DOTALL)
        if m:
            section = m.group(1)
            # Average Price Target
            tm = re.search(r'Average Price Target[^<]*</dt><dd[^>]*><strong>\$?([\d,.]+)', section)
            if tm and "consensus_target" not in result:
                result["consensus_target"] = float(tm.group(1).replace(",", ""))
            # High Price Target
            tm = re.search(r'High Price Target[^<]*</dt><dd[^>]*><strong>\$?([\d,.]+)', section)
            if tm:
                result["target_high"] = float(tm.group(1).replace(",", ""))
            # Low Price Target
            tm = re.search(r'Low Price Target[^<]*</dt><dd[^>]*><strong>\$?([\d,.]+)', section)
            if tm:
                result["target_low"] = float(tm.group(1).replace(",", ""))
            # Consensus Rating
            tm = re.search(r'Consensus Rating[^<]*</dt><dd[^>]*><strong>([^<]+)', section)
            if tm and "consensus_rating" not in result:
                result["consensus_rating"] = tm.group(1).strip()
            # Rating Score
            tm = re.search(r'Rating Score[^<]*</dt><dd[^>]*><strong>([\d.]+)', section)
            if tm:
                result["consensus_score"] = float(tm.group(1))
            # Research Coverage (analyst count)
            tm = re.search(r'Research Coverage[^<]*</dt><dd[^>]*><strong>(\d+)', section)
            if tm and "analyst_count" not in result:
                result["analyst_count"] = int(tm.group(1))

    # 方法5: 从页面顶部摘要提取（兜底）
    if "consensus_target" not in result:
        m = re.search(r'Price Target</dt><dd><strong>\$?([\d,.]+)</strong></dd>', html)
        if m:
            result["consensus_target"] = float(m.group(1).replace(",", ""))
    if "consensus_rating" not in result:
        m = re.search(r'Consensus Rating</dt><dd><strong>([^<]+)</strong></dd>', html)
        if m:
            result["consensus_rating"] = m.group(1).strip()

    return result


_MONTH_ABBR = {m.lower(): i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def _norm_event_date(raw):
    """将各种事件日期格式归一化为 ISO YYYY-MM-DD，无法解析返回 None。

    支持: '2026-09-10' / '2026-09-10T04:15:00-04:00'（ISO）、
          'Sep10'（FF HTML）、'September 09, 2026'（FRED）
    """
    if not raw:
        return None
    s = str(raw).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return s[:10]
    m = re.match(r"^([A-Za-z]{3,9})\s?(\d{1,2})$", s)
    if m:
        mon = _MONTH_ABBR.get(m.group(1).lower()[:3])
        if mon:
            return f"{datetime.date.today().year:04d}-{mon:02d}-{int(m.group(2)):02d}"
    m = re.match(r"^([A-Za-z]+) (\d{1,2}), (\d{4})$", s)
    if m:
        mon = _MONTH_ABBR.get(m.group(1).lower()[:3])
        if mon:
            return f"{m.group(3)}-{mon:02d}-{int(m.group(2)):02d}"
    return None


def _fetch_ff_json_calendar():
    """源2: ForexFactory 官方公开 JSON（本周事件，无需浏览器、无 Cloudflare）

    URL: https://nfs.faireconomy.media/ff_calendar_thisweek.json
    返回事件列表（仅今天及之后的事件）
    """
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    text = _fetch_url(url, timeout=15)
    if not text:
        return []
    try:
        data = json.loads(text)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    now = datetime.datetime.now(timezone.utc)
    impact_map = {"High": "高", "Medium": "中", "Low": "低"}
    out = []
    for it in data:
        if not isinstance(it, dict):
            continue
        try:
            dt = datetime.datetime.fromisoformat(it.get("date", ""))
        except Exception:
            continue
        if dt < now - datetime.timedelta(hours=2):
            continue  # 只要今天及之后的事件
        out.append({
            "date": dt.strftime("%b%d"),
            "time": dt.strftime("%H:%M"),
            "event": it.get("title", "?"),
            "country": it.get("country", "?"),
            "impact": impact_map.get(it.get("impact", ""), it.get("impact", "?")),
            "forecast": it.get("forecast") or "-",
            "previous": it.get("previous") or "-",
            "source": "forexfactory-json",
        })
    return out


# FRED 日历白名单：只保留对美股宏观分析重要的发布（其余每日例行发布噪音过大）
_FRED_MAJOR_RE = re.compile(
    r"Consumer Price Index|Producer Price Index|Employment Situation|"
    r"Unemployment Insurance Weekly Claims|Personal Income and Spending|"
    r"Advance Economic Indicators|Gross Domestic Product|"
    r"Advance Monthly Sales for Retail|Advance Retail Trade|"
    r"Durable Goods Orders|Industrial Production|Industrial Capacity Utilization|"
    r"Consumer Confidence|Consumer Sentiment|"
    r"Housing Starts|Building Permits|New Residential Construction|Housing Market Indicators|"
    r"Existing Home Sales|FOMC|Trade in Goods|Consumer Credit|Monthly Wholesale Trade",
    re.IGNORECASE,
)


def _fetch_fred_calendar(days=14):
    """源3: FRED 发布日历（今日至 +days 天，仅白名单内的重要美国宏观发布）

    URL: https://fred.stlouisfed.org/releases/calendar?vs=YYYY-MM-DD&ve=YYYY-MM-DD
    按天请求（单天 30-45 条，不触发 50 条分页截断），4 线程并发。
    需要 curl_cffi 模拟浏览器 TLS（urllib 直连超时）；无 curl_cffi 时跳过。
    """
    try:
        from curl_cffi import requests as cr
    except ImportError:
        return []

    today = datetime.date.today()
    urls = [
        f"https://fred.stlouisfed.org/releases/calendar"
        f"?vs={(today + datetime.timedelta(days=i)).isoformat()}"
        f"&ve={(today + datetime.timedelta(days=i)).isoformat()}"
        for i in range(days)
    ]

    def _fetch_one(day_iso_url):
        day, url = day_iso_url
        try:
            r = cr.get(url, impersonate="chrome", timeout=20)
            if r.status_code != 200:
                return []
            t = r.text
        except Exception:
            return []
        rows = re.findall(
            r'<tr>\s*<td[^>]*>([^<]*)</td>\s*<td[^>]*>\s*<a href="/release\?rid=\d+">([^<]+)</a>',
            t, re.DOTALL,
        )
        out = []
        for time_s, name in rows:
            name = _clean_html(name).strip()
            if not _FRED_MAJOR_RE.search(name):
                continue
            # 12 小时制转 24 小时制（"7:30 am" → "07:30"），保证同日排序正确
            time_s = time_s.strip()
            tm = re.match(r"^(\d{1,2}):(\d{2})\s*([ap])m$", time_s, re.IGNORECASE)
            if tm:
                h = int(tm.group(1)) % 12
                if tm.group(3).lower() == "p":
                    h += 12
                time_s = f"{h:02d}:{tm.group(2)}"
            out.append({
                "date": day,
                "time": time_s,
                "event": name,
                "country": "USA",
                "impact": "?",
                "source": "fred",
            })
        return out

    out = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        for res in executor.map(_fetch_one, [(u.split("vs=")[1][:10], u) for u in urls]):
            out.extend(res)
    return out


def fetch_economic_calendar():
    """
    获取未来一周经济日历（多数据源合并，按优先级去重）

    源1: Camoufox + ForexFactory（主数据源，绕过 Cloudflare，含 actual/forecast，覆盖本周+下周）
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

    源2: ForexFactory 官方公开 JSON（本周事件，无需浏览器、无 Cloudflare，源1 失败时的主力兜底）
      URL: https://nfs.faireconomy.media/ff_calendar_thisweek.json
      结构: [{"title","country","date"(ISO 带时区),"impact"(High/Medium/Low),"forecast","previous"}, ...]

    源3: FRED 发布日历（今日至 +14 天，仅白名单内的重要美国宏观发布，如 CPI/PPI/NFP/零售）
      URL: https://fred.stlouisfed.org/releases/calendar?vs=YYYY-MM-DD&ve=YYYY-MM-DD
      需 curl_cffi 模拟浏览器 TLS（urllib 直连超时）；无 curl_cffi 时自动跳过

    源4: Fed Calendar（FOMC 会议日期，备用补充）
      URL: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
      预期 HTML 结构:
      <div class="fomc-meeting__month">June</div>
      <div class="fomc-meeting__date">17-18</div>

    输出: 事件列表（按日期升序，非 ISO 日期排最后，上限 150 条）。
      每项: {date, time?, event, country?, impact?, actual?, forecast?, previous?, source}
      去重: 同事件+同日期只保留优先级高的源（源1 > 源2 > 源3 > 源4）。

    故障排查:
      - Camoufox ImportError: 未安装 camoufox，执行 pip install -U camoufox[geoip] && camoufox fetch
      - ForexFactory 返回空: Cloudflare 可能更新了挑战，检查 Camoufox 版本（pip show camoufox）；
        此时源2 JSON / 源3 FRED 仍可正常提供数据
      - 正则匹配 0 条: ForexFactory 改了 CSS class 名，检查页面实际 class（用浏览器 F12 查看）
      - 超时: 网络问题，检查代理设置或增加 timeout
      - 降级: 自动 fallback 到 urllib 直连（可能被 Cloudflare 拦截，但源2/3/4 仍可用）
    """
    results = []

    # 源1: Camoufox + ForexFactory
    try:
        from camoufox.sync_api import Camoufox

        # 启动前清理不完整的 addon 目录（避免 "manifest.json is missing" 启动失败）
        _clean_incomplete_camoufox_addons()

        _script_dir = os.path.dirname(os.path.abspath(__file__))
        _user_data_dir = os.path.join(_script_dir, "temp", "browser_data")

        # 若 10808 SOCKS5 代理可用则走代理，避免 IP 风控，否则直连
        proxy_cfg = None
        if _is_socks_proxy_available():
            proxy_cfg = {"server": f"socks5://{_SOCKS5_ADDR}"}

        def _try_camoufox(headless: bool):
            """尝试用 Camoufox 抓取 ForexFactory 页面，成功返回 HTML，失败返回 None"""
            try:
                with Camoufox(
                    headless=headless,
                    humanize=True,
                    geoip=True,
                    block_webrtc=True,
                    persistent_context=True,
                    user_data_dir=_user_data_dir,
                    proxy=proxy_cfg,
                ) as browser:
                    page = browser.new_page()
                    page.goto("https://www.forexfactory.com/calendar", timeout=60000)
                    page.wait_for_selector("table.calendar__table", timeout=30000)
                    page.wait_for_timeout(2000)
                    return page.content()
            except Exception:
                return None

        # 先 headless 尝试（复用已保存的 Cookie）
        content = _try_camoufox(headless=True)
        # 失败则 headless=False 重试（弹出浏览器处理 Cloudflare 验证）
        if not content:
            print("  headless 模式被 Cloudflare 拦截，尝试可见浏览器模式...")
            print("  请在弹出的浏览器中完成验证，验证通过后数据将自动获取")
            content = _try_camoufox(headless=False)

        if not content:
            print("  Camoufox 两次尝试均失败，跳过 ForexFactory")
        else:
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

            last_date = ""
            for i in range(min(len(events), 50)):
                raw_date = _clean_html(dates[i]) if i < len(dates) else ""
                if raw_date:
                    last_date = raw_date
                # impact 从 CSS class 中提取：icon--ff-impact-red/ora/yel
                raw_impact = impacts[i] if i < len(impacts) else ""
                impact_class = re.search(r'icon--ff-impact-(\w+)', raw_impact)
                impact_map = {"red": "高", "ora": "中", "yel": "低"}
                impact = impact_map.get(impact_class.group(1), "?") if impact_class else "?"

                results.append({
                    "date": last_date,
                    "currency": _clean_html(currencies[i]) if i < len(currencies) else "?",
                    "event": _clean_html(events[i]) if i < len(events) else "?",
                    "impact": impact,
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
            last_date = ""
            for i in range(min(len(events), 50)):
                raw_date = _clean_html(dates[i]) if i < len(dates) else ""
                if raw_date:
                    last_date = raw_date
                results.append({
                    "date": last_date,
                    "currency": _clean_html(currencies[i]) if i < len(currencies) else "?",
                    "event": _clean_html(events[i]) if i < len(events) else "?",
                    "source": "forexfactory"
                })
    except Exception:
        pass

    # 源2: ForexFactory 官方公开 JSON（本周，无 Cloudflare，源1 失败时的主力兜底）
    ff_json_events = _fetch_ff_json_calendar()
    results.extend(ff_json_events)

    # 源3: FRED 重要发布日历（今日至 +14 天，覆盖下周的关键美国宏观数据）
    fred_events = _fetch_fred_calendar(days=14)
    results.extend(fred_events)

    # 源4: Fed Calendar (FOMC 会议，备用补充)
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

    # 去重: 同事件+同日期只保留先出现的（源优先级: 源1 > 源2 > 源3 > 源4）
    seen = set()
    deduped = []
    for ev in results:
        name = re.sub(r"\s+", " ", str(ev.get("event", ""))).lower()[:40]
        iso = _norm_event_date(ev.get("date", ""))
        key = (name, iso or str(ev.get("date", "")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ev)

    # 排序: ISO 日期升序在前，非 ISO 日期（如 "January 27-28"）排最后
    def _sort_key(ev):
        iso = _norm_event_date(ev.get("date", ""))
        if iso:
            return (0, iso, ev.get("time", ""))
        return (1, "", str(ev.get("date", "")))

    deduped.sort(key=_sort_key)
    return deduped[:150]


# ==================== OrioSearch 市场指标搜索（替代 web_search） ====================
#
# 功能：通过 OrioSearch 获取 14 项市场指标（替代 AI 的 web_search 调用）
# 用法：--fetch-url web-indicators
# 数据源：OrioSearch（https://search.my-gun.top）
# 输出：结构化 JSON/text，含每项指标的 answer、关键数值、耗时
# ============================================================


# OrioSearch 答案"无有效信息"模板句式：搜索结果中没有目标数据时的固定回复
# （2026-09-08 实测 14 项中 11 项为此类回复，若不计入 hit 会高估数据质量）
_NO_ANSWER_PATTERNS = (
    # 允许 markdown 加粗（如 "do **not** include"）等干扰字符
    r"do\s+\**not\**\s+(?:contain|include|provide|have)",
    r"does\s+\**not\**\s+(?:contain|include|provide|have)",
    r"don'?t\s+(?:contain|include|provide|have)",
    r"no explicit", r"not explicitly",
    r"cannot be (?:found|determined|calculated)",
    r"unable to (?:find|determine|locate)",
    r"does not (?:provide|have)",
    r"not available in", r"no data",
    r"无法(?:找到|确定|获取|提供)", r"未能(?:找到|获取)", r"没有(?:相关|找到|提供)",
)
_NO_ANSWER_RE = re.compile("|".join(_NO_ANSWER_PATTERNS), re.IGNORECASE)


def _is_no_answer(text):
    """判断 OrioSearch 答案是否为"无有效信息"模板回复（有返回但非真实数据）"""
    if not text:
        return True
    return bool(_NO_ANSWER_RE.search(text[:300]))


def fetch_web_indicators():
    """
    通过 OrioSearch 获取全部 14 项市场指标。

    查询词使用 WEB_INDICATOR_QUERIES_TEMPLATE 模板，
    日期参数在调用时动态计算，避免硬编码。

    Returns:
        dict: {
            "source": "oriosearch",
            "fetched_at": "...",
            "total": 14,
            "hit": 13,            # 有效命中数（不含 no_answer 与 失败）
            "time_s": 1.23,
            "indicators": {
                "HY_OAS": {"query":"...", "answer":"...", "value":"263 bps",
                           "source":"oriosearch", "time_s":0.09},
                # source 取值:
                #   "oriosearch"  有返回且含有效数据（计入 hit）
                #   "no_answer"   有返回但为"无有效信息"模板回复（不计入 hit）
                #   "失败"        接口无返回/超时
            }
        }
    """
    now = datetime.datetime.now(timezone.utc)
    year = now.year
    month = now.month
    month_name = now.strftime("%B")
    prev_month = month - 1 if month > 1 else 12
    prev_month_name = datetime.datetime(year if month > 1 else year - 1, prev_month, 1).strftime("%B")
    today_str = now.strftime("%B %d %Y")

    # 渲染查询词
    queries = {}
    for key, tmpl in WEB_INDICATOR_QUERIES_TEMPLATE.items():
        queries[key] = tmpl.format(year=year, month_name=month_name,
                                    prev_month_name=prev_month_name, today_str=today_str)

    result = {
        "source": "oriosearch",
        "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total": len(queries),
        "hit": 0,
        "time_s": 0.0,
        "indicators": {},
    }

    # 并发发送所有请求（总耗时 ≈ 最慢的单个请求，而非 14 个之和）
    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=len(queries)) as executor:
        fut_map = {}
        for k, q in queries.items():
            t0 = time.time()
            fut = executor.submit(_search_oriosearch, q)
            fut_map[fut] = (k, t0)
        for fut in as_completed(fut_map):
            key, t0 = fut_map[fut]
            answer = fut.result()
            t = time.time() - t0
            result["time_s"] += t

            if answer and _is_no_answer(answer):
                # 有返回但属"无有效信息"模板句 → 单独标记，不计入 hit
                val = "N/A(无有效答案)"
                src = "no_answer"
            elif answer:
                result["hit"] += 1
                val = _extract_indicator_value(answer)
                src = "oriosearch"
            else:
                val = "N/A"
                src = "失败"

            result["indicators"][key] = {
                "query": queries[key],
                "answer": (answer or "")[:300],
                "value": val,
                "source": src,
                "time_s": round(t, 2),
            }

    result["time_s"] = round(result["time_s"], 2)
    return result


def _search_oriosearch(query):
    """OrioSearch 单次搜索，返回答案文本（失败返回 None）

    策略：
      1. 优先取 answer 字段（有 AI 摘要时最快）
      2. answer 为 None 时，从 results 列表中提取前 3 条的 title+content
         作为推算文本，这样即使 OrioSearch 不生成 AI 摘要也能拿到有用数据
      3. advanced 超时 → 降级 basic 重试
    """
    payload = {
        "query": query,
        "search_depth": "advanced",
        "max_results": 5,
        "include_answer": True,
    }

    def _try_request(payload_copy):
        try:
            r = requests.post(f"{ORIO_URL}/search", json=payload_copy, timeout=(5, 90))
            if r.status_code != 200:
                return None
            data = r.json()
            # 优先 answer
            ans = data.get('answer', '') or ''
            if ans.strip():
                return ans.strip()
            # answer 为 None/空 → 从 results 提取全部内容
            results = data.get('results', [])
            if results:
                fragments = []
                for i, res in enumerate(results[:5]):  # 取前5条够用且不乱
                    title = res.get('title', '').strip()
                    content = res.get('content', '').strip() or ''
                    # content 是 Meta Description（短摘要）而非 raw_content（全文）
                    # 所以截取 500 字符是安全的
                    snippet = content[:500].strip() if content else ''
                    if title:
                        if snippet:
                            fragments.append(f"{title}: {snippet}")
                        else:
                            fragments.append(title)
                if fragments:
                    return "\n".join(fragments)
            return None
        except requests.ReadTimeout:
            return "__TIMEOUT__"
        except Exception:
            return None

    # 先 advanced
    result = _try_request(payload)
    if result == "__TIMEOUT__":
        # advanced 超时 → 降级 basic 重试
        payload["search_depth"] = "basic"
        result = _try_request(payload)
    # __TIMEOUT__ 视为失败
    return result if result != "__TIMEOUT__" else None


def _extract_indicator_value(answer):
    """从 answer 中提取关键数值（用于展示）"""
    if not answer:
        return "N/A"
    # 匹配金额($X.XXT/B/M)、百分比(X.XX%)、bps、纯数字+单位
    nums = re.findall(
        r'[\$]?[\d,]+\.?\d*\s*(?:%|bps|trillion|billion|million|T|B|M)?',
        answer[:400],
    )

    def is_junk(s):
        s = s.strip().rstrip(",")
        if not s or len(s) <= 2:
            return True
        if re.match(r'^\d{4}$', s):
            return True
        if s.endswith('.'):
            return True
        return False

    cleaned = [n.strip().rstrip(",") for n in nums if not is_junk(n)]
    cleaned = [n for n in cleaned if n]

    # 去重（保持顺序）
    seen = set()
    unique = []
    for n in cleaned:
        if n not in seen:
            seen.add(n)
            unique.append(n)

    if unique:
        return ", ".join(unique[:5])
    return answer[:150].replace("\n", " ").strip() + ("..." if len(answer) > 150 else "")


def _format_web_indicators_text(data: dict) -> str:
    """格式化市场指标结果为可读文本"""
    lines = []
    lines.append("=== 市场指标 (OrioSearch) ===")
    lines.append("")
    lines.append(f"  {'指标':<15} {'来源':<12} {'耗时':<8} {'关键数值'}")
    lines.append(f"  {'-'*15} {'-'*12} {'-'*8} {'-'*35}")
    inds = data.get("indicators", {})
    for key in WEB_INDICATOR_QUERIES_TEMPLATE:
        info = inds.get(key, {})
        src = info.get("source", "?")
        t = info.get("time_s", 0)
        val = info.get("value", "N/A")
        lines.append(f"  {key:<15} {src:<12} {t:<8.2f} {val}")
    lines.append("")
    lines.append(f"  命中: {data['hit']}/{data['total']} | 总耗时: {data['time_s']}s")
    return "\n".join(lines)


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

    # 过滤 NaN
    mask = ~(np.isnan(high) | np.isnan(low) | np.isnan(close))
    high = high[mask]
    low = low[mask]
    close = close[mask]

    if len(high) < period + 1:
        return 0.0

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

    # 过滤 NaN
    mask = ~(np.isnan(high) | np.isnan(low) | np.isnan(close))
    high = high[mask]
    low = low[mask]
    close = close[mask]

    if len(high) < period:
        return 0.0

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

    # 过滤 NaN，防止 np.histogram 报错
    if np.isnan(high).all() or np.isnan(low).all():
        return [], []

    high = high[~np.isnan(high)]
    low = low[~np.isnan(low)]
    close = close[~np.isnan(close)]

    if len(high) == 0 or len(low) == 0 or len(close) == 0:
        return [], []

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
    close = close[~np.isnan(close)]
    if len(close) < 2:
        return 0.5
    current = close[-1]
    window = close[-period_days:] if len(close) >= period_days else close
    low, high = float(np.min(window)), float(np.max(window))
    if high == low:
        return 0.5
    return round(float((current - low) / (high - low)), 4)


def fetch_technical_indicators(ticker):
    """
    获取单品种技术指标（ATR、CCI、支撑压力位、MA、成交量、风险收益比、价格分位）
    替代 AI 的 ~8 步推理计算
    """
    stock = yf.Ticker(ticker)

    # 近 3 个月日线（用于 ATR, CCI, 支撑压力位）
    df_3mo = get_history_dataframe(ticker, "3mo")
    if df_3mo.empty:
        return {"error": f"无法获取 {ticker} 的 3 个月数据"}

    # 近 1 年日线（用于均线计算，MA200 需要约 200 个交易日）
    df_1y = get_history_dataframe(ticker, "1y")
    if df_1y.empty:
        df_1y = df_3mo

    # 过滤 Close 列中的 NaN
    close_vals = df_3mo["Close"].values
    close_vals = close_vals[~np.isnan(close_vals)]
    if len(close_vals) == 0:
        return {"error": f"{ticker} 的 Close 数据全为 NaN"}
    current_price = round(float(close_vals[-1]), 2)

    # 近 5 日（用于 24h 分位近似）
    df_5d = get_history_dataframe(ticker, "5d")
    if df_5d.empty:
        df_5d = df_3mo.tail(5)

    # 计算均线
    def ma(df, period):
        if len(df) >= period:
            vals = df["Close"].tail(period).dropna().values
            if len(vals) > 0:
                return round(float(vals.mean()), 2)
        return None

    ma_20 = ma(df_1y, 20)
    ma_50 = ma(df_1y, 50)
    ma_200 = ma(df_1y, 200)

    # 成交量
    latest = df_1y.iloc[-1]
    volume = int(latest["Volume"]) if not math.isnan(float(latest["Volume"])) else None

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
        "ma_20": ma_20,
        "ma_50": ma_50,
        "ma_200": ma_200,
        "volume": volume,
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


def search_yfinance_ticker(query, max_candidates=5):
    """
    综合搜索 yfinance ticker
    输入: 字符串（可能是 vt_symbol、公司名称、公司代称、加密货币名称等）
    输出: {
        "query": str,         # 原始查询
        "matched": bool,      # 是否匹配成功
        "ticker": str,        # 匹配到的 yahoo ticker
        "name": str,          # 品种名称
        "exchange": str,      # 交易所
        "quote_type": str,    # 类型（EQUITY/CRYPTOCURRENCY/ETF等）
        "source": str,        # 匹配来源: vt_symbol_info / yf_info_verify / yf_search
        "candidates": [...],  # 候选列表
    }

    搜索策略（按优先级，每步失败自动 fallback 到下一步）:
      1. vt_symbol_info.json 查表（vt_symbol key + ticker 字段 + name 字段）
      2. yf.Ticker(query).info 验证（直接当 yahoo ticker 用）
      3. yf.Search() 模糊搜索兜底

    每步都包裹 try/except，避免中间步骤异常中断整个搜索流程。
    """
    # vt_symbol_info.json 路径（相对于当前脚本）
    _vt_info_path = os.path.join(os.path.dirname(__file__), "vt_symbol_info.json")

    result = {
        "query": query,
        "matched": False,
        "ticker": None,
        "name": None,
        "exchange": None,
        "quote_type": None,
        "source": None,
        "candidates": [],
    }

    query_lower = query.strip().lower()

    # ========== 步骤 1: vt_symbol_info.json 查表 ==========
    # 同时匹配 vt_symbol key（如 265598.SMART）、ticker 字段（如 AAPL）、name 字段（如 Apple）
    try:
        with open(_vt_info_path, "r", encoding="utf-8") as f:
            vt_info = json.load(f)

        for vt_key, info in vt_info.items():
            ticker_in_file = info.get("ticker", "").lower()
            name_in_file = info.get("name", "").lower()
            name_cn = info.get("name_cn", "").lower()
            vt_key_lower = vt_key.lower()

            # 匹配: vt_symbol 完整值、vt_symbol 不含后缀（如 265598）、ticker、name、name_cn
            vt_key_no_suffix = vt_key_lower.split(".")[0] if "." in vt_key_lower else vt_key_lower

            if (query_lower == ticker_in_file
                    or query_lower == name_in_file
                    or query_lower == name_cn
                    or query_lower == vt_key_lower
                    or query_lower == vt_key_no_suffix):
                result["matched"] = True
                result["ticker"] = info["ticker"]
                result["name"] = info.get("name_cn") or info.get("name", "")
                result["exchange"] = "NMS" if "SMART" in vt_key.upper() else info.get("exchange", "")
                result["quote_type"] = info.get("quote_type", "EQUITY")
                result["source"] = "vt_symbol_info"
                # 添加候选
                result["candidates"] = [{
                    "symbol": info["ticker"],
                    "name": info.get("name_cn") or info.get("name", ""),
                    "exchange": result["exchange"],
                    "quote_type": result["quote_type"],
                }]
                return result
    except Exception as e:
        # vt_symbol_info.json 读取失败不中断，继续下一步
        pass

    # ========== 步骤 2: yf.Ticker(query).info 验证 ==========
    # 把 query 直接当作 yahoo ticker，用 info 快速验证是否存在
    try:
        t = yf.Ticker(query.strip().upper())
        info = t.info
        # info 为空或缺少关键字段说明不是有效 ticker
        if info and info.get("symbol") and info.get("shortName"):
            result["matched"] = True
            result["ticker"] = info["symbol"]
            result["name"] = info.get("shortName", "")
            result["exchange"] = info.get("exchange", "")
            result["quote_type"] = info.get("quoteType", "")
            result["source"] = "yf_info_verify"
            result["candidates"] = [{
                "symbol": info["symbol"],
                "name": info.get("shortName", ""),
                "exchange": info.get("exchange", ""),
                "quote_type": info.get("quoteType", ""),
            }]
            return result
    except Exception:
        # yf.Ticker 初始化失败或 info 获取失败，不中断，继续下一步
        pass

    # ========== 步骤 3: yf.Search() 模糊搜索兜底 ==========
    try:
        s = yf.Search(query.strip())
        quotes = s.quotes if s.quotes else []

        if quotes:
            # 过滤出 EQUITY 和 CRYPTOCURRENCY 类型，优先取第一条
            preferred = [q for q in quotes if q.get("quoteType") in ("EQUITY", "CRYPTOCURRENCY")]
            all_candidates = preferred + [q for q in quotes if q not in preferred]

            for i, q in enumerate(all_candidates[:max_candidates]):
                result["candidates"].append({
                    "symbol": q.get("symbol", ""),
                    "name": q.get("shortname") or q.get("longname", ""),
                    "exchange": q.get("exchange", ""),
                    "quote_type": q.get("quoteType", ""),
                })

            if all_candidates:
                best = all_candidates[0]
                result["matched"] = True
                result["ticker"] = best.get("symbol", "")
                result["name"] = best.get("shortname") or best.get("longname", "")
                result["exchange"] = best.get("exchange", "")
                result["quote_type"] = best.get("quoteType", "")
                result["source"] = "yf_search"
                return result
    except Exception:
        # yf.Search 失败，不中断，返回未匹配结果
        pass

    # 所有方法都失败，返回未匹配结果
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

  # 搜索 ticker（支持 vt_symbol、公司名、代称、加密货币名等）
  python get_market_data.py --search-ticker "apple"
  python get_market_data.py --search-ticker "265598.SMART" --output json
  python get_market_data.py --search-ticker "bitcoin"

  # --search-ticker 配合 product-all-info（自动填入 --ticker）
  python get_market_data.py --search-ticker "apple" --fetch-url product-all-info --output json
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
                        choices=list(ICI_URLS.keys()) + ["all", "product-all-info", "calendar", "web-indicators"],
                        help=f"抓取数据: {', '.join(ICI_URLS.keys())}, all(全部ICI), product-all-info(单品种新闻+评级), calendar(全局经济日历), 或 web-indicators(OrioSearch市场指标)")
    parser.add_argument("--ticker", type=str, default=None,
                        help="用于 --fetch-url product-all-info 的 ticker，如 AAPL")
    parser.add_argument("--search-ticker", type=str, default=None, dest="search_ticker",
                        help="综合搜索 yfinance ticker（支持 vt_symbol、公司名、代称、加密货币名等）")

    args = parser.parse_args()

    # ========== --search_ticker 独立模式 ==========
    # 此参数优先级最高：如果只传了 --search_ticker 且没有 --fetch-url，
    # 则输出搜索结果并退出
    if args.search_ticker and not args.fetch_url:
        result = search_yfinance_ticker(args.search_ticker)
        if args.output == "json":
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            if result["matched"]:
                print(f"✅ 匹配成功: {result['ticker']} ({result['name']})")
                print(f"   来源: {result['source']}, 类型: {result['quote_type']}, 交易所: {result['exchange']}")
            else:
                print(f"❌ 未匹配到 ticker: {result['query']}")
            if result["candidates"]:
                print(f"   候选列表 ({len(result['candidates'])} 条):")
                for c in result["candidates"]:
                    print(f"     {c['symbol']:10s} | {c['name']:35s} | {c['exchange']:6s} | {c['quote_type']}")
        return

    # 抓取数据
    if args.fetch_url:
        if args.fetch_url == "product-all-info":
            # --search_ticker 结果可以自动填入 --ticker（当 --ticker 未显式传入时）
            if not args.ticker and args.search_ticker:
                search_result = search_yfinance_ticker(args.search_ticker)
                if search_result["matched"]:
                    args.ticker = search_result["ticker"]
                else:
                    print(f"❌ --search_ticker '{args.search_ticker}' 未匹配到 ticker，且未传入 --ticker")
                    if search_result["candidates"]:
                        print(f"   候选: {', '.join(c['symbol'] for c in search_result['candidates'])}")
                    sys.exit(1)
            if not args.ticker:
                print("❌ --fetch-url product-all-info 需要 --ticker 或 --search-ticker 参数")
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
                    print(f"  成交量: {t.get('volume', '?')}")
                    ma_parts = []
                    for p in ["ma_20", "ma_50", "ma_200"]:
                        v = t.get(p)
                        if v is not None:
                            ma_parts.append(f"{p.replace('ma_', 'MA')}=${v}")
                    if ma_parts:
                        print(f"  均线: {', '.join(ma_parts)}")
                    print(f"  ATR(14): ${t.get('atr_14', '?')} ({t.get('atr_pct', '?')}%)")
                    print(f"  CCI(14): {t.get('cci_14', '?')}")
                    print(f"  三级支撑位: {t.get('support_levels', '?')}")
                    print(f"  三级压力位: {t.get('resistance_levels', '?')}")
                    print(f"  做多风险收益比: {t.get('risk_reward_long', '?')}")
                    print(f"  做空风险收益比: {t.get('risk_reward_short', '?')}")
                    print(f"  近24h价格分位: {t.get('近24h价格分位', '?')}")
                    print(f"  近3月价格分位: {t.get('近3月价格分位', '?')}")
            return

        if args.fetch_url == "web-indicators":
            data = fetch_web_indicators()
            if args.output == "json":
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                print(_format_web_indicators_text(data))
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
