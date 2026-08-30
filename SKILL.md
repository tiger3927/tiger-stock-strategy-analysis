---
name: "tiger-stock-strategy-analysis"
description: "股票量化策略分析工具：为 vnpy 量化软件提供策略分析和下单前审核。支持智能马丁格尔策略(openclaw-martin)、多信号权重评分趋势策略（Multi\_Signal\_Treand）；vnpy整体持仓分析与风控；大盘与板块和资金流向分析；做多做空选股；"
---

# tiger-stock-strategy-analysis

为 vnpy 量化系统提供策略分析，趋势预测，下单前审核！
当执行的策略是本技能涉及的策略类型，你必须打开相应策略类型的文档，了解策略的具体方法，而后思考。
如果用户要求分析的策略，不是本技能支持的策略类型，应该明确的提示给用户。

## vt\_symbol产品信息表

用途：对照查询策略中 vt\_symbol 对应的品种信息（名称、中文名、ticker、分类、行业等，美股品种可查盈透 conid），供策略分析、品种识别与下单前审核使用。

表中缺少的品种或者交易所该品种信息变化，要更新该表。

详细说明、品种缺失 / conid 变化修复流程见 [docs/vt_symbol_info.md](docs/vt_symbol_info.md)。

## 信息获取方法（缓存优先 + 增量更新）

### 尽量用如下外部技能获取股票的信息

1. 用到的相关的外部技能skill矩阵，（以 QQQ ETF 为标的）：

| #  | 要使用的外部技能名称                 | 可采用 | 可能的信息结果                                       | 备注              |
| -- | -------------------------- | --- | --------------------------------------------- | --------------- |
| 1  | `web_search`               | ✅   | Barchart, TradingView, TipRanks 数据聚合          | <br />          |
| 2  | `yahoo-finance` (yfinance) | ✅   | $711.23, MA50=$620.91, MA200=$607.31, YTD+40% | **核心数据源**       |
| 3  | `us-stock-analysis`        | ✅   | 技术分析框架 + 指标解读                                 | 搭配 web\_search  |
| 4  | `agent-reach` (X/Twitter)  | ✅   | QQQ Options/May OPEX 讨论                       | 舆情分析            |
| 5  | `agent-reach` (Reddit)     | ✅   | r/ETFs/r/QQQ社区热度                              | 社区情绪            |
| 6  | `deep-research-pro`        | ✅   | 深度分析                                          | 基本面逻辑           |
| 7  | `multi-search-engine`      | ✅   | TradingView/TipRanks/Barchart交叉验证             | 多源确认            |
| 8  | `tavily`                   | ✅   | 指标，价格，综合信息                                    | 价格目标            |
| 9  | `qveris`                   | ✅   | 行情采集与信息手机                                     | **独立行情 API**    |
| 10 | `ddg-search`               | ✅   | 信息收集                                          | DuckDuckGo HTML |

1. 分析股票等的交易策略，必须获取的信息维度（10项）

| 序号 | 信息类型             |
| -- | ---------------- |
| 1  | 真实公司名称/交易品种      |
| 2  | 最新财报或权威公开信息      |
| 3  | 近 7 天新闻          |
| 4  | Reddit、X 等社交平台讨论 |
| 5  | 最近 24 小时真实成交区间   |
| 6  | 最近 3 个月价格区间      |
| 7  | 当前策略状态信息         |
| 8  | 当前持仓、方向、成本、资金占比  |
| 9  | 策略本次持仓的历史交易记录    |
| 10 | 市场环境、行业环境、事件风险   |

### web\_search 失效后的替代方法

web\_search 使用 Tavily 引擎，每月限额 1000 次，超出后 web\_search 将不可用。

**首选替代：OrioSearch（`https://search.my-gun.top`）** — 自建搜索服务，提供与 Tavily 相同的协议接口，**无需 API key**。

接口：`POST https://search.my-gun.top/search`，请求体（Tavily 兼容）：

```json
{
    "query": "搜索内容",
    "search_depth": "basic",
    "max_results": 5,
    "include_answer": true
}
```

返回 `answer`（AI 摘要）+ `results`（title/content/url 列表），用法：

- 优先取 `answer` 字段（有 AI 摘要时最快）
- `answer` 为空时，从 `results` 前 3-5 条提取 `title`+`content` 作为推算文本
- `search_depth=advanced` 超时 → 降级 `basic` 重试
- 注意：`content` 是 Meta Description 短摘要，截取 500 字符以内

**其次：web\_fetch 依次尝试**：

```
1st: Startpage — Google 内核，无反爬，结果最全
2nd: Brave   — 结果质量好，但有 429 限流
3rd: Bing    — 稳定可靠，结果偏泛
```

## 信息获取优先级（从高到低）

### 上下文中的策略状态信息

