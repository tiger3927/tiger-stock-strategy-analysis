# vnpy_mcp 量化系统使用指南（查询 + 操作指令）

## 先读这个：vnpy_mcp 是 MCP 工具，不是普通参考文档

**vnpy_mcp 是 MCP 工具。** 当任务涉及账户、持仓、策略状态、分析报告、合约查询、ConID 查询，或调仓/平仓/设参/通知/启停策略时，**应优先直接调用 vnpy_mcp 工具**，不要先反复阅读本文来替代工具调用。

### 使用铁律

1. **先调用 `get_guide`**：在使用任何 vnpy_mcp 工具前，先获取工具集完整指南。
2. **直接调用优先**：已知要查实时数据或发送操作指令时，优先直接调用 vnpy_mcp，不要把本文当作执行入口。
3. **本文不是实时数据源**：本文只说明工具用途、常见参数和示例，不提供任何运行时事实。实时数据必须以 vnpy_mcp 工具返回为准。
4. **只在必要时回看本文**：只有在忘记工具名、参数名、返回字段或典型用法时，才回看本文。
5. **操作前提**：涉及调仓、平仓、设参、通知、启停策略等操作时，量化系统必须正在运行。

### 何时直接调用 vnpy_mcp

- 查账户、持仓、委托、成交
- 查策略状态、策略参数、策略分析记录
- 查大盘分析报告、选股结果报告
- 查 Tick、K 线、合约、vt_symbol、ConID
- 发调仓、平仓、设参、通知、启停、新增/删除策略命令

> 查询数据为**实时**（直连量化系统），非缓存数据，**无需用户名参数**，直接调用工具即可。
>
> **备注**：本文档只是 vnpy_mcp 的调用摘要；具体工具定义、参数和返回格式，以 `get_guide` 和 vnpy_mcp 工具实际返回为准。

---

## 1. 健康检查

```
ping → "pong"（服务正常）
```

---

## 2. 账户信息

| 工具 | 用途 | 返回示例 |
|------|------|---------|
| `get_accounts` | 获取所有账户余额信息 | `accountid`、`gateway_name`、`balance`（总资产）、`frozen`（冻结）、`available`（可用） |

```json
{"accountid": "DUQ140183.HKD", "gateway_name": "IB", "balance": 996528.99, "frozen": 0, "available": 995840.16}
```

**注意**：总资产 `balance` 是包含持仓占用保证金和可用现金在内的全部资产（不能再加持仓市值）。

---

## 3. 持仓信息

| 工具 | 用途 |
|------|------|
| `get_positions` | 获取所有持仓信息 |

返回每个持仓的品种、数量、成本价、盈亏等。

---

## 4. 策略状态

### 4.1 所有策略运行概况

| 工具 | 用途 |
|------|------|
| `cta_strategies_get_all` | 获取所有已注册策略的运行概况（中文键名） |

返回每个策略：`策略名称`、`策略类型`、`vt_symbol`、`实际持仓`、`持仓目标`、`持仓均价`、`当前行情价格`、`当前持仓盈亏比`、`可用资金总量`、`已使用资金占比`、`网格已用/最大`、`操作方向`、`自动开仓`、`止盈`、`止损`、`移动止盈`、`网格间距`、`亏损加仓`、`运行状态`、`品种信息`（含 ticker/conid/分类/行业）。

### 4.2 单策略完整状态

| 工具 | 用途 |
|------|------|
| `cta_strategy_get_status` | 获取指定策略的运行状态（中文键名状态字典，含参数和运行变量） |

参数：`strategy_name`（策略名称，如 `MARTIN-AMD`）。

返回：`vt_symbol`、`策略类型`、`实际持仓`、`持仓目标`、`持仓均价`、`历史累计盈亏`、`当前持仓盈亏比`、`当前行情价格`、`已使用资金占比`、`已有网格仓数量`、`持仓交易历史`、`策略基础参数设置和当前变量`（嵌套字典）、`is_trading`、`品种信息`。

### 4.3 策略参数说明

