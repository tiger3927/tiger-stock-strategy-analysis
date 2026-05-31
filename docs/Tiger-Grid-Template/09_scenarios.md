# 09 — 典型场景参数配置

> 每个场景提供完整的 AI 返回 JSON 参考。子类可根据自己的 JSON 协议调整字段名和结构。
> ⚠️ `target_pos` 的具体数值需根据实际资金、价格计算，以下仅为示例。

---

## 场景 1：趋势做多（EMA 金叉 / 均线多头排列）

**判断条件：** 价格在均线上方、EMA 金叉、MACD 多头、CCI > 100

**策略思路：** 顺着趋势开仓，用 ATR 动态止损止盈（保持固定盈亏比），不网格加仓。

```json
{
  "direction": "多",
  "base_direction": 1,
  "target_pos": 100,
  "reason": "30分钟线EMA金叉，价格突破60均线，MACD零轴上方金叉，趋势转多头",
  "market_judgment": "上升趋势",
  "parameters": {
    "enable_stop_profit": false,
    "enable_stop_loss": false,
    "enable_atr_stop_loss": true,
    "enable_atr_stop_profit": true,
    "enable_stop_autoprofit": false,
    "atr_loss_multiple": 12,
    "first_part": 0.2,
    "max_position_ratio": 1.0,
    "enable_martin_add_loss": false,
    "enable_martin_add_profit": false,
    "enable_martin_sub_profit": false,
    "enable_martin_sub_loss": false
  }
}
```

**为什么这样设：**
- 趋势行情不用固定止盈（容易卖飞），交给 ATR 动态管理
- ATR 止损 = 波动率 × 12，止盈 = 止损 × (9%/3%) = 3 倍止损，保持 3:1 盈亏比
- 不开网格加减仓，趋势中不补仓不落袋

---

## 场景 2：震荡低吸高抛（布林带收窄 / CCI 反复穿越）

**判断条件：** 布林带平行、CCI 在 ±100 间反复、价格在区间内无明显趋势

**策略思路：** 开启网格低吸高抛，同时保留固定止损防止假突破。

```json
{
  "direction": "低吸高抛（震荡）",
  "base_direction": 1,
  "target_pos": 100,
  "reason": "30分钟布林带缩口走平，CCI在±100之间反复，无明确趋势，开启网格模式",
  "market_judgment": "震荡",
  "parameters": {
    "enable_stop_profit": false,
    "enable_stop_loss": true,
    "stop_loss_radio": 0.04,
    "enable_atr_stop_loss": false,
    "enable_atr_stop_profit": false,
    "enable_stop_autoprofit": false,
    "enable_martin_add_loss": true,
    "enable_martin_add_profit": false,
    "enable_martin_sub_profit": true,
    "enable_martin_sub_loss": true,
    "martin_grid_distance": 0.025,
    "martin_add_count": 6,
    "martin_grid_profit": 0.02,
    "martin_sub_part": 0.33,
    "first_part": 0.15,
    "max_position_ratio": 0.75,
    "enable_first_allow_prices": true,
    "first_allow_price_min": 150.0,
    "first_allow_price_max": 155.0,
    "enable_allow_price_high": true,
    "allow_price_high": 160.0
  }
}
```

**为什么这样设：**
- 震荡中每跌 2.5% 加一档（最多 6 档），反弹 2% 就卖一档，赚取网格差价
- 首仓 15% 资金，留 85% 作网格弹药
- 止损 4%：防震荡变单边下跌
- 价格保护限制首仓区间，防止高位接盘
- `max_position_ratio=75%`：留 25% 应急

---

## 场景 3：空仓观察（市场不明朗）

**判断条件：** 无明确信号、成交量萎缩、重要事件前静默

```json
{
  "direction": "中性",
  "base_direction": 0,
  "target_pos": 0,
  "reason": "市场无明确方向，成交量萎缩，建议空仓观察",
  "market_judgment": "不确定",
  "parameters": {
    "enable_stop_profit": false,
    "enable_stop_loss": false,
    "enable_atr_stop_loss": false,
    "enable_atr_stop_profit": false,
    "enable_stop_autoprofit": false,
    "enable_martin_add_loss": false,
    "enable_martin_add_profit": false,
    "enable_martin_sub_profit": false,
    "enable_martin_sub_loss": false,
    "enable_martin_add_open": false
  }
}
```

> 或者 AI 直接不做任何操作，不调用 `set_target_pos()`。子类在 `on_openclaw_analysis_result()` 中判断 `target_pos == 0` 时不操作。

---

## 场景 4：激进金字塔进攻（突破前高 / 放量加速）

**判断条件：** 价格突破关键阻力位、放量、趋势加速

**策略思路：** 金字塔加仓（越涨越加、越加越多），移动止盈快速保护利润。

