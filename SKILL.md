---
name: "tiger-stock-strategy-analysis"
description: "股票量化策略分析工具：为 vnpy 量化软件提供策略分析和下单前审核。支持智能马丁格尔策略(openclaw-martin)、多信号权重评分趋势策略（Multi\_Signal\_Treand）、智能短期趋势策略(openclaw-trend)；可查询用户 redis 上的vnpy的量化交易账户信息、策略执行信息，并可向量化程序发送操作指令（开平仓、调仓等）。无需 API key。"
---

# tiger-stock-strategy-analysis

为 vnpy 量化系统提供策略分析，趋势预测，下单前审核！
当执行的策略是本技能涉及的策略类型，你必须打开相应策略类型的文档，了解策略的具体方法，而后思考。
如果用户要求分析的策略，不是本技能支持的策略类型，应该明确的提示给用户。

## vt\_symbol产品代码表，与盈透conid，对照查询

策略信息中的产品代码是 vnpy 专属格式的时候，如 265598.SMART ，对照此文档，获得股票名称，分类：

推荐的美股，盈透conid和品种参考
[scripts/vt\_symbol\_info.json](scripts/vt_symbol_info.json)

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

### Redis API 查询

如需要了解量化交易系统的持仓全貌，你可以从 redis api 查询整个量化交易系统的最新信息。

#### 查询方式

详细用法见：[docs/redis-info.md](docs/redis-info.md)

**注意**：必须明确用户名，避免查错。

### 通过RedisProxy 向量化程序发送命令

如需要向量化程序发送操作指令（开平仓、调仓等），可通过命令发送工具实现。

详细用法见：[docs/stock-command-tool.md](docs/stock-command-tool.md)

**注意**：必须明确用户名和策略名，避免发错。

#### 支持的命令类型

| 命令               | 说明      |
| ---------------- | ------- |
| `close`          | 全部平仓    |
| `set_target_pos` | 调整目标持仓量 |
| `notice`         | 发送通知消息  |

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

- [tiger_grid_template 策略基类文档](docs/Tiger-Grid-Template/00_index.md)


## 输出

- 采用 json 格式返回结果，结构清晰
- 具体字段必须符合策略中的具体要求
- 要区分分析任务和下单前审核任务

## 安装

地址：
<https://github.com/tiger3927/skill-tiger-stock-strategy-analysis.git>

git克隆到到工作区目录下的skills目录下，克隆的目录改名，本技能目录必须为：tiger-stock-strategy-analysis

tiger-stock-strategy-analysis目录下应该有本SKILL.md，docs目录，scripts目录

***

