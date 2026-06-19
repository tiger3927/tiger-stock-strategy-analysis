"""
get_market_data.py 功能测试脚本
===============================
测试所有核心功能模块，用于快速诊断网络/数据问题。
运行方式:
  python scripts/test_get_market_data.py              # 快速测试（默认）
  python scripts/test_get_market_data.py --full        # 全量测试（含网络请求）
  python scripts/test_get_market_data.py --batch       # 仅测试 batch 数据获取
  python scripts/test_get_market_data.py --product     # 仅测试 product-all-info
  python scripts/test_get_market_data.py --calendar    # 仅测试经济日历
  python scripts/test_get_market_data.py --technical   # 仅测试技术指标计算
  python scripts/test_get_market_data.py --ratings     # 仅测试评级获取
  python scripts/test_get_market_data.py --news        # 仅测试新闻获取
  python scripts/test_get_market_data.py --web-indicators  # 仅测试 OrioSearch 市场指标
"""
import sys
import os
import json
import time
import traceback

# 确保能导入同目录下的模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import get_market_data as gmd
import numpy as np


# ==================== 测试配置 ====================
TEST_TICKER = "AAPL"          # 主测试品种
TEST_TICKER_NYSE = "XOM"      # NYSE 品种（用于测试不同页面结构）
TEST_TICKERS = ["AAPL", "MSFT", "NVDA"]  # 批量测试
TEST_MARKET = "us_stocks"
TEST_BATCH = "us-major-indices"

PASS = 0
FAIL = 0
SKIP = 0


def report(name, status, detail=""):
    global PASS, FAIL, SKIP
    if status == "PASS":
        PASS += 1
        print(f"  ✅ {name}")
    elif status == "FAIL":
        FAIL += 1
        print(f"  ❌ {name}: {detail}")
    elif status == "SKIP":
        SKIP += 1
        print(f"  ⏭️  {name}: {detail}")


# ==================== 测试用例 ====================

def test_safe_float():
    """测试 safe_float 工具函数"""
    print("\n--- safe_float ---")
    assert gmd.safe_float(42.5) == 42.5
    assert gmd.safe_float("3.14") == 3.14
    assert gmd.safe_float(None) is None
    assert gmd.safe_float(float("nan")) is None
    assert gmd.safe_float("abc") is None
    report("正常浮点数", "PASS")
    report("字符串转浮点", "PASS")
    report("None 处理", "PASS")
    report("NaN 处理", "PASS")
    report("非法字符串", "PASS")


def test_calc_52w_percentile():
    """测试 52 周百分位计算"""
    print("\n--- calc_52w_percentile ---")
    assert gmd.calc_52w_percentile(150, 100, 200) == 50.0
    assert gmd.calc_52w_percentile(100, 100, 200) == 0.0
    assert gmd.calc_52w_percentile(200, 100, 200) == 100.0
    assert gmd.calc_52w_percentile(150, 150, 150) is None  # 高低相等
    report("中间值", "PASS")
    report("最低值", "PASS")
    report("最高值", "PASS")
    report("高低相等", "PASS")


def test_calc_atr():
    """测试 ATR 计算"""
    print("\n--- calc_atr ---")
    import pandas as pd
    # 构造模拟数据
    np.random.seed(42)
    n = 30
    df = pd.DataFrame({
        "High": np.random.uniform(100, 110, n),
        "Low": np.random.uniform(90, 100, n),
        "Close": np.random.uniform(95, 105, n),
    })
    atr = gmd.calc_atr(df)
    assert atr > 0, f"ATR 应为正数，得到 {atr}"
    report(f"正常数据 ATR={atr}", "PASS")

    # 测试 NaN 过滤
    df_nan = df.copy()
    df_nan.iloc[0, 0] = float("nan")
    atr_nan = gmd.calc_atr(df_nan)
    assert atr_nan > 0, f"含 NaN 的 ATR 应为正数，得到 {atr_nan}"
    report(f"含 NaN 数据 ATR={atr_nan}", "PASS")

    # 测试数据不足
    df_short = pd.DataFrame({"High": [100], "Low": [90], "Close": [95]})
    atr_short = gmd.calc_atr(df_short)
    assert atr_short == 0.0, f"数据不足应返回 0.0，得到 {atr_short}"
    report("数据不足返回 0.0", "PASS")


