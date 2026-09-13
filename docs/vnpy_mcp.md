# vnpy_mcp 量化系统使用指南（查询 + 操作指令）

## 先读这个：vnpy_mcp 是 MCP 工具集，不是普通参考文档

**vnpy_mcp 是当前环境中独立部署的 MCP 工具集（MCP server），不从属于任何技能。** 本技能只是调用它，不包含它；无论是否加载本技能，vnpy_mcp 都存在于当前环境中，可直接调用。

当任务涉及账户、持仓、策略状态、分析报告、合约查询、ConID 查询，或调仓/平仓/设参/通知/启停策略时，**应优先直接调用 vnpy_mcp 工具集中的具体工具**，不要先反复阅读本文来替代工具调用。

### 使用铁律

1. **直接调用优先**：已知要查实时数据或发送操作指令时，优先直接调用 vnpy_mcp 工具集中的具体工具（如 `get_accounts`、`cta_report_get` 等），不要把本文当作执行入口。
2. **本文不是实时数据源**：本文只说明工具用途、常见参数和示例，不提供任何运行时事实。实时数据必须以 vnpy_mcp 工具返回为准。
3. **只在必要时回看本文**：只有在忘记工具名、参数名、返回字段或典型用法时，才回看本文。
4. **操作前提**：涉及调仓、平仓、设参、通知、启停策略等操作时，量化系统必须正在运行。
5. **辅助指南**：如需 vnpy_mcp 工具集的完整指南，可调用 `get_guide`（它是 vnpy_mcp 工具集下的一个工具，不是独立 MCP 工具）。

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
| `cta_strategy_get_parameters_info` | 获取指定策略**全部参数**的定义和说明（动态生成，含 `value` / `type` / `description` / `name_cn` 中文名 / `mcp_control`） |

参数：`strategy_name`。

> `mcp_control`：`true`=允许 MCP 修改；`false`=只读展示（`cta_strategy_set_parameters` 会拒绝修改）。

### 4.4 策略类型查询（新增策略前使用）

| 工具 | 用途 | 关键参数 |
|------|------|---------|
| `cta_strategy_get_classes` | 获取可用的 CTA 策略类列表（用于 `cta_strategy_add_and_start` 新增策略时选择） | 无参数 |
| `cta_strategy_get_class_parameters` | 获取指定策略类的初始化必填参数（仅返回新建策略时必须填写的关键参数） | `class_name`（必填） |

```json
// cta_strategy_get_classes 返回示例
[
  {"class_name": "openclaw_martin_Strategy", "author": "Tiger Trader"},
  {"class_name": "Multi_Signal_Treand_Strategy", "author": "Tiger Trader"}
]

// cta_strategy_get_class_parameters 返回示例（仅 init_important=True 的必填参数）
{"class_name": "openclaw_martin_Strategy", "required_parameters": {"init_load_days": {"value": 5, "type": "int", "description": "策略运行需要历史行情的前置天数"}, ...}}
```

> 新增策略流程：`cta_strategy_get_classes` -> `cta_strategy_get_class_parameters` -> `search_vt_symbol` -> `cta_strategy_add_and_start`

### 4.5 持仓决策上下文

| 工具 | 用途 | 关键参数 |
|------|------|---------|
| `cta_strategy_get_all_reason` | 获取策略当前持仓的完整决策上下文（**修改参数或评估持仓前建议先调用**）：`持仓上下文`（当前持仓/方向/开仓时间/首仓价）、`开仓时的AI分析`、`最近一次AI分析`、`本次持仓交易历史`、`参数最近修改`（每参数最近一次 from/to/source/reason，不做持仓时间过滤）、`说明` | `strategy_name` |

> `参数最近修改.source` 取值：`manual`（UI 手动）/ `mcp`（外部智能体）/ `ai`（OpenClaw 落地）/ `config`（配置加载）/ `reset`（复位）。`manual`/`mcp` 的参数是刻意设置的，无充分行情依据不建议修改；确需修改应在 reason 中写明推翻原因。

### 4.6 策略交易历史

| 工具 | 用途 | 关键参数 |
|------|------|---------|
| `cta_strategy_get_trade_history` | 策略自创建以来的完整交易历史（读本地 `.vntrader/Trade_Records/{strategy_name}-{vt_symbol}.trades_txt`，**含已平仓轮次**；交易列表按时间倒序，`持仓价`=交易后持仓均价；策略需在实盘模式运行过才有该文件） | `strategy_name`（必填）、`limit`（不传=全部，传则返回最近 N 条） |

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

