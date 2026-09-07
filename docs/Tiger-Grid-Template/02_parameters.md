# 02 — 参数列表

> 全部 50 个参数定义（其中 24 个 AI 可控、4 个人工作业层控制、其余人工设置；另有 3 个 UI 分隔符，无实际逻辑）。
>
> **标准名**列即代码 `parameters_info.name_cn`，是 AI 读写参数时的**标准中文键名**（状态字典上报、AI 返回 JSON 均使用此列名称，请勿使用其他叫法）。
> 并非所有参数都进入状态字典上报（未上报的参数本身仍然生效），上报清单见 [`03_variables_and_state.md`](03_variables_and_state.md) 第二节。
>
> 可改性说明：
> - **AI/MCP**：AI 返回 JSON 的 `parameters` 可携带，MCP `set_param` 可设置；
> - **MCP**：仅 MCP/人工可设置，AI 分析 JSON 不携带；
> - **人工**：仅 UI 人工设置，AI 与 MCP 均不改。
>
> 注意：基类**没有** `max_position_ratio`（最大持仓比例）参数。加仓幅度由资金切分天然约束（每格量 = 剩余资金 ÷ `martin_add_count`），总持仓以全部可用资金为上限。

---

## 一、基础配置

| 参数名 | 标准名 | 类型 | 默认值 | 可改性 | 说明 |
|:--|:--|:--|:--:|:--|:--|
| `init_load_days` | `初始化所需天数` | int | 5 | 人工 | 策略启动需加载的历史行情前置天数 |
| `use_1m_5m_15m_30m_60m` | `主信号K线周期(分钟数)` | int | 15 | 人工 | 主 K 线周期（1/5/15/30/60/240/720/1440） |
| `us_stock_trading_hours_only` | `美股盘中交易` | bool | True | 人工 | 是否只在美股交易时段交易（仅对美股生效，非美股不受影响） |
| `enable_publish_status_redis` | `发布Redis状态板` | bool | True | 人工 | 是否启用 Redis 状态发布（外部系统/MCP 查询状态板的开关，仅实盘生效） |

---

## 二、方向参数

| 参数名 | 标准名 | 类型 | 默认值 | 可改性 | 说明 |
|:--|:--|:--|:--:|:--|:--|
| `trend_direction` | `趋势方向` | int | 0 | 人工 | 人或 AI 分析得出的**品种趋势**（1=看多，-1=看空，0=无趋势）；仅记录，供复盘/下次分析参考，不直接触发任何动作 |
| `base_direction` | `操作方向` | int | 0 | AI/MCP | 人或 AI 分析得出的**策略开仓方向**（1=做多，-1=做空，0=无方向）；`enable_martin_add_open=True` 且空仓时，网格按此方向主动开仓 |

---

## 三、交易资金相关

| 参数名 | 标准名 | 类型 | 默认值 | 可改性 | 说明 |
|:--|:--|:--|:--:|:--|:--|
| `start_asset` | `初始资金` | float | 1000000 | MCP | 分配的初始资金（可用于开仓的总资金量） |
| `trade_radio` | `杠杆(合约乘数)` | float | 1.0 | 人工 | 交易杠杆（期货合约乘数/数字币杠杆） |
| `trade_fee` | `交易手续费` | float | 0.0002 | 人工 | 交易手续费率 |
| `first_part` | `建议首仓占比` | float | 0.2 | AI/MCP | 首仓动用资金的比例。**关键杠杆参数**：设为 1.0 即一次性建仓，不留资金补仓；设为 0.2 则首仓 20%，其余留给网格加仓 |
| `volume_min_unit` | `最小交易单位` | float | 1.0 | 人工 | 最小交易单位（程序从交易所自动获取，币市可为小数；仅回测手工填写） |
| `volume_change_with_v` | `盈亏算入资金量` | bool | True | 人工 | 随盈亏调整每次交易量（盈利增、亏损减）；False=资金固定为 `start_asset` |
| `order_price_add` | `下单加价比例` | float | 0.0005 | 人工 | 限价单在对手价上加/减的让价比例 |
| `target_delay_minute_max` | `成交等待分钟数` | int | 3 | 人工 | 下单最大延迟分钟数，超时未成交则取消本次调仓 |
| `close_cooldown_hours` | `平仓冷却（小时）` | int | 48 | 人工 | 平仓冷静期：平仓后禁止**同方向**开新仓的时长（0=不限制，1-72）。智能体观察到"平仓后开仓未生效"时优先排查此项 |