"如果策略在执行中，会在上下文中提供执行策略状态信息"

### 用外部技能联网查询分析的信息

### vnpy\_mcp 工具查询与操作（优先）

**vnpy\_mcp 是当前环境中独立部署的 MCP 工具集（MCP server），不从属于任何技能。** 本技能只是调用它，不包含它；无论是否加载本技能，vnpy\_mcp 都存在于当前环境中，可直接调用。

凡是涉及以下事项，**优先直接调用 vnpy\_mcp 工具集中的具体工具**，不要先反复阅读本文来替代工具调用：

- 账户、持仓、活动委托、成交记录
- 策略状态、策略参数、策略分析记录
- 大盘分析报告、选股结果报告
- 合约、vt\_symbol、ConID、Tick、历史 K 线
- 调仓、平仓、设参、发送通知、启停策略、新增/删除策略

执行规则：

1. 查询实时数据时，**直接调用 vnpy\_mcp 工具集中的具体工具**（如 `get_accounts`、`cta_report_get` 等），不要把文档内容当作实时数据来源。
2. 只有在**忘记工具名、参数名、返回字段**时，才回看 [docs/vnpy\_mcp.md](docs/vnpy_mcp.md)。
3. 如需发送操作指令，前提是**量化系统必须正在运行**。
4. 如需 vnpy\_mcp 工具集的完整指南，可调用 `get_guide`（它是 vnpy\_mcp 工具集下的一个工具，不是独立 MCP 工具）。

> `SKILL.md` 只负责路由和优先级说明；vnpy\_mcp 的具体工具名、参数和返回格式，以 `get_guide` 和 [docs/vnpy\_mcp.md](docs/vnpy_mcp.md) 为准。

## 大盘与板块和资金流向分析

本技能支持对**不同交易市场**进行大盘走势、板块轮动和资金流向的综合分析。详细说明见 [docs/大盘与板块和资金流向分析/00\_index.md](docs/大盘与板块和资金流向分析/00_index.md)。

**报告读取方式**：分析结果由量化系统自动保存为分析报告，通过 vnpy\_mcp 的 `cta_report_list` / `cta_report_get` 直接获取。

| 市场类型 | 分析文档                                  | 报告名称（cta\_report\_get 参数） |
| ---- | ------------------------------------- | ------------------------- |
| 美股   | [美股市场](docs/大盘与板块和资金流向分析/美股市场.md)     | `美股大盘与板块和资金流向分析`          |
| 加密货币 | [加密货币市场](docs/大盘与板块和资金流向分析/加密货币市场.md) | `加密货币大盘与板块和资金流向分析`        |
| 中国期货 | 待补充                                   | 待确认                       |
| 中国A股 | 待补充                                   | 待确认                       |
| 港股   | 待补充                                   | 待确认                       |
| 台股   | 待补充                                   | 待确认                       |
| 日股   | 待补充                                   | 待确认                       |

## 美股选股模块

本技能支持对**美股市场**进行做多/做空选股分析，根据用户指定的 `direction` 参数自动路由：

- **做多方向**：按照 [美股做多选择.md](docs/美股选股/美股做多选择.md) 中定义的选股逻辑，结合大盘环境，从候选股池中筛选符合「价值+成长混合（GARP）」策略的做多标的，输出评分卡和入场计划。
- **做空方向**：按照 [美股做空选择.md](docs/美股选股/美股做空选择.md) 中定义的做空逻辑，结合大盘环境，在估值泡沫、基本面恶化或板块轮动过热的标的中筛选做空标的，输出评分卡和入场计划。

详细执行流程见 [docs/美股选股/00\_index.md](docs/美股选股/00_index.md)。

报告读取方式：选股结果由量化系统自动保存为分析报告，通过 vnpy\_mcp `cta_report_get` 获取：

- 做多：`cta_report_get(report_kind="美股做多选股结果")`
- 做空：`cta_report_get(report_kind="美股做空选股结果")`

## vnpy整体持仓分析与风控

本技能支持对指定用户的**量化交易账户整体状态**进行综合分析，包括账户风险评估、各策略持仓盈亏分析，并结合大盘与板块走势判断是否存在重大机会或风险，必要时发送控制命令进行调整。

详细说明见 [docs/vnpy整体持仓分析与风控/00\_index.md](docs/vnpy整体持仓分析与风控/00_index.md)。

## 你支持如下量化交易策略类型

### 智能马丁格尔网格策略 (openclaw-martin)

一种扩展的CTA趋势策略

- **核心逻辑**: 基于支撑压力结构的马丁格尔资金管理——做多时在支撑区建仓、下跌分批加仓摊低成本；做空时在压力区建仓、上涨分批加空仓摊高成本。价格回归盈利线时整体获利退出
- **风险控制**: 最大仓位上限 + 止损线（网格间距 × 网格数量 × 0.6）+ 关键位失效 + 基本面恶化 + 多指标背离预警
- **适用场景**: 震荡行情、区间波动明确的标的，支持做多和做空双向