`cta_report_list` 返回结构（示例）：
```json
{
  "分析报告": [
    {"报告名称": "美股大盘与板块和资金流向分析", "报告数量": 5, "最新报告": "20260731_094309"}
  ],
  "策略分析记录": [
    {"策略名称": "MARTIN-AMD", "分析报告数量": 6, "最新分析报告": "20260722_120537",
     "订单审核数量": 2, "最新订单审核": "20260806_110000"}
  ]
}
```

> - 两类报告均为**本地落盘文件**（`.vntrader/Strategy_OpenClaw_Records/`）；返回"报告不存在"时，需先触发对应 AI 分析任务生成后重试
> - 策略分析记录区分 `analyze-*.json`（AI 分析）与 `order-*.json`（订单审核），某类无记录则对应字段不出现
> - `report_index` 支持负数（-1=最新，-2=倒数第二）

---

## 7. 行情与合约

| 工具 | 用途 | 关键参数 |
|------|------|---------|
| `get_tick` | 获取指定合约最新 Tick 行情 | `vt_symbol` |
| `get_history_bars` | 获取指定合约历史 K 线。**推荐直接传 `vt_symbol`**（服务端自动解析 symbol/exchange/gateway_name）；或传 `symbol`+`exchange`+`gateway_name` 三参数（必须取自 `get_contract` / `get_all_contracts` 返回字段，严禁自行猜测） | `vt_symbol` / `symbol`、`exchange`、`gateway_name`、`interval`（可选 `1m`/`1h`/`d`，默认 `1m`）、`start`、`end`（日期，缺省为当天） |
| `get_contract` | 获取指定合约信息 | `vt_symbol` |
| `get_all_contracts` | 获取所有已注册（已订阅）的合约信息 | 无 |
| `search_vt_symbol` | 搜索合约的 vt_symbol（支持 IB 实时查询 + 本地文件）；返回 `vt_symbol`、`ticker`、`name`、`gateway`、`source` 等 | `query`（ticker / 名称 / conid，如 AAPL、苹果、265598）、`market`（可选 `IB`/`BINANCE`/`CTP`/`A`，默认全量；`IB` 时优先实时向网关查询确保 conid 最新） |
| `subscribe_contract` | 订阅合约 Tick 行情（**策略创建时会自动订阅，通常无需手动调用**；仅用于提前查看行情或未被策略使用的合约） | `vt_symbol`、`gateway_name` |
| `get_gateways` | 查询已注册的交易所网关及其连通状态（名称、是否连通、支持的交易所、已订阅合约数、账户数） | 无 |

---

## 8. 操作指令（调仓 / 平仓 / 设参 / 通知 / 启停策略）

通过 vnpy_mcp 操作类工具直接向量化系统发送操作指令，实时生效。

> 前提条件：**量化系统必须正在运行**。操作类工具直连量化系统，实时生效。

### 8.1 操作工具速查

