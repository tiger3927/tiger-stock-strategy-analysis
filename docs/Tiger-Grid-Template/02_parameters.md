# 02 — 参数列表

> 全部 45 个参数定义。`上报名` = `_build_status_dict()` 中向 AI 上报时使用的中文名称，`—` = 不上报状态字典（但仍在系统内部生效）。

---

## 一、基础配置

| 参数名 | 上报名 | 类型 | 默认值 | 说明 |
|:--|:--|:--|:--:|:--|
| `init_load_days` | — | int | 5 | 策略需要历史行情的前置天数 |
| `use_1m_5m_15m_30m_60m` | `主信号K线周期（分钟数）` | int | 15 | 主K线周期（1/5/15/30/60/240/720/1440） |
| `us_stock_trading_hours_only` | — | bool | True | 是否仅在美股交易时段交易 |
| `enable_publish_status_redis` | — | bool | True | 是否发布策略状态到 Redis |

---

## 二、交易资金相关

| 参数名 | 上报名 | 类型 | 默认值 | 说明 |
|:--|:--|:--|:--:|:--|
| `start_asset` | `可用资金总量`（含 total_v） | float | 1000000 | 分配的初始资金 |
| `trade_radio` | `杠杆` | float | 1.0 | 交易杠杆（期货合约乘数/数字币杠杆） |
| `trade_fee` | — | float | 0.0002 | 交易手续费率 |
| `first_part` | `开仓动用资金比例` | float | 0.2 | 开仓使用分配资金的比例 |
| `max_position_ratio` | — | float | 1.0 | 最大允许持仓比例（相对可用总资金） |
| `volume_min_unit` | `最小交易量单位` | float | 1.0 | 最小交易单位 |
| `volume_change_with_v` | — | bool | True | 随盈亏调整每次交易量（盈利增、亏损减） |
| `order_price_add` | — | float | 0.0005 | 下单加价比例 |
| `target_delay_minute_max` | — | int | 3 | 下单最大延迟分钟数，超时取消 |

> **`first_part` 是关键杠杆参数：** 设为 1.0 即一次性建仓，不留资金补仓；设为 0.2 则首仓只用 20% 资金，其余用于网格加仓。

---

## 三、止盈止损参数

| 参数名 | 上报名 | 类型 | 默认值 | 说明 |
|:--|:--|:--|:--:|:--|
| `enable_stop_profit` | `止盈开关` | bool | True | 允许固定比例止盈 |
| `stop_profit_radio` | `止盈幅度` | float | 0.09 | 止盈比例（9%） |
| `enable_stop_loss` | `止损开关` | bool | True | 允许固定比例止损 |
| `stop_loss_radio` | `止损幅度` | float | 0.03 | 止损比例（3%） |
| `enable_atr_stop_loss` | `ATR动态止损开关` | bool | False | 用 ATR 动态计算止损价（独立系统） |
| `enable_atr_stop_profit` | `ATR动态止盈开关` | bool | False | 用 ATR 动态计算止盈价 |
| `atr_loss_period` | — | int | 14 | ATR 指标周期 |
| `atr_loss_multiple` | — | int | 12 | ATR 止损倍数 |
| `loss_close_need_manual` | — | bool | False | 亏损平仓是否需要人工审批 |
| `enable_stop_autoprofit` | `移动止盈开关` | bool | True | 允许盈利回撤移动止盈 |
| `stop_autoprofit_start_radio` | `移动止盈启动幅度` | float | 0.05 | 盈利多少启动移动止盈（5%） |
| `stop_autoprofit_back_radio` | — | float | 0.61 | 移动止盈回撤比例（盈利的61%） |
| `stop_autoprofit_back_maxvalue` | `移动止盈回撤幅度` | float | 0.03 | 移动止盈回撤最大绝对值（3%） |

---

## 四、马丁格尔网格参数

| 参数名 | 上报名 | 类型 | 默认值 | 说明 |
|:--|:--|:--|:--:|:--|
| `martin_k_time` | `马丁格尔K线周期` | int | 5 | 网格使用的 K 线周期 |
| `cci_martin_period` | `马丁格尔CCI信号周期` | int | 35 | CCI 指标周期（越小加建仓越快） |
| `enable_martin_add_open` | `允许马丁格尔CCI信号主动开仓` | bool | False | 允许马丁格尔主动开仓 |
| `enable_martin_add_profit` | `盈利时允许马丁格尔加仓（等距）` | bool | False | 允许盈利时在 CCI 信号位加仓 |
| `enable_martin_add_loss` | `亏损时允许马丁格尔加仓（等距）` | bool | False | 允许亏损时在 CCI 信号位加仓 |
| `martin_add_pyramid` | — | bool | False | 是否按金字塔比例加仓（进攻型打法） |
| `martin_add_pyramid_radio` | — | float | 0.1 | 金字塔增减加仓比例基数 |
| `martin_grid_distance` | `马丁格尔网格间距` | float | 0.03 | 亏损加仓的最小价格距离（3%） |
| `martin_add_count` | `马丁格尔网格数` | int | 10 | 可加仓次数（决定每格交易量） |
| `enable_martin_sub_profit` | — | bool | True | 允许减基础底仓（盈利时） |
| `enable_martin_sub_loss` | `持仓亏损时允许马丁格尔减盈利的网格仓` | bool | True | 允许亏损时减仓（加仓部分盈利时） |
| `martin_grid_profit` | `马丁格尔网格止盈幅度` | float | 0.03 | 网格减仓必须的最小盈利（3%） |
| `martin_sub_part` | — | float | 0.33 | 盈利仓位的减仓比例（33%） |