| 工具 | 用途 |
|------|------|
| `cta_strategy_get_parameters_info` | 获取指定策略显式暴露的关键参数说明（名称/类型/当前值/描述） |

参数：`strategy_name`。

### 4.4 策略类型查询（新增策略前使用）

| 工具 | 用途 | 关键参数 |
|------|------|---------|
| `cta_strategy_get_classes` | 获取可用的 CTA 策略类列表（用于 `cta_strategy_add_and_start` 新增策略时选择） | 无参数 |
| `cta_strategy_get_class_parameters` | 获取指定策略类的初始化必填参数（仅返回新建策略时必须填写的关键参数） | `class_name`（必填） |

```json
// cta_strategy_get_classes 返回示例
[{"class_name": "openclaw_martin_Strategy", "author": "Tiger Trader"}]

// cta_strategy_get_class_parameters 返回示例（仅 init_important=True 的必填参数）
{"class_name": "openclaw_martin_Strategy", "required_parameters": {"init_load_days": {"value": 5, "type": "int", "description": "策略运行需要历史行情的前置天数"}, ...}}
```

> 新增策略流程：`cta_strategy_get_classes` -> `cta_strategy_get_class_parameters` -> `search_vt_symbol` -> `cta_strategy_add_and_start`

---

## 5. 成交与委托

| 工具 | 用途 |
|------|------|
| `get_trades` | 获取系统本次启动后的成交记录（参数 `limit`，默认 50） |
| `get_active_orders` | 获取所有活动委托（未成交或部分成交的订单） |

---

## 6. 分析报告（大盘分析 / 选股结果 / 策略记录）

| 工具 | 用途 |
|------|------|
| `cta_report_list` | 列出所有可获取的分析报告目次（分析报告 + 策略分析记录） |
| `cta_report_get` | 获取指定分析报告内容 |

`cta_report_get` 参数：
- `report_kind`：分析任务名，如 `美股大盘与板块和资金流向分析`、`美股做多选股结果`、`美股做空选股结果`
- `strategy_name`：策略名（查策略分析记录时用）
- `report_index`：报告索引（默认 -1，取最新）

**典型报告名称**（来自 `cta_report_list` 实测）：
- `美股大盘与板块和资金流向分析`
- `美股做多选股结果`
- `美股做空选股结果`
- 各策略分析记录（按 `strategy_name` 查询）

---

## 7. 行情与合约

| 工具 | 用途 |
|------|------|
| `get_tick` | 获取指定合约最新 Tick 行情（参数 `vt_symbol`） |
| `get_history_bars` | 获取指定合约历史 K 线（参数 symbol/exchange/gateway_name/start/interval） |
| `get_contract` | 获取指定合约信息（参数 `vt_symbol`） |
| `get_all_contracts` | 获取所有合约 |
| `search_vt_symbol` | 搜索 vt_symbol / 查询 ConID 对应品种 |

---

## 8. 操作指令（调仓 / 平仓 / 设参 / 通知 / 启停策略）

通过 vnpy_mcp 操作类工具直接向量化系统发送操作指令，实时生效。

> 前提条件：**量化系统必须正在运行**。操作类工具直连量化系统，实时生效。

### 8.1 操作工具速查

| 工具 | 用途 | 关键参数 |
|------|------|---------|
| `cta_strategy_set_target_pos` | 设置策略目标仓位（**绝对数量**，正数=多，负数=空，0=空仓） | `strategy_name`、`target_pos`（number，必填）、`reason`（可选） |
| `cta_strategy_close` | 执行策略平仓（设置目标仓位为 0） | `strategy_name`、`reason`（可选） |
| `cta_strategy_set_parameters` | 修改策略运行参数（立即生效，无需重启） | `strategy_name`、`parameters`（object，必填） |
| `cta_strategy_send_notice` | 向策略发送通知信息（是通知不是指令，仅用于记录信息） | `strategy_name`、`message`（必填） |
| `cta_strategy_get_parameters_info` | 修改参数前先了解参数说明（返回部分关键参数，并非全部） | `strategy_name` |
| `cta_strategy_start` | 启动策略（未初始化则先初始化再启动） | `strategy_name` |
| `cta_strategy_stop` | 停止策略（不删除策略，仅停止交易） | `strategy_name` |
| `cta_strategy_add_and_start` | 新增并启动策略 | `class_name`、`strategy_name`、`vt_symbol`（`setting` 可选） |
| `cta_strategy_delete` | 删除策略（需持仓为 0，运行中会自动先停止） | `strategy_name` |

