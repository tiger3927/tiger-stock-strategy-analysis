# 06 — 网格加减仓机制

> 马丁格尔网格的四段独立逻辑：减仓 → 亏损加仓 → 盈利加仓 → 主动开仓。
> 每根 bar 调用一次 `martin_add_sub()`，按顺序执行。

---

## 一、统一前置检查

所有加减仓动作前有一道闸门：

```
if self.target_pos == self.pos:
    # 无主方向订单在执行中，才执行加减仓
    self.set_target_pos(...)
```

即：如果 `set_target_pos()` 刚被调用、订单尚未成交（`target_pos ≠ pos`），网格加减仓**暂停**。

---

## 二、第一段：减仓（盈利网格仓 or 基础底仓）

**触发前提：** CCI 方向与持仓方向**相反**（`direction × pos < 0`）

> **变盘场景实战：** 此段是基类应对"持仓方向与大盘趋势相反"的**核心自动防御机制**。详细策略、参数调整和场景示例见 [`10_reversal_handling.md`](10_reversal_handling.md)。

```
├─ 尝试减基础底仓
│   条件：enable_martin_sub_profit=True 且 盈亏比 > martin_grid_profit
│   量：get_open_pos_sub_volume() = 资金 × first_part × martin_sub_part
│
└─ 如果基础底仓不能减，尝试减网格仓
    条件：有 poschange（网格成交记录）
          且 该网格仓的盈利 > martin_grid_profit
          且（若当前亏损，需要 enable_martin_sub_loss=True 才允许减）
    量：累加所有达标的网格仓量
```

**关键参数：**

| 参数 | 控制什么 |
|:--|:--|
| `enable_martin_sub_profit` | 盈利时能否卖基础底仓 |
| `enable_martin_sub_loss` | 亏损时能否减网格仓（减盈利的网格仓来降低仓位） |
| `martin_sub_part` | 每次减仓比例（0.33 = 每次减 1/3 基础仓） |
| `martin_grid_profit` | 网格仓需盈利多少才允许减（0.03 = 3%） |

> **注意：** 新建立的网格仓不会被立即卖出——必须等该网格仓盈利超过 `martin_grid_profit` 后才允许减仓。

---

## 三、第二段：亏损加仓

**触发前提：** `enable_martin_add_loss=True` 且当前**亏损**状态且持仓 ≠ 0

**加仓逻辑：**
```
从 poschange 中取最极端价格（做多取最低成交价，做空取最高成交价）
用 first_open_price × martin_grid_distance 作为网格间距
计算下一格触发价格：
  做多：tick_price < 当前极端价下方第 1 格
  做空：tick_price > 当前极端价上方第 1 格
如果价格到达下一格 → 触发加仓
```

**关键参数：**

| 参数 | 效果 |
|:--|:--|
| `enable_martin_add_loss` | 亏损时自动加仓（开 = 跌破网格线自动补仓） |
| `martin_grid_distance` | 网格间距（0.03 = 每 3% 设一档） |
| `martin_add_count` | 最大网格数（10 = 最多加 10 次） |
| `first_open_price` | 网格基准价（首次开仓价，后续不变） |

**等份额模式（`martin_add_pyramid=False`）：** 每格加仓量 = `get_martin_add_volume()` = 剩余资金 / `martin_add_count`

**金字塔模式（`martin_add_pyramid=True`）：** 根据盈亏方向自动调整加仓量：
- **亏损时**：加仓量 = `get_martin_add_volume()` × `(1 + martin_add_pyramid_radio × 已触发格数)`，越跌加越多
- **盈利时**：加仓量 = `get_martin_add_volume()` × `(1 - martin_add_pyramid_radio × 已触发格数)`，越涨加仓量越少（经典金字塔）

---

## 四、第三段：盈利加仓

**触发前提：** `enable_martin_add_profit=True` 且当前**盈利**状态且持仓 ≠ 0

逻辑与亏损加仓对称，但向反方向。盈利时价格继续同向突破时加仓（追趋势）。

**关键参数：** `enable_martin_add_profit` — 盈利时顺着趋势加仓（开 = 涨了还敢加）。

---

## 五、第四段：主动开仓（空仓时）

**触发前提：** `enable_martin_add_open=True` 且持仓 = 0 且 `base_direction ≠ 0`

顺着 `base_direction` 方向开仓，量为 `get_fixed_volume()`。

> **注意：** `base_direction` 由子类的信号或 AI 结果设定。若 `base_direction = 0`，此段不执行。

**关键参数：** `enable_martin_add_open` — 空仓时自动开仓（开 = AI 设定方向后自动入场）。

---

## 六、各段关系总结

```
martin_add_sub() 每 bar 一次，按顺序：
  ┌─────────────────────────────────────┐
  │ 前置闸门: target_pos == pos?         │
  │   No → 跳过全部加减仓               │
  ├─────────────────────────────────────┤
  │ ① 减仓：CCI 反向 → 减基础/减网格   │
  │ ② 亏损加仓：价破网格线 → 加一档    │
  │ ③ 盈利加仓：价破网格线 → 加一档    │
  │ ④ 主动开仓：空仓且base_direction≠0│
  └─────────────────────────────────────┘
```

- 四段**顺序执行**，同一 bar 可能先触发减仓再触发加仓
- 但新建网格仓有 `martin_grid_profit` 保护，不会被立即减掉
- AI 设置的 target_pos 通过前置闸门暂停所有网格操作
