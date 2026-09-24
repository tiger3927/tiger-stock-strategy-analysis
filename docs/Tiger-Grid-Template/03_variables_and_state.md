# 03 — 变量列表与状态上报

> 运行时自动计算记录的 17 个变量。`上报名` = `_build_status_dict()` 中向 AI/Redis 上报时使用的名称，`—` = 不在状态字典中暴露（但系统内部仍然使用）。
> 基类还维护若干其他内部变量（AI 拒绝记录等），智能体无需关注，也不对外暴露。

---

## 一、全部运行时变量

| 变量名 | 上报名 | 说明 |
|:--|:--|:--|
| `tick_price` | `当前行情价格` | 最近 tick 价格 |
| `tick_timestamp` | — | 最近 tick 时间戳 |
| `pos_price` | `持仓均价` | 当前持仓均价 |
| `first_open_price` | — | 初始开仓价（网格基准锚点，首笔成交时记录，后续加仓不变） |
| `current_profit_loss_radio` | `当前持仓盈亏比` | 当前持仓浮动盈亏比例 |
| `low_stop_price` | — | 当前止损/止盈价格（多头：止损+移动止盈线；空头：止盈+移动止损线） |
| `high_stop_price` | — | 当前止盈/止损价格（多头：止盈线；空头：止损线） |
| `total_v` | `历史累计盈亏` | 策略开启后累计盈亏金额 |
| `target_pos` | `持仓目标` | 下单目标持仓量 |
| `target_timestamp` | — | 下单起始时间戳 |
| `target_delay_minute` | — | 本次调仓容忍的最大延迟分钟数 |
| `target_allow_price` | — | 本次调仓允许的最大滑点价格（默认按当前价 ±0.5%） |
| `poschange` | `已有网格仓数量`（`len()`） | 当前持仓的加建仓成交历史记录 |
| `close_cooldown_until` | `平仓冷静期截止`（条件） | 平仓冷却期截止时间戳；平仓后 `close_cooldown_hours` 小时内同方向开仓被拦截（见 07 风控链第 8 步）；全平/转向时条件上报为时间字符串 |
| `close_cooldown_direction` | `上次平仓方向`（条件） | 最近一次全平/转向的方向（1=平多 / -1=平空 / 0=无），决定冷却期拦截哪个方向的开仓；全平/转向时条件上报（做多/做空/无） |
| `close_last_price` | `上次平仓价`（条件） | 最近一次全平/转向那笔的成交价；`0`=尚无记录；`on_trade` 中 pos 归零或转向时记录（持久化） |
| `param_source` | — | 参数来源治理表（参数名 → source/reason/ts），供分层复位与 AI 决策上下文使用 |

---

## 二、`_build_status_dict()` — 注入 AI 的状态信息

此方法生成当前策略状态的快照字典（供 AI 分析参考，也是状态查询上报的内容）。顶层字段：

| 状态字段 | 类型 | 说明 |
|:--|:--|:--|
| `vt_symbol` | string | 合约代码（大写） |
| `策略类型` | string | 策略类型名称 |
| `实际持仓` | number | 当前持仓（正=多，负=空，0=空仓） |
| `持仓目标` | number | 当前目标持仓量 |
| `持仓均价` | float | 加权平均持仓成本 |
| `历史累计盈亏` | float | 启动以来总盈亏金额 |
| `当前持仓盈亏比` | float | 浮动盈亏比例 |
| `当前行情价格` | float | 最新 tick 价格 |
| `已使用资金占比` | float | 持仓市值 / (启动资金+累计盈亏) |
| `已有网格仓数量` | int | 已触发网格档数 |
| `持仓交易历史` | list | 当前持仓周期的成交记录列表 |
| `策略运行中` | bool | 策略是否处于运行状态 |
| `在交易时间段内` | bool | 是否处于可交易时段（非美股恒为 True） |
| `上次平仓价` | float | 最近一次全平/转向那笔的成交价（`close_last_price`，条件字段） |
| `平仓冷静期截止` | string | `close_cooldown_until` 转时间字符串（条件字段） |
| `上次平仓方向` | string | 被平方向：做多/做空/无（`close_cooldown_direction`，条件字段） |
| `品种信息` | dict | 品种详情（品种信息表可查到时才有） |