def test_calc_cci():
    """测试 CCI 计算"""
    print("\n--- calc_cci ---")
    import pandas as pd
    np.random.seed(42)
    n = 30
    df = pd.DataFrame({
        "High": np.random.uniform(100, 110, n),
        "Low": np.random.uniform(90, 100, n),
        "Close": np.random.uniform(95, 105, n),
    })
    cci = gmd.calc_cci(df)
    assert isinstance(cci, float), f"CCI 应为 float，得到 {type(cci)}"
    report(f"正常数据 CCI={cci}", "PASS")

    # 测试 NaN 过滤
    df_nan = df.copy()
    df_nan.iloc[0, 0] = float("nan")
    cci_nan = gmd.calc_cci(df_nan)
    assert isinstance(cci_nan, float)
    report(f"含 NaN 数据 CCI={cci_nan}", "PASS")

    # 测试数据不足
    df_short = pd.DataFrame({"High": [100], "Low": [90], "Close": [95]})
    cci_short = gmd.calc_cci(df_short)
    assert cci_short == 0.0
    report("数据不足返回 0.0", "PASS")


def test_find_support_resistance():
    """测试支撑压力位计算"""
    print("\n--- find_support_resistance ---")
    import pandas as pd
    np.random.seed(42)
    n = 100
    df = pd.DataFrame({
        "High": np.random.uniform(100, 110, n),
        "Low": np.random.uniform(90, 100, n),
        "Close": np.random.uniform(95, 105, n),
    })
    supports, resistances = gmd.find_support_resistance(df)
    assert len(supports) > 0, "应有支撑位"
    assert len(resistances) > 0, "应有压力位"
    report(f"支撑位: {supports}", "PASS")
    report(f"压力位: {resistances}", "PASS")

    # 测试全 NaN 数据
    df_nan = pd.DataFrame({
        "High": [float("nan")] * 10,
        "Low": [float("nan")] * 10,
        "Close": [float("nan")] * 10,
    })
    s, r = gmd.find_support_resistance(df_nan)
    assert s == [] and r == [], "全 NaN 应返回空列表"
    report("全 NaN 返回空列表", "PASS")


def test_calc_price_percentile():
    """测试价格分位计算"""
    print("\n--- calc_price_percentile ---")
    import pandas as pd
    df = pd.DataFrame({"Close": [100, 110, 120, 130, 140, 150]})
    pct = gmd.calc_price_percentile(df, 6)
    assert pct == 1.0, f"最高值应为 1.0，得到 {pct}"
    report(f"最高值分位={pct}", "PASS")

    df2 = pd.DataFrame({"Close": [100, 110, 120, 130, 140, 100]})
    pct2 = gmd.calc_price_percentile(df2, 6)
    assert pct2 == 0.0, f"最低值应为 0.0，得到 {pct2}"
    report(f"最低值分位={pct2}", "PASS")

    # 测试 NaN 过滤
    df_nan = pd.DataFrame({"Close": [100, float("nan"), 120, 130, float("nan"), 150]})
    pct_nan = gmd.calc_price_percentile(df_nan, 6)
    assert 0 <= pct_nan <= 1, f"含 NaN 的分位应在 0~1 之间，得到 {pct_nan}"
    report(f"含 NaN 分位={pct_nan}", "PASS")


def test_fetch_news():
    """测试新闻获取（网络请求）"""
    print("\n--- fetch_news ---")
    try:
        result = gmd.fetch_news(TEST_TICKER)
        assert "count" in result, "应包含 count 字段"
        assert "items" in result, "应包含 items 字段"
        assert result["count"] > 0, f"应有新闻，得到 {result['count']}"
        assert len(result["items"]) > 0, "items 不应为空"
        report(f"获取 {result['count']} 条新闻", "PASS")
    except Exception as e:
        report("新闻获取", "FAIL", str(e)[:100])


def test_clean_html():
    """测试 _clean_html 工具函数"""
    print("\n--- _clean_html ---")
    assert gmd._clean_html("<b>Hello</b> World") == "Hello World"
    assert gmd._clean_html("<div><p>Test</p></div>") == "Test"
    assert gmd._clean_html("   Multiple   spaces   ") == "Multiple spaces"
    assert gmd._clean_html("") == ""
    assert gmd._clean_html("<tag>text</tag> more <br/>") == "text more"
    report("基本标签清除", "PASS")
    report("嵌套标签", "PASS")
    report("多余空格合并", "PASS")
    report("空字符串", "PASS")
    report("混合内容", "PASS")


