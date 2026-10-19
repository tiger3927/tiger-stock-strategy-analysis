---
name: "tiger-stock-strategy-analysis"
description: "股票量化策略分析工具：为 vnpy 量化软件提供策略分析和下单前审核。支持智能马丁格尔策略(openclaw-martin)、多信号权重评分趋势策略（Multi\_Signal\_Treand）、多用途网格策略（趋势/反转/波动收波三意图）；大盘与板块和资金流向分析；做多做空选股；板块轮动调仓；vnpy量化系统全局配置答疑与修改；"
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

### 数据源边界（2026-10-18 定，不可越界）

本技能是**分析层**：市场与基本面数据只走脚本内的公开通道（Yahoo / FRED / SEC EDGAR / ICI / CNN 等）+ 受限 `web_search` / `web_fetch`。

- **禁止依赖 TWS / IB Gateway / 券商 API 取分析数据**（不得把 `ib_insync` 之类引入技能脚本）——技能必须在没有券商客户端登录的机器上也能完整运行，且不把分析职责与执行层耦合。
- 因此**券商账户侧字段**（可借数量、借券费率、做空保证金要求）在分析层一律标 `missing`：做空拥挤度用公开 `short_pct_of_float` / `short_days_to_cover` / `short_interest_prior` 判定；「能否真的借到券、成本多少」由**执行层下单时兜底**（不可借券的订单会被券商拒单）。
- 账户 / 持仓 / 委托 / 成交等**账户侧事实**仍按上一节走 `vnpy_mcp` 工具（`get_accounts`、`get_positions` 等）——那是量化系统已建立的连接，与本技能自己去连券商是两回事。

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
- **候选池**：[选股操作手册.md](docs/美股选股/选股操作手册.md)（候选池**动态生成**规则：板块衍生 / 全网扫描 / 用户指定，由 00\_index Step 2 引用；**每次生成都必须执行 §七 池外探测**）
- **选股池**：[美股选股池管理.md](docs/调仓/美股/美股选股池管理.md)（固定池数据源 `scripts/stock_pool.json`，校验脚本 `scripts/sync_stock_pool.py`，补 T3 用 `scripts/refill_stock_pool_t3.py`；三层结构 + §6.4 池外探测 + **§10 历史模式（as-of）回放与前视隔离**——回放沙箱 `pool_snapshot.py` / 评估 `pool_backtest.py` / 成分 as-of `fetch_index_constituents.py`，**做"假如当时执行"的回放必须先读 §10**；**只是种子兜底，不是视野边界**）

详细执行流程见 [docs/美股选股/00\_index.md](docs/美股选股/00_index.md)。

报告读取方式：选股结果由量化系统自动保存为分析报告，通过 vnpy\_mcp `cta_report_get` 获取：

- 做多：`cta_report_get(report_kind="美股做多选股结果")`
- 做空：`cta_report_get(report_kind="美股做空选股结果")`

## 加密货币选币模块

本技能支持对**加密货币市场**进行做多/做空选币分析，根据用户指定的 `direction` 参数自动路由：

- **做多方向**：按照 [加密货币做多选择.md](docs/加密货币选择/加密货币做多选择.md) 中定义的逻辑（叙事成长型：代币经济学 + 协议基本面 + 资金面），结合大盘环境预测下一个轮入板块，从选币池中筛选做多标的，输出评分卡和入场计划。
- **做空方向**：按照 [加密货币做空选择.md](docs/加密货币选择/加密货币做空选择.md) 中定义的逻辑（板块过热/叙事衰竭 + **解锁抛压** + 资金费率与杠杆），筛选做空标的；**必须通过轧空风险评估与做空成本核算两道闸**。
- **选币池**：[选币操作手册.md](docs/加密货币选择/选币操作手册.md)（数据源 `scripts/crypto_pool.json`，校验脚本 `scripts/sync_crypto_pool.py`）

详细执行流程见 [docs/加密货币选择/00\_index.md](docs/加密货币选择/00_index.md)。

报告读取方式：选币结果由量化系统自动保存为分析报告，通过 vnpy\_mcp `cta_report_get` 获取：

- 做多：`cta_report_get(report_kind="加密货币做多选股结果")`
- 做空：`cta_report_get(report_kind="加密货币做空选股结果")`

