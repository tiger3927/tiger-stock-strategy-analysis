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
│   ├── 加密货币选择/            # 做多/做空选币（含选币操作手册）
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
│   ├── sync_crypto_pool.py    # 选币池数据校验
│   ├── stock_pool.json        # 美股选股池
│   ├── crypto_pool.json       # 加密货币选币池
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

### 3.3 加密货币选币模块

触发词：选币、加密做多候选、加密做空候选、评分卡等。

- 做多：参照 [加密货币做多选择.md](docs/加密货币选择/加密货币做多选择.md)，筛选「叙事成长型」标的（代币经济学 + 协议基本面 + 资金面）
- 做空：参照 [加密货币做空选择.md](docs/加密货币选择/加密货币做空选择.md)，筛板块过热/解锁抛压标的，**必过轧空风险与做空成本两道闸**
- 选币池：[选币操作手册.md](docs/加密货币选择/选币操作手册.md)（数据源 `scripts/crypto_pool.json`）
- 执行流程：[00_index.md](docs/加密货币选择/00_index.md)
- 报告：`cta_report_get(report_kind="加密货币做多选股结果")` / `报告="加密货币做空选股结果"`
- ⚠️ 可交易性：候选必须与 `get_all_contracts`（BINANCE_LINEAR）求交集，`vt_symbol` 以系统返回为准

### 3.4 vnpy 整体持仓分析与风控

触发词：持仓分析、账户风控、整体仓位等。

- 入口：[00_index.md](docs/vnpy整体持仓分析与风控/00_index.md)
- 覆盖：账户风险评估、各策略持仓盈亏、结合大盘判断机会/风险、必要时发控制命令

### 3.5 策略分析文档

| 策略类型 | 核心逻辑 | 入口文档 |
|---------|---------|---------|
| 智能马丁格尔网格策略 (openclaw-martin) | 支撑压力结构的马丁格尔资金管理，分批加仓摊低成本/成本 | [docs/Martingale-Grid-Trading-Strategy/00-Router.md](docs/Martingale-Grid-Trading-Strategy/00-Router.md) |
| 多信号权重评分趋势策略 (Multi_Signal_Treand) | 多信号权重评分，高分开仓，止盈/止损离场 | [docs/Multi_Signal_Treand_Strategy.md](docs/Multi_Signal_Treand_Strategy.md) |
| CTA 趋势策略基类 (tiger_grid_template) | 策略基类原理（一般无需深入） | [docs/Tiger-Grid-Template/00_index.md](docs/Tiger-Grid-Template/00_index.md) |

### 3.6 调仓

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

### refill_stock_pool_t3.py

为美股选股池补充 **Tier 3（潜力小盘）**：维基百科 S&P 600 / S&P 400 成分表（含 GICS Sub-Industry）→ 映射到池子的子板块 → `get_market_data.py` 逐个验证（市值 $1B-$5B、价格 ≥ $3、日均成交额 ≥ $5M）→ 每个子板块按流动性降序取前 N 只。

**用法：**
```bash
python scripts/refill_stock_pool_t3.py                    # 预演（dry-run，不写文件）
python scripts/refill_stock_pool_t3.py --apply            # 写回 stock_pool.json 并自动跑校验
python scripts/refill_stock_pool_t3.py --only=15105010    # 只补指定子板块（增量）
python scripts/refill_stock_pool_t3.py --apply --per=5    # 每个子板块补到 5 只（默认 3）
python scripts/refill_stock_pool_t3.py --refresh          # 忽略行情缓存，全量重取
```

> 数据源：维基百科 S&P 600 / S&P 400 成分表 + `get_market_data.py`；只改 `stock_pool.json` 的 `sub_sectors.*.tiers.T3` 与 `meta` 时间戳/快照，不动 T1/T2。
> 依赖：`pandas`（+ `lxml`，`read_html` 用）——已随 `get_market_data.py` 安装。
> 行情缓存：`scripts/temp/refill_t3_quotes.json`（便于反复预演）；候选映射表 `MAP` / 人工补充 `EXTRA` 在脚本内，GICS 调整时需复核。

### sync_crypto_pool.py

校验加密货币选币池数据（`crypto_pool.json`）：结构、分类/tier 与 coins 的一致性、硬排除规则（稳定币/杠杆代币/封装币）、高弹性占比（≥20%），并输出统计。

**用法：**
```bash
python scripts/sync_crypto_pool.py
```

> 数据源：`crypto_pool.json` — AI 直接修改此文件即可更新选币池，无需改 Python 代码。
> 注意：本脚本不联网，**不校验 yahoo 符号是否真的有效**——需先跑 `get_market_data.py --market crypto --tickers ...` 批量校验并置 `yahoo_verified=true`。

