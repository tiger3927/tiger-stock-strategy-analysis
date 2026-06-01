# 10 — 变盘逆势：如何应对持仓方向与大盘趋势相反

> 当持仓方向与大盘/个股趋势出现背离时，基类多层防御机制如何配合、参数如何调整。
> 对照代码：`strategies/tiger_grid_template.py` 中 `martin_add_sub()` / `profit_loss_stop_action()` / `set_target_pos()` / `loss_close_need_manual`。

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
  ├── [T+0, 信号层] 子类/主信号检测（最快）
  │     均线交叉、ADX衰减、ATR突变、EMA下穿 → 直接 set_target_pos(0)
  │     ⚠️ 若 loss_close_need_manual=True 且亏损，平仓被拦截
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
  └── [T+数分钟, bar 级] 子类 on_bar() 定期检查
        自定义指标值变化 → set_target_pos()
```

**关键认识：**

- **最快**是子类自身信号，不需要等 CCI 穿越
- **CCI 减仓是"自动化的主动防御"**，不需要 AI，适合懒人挂机
- **止损是最后的被动防线**，不是进攻手段
- **loss_close_need_manual** 可能切断前两条路，务必谨慎

---

## 三、变盘减仓的 CCI 机制深度解析

### CCI 方向判断原理

`get_martin_chance()` 调用 `numba_cci_100_back()`（[tiger_Indicators.py](file:///d:/Code/Python/Trading/vnpy_test/tools/tiger_Indicators.py#L7-L21)）：

```
在最近 period（默认120）根bar中，从最新往回扫：
  若 CCI[i-1] >= 100 且 CCI[i] < 100  → direction = -1（CCI向下穿越100）
  若 CCI[i] > -100 且 CCI[i-1] >= -100 → direction = 1（CCI向上穿越-100）
```

然后在 `martin_add_sub()` 中（[src](file:///d:/Code/Python/Trading/vnpy_test/strategies/tiger_grid_template.py#L3435-L3483)）：

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
| **loss_close_need_manual 阻断** | 所有平仓/减仓动作最终调用 `set_target_pos()`，亏损平仓被拦截 | 只能硬扛到止损触发 |

---

## 四、参数调整策略

### 心态决定打法

| 你的态度 | 关键设置 | 效果 |
|:--|:--|:--|
| **宁可少赚也要保本** | `loss_close_need_manual=False` + `enable_martin_sub=True` | CCI反转时果断减仓，不犹豫 |
| **网格定投、相信反转会回来** | `enable_martin_sub=False` + `loss_close_need_manual=True` | CCI反转不卖，亏损也不人工确认（被动等待止损或反弹） |
| **让止损决定一切** | 仅 `enable_stop_loss=True`，其他子类信号处理 | 简单粗暴，适合信号很准的策略 |

### 核心场景 1：趋势追踪变盘（做多 → 趋势转空，立即平仓）

**参数策略：完全信任子类信号，CCI减仓当补充**

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
    "first_part": 0.2,
    "max_position_ratio": 1.0
  }
}
```

**为什么这样设：**

- `loss_close_need_manual=False`：不拦平仓（**最重要**，趋势反转时不犹豫）
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
    "first_part": 0.2,
    "max_position_ratio": 1.0
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
    "first_part": 0.2,
    "max_position_ratio": 0.2
  }
}
```

**为什么这样设：**

- `target_pos=20`：主动降低到20%仓位（原有的100股减到20股）
- `max_position_ratio=0.2`：限制未来加仓上限
- `enable_martin_sub=False`：网格仓不急于减（还给盈利空间）
- `stop_autoprofit_start_radio=0.03` + `back_maxvalue=0.01`：极敏感的移动止盈，回撤1%就跑
- `enable_martin_add_loss=False`：禁止继续摊平

---

## 五、`loss_close_need_manual` 的变盘场景陷阱

### 参数定义

| 参数 | 默认值 | 含义 |
|:--|:--|:--|
| `loss_close_need_manual` | `False` | 亏损时调用 `set_target_pos(0)` 是否需要人工审批 |

源码逻辑（[tiger_grid_template.py](file:///d:/Code/Python/Trading/vnpy_test/strategies/tiger_grid_template.py#L2860-L2864)）：

```python
if self.loss_close_need_manual and not manual:
    if self.pos != 0 and self.current_profit_loss_radio < 0:
        if target_pos_calcover == 0:
            print("当前设置，人工审核亏损情况的平仓行为！")
            return   # ← 直接拦截！