### 8.2 详细用法

#### set_target_pos — 调仓

```python
cta_strategy_set_target_pos(
    strategy_name="MARTIN-AMD",
    target_pos=100,         # 目标持仓数量（股数/张数），不是资金比例；0=空仓，负数=空头
    reason="板块走弱减仓"
)
```

> **注意**：`target_pos` 是**绝对数量**（如 100 股），不是资金比例（0.5 ≠ 50% 仓位）。设为 0 等同于平仓（与 `cta_strategy_close` 等效）。

#### close — 平仓

```python
cta_strategy_close(
    strategy_name="MARTIN-AMD",
    reason="紧急平仓"
)
```

#### set_parameters — 参数设置

```python
# 先用 cta_strategy_get_parameters_info 查看参数说明
cta_strategy_set_parameters(
    strategy_name="MARTIN-AMD",
    parameters={
        "stop_loss_radio": 0.02,     # 固定止损比例
        "enable_martin_sub": True    # 允许网格盈利仓减仓
    }
)
```

> **注意**：`parameters` 键=参数名（字符串），值=新值（**保持原始类型**，float 传 `0.02` 而非 `"2%"`）。只传需要修改的参数，未传入的保持不变；参数立即写入并保存，重启后不丢失。参数改变**不会主动触发交易动作**（如改方向需等下一个信号才会按新方向操作）。

#### send_notice — 发送通知

```python
cta_strategy_send_notice(
    strategy_name="MARTIN-AMD",
    message="注意风控：大盘风险等级升高"
)
```

#### 启停策略

```python
cta_strategy_start(strategy_name="MARTIN-AMD")  # 未初始化则先初始化再启动
cta_strategy_stop(strategy_name="MARTIN-AMD")   # 仅停止交易，不删除策略
```

#### 新增 / 删除策略

```python
# 新增并启动：vt_symbol 由 search_vt_symbol 返回，直接使用，严禁自行拼接
# IB 合约必须是 conid.SMART（如 "265598.SMART"），严禁 ticker.SMART；币安为 SYMBOL_SWAP_BINANCE.GLOBAL
cta_strategy_add_and_start(
    class_name="openclaw_martin_Strategy",   # 先 cta_strategy_get_classes 查可用策略类型
    strategy_name="MARTIN-AAPL",
    vt_symbol="265598.SMART",
    setting={}   # 可选，覆盖默认参数；必填参数优先参考 cta_strategy_get_class_parameters
)

# 删除策略：需持仓为 0；运行中会自动先停止
cta_strategy_delete(strategy_name="MARTIN-AAPL")
# → {"success": true, "message": "策略 ... 已删除", "pos": 0}
```

> **注意**：删除前若 `pos != 0` 会拒绝删除，需先 `cta_strategy_close` 平仓。

### 8.3 注意事项

- 操作工具需明确 `strategy_name`（策略名称），避免操作错误策略。
- 调仓前建议先用 `cta_strategy_get_status` 查询当前持仓和参数，确认目标仓位合理。

---

## 9. 使用前必读

- 使用任何 vnpy_mcp 工具前，**必须先调用 `get_guide`** 获取工具集完整指南（工具分类、调用格式、返回示例、核心业务流程）。
- 查询类工具均无需用户名参数，直接调用。
- 操作工具需明确 `strategy_name`（策略名称），避免操作错误策略。
- 调仓前建议先用 `cta_strategy_get_status` 查询当前持仓和参数，确认目标仓位合理。