---

## 四、止盈止损参数

| 参数名 | 标准名 | 类型 | 默认值 | 可改性 | 说明 |
|:--|:--|:--|:--:|:--|:--|
| `enable_stop_profit` | `是否允许止盈` | bool | True | AI/MCP | 固定比例止盈开关 |
| `stop_profit_radio` | `止盈幅度` | float | 0.09 | AI/MCP | 固定止盈比例（0.09=9%） |
| `enable_stop_loss` | `是否允许止损` | bool | True | AI/MCP | 固定比例止损开关 |
| `stop_loss_radio` | `止损幅度` | float | 0.03 | AI/MCP | 固定止损比例（0.03=3%） |
| `enable_atr_stop_loss` | `是否允许ATR动态止损` | bool | False | AI/MCP | 用 ATR 动态计算止损价（独立系统） |
| `enable_atr_stop_profit` | `是否允许ATR动态止盈` | bool | False | AI/MCP | 用 ATR 动态计算止盈价 |
| `atr_loss_period` | `止盈止损用ATR周期` | int | 14 | AI/MCP | 基于主周期的ATR动态止损/止盈的ATR指标周期（AI 明确给出即落地，缺省随复位回 14；范围 5-120） |
| `atr_loss_multiple` | `止盈止损用ATR倍数` | int | 12 | AI/MCP | 基于主周期的ATR动态止损/止盈的倍数（AI 明确给出即落地，缺省随复位回 12；范围 1-50） |
| `enable_stop_autoprofit` | `是否允许移动止盈` | bool | True | AI/MCP | 盈利回撤移动止盈开关 |
| `stop_autoprofit_start_radio` | `移动止盈启动幅度` | float | 0.05 | AI/MCP | 浮盈达到此比例后启动移动止盈 |
| `stop_autoprofit_back_maxvalue` | `移动止盈回撤幅度` | float | 0.02 | AI/MCP | 移动止盈允许的最大回撤绝对值比例 |

---

## 五、马丁格尔网格参数

| 参数名 | 标准名 | 类型 | 默认值 | 可改性 | 说明 |
|:--|:--|:--|:--:|:--|:--|
| `martin_k_time` | `网格K线周期` | int | 5 | 人工 | 网格捕捉加减仓/开仓信号的 K 线周期（1/5/15/30/60/240/720/1440；240=4小时、720=12小时、1440=日K，大周期仅按日级节奏判网格） |
| `cci_martin_period` | `网格K线CCI指标周期` | int | 35 | 人工 | CCI 指标周期，越小信号越多、噪音越大 |
| `enable_martin_add_open` | `是否允许网格主动开仓` | bool | False | AI/MCP | **必须打开此开关才会自动开仓**：空仓且 `base_direction≠0` 时顺方向开仓 |
| `enable_martin_add_profit` | `是否允许盈利时网格加仓` | bool | False | AI/MCP | 盈利时等距网格加仓（顺趋势） |
| `enable_martin_add_loss` | `是否允许亏损时网格加仓` | bool | False | AI/MCP | 亏损时等距网格加仓（摊低成本） |
| `martin_grid_distance` | `网格间距` | float | 0.03 | AI/MCP | 网格间距（相对首仓价相差 3% 为一格） |
| `martin_add_count` | `网格数量` | int | 10 | AI/MCP | 剩余资金切分的格数，决定每格交易量（不是加仓次数的硬上限） |
| `enable_martin_sub_base` | `是否允许卖出基础底仓` | bool | False | AI/MCP | 允许减基础底仓（仅盈利时有效） |
| `enable_martin_sub` | `是否允许网格减仓` | bool | False | AI/MCP | 允许减盈利的网格仓（统一开关，与盈亏状态无关） |
| `martin_grid_profit` | `网格止盈` | float | 0.03 | AI/MCP | 网格仓必须达到的盈利比例，达到才允许减仓 |
| `martin_sub_base_part` | `基础底仓分批出货比例` | float | 0.33 | AI/MCP | `enable_martin_sub_base=True` 时每次减基础底仓的比例 |

