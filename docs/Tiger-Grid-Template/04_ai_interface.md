# 04 — AI 接口（虚方法）

> 基类提供的虚方法，子类必须重写其中 3 个来实现自己的策略逻辑。

---

## 一、必须重写的 3 个虚方法

### 1. `build_openclaw_prompt(tick) → dict`

构建发送给 AI 的 prompt。基类默认返回空，**子类必须重写**。

```python
def build_openclaw_prompt(self, tick=None) -> dict:
    return {"message": ""}
```

**子类应注入的信息：**
- 当前市场环境（K 线数据、技术指标、成交量变化）
- `_build_status_dict()` 返回的策略状态快照
- 自定义指标的当前值（`x_script` / `y_script` 的计算结果）
- 策略所处阶段

### 2. `on_openclaw_analysis_result(result) → None`

AI 分析结果回调。基类默认不做任何处理，**子类必须重写**。

```python
def on_openclaw_analysis_result(self, result: dict):
    pass
```

**子类应做的事（标准流程）：**
```
1. self.reset_ai_strategy_params()          # 清洗参数
2. 解析 result JSON，提取 AI 建议
3. 根据建议设置参数（调用基类方法或直接赋值）
4. 调用 set_target_pos(target) 设定目标仓位
```

### 3. `reset_ai_strategy_params()`

参数清洗，在每次应用 AI 结果前调用。**子类重写时必须先调用 `super()`。**

#### 开关 → 全部关闭

```
enable_martin_add_open      = False
enable_martin_add_profit    = False
enable_martin_add_loss      = False
enable_martin_sub_profit    = False
enable_martin_sub_loss      = False
enable_stop_profit          = False
enable_stop_loss            = False
enable_atr_stop_loss        = False
enable_atr_stop_profit      = False
enable_stop_autoprofit      = False
enable_first_allow_prices   = False
enable_allow_price_high     = False
```

#### 数值 → 回默认

```
first_part                  = 0.2
max_position_ratio          = 1.0
stop_profit_radio           = 0.09
stop_loss_radio             = 0.03
stop_autoprofit_start_radio = 0.05
stop_autoprofit_back_radio  = 0.61
stop_autoprofit_back_maxvalue = 0.02
martin_grid_distance        = 0.02
martin_grid_profit          = 0.02
martin_add_count            = 10
martin_sub_part             = 0.33
target_allow_price          = 0
```

---

## 二、基类提供的辅助方法（子类直接调用）

### `set_first_prices(enable, min_price, max_price)`

设置首仓价格区间。自动校验：若 `min_price >= max_price` 或 `min_price < 0`，则拒绝设置。

### `set_allow_price_high(enable, price)`

设置禁止追高/追低价格红线。自动校验：若 `price == 0`，则拒绝设置。

---

## 三、AI 返回 JSON 约定（⚠️ 由子类自行定义格式，以下为推荐协议）

> 基类不规定 AI 返回的 JSON 格式，`on_openclaw_analysis_result()` 的解析逻辑完全由子类决定。
> 以下是推荐的标准化格式，便于 AI 智能体与子类策略协作。

### 推荐格式

```json
{
  "direction": "多",
  "base_direction": 1,
  "target_pos": 100.0,
  "reason": "30分钟线EMA金叉，CCI上穿100，趋势信号明确",
  "market_judgment": "上升趋势",
  "parameters": {
    "enable_stop_profit": false,
    "enable_atr_stop_loss": true,
    "enable_atr_stop_profit": true,
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

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|:--|:--|:--|:--|
| `direction` | string | 是 | 方向描述："多"/"空"/"中性" |
| `base_direction` | int | 是 | 1=做多，-1=做空，0=空仓待命 |
| `target_pos` | float | 是 | 目标持仓量 |
| `reason` | string | 是 | AI 判断依据，便于日志追溯 |
| `market_judgment` | string | 否 | 市场判断："上升趋势"/"下跌趋势"/"震荡"/"不确定" |
| `parameters` | dict | 是 | 本轮建议的参数配置，仅填需要修改的字段 |

### `parameters` 中可填的参数（子类应根据自己的参数表调整）

| 类别 | 可填参数 |
|:--|:--|
| 止盈止损开关 | `enable_stop_profit`, `enable_stop_loss`, `enable_atr_stop_loss`, `enable_atr_stop_profit`, `enable_stop_autoprofit` |
| 止盈止损数值 | `stop_profit_radio`, `stop_loss_radio`, `atr_loss_multiple`, `stop_autoprofit_start_radio`, `stop_autoprofit_back_maxvalue` |
| 网格开关 | `enable_martin_add_open`, `enable_martin_add_loss`, `enable_martin_add_profit`, `enable_martin_sub_profit`, `enable_martin_sub_loss` |
| 网格数值 | `martin_grid_distance`, `martin_add_count`, `martin_grid_profit`, `martin_sub_part` |
| 仓位数值 | `first_part`, `max_position_ratio` |
| 价格保护 | `enable_first_allow_prices`, `first_allow_price_min`, `first_allow_price_max`, `enable_allow_price_high`, `allow_price_high` |
| 金字塔 | `martin_add_pyramid`, `martin_add_pyramid_radio` |

---

## 四、子类不需要重写的方法

### `on_openclaw_confirm_target_pos_result()`

当 `enable_openclaw_confirm_target_pos=True` 时，基类在 `trade()` 中自动处理 AI 订单审核。AI 应返回：

```json
{"decision": "同意|拒绝", "approved_target_pos": 0.0, "reason": "..."}
```

子类不需要重写此方法。
