# 03 — 变量列表与状态上报

> 运行时自动计算记录的 15 个变量。`上报名` = `_build_status_dict()` 中向 AI 上报时使用的中文名称，`—` = 不在状态字典中暴露（但系统内部仍然使用）。

---

## 一、全部运行时变量

| 变量名 | 上报名 | 说明 |
|:--|:--|:--|
| `tick_price` | `当前行情价格` | 最近 tick 价格 |
| `tick_timestamp` | — | 最近 tick 时间戳 |
| `base_direction` | — | 策略操作方向（1=多，-1=空，0=未知），由子类的信号或 AI 分析结果设定 |
| `pos_price` | `持仓均价` | 当前持仓均价 |
| `first_open_price` | — | 初始开仓价（网格基准锚点，首笔成交时记录，后续加仓不变） |
| `current_profit_loss_radio` | `当前持仓盈亏比` | 当前持仓盈亏比例 |
| `low_stop_price` | — | 当前实时计算的止损价格 |
| `high_stop_price` | — | 当前实时计算的止盈价格 |
| `total_v` | `策略累计盈亏` | 策略开启后累计盈亏金额 |
| `target_pos` | `持仓目标` | 下单目标持仓量 |
| `target_timestamp` | — | 下单起始时间戳 |
| `target_delay_minute` | — | 下单容忍最大延迟分钟数 |
| `target_allow_price` | — | 下单允许最大滑点价格 |
| `poschange` | `已有网格仓数量`（`len()`） | 当前持仓的加建仓成交历史记录 |
| `max_position_ratio` | — | 最大允许持仓在策略资金的占比 |

---

## 二、`_build_status_dict()` — 注入 AI 的状态信息

子类构建 prompt 时调用此方法获取当前策略状态的快照字典：

| 状态字段 | 类型 | 说明 |
|:--|:--|:--|
| `vt_symbol` | string | 合约代码（大写） |
| `实际持仓` | int | 当前持仓（正=多，负=空，0=空仓） |
| `持仓目标` | int | 当前目标持仓量 |
| `持仓均价` | float | 加权平均持仓成本 |
| `策略累计盈亏` | float | 启动以来总盈亏金额 |
| `当前持仓盈亏比` | float | 浮动盈亏比例 |
| `当前行情价格` | float | 最新 tick 价格 |
| `已使用资金占比` | float | 持仓市值 / 总资金 |
| `已有网格仓数量` | int | 已触发网格档数 |
| `持仓交易历史` | list | 当前持仓周期的成交记录列表 |

此外还包含一个嵌套的 `策略基础参数设置和当前变量` 字典（子类可扩展），内含当前所有开关、比例、价格的实时值。以下为该嵌套字典中的全部字段（按上报名-变量名映射）：

| 上报名 | 变量名 | 说明 |
|:--|:--|:--|
| `主信号K线周期（分钟数）` | `use_1m_5m_15m_30m_60m` | 策略主 K 线周期 |
| `最小交易量单位` | `volume_min_unit` | 最小交易股数 |
| `杠杆` | `trade_radio` | 杠杆倍数 |
| `可用资金总量` | `start_asset + total_v` | 启动资金 + 累计盈亏 |
| `开仓动用资金比例` | `first_part` | 首仓占用资金比例 |
| `止盈开关` | `enable_stop_profit` | 固定比例止盈开关 |
| `止盈幅度` | `stop_profit_radio` | 固定止盈比例 |
| `止损开关` | `enable_stop_loss` | 固定比例止损开关 |
| `止损幅度` | `stop_loss_radio` | 固定止损比例 |
| `移动止盈开关` | `enable_stop_autoprofit` | 移动止盈开关 |
| `移动止盈启动幅度` | `stop_autoprofit_start_radio` | 移动止盈触发阈值 |
| `移动止盈回撤幅度` | `stop_autoprofit_back_maxvalue` | 移动止盈回撤平仓幅度 |
| `马丁格尔K线周期` | `martin_k_time` | 网格使用的 K 线周期 |
| `马丁格尔CCI信号周期` | `cci_martin_period` | CCI 指标周期 |
| `允许马丁格尔CCI信号主动开仓` | `enable_martin_add_open` | 空仓时允许自动开仓 |
| `盈利时允许马丁格尔加仓（等距）` | `enable_martin_add_profit` | 盈利时网格加仓开关 |
| `亏损时允许马丁格尔加仓（等距）` | `enable_martin_add_loss` | 亏损时网格加仓开关 |
| `马丁格尔网格间距` | `martin_grid_distance` | 网格加仓价格间距 |
| `马丁格尔网格数` | `martin_add_count` | 最大加仓次数 |
| `网格是否按金字塔增减加仓` | `martin_add_pyramid` | 金字塔加仓开关（上方减免） |
| `金字塔每格变化比例` | `martin_add_pyramid_radio` | 金字塔每格加减仓比例基数 |
| `亏损平仓是否人工审核` | `loss_close_need_manual` | 亏损平仓时是否需人工确认 |
| `允许减盈利网格仓` | `enable_martin_sub` | 允许减盈利网格仓（统一开关，不区分盈亏状态） |
| `马丁格尔网格止盈幅度` | `martin_grid_profit` | 网格仓需盈利多少才允许减仓 |

> **注意：** 上表红色加粗标记的 3 个字段为近期新增，若 AI 上报数据中缺失属正常。

> **快照特性：** 输出是调用时刻的快照，不是实时流。AI 应基于快照分析，不考虑调用期间的微小价格变化。
