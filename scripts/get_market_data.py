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

# 控制台编码兜底（2026-10-18）：Windows GBK 控制台下打印中文/符号（如 ❌）会 UnicodeEncodeError
# 直接崩掉整个进程——这也是 A1「父进程按 utf-8 解码、子进程按 GBK 写」解码崩溃的根因之一。
# 在脚本入口统一切到 UTF-8：调用方不再需要 -X utf8 / PYTHONIOENCODING，
# 且与既有消费者（generate_candidate_pool / refill_stock_pool_t3 / rebalance_acceptance
# 均按 encoding="utf-8" 解码）口径一致。
for _stream in ("stdout", "stderr"):
    try:
        getattr(sys, _stream).reconfigure(encoding="utf-8")
    except Exception:
        pass

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

# ---------- 缓存架构（v2 双模式，设计依据 Task-代码取数前视能力.md P0-1） ----------
# live 模式（默认，无 --end）——生产行为，不随历史模式改动：
#   1) 历史行情缓存（_HISTORY_CACHE_DIR）:
#      K线 DataFrame 文件缓存（.pkl），key = {ticker}_{period}，TTL 30 分钟。
#      挂在 get_history_dataframe 统一出口，代理 / yfinance 两条路径都命中。
#      目的: 多个策略在 30 分钟内先后拉取同一批次（如 us-all）时秒回，避免重复请求。
#   2) info 元数据缓存（_INFO_CACHE_DIR）:
#      基本面元数据（名称/PE/市值/行业等）JSON 文件缓存，TTL 12 小时。变化慢，长缓存减少请求。
#   注: requests_cache（_CACHE_DB）为历史遗留定义、未启用——yfinance 1.5.2 拒绝缓存会话，
#       实际活跃缓存只有上述两层；--no-cache/--refresh 穿透的即这两层。
# 历史模式（--end/--as-of）——TTL 语义作废，不进 live 缓存池（物理隔离，互不污染）：
#   不可变窗口缓存，目录 = --cache-dir（默认 _HISTORY_CACHE_DIR_ASOF），
#   key = {ticker}_{period}_to{end}.pkl，无 TTL（定窗日K不可变；
#   拆股/分红会重述历史，严格复现用 --no-cache 强制重取）。

# requests_cache 持久缓存路径（历史遗留，未启用；固定到脚本所在目录）
_CACHE_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "yf_cache_db")

# yfinance info 数据文件缓存（info 无内置缓存，用文件缓存避免重复请求）
_INFO_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".yf_info_cache")
_INFO_CACHE_TTL = 12 * 3600  # 12 小时

# 历史行情 DataFrame 文件缓存（live 模式，统一出口缓存，代理 / yfinance 路径均命中）
_HISTORY_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".yf_history_cache")
_HISTORY_CACHE_TTL = 1800  # 30 分钟

# 历史模式（--end）不可变窗口缓存目录（与 live 缓存物理隔离）
_HISTORY_CACHE_DIR_ASOF = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".yf_history_cache_asof")

# 期权链缓存（2026-10-18 新增；期权报价变动快，TTL 与 live K 线同档 30 分钟）
_OPTIONS_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".yf_options_cache")
_OPTIONS_CACHE_TTL = 1800
# 入场计划默认取三档时间跨度（自然日）：短/中/长期，对应做空 1-2 周 / 1-3 月 / 3-6 月持有期
_OPTIONS_TARGET_HORIZONS = (21, 63, 126)

# 运行时开关（main() 按 CLI 设置；默认值 = 现有生产行为，模块级 import 调用方不受影响）
_RUNTIME = {
    "no_cache": False,                    # --no-cache/--refresh：不读活跃缓存（仍写入）
    "asof_dir": _HISTORY_CACHE_DIR_ASOF,  # --cache-dir：历史模式缓存目录
    "concurrency": 3,                     # --concurrency：批量并发线程数（默认 3，限流保护）
    "retries": 3,                         # --retries：单 ticker 重试次数（默认 3）
}


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
    "^FVX": "美国5年期国债收益率（2Y 代理：Yahoo 无 2 年期收益率符号，2YY.F 实为 Urbana Corp 个股）",
    "DX-Y.NYB": "美元指数 DXY",
    "SHY": "1-3年国债 ETF（短端利率）",
    "TLT": "20+年国债 ETF（长端利率）",
    "HYG": "高收益债 ETF（信用利差）",
    "LQD": "投资级公司债 ETF（信用利差基准）",
}
US_MACRO_TICKERS = ["^TNX", "^FVX", "DX-Y.NYB", "SHY", "TLT", "HYG", "LQD"]

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

US_COMMODITIES = {
    "GLD": "黄金 ETF（避险/大宗商品代理）",
    "USO": "WTI 原油 ETF（能源/大宗商品代理）",
}
US_COMMODITIES_TICKERS = ["GLD", "USO"]

# 行业龙头个股（用于美股分析）
US_SECTOR_LEADERS = {
    "NVDA": "英伟达（AI芯片龙头）",
    "MSFT": "微软（AI+软件龙头）",
    "META": "Meta（社交媒体+AI）",
    "GOOG": "谷歌（AI+搜索）",
    "AMZN": "亚马逊（云计算+电商）",
    "SOXX": "半导体ETF",
    "SMH": "半导体ETF（SOXX 的第二代理）",
    "TSLA": "特斯拉（汽车龙头）",
    "AMD": "AMD（CPU/GPU）",
    "AVGO": "博通（网络/AI芯片）",
    "TSM": "台积电（晶圆代工）",
    "MU": "美光（存储芯片）",
    "GOOGL": "谷歌A类（与 GOOG 同公司，交叉验证口径）",
    "JPM": "摩根大通（银行龙头）",
    "GS": "高盛（投行）",
    "BAC": "美国银行",
    "MS": "摩根士丹利",
    "AAPL": "苹果（消费电子）",
}
US_SECTOR_LEADERS_TICKERS = list(US_SECTOR_LEADERS.keys())

# 合并所有 US labels（用于 us-all 超级批次）
US_ALL_LABELS = {}
for d in [US_MAJOR_INDICES, US_MACRO, US_SECTORS, US_STYLE, US_COMMODITIES, US_SECTOR_LEADERS]:
    US_ALL_LABELS.update(d)
_seen = set()
US_ALL_TICKERS = []
for group in [US_MAJOR_INDICES_TICKERS, US_MACRO_TICKERS,
              US_SECTORS_TICKERS, US_STYLE_TICKERS, US_COMMODITIES_TICKERS,
              US_SECTOR_LEADERS_TICKERS]:
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
    # SUI 必须用 Yahoo 消歧符号 SUI20947-USD：SUI-USD 取不到数据
    # （实测 2026-09-13：SUI-USD 报 Rate limited，SUI20947-USD 正常返回价格）
    "SUI20947-USD": "Sui",
    "NEAR-USD": "NEAR Protocol",
    "APT21794-USD": "Aptos",
}
CRYPTO_ALT_L1_TICKERS = ["SOL-USD", "AVAX-USD", "SUI20947-USD", "NEAR-USD", "APT21794-USD"]

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

# 交易所板块（**按需取用**，不并入 crypto-all）
# 用途：加密文档 Step 0.3-B 条件触发「头部交易所代币（BNB 等）单日跌幅 > 5%」需要 BNB 价格
# 不并入理由：Step 2 板块轮动观察标的只有 大盘锚/L1/DeFi/Meme/基础设施 五类，
#            保持 crypto-all 与文档口径一致，避免每次全量都多拉一个条件触发币
CRYPTO_EXCHANGE = {
    "BNB-USD": "币安币（交易所板块）",
}
CRYPTO_EXCHANGE_TICKERS = ["BNB-USD"]

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
    "crypto-exchange":  {"tickers": CRYPTO_EXCHANGE_TICKERS,      "labels": CRYPTO_EXCHANGE,      "desc": "交易所板块（条件触发用，不含在 crypto-all）"},
}