---

## 六、价格过滤参数

| 参数名 | 标准名 | 类型 | 默认值 | 可改性 | 说明 |
|:--|:--|:--|:--:|:--|:--|
| `enable_first_allow_prices` | `是否限制首仓价格区间` | bool | False | AI/MCP | 首仓价格限制开关（仅空仓开仓时检查） |
| `first_allow_price_min` | `首仓最低价` | float | 0.0 | AI/MCP | 首仓价格区间下限（AI 当轮分析基于支撑/压力推导，MCP/人工可覆盖） |
| `first_allow_price_max` | `首仓最高价` | float | 0.0 | AI/MCP | 首仓价格区间上限（AI 当轮分析基于支撑/压力推导，MCP/人工可覆盖） |
| `enable_allow_price_high` | `是否禁止追高` | bool | False | AI/MCP | 禁止追高/追低开关（仅持仓时加仓有效，见下方说明） |
| `allow_price_high` | `禁止追高价格红线` | float | 0.0 | AI/MCP | 禁止追高/追低价格红线（AI 当轮分析基于 MA50/RR 推导，MCP/人工可覆盖） |

> **首仓价格区间：按开仓方向单向检查（原理）**
> 代码不要求同时满足上下限，而是按方向各查一边：
> - **开多仓**（目标 > 0）：只检查当前价 ≤ `first_allow_price_max`，即"超过此价不追买"；下限不参与判断；
> - **开空仓**（目标 < 0）：只检查当前价 ≥ `first_allow_price_min`，即"低于此价不追卖"；上限不参与判断。
>
> 因此实际使用时只需按方向设置单边：做多时把 `first_allow_price_max` 设为"不高于某价才买"的红线；做空时把 `first_allow_price_min` 设为"不低于某价才卖"的红线。

> **禁止追高/追低：只在持仓时有效（原理）**
> `enable_allow_price_high` 只约束**持仓状态下的加仓**，减仓/平仓不受拦截，空仓开仓也不触发：
> - 持多仓且当前价 ≥ 红线 → 拒绝加仓；
> - 持空仓且当前价 ≤ 红线 → 拒绝加仓；
> - 空仓（pos=0）时本检查不生效，首仓开仓的价格保护请用首仓价格区间或策略内置逻辑。

---

## 七、人工作业层控制参数（AI 不输出；MCP 未经用户明确要求不得修改）

> 以下 4 个参数都是**人（策略部署者）的决策**，控制"智能体如何参与这个策略"。
> AI 分析 JSON **不携带**这 4 个参数（AI 不会输出它们）；
> 其中 `启用AI策略分析` / `AI定时分析周期` / `AI审核后下单` 3 个**开了 MCP 权限**（外部智能体技术上可设，例如用户要求时临时关闭 AI 分析），
> `亏损平仓人工审批` 为纯人工参数，MCP 不可设置。
>
> **纪律：没有用户的明确要求，AI 与 MCP 都不得修改这 4 个参数。**
> 智能体只需知道它们的存在与效果——观察到"调仓未生效 / 分析不触发 / 下单无审核"等现象时，应从这些开关找原因，
> 但只能**报告**给用户，不应主动建议或代为修改。