```

### 变盘时的影响

| `loss_close_need_manual` | 变盘时行为 |
|:--|:--|
| `True` | ⚠️ 子类信号平仓 → **被拦**；CCI减仓 → **被拦**；止损平仓（`set_target_pos(0)`）→ **也被拦**。只能外部手动操作。 |
| `False`（默认） | ✅ 所有平仓路径畅通，三层防御正常工作 |

### 建议

> **变盘场景下必须设置 `loss_close_need_manual=False`**。这个参数是为"防止AI误判卖飞网格大仓位"设计的保护性开关，适合**震荡行情**——趋势反转时反而是障碍。

---

## 六、子类自行检测方向背离的最佳实践

基类的 CCI 减仓是被动兜底，**子类应该主动检测方向背离**。

### 可检测的信号

| 信号 | 计算方法 | 判断条件 |
|:--|:--|:--|
| 均线交叉 | EMA快线下穿EMA慢线 | 多转空信号 |
| ADX 衰减 | ADX(14) 从 > 25 持续下降 | 趋势减弱 |
| MACD 死叉 | MACD快线下穿MACD慢线 | 多转空信号 |
| ATR 突变 | ATR(14) 突然放大 2 倍以上 | 波动率暴增 = 趋势可能终结 |
| 成交量异常 | 放量下跌 > 平均成交量 2 倍 | 恐慌抛售，趋势逆转 |
| CCI 提前预警 | CCI 从 +200 跌到 +50（尚未跌破 100） | 动量骤减，趋势可能终结 |

### 推荐的子类实现骨架

```python
def on_bar(self, bar: BarData):
    super().on_bar(bar)

    if self.pos == 0:
        return

    reversal = self._detect_reversal()
    if reversal:
        self.reset_ai_strategy_params()
        self.enable_stop_loss = True
        self.stop_loss_radio = 0.03
        self.loss_close_need_manual = False  # 关键：允许平仓
        self.enable_martin_sub_base = True
        self.enable_martin_sub = True
        self.set_target_pos(0, comment="子类检测方向背离，平仓")

def _detect_reversal(self):
    """子类应根据自身策略信号实现 """
    if self.pos > 0:
        # 做多转空示例
        ema_dead_cross = self.ema_fast[-1] < self.ema_slow[-1]
        cci_dropping = self.cci[-1] < 50 and self.cci[-2] > 100
        adx_weakening = self.adx[-1] < 20
        return ema_dead_cross or (cci_dropping and adx_weakening)
    else:
        # 做空转多... 对称逻辑
        pass
    return False
```

---

## 七、参数速查表（变盘场景专用）

| 参数 | 变盘建议值 | 说明 |
|:--|:--|:--|
| `loss_close_need_manual` | `False` | ⭐ 最关键：不拦平仓 |
| `enable_stop_loss` | `True` | 最后兜底 |
| `stop_loss_radio` | `0.03` ~ `0.05` | 网格仓位多则放宽 |
| `enable_martin_add_loss` | `False` | 停止抄底 |
| `enable_martin_add_profit` | `False` | 停止追高 |
| `enable_martin_sub_base` | `True` | CCI反向时减基础底仓 |
| `enable_martin_sub` | `True` | CCI反向时减网格仓 |
| `martin_grid_profit` | `0.00` ~ `0.01` | 降低减仓盈利门槛 |
| `martin_sub_part` | `0.33` ~ `1.0` | 减仓比例（1.0=全部） |
| `max_position_ratio` | 当前占比或更低 | 限制后续重新加仓 |
| `target_pos` | `0` 或 `当前×30%` | 平仓或大幅缩仓 |

---

## 八、与现有文档的关系

| 相关文档 | 关联内容 |
|:--|:--|
| [`05_stop_profit_loss.md`](05_stop_profit_loss.md) | 止损平仓的触发价格和运行节奏（本文第一层防御） |
| [`06_grid_martin.md`](06_grid_martin.md) | CCI 减仓的完整四段逻辑（本文第二层防御） |
| [`07_trade_control.md`](07_trade_control.md) | `set_target_pos()` 风控链，含 `loss_close_need_manual` 拦截 |
| [`04_ai_interface.md`](04_ai_interface.md) | AI 返回 JSON 的 `parameters` 字段可填参数 |
| [`09_scenarios.md`](09_scenarios.md) | 场景 2（震荡→破位）、场景 5（保守防守）与本主题有交集 |