> ⚠️ **可交易性**：候选标的必须与 `get_all_contracts`（BINANCE_LINEAR）求交集，`vt_symbol` 以系统返回值为准，不得使用池中缓存值（币安存在 `1000PEPE` 类符号变体）。

## 美股板块轮动调仓

本技能支持对**美股持仓**进行机构级板块轮动调仓（宏观定调 → 板块排序 → 子板块排序 → 持仓映射 → 调仓执行 → 仓位风控 → 拆单优化）。

详细说明见 [docs/调仓/美股/00_index.md](docs/调仓/美股/00_index.md)（美股板块轮动调仓文档组：索引 + 七层方法论 + 策略资金分配 + AI 调仓输出模板，共 13 篇，消费固定池 `scripts/stock_pool.json`；池子本身的新增/删除/池外探测见 [美股选股池管理.md](docs/调仓/美股/美股选股池管理.md)）。

**任务完成标准（一次闭环，00_index §4）**：任务「生成调仓报告」必须一次执行完——取数 → fund → gap → 评分/映射 → assemble → AI 填 调仓指令/持仓映射/文本字段 → **verify「校验」= 通过** → 交付最终报告 JSON。中间产物（各子命令 JSON、assemble 骨架）均不是任务完成；「无法校验/不通过」≠ 通过，指令集为空的"全绿"verify 是空洞通过。

## vnpy整体持仓分析与风控

本技能支持对指定用户的**量化交易账户整体状态**进行综合分析，包括账户风险评估、各策略持仓盈亏分析，并结合大盘与板块走势判断是否存在重大机会或风险，必要时发送控制命令进行调整。

详细说明见 [docs/vnpy整体持仓分析与风控/00\_index.md](docs/vnpy整体持仓分析与风控/00_index.md)。

## vnpy量化系统全局配置

本技能支持 vnpy 量化系统**全局配置参数**（`.vntrader/vt_setting.json`）的查询、答疑与修改建议，覆盖：界面/日志、邮件/数据源/数据库、AI 分析链路（`use_goose` goose 直连 vs OpenClaw 桥）、数据代理、MCP 认证令牌等。
用户问"某配置项是什么/默认值/怎么改/改了有什么影响"或要求修改全局配置时，查阅本文档。

详细说明见 [docs/vnpy量化系统配置/vnpy量化系统配置.md](docs/vnpy量化系统配置/vnpy量化系统配置.md)。

## 你支持如下量化交易策略类型

### 智能马丁格尔网格策略 (openclaw-martin)

一种扩展的CTA趋势策略

- **核心逻辑**: 基于支撑压力结构的马丁格尔资金管理——做多时在支撑区建仓、下跌分批加仓摊低成本；做空时在压力区建仓、上涨分批加空仓摊高成本。价格回归盈利线时整体获利退出
- **风险控制**: 最大仓位上限 + 止损线（网格间距 × 网格数量 × 0.6）+ 关键位失效 + 基本面恶化 + 多指标背离预警
- **适用场景**: 震荡行情、区间波动明确的标的，支持做多和做空双向

具体见入口文档：

[docs/Martingale-Grid-Trading-Strategy/00-Router.md](docs/Martingale-Grid-Trading-Strategy/00-Router.md)

### 多用途网格策略（多意图网格 CTA）

一套策略承载三种意图，"一仓一意图"，仓位=0 时整套切换参数：

- **核心逻辑**: **趋势** = 突破回踩确认后一笔进场吃一段趋势（网格关闭，止损=形态失效线，盈亏比预期 ≥3:1~5:1）；**反转** = 大级别结构位反向接货，等均值修复到中轴离场（抓V反 / 阶段抄底两种入场锚定）；**波动收波** = 8~10 格区间来回赚差价（网格方向跟随大环境，单边打穿即认输）
- **风险控制**: 固定止损恒开（关闭则禁止开新仓）+ 按意图的目标止盈/盈利离场 + 首仓价格区间防追高 + 平仓冷却；阶段流转：空仓观察 → 等待首仓 → 首仓建立中 → 网格管理期 → 利润收割期 → 风险退出期（持仓期任一阶段可因基本面恶化/止损触发/重大风险直入风险退出期）
- **意图执行**: 三明治结构——核心思路=宪法（人工/宏观层写，AI 只读核对）、备注=本手合同（方向/依据/目标位/失效价/时限/考核条款，开仓前写好）、通知=短期禁令电报；持仓期换意图须满足兼容三条件+重大变化，否则先清仓再切
- **适用场景**: 震荡、趋势、深跌修复等不同行情形态各选对应意图；支持做多和做空双向（美股、加密货币）