| 参数名 | 标准名 | 类型 | 默认值 | 人工控制的内容 |
|:--|:--|:--|:--:|:--|
| `loss_close_need_manual` | `亏损平仓人工审批` | bool | False | **拦截平仓行为**：人工设置为 True 后，亏损状态下一切平仓路径被拦截（止损平仓/CCI 减仓/信号平仓全部不生效，仅人工操作可平仓）——刻意的"抗巨大亏损"选择 |
| `enable_openclaw_analysis` | `启用AI策略分析` | bool | False | **启动智能体分析**：True 时策略定时请求智能体分析并落地其参数 |
| `openclaw_main_interval` | `AI定时分析周期` | int | 60 | **智能体分析定时间隔**：间隔多少分钟触发一次定时分析（0 或负数=关闭） |
| `enable_openclaw_confirm_target_pos` | `AI审核后下单` | bool | False | **启动智能体审核交易订单**：True 时每笔调仓先提交智能体审核，同意才执行；被拒的相同调仓在 5 分钟冷却期内被忽略 |

---

## 八、自定义指标参数

| 参数名 | 标准名 | 类型 | 默认值 | 可改性 | 说明 |
|:--|:--|:--|:--:|:--|:--|
| `x_script` | `AI指标-周期-参1_参2` | str | "" | 人工 | X 轴指标脚本（自定义指标描述符） |
| `x_window_count` | `AI计算窗口宽度` | int | 100 | 人工 | X 指标窗口周期数 |
| `y_script` | `预测类型,多个` | str | "" | 人工 | Y 轴预测指标描述符 |
| `y_window_count` | `结果持续分钟数` | int | 240 | 人工 | Y 指标窗口周期数 |

---

## 九、分隔符（UI 布局用，无实际逻辑）

| 参数名 |
|:--|
| `seperator_to_form_2` |
| `seperator_to_form_3` |
| `seperator_to_form_4` |

---

## 十、参数分类速查

### 按功能组分

| 功能组 | 核心参数 | 详参见 |
|:--|:--|:--|
| **方向** | `base_direction`, `trend_direction` | 本文二 |
| **仓位控制** | `first_part`, `volume_min_unit`, `target_delay_minute_max`, `close_cooldown_hours` | [`07_trade_control.md`](07_trade_control.md) |
| **止盈** | `enable_stop_profit`, `stop_profit_radio`, `enable_stop_autoprofit`, `enable_atr_stop_profit` | [`05_stop_profit_loss.md`](05_stop_profit_loss.md) |
| **止损** | `enable_stop_loss`, `stop_loss_radio`, `enable_atr_stop_loss` | [`05_stop_profit_loss.md`](05_stop_profit_loss.md) |
| **网格加仓** | `enable_martin_add_loss`, `enable_martin_add_profit`, `martin_grid_distance`, `martin_add_count` | [`06_grid_martin.md`](06_grid_martin.md) |
| **网格减仓** | `enable_martin_sub_base`, `enable_martin_sub`, `martin_grid_profit`, `martin_sub_base_part` | [`06_grid_martin.md`](06_grid_martin.md) |
| **网格开仓** | `enable_martin_add_open`, `base_direction` | [`06_grid_martin.md`](06_grid_martin.md) |
| **价格保护** | `enable_first_allow_prices`, `enable_allow_price_high` | [`07_trade_control.md`](07_trade_control.md) |
| **人工作业层控制** | `loss_close_need_manual`, `enable_openclaw_analysis`, `openclaw_main_interval`, `enable_openclaw_confirm_target_pos` | 本文七 |

### 给智能体的要点

- AI 返回 JSON 的 `parameters` 中只能填**可改性含 AI** 的参数，键名必须用"标准名"列；
- MCP 可额外设置**可改性含 MCP** 的参数（含 `base_direction`、`start_asset`）；
- `trend_direction` 只记录、不产生动作；真正驱动网格主动开仓的是 `base_direction`；
- **人工作业层控制参数（见七）智能体不可能设置**：它们是人部署策略时的决策，智能体只能理解其效果、不能建议修改。
