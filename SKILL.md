---
name: "tiger-stock-strategy-analysis"
description: "股票量化策略分析工具：为 vnpy 量化软件提供策略分析和下单前审核。支持智能马丁格尔策略(openclaw-martin)、多信号权重评分趋势策略（Multi\_Signal\_Treand）、智能短期趋势策略(openclaw-trend)；可查询用户 redis 上的vnpy的量化交易账户信息、策略执行信息，并可向量化程序发送操作和查询指令（开平仓、调仓、行情查询等、查询盈透IBKR的conid）。无需 API key。以及，vnpy整体持仓分析与风控。"
---

# tiger-stock-strategy-analysis

为 vnpy 量化系统提供策略分析，趋势预测，下单前审核！
当执行的策略是本技能涉及的策略类型，你必须打开相应策略类型的文档，了解策略的具体方法，而后思考。
如果用户要求分析的策略，不是本技能支持的策略类型，应该明确的提示给用户。

## vt\_symbol产品代码表，与盈透conid，对照查询

策略信息中的产品代码是 vnpy 专属格式的时候，如 265598.SMART ，对照此文档，获得股票名称，分类：

推荐的美股的盈透conid参考：
[scripts/vt\_symbol\_info.json](scripts/vt_symbol_info.json)
如果上述文档中不包含，可以通过如下方式查询美股的盈透的conid：
[docs/vnpy-command-tool.md](docs/vnpy-command-tool.md)

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


### web_search 失效后的替代方法

web_search 使用 Tavily 引擎，每月限额 1000 次，超出后 web_search 将不可用。

替代方案（通过 web_fetch 依次尝试）：

```
1st: Startpage — Google 内核，无反爬，结果最全
2nd: Brave   — 结果质量好，但有 429 限流
3rd: Bing    — 稳定可靠，结果偏泛
```

### 查询的信息存档

路径规则 : \[工作区]/stock\_data/\[vt\_symbol]/
分为每种技能获取的信息，对应不同的存档md文件

| 外部技能名称                  | 文档名称                   | 文档用途：以QQQ指数产品为例                                         |
| ----------------------- | ---------------------- | ------------------------------------------------------- |
| web\_search             | web\_search.md         | 归集Barchart、TradingView、TipRanks等平台数据聚合信息，用于美股相关公开数据检索采集 |
| yahoo-finance（yfinance） | yahoo-finance.md       | 作为核心数据源，存储个股价格、50日均线、200日均线、YTD涨幅等实时行情数据                |
| us-stock-analysis       | us-stock-analysis.md   | 整理美股技术分析框架及各类技术指标解读逻辑，配合web\_search做行情深度分析              |
| agent-reach（X/Twitter）  | agent-reach-twitter.md | 采集X/Twitter平台QQQ期权、五月期权到期相关市场讨论，用于市场舆情分析归档              |
| agent-reach（Reddit）     | agent-reach-reddit.md  | 收录r/ETFs、r/QQQ等社区讨论内容与热度数据，记录美股社区情绪风向                   |
| deep-research-pro       | deep-research-pro.md   | 整理个股及行业深度研究内容，沉淀基本面分析逻辑与投资逻辑框架                          |
| multi-search-engine     | multi-search-engine.md | 记录TradingView、TipRanks、Barchart多平台数据交叉验证方法与结果，保障信息准确性   |
| tavily                  | tavily.md              | 汇总行情指标、标的价格、市场综合资讯，整理机构价格目标预测相关信息                       |
| qveris                  | qveris.md              | 独立行情API专用文档，归集行情自动采集、资讯信息抓取的接口规则与数据结果                   |
| ddg-search              | ddg-search.md          | 留存DuckDuckGo网页检索原始HTML信息，用于泛市场资讯收集与原始素材归档               |

重要规则 :

- ✅ 存：外部信息、报告、分析结果
- ❌ 不存：策略的当前信息（持仓、成本等），因为不同策略要复用这些外部数据
  质量门槛 : 如果觉得信息收集不够全面或者质量较差， 不要写这个 md 文件

## 信息获取优先级（从高到低）

### 上下文中的策略状态信息

"如果策略在执行中，会在上下文中提供执行策略状态信息"

### 用外部技能联网查询分析的信息

### Redis API 查询账户和策略持仓信息

如需要了解量化交易系统的持仓全貌，你可以从 redis api 查询整个量化交易系统的最新信息。

查询方式，详细用法见：[docs/redis-info.md](docs/redis-info.md)

**注意**：必须明确用户名，避免查错。

### vnpy-command-tool，量化程序命令工具

如需要向量化程序发送操作指令（开平仓、调仓、查询行情和策略参数，设置策略参数，查美股在盈透的 ConID 等），可使用统一命令工具vnpy-command-tool，前提是量化系统必须正在运行。

详细用法见：[docs/vnpy-command-tool.md](docs/vnpy-command-tool.md)

