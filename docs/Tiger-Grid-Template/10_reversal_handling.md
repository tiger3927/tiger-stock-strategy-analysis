# 10 — 变盘逆势：如何应对持仓方向与大盘趋势相反

> 当持仓方向与大盘/个股趋势出现背离时，基类多层防御机制如何配合、参数如何调整。
> 对照代码：`strategies/tiger_grid_template.py` 中 `martin_add_sub()` / `profit_loss_stop_action()` / `set_target_pos()`。

---

## 一、问题定义

| 场景 | 描述 | 典型触发 |
|:--|:--|:--|
| **趋势反转** | 原定上升趋势，突然转跌 | 做多持仓但大盘跳空低开、重要利空、均线死叉 |
| **假突破回归** | 突破信号被证伪，价格跌回区间 | 做多突破后迅速回落，CCI 从 +150 快速跌到 0 以下 |
| **震荡中被动亏损** | 网格模式下价格脱离震荡区间 | 做多摊了 5 格网格后，趋势转为单边下跌 |
| **方向由多转空反手** | 确认趋势反转，应立即反手做空 | 多转空信号，平多单 + 反手开空 |

---

## 二、基类已内置的 5 层防御（含触发速度和覆盖场景）

```
变盘发生
  │
  ├── [T+0, 信号层] 策略主信号检测（最快）
  │     均线交叉、ADX衰减、ATR突变、EMA下穿 → 直接 set_target_pos(0)
  │     ⚠️ 亏损时，平仓可能被"人工作业层介入"拦截（人工设置 loss_close_need_manual=True，智能体不处理）
  │
  ├── [T+0~数分钟, tick 级] profit_loss_stop_action()
  │     ATR 动态止损 / 固定止损 / 移动止盈 / ATR 动态止盈
  │     注：必须亏到阈值才触发，是最后防线
  │
  ├── [T+1分钟, bar 级] martin_add_sub() 第①段：CCI方向减仓
  │     CCI 从 +100 以上跌破 100（或 -100 以下升破 -100）→ direction × pos < 0
  │     → 减基础底仓 / 减网格仓
  │
  ├── [T+数分钟, bar 级] AI 分析周期（若 enable_openclaw_analysis=True）
  │     AI 感知方向背离 → 返回 target_pos=0 或反向
  │
  └── [T+数分钟, bar 级] 策略代码定期检查
        自定义指标值变化 → set_target_pos()
```

**关键认识：**

- **最快**是策略自身信号，不需要等 CCI 穿越
- **CCI 减仓是"自动化的主动防御"**，不需要 AI，适合懒人挂机
- **止损是最后的被动防线**，不是进攻手段
- **人工作业层介入**（`loss_close_need_manual`，人工设置）可能切断亏损状态下的全部平仓路径，智能体无法设置或绕过

---

## 三、变盘减仓的 CCI 机制深度解析

### CCI 方向判断原理

