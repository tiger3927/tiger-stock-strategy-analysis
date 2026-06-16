

# 本技能的补充说明

## 数据获取工具脚本 和 检测脚本

### get_market_data.py

统一数据入口脚本，避免 AI 每次临时写爬虫脚本。

**功能：**
- 按市场类型获取预设数据批次（指数、板块、宏观、龙头股）
- 支持自定义 ticker 列表
- `--fetch-url product-all-info` 一站式获取单品种新闻+评级+技术指标（ATR/CCI/支撑压力位/均线/成交量/风险收益比/价格分位）
- `--fetch-url calendar` 获取全局经济日历（ForexFactory + Fed Calendar）
- `--fetch-url all` 获取 ICI 资金流数据
- `--search-ticker` 综合搜索 yfinance ticker（支持 vt_symbol、公司名、代称、加密货币名等）
- 输出结构化 JSON

**用法示例：**
```bash
# 个股信息（新闻+评级+技术指标）
python scripts/get_market_data.py --fetch-url product-all-info --ticker AAPL --output json

# 大盘行情数据（指数、板块、宏观）
python scripts/get_market_data.py --market us_stocks --batch us-all --output json

# 加密货币行情
python scripts/get_market_data.py --market crypto --batch crypto-all --output json

# 经济日历
python scripts/get_market_data.py --fetch-url calendar --output json

# 搜索 ticker（支持 vt_symbol、公司名、加密货币名等）
python scripts/get_market_data.py --search-ticker "apple" --output json
python scripts/get_market_data.py --search-ticker "265598.SMART"

# --search-ticker 配合 product-all-info（自动填入 --ticker）
python scripts/get_market_data.py --search-ticker "apple" --fetch-url product-all-info --output json

# 列出可用批次
python scripts/get_market_data.py --list-batches
```

### test_get_market_data.py

功能测试脚本，用于快速诊断网络/数据问题。覆盖所有核心功能模块，含本地计算（NaN 边界测试）和网络请求测试。

**用法：**
```bash
python scripts/test_get_market_data.py              # 快速测试（默认）
python scripts/test_get_market_data.py --full        # 全量测试（含网络请求）
python scripts/test_get_market_data.py --batch       # 仅测试 batch 数据获取
python scripts/test_get_market_data.py --product     # 仅测试 product-all-info
python scripts/test_get_market_data.py --calendar    # 仅测试经济日历
python scripts/test_get_market_data.py --technical   # 仅测试技术指标计算
python scripts/test_get_market_data.py --ratings     # 仅测试评级获取
python scripts/test_get_market_data.py --news        # 仅测试新闻获取
```

## 必须的定时任务

为建立分析用的数据缓存，需要建立定时任务，不然分析模块就会缺乏数据。

### 比如 美股
在openclaw建立定时分析任务，6个小时一次，以下任务提示经过验证，具备较高执行稳定性

``` text
任务：【美股 — 大盘与板块和资金流向分析 — 全量刷新】
1. 使用 tiger-stock-strategy-analysis 技能的“大盘与板块和资金流向分析模块”的能力！
2. 分析周期：短线周期（未来1-10个交易日）
3. 执行 Step 0.1 全部三次数据获取：
   - python scripts/get_market_data.py --market us_stocks --batch us-all --output json
   - python scripts/get_market_data.py --fetch-url all --output json
   - python scripts/get_market_data.py --fetch-url calendar --output json   ← 必须执行，不得跳过！
4. 强制不使用任何缓存，本次全量重分析
5. 输出必须严格按 美股市场.md 中【六、输出模板】的 JSON 格式，字段名必须完全一致（中文 key）
6. 完成分析后使用 vnpy_command publish 存入 Redis：
python scripts/vnpy_command.py --token TOKEN publish 用户名 /vnpy:美股:大盘与板块和资金流向分析 '{json结果}' --expire 43200
不允许自定义或修改 key 路径。
7. 最终将分析 JSON 投递给用户。
```

### 比如 加密货币

在openclaw建立定时分析任务，6个小时一次，以下任务提示经过验证，具备较高执行稳定性

``` text
任务：【加密货币 — 大盘与板块和资金流向分析 — 全量刷新】
1. 使用 tiger-stock-strategy-analysis 技能的"大盘与板块和资金流向分析模块"的能力！
2. 分析周期：短线周期（未来1-10个交易日）
3. 执行 Step 0.1 全部三次数据获取：
   - python scripts/get_market_data.py --market crypto --batch crypto-all --output json
   - python scripts/get_market_data.py --fetch-url all --output json
   - python scripts/get_market_data.py --fetch-url calendar --output json   ← 必须执行，不得跳过！
4. 强制不使用任何缓存，本次全量重分析
5. 输出必须严格按 加密货币市场.md 中【六、输出模板】的 JSON 格式，字段名必须完全一致（中文 key）
6. 完成分析后使用 vnpy_command publish 存入 Redis：
python scripts/vnpy_command.py --token TOKEN publish 用户名 /vnpy:加密货币:大盘与板块和资金流向分析 '{json结果}' --expire 43200
不允许自定义或修改 key 路径。
7. 最终将分析 JSON 投递给用户。
```