# 市场 → 可用批次
MARKET_BATCHES = {
    "us_stocks":  ["us-all", "us-major-indices", "us-macro", "us-sectors", "us-style"],
    "crypto":     ["crypto-all", "crypto-majors", "crypto-alt-l1", "crypto-defi", "crypto-meme", "crypto-infra", "crypto-exchange"],
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


def _period_to_days(period: str) -> int:
    """周期 → 自然日数（历史模式 period1/period2 窗口计算用）。无法解析按 1y。"""
    m = re.match(r"(\d+)([a-zA-Z]+)", str(period).strip())
    if not m:
        return 365
    num, unit = int(m.group(1)), m.group(2).lower()
    per = {"d": 1, "mo": 30, "y": 365}.get(unit)
    if per is None or num <= 0:
        return 365
    return num * per


def _date_to_ts(date_str: str, end_of_day: bool = False) -> int:
    """'YYYY-MM-DD' → unix 秒（UTC）。end_of_day=True 取当日 23:59:59。"""
    d = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    if end_of_day:
        d = d + datetime.timedelta(days=1, seconds=-1)
    return int(d.replace(tzinfo=timezone.utc).timestamp())


def _validate_end_date(end: str) -> str:
    """校验 --end 格式；拒绝晚于今天的日期（live 请用缺省，勿用未来 as-of）。"""
    try:
        d = datetime.datetime.strptime(str(end).strip(), "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"--end 日期格式非法: {end!r}（应为 YYYY-MM-DD）")
    today = datetime.datetime.now(timezone.utc).date()
    if d.date() > today:
        raise ValueError(f"--end 不能晚于今天（{today}）；取当前数据请不传 --end")
    return d.strftime("%Y-%m-%d")


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


def _fetch_chart_via_proxy(ticker: str, period: str, end: str = None,
                           adj: bool = False) -> pd.DataFrame:
    """
    用 curl_cffi + socks5 代理直调 Yahoo chart API，返回与 yfinance 兼容的 DataFrame。
    返回列：Date(index, TZ-aware), Open, High, Low, Close, Volume（adj=True 时增 adj_close）

    双模式（契约红线：live 分支保持 range= 写法不变）：
      end=None               → live：range= 相对区间，行为与旧版逐行一致；
      end="YYYY-MM-DD"       → 历史模式：period1/period2 显式窗口（period2=end 当日 23:59:59 UTC）。
    adj=True 时请求 includeAdjustedClose=true，输出 adj_close 列（复权价，回测持有期收益用）。
    """
    params = {"interval": "1d", "includePrePost": "false"}
    if end:
        p2 = _date_to_ts(end, end_of_day=True)
        p1 = p2 - _period_to_days(period) * 86400
        params["period1"] = str(p1)
        params["period2"] = str(p2)
    else:
        params["range"] = _period_to_range(period)
    if adj:
        params["includeAdjustedClose"] = "true"
    last_err = None
    for host in _YAHOO_HOSTS:
        url = f"https://{host}/v8/finance/chart/{ticker}"
        try:
            session = _make_proxy_cfr_session()
            resp = session.get(
                url,
                params=params,
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
            if adj:
                adjblk = (result.get("indicators") or {}).get("adjclose") or [{}]
                rows["Adj Close"] = adjblk[0].get("adjclose") if adjblk else None
            df = pd.DataFrame(rows).set_index("Date")
            # 去掉全 NaN / 无收盘价的行（非交易日或停牌）
            df = df.dropna(subset=["Close"])
            if adj and "Adj Close" in df.columns:
                # adj_close 与现有列同行对齐；adjclose 缺失行保留 NaN，不影响现有列
                df["adj_close"] = df["Adj Close"]
                df = df.drop(columns=["Adj Close"])
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
    读取历史行情文件缓存（live 模式）。

    key = {ticker}_{period}（如 QQQ_1y.pkl），TTL = _HISTORY_CACHE_TTL（30 分钟）。
    命中且未过期返回 DataFrame，否则返回 None（由 get_history_dataframe 重新拉取）。
    缓存文件缺失 / 过期 / 损坏 / 读取异常，一律返回 None，保证不影响正常拉取流程。
    --no-cache/--refresh（_RUNTIME["no_cache"]）→ 不读缓存（仍写入）。
    """
    if _RUNTIME["no_cache"]:
        return None
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


def _asof_cache_path(ticker: str, period: str, end: str) -> str:
    """历史模式不可变缓存路径：目录 _RUNTIME["asof_dir"]，key 带窗口终点。"""
    return os.path.join(_RUNTIME["asof_dir"], f"{ticker}_{period}_to{end}.pkl")


def _load_asof_cache(ticker: str, period: str, end: str):
    """
    读取历史模式（--end）不可变窗口缓存。无 TTL（定窗日K不可变）；
    拆股/分红会重述历史 → 严格复现用 --no-cache 强制重取。
    """
    if _RUNTIME["no_cache"]:
        return None
    try:
        path = _asof_cache_path(ticker, period, end)
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def _save_asof_cache(ticker: str, period: str, end: str, df: pd.DataFrame):
    """保存历史模式不可变窗口缓存（原子替换，同 _save_history_cache）。"""
    try:
        os.makedirs(_RUNTIME["asof_dir"], exist_ok=True)
        path = _asof_cache_path(ticker, period, end)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            pickle.dump(df, f)
        os.replace(tmp, path)
    except Exception:
        pass


def get_history_dataframe(ticker: str, period: str = "1y", session=None,
                          end: str = None, adj: bool = False) -> pd.DataFrame:
    """
    统一历史数据入口（v2 双模式缓存）。

    live 模式（end=None，默认）——行为与旧版一致：
      1. 历史行情文件缓存命中（30 分钟 TTL）→ 直接返回，秒级响应
      2. socks5 代理直调 Yahoo chart API（curl_cffi，range= 写法），结果写入缓存
      3. 回落 yfinance 原生 history（yfinance 1.5.2 不接受 requests_cache 缓存会话，
         由 yfinance 自建 session 实时请求）

    历史模式（end="YYYY-MM-DD"）——不进 live TTL 缓存池：
      不可变窗口缓存（无 TTL，目录 --cache-dir）→ 代理 period1/period2 →
      回落 yfinance history(start=, end=)（不传 period，避免参数冲突；
      adj=True 时 auto_adjust=False 取原始 Close + Adj Close）。

    返回与 yfinance history() 兼容的 DataFrame（列 Open/High/Low/Close/Volume；
    adj=True 时增 adj_close）。df.attrs["data_source"] 标记 proxy/yfinance。失败抛异常。
    """
    # ---------- 历史模式（--end）：物理隔离的不可变窗口缓存 ----------
    if end:
        cached = _load_asof_cache(ticker, period, end)
        if cached is not None and not cached.empty:
            # attrs 不随 pickle 保留 → 缓存命中时补标 data_source
            cached.attrs["data_source"] = "cache"
            return cached

        if _HAS_CURL_CFFI and _is_socks_proxy_available():
            try:
                df = _fetch_chart_via_proxy(ticker, period, end=end, adj=adj)
                if not df.empty:
                    _save_asof_cache(ticker, period, end, df)
                    df.attrs["data_source"] = "proxy"
                return df
            except Exception:
                pass  # 走代理失败，回落到 yfinance

        end_dt = datetime.datetime.strptime(end, "%Y-%m-%d")
        start_str = (end_dt - datetime.timedelta(days=_period_to_days(period))).strftime("%Y-%m-%d")
        # yfinance 的 end 为排他边界 → +1 天使 as_of 当日收盘包含在窗口内
        end_excl = (end_dt + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        if adj:
            # 原始 Close + Adj Close（与代理路径口径一致：Close 未复权、adj_close 复权）
            df = yf.Ticker(ticker).history(start=start_str, end=end_excl,
                                           auto_adjust=False, actions=False)
            if "Adj Close" in df.columns:
                df = df.rename(columns={"Adj Close": "adj_close"})
        else:
            df = yf.Ticker(ticker).history(start=start_str, end=end_excl)
        if not df.empty:
            _save_asof_cache(ticker, period, end, df)
            df.attrs["data_source"] = "yfinance"
        return df

    # ---------- live 模式：以下与旧版逐行一致（仅新增 no_cache 开关）----------
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
                df.attrs["data_source"] = "proxy"
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
        df.attrs["data_source"] = "yfinance"
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

    # 缓存命中且未过期。
    # A5b（2026-10-18）修正：--no-cache / --refresh 此前只跳过 K 线缓存，**info 缓存照读**，
    # 与 --no-cache 的 help 文案（"不读两层活跃缓存"）不符 → 新增字段在 12h 内取不到。
    # 现在两层缓存一致：不读但仍写入（缓存目录与 TTL 不变，符合前视红线 #3）。
    if not _RUNTIME.get("no_cache") and os.path.exists(cache_path):
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
            def _pick(mods, key):
                """按模块优先级取 raw 值（Yahoo 同一字段可能散在多个模块里）"""
                for mod in mods:
                    v = (mod.get(key) or {}).get("raw")
                    if v is not None:
                        return v
                return None

            for k in ["marketCap", "trailingPE", "priceToBook", "sharesOutstanding",
                      "regularMarketVolume", "floatShares",
                      # C4（2026-10-18）做空/估值补齐：P/S（summaryDetail）
                      "priceToSalesTrailing12Months"]:
                info[k] = _pick([sd, dks], k)
            # C1（2026-10-18）做空数据通道：做空量 / 前月做空量 / 回补天数 / 占流通盘比
            # （defaultKeyStatistics 原生字段，此前未取 → AI 只能逐只 web_fetch MarketBeat）
            for k in ["sharesShort", "sharesShortPriorMonth", "shortRatio",
                      "shortPercentOfFloat", "pegRatio", "trailingEps", "forwardPE"]:
                info[k] = _pick([dks, sd, fin], k)
            for k in ["returnOnEquity", "revenueGrowth", "totalRevenue",
                      # C4：EV/EBITDA、盈利增速、利润率、FCF、债务与 EBITDA（净债务/EBITDA 由下游算）
                      "enterpriseToEbitda", "earningsGrowth", "profitMargins",
                      "freeCashflow", "totalDebt", "totalCash", "ebitda"]:
                info[k] = (fin.get(k) or {}).get("raw")
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


def get_ticker_data(ticker: str, session: CachedSession = None,
                    end: str = None, adj: bool = False) -> dict:
    """
    获取单个 ticker 的完整数据

    Args:
        ticker: 股票代码
        session: 保留参数（yfinance 1.5.2 拒绝 requests_cache 缓存会话，不再传给 yfinance）
        end: 历史模式截止日 YYYY-MM-DD（None=live，行为与旧版一致）。
             历史模式下 as_of=末根 bar 日期，并追加 requested_end /
             fundamentals_point_in_time=false（info 永远是当前快照）/ data_source。
        adj: 请求复权列（历史模式回测持有期收益用；live 默认 False 不改输出）

    Returns:
        dict: {ticker, name, price, change, change_pct, ma_20, ma_50, ma_200,
               high_52w, low_52w, pct_52w, volume, avg_volume, market_cap,
               shares_outstanding, pe_ratio, pb_ratio, roe, revenue_growth,
               sector, industry, as_of}
    """
    max_retries = max(1, int(_RUNTIME["retries"]))
    for attempt in range(max_retries):
        try:
            stock = yf.Ticker(ticker)
            hist = get_history_dataframe(ticker, "1y", session=session, end=end, adj=adj)
            if hist.empty:
                if attempt < max_retries - 1:
                    wait = (2 ** attempt) + random.uniform(0, 1)
                    time.sleep(wait)
                    continue
                err = {"ticker": ticker, "error": "无数据", "as_of": datetime.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
                if end:
                    err["requested_end"] = end
                return err

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

            # ---- C1/C3/C4（2026-10-18，只增字段）做空通道 + 估值补齐 + 技术面本地算 ----
            # 此前这些口径没有脚本通道 → AI 逐只 web_fetch finviz/MarketBeat（反爬、滞后约 1 个月）
            ps_ratio = safe_float(info.get("priceToSalesTrailing12Months"))
            peg_ratio = safe_float(info.get("pegRatio"))
            ev_ebitda = safe_float(info.get("enterpriseToEbitda"))
            earnings_growth = safe_float(info.get("earningsGrowth"))
            profit_margins = safe_float(info.get("profitMargins"))
            free_cashflow = safe_float(info.get("freeCashflow"))
            total_debt = safe_float(info.get("totalDebt"))
            total_cash = safe_float(info.get("totalCash"))
            ebitda = safe_float(info.get("ebitda"))
            net_debt_to_ebitda = (round((total_debt - (total_cash or 0)) / ebitda, 2)
                                  if (total_debt is not None and ebitda) else None)
            # Yahoo 的 enterpriseToEbitda 经常为 null（实测 MSFT/BA 等）→ 用
            # EV = 市值 + 总债务 - 现金 自行计算，字段可得性不再依赖 Yahoo 单点。
            # ev_ebitda_derived=true 表示是本地推算值（报告里要注明口径）
            ev_ebitda_derived = False
            if ev_ebitda is None and market_cap and ebitda and total_debt is not None:
                ev_ebitda = round((market_cap + total_debt - (total_cash or 0)) / ebitda, 2)
                ev_ebitda_derived = True
            fcf_yield = (round(free_cashflow / market_cap, 4)
                         if (free_cashflow is not None and market_cap) else None)
            short_interest = safe_float(info.get("sharesShort"))
            short_interest_prior = safe_float(info.get("sharesShortPriorMonth"))
            short_days_to_cover = safe_float(info.get("shortRatio"))
            short_pct_of_float = safe_float(info.get("shortPercentOfFloat"))
            rsi_14 = calc_rsi(hist)
            macd_line, macd_signal, macd_hist_val = calc_macd(hist)
            try:
                atr_14 = calc_atr(hist) if {"High", "Low", "Close"} <= set(hist.columns) else None
            except Exception:
                atr_14 = None

            # as_of：live=取数时刻；历史模式=末根 bar 日期（as-of 语义，非今天）
            if end:
                as_of_val = str(hist.index[-1])[:10]
            else:
                as_of_val = datetime.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            out = {
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
                # ---- 2026-10-18 只增字段：做空通道 / 估值补齐 / 技术面（现有字段与顺序不动）----
                # short_pct_of_float 是小数（0.023 = 2.3%）；short_days_to_cover = 回补天数
                # 这些是**当前快照**：历史模式下 fundamentals_point_in_time=false 已覆盖
                "ps_ratio": ps_ratio,
                "peg_ratio": peg_ratio,
                "ev_ebitda": ev_ebitda,
                "ev_ebitda_derived": ev_ebitda_derived,
                "earnings_growth": earnings_growth,
                "profit_margins": profit_margins,
                "fcf_yield": fcf_yield,
                "net_debt_to_ebitda": net_debt_to_ebitda,
                "short_interest": short_interest,
                "short_interest_prior": short_interest_prior,
                "short_days_to_cover": short_days_to_cover,
                "short_pct_of_float": short_pct_of_float,
                "rsi_14": rsi_14,
                "macd": macd_line,
                "macd_signal": macd_signal,
                "macd_hist": macd_hist_val,
                "atr_14": atr_14,
                "sector": sector,
                "industry": industry,
                "as_of": as_of_val,
            }
            if end:
                # 历史模式元信息（只增字段，live 输出不变）：
                # 价格段是 as-of 时点值；info 基本面永远是当前快照 → 显式标记
                out["requested_end"] = end
                out["fundamentals_point_in_time"] = False
                ds = hist.attrs.get("data_source") if hasattr(hist, "attrs") else None
                if ds:
                    out["data_source"] = ds
            return out
        except Exception as e:
            # 遇到 429 限流时指数退避重试
            if "429" in str(e) and attempt < max_retries - 1:
                wait = (2 ** attempt) * 5 + random.uniform(0, 2)
                time.sleep(wait)
                continue
            if attempt == max_retries - 1:
                err = {"ticker": ticker, "error": str(e), "as_of": datetime.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
                if end:
                    err["requested_end"] = end
                return err
            wait = (2 ** attempt) + random.uniform(0, 1)
            time.sleep(wait)


def _fetch_single(ticker, labels=None, session=None, end=None, adj=False):
    """线程池包装器：获取单个 ticker 数据并附加 label"""
    data = get_ticker_data(ticker, session=session, end=end, adj=adj)
    if labels and ticker in labels:
        data["label"] = labels[ticker]
    return data


def fetch_batch(tickers: list, labels: dict = None, cache_ttl: int = 1800,
                end: str = None, adj: bool = False, concurrency: int = None) -> list:
    """
    批量获取 ticker 数据（带线程池 + 随机延迟）。

    默认 3 并发线程 + 0.5-1.5s 随机间隔，降低 yfinance 限流概率（契约红线：默认不变）。
    缓存（双模式架构详见文件头部全局常量区注释）:
      - live  : 历史行情 K 线 30 分钟 TTL + info 12 小时 TTL（--no-cache 可不读）
      - 历史  : --end 时走不可变窗口缓存（无 TTL，独立目录），天然支持多日期回放断点续拉
      - yfinance HTTP : 1.5.2 无 HTTP 响应缓存（拒绝 requests_cache），实时请求

    Args:
        tickers: 需要获取的 ticker 列表
        labels: ticker → 中文名映射（可选），命中时附加到结果
        cache_ttl: 保留参数（yfinance 1.5.2 已无 HTTP 缓存，去重靠文件缓存），默认 1800
        end: 历史模式截止日 YYYY-MM-DD（None=live）
        adj: 请求复权列（历史模式用）
        concurrency: 并发线程数（None → _RUNTIME["concurrency"]，默认 3）
    """
    if not tickers:
        return []

    # 注意：yfinance 1.5.2 拒绝 requests_cache 缓存会话（HTTP 层不再缓存），
    # 跨进程去重由 get_history_dataframe 的文件缓存（30 分钟 TTL）承担。
    workers = max(1, int(concurrency or _RUNTIME["concurrency"]))
    results = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_fetch_single, t, labels, None, end, adj): t for t in tickers}
        for future in as_completed(futures):
            data = future.result()
            results.append(data)
            time.sleep(random.uniform(0.5, 1.5))
    return results


# --fields/--minimal 字段过滤（默认不过滤；元信息与 error 项始终保留）
_MINIMAL_FIELDS = {"ticker", "label", "name", "price", "change", "change_pct",
                   "ma_20", "ma_50", "ma_200", "pct_52w", "avg_volume", "as_of"}
_ALWAYS_KEEP = {"ticker", "error", "as_of", "label", "requested_end",
                "fundamentals_point_in_time", "data_source"}


def _apply_field_filter(data: list, args) -> list:
    """--fields 白名单 / --minimal 精简；两者都不传 → 原样返回（默认全字段）"""
    keep = None
    if getattr(args, "fields", None):
        keep = {t.strip() for t in args.fields.split(",") if t.strip()} | _ALWAYS_KEEP
    elif getattr(args, "minimal", False):
        keep = _MINIMAL_FIELDS | _ALWAYS_KEEP
    if keep is None:
        return data
    out = []
    for item in data:
        if not isinstance(item, dict) or "error" in item:
            out.append(item)
            continue
        out.append({k: v for k, v in item.items() if k in keep})
    return out


def _apply_sector_median(data: list) -> list:
    """C2（2026-10-18，opt-in）：按 sector 分组算 PE / P/S / EV-EBITDA 中位数（样本＝本次批次），
    给每条记录加 sector_median_* 与 *_vs_sector_median。

    只在 `--sector-median` 时调用 → 默认输出与改前完全一致。
    样本 < 3 的板块不写中位数（避免 1-2 个样本的伪中位数）；
    口径是**批次内同板块**中位数，不是全市场行业统计 → 报告里要写明样本数。
    """
    import statistics
    groups = {}
    for it in data:
        if not isinstance(it, dict) or it.get("error"):
            continue
        if it.get("sector"):
            groups.setdefault(it["sector"], []).append(it)

    medians = {}
    for sec, items in groups.items():
        if len(items) < 3:
            continue

        def _med(key, _items=items):
            vals = [it[key] for it in _items if isinstance(it.get(key), (int, float))]
            return round(statistics.median(vals), 4) if len(vals) >= 3 else None

        medians[sec] = {"pe": _med("pe_ratio"), "ps": _med("ps_ratio"),
                        "ev_ebitda": _med("ev_ebitda"), "n": len(items)}

    for it in data:
        if not isinstance(it, dict) or it.get("error"):
            continue
        m = medians.get(it.get("sector"))
        if not m:
            continue
        it["sector_sample_in_batch"] = m["n"]
        for key, label in (("pe", "pe_ratio"), ("ps", "ps_ratio"), ("ev_ebitda", "ev_ebitda")):
            if m[key] is None:
                continue
            it[f"sector_median_{key}"] = m[key]
            val = it.get(label)
            if isinstance(val, (int, float)) and m[key]:
                it[f"{label}_vs_sector_median"] = round(val / m[key], 3)
    return data


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
# 配置优先级：环境变量 ORIOSEARCH_URL / ORIOSEARCH_API_KEY > 同目录 orio_config.json > 内置默认值
# （2026-10-09 服务启用鉴权：缺失 401，错误 403；本文件与 orio_config.json 勿提交公开仓库）
_ORIO_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "orio_config.json")


def _load_orio_config():
    """读取同目录 orio_config.json；文件缺失/JSON 损坏时静默回退内置默认值"""
    defaults = {"url": "https://search.my-gun.top",
                "api_key": "sk-ario-9d0b3f8400d4d09ea58c6b76362acddb"}
    try:
        with open(_ORIO_CONFIG_PATH, encoding="utf-8") as f:
            return {**defaults, **json.load(f)}
    except (OSError, ValueError):
        return defaults


_ORIO_CFG = _load_orio_config()
ORIO_URL = os.environ.get("ORIOSEARCH_URL", _ORIO_CFG["url"])
ORIOSEARCH_API_KEY = os.environ.get("ORIOSEARCH_API_KEY", _ORIO_CFG["api_key"])

# 多端点回退（2026-10-10）：主端点失败/超时/无结果时自动切 fallback 端点重试。
# 配置：orio_config.json 加 "fallback": {"url": "...", "api_key": "..."}
# （api_key 可省略则沿用主端点 Key；端点与主端点相同则视为无回退）。
# 典型用途：本地 Docker 挂掉/被限流 → 切远程独立检索栈 search.my-gun.top。
_ORIO_FALLBACK_CFG = _ORIO_CFG.get("fallback") or {}
ORIO_FALLBACK_URL = (_ORIO_FALLBACK_CFG.get("url") or "").rstrip("/")
ORIO_FALLBACK_KEY = _ORIO_FALLBACK_CFG.get("api_key") or ORIOSEARCH_API_KEY
ORIO_FALLBACK_ENABLED = bool(
    ORIO_FALLBACK_URL and ORIO_FALLBACK_URL != ORIO_URL.rstrip("/"))

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
        return {"count": 0, "items": [], "error": "Yahoo RSS 无响应", "point_in_time": False}

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

    # point_in_time=false：RSS 只有近期条目，历史新闻不可回溯（P0-5 类②），
    # 回放/回测流程禁止用本结果做决策（详见 Task-代码取数前视能力.md）
    return {"count": len(news), "items": news[:10], "point_in_time": False}


def fetch_ratings(ticker):
    """
    获取机构评级（**安全入口：永不抛异常**）

    降级背景：币种（BTC-USD 等）、指数等标的没有分析师评级，MarketBeat 页面结构与
    个股不同，正则可能抓到纯标点（如 "."）导致 float() 抛 ValueError。
    若异常向外传播，会同批拖垮同一流程里的 news 与 technical
    （见 fetch_product_all_info 的调用顺序），故此处统一兜底为 {"error": ...}。
    """
    try:
        r = _fetch_ratings_inner(ticker)
    except Exception as e:
        return {"error": f"评级获取失败（已降级，不影响新闻/技术指标）: {type(e).__name__}: {e}",
                "point_in_time": False}
    # point_in_time=false：分析师共识永远是"当前"快照，免费源无历史存档（P0-5 类②）
    r["point_in_time"] = False
    return r


def _fetch_ratings_inner(ticker):
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


def _fetch_fred_calendar(days=14, end=None):
    """源3: FRED 发布日历（仅白名单内的重要美国宏观发布）

    URL: https://fred.stlouisfed.org/releases/calendar?vs=YYYY-MM-DD&ve=YYYY-MM-DD
    按天请求（单天 30-45 条，不触发 50 条分页截断），4 线程并发。
    需要 curl_cffi 模拟浏览器 TLS（urllib 直连超时）；无 curl_cffi 时跳过。

    end=None → live：今日至 +days 天（原行为）；
    end="YYYY-MM-DD" → 历史模式：end-days+1 至 end 的**过去窗口**
      （FRED 日历按日期 URL 取数，天然可回溯——P0-5 类①）。
    """
    try:
        from curl_cffi import requests as cr
    except ImportError:
        return []

    if end:
        base = datetime.datetime.strptime(end, "%Y-%m-%d").date()
        urls = [
            f"https://fred.stlouisfed.org/releases/calendar"
            f"?vs={(base - datetime.timedelta(days=days - 1 - i)).isoformat()}"
            f"&ve={(base - datetime.timedelta(days=days - 1 - i)).isoformat()}"
            for i in range(days)
        ]
    else:
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


def fetch_economic_calendar(end=None):
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

    end="YYYY-MM-DD"（历史模式，P0-5）：源1/2/4 均为"当前页"源不可回溯 →
      只走 FRED 过去窗口（end-14d..end），每项标 point_in_time=false。
      live（end=None）行为与旧版完全一致。
    """
    # 历史模式：仅可回溯源（FRED 按日期 URL），当前页源全部跳过
    if end:
        items = _fetch_fred_calendar(days=14, end=end)
        for it in items:
            it["point_in_time"] = False
        return items

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
    r"do\s+\**not\**\s+(?:contain|include|provide|have|show|lists|list|report|give|state|offer|disclose)",
    r"does\s+\**not\**\s+(?:contain|include|provide|have|show|lists|list|report|give|state|offer|disclose)",
    r"did\s+\**not\**\s+(?:contain|include|provide|show|report|list)",
    r"don['\u2019]?t\s+(?:contain|include|provide|have|state|show|list)",
    r"no\s+(?:explicit|information|data|specific|figures?|numbers?|usable|relevant|meaningful)",
    r"not\s+(?:explicitly|available|provided|listed|mentioned|stated|disclosed|specified|shown)",
    r"cannot be (?:found|determined|calculated)",
    r"unable to (?:find|determine|locate|provide)",
    r"does not (?:provide|have|show|include|list)",
    r"not available in", r"no data",
    r"only (?:show|shows|contain|contains|cover|covers|provide|provides|list|lists|address|addresses)\b",
    r"the only result", r"they are (?:about|unrelated)", r"unrelated to",
    r"无法(?:找到|确定|获取|提供|给出)", r"未能(?:找到|获取)",
    r"没有(?:相关|找到|提供|给出)", r"未(?:找到|包含|给出|提供|显示|列出|涉及|发现)",
    r"均(?:未|不含)", r"无(?:相关|有效)?(?:数据|信息|结果)",
    r"建议(?:直接|查看|访问|查询)",
)
_NO_ANSWER_RE = re.compile("|".join(_NO_ANSWER_PATTERNS), re.IGNORECASE)


def _is_no_answer(text):
    """判断答案是否为"无有效信息"模板回复（有返回但非真实数据）

    2026-09-22 补全句式表：原表漏掉 do not show / there is no information /
    there are no specific figures 及中文"未找到/未包含/建议直接查看"，导致
    PutCallRatio/Secondary/MarginDebt 三项被计为 hit（total 14 / hit 3 假命中）。
    """
    if not text:
        return True
    return bool(_NO_ANSWER_RE.search(text[:300]))


def _value_before_negation(answer):
    """首个带量纲真值是否出现在首个"无答案模板"词之前

    2026-10-10（HY_OAS 误杀案例）：LLM 答案前半给值（"reported at 3.03%, which
    equals 303 basis points"），尾部对**其他来源**对冲（"the numeric current
    level is not shown in the provided snippet [1][2]"），被模板词表误杀。
    判据：真值位置 < 模板词位置 → 否定只针对部分来源，真值应保留。
    反之（否定在前、数字在后）多半是模板顺带引用的数字，仍视为无答案。
    """
    i_val = None
    for m in _MAG_RE.finditer(answer):
        if not _YEAR_RE.match(m.group(0)):
            i_val = m.start()
            break
    i_no = _NO_ANSWER_RE.search(answer[:300])
    i_no = i_no.start() if i_no else len(answer)
    return i_val is not None and i_val < i_no


# ---------- 有效值闸门（第二道判据） ----------
# 仅靠句式表判不住"有返回、不含否定模板词、但通篇没有目标数值"的答案。
# 要求答案里存在 ① 带量纲数值（$X.XT/B/M、NNN%、NNNbp）或
#              ② 指标关键词邻域内带小数的指数值（MOVE 81.2 / F&G 33.66 / P-C 0.58）。
_MAG_RE = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?\s*(?:万亿|千亿|trillion|billion|million|[tbm])?"
    r"|\d[\d,]*(?:\.\d+)?\s*(?:%|percent|bps?\b|basis points?\b|pct\b|万亿|trillion|billion|million)",
    re.IGNORECASE)
_INDEX_NUM_RE = re.compile(r"\d{1,4}\.\d{1,4}|\d{2,4}\s*(?:points?\b|index\b)", re.IGNORECASE)
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
_INDICATOR_KEYWORDS = {
    "HY_OAS": ("oas", "spread", "high yield"),
    "MOVE": ("move", "bond vol", "treasury vol"),
    "FearGreed": ("fear", "greed", "f&g"),
    "MarginDebt": ("margin", "debit", "finra"),
    "Buyback": ("buyback", "repurchase"),
    "IPO": ("ipo", "raised", "proceeds", "pricing"),
    "Secondary": ("secondary", "follow-on", "offering"),
    "FOMC": ("fomc", "fed funds", "rate", "basis point", "hike", "cut"),
    "CPI": ("cpi", "inflation", "consumer price"),
    "FedWatch": ("fedwatch", "fed funds", "probability", "oisl", "implied"),
    "PutCallRatio": ("put/call", "put call", "p/c", "ratio"),
    "Liquidity": ("rrp", "reverse repo", "liquidity"),
    "Calendar": ("mon", "tue", "wed", "thu", "fri", "est", "release"),
    "Earnings": ("earnings", "fiscal", "q3", "q4", "report"),
}


def _validate_indicator_value(key, answer):
    """校验答案里是否真有该指标数值 → (ok, 展示用关键数值)"""
    if not answer:
        return False, "N/A"
    mags = [m.group(0).strip() for m in _MAG_RE.finditer(answer)]
    mags = [m for m in mags if not _YEAR_RE.match(m)]
    if mags:
        return True, " | ".join(dict.fromkeys(mags))[:110]
    low = answer.lower()
    near = []
    for kw in _INDICATOR_KEYWORDS.get(key, ()):
        start = 0
        while True:
            i = low.find(kw, start)
            if i < 0:
                break
            start = i + 1
            w = low[max(0, i - 45): i + len(kw) + 45]
            for n in _INDEX_NUM_RE.finditer(w):
                tok = n.group(0).strip()
                if not _YEAR_RE.match(tok):
                    near.append(tok)
    if near:
        return True, " | ".join(dict.fromkeys(near))[:110] + " (无量纲)"
    return False, "N/A(答案无目标数值)"


# ---------- 直连结构化源（优先于搜索层） ----------
# 2026-09-22 实测：OrioSearch 对这批英文指标查询返回无关词条（"MOVE index"→知乎
# 表情包、"CNN fear greed"→卷积神经网络、"S&P 500 buyback"→"字母 s"百科），
# 中文与常识控制组同样返回垃圾 → 上游检索源不适配，改查询词无解。
# 能直取的指标一律走直连，搜索只兜底。
CNN_FG_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
YAHOO_DIRECT_SYMBOLS = {"VIX": "^VIX", "VIX9D": "^VIX9D", "VXN": "^VXN", "MOVE": "^MOVE"}
# FRED 公开 CSV（无需 API key）。P0-5 类①：HY OAS 的历史序列源——
# 仅 --end 历史模式启用（契约红线：live 源优先级不变，现源仍走 CNN/搜索层）
FRED_SERIES = {"HY_OAS": "BAMLH0A0HYM2"}  # 高收益债 OAS 利差（%）

# ---- P0-6 宏观回溯层（2026-10-18 新增，仅 --end 历史模式启用）----
# 判据同 P0-5 类①：源头有历史档案才做回溯。以下均为 2026-10-18 实测可取 as-of 值：
#   CPIAUCSL 2025-06-01=321.435 / 2024-06-01=313.044 → 同比 2.68%（与官方公布 2.7% 吻合）
#   WALCL 6,662,200 − WTREGEN 364,375 − RRPONTSYD 460.731（均 $M）→ 净流动性 6297.4 $bn
#   DFEDTARU 4.50 / DFEDTARL 4.25（2025-06-30，即当次 FOMC 目标区间）
#   FINRA 保证金债务 2025-06-01=1,007,961 $M（同比 24.54%、超额杠杆 11.01）
FRED_CPI_SERIES = {"headline": "CPIAUCSL", "core": "CPILFESL"}
FRED_LIQUIDITY_SERIES = {"fed_assets": "WALCL", "tga": "WTREGEN", "rrp": "RRPONTSYD"}
FRED_FEDFUNDS_SERIES = {"upper": "DFEDTARU", "lower": "DFEDTARL"}
# FINRA 保证金债务：FRED 的 RAMMDT 已下线（实测 404）、FINRA 官方页无 CSV（实测 86KB 无下载链接）；
# 此 CSV 是 FINRA 官方统计的第三方再加工（1997 起月度，含现成 yoy / excess_leverage），
# 实测与 YCharts 口径一致（2026-09=1.48T）。**非官方端点** → source 里显式标 third-party。
MARGIN_DEBT_CSV = "https://www.thetrading.tools/data/indicators/margin-debt.csv"
# **PutCallRatio 不做回溯**（2026-10-18 实测）：CBOE 的 4 个 Put_Call/PCR CSV 路径全部 403
# AccessDenied，dashboard 页数据为 JS 渲染（HTML 内无 put/call 数值），Yahoo "^PUT" 返回
# 3043.44（真实 put/call 量级 0.9~1.5，量纲不符、口径不明）→ 无可靠免费历史源，
# 历史模式保持 asof_unsupported（不假装有值）。
_MACRO_ASOF_MEMO = {}  # 进程内 memo：同一次运行内同序列只抓一次


def _fetch_fred_rows(series_id: str, start: str = "2000-01-01", end: str = None):
    """FRED fredgraph.csv → [(date, value), ...]（升序，缺失值 . 已剔除）；失败 []。

    FRED 对 requests 直连超时（TLS 指纹），需 curl_cffi impersonate="chrome"
    （与 _fetch_fred_calendar 同一实测结论）；无 curl_cffi 时回落 requests。
    带进程内 memo（历史窗口不可变，同一次运行内同参数只抓一次）。
    """
    memo_key = (series_id, start, end)
    if memo_key in _MACRO_ASOF_MEMO:
        return _MACRO_ASOF_MEMO[memo_key]
    rows = []
    try:
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
        if end:
            url += f"&cosd={start}&coed={end}"
        text = None
        if _HAS_CURL_CFFI:
            try:
                r = cfr.get(url, impersonate="chrome", timeout=25)
                if r.status_code == 200:
                    text = r.text
            except Exception:
                text = None
        if text is None:
            r = requests.get(url, headers={"User-Agent": _YAHOO_UA}, timeout=(5, 25))
            if r.status_code == 200:
                text = r.text
        if text:
            for line in text.splitlines()[1:]:
                parts = line.split(",")
                if len(parts) >= 2 and parts[1] not in (".", ""):
                    try:
                        rows.append((parts[0], float(parts[1])))
                    except ValueError:
                        pass
    except Exception:
        rows = []
    _MACRO_ASOF_MEMO[memo_key] = rows
    return rows


def _fetch_fred_series_latest(series_id: str, end: str = None):
    """FRED 取 ≤end 的最近观测 → (date, value)；失败 None。（签名与行为保持不变）"""
    rows = _fetch_fred_rows(series_id, "2000-01-01", end)
    return rows[-1] if rows else None


def _fred_latest_in_window(series_id: str, end: str, back_days: int):
    """取 (end − back_days) 之前最近的一条观测 → (date, value)；失败 None。"""
    try:
        start = (datetime.date.fromisoformat(end) - datetime.timedelta(days=back_days)).isoformat()
    except Exception:
        return None
    return _fetch_fred_series_latest(series_id, end=start)


def _add_macro_asof_layers(out, end):
    """P0-6：把"源头有历史档案"的宏观指标在历史模式补上 as-of 值（仅 --end 调用）。

    覆盖 CPI（同比/环比/核心）、Liquidity（Fed 净流动性）、FOMC（当时目标利率区间 +
    区间生效日）、MarginDebt（FINRA 保证金债务 + 同比 + 超额杠杆）。
    取到才写 out[key]（上层据此计入 hit 并从 asof_unsupported 名单移除）；取不到不写。
    """
    def _shift(days):
        try:
            return (datetime.date.fromisoformat(end) - datetime.timedelta(days=days)).isoformat()
        except Exception:
            return None

    # ---------- CPI：指数序列 → 同比/环比（FRED 只有指数，比率本地算） ----------
    h_now = _fetch_fred_series_latest(FRED_CPI_SERIES["headline"], end=end)
    h_yoy = _fred_latest_in_window(FRED_CPI_SERIES["headline"], end, 366)
    h_mom = _fred_latest_in_window(FRED_CPI_SERIES["headline"], end, 32)
    c_now = _fetch_fred_series_latest(FRED_CPI_SERIES["core"], end=end)
    c_yoy = _fred_latest_in_window(FRED_CPI_SERIES["core"], end, 366)
    if h_now and h_yoy and h_yoy[1]:
        yoy = (h_now[1] / h_yoy[1] - 1.0) * 100.0
        item = {
            "query": "fred:" + FRED_CPI_SERIES["headline"],
            "answer": "fred CPIAUCSL/CPILFESL 同比（本地由指数算，季调）",
            "value": round(yoy, 2),                      # 主值＝headline 同比 %
            "as_of": h_now[0],
            "source": "direct:fred:" + FRED_CPI_SERIES["headline"],
            "point_in_time": False, "time_s": 0.0,
            "headline_index": h_now[1],
            "headline_yoy_pct": round(yoy, 2),
            "basis_note": "同比＝≤end 最近观测 / ≤(end−12月) 最近观测；月度序列按公布月对齐",
        }
        if h_mom and h_mom[1]:
            item["headline_mom_pct"] = round((h_now[1] / h_mom[1] - 1.0) * 100.0, 2)
        if c_now and c_yoy and c_yoy[1]:
            item["core_yoy_pct"] = round((c_now[1] / c_yoy[1] - 1.0) * 100.0, 2)
            item["core_index"] = c_now[1]
        out["CPI"] = item

    # ---------- Liquidity：Fed 净流动性 = 总资产 − TGA − ON RRP（均 $M → 输出 $bn） ----------
    liq = {}
    for label, sid in FRED_LIQUIDITY_SERIES.items():
        obs = _fetch_fred_series_latest(sid, end=end)
        if obs:
            liq[label] = obs
    if len(liq) == len(FRED_LIQUIDITY_SERIES):
        net_m = liq["fed_assets"][1] - liq["tga"][1] - liq["rrp"][1]
        out["Liquidity"] = {
            "query": "fred:" + ",".join(FRED_LIQUIDITY_SERIES.values()),
            "answer": "fred WALCL − WTREGEN − RRPONTSYD（Fed 净流动性）",
            "value": round(net_m / 1000.0, 1),           # $bn
            "as_of": liq["fed_assets"][0],
            "source": "direct:fred:WALCL-WTREGEN-RRPONTSYD",
            "point_in_time": False, "time_s": 0.0,
            "units": "billion USD",
            "fed_assets_bn": round(liq["fed_assets"][1] / 1000.0, 1),
            "tga_bn": round(liq["tga"][1] / 1000.0, 1),
            "rrp_bn": round(liq["rrp"][1] / 1000.0, 1),
            # WALCL 周三 / WTREGEN·RRP 日频 → 三者 as_of 可能不同日，差 1~2 天属正常
            "components_as_of": {k: v[0] for k, v in liq.items()},
            "basis_note": "净流动性口径＝Fed 总资产 − 财政部一般账户 − 隔夜逆回购（$M 相加后转 $bn）",
        }

    # ---------- FOMC：当时的目标利率区间 + 该区间自何时生效（= 上次决议日） ----------
    fu = _fetch_fred_series_latest(FRED_FEDFUNDS_SERIES["upper"], end=end)
    fl = _fetch_fred_series_latest(FRED_FEDFUNDS_SERIES["lower"], end=end)
    if fu and fl:
        item = {
            "query": "fred:" + ",".join(FRED_FEDFUNDS_SERIES.values()),
            "answer": "fred DFEDTARU/DFEDTARL 目标区间（as-of 当时值）",
            "value": fu[1],                              # 主值＝区间上限 %
            "as_of": fu[0],
            "source": "direct:fred:DFEDTARU-DFEDTARL",
            "point_in_time": False, "time_s": 0.0,
            "target_range": "%.2f-%.2f%%" % (fl[1], fu[1]),
            "target_upper_pct": fu[1], "target_lower_pct": fl[1],
        }
        # 区间生效日＝最近一次上限变化日（往前扫 15 个月窗口）
        rows = _fetch_fred_rows(FRED_FEDFUNDS_SERIES["upper"], _shift(460) or "2000-01-01", end)
        since, prev_val = None, None
        for i in range(len(rows) - 1, 0, -1):
            if rows[i][1] != rows[i - 1][1]:
                since, prev_val = rows[i][0], rows[i - 1][1]
                break
        if since:
            item["range_since"] = since
            item["prev_upper_pct"] = prev_val
            item["last_move"] = ("cut" if fu[1] < prev_val else
                                 "hike" if fu[1] > prev_val else "hold")
        out["FOMC"] = item

    # ---------- MarginDebt：FINRA 保证金债务（第三方再加工 CSV，1997 起月度） ----------
    md = _fetch_margin_debt_rows()
    le = [r for r in md if r["date"] <= end]
    if le:
        cur = le[-1]
        base = [r for r in le[:-1] if r["date"] <= _shift(366)]
        item = {
            "query": MARGIN_DEBT_CSV,
            "answer": "FINRA 保证金债务（debit balances）月度，第三方再加工源",
            "value": round(cur["debit_balances"] / 1000.0, 3),   # $bn
            "as_of": cur["date"],
            "source": "direct:third_party:finra_margin_debt",
            "point_in_time": False, "time_s": 0.0,
            "units": "billion USD",
            "debit_balances_musd": cur["debit_balances"],
            "reliability_note": "FINRA 官方统计的第三方再加工（非官方端点，FRED RAMMDT 已下线）；"
                                "与 YCharts 口径实测一致 → 结论用于趋势/拥挤度判断，不当精确账务用",
        }
        if cur.get("debit_yoy") is not None:
            item["debit_yoy_pct"] = cur["debit_yoy"]
        elif base and base[-1]["debit_balances"]:
            item["debit_yoy_pct"] = round(
                (cur["debit_balances"] / base[-1]["debit_balances"] - 1.0) * 100.0, 2)
        for k in ("spy_yoy", "excess_leverage"):
            if cur.get(k) is not None:
                item[k] = cur[k]
        if cur.get("excess_leverage") is not None:
            item["leverage_note"] = ("excess_leverage＝保证金债务同比 − SPY 同比："
                                     ">10 视为杠杆过热，<0 视为去杠杆")
        out["MarginDebt"] = item

    # ---------- Calendar：as-of 前 7 天发布窗口（复用 P0-5 已实现的 FRED 日历通道） ----------
    try:
        cal = _fetch_fred_calendar(days=7, end=end) or []
    except Exception:
        cal = []
    if cal:
        out["Calendar"] = {
            "query": "fred_calendar:past_7d",
            "answer": "FRED 发布日历 as-of 前 7 天窗口（P0-5 类①，按日期 URL 取数）",
            "value": "%d 条重要发布（as-of 前 7 个自然日）" % len(cal),
            "as_of": end,
            "source": "direct:fred_calendar",
            "point_in_time": False, "time_s": 0.0,
            "count": len(cal),
            "items": cal[:12],
            "basis_note": "历史模式给的是 as-of **之前**的发布（live 给未来 14 天）；"
                          "FRED 日历为事后口径，point_in_time=false（当时是否已公布无法确证）",
        }


def _fetch_margin_debt_rows():
    """FINRA 保证金债务月度 CSV → [{date, debit_balances, debit_yoy, spy_yoy, excess_leverage}]。

    按表头取列（列序变化不影响）；失败 []。带进程内 memo。
    """
    if "margin_debt" in _MACRO_ASOF_MEMO:
        return _MACRO_ASOF_MEMO["margin_debt"]
    rows = []
    try:
        text = None
        if _HAS_CURL_CFFI:
            try:
                r = cfr.get(MARGIN_DEBT_CSV, impersonate="chrome", timeout=25)
                if r.status_code == 200:
                    text = r.text
            except Exception:
                text = None
        if text is None:
            r = requests.get(MARGIN_DEBT_CSV, headers={"User-Agent": _YAHOO_UA}, timeout=(5, 25))
            if r.status_code == 200:
                text = r.text
        if text:
            lines = text.splitlines()
            header = [h.strip() for h in lines[0].split(",")]
            want = {"date": "date", "debit_balances": "debit_balances",
                    "debit_yoy": "debit_yoy", "spy_yoy": "spy_yoy",
                    "excess_leverage": "excess_leverage"}
            idx = {k: header.index(v) for k, v in want.items() if v in header}
            if "date" in idx and "debit_balances" in idx:
                for ln in lines[1:]:
                    p = ln.split(",")
                    if len(p) <= max(idx.values()):
                        continue
                    d = p[idx["date"]].strip()
                    if not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
                        continue
                    rec = {"date": d}
                    for k, i in idx.items():
                        if k == "date":
                            continue
                        rec[k] = safe_float(p[i].strip())
                    if rec.get("debit_balances"):
                        rows.append(rec)
    except Exception:
        rows = []
    _MACRO_ASOF_MEMO["margin_debt"] = rows
    return rows


def _fetch_direct_indicators(end=None):
    """直连源采集（CNN 恐惧贪婪 JSON + Yahoo 指数族）。失败项缺席，由搜索层兜底。

    注：Yahoo 无 "^VIX6D"（404），VIX 期限结构用 ^VIX9D vs ^VIX(=1M)；
    这些是指数符号，不能当 ticker 传给行情批次（不带 ^ 会返回"无数据"）。

    end="YYYY-MM-DD"（历史模式，P0-5 类①）：
      - Yahoo 指数族走 get_history_dataframe(end=) → 自动 as-of；
      - HY_OAS 走 FRED 历史序列（live 不加此层，保持现源优先级）；
      - **P0-6（2026-10-18）**：CPI / Liquidity / FOMC / MarginDebt 由
        `_add_macro_asof_layers` 补 as-of 值（FRED 序列 + FINRA 保证金债务 CSV）；
      - CNN F&G **历史模式跳过**：2026-10-15 实测其 start/end 参数被服务端忽略、
        恒返回当前值——历史模式取用即前视污染，只能标 asof_unsupported；
      - PutCallRatio / FedWatch / Buyback / IPO / Secondary / Earnings 无免费历史档案
        （PutCall 的实测证据见 FRED_SERIES 下方注释）→ 保持 asof_unsupported。
    """
    out = {}
    if not end:  # CNN F&G 仅 live（区间参数无效，历史模式禁用——见 docstring）
        try:
            r = requests.get(CNN_FG_URL, headers={"User-Agent": _YAHOO_UA}, timeout=(5, 20))
            fg = (r.json() or {}).get("fear_and_greed") or {}
            score = fg.get("score")
            if score is not None:
                out["FearGreed"] = {
                    "query": CNN_FG_URL, "answer": "direct: CNN fear_and_greed JSON",
                    "value": "%.2f (%s)" % (float(score), fg.get("rating", "")),
                    "as_of": fg.get("timestamp", ""),
                    "prev_close": safe_float(fg.get("previous_close")),
                    "prev_1_week": safe_float(fg.get("previous_1_week")),
                    "prev_1_month": safe_float(fg.get("previous_1_month")),
                    "prev_1_year": safe_float(fg.get("previous_1_year")),
                    "source": "direct:cnn", "time_s": 0.0}
        except Exception:
            pass
    for key, sym in YAHOO_DIRECT_SYMBOLS.items():
        try:
            df = get_history_dataframe(sym, "1mo", end=end)
        except Exception:
            df = None
        if df is None or not len(df):
            continue
        try:
            val = round(float(df["Close"].iloc[-1]), 2)
        except Exception:
            continue
        out[key] = {"query": "yfinance:" + sym, "answer": "yahoo " + sym + " close",
                    "value": val, "as_of": str(df.index[-1])[:10],
                    "source": "direct:yahoo:" + sym, "time_s": 0.0}
        if end:
            out[key]["point_in_time"] = False
    # HY OAS：仅历史模式启用 FRED 回溯层（live 现源优先级不变——契约红线 #7）
    if end:
        for key, sid in FRED_SERIES.items():
            obs = _fetch_fred_series_latest(sid, end=end)
            if obs:
                out[key] = {"query": "fred:" + sid, "answer": "fred " + sid,
                            "value": round(obs[1], 2), "as_of": obs[0],
                            "source": "direct:fred:" + sid,
                            "point_in_time": False, "time_s": 0.0}
        # P0-6（2026-10-18）：CPI / Liquidity / FOMC / MarginDebt 的 as-of 回溯层
        _add_macro_asof_layers(out, end)
    return out


def fetch_web_indicators(end=None):
    """
    市场指标采集：直连源优先 + OrioSearch 兜底，并对每项做有效值校验。

    Returns:
        dict: {
            "source": "direct+oriosearch", "fetched_at": "...",
            "total": 14, "hit": N,            # hit 仅统计通过量纲校验的真有效值
            "time_s": 墙钟秒, "search_time_s": 各项搜索耗时之和,
            "vix_term_structure": {"VIX":..,"VIX9D":..,"VXN":..,"spread_9D_minus_1M":..},
            "indicators": {key: {"query","answer","value","source","time_s",["as_of"]}}
        }
        indicators[key]["source"] 取值：
          "direct:cnn" / "direct:yahoo:^XX"  直连结构化源（计入 hit）
          "oriosearch"      搜索返回且通过量纲校验（计入 hit）
          "no_answer"       搜索返回"无有效信息"模板（不计入 hit）
          "no_valid_value"  有返回但无该指标目标数值（不计入 hit）
          "失败"            接口无返回/超时
    """
    now = datetime.datetime.now(timezone.utc)
    t_start = time.time()
    # 历史模式（P0-5）：查询模板日期锚定 as-of；搜索层（当前网页）整体禁用
    ref = now
    if end:
        ref = datetime.datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    year, month = ref.year, ref.month
    prev_month = month - 1 if month > 1 else 12
    queries = {}
    for key, tmpl in WEB_INDICATOR_QUERIES_TEMPLATE.items():
        queries[key] = tmpl.format(
            year=year, month_name=ref.strftime("%B"),
            prev_month_name=datetime.datetime(
                year if month > 1 else year - 1, prev_month, 1).strftime("%B"),
            today_str=ref.strftime("%B %d %Y"))

    result = {
        "source": "oriosearch",            # 对外契约不变（test/文档断言此值）
        "source_layers": "direct+oriosearch" if not end else "direct:asof-only",
        "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "requested_end": end,
        "point_in_time": end is None,
        "total": len(queries),
        "hit": 0,
        "time_s": 0.0,
        "search_time_s": 0.0,
        "indicators": {},
    }

    # 1) 直连源（含 VIX 期限结构，供分析层直接用，避免以 ticker 形式误查）
    direct = _fetch_direct_indicators(end=end)
    ts = {}
    for k in ("VIX", "VIX9D", "VXN"):
        d = direct.pop(k, None)
        if d:
            ts[k] = {"value": d["value"], "as_of": d["as_of"]}
    if "VIX9D" in ts and "VIX" in ts:
        try:
            ts["spread_9D_minus_1M"] = round(
                float(ts["VIX9D"]["value"]) - float(ts["VIX"]["value"]), 2)
        except Exception:
            pass
    ts["note"] = "Yahoo 无 ^VIX6D；^VIX 即 1M 口径，勿以不带 ^ 的 ticker 查询"
    result["vix_term_structure"] = ts

    pending = {}
    for k, q in queries.items():
        d = direct.get(k)
        if d:
            item = dict(d)
            item["query"] = q
            result["indicators"][k] = item
            result["hit"] += 1
        else:
            pending[k] = q

    # 历史模式（P0-5）：OrioSearch 抓的是"当前网页"，无法 as-of 回溯 →
    # 未获直连 as-of 值的指标显式标 asof_unsupported（不计 hit，不假装有值）
    if end and pending:
        for k, q in pending.items():
            result["indicators"][k] = {
                "query": q, "answer": "",
                "value": "N/A(当前快照源不支持 as-of 回溯)",
                "source": "asof_unsupported", "point_in_time": False, "time_s": 0.0}
        pending = {}

    # 2) 搜索兜底（限流：3 并发 + 每项随机抖动启动。
    #    全量 14 路并发会同时锤 SearXNG→Google，实测 google 引擎跑十几分钟即被限流掉线；
    #    降并发 + 错峰后单引擎响应更稳，总耗时约 1.5~2 分钟，可接受。）
    if pending:
        with ThreadPoolExecutor(max_workers=3) as executor:
            fut_map = {}
            for k, q in pending.items():
                fut_map[executor.submit(_search_oriosearch, q)] = (k, time.time())
            for fut in as_completed(fut_map):
                key, t0 = fut_map[fut]
                answer = fut.result()
                dt = time.time() - t0
                result["search_time_s"] += dt
                if answer and _is_no_answer(answer):
                    ok, val, src = False, "N/A(无有效答案)", "no_answer"
                    # 尾部对冲豁免：模板词若出现在真值之后（否定仅针对部分来源），
                    # 且量纲校验通过，则保留真值计为 hit（2026-10-10 HY_OAS 303 bps 案例）
                    v_ok, v_val = _validate_indicator_value(key, answer)
                    if v_ok and _value_before_negation(answer):
                        ok, val, src = True, v_val, "oriosearch"
                elif answer:
                    ok, val = _validate_indicator_value(key, answer)
                    src = "oriosearch" if ok else "no_valid_value"
                    if not ok:
                        val = "N/A(答案无目标数值)"
                else:
                    ok, val, src = False, "N/A", "失败"
                if ok:
                    result["hit"] += 1
                result["indicators"][key] = {
                    "query": pending[key],
                    "answer": (answer or "")[:300],
                    "value": val,
                    "source": src,
                    "time_s": round(dt, 2),
                }

    result["time_s"] = round(time.time() - t_start, 2)
    result["search_time_s"] = round(result["search_time_s"], 2)
    return result


def _search_oriosearch(query):
    """OrioSearch 单次搜索，返回答案文本（失败返回 None）

    策略：
      1. 优先取 answer 字段（有 AI 摘要时最快）
      2. answer 为 None 时，从 results 列表中提取前 3 条的 title+content
         作为推算文本，这样即使 OrioSearch 不生成 AI 摘要也能拿到有用数据
      3. advanced 超时 → 降级 basic 重试
      4. 多端点回退：主端点 失败/超时/无结果 → 自动切 fallback 端点重试
         （配置 orio_config.json 的 "fallback"，如 本地 Docker → 远程 search.my-gun.top）
    """
    # 随机错峰 0~1.5s：避免多路并发同时打到 SearXNG/Google（降低被限流概率）
    time.sleep(random.uniform(0, 1.5))
    payload = {
        "query": query,
        "search_depth": "advanced",
        "max_results": 5,
        "include_answer": True,
        # 2026-09-21（远程 search.my-gun.top）：general 主题失效、news 可恢复财经命中；
        # 2026-10-09（本地 Docker SearXNG）：实测相反——news 引擎覆盖稀疏（HY_OAS/PutCallRatio 0 结果），
        # general 有结果且 include_answer 能答出目标数值，故本地用 general。
        # 服务仅接受 general|news 两种 topic（finance/science 会 422）
        "topic": "general",
    }

    def _try_request(url, api_key, payload_copy):
        try:
            r = requests.post(f"{url}/search", json=payload_copy,
                              headers={"Authorization": f"Bearer {api_key}"},
                              timeout=(5, 90))
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

    def _run_attempts(url, api_key):
        """单端点：先 advanced，超时降级 basic 重试；__TIMEOUT__ 视为失败返回 None"""
        p = dict(payload)
        result = _try_request(url, api_key, p)
        if result == "__TIMEOUT__":
            p["search_depth"] = "basic"
            result = _try_request(url, api_key, p)
        return result if result != "__TIMEOUT__" else None

    result = _run_attempts(ORIO_URL.rstrip("/"), ORIOSEARCH_API_KEY)
    # 4) 多端点回退：主端点拿不到结果（无 answer 也无 results / 超时 / 报错）时切 fallback 端点
    if not result and ORIO_FALLBACK_ENABLED:
        result = _run_attempts(ORIO_FALLBACK_URL, ORIO_FALLBACK_KEY)
    return result


def _extract_indicator_value(answer):
    """[已废弃] 会输出无单位裸数字（曾把 S&P 500 的 500 当数值）；改用 _validate_indicator_value。从 answer 中提取关键数值（用于展示）"""
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
    """web-indicators 文本输出：标出来源层与有效性，OK=计入 hit 的真有效值"""
    lines = ["[市场指标 web-indicators]",
             "有效命中: %s/%s  墙钟: %ss  搜索累计: %ss" % (
                 data.get("hit"), data.get("total"),
                 data.get("time_s"), data.get("search_time_s"))]
    ts = data.get("vix_term_structure") or {}
    vals = ["%s=%s@%s" % (k, v.get("value"), str(v.get("as_of"))[:10])
            for k, v in ts.items() if isinstance(v, dict)]
    if vals:
        lines.append("VIX 期限结构: " + "  ".join(vals))
    if "spread_9D_minus_1M" in ts:
        lines.append("  9D-1M 价差: %s（负值=近月更紧/contango 收敛）" % ts["spread_9D_minus_1M"])
    for k, v in (data.get("indicators") or {}).items():
        if not isinstance(v, dict):
            continue
        src = str(v.get("source", ""))
        ok = src == "oriosearch" or src.startswith("direct")
        row = "%s%-14s %-20s %s" % ("OK " if ok else "  ", k, src, str(v.get("value", ""))[:80])
        if v.get("as_of"):
            row += "  @" + str(v["as_of"])[:10]
        lines.append(row)
        ans = (v.get("answer") or "").strip().replace("\n", " ")
        if ans and not ans.startswith("direct") and not ans.startswith("yahoo"):
            lines.append("     " + ans[:150])
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


def calc_rsi(df, period=14):
    """RSI(14)，Wilder 平滑；数据不足返回 None。

    C3（2026-10-18）：00_index Step 5 技术面过滤要用 RSI，此前无通道 →
    AI 逐只 web_fetch finviz（反爬超时）。这里用已取到的 K 线本地算，零额外请求。
    历史模式（--end）下 df 已按截止日截断 → 无前视。
    """
    try:
        close = np.asarray(df["Close"].values, dtype="float64")
    except Exception:
        return None
    if len(close) < period + 1:
        return None
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = float(np.mean(gain[:period]))
    avg_loss = float(np.mean(loss[:period]))
    for g, l in zip(gain[period:], loss[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def calc_macd(df, fast=12, slow=26, signal=9):
    """MACD(12,26,9) → (macd, signal, hist)；数据不足返回 (None, None, None)。"""
    try:
        close = pd.Series(np.asarray(df["Close"].values, dtype="float64"))
    except Exception:
        return None, None, None
    if len(close) < slow + signal:
        return None, None, None
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return (round(float(macd_line.iloc[-1]), 4), round(float(signal_line.iloc[-1]), 4),
            round(float((macd_line - signal_line).iloc[-1]), 4))


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


# ==================== 期权链（入场计划，2026-10-18 新增，opt-in） ====================
# 用途：00_index Step 8 入场计划 + 美股做空选择.md §六「优先使用 put 期权做空」。
# 此前技能内**没有任何期权通道** → AI 只能标 missing 或 web_fetch 期权页（实测反爬、超时）。
# 数据源：Yahoo v7 options（复用既有 socks5h 代理 + crumb；yfinance 路径实测常被 429，仅作兜底）。
# 前视红线：期权链只有**当前**快照，不可 as-of 回溯 → main() 的 P0-5 门禁拒绝 --end 组合。


def _pick_expiry_horizons(iso_list, horizons=_OPTIONS_TARGET_HORIZONS):
    """按短/中/长三档时间跨度（默认 21/63/126 自然日）各挑最近的到期日，去重。"""
    today = datetime.date.today()
    dated = []
    for s in iso_list:
        try:
            d = datetime.date.fromisoformat(s)
        except Exception:
            continue
        if (d - today).days > 0:
            dated.append((s, (d - today).days))
    out = []
    for h in horizons:
        if not dated:
            break
        best = min(dated, key=lambda x: abs(x[1] - h))
        if best[0] not in out:
            out.append(best[0])
    return out


def _opt_contract_row(c, side, spot):
    """Yahoo 合约 → 报告可直接用的紧凑字段（权利金成本、盈亏平衡、点差、虚实值）"""
    strike = safe_float(c.get("strike"))
    bid = safe_float(c.get("bid"))
    ask = safe_float(c.get("ask"))
    last = safe_float(c.get("lastPrice"))
    iv = safe_float(c.get("impliedVolatility"))
    # 报价可能为 0（无买盘/停牌）→ 用 lastPrice 兜底，避免 premium=0 的假便宜
    premium = ask if (ask and ask > 0) else (last if (last and last > 0) else None)
    mid = ((bid + ask) / 2.0) if (bid is not None and ask is not None and (bid + ask) > 0) else None
    row = {
        "side": side,
        "contract": c.get("contractSymbol"),
        "strike": strike,
        "last_price": last,
        "bid": bid,
        "ask": ask,
        "iv": round(iv, 4) if iv is not None else None,
        "open_interest": c.get("openInterest"),
        "volume": c.get("volume"),
        "in_the_money": bool(c.get("inTheMoney")),
    }
    if premium is not None:
        row["premium"] = round(premium, 2)
        if spot:
            row["premium_pct_of_spot"] = round(premium / spot, 4)   # 权利金占现价比 ≈ 粗略成本
    if mid and bid is not None and ask is not None:
        row["bid_ask_spread_pct"] = round((ask - bid) / mid, 4)      # >15% = 流动性差，不宜入场
    if premium is not None and strike is not None:
        row["breakeven"] = round(strike - premium, 2) if side == "put" else round(strike + premium, 2)
    if strike and spot:
        dist = (strike - spot) / spot
        row["strike_vs_spot_pct"] = round(dist, 4)
        row["moneyness"] = "ITM" if c.get("inTheMoney") else ("ATM" if abs(dist) <= 0.02 else "OTM")
    return row


def _options_via_yfinance(ticker, want_iso):
    """兜底路径：yfinance（实测常被 Yahoo 429，仅代理路径不可用时尝试）。
    返回 {iso: {"calls": [...], "puts": [...]}}；失败返回 {}。"""
    out = {}
    try:
        tk = yf.Ticker(ticker)
        chain_dates = want_iso or list(tk.options or [])
    except Exception:
        return out
    for d in chain_dates:
        try:
            opt = tk.option(d)
            calls = opt.calls.to_dict("records") if getattr(opt, "calls", None) is not None else []
            puts = opt.puts.to_dict("records") if getattr(opt, "puts", None) is not None else []
            out[d] = {"calls": calls, "puts": puts}
        except Exception:
            continue
    return out


def fetch_option_chain(ticker, expiries=None, side="both", strike_pct=0.20,
                       include_all=False, max_contracts=24):
    """取期权链（当前快照，30 分钟缓存）。

    expiries: ["YYYY-MM-DD", ...]；None = 按 _OPTIONS_TARGET_HORIZONS 自动选短/中/长三档
    side:     put / call / both（做空入场看 put，对冲看 call）
    strike_pct: 只保留现价 ±strike_pct 的行权价（include_all=True 不过滤）
    max_contracts: 输出合约上限（按未平仓量降序保留，控制上下文体积）

    返回 dict：spot / expiries_available / expiries_selected / expiry_summary /
    contracts / put_call_oi_ratio / point_in_time=False。
    """
    import statistics
    ticker = (ticker or "").upper().strip()
    result = {
        "ticker": ticker,
        "as_of": datetime.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "point_in_time": False,
        "data_source": "yahoo_v7_options",
        "时效说明": "期权链与做空字段一样只有当前快照 → 历史回放(--end/--as-of)不支持，"
                    "报告须标 point_in_time=false",
    }
    if not ticker:
        result["error"] = "缺少 ticker"
        return result

    cache_key = hashlib.md5(
        f"{ticker}|{','.join(expiries or [])}|{side}|{strike_pct}|{include_all}|{max_contracts}"
        .encode()).hexdigest()
    cache_path = os.path.join(_OPTIONS_CACHE_DIR, cache_key + ".json")
    if not _RUNTIME.get("no_cache") and os.path.exists(cache_path) \
            and time.time() - os.path.getmtime(cache_path) < _OPTIONS_CACHE_TTL:
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            cached["cache_hit"] = True
            return cached
        except Exception:
            pass

    url = f"https://query1.finance.yahoo.com/v7/finance/options/{ticker}"
    base = _fetch_proxy_json(url)
    ts_by_iso, iso_all, spot = {}, [], None
    if base:
        try:
            res0 = base["optionChain"]["result"][0]
            q = res0.get("quote") or {}
            spot = safe_float(q.get("regularMarketPrice")) or safe_float(q.get("postMarketPrice"))
            for ts in res0.get("expirationDates") or []:
                iso = datetime.date.fromtimestamp(int(ts)).isoformat()
                ts_by_iso[iso] = int(ts)
                iso_all.append(iso)
        except Exception:
            base = None
    if not base:
        result["data_source"] = "yfinance(fallback)"
        try:
            iso_all = sorted(list(yf.Ticker(ticker).options or []))
        except Exception as e:
            result["error"] = (f"期权链取数失败：代理路径不可用且 yfinance 也失败 "
                               f"({type(e).__name__}: {str(e)[:120]})")
            return result

    want = [d for d in (expiries or []) if d] or _pick_expiry_horizons(iso_all)
    want = [d for d in want if d in iso_all] if iso_all else want
    result["expiries_available"] = iso_all
    result["expiries_selected"] = want
    result["spot"] = spot

    chains = {}
    for iso in want:
        if base:
            js = _fetch_proxy_json(url, params={"date": str(ts_by_iso.get(iso, ""))})
            try:
                opts = js["optionChain"]["result"][0].get("options") or []
                if opts:
                    chains[iso] = opts[0]
            except Exception:
                continue
        else:
            y = _options_via_yfinance(ticker, want)
            for k, v in y.items():
                chains[k] = v
            break
    if not chains:
        result["error"] = "所选到期日均无合约数据（可能到期日无效或数据源限流）"
        return result

    today = datetime.date.today()
    contracts, expiry_summary = [], []
    oi_put_total = oi_call_total = 0
    iv_atm_put = []
    for iso, opt in sorted(chains.items()):
        calls, puts = opt.get("calls") or [], opt.get("puts") or []
        try:
            dte = (datetime.date.fromisoformat(iso) - today).days
        except Exception:
            dte = None
        row = {"expiration": iso, "days_to_expiry": dte, "contracts": len(calls) + len(puts)}
        for lst, s in ((calls, "call"), (puts, "put")):
            oi = int(sum(safe_float(x.get("openInterest"), 0) or 0 for x in lst))
            ivs = [v for v in (safe_float(x.get("impliedVolatility")) for x in lst) if v]
            row[f"oi_{s}_total"] = oi
            row[f"iv_median_{s}"] = round(statistics.median(ivs), 4) if ivs else None
            if s == "put":
                oi_put_total += oi
            else:
                oi_call_total += oi
        expiry_summary.append(row)
        for lst, s in ((calls, "call"), (puts, "put")):
            if side not in ("both", s):
                continue
            for c in lst:
                st = safe_float(c.get("strike"))
                if not include_all and spot and st and abs(st - spot) / spot > strike_pct:
                    continue
                r = _opt_contract_row(c, s, spot)
                r["expiration"] = iso
                r["days_to_expiry"] = dte
                contracts.append(r)
                if s == "put" and r.get("moneyness") == "ATM" and r.get("iv"):
                    iv_atm_put.append(r["iv"])

    dropped = 0
    if not include_all and max_contracts and len(contracts) > max_contracts:
        contracts.sort(key=lambda r: -(safe_float(r.get("open_interest"), 0) or 0))
        dropped = len(contracts) - max_contracts
        contracts = contracts[:max_contracts]
    contracts.sort(key=lambda r: (r.get("expiration") or "", r.get("side") or "",
                                  abs(safe_float(r.get("strike_vs_spot_pct"), 0) or 0)))

    result["expiry_summary"] = expiry_summary
    result["contracts"] = contracts
    result["put_call_oi_ratio"] = (round(oi_put_total / oi_call_total, 3)
                                   if oi_call_total else None)
    result["iv_atm_put_median"] = round(statistics.median(iv_atm_put), 4) if iv_atm_put else None
    result["审计"] = {"到期日数": len(chains), "返回合约数": len(contracts),
                      "合约裁剪数": dropped,
                      "行权价过滤": "关闭" if include_all else f"现价±{strike_pct:.0%}"}
    if spot is None:
        result["missing"] = ["spot（yfinance 兜底路径无现价 → 未做行权价过滤）"]

    try:
        os.makedirs(_OPTIONS_CACHE_DIR, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
    except Exception:
        pass
    return result


def _format_options_text(data) -> str:
    """期权链的可读文本输出（默认 --output text）"""
    lines = [f"=== {data.get('ticker')} 期权链（{data.get('data_source')}） ==="]
    if data.get("error"):
        lines.append(f"❌ {data['error']}")
        return "\n".join(lines)
    lines.append(f"现价: {data.get('spot')} | 可用到期日 {len(data.get('expiries_available') or [])} 个"
                 f" | 选取: {', '.join(data.get('expiries_selected') or [])}")
    lines.append(f"put/call 未平仓量比: {data.get('put_call_oi_ratio')} "
                 f"| ATM put IV 中位: {data.get('iv_atm_put_median')}")
    lines.append("")
    for row in data.get("expiry_summary") or []:
        lines.append(f"[{row['expiration']}] DTE={row.get('days_to_expiry')} "
                     f"OI_put={row.get('oi_put_total')} OI_call={row.get('oi_call_total')} "
                     f"IV_put={row.get('iv_median_put')} IV_call={row.get('iv_median_call')}")
    lines.append("")
    for c in data.get("contracts") or []:
        lines.append(
            f"  {c.get('expiration')} {c.get('side'):4s} K={c.get('strike')} "
            f"{c.get('moneyness'):3s} bid={c.get('bid')} ask={c.get('ask')} "
            f"IV={c.get('iv')} OI={c.get('open_interest')} vol={c.get('volume')} "
            f"权利金/现价={c.get('premium_pct_of_spot')} 盈亏平衡={c.get('breakeven')} "
            f"点差={c.get('bid_ask_spread_pct')}")
    a = data.get("审计") or {}
    lines.append(f"\n审计: {a}")
    return "\n".join(lines)


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

  # 历史模式（回放/回测，v2 新增；缺省不传 = live，行为与旧版一致）
  python get_market_data.py --market us_stocks --tickers AAPL,MSFT --end 2025-06-30 --output json
  python get_market_data.py --market us_stocks --tickers AAPL --end 2025-06-30 \\
      --cache-dir ../replay/cache --no-cache   # 独立缓存目录 + 强制重取
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

    # ---------- 期权链（2026-10-18 新增；opt-in 独立模式，不影响任何现有输出契约） ----------
    parser.add_argument("--options", type=str, default=None, metavar="TICKER",
                        help="取期权链（put/call、IV、未平仓量、权利金占现价比、盈亏平衡、点差）"
                             "——供 Step 8 入场计划与「优先用 put 做空」；只有当前快照，--end 下拒绝")
    parser.add_argument("--opt-expiry", dest="opt_expiry", type=str, default=None,
                        help="到期日（逗号分隔 YYYY-MM-DD）；缺省＝自动选短/中/长三档(21/63/126 天)")
    parser.add_argument("--opt-side", dest="opt_side", choices=["put", "call", "both"],
                        default="both", help="期权链只取 put / 只取 call / 两者（默认 both）")
    parser.add_argument("--opt-strike-pct", dest="opt_strike_pct", type=float, default=0.20,
                        help="只保留现价 ±该比例的行使价（默认 0.20＝±20%）")
    parser.add_argument("--opt-all", dest="opt_all", action="store_true",
                        help="不过滤行使价、不按未平仓量裁剪（输出很大，慎用）")

    # ---------- 历史模式 / 缓存与批量控制（v2 新增；全部缺省 = 现有生产行为） ----------
    parser.add_argument("--end", "--as-of", dest="end", type=str, default=None,
                        metavar="YYYY-MM-DD",
                        help="历史模式：取数截止日（含当日），K线/指标均截至该日；缺省=live。"
                             "历史模式不进 live TTL 缓存池，走独立不可变窗口缓存")
    parser.add_argument("--no-cache", "--refresh", dest="no_cache", action="store_true",
                        help="不读两层活跃缓存（live 30min K线 / 12h info；历史模式不可变缓存），仍照常写入")
    parser.add_argument("--cache-dir", dest="cache_dir", type=str, default=None,
                        help="历史模式不可变缓存目录（默认 scripts/.yf_history_cache_asof；回放建议指向 replay/ 下独立目录）")
    parser.add_argument("--fields", type=str, default=None,
                        help="输出字段白名单（逗号分隔；默认全字段。ticker/error/as_of 等元信息始终保留）")
    parser.add_argument("--minimal", action="store_true",
                        help="精简输出（仅价格/涨跌幅/均线/52周分位/均量等核心字段）")
    parser.add_argument("--retries", type=int, default=3,
                        help="单 ticker 取数重试次数（默认 3，与旧版一致）")
    parser.add_argument("--concurrency", type=int, default=3,
                        help="批量并发线程数（默认 3 + 随机延迟，限流保护设计，勿随意调大）")
    parser.add_argument("--out", type=str, default=None,
                        help="结果 JSON 落盘路径；给了 --out 时 stdout 只打印一行摘要"
                             "（默认不传 = 现有 stdout 契约完全不变）")
    parser.add_argument("--sector-median", dest="sector_median", action="store_true",
                        help="按 sector 算批次内 PE/P-S/EV-EBITDA 中位数并附到每条记录"
                             "（opt-in，默认关闭；样本<3 的板块不写）")

    args = parser.parse_args()

    # ---------- 装配运行时开关（默认值下 _RUNTIME 与旧版行为完全一致） ----------
    _RUNTIME["no_cache"] = bool(args.no_cache)
    _RUNTIME["retries"] = max(1, args.retries)
    _RUNTIME["concurrency"] = max(1, args.concurrency)
    if args.cache_dir:
        _RUNTIME["asof_dir"] = os.path.abspath(args.cache_dir)
    if args.end:
        try:
            args.end = _validate_end_date(args.end)
        except ValueError as e:
            print(f"❌ {e}")
            sys.exit(1)
        # --end 与 --fetch-url 的组合按 P0-5 三分类放行/拒绝：
        #   calendar / web-indicators：类①可回溯源（FRED 窗口、Yahoo 指数族、CNN 区间、FRED OAS）→ 放行
        #   product-all-info(news/ratings) / ICI / search-ticker：类②③不可回溯 → 拒绝
        if args.options or args.search_ticker or (
                args.fetch_url and args.fetch_url not in ("calendar", "web-indicators")):
            print("❌ --end 历史模式下，news/ratings（当前共识/RSS 近期条目）、ICI（当前周报）、"
                  "search-ticker（当前索引）、options（期权链只有当前挂牌合约）不支持 as-of 回溯；"
                  "可回溯的 calendar/web-indicators 请直接组合使用"
                  "（详见 Task-代码取数前视能力.md P0-5）")
            sys.exit(1)

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

    # ========== --options 独立模式（期权链，2026-10-18 新增） ==========
    if args.options and not args.fetch_url:
        data = fetch_option_chain(
            args.options,
            expiries=[x.strip() for x in args.opt_expiry.split(",") if x.strip()]
                     if args.opt_expiry else None,
            side=args.opt_side, strike_pct=args.opt_strike_pct, include_all=args.opt_all)
        # 与批量模式同款 --out 语义：落盘 + stdout 一行摘要（不传 --out = 现有输出不变）
        if args.out:
            out_dir = os.path.dirname(os.path.abspath(args.out))
            os.makedirs(out_dir, exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            a = data.get("审计") or {}
            print(f"已写入 {args.out} | ticker={data.get('ticker')} 现价={data.get('spot')} "
                  f"到期日{a.get('到期日数', 0)}个 合约{a.get('返回合约数', 0)}条 "
                  f"裁剪{a.get('合约裁剪数', 0)} put/callOI比={data.get('put_call_oi_ratio')} "
                  f"ATMput_IV={data.get('iv_atm_put_median')} "
                  f"{'缓存命中' if data.get('cache_hit') else '实时'}"
                  + (f" | 错误: {data['error']}" if data.get("error") else ""))
            return
        if args.output == "json":
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(_format_options_text(data))
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
            data = fetch_web_indicators(end=args.end)
            if args.output == "json":
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                print(_format_web_indicators_text(data))
            return

        if args.fetch_url == "calendar":
            data = fetch_economic_calendar(end=args.end)
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

    # 获取数据（历史模式自动带 adj_close，回测持有期收益用；live 不变）
    data = fetch_batch(unique_tickers, labels, end=args.end, adj=bool(args.end))

    # C2: 板块中位数（opt-in，默认关闭）——放在字段过滤**之前**，让 --fields 也能选到中位数字段
    if getattr(args, "sector_median", False):
        data = _apply_sector_median(data)

    # 字段过滤（默认全字段；error 项与元信息字段始终保留，subprocess 消费者契约不受影响）
    data = _apply_field_filter(data, args)

    # 输出（顶层结构只增不改：requested_end/point_in_time/失败项 为 v2 新增字段）
    fails = [it for it in data if isinstance(it, dict) and it.get("error")]
    result = {
        "market": args.market,
        "batch": args.batch,
        "fetched_at": datetime.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(data),
        "requested_end": args.end,
        "point_in_time": args.end is None,
        "data": data,
    }
    if fails:
        result["失败项"] = [{"ticker": it.get("ticker"), "error": str(it.get("error"))[:200]}
                           for it in fails]
    # A6(2026-10-18): 显式 --out 时完整 JSON 落盘、stdout 只给一行摘要；
    # 不传 --out 时 stdout 与改前逐字节一致（保护 refill / generate_candidate_pool 等消费者）
    if args.out:
        out_dir = os.path.dirname(os.path.abspath(args.out))
        os.makedirs(out_dir, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"已写入 {args.out} | market={args.market} batch={args.batch or 'custom'} "
              f"count={len(data)} 失败={len(fails)} 截至={args.end or 'live'}")
        return
    if args.output == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        batch_desc = BATCHES.get(args.batch, {}).get("desc", "") if args.batch else ""
        title = f"[{args.market}] {batch_desc}" if batch_desc else f"[{args.market}] 自定义"
        if args.end:
            title += f"（截至 {args.end}）"
        print(format_text_output(data, title=title))


if __name__ == "__main__":
    main()
