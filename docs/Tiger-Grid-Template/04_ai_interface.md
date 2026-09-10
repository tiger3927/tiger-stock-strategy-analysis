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

- 全部 AI 可控（治理）参数 → 恢复 **parameters_info 模板默认值**（基类 24 个，清单见下；`openclaw_martin` 另将"用户或者智能体的备注"纳入治理集，共 25 个）；
- **用户 UI 手动设置、或 MCP 设置的参数值作为基线保留，不会被复位覆盖。** 即：MCP 通过 `set_param` 设置的参数在后续每轮 AI 复位后仍然生效。

#### 复位后：开关 → 模板默认（注意：并非全部关闭！）

| 开关 | 复位后 | 说明 |
|:--|:--:|:--|
| `enable_stop_profit` / `enable_stop_loss` / `enable_stop_autoprofit` | **True（开）** | 模板默认即开：AI 当轮不输出时，固定止盈 9% / 固定止损 3% / 移动止盈 5% 持续生效 |
| `enable_martin_add_open` / `enable_martin_add_profit` / `enable_martin_add_loss` | False（关） | 网格开仓/加仓开关全关 |
| `enable_martin_sub_base` / `enable_martin_sub` | False（关） | 网格减仓开关全关 |
| `enable_atr_stop_loss` / `enable_atr_stop_profit` | False（关） | ATR 独立系统关 |
| `enable_first_allow_prices` / `enable_allow_price_high` | False（关） | 价格约束清空 |

> ⚠️ **关键语义：每轮复位后，三个止盈止损开关（固定止盈/固定止损/移动止盈）默认是开着的，不是关着的。**
> AI 不输出这三个开关 = 止盈止损以模板默认值继续保护；想关掉必须显式输出 `false`。

#### 复位后：数值 → 模板默认

| 参数 | 复位后 |
|:--|:--:|
| `base_direction` | 0（无方向） |
| `first_part` | 0.2 |
| `stop_profit_radio` | 0.09 |
| `stop_loss_radio` | 0.03 |
| `atr_loss_period` | 14 |
| `atr_loss_multiple` | 12 |
| `stop_autoprofit_start_radio` | 0.05 |
| `stop_autoprofit_back_maxvalue` | 0.02 |
| `martin_grid_distance` | 0.03 |
| `martin_add_count` | 10 |
| `martin_grid_profit` | 0.03 |
| `martin_sub_base_part` | 0.33 |

（同时清零运行时保护变量 `target_allow_price=0` 与 ATR 动态止损/止盈价格。人工作业层控制参数——`loss_close_need_manual`、`enable_openclaw_analysis`、`openclaw_main_interval`、`enable_openclaw_confirm_target_pos`——均不在复位范围内，始终保留人工设置。）

---

## 二、价格类参数落地校验规则（AI JSON 相关）

设置首仓价格区间 / 禁止追高红线时有内置校验，不满足则**自动关闭对应开关并把价格归零（约束不生效），并记录日志**：

| 设置 | 校验规则 | 非法时的行为 |
|:--|:--|:--|
| 首仓价格区间（`enable_first_allow_prices` + min/max） | 需满足 `0 <= min < max` | 开关被关，min/max 归零 |
| 禁止追高/追低红线（`enable_allow_price_high` + price） | 需满足 `price != 0`（0=未设置） | 开关被关，价格归零 |

- 价格数值参数（`first_allow_price_min/max`、`allow_price_high`）由 **AI/MCP** 设置（AI 当轮分析基于支撑/压力/风险收益比推导），落地前须经上方校验；
- 对应开关 `enable_first_allow_prices`/`enable_allow_price_high` 为 false 时清空约束、价格值不生效；
- 字符串价格会导致事件线程崩溃，价格必须严格为数值。

---

## 三、三类主题参数：生命周期分层（策略核心思路 / 用户备注 / 最新通知）

三者都会注入 AI 分析提示词，但**生命周期与写入方完全不同**。区分口径按三维：**谁写入 × 是否随单轮复位 × 是否随全清仓清除**。

| 参数 | 定位 | 谁能写 | 随单轮复位？ | 随全清仓清除？ | 提示词区块 |
|:--|:--|:--|:--:|:--:|:--|
| `openclaw_main_approach`（策略核心思路） | **品种级长期打法锚点**（如分红吃息/趋势跟随/均值回归/动量轮动/网格收波/市场中性/波动率） | 仅 MCP / 人工 / 新建（**AI 只读**） | 否 | 否 | `[策略核心思路]` |
| `openclaw_user_remark`（用户或者智能体的备注） | **双用**：AI 面=持仓期开仓意图；manual/mcp 面=长期基线 | AI + MCP + 人工 | AI 来源=是；manual/mcp=否 | AI 来源=是（风险退出期清仓原因保留）；manual/mcp=否 | `[策略要求备注]` |
| `openclaw_notice`（最新通知） | **短期事件**（如"今晚财报，注意风控"） | 仅 MCP / 人工 | 否 | 否 | `[最新通知]` |

> 一句话：**main_approach 最长期**（品种级、AI 只读、永不复位/永不随清仓清除）；**notice 最短**（事件型、覆盖式）；**user_remark 居中且双用**——它本身不是纯粹的"短期"，AI 来源是持仓期开仓意图、manual/mcp 来源是长期基线。

