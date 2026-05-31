# 05 — 止盈止损机制

> 止盈价和止损价的生成规则及平仓触发条件。

---

## 一、运行节奏

基类在**两个频率**下运行：

```
on_tick()（每笔 tick，高速）
  ├── 更新 1m K线数据
  ├── profit_loss_stop_action()   —— 止盈止损触发检查
  └── trade()                     —— 执行未完成订单（限频每秒1次）

on_bar()（每分钟，低速）
  ├── 更新 CCI 指标和 K 线数据
  ├── profit_loss_stop_action()   —— 模拟盘路径用 bar 价检查
  ├── calc_current_profit_loss()  —— 更新当前盈亏比
  ├── martin_add_sub()            —— 网格加减仓
  ├── calc_profit_loss_stop_price() —— 计算止盈止损价格（为下分钟tick检查准备）
  └── trade()
```

**核心关系：** `calc_profit_loss_stop_price()` 是"生产者"（每分钟计算一次价格），`profit_loss_stop_action()` 是"消费者"（每 tick 用已有价格判断触发）。

---

## 二、止盈止损价生成规则

`calc_profit_loss_stop_price()` 每分钟计算 `high_stop_price` 和 `low_stop_price`：

### 做多时（pos > 0）

| 体系 | 参与条件 | 价格计算 |
|:--|:--|:--|
| 固定止盈 | `enable_stop_profit=True` | `high_stop_price = pos_price × (1 + stop_profit_radio)` |
| 固定止损 | `enable_stop_loss=True` | `low_stop_price = pos_price × (1 - stop_loss_radio)` |
| 移动止盈 | `enable_stop_autoprofit=True` 且 `current_profit_loss_radio > stop_autoprofit_start_radio` | 取回撤保护价覆盖到 low_stop_price，**永不下降** |
| ATR 止损 | `enable_atr_stop_loss=True` | `atr_stop_price = close - ATR(14) × atr_loss_multiple`（独立变量） |
| ATR 止盈 | `enable_atr_stop_profit=True` | `atr_profit_price = close + (stop_profit_ratio/stop_loss_ratio) × ATR(14) × atr_loss_multiple`（独立变量） |

**核心原则：** 所有价格都向**保守方向**更新（做多时 low_stop_price 只升不降，high_stop_price 只降不升）。

**做空时（pos < 0）：** 逻辑对称，方向相反。

---

## 三、ATR 动态止损止盈（独立系统）

ATR 系统使用**独立变量** `atr_stop_price` / `atr_profit_price`，不与 `high/low_stop_price` 共用。

| 特性 | 说明 |
|:--|:--|
| 止损幅度 | ATR(14) × `atr_loss_multiple`（默认 12 倍） |
| 止盈幅度 | 止损幅度 × (`stop_profit_radio` / `stop_loss_radio`) |
| 锚定价格 | 当前 K 线收盘价 `close`（非 `pos_price`） |
| K 线周期 | `use_1m_5m_15m_30m_60m` 指定的主周期 |

**设计原理：保持固定盈亏比。** 用户配置的 `stop_profit_radio` / `stop_loss_radio` 定义了期望盈亏比（如 9%÷3%=3:1），ATR 系统将此比例复用到动态波动率环境中。无论市场波动如何，策略风险收益特征保持一致。

**与固定/移动止盈止损的并行关系：**
- 三个系统互不干扰，**并行检查**
- 固定止损 → 基于 `pos_price` 的绝对比例，**只升不降**
- 移动止盈 → 基于最高盈利回撤，**只升不降**
- ATR → 基于波动率实时计算，**每次 bar 重新计算**，可升可降

---

## 四、平仓触发条件

`profit_loss_stop_action()` 每 tick 检查：

| 持仓 | 触发条件 | 结果 |
|:--|:--|:--|
| 做多 | `tick_price <= atr_stop_price` | ATR动态止损平仓 |
| 做多 | `tick_price >= atr_profit_price` | ATR动态止盈平仓 |
| 做多 | `tick_price >= high_stop_price` | 止盈平仓 |
| 做多 | `tick_price <= low_stop_price` 且盈亏 ≥ 0 | 移动止盈平仓 |
| 做多 | `tick_price <= low_stop_price` 且盈亏 < 0 | 止损平仓 |
| 做空 | `tick_price >= atr_stop_price` | ATR动态止损平仓 |
| 做空 | `tick_price <= atr_profit_price` | ATR动态止盈平仓 |
| 做空 | `tick_price >= high_stop_price` 且盈亏 < 0 | 止损平仓 |
| 做空 | `tick_price >= high_stop_price` 且盈亏 ≥ 0 | 移动止盈平仓 |
| 做空 | `tick_price <= low_stop_price` | 止盈平仓 |

---

## 五、移动止盈详细机制

当盈利达到 `stop_autoprofit_start_radio`（默认 5%）后触发：

- 记录从最高盈利回撤的幅度
- 回撤超过 `stop_autoprofit_back_maxvalue`（如 3%）或 `stop_autoprofit_back_radio`（如 61% 比例），触发平仓
- 关键：做多时 low_stop_price 随盈利增加而**持续上移**，永不下移