> **条件字段说明：** `上次平仓价` / `平仓冷静期截止` / `上次平仓方向` 三者仅在发生过**全平或转向**后（`close_last_price` 非 0）才出现；且只在**全量模式**上报，订单审核状态字典（`review_mode`）中不出现。

其中嵌套的 `策略基础参数设置和当前变量` 字典（键 = 参数标准名 name_cn，与 02 文档"标准名"列一致）：

| 上报名 | 变量名 | 说明 |
|:--|:--|:--|
| `主信号K线周期(分钟数)` | `use_1m_5m_15m_30m_60m` | 策略主 K 线周期 |
| `最小交易单位` | `volume_min_unit` | 最小交易股数 |
| `杠杆(合约乘数)` | `trade_radio` | 杠杆倍数 |
| `可用资金总量` | `start_asset + total_v` | 启动资金 + 累计盈亏 |
| `建议首仓占比` | `first_part` | 首仓占用资金比例 |
| `是否允许止盈` | `enable_stop_profit` | 固定比例止盈开关 |
| `止盈幅度` | `stop_profit_radio` | 固定止盈比例 |
| `是否允许止损` | `enable_stop_loss` | 固定比例止损开关 |
| `止损幅度` | `stop_loss_radio` | 固定止损比例 |
| `是否允许移动止盈` | `enable_stop_autoprofit` | 移动止盈开关 |
| `移动止盈启动幅度` | `stop_autoprofit_start_radio` | 移动止盈触发阈值 |
| `移动止盈回撤幅度` | `stop_autoprofit_back_maxvalue` | 移动止盈回撤平仓幅度 |
| `网格K线周期` | `martin_k_time` | 网格使用的 K 线周期 |
| `网格K线CCI指标周期` | `cci_martin_period` | CCI 指标周期 |
| `是否允许网格主动开仓` | `enable_martin_add_open` | 空仓时允许自动开仓 |
| `是否允许盈利时网格加仓` | `enable_martin_add_profit` | 盈利时网格加仓开关 |
| `是否允许亏损时网格加仓` | `enable_martin_add_loss` | 亏损时网格加仓开关 |
| `网格间距` | `martin_grid_distance` | 网格加仓价格间距 |
| `网格数量` | `martin_add_count` | 剩余资金切分的格数（决定每格交易量，不是加仓次数硬上限） |
| `是否允许卖出基础底仓` | `enable_martin_sub_base` | 允许减基础底仓（仅盈利时） |
| `是否允许网格减仓` | `enable_martin_sub` | 允许减盈利网格仓（统一开关，不区分盈亏状态） |
| `网格止盈` | `martin_grid_profit` | 网格仓需盈利多少才允许减仓 |
| `亏损平仓人工审批` | `loss_close_need_manual` | 亏损平仓人工审批开关（人工作业层控制参数） |

> 说明：ATR 动态止盈止损的 4 个参数（`enable_atr_stop_loss/profit`、`atr_loss_period/multiple`）**只在订单审核状态字典**（`_build_confirm_status_dict`，AI 审核下单时可见）中上报，**分析状态字典**（`_build_status_dict`，定时分析时可见）中不上报；`martin_sub_base_part`、`allow_price_high`、`first_allow_price_min/max` 等不在任何状态字典中上报，若 AI 上报数据中缺失属正常。
> 不同策略可能向状态字典追加各自的自定义字段，智能体遇到未知字段时当作参考信息处理即可。

> **快照特性：** 输出是调用时刻的快照，不是实时流。AI 应基于快照分析，不考虑调用期间的微小价格变化。