### 3.1 策略核心思路 `openclaw_main_approach`（长期锚点，AI 只读）

本品种的**长期操作范式**。由用户/MCP 通过 `cta_strategy_set_parameters`（或新建策略 `setting`）设定，**AI 只读、不可改、不随轮次复位、不随清仓清除、长期保留**；有值时以 `[策略核心思路]` 附加到 AI 分析提示词，AI 各轮决策须与其保持一致（要偏离须在"参数修改理由"中说明）。

### 3.2 用户备注 `openclaw_user_remark`（双用：持仓期开仓意图 + 长期基线）

值经 **AI 分析提示词的「策略状态信息」JSON** 上报（该段静态说明已注明双用途语义）；有值时另以 `[策略要求备注]` 区块附加到**订单确认提示词**。按来源分两面：

- **manual/mcp 面 = 长期基线**：用户 UI 直接设置，或 MCP 通过 `cta_strategy_send_user_remark` 写入（source=manual/mcp，**不被 AI 复位清除**）；有值时 AI 给出方向/参数/下单建议应**优先满足备注中的用户要求**，与备注冲突的建议应避免。
- **AI 面 = 持仓期开仓意图（`openclaw_martin` 特有）**：该策略把此参数纳入 AI 治理集——"等待首仓"阶段 AI **必须**输出开仓目的/预期/退出条件（缺失留痕注入下轮），持仓阶段每轮重复输出（单轮语义，AI 来源随单轮复位、全清仓清除），AI 覆盖人工/MCP 原值时**留痕告知**；全清仓时系统自动清除已随仓位了结的意图（风险退出期的清仓原因保留为观察上下文）。详见 [`../Martingale-Grid-Trading-Strategy/00-Router.md`](../Martingale-Grid-Trading-Strategy/00-Router.md) §4.4/§4.5-6。

### 3.3 最新通知 `openclaw_notice`（短期事件）

**最新短期通知**，有值时以 `[最新通知]` 附加到**下一轮 AI 分析提示词**，新写入覆盖旧通知（提示词注明"以最新内容为准"）。

- 设置方式：MCP 通过 `cta_strategy_send_notice` 发送，或 `cta_strategy_set_parameters` 写 `openclaw_notice`；
- 与备注的区别：notice 是**短期事件**（覆盖式、一轮/一事件），备注的 manual/mcp 面是**长期基线**；
- 智能体行为：通知有值时，本轮分析应优先关注通知所述事项。

---

## 四、AI 返回 JSON 约定（参考协议）

> 基类本身不解析 AI 结果——JSON 的解析与落地由各具体策略代码实现，**实际协议以该策略自己的技能文档为准**。
> 例如六阶段状态机策略 `openclaw_martin` 使用 42 字段中文键协议（含"策略阶段/操作/趋势枚举/预测置信度/参数修改理由/用户或者智能体的备注"等必填字段），完整约定见 [`../Martingale-Grid-Trading-Strategy/00-Router.md`](../Martingale-Grid-Trading-Strategy/00-Router.md)。
> 以下为最小参考格式。**`parameters` 中落地参数的键名必须使用 02 的"标准名"（中文 name_cn），不得使用英文参数名**——英文键会被策略代码忽略（参数不落地）。

### 参考格式

```json
{
  "direction": "多",
  "base_direction": 1,
  "target_pos": 100.0,
  "reason": "30分钟线EMA金叉，CCI上穿100，趋势信号明确",
  "market_judgment": "上升趋势",
  "parameters": {
    "是否允许止盈": false,
    "是否允许ATR动态止损": true,
    "是否允许ATR动态止盈": true,
    "建议首仓占比": 0.2,
    "是否允许亏损时网格加仓": false,
    "是否允许盈利时网格加仓": false,
    "是否允许卖出基础底仓": false,
    "是否允许网格减仓": false
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
| 止盈止损数值 | `stop_profit_radio`, `stop_loss_radio`, `stop_autoprofit_start_radio`, `stop_autoprofit_back_maxvalue`, `atr_loss_period`, `atr_loss_multiple` |
| 网格开关 | `enable_martin_add_open`, `enable_martin_add_loss`, `enable_martin_add_profit`, `enable_martin_sub_base`, `enable_martin_sub` |
| 网格数值 | `martin_grid_distance`, `martin_add_count`, `martin_grid_profit`, `martin_sub_base_part` |
| 仓位数值 | `first_part` |
| 价格保护开关 | `enable_first_allow_prices`, `enable_allow_price_high` |

> 价格数值（`first_allow_price_min/max`、`allow_price_high`）由 AI/MCP 设置（AI 当轮分析推导，见第二节）；ATR 数值（`atr_loss_period/multiple`）可由 AI 输出（落地范围 5-120 / 1-50，不输出则随复位回 14/12）；`trend_direction` 仅作记录，不驱动动作。

---

## 五、AI 订单审核协议（基类自动完成，智能体只需按格式返回）

当 `enable_openclaw_confirm_target_pos=True`（人工设定）时，每笔调仓在 `set_target_pos()` 阶段先提交智能体审核，同意后才执行；被拒的相同调仓在 5 分钟冷却期内被忽略。智能体应返回：

```json
{"decision": "同意|拒绝", "approved_target_pos": 0.0, "reason": "..."}
```
