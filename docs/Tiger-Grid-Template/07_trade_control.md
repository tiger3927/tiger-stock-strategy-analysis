# 07 — 交易量计算与下单风控

> 交易量计算公式 + `set_target_pos()` 完整风控链 + 参数联动场景速查。

---

## 一、交易量计算公式

| 方法 | 用途 | 公式 |
|:--|:--|:--|
| `get_fixed_volume()` | 首仓/主动开仓量 | `(start_asset + total_v) × first_part / tick_price` |
| `get_martin_add_volume()` | 每格加仓量 | `(start_asset + total_v) × (1 - first_part) / martin_add_count / tick_price` |
| `get_open_pos_sub_volume()` | 基础底仓减仓量 | `(start_asset + total_v) × first_part × martin_sub_base_part / tick_price` |
| `get_all_canbuy_volume(ratio)` | 最大可持仓量 | `(start_asset + total_v) × ratio / tick_price` |

### `volume_change_with_v` 的影响

| 设置 | 效果 |
|:--|:--|
| `True`（默认） | 资金 = `start_asset + total_v`（含累计盈亏），盈利多→量大，亏损多→量小 |
| `False` | 资金固定 = `start_asset`，盈亏不影响交易量 |

> **注意：** 若 AI 希望保持固定仓位，建议此参数设为 False。

---

## 二、`set_target_pos()` 风控链（完整流程）

```
set_target_pos(target_pos, ..., manual=False)
  ├── 1. 交易时段检查：策略未启动 / 美股非交易时段（美股盘中交易=True） → 拒绝
  ├── 2. 仓位无变化：target_pos == pos → 跳过
  ├── 3. 目标去重：target_pos == 当前 target_pos → 跳过（重复提交相同目标无效）
  ├── 4. 近期 pending 去重：180 秒内上一次 pending 目标与本次相同 → 跳过
  ├── 5. 人工作业层介入：loss_close_need_manual=True（人工设置）且亏损且 target_pos=0 → 拒绝
  │      （人工作业层控制参数，智能体不可设置；manual=True 人工操作不受此步约束）
  ├── 6. 首仓价格区间（仅 pos=0，按方向单向检查）：
  │      开多：价格 > 首仓最高价 → 拒绝；开空：价格 < 首仓最低价 → 拒绝
  ├── 7. 禁止追高/追低（仅持仓时有效）：
  │      持多仓且价格 ≥ 红线 → 拒绝加仓；持空仓且价格 ≤ 红线 → 拒绝加仓
  │      （减仓/平仓不拦截；空仓开仓不检查）
  ├── 8. 平仓冷却期 ⚠️：close_cooldown_hours>0（默认 48）且空仓且开新仓
  │      且距上次平仓未超冷却期且与上次平仓同方向 → 拒绝（反方向开仓不拦截）
  ├── 9. AI 拒绝冷却：相同调仓（同持仓→同目标）5 分钟内被 AI 审核拒绝过 → 忽略
  ├── 10. AI 审核：enable_openclaw_confirm_target_pos=True（且 manual=False）
  │      → 提交 AI 审核，同意后执行 do_set_target_pos()；拒绝则进入第 9 步冷却
  └── 通过 → do_set_target_pos()
              记录 target_pos、起始时间、允许滑点（默认按当前价 ±0.5%）
              trade() 在下个 tick/bar 中实际下单（超时/超滑点则放弃调仓）
```

> **`manual=True`：** 人工操作入口，绕过第 5 步人工作业层介入（`loss_close_need_manual`）和第 10 步 AI 审核。
>
> **⚠️ 平仓冷却期（第 8 步）是"调仓未生效"最常见的隐藏原因：** 平仓后 `close_cooldown_hours`（默认 48 小时）内禁止**同方向**开新仓。智能体若发现设置开仓目标后未下单、且策略最近刚平过仓，优先排查此项（人工参数，智能体不可改；需缩短/关闭时由人工设为 0）。
>
> **变盘陷阱：** 第 5 步的人工作业层介入（`loss_close_need_manual=True`，人工设置）在趋势反转时可能成为障碍——开启时会拦截所有 `set_target_pos(0)` 调用（包括止损平仓、信号平仓）。变盘防御场景下需**人工**将其关闭，智能体无法设置也不应绕过。详见 [`10_reversal_handling.md`](10_reversal_handling.md)。

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
| **空仓等信号入场** | `enable_martin_add_open=True` | — | 设定 `base_direction` 后自动入场 |
| **一次性建仓** | `first_part=1.0` | — | 全仓进出，不补仓（网格加仓量自然为 0） |