```json
{
  "direction": "多",
  "base_direction": 1,
  "target_pos": 100,
  "reason": "价格突破前高，成交量放大2倍，趋势加速，金字塔模式加仓",
  "market_judgment": "上升趋势（加速）",
  "parameters": {
    "enable_stop_profit": false,
    "enable_stop_loss": false,
    "enable_atr_stop_loss": false,
    "enable_atr_stop_profit": false,
    "enable_stop_autoprofit": true,
    "stop_autoprofit_start_radio": 0.05,
    "stop_autoprofit_back_maxvalue": 0.02,
    "enable_martin_add_profit": true,
    "enable_martin_add_loss": false,
    "martin_grid_distance": 0.025,
    "martin_add_count": 8,
    "martin_add_pyramid": true,
    "martin_add_pyramid_radio": 0.12,
    "enable_martin_sub_profit": false,
    "enable_martin_sub_loss": false,
    "first_part": 0.2,
    "max_position_ratio": 1.0
  }
}
```

**为什么这样设：**
- 金字塔模式（盈利场景）：第 1 格加基准量 × (1-0.12×1)=88%，第 5 格加基准量 × (1-0.12×5)=40%（越涨加仓量越少）
- 移动止盈：赚 5% 后启动保护，回撤 2% 就平仓
- 盈利加仓：顺趋势追击，追涨不恐高

---

## 场景 5：保守防守（已有浮盈 / 市场不确定性增加）

**判断条件：** 持有利润头寸、关键阻力位、重要数据公布前

**策略思路：** 关闭所有网格加仓，只保留止盈止损锁定利润。

```json
{
  "direction": "不操作（持有）",
  "base_direction": 1,
  "target_pos": 100,
  "reason": "当前位置已触关键阻力，市场不确定性高，转为防守模式保护利润",
  "market_judgment": "不确定（防守）",
  "parameters": {
    "enable_stop_profit": true,
    "stop_profit_radio": 0.06,
    "enable_stop_loss": true,
    "stop_loss_radio": 0.02,
    "enable_atr_stop_loss": false,
    "enable_atr_stop_profit": false,
    "enable_stop_autoprofit": true,
    "stop_autoprofit_start_radio": 0.03,
    "stop_autoprofit_back_maxvalue": 0.015,
    "enable_martin_add_loss": false,
    "enable_martin_add_profit": false,
    "enable_martin_sub_profit": false,
    "enable_martin_sub_loss": false,
    "first_part": 0.2,
    "max_position_ratio": 1.0
  }
}
```

**为什么这样设：**
- 关闭所有网格：不给市场再去冒险加仓的机会
- 开启三套止盈止损：固定止盈 6% + 固定止损 2% + 移动止盈（回撤 1.5% 即走）
- 移动止盈启动线降到 3%（之前已盈利，现在更敏感保护）

---

## 场景 6：一次性建仓（不补仓模式）

**判断条件：** 用户偏好一次性进出、不想用网格加仓

**策略思路：** `first_part=1.0` 全仓进出，只用止盈止损控制风险。

```json
{
  "direction": "多",
  "base_direction": 1,
  "target_pos": 100,
  "reason": "用户设置一次性建仓模式，全仓进出不做网格补仓",
  "market_judgment": "上升趋势",
  "parameters": {
    "enable_stop_profit": true,
    "stop_profit_radio": 0.09,
    "enable_stop_loss": true,
    "stop_loss_radio": 0.03,
    "enable_atr_stop_loss": false,
    "enable_atr_stop_profit": false,
    "enable_stop_autoprofit": true,
    "stop_autoprofit_start_radio": 0.05,
    "stop_autoprofit_back_maxvalue": 0.02,
    "enable_martin_add_loss": false,
    "enable_martin_add_profit": false,
    "enable_martin_sub_profit": false,
    "enable_martin_sub_loss": false,
    "first_part": 1.0,
    "max_position_ratio": 1.0
  }
}
```

**为什么这样设：**
- `first_part=1.0` → 全部资金用于首仓，`(1 - first_part) = 0`，网格加仓量为 0
- `max_position_ratio=1.0` → 允许满仓
- 只用止盈止损控制风险，回撤到阈值自动平仓

---

## 场景对照表

| 场景 | 止盈止损 | 网格 | 仓位 | 适用行情 |
|:--|:--|:--|:--|:--|
| 趋势做多 | ATR 动态 | 关闭 | `first_part=0.2` | 均线多头、EMA 金叉 |
| 震荡低吸高抛 | 仅止损 4% | 开启加减 | `first_part=0.15` | 布林带收窄、CCI 振荡 |
| 空仓观察 | 关闭 | 关闭 | `target_pos=0` | 无信号、事件前 |
| 金字塔进攻 | 移动止盈 | 金字塔盈利加 | `first_part=0.2` | 突破、放量、加速 |
| 保守防守 | 三套全开 | 关闭 | 不变 | 已有利润、不确定性高 |
| 一次性建仓 | 三套全开 | 关闭 | `first_part=1.0` | 任意（用户偏好） |

> **变盘场景（趋势反转、震荡破位）：** 以上场景的参数配置是针对"趋势成立"的前提。若已持有头寸且趋势发生反转，需切换到变盘防御模式，参见 [`10_reversal_handling.md`](10_reversal_handling.md)。