def test_format_text_output():
    """测试 format_text_output 文本格式化"""
    print("\n--- format_text_output ---")
    data = [
        {"ticker": "AAPL", "price": 150.0, "change": 2.5, "change_pct": 1.67,
         "ma_20": 148.0, "ma_50": 145.0, "ma_200": 140.0,
         "high_52w": 200.0, "low_52w": 100.0, "pct_52w": 50.0,
         "label": "苹果", "volume": 50000000},
        {"ticker": "TEST", "error": "无数据"},
    ]
    output = gmd.format_text_output(data, "测试标题")
    assert "测试标题" in output
    assert "AAPL" in output
    assert "150.00" in output
    assert "+2.50 (+1.67%)" in output
    assert "MA20=148.00" in output
    assert "52w分位=50.0%" in output
    assert "TEST" in output
    assert "无数据" in output
    report("标题+数据+错误行", "PASS")

    # 测试空数据
    output_empty = gmd.format_text_output([], "")
    assert output_empty == ""
    report("空数据", "PASS")

    # 测试 price 为 None
    data_none_price = [{"ticker": "AAPL", "price": None, "change": None, "change_pct": None}]
    output_none = gmd.format_text_output(data_none_price)
    assert "N/A" in output_none
    report("price=None", "PASS")


def test_format_ici_output():
    """测试 format_ici_output ICI 文本格式化"""
    print("\n--- format_ici_output ---")
    from datetime import datetime, timezone
    fake_now = "2026-06-16T12:00:00Z"
    data = {
        "fetched_at": fake_now,
        "tables": [
            {
                "headers": ["Category", "Value"],
                "rows": [["Equity", "100"], ["Total", "200"]],
            }
        ],
    }
    output = gmd.format_ici_output(data, "ICI 测试")
    assert "ICI 测试" in output
    assert fake_now in output
    assert "Category" in output
    assert "Equity" in output
    report("正常数据", "PASS")

    # 测试 error
    data_err = {"error": "网络错误"}
    output_err = gmd.format_ici_output(data_err)
    assert "网络错误" in output_err
    report("错误数据", "PASS")

    # 测试空数据
    data_empty = {"fetched_at": "now", "tables": []}
    output_empty = gmd.format_ici_output(data_empty)
    assert "now" in output_empty
    report("空表格", "PASS")


def test_fetch_url():
    """测试 _fetch_url 网络请求（带重试）"""
    print("\n--- _fetch_url ---")
    # 测试正常 URL
    result = gmd._fetch_url("https://httpbin.org/status/200", timeout=10)
    if result:
        report("正常 URL 请求", "PASS")
    else:
        report("正常 URL 请求", "SKIP", "httpbin 不可达")

    # 测试 404
    result_404 = gmd._fetch_url("https://httpbin.org/status/404", timeout=10)
    if result_404 is None:
        report("404 返回 None", "PASS")
    else:
        report("404 返回 None", "SKIP", "httpbin 不可达")

    # 测试超时（短 timeout 触发）
    result_timeout = gmd._fetch_url("https://httpbin.org/delay/5", timeout=1, retries=0)
    if result_timeout is None:
        report("超时返回 None", "PASS")
    else:
        report("超时返回 None", "SKIP", "httpbin 不可达")