---

## 五、价格过滤参数

| 参数名 | 上报名 | 类型 | 默认值 | 说明 |
|:--|:--|:--|:--:|:--|
| `enable_first_allow_prices` | — | bool | False | 首仓价格区间开关 |
| `first_allow_price_min` | — | float | 0.0 | 首仓允许最低价 |
| `first_allow_price_max` | — | float | 0.0 | 首仓允许最高价 |
| `enable_allow_price_high` | — | bool | False | 禁止追高/追低开关 |
| `allow_price_high` | — | float | 0.0 | 禁止追高/追低价格红线 |

---

## 六、OpenClaw AI 参数

| 参数名 | 上报名 | 类型 | 默认值 | 说明 |
|:--|:--|:--|:--:|:--|
| `enable_openclaw_analysis` | — | bool | False | 是否启用 OpenClaw API 分析 |
| `openclaw_main_interval` | — | int | 60 | API 调用间隔（分钟） |
| `enable_openclaw_confirm_target_pos` | — | bool | False | 是否启用 AI 审核调仓下单 |

---

## 七、自定义指标参数

| 参数名 | 上报名 | 类型 | 默认值 | 说明 |
|:--|:--|:--|:--:|:--|
| `x_script` | — | str | "" | 自动指标描述符 X |
| `x_window_count` | — | int | 100 | X 指标窗口数 |
| `y_script` | — | str | "" | 预测指标描述符 Y |
| `y_window_count` | — | int | 240 | Y 指标窗口数 |

---

## 八、分隔符（UI 布局用，无实际逻辑）

| 参数名 | 上报名 |
|:--|:--|
| `seperator_to_form_2` | — |
| `seperator_to_form_3` | — |
| `seperator_to_form_4` | — |

---

## 九、参数分类速查

### 按修改权重分

| 类别 | 说明 | 示例 |
|:--|:--|:--|
| **用户固定参数** | UI 设置，AI 不直接修改 | `start_asset`, `trade_radio`, `use_1m_5m_15m_30m_60m` |
| **AI 可建议修改** | 子类可根据 AI 建议在代码中设置 | `first_part`, `max_position_ratio`, `martin_grid_distance` |
| **AI 可控制开关** | 子类根据 AI JSON 设置 `enable_*` | `enable_stop_profit`, `enable_martin_add_loss` |
| **系统自动变量** | 只读，AI 参考用（见 `03_`） | `pos_price`, `current_profit_loss_radio`, `total_v` |

### 按功能组分

| 功能组 | 核心参数 | 详参见 |
|:--|:--|:--|
| **仓位控制** | `first_part`, `max_position_ratio`, `volume_min_unit` | [`07_trade_control.md`](07_trade_control.md) |
| **止盈** | `enable_stop_profit`, `stop_profit_radio`, `enable_stop_autoprofit`, `enable_atr_stop_profit` | [`05_stop_profit_loss.md`](05_stop_profit_loss.md) |
| **止损** | `enable_stop_loss`, `stop_loss_radio`, `enable_atr_stop_loss` | [`05_stop_profit_loss.md`](05_stop_profit_loss.md) |
| **网格加仓** | `enable_martin_add_loss`, `martin_grid_distance`, `martin_add_count` | [`06_grid_martin.md`](06_grid_martin.md) |
| **网格减仓** | `enable_martin_sub_loss`, `martin_grid_profit`, `martin_sub_part` | [`06_grid_martin.md`](06_grid_martin.md) |
| **价格保护** | `enable_first_allow_prices`, `enable_allow_price_high` | [`07_trade_control.md`](07_trade_control.md) |
| **金字塔模式** | `martin_add_pyramid`, `martin_add_pyramid_radio` | [`06_grid_martin.md`](06_grid_martin.md) |
| **AI 分析** | `enable_openclaw_analysis`, `openclaw_main_interval` | [`04_ai_interface.md`](04_ai_interface.md) |
