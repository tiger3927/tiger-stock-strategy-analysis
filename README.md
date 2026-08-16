# tiger-stock-strategy-analysis 技能说明

本技能为 **vnpy 量化系统**提供策略分析、趋势预测与下单前审核。它是一套 AI 智能体作业指导书（`docs/` 下的各模块文档）+ 数据/工具脚本（`scripts/`）的集合，供 openclaw、workbuddy / codex 等智能体加载使用。

> **技能与 vnpy_mcp 的关系**：`vnpy_mcp` 是当前环境中独立部署的 MCP 工具集（MCP server），**不从属于本技能**。本技能只是调用它查询量化系统实时数据、发送操作指令。详见 [docs/vnpy_mcp.md](docs/vnpy_mcp.md)。

---

## 一、技能概述

为 vnpy 量化系统提供以下能力：

| 能力 | 说明 | 入口文档 |
|------|------|---------|
| **大盘与板块和资金流向分析** | 对 7 个市场（美股/加密货币/中国期货/中国A股/港股/台股/日股）进行大盘走势、板块轮动、资金流向综合判断 | [docs/大盘与板块和资金流向分析/00_index.md](docs/大盘与板块和资金流向分析/00_index.md) |
| **美股选股** | 做多/做空选股，输出评分卡和入场计划 | [docs/美股选股/00_index.md](docs/美股选股/00_index.md) |
| **vnpy 整体持仓分析与风控** | 账户风险评估、各策略持仓盈亏分析、必要时发送控制命令 | [docs/vnpy整体持仓分析与风控/00_index.md](docs/vnpy整体持仓分析与风控/00_index.md) |
| **策略分析** | 对支持的量化策略类型做分析与下单前审核 | 见下方「策略分析文档」 |
| **vnpy_mcp 调用** | 直连量化系统查询/操作 | [docs/vnpy_mcp.md](docs/vnpy_mcp.md) |

---

## 二、目录结构

```
tiger-stock-strategy-analysis/
├── SKILL.md                  # 技能主入口（路由、优先级、规则）
├── README.md                 # 本说明文件
├── docs/                     # 各模块作业指导书
│   ├── 大盘与板块和资金流向分析/   # 大盘分析（美股/加密货币…）
│   ├── 美股选股/               # 做多/做空选股
│   ├── vnpy整体持仓分析与风控/   # 持仓分析与风控
│   ├── Martingale-Grid-Trading-Strategy/  # 马丁格尔网格策略
│   ├── Tiger-Grid-Template/   # CTA 策略基类模板
│   ├── 调仓/                   # 美股板块轮动调仓、选股池管理
│   ├── Multi_Signal_Treand_Strategy.md
│   └── vnpy_mcp.md            # vnpy_mcp 工具集使用指南
├── scripts/                  # 数据获取与工具脚本
│   ├── get_market_data.py     # 统一数据入口脚本
│   ├── test_get_market_data.py
│   ├── sync_stock_pool.py     # 选股池数据校验
│   ├── stock_pool.json        # 美股选股池
│   └── vt_symbol_info.json    # 盈透 conid 对照表
└── 复盘记录/                   # 复盘分析结果（按日期归档）
```

---

## 三、分析模块文档索引

智能体或使用者遇到对应需求时，加载对应入口文档执行。

### 3.1 大盘与板块和资金流向分析

触发词：大盘、板块、资金流向、市场走势等。

| 市场 | 分析文档 | 报告名称（cta_report_get 参数） |
|------|---------|------------------------------|
| 美股 | [美股市场.md](docs/大盘与板块和资金流向分析/美股市场.md) | `美股大盘与板块和资金流向分析` |
| 加密货币 | [加密货币市场.md](docs/大盘与板块和资金流向分析/加密货币市场.md) | `加密货币大盘与板块和资金流向分析` |
| 中国期货 / A股 / 港股 / 台股 / 日股 | 待补充 | 待确认 |

> 分析结果由量化系统自动保存为分析报告，通过 `cta_report_list` / `cta_report_get` 读取。

### 3.2 美股选股模块

触发词：选股、做多候选、做空候选、评分卡等。