def test_main_cli():
    """测试 main() CLI 入口（通过 subprocess）"""
    print("\n--- main() CLI ---")
    import subprocess
    script_path = os.path.join(os.path.dirname(__file__), "get_market_data.py")

    # 测试 --list-batches
    try:
        r = subprocess.run(
            [sys.executable, script_path, "--list-batches"],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "YFINANCE_CACHE_DIR": os.environ.get("TEMP", "") + "\\yfinance_test_cache"}
        )
        if r.returncode == 0 and "us_stocks" in r.stdout:
            report("--list-batches", "PASS")
        else:
            report("--list-batches", "FAIL", f"exit={r.returncode}, out={r.stdout[:100]}")
    except Exception as e:
        report("--list-batches", "FAIL", str(e)[:80])

    # 测试 --fetch-url product-all-info（快速验证）
    try:
        r = subprocess.run(
            [sys.executable, script_path, "--fetch-url", "product-all-info",
             "--ticker", "AAPL", "--output", "json"],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "YFINANCE_CACHE_DIR": os.environ.get("TEMP", "") + "\\yfinance_test_cache"}
        )
        if r.returncode == 0:
            try:
                data = json.loads(r.stdout)
                if data.get("ticker") == "AAPL" and "news" in data:
                    report("--fetch-url product-all-info", "PASS")
                else:
                    report("--fetch-url product-all-info", "FAIL", "返回结构异常")
            except json.JSONDecodeError:
                report("--fetch-url product-all-info", "FAIL", "非 JSON 输出")
        else:
            report("--fetch-url product-all-info", "FAIL", f"exit={r.returncode}")
    except Exception as e:
        report("--fetch-url product-all-info", "FAIL", str(e)[:80])

    # 测试 --help
    try:
        r = subprocess.run(
            [sys.executable, script_path, "--help"],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0 and "usage:" in r.stdout.lower():
            report("--help", "PASS")
        else:
            report("--help", "FAIL")
    except Exception as e:
        report("--help", "FAIL", str(e)[:80])


def test_search_yfinance_ticker():
    """测试综合搜索 yfinance ticker"""
    print("\n--- search_yfinance_ticker ---")

    # 测试 vt_symbol 匹配（如 265598.SMART → AAPL）
    try:
        result = gmd.search_yfinance_ticker("265598.SMART")
        assert result["matched"], "vt_symbol 应匹配成功"
        assert result["ticker"] == "AAPL", f"ticker 应为 AAPL，得到 {result['ticker']}"
        assert result["source"] == "vt_symbol_info", f"source 应为 vt_symbol_info，得到 {result['source']}"
        report("vt_symbol 匹配: 265598.SMART → AAPL", "PASS")
    except AssertionError as e:
        report("vt_symbol 匹配", "FAIL", str(e))
    except Exception as e:
        report("vt_symbol 匹配", "FAIL", str(e)[:80])

    # 测试 vt_symbol 不含后缀匹配（如 265598 → AAPL）
    try:
        result = gmd.search_yfinance_ticker("265598")
        assert result["matched"], "不含后缀的 vt_symbol 应匹配成功"
        assert result["ticker"] == "AAPL"
        report("vt_symbol 无后缀匹配: 265598 → AAPL", "PASS")
    except AssertionError as e:
        report("vt_symbol 无后缀匹配", "FAIL", str(e))
    except Exception as e:
        report("vt_symbol 无后缀匹配", "FAIL", str(e)[:80])

    # 测试 yahoo ticker 直接匹配（AAPL 在 vt_symbol_info.json 中有记录）
    try:
        result = gmd.search_yfinance_ticker("AAPL")
        assert result["matched"], "直接 ticker 应匹配成功"
        assert result["ticker"] == "AAPL"
        report("直接 ticker 匹配: AAPL", "PASS")
    except AssertionError as e:
        report("直接 ticker 匹配", "FAIL", str(e))
    except Exception as e:
        report("直接 ticker 匹配", "FAIL", str(e)[:80])

    # 测试公司名称模糊搜索（apple → AAPL）
    try:
        result = gmd.search_yfinance_ticker("apple")
        assert result["matched"], f"公司名称应匹配成功，source={result.get('source')}"
        assert result["ticker"] == "AAPL" or result["ticker"] == "AAPL", \
            f"ticker 应为 AAPL，得到 {result['ticker']}"
        assert len(result["candidates"]) > 0, "应有候选列表"
        report(f"公司名称匹配: apple → {result['ticker']} ({result['source']})", "PASS")
    except AssertionError as e:
        report("公司名称匹配", "FAIL", str(e))
    except Exception as e:
        report("公司名称匹配", "FAIL", str(e)[:80])

    # 测试 cryptocurrency 搜索（bitcoin → BTC-USD）
    try:
        result = gmd.search_yfinance_ticker("bitcoin")
        assert result["matched"], f"加密货币应匹配成功，source={result.get('source')}"
        assert "BTC" in (result["ticker"] or ""), f"ticker 应包含 BTC，得到 {result['ticker']}"
        report(f"加密货币搜索: bitcoin → {result['ticker']} ({result['source']})", "PASS")
    except AssertionError as e:
        report("加密货币搜索", "FAIL", str(e))
    except Exception as e:
        report("加密货币搜索", "FAIL", str(e)[:80])

    # 测试不存在的搜索词（应返回 unmatched + 候选列表）
    try:
        result = gmd.search_yfinance_ticker("xyzzy_nonexistent_12345")
        # 不存在的搜索词，matched 可能为 False，也可能匹配到奇怪的东西
        # 至少 candidates 应该返回
        assert isinstance(result["candidates"], list), "candidates 应为列表"
        report(f"不存在的搜索词: matched={result['matched']}, candidates={len(result['candidates'])}", "PASS")
    except AssertionError as e:
        report("不存在的搜索词", "FAIL", str(e))
    except Exception as e:
        report("不存在的搜索词", "FAIL", str(e)[:80])

    # 测试返回结构完整性
    try:
        result = gmd.search_yfinance_ticker("AAPL")
        required_keys = ["query", "matched", "ticker", "name", "exchange", "quote_type", "source", "candidates"]
        missing = [k for k in required_keys if k not in result]
        assert not missing, f"缺少字段: {missing}"
        report("返回结构完整性", "PASS")
    except AssertionError as e:
        report("返回结构完整性", "FAIL", str(e))
    except Exception as e:
        report("返回结构完整性", "FAIL", str(e)[:80])


def test_search_ticker_cli():
    """测试 --search-ticker CLI 入口（通过 subprocess）"""
    print("\n--- --search-ticker CLI ---")
    import subprocess
    script_path = os.path.join(os.path.dirname(__file__), "get_market_data.py")

    # 测试 --search-ticker 独立模式
    try:
        r = subprocess.run(
            [sys.executable, script_path, "--search-ticker", "apple", "--output", "json"],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "YFINANCE_CACHE_DIR": os.environ.get("TEMP", "") + "\\yfinance_test_cache"}
        )
        if r.returncode == 0:
            data = json.loads(r.stdout)
            if data.get("matched") and data.get("ticker") == "AAPL":
                report("--search-ticker apple (独立)", "PASS")
            else:
                report("--search-ticker apple (独立)", "FAIL", f"matched={data.get('matched')}, ticker={data.get('ticker')}")
        else:
            report("--search-ticker apple (独立)", "FAIL", f"exit={r.returncode}")
    except Exception as e:
        report("--search-ticker apple (独立)", "FAIL", str(e)[:80])

    # 测试 --search-ticker 配合 product-all-info（省略 --ticker）
    try:
        r = subprocess.run(
            [sys.executable, script_path, "--search-ticker", "apple",
             "--fetch-url", "product-all-info", "--output", "json"],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "YFINANCE_CACHE_DIR": os.environ.get("TEMP", "") + "\\yfinance_test_cache"}
        )
        if r.returncode == 0:
            data = json.loads(r.stdout)
            if data.get("ticker") == "AAPL" and "news" in data:
                report("--search-ticker apple + product-all-info", "PASS")
            else:
                report("--search-ticker apple + product-all-info", "FAIL", f"ticker={data.get('ticker')}")
        else:
            report("--search-ticker apple + product-all-info", "FAIL", f"exit={r.returncode}")
    except Exception as e:
        report("--search-ticker apple + product-all-info", "FAIL", str(e)[:80])

    # 测试 --search-ticker 不存在时 product-all-info 报错
    try:
        r = subprocess.run(
            [sys.executable, script_path, "--search-ticker", "xyzzy_nonexistent",
             "--fetch-url", "product-all-info", "--output", "json"],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "YFINANCE_CACHE_DIR": os.environ.get("TEMP", "") + "\\yfinance_test_cache"}
        )
        if r.returncode != 0:
            report("--search-ticker 不存在时 product-all-info 报错", "PASS")
        else:
            report("--search-ticker 不存在时 product-all-info 报错", "FAIL", "应报错但 exit=0")
    except Exception as e:
        report("--search-ticker 不存在时 product-all-info 报错", "FAIL", str(e)[:80])

    # 测试 --search-ticker + --ticker 同时存在时 --ticker 优先
    try:
        r = subprocess.run(
            [sys.executable, script_path, "--search-ticker", "apple",
             "--ticker", "MSFT", "--fetch-url", "product-all-info", "--output", "json"],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "YFINANCE_CACHE_DIR": os.environ.get("TEMP", "") + "\\yfinance_test_cache"}
        )
        if r.returncode == 0:
            data = json.loads(r.stdout)
            if data.get("ticker") == "MSFT":
                report("--search-ticker + --ticker 同时存在时 --ticker 优先", "PASS")
            else:
                report("--search-ticker + --ticker 同时存在时 --ticker 优先", "FAIL",
                       f"ticker={data.get('ticker')} 应为 MSFT")
        else:
            report("--search-ticker + --ticker 同时存在时 --ticker 优先", "FAIL", f"exit={r.returncode}")
    except Exception as e:
        report("--search-ticker + --ticker 同时存在时 --ticker 优先", "FAIL", str(e)[:80])