**注意**：该工具必须明确用户名和策略名，以及用户名，避免发错。

#### 支持的命令类型

| 命令                      | 说明                |
| ----------------------- | ----------------- |
| `conid`                 | 查询股票 IB ConID     |
| `close`                 | 全部平仓              |
| `query_strategy_status` | 查询策略完整状态信息        |
| `set_target_pos`        | 调整目标持仓量           |
| `notice`                | 发送通知消息            |
| `publish`               | 向 Redis 发布信息（SET） |
| `get`                   | 读取 Redis 信息（GET）  |

## 大盘与板块和资金流向分析

本技能支持对**不同交易市场**进行大盘走势、板块轮动和资金流向的综合分析。详细说明见 [docs/大盘与板块和资金流向分析/00\_index.md](docs/大盘与板块和资金流向分析/00_index.md)。

所有市场的缓存 key 统一由 [00\_index.md 的市场子模块表格](docs/大盘与板块和资金流向分析/00_index.md#市场子模块) 定义，**不得自定义 key 格式或路径**。

| 市场类型 | 分析文档                                  | 缓存 key                    |
| ---- | ------------------------------------- | ------------------------- |
| 美股   | [美股市场](docs/大盘与板块和资金流向分析/美股市场.md)     | `/vnpy:美股:大盘与板块和资金流向分析`   |
| 加密货币 | [加密货币市场](docs/大盘与板块和资金流向分析/加密货币市场.md) | `/vnpy:加密货币:大盘与板块和资金流向分析` |
| 中国期货 | 待补充                                   | `/vnpy:中国期货:大盘与板块和资金流向分析` |
| 中国A股 | 待补充                                   | `/vnpy:中国A股:大盘与板块和资金流向分析` |
| 港股   | 待补充                                   | `/vnpy:港股:大盘与板块和资金流向分析`   |
| 台股   | 待补充                                   | `/vnpy:台股:大盘与板块和资金流向分析`   |
| 日股   | 待补充                                   | `/vnpy:日股:大盘与板块和资金流向分析`   |

## 美股选股模块

本技能支持对**美股市场**进行做多/做空选股分析，根据用户指定的 `direction` 参数自动路由：

- **做多方向**：按照 [美股做多选择.md](docs/选股/美股做多选择.md) 中定义的选股逻辑，结合大盘环境，从候选股池中筛选符合「价值+成长混合（GARP）」策略的做多标的，输出评分卡和入场计划。
- **做空方向**：按照 [美股做空选择.md](docs/选股/美股做空选择.md) 中定义的做空逻辑，结合大盘环境，在估值泡沫、基本面恶化或板块轮动过热的标的中筛选做空标的，输出评分卡和入场计划。

详细执行流程见 [docs/选股/00\_index.md](docs/选股/00_index.md)。

缓存 key：
- 做多：`/vnpy:美股:选股分析结果`（12 小时过期）
- 做空：`/vnpy:美股:做空选股分析结果`（12 小时过期）

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

[docs/Multi\_Signal\_Treand\_Strategy.md](docs/Multi_Signal_Treand_Strategy.md)

### 智能短期趋势策略  (openclaw-trend)

一种扩展的CTA趋势策略

- **核心逻辑**: 结合技术指标判断短期走势
- **买入信号**: 多头排列、成交量放大、突破关键位
- **卖出信号**: 空头排列、跌破支撑、背离现象，或者指明此种策略的应用场景

具体见：

[docs/Short-term-CCI-Trend-Strategy.md](docs/Short-term-CCI-Trend-Strategy.md)

## CTA趋势策略基类说明

一般无需深入了解基类原理；若确需查阅，入口文档如下：

- [tiger\_grid\_template 策略基类文档](docs/Tiger-Grid-Template/00_index.md)

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

| 模块                             | 安装命令                                                | 用途                                   |
| ------------------------------ | --------------------------------------------------- | ------------------------------------ |
| `get_market_data.py`           | `pip install yfinance`                              | 获取结构化价格数据（含均线、52周百分位）                |
| `get_market_data.py`           | `pip install requests beautifulsoup4 numpy`         | HTTP 请求、HTML 解析、数值计算（ATR/CCI/支撑压力位） |
| `get_market_data.py`（calendar） | `pip install -U camoufox[geoip]` + `camoufox fetch` | 必须绕过 Cloudflare 获取 ForexFactory 经济日历 |
| `stock_redis_query.py`         | `pip install requests`                              | 通过 Redis Proxy API 查询账户/策略信息          |
| `vnpy_command.py`              | `pip install requests`                              | 通过 Redis Proxy API 发送命令（conid/close/publish 等） |
| `test_get_market_data.py`      | `pip install pandas`                                | 测试脚本中构造模拟 DataFrame 数据                |
| `tools.py`                     | （纯标准库，无需安装）                                       | JSON 文件读取工具函数                         |

***