具体见入口文档：

[docs/Martingale-Grid-Trading-Strategy/00-Router.md](docs/Martingale-Grid-Trading-Strategy/00-Router.md)

### 多信号权重评分趋势策略  （Multi\_Signal\_Treand）

一种扩展的CTA趋势策略

- **核心逻辑**: 量化策略代码计算多信号并权重评分，高分开仓，止盈或止损离场
- **风险控制**: 设置止损线
- **适用场景**: 趋势、反弹，等明显具备趋势且持续数天到一个月的场景

具体见：

[docs/Multi\_Signal\_Treand\_Strategy/Multi\_Signal\_Treand\_Strategy.md](docs/Multi_Signal_Treand_Strategy/Multi_Signal_Treand_Strategy.md)

## TigerGridTemplate CTA类策略的基类文档（CTA策略的参数驾驭手册）

> 上面两类策略均继承自 `TigerGridTemplate` 基类。
> 该文档集（docs/Tiger-Grid-Template/）的唯一视角：**写给智能体的"通过参数设置驾驭策略代码"手册**
> ——只写"设了参数后会发生什么"（触发条件/量公式/风控拦截/参数联动），不含写策略代码与内部工艺。
> 入口：[00\_index.md](docs/Tiger-Grid-Template/00_index.md)（先读它确定加载哪些子文档）。

| 子文档 | 内容 | 何时查 |
|:--|:--|:--|
| 02 | 48 个参数（47 智能体体系 + 1 人工作业层控制）+ 标准名 + 可改性 | 查参数含义、哪些参数 AI 能改 |
| 03 | 13 个运行时变量 + 状态字典上报内容 | 查变量含义、读状态上报 |
| 04 | AI 返回 JSON 约定 + 分层复位语义 + 订单审核协议 | 返回 AI JSON / 订单审核时 |
| 05 | 止盈止损三系统（固定/ATR/移动止盈） | 问止盈止损怎么触发 |
| 06 | 网格加减仓四段逻辑 | 问网格怎么加减仓 |
| 07 | 交易量公式 + 下单风控链 | 问下单风控、每笔量怎么算 |
| 08 | 行为规则与坑点速查 | 理解行为、查坑点 |
| 09 | 典型场景的完整 JSON 参数配置 | 问"震荡/趋势该设什么参数" |
| 10 | 变盘逆势应对（5 层防御 + 背离信号参考） | 问"大盘方向变了怎么办" |

**要点：**
- 参数"标准名"（代码 name_cn）是 AI JSON 与状态上报的标准中文键名，勿用其他叫法；
- 人工作业层控制参数（`loss_close_need_manual`、`enable_openclaw_analysis`、`openclaw_main_interval`、`enable_openclaw_confirm_target_pos`）智能体**不可设置**，只需理解其效果（见 02 第七节）；
- 参数组合可配置出多种策略形态（一次性建仓/纯网格/纯趋势/盈利加仓进攻/变盘防御等，见 00 第五节）。

## 输出

- 采用 json 格式返回结果，结构清晰
- 具体字段必须符合策略中的具体要求
- 要区分分析任务和下单前审核任务

## 安装

地址：
<https://github.com/tiger3927/skill-tiger-stock-strategy-analysis.git>

git克隆到到工作区目录下的skills目录下，克隆的目录改名，本技能目录必须为：tiger-stock-strategy-analysis

tiger-stock-strategy-analysis目录下应该有本SKILL.md，docs目录，scripts目录

### scripts 目录依赖

| 模块                             | 安装命令                                                | 用途                                             |
| ------------------------------ | --------------------------------------------------- | ---------------------------------------------- |
| `get_market_data.py`           | `pip install yfinance`                              | 获取结构化价格数据（含均线、52周百分位）                          |
| `get_market_data.py`           | `pip install requests beautifulsoup4 numpy`         | HTTP 请求、HTML 解析、数值计算（ATR/CCI/支撑压力位）            |
| `get_market_data.py`（calendar） | `pip install -U camoufox[geoip]` + `camoufox fetch` | 必须绕过 Cloudflare 获取 ForexFactory 经济日历           |
| `test_get_market_data.py`      | `pip install pandas`                                | 测试脚本中构造模拟 DataFrame 数据                         |
| `tools.py`                     | （纯标准库，无需安装）                                         | JSON 文件读取工具函数                                  |
| `update_vt_symbol.py`          | （纯标准库，无需安装）                                         | 管理 vt\_symbol\_info.json：新增/修改/删除/查询品种记录（原子写入） |

***