---

## 六、vnpy 系统附带的 openclaw 智能体必须的定时任务

为建立分析用的数据缓存，需要建立定时任务，不然分析模块就会缺乏数据。

> **⚠️ 命名铁律（踩过坑）**：在 vnpy 里建任务时，**任务名 = 分析报告名 = `分析任务/<任务名>/` 落盘文件夹名**（配置见 `.vntrader/macro_analyze.json` 的 tasks key）。必须与技能文档中定义的名字**逐字一致**，否则策略侧 `cta_report_get(report_kind="…")` 会永远取不到报告（曾出现任务名写「数字币大盘与板块和资金流向分析」、文档写「加密货币大盘与板块和资金流向分析」，导致加密策略读不到大盘报告）。
>
> - 美股大盘：`美股大盘与板块和资金流向分析`
> - 加密货币大盘：`加密货币大盘与板块和资金流向分析`
> - 美股做多/做空：`美股做多选股结果` / `美股做空选股结果`
> - 加密货币做多/做空：`加密货币做多选股结果` / `加密货币做空选股结果`
>
> 改名时需同步 4 处：`macro_analyze.json`（key + `name`）、`macro_analyze_state.json`（key）、`strategy_session_map.json`（`macro::<任务名>` key）、落盘目录 `Strategy_OpenClaw_Records/分析任务/<任务名>/`。

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
3. 执行 Step 0.1 数据获取（前三条必须执行，不得跳过）：
   - python scripts/get_market_data.py --market crypto --batch crypto-all --output json        ← 必须执行，不得跳过！
   - python scripts/get_market_data.py --market us_stocks --batch us-all --output json         ← 必须执行，不得跳过！（跨市场联动：QQQ / US10Y / DXY / ^VIX / HYG·LQD）
   - python scripts/get_market_data.py --fetch-url calendar --output json                      ← 必须执行，不得跳过！
   - 加密货币专属指标（BTC.D、TOTAL/TOTAL2、稳定币市值、资金费率、OI、交易所净流入、Crypto Fear & Greed、BTC/ETH ETF 净流入、DeFi TVL）按 Step 0.3 三层预算用 web_search：核心必查约 8 次必做；条件触发按条件；背景参考仅中期
   - ⚠️ 不要执行 --fetch-url all（ICI 为美股专用，本市场不适用）
   - ⚠️ 不要用 vnpy_mcp 的 get_history_bars 取美股数据（未连 IB 网关时返回空，会被误判为数据缺失）
4. 强制不使用任何缓存，本次全量重分析：先删除 scripts/.yf_history_cache（30 分钟历史缓存）；scripts/.yf_info_cache（12 小时）可选删除——删除会加剧限流，遇限流按 Step 0.3「重试容忍度」处理并记入「采集脚本失败项」
5. 输出必须严格按 加密货币市场.md 中【六、输出模板】的 JSON 格式，字段名必须完全一致（中文 key）
6. 跨市场边界：美股大盘报告为可选旁证——存在且近 3 日内可引用并注明来源；不存在或过期则直接跳过（不等待、不标 missing、不降置信度）
7. 任务名（= 报告名 = 落盘文件夹名）必须为：加密货币大盘与板块和资金流向分析（不得自定义）
8. 回答中输出JSON，不要保存到文件。

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
- git 克隆到**所用智能体系统的技能目录**（各系统约定不同），克隆的目录改名，本技能目录必须为：`tiger-stock-strategy-analysis`
  - WorkBuddy：项目级 `<项目根>/.workbuddy/skills/`
  - openclaw / codex：`<工作区目录>/skills/`
  - 其他智能体系统：按其技能目录约定（通常为 `skills/` 或 `~/.<系统名>/skills/`）
- 该目录下应有 `SKILL.md`、`docs` 目录、`scripts` 目录

### scripts 目录依赖

| 模块 | 安装命令 | 用途 |
|------|---------|------|
| `get_market_data.py` | `pip install yfinance` | 获取结构化价格数据（含均线、52周百分位） |
| `get_market_data.py` | `pip install requests beautifulsoup4 numpy` | HTTP 请求、HTML 解析、数值计算（ATR/CCI/支撑压力位） |
| `get_market_data.py`（calendar） | `pip install -U camoufox[geoip]` + `camoufox fetch` | 必须绕过 Cloudflare 获取 ForexFactory 经济日历 |
| `test_get_market_data.py` | `pip install pandas` | 测试脚本中构造模拟 DataFrame 数据 |
| `tools.py` | （纯标准库，无需安装） | JSON 文件读取工具函数 |