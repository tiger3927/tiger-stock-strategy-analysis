# 04 — AI 接口协议与参数落地规则

> 写给 openclaw 等智能体：AI 返回的 JSON 如何落地为策略参数与调仓、参数复位的语义、AI 返回 JSON 的约定。

---

## 一、AI 结果落地路径（智能体返回 JSON 之后发生什么）

### 1. AI 请求中能看到什么（prompt 由策略代码组装）

发给智能体的 prompt 由策略代码组装，内容一般包括：
- 当前市场环境（K 线数据、技术指标、成交量变化）
- `_build_status_dict()` 返回的策略状态快照（见 03）
- 自定义指标的当前值（`x_script` / `y_script` 的计算结果）
- 策略所处阶段、用户备注（`openclaw_user_remark`）

智能体无需关心 prompt 的组织方式，**只需按第三节的 JSON 约定返回**；返回后由策略代码解析落地。

### 2. 返回 JSON 后的标准落地流程

```
1. reset_ai_strategy_params()          分层复位参数（语义见下节）
2. 解析 AI 返回 JSON，提取建议
3. 按建议设置参数
4. 调用 set_target_pos(target) 设定目标仓位（进入 01 的订单生命周期）
```

### 3. `reset_ai_strategy_params()` 的分层复位语义

每次应用 AI 结果前自动执行。智能体需要知道的两点：

- 所有 AI 可控开关 → 关闭，数值参数 → 恢复模板默认值（清单见下）；
- **用户 UI 手动设置、或 MCP 设置的参数值作为基线保留，不会被复位覆盖。** 即：MCP 通过 `set_param` 设置的参数在后续每轮 AI 复位后仍然生效。

#### 复位后：开关 → 全部关闭

```
enable_martin_add_open      = False
enable_martin_add_profit    = False
enable_martin_add_loss      = False
enable_martin_sub_base      = False
enable_martin_sub           = False
enable_stop_profit          = False
enable_stop_loss            = False
enable_atr_stop_loss        = False
enable_atr_stop_profit      = False
enable_stop_autoprofit      = False
enable_first_allow_prices   = False
enable_allow_price_high     = False
```

#### 复位后：数值 → 模板默认

```
first_part                  = 0.2
stop_profit_radio           = 0.09
stop_loss_radio             = 0.03
stop_autoprofit_start_radio = 0.05
stop_autoprofit_back_maxvalue = 0.02
martin_grid_distance        = 0.03
martin_grid_profit          = 0.03
martin_add_count            = 10
martin_sub_part             = 0.33
target_allow_price          = 0
```

（同时清零 ATR 动态止损/止盈价格。人工作业层控制参数——`loss_close_need_manual`、`enable_openclaw_analysis`、`openclaw_main_interval`、`enable_openclaw_confirm_target_pos`——均不在复位范围内，始终保留人工设置。）

---

## 二、价格类参数落地校验规则（AI JSON 相关）

设置首仓价格区间 / 禁止追高红线时有内置校验，不满足则**拒绝设置、保留旧值**：

| 设置 | 校验规则 |
|:--|:--|
| 首仓价格区间（`enable_first_allow_prices` + min/max） | `min < 0` 或 `min >= max` 时拒绝 |
| 禁止追高/追低红线（`enable_allow_price_high` + price） | `price == 0` 时拒绝（0=未设置） |

- 价格数值参数（`first_allow_price_min/max`、`allow_price_high`）**不是 AI 可控参数**（见 02 可改性列），智能体 JSON 中携带会被忽略，需要时由 MCP/人工设置；
- 字符串价格会导致事件线程崩溃，价格必须严格为数值。

---

## 三、用户备注 `openclaw_user_remark`

`openclaw_user_remark` 是用户附加的提示词要求（有值时以 `[策略要求备注]` 附加到 **AI 分析提示词和订单确认提示词** 中，长期有效）。

- 设置方式：用户直接设置参数，或 MCP 通过 `cta_strategy_send_user_remark` 写入；
- 智能体行为：该备注有值时，AI 给出方向/参数/下单建议应**优先满足备注中的用户要求**，与备注冲突的建议应避免。

---

## 四、AI 返回 JSON 约定（推荐协议）

> 不同策略的 JSON 协议可能略有差异（字段名/结构），以下为推荐标准格式；
> 若某策略使用自有协议，以该策略的实际解析行为为准。

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
    "first_part": 0.2,
    "enable_martin_add_loss": false,
    "enable_martin_add_profit": false,
    "enable_martin_sub_base": false,
    "enable_martin_sub": false
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

### `parameters` 中可填的参数（仅 AI 可控参数，键名用 02 的标准名）

| 类别 | 可填参数 |
|:--|:--|
| 方向 | `base_direction`（1=多 / -1=空 / 0=观望） |
| 止盈止损开关 | `enable_stop_profit`, `enable_stop_loss`, `enable_atr_stop_loss`, `enable_atr_stop_profit`, `enable_stop_autoprofit` |
| 止盈止损数值 | `stop_profit_radio`, `stop_loss_radio`, `stop_autoprofit_start_radio`, `stop_autoprofit_back_maxvalue` |
| 网格开关 | `enable_martin_add_open`, `enable_martin_add_loss`, `enable_martin_add_profit`, `enable_martin_sub_base`, `enable_martin_sub` |
| 网格数值 | `martin_grid_distance`, `martin_add_count`, `martin_grid_profit`, `martin_sub_part` |
| 仓位数值 | `first_part` |
| 价格保护开关 | `enable_first_allow_prices`, `enable_allow_price_high` |

> 价格数值（`first_allow_price_min/max`、`allow_price_high`）、`atr_loss_multiple` 等不是 AI 可控参数（见 02 可改性列），需要时由 MCP/人工设置；`trend_direction` 仅作记录，不驱动动作。

---

## 五、AI 订单审核协议（基类自动完成，智能体只需按格式返回）

当 `enable_openclaw_confirm_target_pos=True`（人工设定）时，每笔调仓在 `set_target_pos()` 阶段先提交智能体审核，同意后才执行；被拒的相同调仓在 5 分钟冷却期内被忽略。智能体应返回：

```json
{"decision": "同意|拒绝", "approved_target_pos": 0.0, "reason": "..."}
```