`get_martin_chance()` 调用 `cci_100_back()`（[tiger_Indicators.py](file:///d:/Code/Python/Trading/vnpy_test/tools/tiger_Indicators.py#L7-L25)），在最近 `target_delay_minute_max`（默认 3）根 bar 内，从最新往回扫 CCI ±100 穿越信号：

```
若 CCI[i-1] >= 100 且 CCI[i] < 100   → direction = -1（CCI向下穿越100）
若 CCI[i-1] <= -100 且 CCI[i] > -100 → direction = 1（CCI向上穿越-100）
扫不到穿越信号 → direction = 0（不触发减仓）
```

注意：CCI 指标周期是 `cci_martin_period`（默认 35），在网格 K 线周期（`martin_k_time`）上计算；上面的扫描窗口是 `target_delay_minute_max` 根 bar，两者不是同一个参数。

然后在 `martin_add_sub()` 中（[src](file:///d:/Code/Python/Trading/vnpy_test/strategies/tiger_grid_template.py#L4444-L4604)）：

```
if direction × pos < 0:      ← CCI方向与持仓方向相反
    ├─ 减基础底仓：需 enable_martin_sub_base=True 且 盈亏比 > martin_grid_profit
    └─ 减网格仓：需 enable_martin_sub=True 且 该网格仓盈利 > martin_grid_profit
```

### CCI 减仓的盲区

| 盲区 | 原因 | 后果 |
|:--|:--|:--|
| **CCI 滞后性** | CCI 需要跌穿 100 线才发出方向信号，此时价格可能已跌了不止3% | 减仓触发时亏损可能已超过止损线 |
| **微利时无法减基础底仓** | 减基础底仓要求 `盈利 > martin_grid_profit`（默认3%） | 若开仓后微利1%~2%即反转，CCI方向已反但基础底仓不减 |
| **网格减仓被全局关闭** | `enable_martin_sub=False` 时，所有网格减仓被禁止（无论盈亏） | 仓位无法通过 CCI 减仓主动降低，只能硬扛到止损 |
| **人工作业层介入** | 所有平仓/减仓动作最终调用 `set_target_pos()`，`loss_close_need_manual=True`（人工设置）时亏损平仓被拦截（智能体不可设置/绕过） | 只能由人工将其关闭或人工处理 |

---

## 四、参数调整策略

### 心态决定打法

| 你的态度 | 关键设置 | 效果 |
|:--|:--|:--|
| **宁可少赚也要保本** | `enable_martin_sub=True`（并确保 `loss_close_need_manual=False`，人工设置） | CCI反转时果断减仓，不犹豫 |
| **网格定投、相信反转会回来** | `enable_martin_sub=False`（人工可将 `loss_close_need_manual` 设为 True，扛住亏损不自动平） | CCI反转不卖（被动等待止损或反弹） |
| **让止损决定一切** | 仅 `enable_stop_loss=True`，其他靠策略信号处理 | 简单粗暴，适合信号很准的策略 |

### 核心场景 1：趋势追踪变盘（做多 → 趋势转空，立即平仓）

**参数策略：完全信任策略信号，CCI减仓当补充**

```json
{
  "direction": "空",
  "base_direction": -1,
  "target_pos": 0,
  "reason": "EMA死叉 + CCI从+150急跌至-50，趋势确认反转，平多单观望",
  "market_judgment": "下跌趋势（反转）",
  "parameters": {
    "enable_stop_profit": true,
    "stop_profit_radio": 0.09,
    "enable_stop_loss": true,
    "stop_loss_radio": 0.03,
    "enable_atr_stop_loss": false,
    "enable_atr_stop_profit": false,
    "enable_stop_autoprofit": false,
    "enable_martin_add_loss": false,
    "enable_martin_add_profit": false,
    "enable_martin_sub_base": true,
    "enable_martin_sub": true,
    "martin_grid_profit": 0.01,
    "martin_sub_part": 1.0,
    "enable_martin_add_open": false,
    "first_part": 0.2
  }
}
```

**为什么这样设：**

- **前置确认**：`loss_close_need_manual` 需为 False（人工作业层介入关闭；人工设置，智能体无法修改），否则亏损平仓会被拦截
- `martin_sub_part=1.0`：CCI确认反向后一次全部减仓（不保留底仓）
- `martin_grid_profit=0.01`：降低基础底仓减仓门槛，微利也减
- `enable_martin_sub=True`：允许减网格仓（防止越陷越深）
- `enable_martin_add_loss=False`：趋势已转，不再抄底
- `stop_loss_radio=0.03`：作为兜底，万一 CCI 没触发也有最后防线

### 核心场景 2：震荡 → 单边下跌（持有网格仓位，越跌越深）

**参数策略：关闭亏损加仓 + 允许减网格仓**

```json
{
  "direction": "空",
  "base_direction": -1,
  "target_pos": 0,
  "reason": "震荡区间下沿放量跌破，网格模式下5档持仓已全部被套，建议平仓离场",
  "market_judgment": "下跌趋势（震荡破位）",
  "parameters": {
    "enable_stop_profit": false,
    "enable_stop_loss": true,
    "stop_loss_radio": 0.05,
    "enable_atr_stop_loss": false,
    "enable_atr_stop_profit": false,
    "enable_stop_autoprofit": false,
    "enable_martin_add_loss": false,
    "enable_martin_add_profit": false,
    "enable_martin_sub_base": true,
    "enable_martin_sub": true,
    "martin_grid_profit": 0.00,
    "martin_sub_part": 1.0,
    "enable_martin_add_open": false,
    "first_part": 0.2
  }
}
```

**为什么这样设：**

- `enable_martin_add_loss=False`：**核心**——停止亏损加仓，不再摊平
- `martin_grid_profit=0.00`：网格仓无条件减仓（不等盈利，不抱幻想）
- `martin_sub_part=1.0`：一次全部减仓
- `enable_martin_sub=True`：允许减网格仓
- `stop_loss_radio=0.05`：放宽止损线（因为已持有网格仓位，成本较高），作为保底

### 核心场景 3：怀疑反转但不确定（减仓观望，不全平）

**参数策略：减仓 + 缩仓**

```json
{
  "direction": "中性",
  "base_direction": 0,
  "target_pos": 20,
  "reason": "MACD顶背离，但均线仍多头排列，疑为短期回调，减仓至20%观察",
  "market_judgment": "不确定（疑似顶背离）",
  "parameters": {
    "enable_stop_profit": false,
    "enable_stop_loss": true,
    "stop_loss_radio": 0.03,
    "enable_stop_autoprofit": true,
    "stop_autoprofit_start_radio": 0.03,
    "stop_autoprofit_back_maxvalue": 0.01,
    "enable_martin_add_loss": false,
    "enable_martin_add_profit": false,
    "enable_martin_sub_base": true,
    "enable_martin_sub": false,
    "martin_grid_profit": 0.02,
    "martin_sub_part": 0.33,
    "first_part": 0.2
  }
}
```

**为什么这样设：**

- `target_pos=20`：主动降低到20%仓位（原有的100股减到20股）
- `enable_martin_sub=False`：网格仓不急于减（还给盈利空间）
- `stop_autoprofit_start_radio=0.03` + `back_maxvalue=0.01`：极敏感的移动止盈，回撤1%就跑
- `enable_martin_add_loss=False`：禁止继续摊平

---

## 五、人工作业层介入（loss_close_need_manual）的变盘盲区

策略代码内置一道**人工层硬约束**——参数 `loss_close_need_manual`（人工作业层控制参数，智能体不可设置、不可复位，见 02 文档第七节）：人工将其设为 True 后，亏损状态下**一切**平仓路径（止损平仓、CCI 减仓、信号平仓，即所有 `set_target_pos(0)` 路径）都会被拦截，只有人工操作（`manual=True`）可以执行平仓。

它的设计意图是**人为扛住极端行情/巨大亏损**——防止智能体或程序在恐慌中自动平掉网格介入的大仓位，是刻意的"抗巨大亏损"选择。

| `loss_close_need_manual` | 变盘时行为 |
|:--|:--|
| `True`（人工开启介入） | ⚠️ 全部自动平仓路径被拦，智能体观察到的现象是"调仓目标未生效" |
| `False`（默认，介入关闭） | ✅ 所有平仓路径畅通，各层防御正常工作 |

> **智能体的正确行为：** 观察到"亏损状态下平仓未生效"时，不要反复重试 `set_target_pos(0)`，也不要尝试绕过——应判断为人工作业层介入，向人工汇报并等待指令。
> **人的决策：** 变盘（趋势反转）场景下应由人工将 `loss_close_need_manual` 设为 False，否则各层自动防御全部失效；震荡行情中则适合保持 True。

---

## 六、方向背离检测的信号参考（供智能体自身分析用）

基类的 CCI 减仓是被动兜底。智能体在自己的分析中应关注以下方向背离信号，确认背离后可给出平仓/减仓建议（仍经 01/07 的落地与风控链路执行）：

### 可检测的信号

| 信号 | 计算方法 | 判断条件 |
|:--|:--|:--|
| 均线交叉 | EMA快线下穿EMA慢线 | 多转空信号 |
| ADX 衰减 | ADX(14) 从 > 25 持续下降 | 趋势减弱 |
| MACD 死叉 | MACD快线下穿MACD慢线 | 多转空信号 |
| ATR 突变 | ATR(14) 突然放大 2 倍以上 | 波动率暴增 = 趋势可能终结 |
| 成交量异常 | 放量下跌 > 平均成交量 2 倍 | 恐慌抛售，趋势逆转 |
| CCI 提前预警 | CCI 从 +200 跌到 +50（尚未跌破 100） | 动量骤减，趋势可能终结 |

> 提醒：检测出背离并不保证立即平仓——亏损状态下若人工作业层介入（`loss_close_need_manual=True`）开启，平仓会被拦截，此时应向人工汇报并等待指令（见本文第五节）。

---

## 七、参数速查表（变盘场景专用）

| 参数 | 变盘建议值 | 说明 |
|:--|:--|:--|
| `loss_close_need_manual` | `False`（关闭介入） | ⭐ 最关键：不拦平仓；人工作业层控制参数，智能体无法设置，需人工确认 |
| `enable_stop_loss` | `True` | 最后兜底 |
| `stop_loss_radio` | `0.03` ~ `0.05` | 网格仓位多则放宽 |
| `enable_martin_add_loss` | `False` | 停止抄底 |
| `enable_martin_add_profit` | `False` | 停止追高 |
| `enable_martin_sub_base` | `True` | CCI反向时减基础底仓 |
| `enable_martin_sub` | `True` | CCI反向时减网格仓 |
| `martin_grid_profit` | `0.00` ~ `0.01` | 降低减仓盈利门槛 |
| `martin_sub_part` | `0.33` ~ `1.0` | 减仓比例（1.0=全部） |
| `target_pos` | `0` 或 `当前×30%` | 平仓或大幅缩仓 |

---

## 八、与现有文档的关系

| 相关文档 | 关联内容 |
|:--|:--|
| [`05_stop_profit_loss.md`](05_stop_profit_loss.md) | 止损平仓的触发价格和运行节奏（本文第一层防御） |
| [`06_grid_martin.md`](06_grid_martin.md) | CCI 减仓的完整四段逻辑（本文第二层防御） |
| [`07_trade_control.md`](07_trade_control.md) | `set_target_pos()` 风控链，含人工作业层介入（loss_close_need_manual）拦截 |
| [`04_ai_interface.md`](04_ai_interface.md) | AI 返回 JSON 的 `parameters` 字段可填参数 |
| [`09_scenarios.md`](09_scenarios.md) | 场景 2（震荡→破位）、场景 5（保守防守）与本主题有交集 |