- 做多：参照 [美股做多选择.md](docs/美股选股/美股做多选择.md)，筛选「价值+成长混合（GARP）」标的
- 做空：参照 [美股做空选择.md](docs/美股选股/美股做空选择.md)，筛选估值泡沫/基本面恶化标的
- 执行流程：[00_index.md](docs/美股选股/00_index.md)
- 报告：`cta_report_get(report_kind="美股做多选股结果")` / `报告="美股做空选股结果"`

### 3.3 vnpy 整体持仓分析与风控

触发词：持仓分析、账户风控、整体仓位等。

- 入口：[00_index.md](docs/vnpy整体持仓分析与风控/00_index.md)
- 覆盖：账户风险评估、各策略持仓盈亏、结合大盘判断机会/风险、必要时发控制命令

### 3.4 策略分析文档

| 策略类型 | 核心逻辑 | 入口文档 |
|---------|---------|---------|
| 智能马丁格尔网格策略 (openclaw-martin) | 支撑压力结构的马丁格尔资金管理，分批加仓摊低成本/成本 | [docs/Martingale-Grid-Trading-Strategy/00-Router.md](docs/Martingale-Grid-Trading-Strategy/00-Router.md) |
| 多信号权重评分趋势策略 (Multi_Signal_Treand) | 多信号权重评分，高分开仓，止盈/止损离场 | [docs/Multi_Signal_Treand_Strategy.md](docs/Multi_Signal_Treand_Strategy.md) |
| CTA 趋势策略基类 (tiger_grid_template) | 策略基类原理（一般无需深入） | [docs/Tiger-Grid-Template/00_index.md](docs/Tiger-Grid-Template/00_index.md) |

### 3.5 调仓

| 文档 | 用途 |
|------|------|
| [美股板块轮动调仓.md](docs/调仓/美股板块轮动调仓.md) | 机构级板块轮动调仓方法论（先卖弱再买强、分批执行、资金闭环） |
| [美股选股池管理.md](docs/调仓/美股选股池管理.md) | 选股池的构建、维护和使用规则 |

---

## 四、vnpy_mcp 使用说明

**vnpy_mcp 是当前环境中独立部署的 MCP 工具集（MCP server），不从属于任何技能。** 本技能只是调用它。

- 查询类工具（账户/持仓/策略状态/分析报告/合约/行情）：**实时直连量化系统，无需用户名参数**
- 操作类工具（调仓/平仓/设参/通知/启停/新增删除策略）：需量化系统正在运行
- 使用前可调用 `get_guide` 获取工具集完整指南（它是 vnpy_mcp 工具集下的一个工具）

详细工具清单、参数与返回格式见 **[docs/vnpy_mcp.md](docs/vnpy_mcp.md)**。

---

## 五、数据获取工具脚本

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

**缓存机制：**
- **历史行情缓存**（`.yf_history_cache/`）：价格/均线/52周高低等 K 线数据，TTL **30 分钟**，多策略重复拉取同一批次时秒回
- **info 元数据缓存**（`.yf_info_cache/`）：公司名/PE/市值/行业等基本面数据，TTL **12 小时**
- 缓存 key 按 `ticker + period` 区分，原子写入，并发安全

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

### sync_stock_pool.py

校验美股选股池数据（`stock_pool.json`），验证 JSON 结构合理性并输出统计。

**用法：**
```bash
python scripts/sync_stock_pool.py
```

> 数据源：`stock_pool.json` — AI 直接修改此文件即可更新选股池，无需改 Python 代码。

---

## 六、vnpy 系统附带的 openclaw 智能体必须的定时任务

为建立分析用的数据缓存，需要建立定时任务，不然分析模块就会缺乏数据。

### 例如 美股
在 vnpy 系统中建立定时分析任务，每日开盘前一次，以下任务提示经过验证，具备较高执行稳定性。

```text

任务：【美股 — 大盘与板块和资金流向分析 — 全量刷新】

1. 使用 tiger-stock-strategy-analysis 技能的"大盘与板块和资金流向分析模块"的能力！
2. 分析周期：短线周期（未来1-10个交易日）
3. 执行 Step 0.1 全部三次数据获取：
   - python scripts/get_market_data.py --market us_stocks --batch us-all --output json  ← 必须执行，不得跳过！
   - python scripts/get_market_data.py --fetch-url all --output json  ← 必须执行，不得跳过！
   - python scripts/get_market_data.py --fetch-url calendar --output json   ← 必须执行，不得跳过！
   - python scripts/get_market_data.py --fetch-url web-indicators --output json  ← 必须执行，不得跳过，搜索时间比较长，可能会超过3分钟！
4. 强制不使用任何缓存，本次全量重分析
5. 必须严格按 美股市场.md 中【六、输出模板】的 JSON 格式输出，字段名必须完全一致（中文 key）
6. 回答中输出JSON，不要保存到文件。

```

