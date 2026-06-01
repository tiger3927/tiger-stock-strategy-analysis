# 07 — 交易量计算与下单风控

> 交易量计算公式 + `set_target_pos()` 完整风控链 + 参数联动场景速查。

---

## 一、交易量计算公式

| 方法 | 用途 | 公式 |
|:--|:--|:--|
| `get_fixed_volume()` | 首仓/主动开仓量 | `(start_asset + total_v) × first_part / tick_price` |
| `get_martin_add_volume()` | 每格加仓量 | `(start_asset + total_v) × (1 - first_part) / martin_add_count / tick_price` |
| `get_open_pos_sub_volume()` | 基础底仓减仓量 | `(start_asset + total_v) × first_part × martin_sub_part / tick_price` |
| `get_all_canbuy_volume(ratio)` | 最大可持仓量 | `(start_asset + total_v) × ratio / tick_price` |

### `volume_change_with_v` 的影响

| 设置 | 效果 |
|:--|:--|
| `True`（默认） | 资金 = `start_asset + total_v`（含累计盈亏），盈利多→量大，亏损多→量小 |
| `False` | 资金固定 = `start_asset`，盈亏不影响交易量 |

> **注意：** 若 AI 希望保持固定仓位，建议此参数设为 False。

### `max_position_ratio` 的拦截

`do_set_target_pos()` 中强制检查：若 `target_pos > get_all_canbuy_volume(max_position_ratio)`，则截断至上限。

---

## 二、`set_target_pos()` 风控链（完整流程）

```
set_target_pos(target_pos)
  ├── 1. 交易时段检查：非交易时段 → 拒绝
  ├── 2. 仓位无变化：target_pos == pos → 拒绝
  ├── 3. AI 冷却期：上次被拒的相同 target_pos 在 3 分钟内 → 拒绝
  ├── 4. 亏损平仓人工审核：loss_close_need_manual=True 且亏损且想平仓 → 拒绝
  ├── 5. 首仓价格区间：pos=0 且 enable_first_allow_prices → 检查价格是否在 [min, max]
  ├── 6. 禁止追高/追低：enable_allow_price_high → 检查当前价是否越线
  ├── 7. OpenClaw 拒绝记录：上轮被拒的同方向调仓 → 拒绝（冷却期内）
  ├── 8. AI 审核模式：enable_openclaw_confirm_target_pos=True → 提交 AI 等待审核
  └── 通过 → do_set_target_pos(target_pos)
                  └── max_position_ratio 上限拦截


do_set_target_pos()
  ├── 根据 max_position_ratio 计算最大可持仓量，截断 target_pos
  ├── 设置 target_timestamp、target_delay_minute、target_allow_price
  ├── 记录日志（含当前盈亏方向和原因）
  └── trade() 在下个循环中执行
```

> **变盘陷阱：** 第 4 步的 `loss_close_need_manual` 在趋势反转时可能成为障碍——它会拦截所有 `set_target_pos(0)` 调用（包括止损平仓、CCI减仓、子类主动平仓）。变盘防御场景下务必设为 `False`。详见 [`10_reversal_handling.md`](10_reversal_handling.md)。

---

## 三、参数联动场景速查

以下列出典型参数组合下的实际行为：

| 场景 | 开关设置 | 数值设置 | 预期行为 |
|:--|:--|:--|:--|
| **纯网格防守** | 所有 `enable_martin_*` = False | — | 持仓不变，仅止盈止损 |
| **亏损加仓 + 减盈利网格** | `enable_martin_add_loss=True`, `enable_martin_sub=True` | `grid_distance=0.03`, `grid_profit=0.03` | 每跌 3% 加一档，每涨 3% 减一档 |
| **进攻型（跌加涨也加）** | `enable_martin_add_loss=True`, `enable_martin_add_profit=True` | — | 双向加仓，仓位持续扩大 |
| **保守型（只止盈止损）** | 所有 `enable_martin_*` = False, `enable_stop_*` = True | `profit_radio=0.06`, `loss_radio=0.02` | 赚 6% 止盈，亏 2% 止损 |
| **移动止盈保护利润** | `enable_stop_autoprofit=True` | `start_radio=0.05`, `back_maxvalue=0.02` | 赚 5% 后开始保护，回撤 2% 即走 |
| **空仓等信号入场** | `enable_martin_add_open=True` | — | AI/子类设定 base_direction 后自动入场 |
| **金字塔进攻（亏损场景）** | `martin_add_pyramid=True`, `enable_martin_add_loss=True` | `pyramid_radio=0.1` | 第 1 格加 100 股，第 5 格加 150 股 |
| **一次性建仓** | `first_part=1.0`, `max_position_ratio=1.0` | — | 全仓进出，不补仓 |