| 工具 | 用途 | 关键参数 |
|------|------|---------|
| `cta_strategy_set_target_pos` | 设置策略目标仓位（**绝对数量**，正数=多，负数=空，0=空仓） | `strategy_name`、`target_pos`（number，必填）、`reason`（可选） |
| `cta_strategy_close` | 执行策略平仓（设置目标仓位为 0） | `strategy_name`、`reason`（可选） |
| `cta_strategy_set_parameters` | 修改策略运行参数（立即生效，无需重启） | `strategy_name`、`parameters`（object，必填）、`reason`（建议必填，缺省记为 "MCP"） |
| `cta_strategy_send_notice` | 向策略发送**短期通知**（写入 `openclaw_notice` 参数，随 AI 分析提示词注入；自动加时间戳，最新一条为准；不支持该参数的策略仅写日志） | `strategy_name`、`message`（必填） |
| `cta_strategy_send_user_remark` | 为策略设置**用户备注**（长期指令/约束：写入 `openclaw_user_remark`，长期有效、新备注覆盖旧备注，随 AI 分析提示词注入；不支持该参数的策略仅写日志） | `strategy_name`、`message`（必填） |
| `cta_strategy_set_main_approach` | 设置**策略核心思路**（本品种长期操作范式，写入 `openclaw_main_approach`；**AI 只读**、各轮决策须对齐；不自动加时间戳，可带 `reason` 留痕；不支持该参数的策略仅写日志） | `strategy_name`、`message`（必填）、`reason`（建议必填） |
| `cta_strategy_get_parameters_info` | 修改参数前先了解参数说明（返回**全部参数**定义，含 `name_cn` / `mcp_control`） | `strategy_name` |
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
> **加仓 / 设目标仓位前必须先核算可用资金**（`cta_strategy_get_status` 取持仓/杠杆 → `get_accounts` 取余额 → `get_tick` 取现价 → 增仓数量×现价÷杠杆+缓冲 ≤ 可用资金）；资金不足时不要下单，避免被拒单。

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
    },
    reason="价格波动加剧，收紧止损"   # 建议必填；缺省时记为 "MCP"
)
```

> `parameters` 键=参数名（字符串），值=新值（**保持原始类型**，float 传 `0.02` 而非 `"2%"`）。只传需要修改的参数，未传入的保持不变；参数立即写入并保存，重启后不丢失。参数改变**不会主动触发交易动作**（如改方向需等下一个信号才会按新方向操作）。`mcp_control=false` 的参数会被拒绝修改。

#### send_notice — 发送短期通知

```python
cta_strategy_send_notice(
    strategy_name="MARTIN-AMD",
    message="注意风控：大盘风险等级升高"
)
```

> 写入 `openclaw_notice` 参数（source=mcp，计入参数修改历史），随 AI 分析提示词注入并持久化到策略设置；头部自动加 `[YYYY-MM-DD HH:MM:SS]` 时间戳（调用方无需自行添加），**最新一条为准**。不支持该参数的策略仅写策略日志（AI 不可见）。

#### send_user_remark — 设置用户备注（长期）

```python
cta_strategy_send_user_remark(
    strategy_name="MARTIN-AAPL",
    message="回撤超 5% 停止加仓"
)
```

> 写入 `openclaw_user_remark` 参数（source=mcp，计入参数修改历史），**长期有效、新备注覆盖旧备注**，随 AI 分析提示词注入；自动加时间戳。不支持该参数的策略仅写策略日志。

#### set_main_approach — 设置策略核心思路（长期锚点）

```python
cta_strategy_set_main_approach(
    strategy_name="MARTIN-AAPL",
    message="网格收波：震荡区间内等距加仓、逐格止盈，趋势确立后转趋势跟随",
    reason="复盘发现本品种近月以区间震荡为主，锚定打法"   # 建议必填，计入参数修改历史
)
```

> 写入 `openclaw_main_approach` 参数：本品种**长期操作范式**（分红吃息/趋势跟随/均值回归/动量轮动/网格收波/市场中性/波动率等），**AI 只读不可改**、各轮决策须与其保持一致。**不自动加时间戳**（它是状态描述而非消息，写入时间见 `cta_strategy_get_all_reason` 的 `参数最近修改`）。换打法才改，不是调参。

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
> **openclaw_martin 新增必填 `openclaw_main_approach`**（策略核心思路，init_important）：`setting` 中留空该参数会被拒绝创建；建议事后用 `cta_strategy_set_main_approach` 语义化写入。
> **初始化耗时**：openclaw_martin 的 `init_load_days` 建议 5-7 天（过长易超时）；Multi_Signal_Treand 需 30 天左右、初始化较慢，返回超时后稍候用 `cta_strategy_get_status` / `cta_strategies_get_all` 检查，`inited=True` 未启动可用 `cta_strategy_start` 手动启动。

### 8.3 注意事项

- 操作工具需明确 `strategy_name`（策略名称），避免操作错误策略。
- 调仓前建议先用 `cta_strategy_get_status` 查询当前持仓和参数，确认目标仓位合理。
- **投递与路由**：操作类命令经 event_engine 投递到事件线程执行（线程安全），并按 `strategy_name` **精确路由**——目标策略未创建或未运行时立即返回明确错误（不静默等待 30 秒超时）；多策略并行时命令互不影响。`add_and_start` 返回成功后的毫秒级窗口内 handler 可能尚未注册，此时命令走超时兜底（不会误执行到别的策略）。

---

## 9. 使用前必读

- 使用任何 vnpy_mcp 工具前，**必须先调用 `get_guide`** 获取工具集完整指南（工具分类、调用格式、返回示例、核心业务流程）。
- 查询类工具均无需用户名参数，直接调用。
- 操作工具需明确 `strategy_name`（策略名称），避免操作错误策略。
- 调仓前建议先用 `cta_strategy_get_status` 查询当前持仓和参数，确认目标仓位合理。