### 例如 加密货币

在 openclaw 建立定时分析任务，6 个小时一次，以下任务提示经过验证，具备较高执行稳定性。

```text

任务：【加密货币 — 大盘与板块和资金流向分析 — 全量刷新】

1. 使用 tiger-stock-strategy-analysis 技能的"大盘与板块和资金流向分析模块"的能力！
2. 分析周期：短线周期（未来1-10个交易日）
3. 执行 Step 0.1 全部三次数据获取：
   - python scripts/get_market_data.py --market crypto --batch crypto-all --output json
   - python scripts/get_market_data.py --fetch-url all --output json
   - python scripts/get_market_data.py --fetch-url calendar --output json   ← 必须执行，不得跳过！
4. 强制不使用任何缓存，本次全量重分析
5. 输出必须严格按 加密货币市场.md 中【六、输出模板】的 JSON 格式，字段名必须完全一致（中文 key）
6. 回答中输出JSON，不要保存到文件。

```

---

## 七、关于复盘

### 复盘方式

可借助 workbuddy / codex 等智能体，以本技能目录为工作目录，通过 **vnpy_mcp** 工具连接实盘运行的 vnpy 系统（vnpy 自带 openclaw，可使用本技能全自动分析和交易），定期获取复盘结果，根据实际操作的好与坏，持续改进本技能。

### 复盘记录存放

复盘工作一般情况下**不直接编辑技能文件**。复盘分析结果统一存入本技能目录下的 `复盘记录` 子文件夹，按日期创建子文件夹（格式：`YYYY-MM-DD`，如 `2026-08-04`），复盘结果存放在对应日期的文件夹中。

### 复盘结果用途

复盘结果用于改进以下三方面能力：

1. **分析能力**：大盘与板块分析、策略分析的准确性与稳定性；
2. **参数设置能力**：空仓等待 → 开仓 → 持仓 → 加减仓 → 平仓各阶段中，具体策略参数设置的准确性；
3. **干预能力**：利用 vnpy 可设置的策略参数，以及 vnpy_mcp 的参数设置与下单交易能力，干预策略运行态势的能力。

具体见 [复盘记录/ReadME.md](复盘记录/ReadME.md)

---

## 八、技能迭代

> **注意：技能迭代会修改 `docs/` 下的技能文档本身，必须经用户明确要求后才可执行，AI 不得自动发起。**

复盘完成后，如果需要根据复盘结论修订技能文档，应按照 [技能迭代/ReadME.md](技能迭代/ReadME.md) 中定义的标准流程执行：备份原文档 -> 修改 -> 记录变更日志。详见该文档。

---

## 九、安装

- 地址：<https://github.com/tiger3927/skill-tiger-stock-strategy-analysis.git>
- git 克隆到工作区目录下的 `skills` 目录，克隆的目录改名，本技能目录必须为：`tiger-stock-strategy-analysis`
- 该目录下应有 `SKILL.md`、`docs` 目录、`scripts` 目录

### scripts 目录依赖

| 模块 | 安装命令 | 用途 |
|------|---------|------|
| `get_market_data.py` | `pip install yfinance` | 获取结构化价格数据（含均线、52周百分位） |
| `get_market_data.py` | `pip install requests beautifulsoup4 numpy` | HTTP 请求、HTML 解析、数值计算（ATR/CCI/支撑压力位） |
| `get_market_data.py`（calendar） | `pip install -U camoufox[geoip]` + `camoufox fetch` | 必须绕过 Cloudflare 获取 ForexFactory 经济日历 |
| `test_get_market_data.py` | `pip install pandas` | 测试脚本中构造模拟 DataFrame 数据 |
| `tools.py` | （纯标准库，无需安装） | JSON 文件读取工具函数 |