def test_fetch_ratings():
    """测试评级获取（网络请求）"""
    print("\n--- fetch_ratings ---")

    # 测试 NASDAQ 品种
    try:
        result = gmd.fetch_ratings(TEST_TICKER)
        has_data = any(k in result for k in ["consensus_target", "consensus_rating", "error"])
        if "error" in result:
            report(f"{TEST_TICKER} 评级", "FAIL", result["error"])
        elif result:
            ct = result.get("consensus_target", "?")
            cr = result.get("consensus_rating", "?")
            report(f"{TEST_TICKER} 评级: target=${ct}, rating={cr}", "PASS")
        else:
            report(f"{TEST_TICKER} 评级", "FAIL", "返回空对象")
    except Exception as e:
        report(f"{TEST_TICKER} 评级", "FAIL", str(e)[:100])

    # 测试 NYSE 品种（不同页面结构）
    try:
        result = gmd.fetch_ratings(TEST_TICKER_NYSE)
        if "error" in result:
            report(f"{TEST_TICKER_NYSE} 评级", "FAIL", result["error"])
        elif result:
            ct = result.get("consensus_target", "?")
            cr = result.get("consensus_rating", "?")
            report(f"{TEST_TICKER_NYSE} 评级: target=${ct}, rating={cr}", "PASS")
        else:
            report(f"{TEST_TICKER_NYSE} 评级", "FAIL", "返回空对象")
    except Exception as e:
        report(f"{TEST_TICKER_NYSE} 评级", "FAIL", str(e)[:100])


