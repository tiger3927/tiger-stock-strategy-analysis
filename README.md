

# 本技能的补充说明

## 定时任务

为建立分析用的数据缓存，需要建立定时任务，不然分析模块就会缺乏数据。

### 比如 美股
在openclaw建立定时分析任务，6个小时一次，以下任务提示经过验证，具备较高执行稳定性

``` bash
# 任务：【美股 — 大盘与板块和资金流向分析 — 全量刷新】
# 1. 使用 tiger-stock-strategy-analysis 技能的"大盘与板块和资金流向分析模块"的能力！
# 2. 分析周期：短线周期（未来1-10个交易日）
# 3. 执行 Step 0.1 全部三次数据获取：
#    - python scripts/get_market_data.py --market us_stocks --batch us-all --output json
#    - python scripts/get_market_data.py --fetch-url all --output json
#    - python scripts/get_market_data.py --fetch-url calendar --output json   ← 必须执行，不得跳过！
# 4. 强制不使用任何缓存，本次全量重分析
# 5. 输出必须严格按 美股市场.md 中【六、输出模板】的 JSON 格式，字段名必须完全一致（中文 key）
# 6. 完成分析后使用 vnpy_command publish 存入 Redis：
# python scripts/vnpy_command.py --token TOKEN publish 用户名 /vnpy:美股:大盘与板块和资金流向分析 '{json结果}' --expire 43200
# 不允许自定义或修改 key 路径。
# 7. 最终将分析 JSON 投递给用户。
```

### 比如 加密货币

在openclaw建立定时分析任务，6个小时一次，以下任务提示经过验证，具备较高执行稳定性

``` bash
# 任务：【加密货币 — 大盘与板块和资金流向分析 — 全量刷新】
# 1. 使用 tiger-stock-strategy-analysis 技能的"大盘与板块和资金流向分析模块"的能力！
# 2. 分析周期：短线周期（未来1-10个交易日）
# 3. 执行 Step 0.1 全部三次数据获取：
#    - python scripts/get_market_data.py --market crypto --batch crypto-all --output json
#    - python scripts/get_market_data.py --fetch-url all --output json
#    - python scripts/get_market_data.py --fetch-url calendar --output json   ← 必须执行，不得跳过！
# 4. 强制不使用任何缓存，本次全量重分析
# 5. 输出必须严格按 加密货币市场.md 中【六、输出模板】的 JSON 格式，字段名必须完全一致（中文 key）
# 6. 完成分析后使用 vnpy_command publish 存入 Redis：
# python scripts/vnpy_command.py --token TOKEN publish 用户名 /vnpy:加密货币:大盘与板块和资金流向分析 '{json结果}' --expire 43200
# 不允许自定义或修改 key 路径。
# 7. 最终将分析 JSON 投递给用户。
```