具体见文档：

- 入口/执行索引：[docs/多用途网格策略/00-Index.md](docs/多用途网格策略/00-Index.md)（铁则、三意图参数速查、阶段表、机制）

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

git 克隆到**所用智能体系统的技能目录**（各系统约定不同，见下表），克隆的目录改名，本技能目录必须为：tiger-stock-strategy-analysis

| 智能体系统 | 技能目录位置 |
|---------|---------|
| WorkBuddy | 项目级：`<项目根>/.workbuddy/skills/` |
| openclaw / codex | `<工作区目录>/skills/` |
| 其他智能体系统 | 按其技能目录约定（通常为 `skills/` 或 `~/.<系统名>/skills/`） |

tiger-stock-strategy-analysis 目录下应该有本 SKILL.md，docs 目录，scripts 目录（放在哪个系统下都须如此）

### scripts 目录依赖

| 模块                             | 安装命令                                                | 用途                                             |
| ------------------------------ | --------------------------------------------------- | ---------------------------------------------- |
| `get_market_data.py`           | `pip install yfinance`                              | 获取结构化价格数据（含均线、52周百分位）                          |
| `get_market_data.py`           | `pip install requests requests-cache beautifulsoup4 numpy pandas lxml` | HTTP 请求、HTML 解析、数值计算（ATR/CCI/支撑压力位）、DataFrame 数据处理（顶层硬依赖，含 pd.read_html 所需的 lxml）。注：requests-cache 为历史遗留依赖，**未启用**（yfinance 1.5.2 拒绝缓存会话）；实际缓存=K线 pkl 30min + info json 12h 两层，`--no-cache` 穿透 |
| `get_market_data.py`（历史模式） | （无新增依赖）                            | `--end YYYY-MM-DD` 回放取数（period1/period2 + adj_close 复权列 + 独立不可变窗口缓存 `--cache-dir`）；`--fields/--minimal/--retries/--concurrency` 批量控制；缺省不传 = live 行为不变。**v3(2026-10-18)**：`--out <path>` 结果落盘 + stdout 只回一行摘要（不传 = stdout 逐字节不变，保护 subprocess 消费者）；`--sector-median` 批次内同板块 PE/P-S/EV-EBITDA 中位数（opt-in，样本<3 不写）；**修正 `--no-cache` 此前不穿透 12h info 缓存**的缺陷（现两层缓存一致：不读但仍写入） |
| `get_market_data.py`（v3 新增字段，只增不改） | （无新增依赖） | 每条记录新增：做空 `short_interest` / `short_interest_prior` / `short_pct_of_float`（小数）/ `short_days_to_cover`；估值 `ps_ratio` / `peg_ratio` / `ev_ebitda`（`ev_ebitda_derived` 标记本地推算）/ `earnings_growth` / `profit_margins` / `fcf_yield` / `net_debt_to_ebitda`；技术面 `rsi_14` / `macd` / `macd_signal` / `macd_hist` / `atr_14`（本地由 K 线算，`--end` 下按截止日截断）。做空字段是 Yahoo 双月快照 → `--as-of` 不可回溯，报告标 `point_in_time=false`。**入口已统一强制 stdout/stderr 为 UTF-8**（修 GBK 控制台中文/符号崩溃，即 A1 子进程解码崩溃根因），调用方不再需要 `-X utf8` / `PYTHONIOENCODING` |
| `get_market_data.py --options`（期权链，2026-10-18 新增） | （无新增依赖，复用既有代理 + crumb 通道） | 期权链取数：`--options TICKER`（`--opt-side put/call/both`、`--opt-expiry`、`--opt-strike-pct`、`--opt-all`、支持 `--out`）。返回 `expiry_summary[]`（DTE / put·call 未平仓量 / IV 中位数）、`contracts[]`（strike / moneyness / iv / OI / volume / bid·ask / `premium_pct_of_spot` / `breakeven` / `bid_ask_spread_pct`）、`put_call_oi_ratio`、`iv_atm_put_median`；30 分钟缓存。**只有当前挂牌合约 → `--end/--as-of` 直接拒绝**（P0-5 类②） |
| `get_market_data.py --fetch-url web-indicators`（历史模式，P0-6 2026-10-18 新增） | （无新增依赖，FRED CSV + curl_cffi 指纹） | `--end` 下宏观指标回溯层：`CPI`（FRED `CPIAUCSL`/`CPILFESL`，`value`＝同比 + `headline_mom_pct`/`core_yoy_pct`）、`Liquidity`（`WALCL−WTREGEN−RRPONTSYD`，$bn + 三分项）、`FOMC`（`DFEDTARU`/`DFEDTARL` → `target_range`/`range_since`/`last_move`）、`MarginDebt`（FINRA 月度，第三方再加工，`source=direct:third_party:*` + `debit_yoy_pct`/`excess_leverage`）、`Calendar`（FRED 日历 as-of 前 7 天）。实测 `--end 2025-06-30`：**hit 2/14 → 7/14**（加 VIX 族 10/14 可用），9.3 s。`FearGreed`/`FedWatch`/`PutCallRatio`/`Buyback`/`IPO`/`Secondary`/`Earnings` 保持 `asof_unsupported`（源头只有当前快照，PutCall 的 CBOE CSV 实测 403、Yahoo `^PUT` 量纲不符）；历史模式搜索层禁用 → `source_layers=direct:asof-only`，全部 `point_in_time=false` |
| `get_market_data.py`（可选）     | `pip install curl_cffi`                             | 可选：走代理直调 Yahoo chart API，替代被风控的 yfinance 内部请求；未安装自动降级 |
| `get_market_data.py`（calendar） | `pip install -U camoufox[geoip]` + `camoufox fetch` | 必须绕过 Cloudflare 获取 ForexFactory 经济日历           |
| `test_get_market_data.py`      | （无新增依赖）     | `get_market_data.py` 回归测试（构造模拟数据）                         |
| `tools.py`                     | （纯标准库，无需安装）                                         | JSON 文件读取工具函数                                  |
| `update_vt_symbol.py`          | （纯标准库，无需安装）                                         | 管理 vt\_symbol\_info.json：新增/修改/删除/查询品种记录（原子写入） |
| `sync_stock_pool.py`           | （纯标准库，无需安装）                                         | 校验美股选股池数据（stock\_pool.json 结构与统计）；v2 扩展：`--pool-file` 沙箱 / `--as-of` 到期锚定 / `--output json` 机器证据 / tier 数量与 conid 覆盖率校验（默认 warning，`--strict-tiers` 升级） |
| `refill_stock_pool_t3.py`      | （无新增依赖，pandas / lxml 已随 `get_market_data.py` 安装）                       | 补美股选股池 T3（潜力小盘）：S&P600/400 成分 → 子板块映射 → 取数验证（取数间接依赖 `get_market_data.py`；支持 dry-run / --apply / --only / --per；v2：`--pool-file` 沙箱 / `--as-of` 回放透传 --end，缓存按 as-of 隔离） |
| `generate_candidate_pool.py` | （无新增依赖，`requests` 已随 `get_market_data.py` 安装；可选 `curl_cffi`，未装自动降级） | 美股候选池生成：入口抓取 + 指标取数 + 筛选与池外探测（取数间接依赖 `get_market_data.py`；支持 `--data-file` 离线 / `--no-network`；v2：`--seed-pool` 沙箱 / `--as-of` 透传 --end / `--no-cache`；**v3(2026-10-18 性能修复)**：`--out` 落盘+一行摘要、`--prefilter-max` 取指标前预筛（`--as-of` 下自动禁用防前视）、`--probe-max-tickers` / `--probe-entries` / `--budget-seconds`、§5.1 分层配额截断（先保小盘名额）、入口去重 + 并发 3 + `pool_entries.json` 的 `disabled` 开关（finviz-screener 实测客户端渲染 0 条已停用）、子进程 UTF-8 容错、`审计` 新增耗时与取数量字段、**候选池记录直接携带做空/估值/技术面字段**（`short_*` / `ps_ratio` / `peg_ratio` / `fcf_yield` / `rsi_14` 等，Step 3+4 不必二次取数）；**P1-3 接线（2026-10-18）**：`--constituents off\|annotate\|filter`（仅 `--as-of` 生效，默认 annotate）按 SEC NPORT-P 官方成分给候选打 `in_constituents` **三态**标记（True＝当期成员 / False＝覆盖范围内但当期非成员＝**幸存者偏差信号** / None＝指数覆盖范围外），`filter` 额外剔除 False；结果新增 `constituents` 块（`holdings_as_of`/`lag_days`/`match_rate`/`判定` 计数，**不含 symbols 明细**防报告膨胀）与 `members_point_in_time`，与 `point_in_time`（榜单页维度）**分开标**；live 路径**完全不 import 不取数** |
| `pool_snapshot.py`（v2 新增）  | （纯标准库，无需安装）                                         | 选股池回放沙箱：`as-of` 生成回放副本 + 重写 meta 时间戳 + 备份；`restore` 恢复生产；`diff` 换手率。产物落 `<skill>/replay/`，命名红线禁止落 scripts/ |
| `pool_backtest.py`（v2 新增）  | （依赖 pandas + `get_market_data.py`）                       | 选股池历史回放评估：after/before/随机对照/基准四组，宽窗口一次取数本地切片，horizon 收益 + 波动/Sharpe/回撤/beta + 多日期胜率汇总（`--weight equal\|cap\|tier` / `--missing drop\|zero\|carry` / `--control random:N`）；**P1-3 接线（2026-10-18）**：`--constituents off\|annotate`（默认 annotate）每 as-of 用 SEC NPORT-P 官方成分对照池成员，输出 `survivorship_suspects`（**池里有、当期官方成分没有**）+ `per_tier` 三态计数 + `holdings_as_of`，payload 新增 `members_point_in_time` |
| `fetch_index_constituents.py`（**P1-3 新增，2026-10-18**） | （无新增依赖；`curl_cffi` 可选，未装自动回落 requests） | **指数/ETF 成分 as-of 取数**（SEC NPORT-P 官方档案，不预回填）：`--as-of` 走**严格双重门控 `reportDate ≤ as_of` 且 `filingDate ≤ as_of`** → 按需实时取 + 按 `accession` 落不可变缓存（跑过即成档案）。参数 `--index sp500|midcap400|all` / `--cik` / `--list-filings` / `--no-diff` / `--alias-file` / `--min-hit` / `--cache-dir` / `--out`。实测：SPY **28 份申报覆盖 2019-09-30..2026-06-30**；`--as-of 2025-06-30` → 持仓期末 **2025-03-31**（**滞后 91 天**，期末后 ~2 个月才公开），成员全集 **503**、映射到 ticker **484（96.2%）**、`added/removed` 就位；缓存命中后 **0.06 s**。**红线**：① 成分滞后 2~4 个月，报告须写明 `holdings_as_of`；② **ETF 持仓 ≠ 指数官方成分**；③ ticker 靠名称匹配，**年份越久匹配率越低**（2021-12-31 实测 86.8%，因改名/被并购/退市），未匹配项保留 `name+cusip`，**不可当成"当时没有这只股票"**；④ IWM 属 series 制申报人（AllSpring Funds Trust，一季多份）**未实现**，需 `--cik` 指定单基金申报人。**已接入回放链（2026-10-18）**：`constituents_for_asof()` 供 `generate_candidate_pool` / `pool_backtest` 调用（覆盖范围参照集＝当期 ∪ 最新一期，用于把指数外标的判 unknown 而非 False），实测 `--as-of 2021-12-31` → 持仓期末 2021-09-30（滞后 92 天）、567 候选中 426 命中 / **93 幸存者嫌疑** / 48 覆盖范围外，`PLTR` 正确判 False；回归 `tests/test_pool_constituents_wiring.py` **12/12** |
| `rebalance_fetch.py` | `pip install requests`（硬依赖） | 加密货币调仓取数（币安合约 K线 / 逐笔 / 最小成交单位） |
| `rebalance_tools.py` / `rebalance_audit.py` / `rebalance_acceptance.py` / `rebalance_x.py` | （纯标准库，无需安装） | 调仓工具链：计算与校验门（fund/gap/score/map/verify/exec）+ 源对账审计 + 一键验收 + 探索版 |
| `sync_crypto_pool.py` | （纯标准库，无需安装） | 校验加密货币选币池数据（crypto_pool.json 结构与统计） |

***