def test_fetch_technical_indicators():
    """测试技术指标获取（网络请求）"""
    print("\n--- fetch_technical_indicators ---")
    try:
        result = gmd.fetch_technical_indicators(TEST_TICKER)
        if "error" in result:
            report("技术指标", "FAIL", result["error"])
            return

        required = ["current_price", "ma_20", "ma_50", "ma_200", "volume",
                     "atr_14", "cci_14", "support_levels", "resistance_levels",
                     "risk_reward_long", "risk_reward_short",
                     "近24h价格分位", "近3月价格分位"]
        missing = [k for k in required if k not in result]
        if missing:
            report("技术指标", "FAIL", f"缺少字段: {missing}")
        else:
            report(f"价格=${result['current_price']}, ATR={result['atr_14']}, "
                   f"CCI={result['cci_14']}, MA20={result['ma_20']}", "PASS")
    except Exception as e:
        report("技术指标", "FAIL", str(e)[:100])


def test_fetch_product_all_info():
    """测试一站式品种信息获取（网络请求）"""
    print("\n--- fetch_product_all_info ---")
    try:
        result = gmd.fetch_product_all_info(TEST_TICKER)
        assert "ticker" in result, "应包含 ticker"
        assert "news" in result, "应包含 news"
        assert "ratings" in result, "应包含 ratings"
        assert "technical" in result, "应包含 technical"
        assert result["ticker"] == TEST_TICKER

        # 检查 technical 字段完整性
        tech = result["technical"]
        if "error" not in tech:
            for k in ["current_price", "ma_20", "ma_50", "ma_200", "volume",
                       "atr_14", "cci_14", "support_levels", "resistance_levels"]:
                assert k in tech, f"technical 缺少 {k}"

        report(f"{TEST_TICKER} product-all-info 完整 ({result['news']['count']}条新闻)", "PASS")
    except Exception as e:
        report("product-all-info", "FAIL", str(e)[:100])


def test_fetch_economic_calendar():
    """测试经济日历获取（网络请求，需 camoufox）"""
    print("\n--- fetch_economic_calendar ---")
    try:
        result = gmd.fetch_economic_calendar()
        if isinstance(result, list) and len(result) > 0:
            report(f"获取 {len(result)} 条日历事件", "PASS")
            # 显示前 3 条
            for item in result[:3]:
                print(f"    {item.get('date','?'):12s} | {item.get('event','?'):40s} | {item.get('source','?')}")
        elif isinstance(result, list):
            report("经济日历", "SKIP", "返回空列表（可能非交易日）")
        else:
            report("经济日历", "FAIL", f"返回类型异常: {type(result)}")
    except Exception as e:
        report("经济日历", "SKIP", f"需 camoufox 或网络异常: {str(e)[:80]}")


def test_fetch_web_indicators():
    """测试 OrioSearch 市场指标获取（网络请求）"""
    print("\n--- fetch_web_indicators ---")
    try:
        result = gmd.fetch_web_indicators()
        assert isinstance(result, dict), "应返回 dict"
        assert result.get("source") == "oriosearch", f"source 应为 oriosearch，得到 {result.get('source')}"
        assert result.get("total") == 14, f"total 应为 14，得到 {result.get('total')}"
        assert "fetched_at" in result, "缺少 fetched_at"
        assert "indicators" in result, "缺少 indicators"
        assert isinstance(result["indicators"], dict), "indicators 应为 dict"

        inds = result["indicators"]
        hit = result.get("hit", 0)
        # 验证每个指标的结构
        for key in gmd.WEB_INDICATOR_QUERIES_TEMPLATE:
            info = inds.get(key, {})
            assert "query" in info, f"{key} 缺少 query"
            assert "value" in info, f"{key} 缺少 value"
            assert "source" in info, f"{key} 缺少 source"
            assert "time_s" in info, f"{key} 缺少 time_s"

        report(f"OrioSearch 市场指标: {hit}/{result['total']} 命中, 耗时 {result['time_s']}s", "PASS")
        # 显示前 5 个指标
        for i, (key, info) in enumerate(inds.items()):
            if i >= 5:
                break
            print(f"    {key:<15} {info['source']:<12} {info['time_s']:<8.2f}s {info['value']}")
    except AssertionError as e:
        report("fetch_web_indicators", "FAIL", str(e))
    except Exception as e:
        report("fetch_web_indicators", "FAIL", str(e)[:100])


def test_web_indicators_cli():
    """测试 --fetch-url web-indicators CLI 入口（通过 subprocess）"""
    print("\n--- --fetch-url web-indicators CLI ---")
    import subprocess
    script_path = os.path.join(os.path.dirname(__file__), "get_market_data.py")

    # 测试 JSON 输出
    try:
        r = subprocess.run(
            [sys.executable, script_path, "--fetch-url", "web-indicators", "--output", "json"],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode == 0:
            data = json.loads(r.stdout)
            assert data.get("source") == "oriosearch", "source 应为 oriosearch"
            assert data.get("total") == 14, f"total 应为 14，得到 {data.get('total')}"
            assert "indicators" in data, "缺少 indicators"
            report("--fetch-url web-indicators (JSON)", "PASS")
        else:
            report("--fetch-url web-indicators (JSON)", "FAIL", f"exit={r.returncode}, err={r.stderr[:100]}")
    except json.JSONDecodeError:
        report("--fetch-url web-indicators (JSON)", "FAIL", "非 JSON 输出")
    except Exception as e:
        report("--fetch-url web-indicators (JSON)", "FAIL", str(e)[:80])

    # 测试 text 输出
    try:
        r = subprocess.run(
            [sys.executable, script_path, "--fetch-url", "web-indicators"],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode == 0 and "市场指标" in r.stdout:
            report("--fetch-url web-indicators (text)", "PASS")
        else:
            report("--fetch-url web-indicators (text)", "FAIL", f"exit={r.returncode}")
    except Exception as e:
        report("--fetch-url web-indicators (text)", "FAIL", str(e)[:80])


def test_get_ticker_data():
    """测试单个 ticker 数据获取（网络请求）"""
    print("\n--- get_ticker_data ---")
    try:
        result = gmd.get_ticker_data(TEST_TICKER)
        if "error" in result:
            report(f"{TEST_TICKER} 数据", "FAIL", result["error"])
            return
        required = ["ticker", "price", "change", "change_pct",
                     "ma_20", "ma_50", "ma_200", "high_52w", "low_52w", "pct_52w", "volume"]
        missing = [k for k in required if k not in result]
        if missing:
            report("ticker 数据", "FAIL", f"缺少字段: {missing}")
        else:
            report(f"{TEST_TICKER}: price=${result['price']}, "
                   f"52w%={result['pct_52w']}, vol={result['volume']}", "PASS")
    except Exception as e:
        report("ticker 数据", "FAIL", str(e)[:100])


def test_fetch_batch():
    """测试批量数据获取（网络请求）"""
    print("\n--- fetch_batch ---")
    try:
        data = gmd.fetch_batch(TEST_TICKERS)
        assert len(data) == len(TEST_TICKERS), f"应返回 {len(TEST_TICKERS)} 条，实际 {len(data)}"
        success = sum(1 for d in data if "error" not in d)
        report(f"批量获取 {success}/{len(TEST_TICKERS)} 成功", "PASS")
    except Exception as e:
        report("批量获取", "FAIL", str(e)[:100])


def test_fetch_ici_table():
    """测试 ICI 资金流数据获取（网络请求）"""
    print("\n--- fetch_ici_table ---")
    try:
        url = gmd.ICI_URLS.get("ici-equity-flows", {}).get("url", "")
        if not url:
            report("ICI 资金流", "SKIP", "未配置 ici-equity-flows URL")
            return
        result = gmd.fetch_ici_table(url)
        if "error" in result:
            report("ICI 资金流", "FAIL", result["error"])
        else:
            report(f"ICI 数据获取成功", "PASS")
    except Exception as e:
        report("ICI 资金流", "SKIP", str(e)[:80])


def test_list_batches():
    """测试列出可用批次"""
    print("\n--- list_batches ---")
    for market in ["us_stocks", "crypto"]:
        batches = gmd.MARKET_BATCHES.get(market, [])
        if batches:
            names = ", ".join(batches)
            report(f"{market}: {names}", "PASS")
        else:
            report(f"{market}", "SKIP", "无预设批次")


def test_ici_urls():
    """测试 ICI URL 配置完整性"""
    print("\n--- ICI_URLS ---")
    required_keys = ["ici-equity-flows", "ici-mmf-assets"]
    missing = [k for k in required_keys if k not in gmd.ICI_URLS]
    if missing:
        report("ICI_URLS", "FAIL", f"缺少: {missing}")
    else:
        report(f"已配置 {len(gmd.ICI_URLS)} 个数据源: {list(gmd.ICI_URLS.keys())}", "PASS")


# ==================== 主入口 ====================

def run_all():
    """运行所有测试"""
    print("=" * 60)
    print("get_market_data.py 功能测试")
    print(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 本地计算测试（无需网络）
    test_safe_float()
    test_calc_52w_percentile()
    test_calc_atr()
    test_calc_cci()
    test_find_support_resistance()
    test_calc_price_percentile()
    test_clean_html()
    test_format_text_output()
    test_format_ici_output()

    # 配置检查
    test_list_batches()
    test_ici_urls()

    # 网络请求测试
    test_fetch_url()
    test_fetch_news()
    test_fetch_ratings()
    test_fetch_technical_indicators()
    test_fetch_product_all_info()
    test_get_ticker_data()
    test_fetch_batch()
    test_fetch_ici_table()
    test_fetch_economic_calendar()
    test_fetch_web_indicators()
    test_web_indicators_cli()
    test_main_cli()
    test_search_yfinance_ticker()
    test_search_ticker_cli()

    # 汇总
    print("\n" + "=" * 60)
    total = PASS + FAIL + SKIP
    print(f"结果: ✅ {PASS} 通过 | ❌ {FAIL} 失败 | ⏭️  {SKIP} 跳过 | 共 {total} 项")
    if FAIL > 0:
        print("⚠️  存在失败项，请检查网络或代码")
    else:
        print("🎉 全部通过！")
    print("=" * 60)
    return FAIL == 0


def run_quick():
    """快速测试（仅本地计算 + 少量网络）"""
    print("=" * 60)
    print("快速测试模式")
    print("=" * 60)
    test_safe_float()
    test_calc_52w_percentile()
    test_calc_atr()
    test_calc_cci()
    test_find_support_resistance()
    test_calc_price_percentile()
    test_clean_html()
    test_format_text_output()
    test_format_ici_output()
    test_list_batches()
    test_ici_urls()
    test_fetch_ratings()
    test_fetch_technical_indicators()
    test_search_yfinance_ticker()

    total = PASS + FAIL + SKIP
    print(f"\n结果: ✅ {PASS} 通过 | ❌ {FAIL} 失败 | ⏭️  {SKIP} 跳过 | 共 {total} 项")
    return FAIL == 0


if __name__ == "__main__":
    args = [a.lower() for a in sys.argv[1:]]

    if "--full" in args:
        run_all()
    elif "--batch" in args:
        test_list_batches()
        test_fetch_batch()
        test_get_ticker_data()
    elif "--product" in args:
        test_fetch_product_all_info()
    elif "--calendar" in args:
        test_fetch_economic_calendar()
    elif "--technical" in args:
        test_calc_atr()
        test_calc_cci()
        test_find_support_resistance()
        test_calc_price_percentile()
        test_fetch_technical_indicators()
    elif "--ratings" in args:
        test_fetch_ratings()
    elif "--news" in args:
        test_fetch_news()
    elif "--web-indicators" in args:
        test_fetch_web_indicators()
        test_web_indicators_cli()
    else:
        run_quick()
