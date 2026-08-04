# vnpy_mcp 量化系统操作指令指南

通过 **vnpy_mcp** 工具集直接向量化系统发送操作指令（调仓、平仓、参数设置、通知、启停策略等）。

> 前提条件：**量化系统必须正在运行**。操作类工具直连量化系统，实时生效。

---

## 操作工具速查

| 工具 | 用途 | 关键参数 |
|------|------|---------|
| `cta_strategy_set_target_pos` | 设置策略目标仓位（正数=多，负数=空，0=空仓） | `strategy_name`、`target_pos`（number）、`reason`（可选） |
| `cta_strategy_close` | 执行策略平仓（设置目标仓位为 0） | `strategy_name`、`reason`（可选） |
| `cta_strategy_set_parameters` | 修改策略运行参数（立即生效，无需重启） | `strategy_name`、`parameters`（object） |
| `cta_strategy_send_notice` | 向策略发送通知信息（非指令，仅记录） | `strategy_name`、`message` |
| `cta_strategy_get_parameters_info` | 修改参数前，先了解参数说明 | `strategy_name` |
| `cta_strategy_start` | 启动策略 | `strategy_name` |
| `cta_strategy_stop` | 停止策略 | `strategy_name` |
| `cta_strategy_add_and_start` | 新增策略并启动 | — |
| `cta_strategy_delete` | 删除策略 | `strategy_name` |

---

## 详细用法

### set_target_pos — 调仓

```python
cta_strategy_set_target_pos(
    strategy_name="MARTIN-AMD",
    target_pos=-2.0,        # 正数=多头，负数=空头，0=空仓
    reason="板块走弱减仓"
)
```

### close — 平仓

```python
cta_strategy_close(
    strategy_name="MARTIN-AMD",
    reason="紧急平仓"
)
```

### set_parameters — 参数设置

```python
# 先用 cta_strategy_get_parameters_info 查看参数说明
cta_strategy_set_parameters(
    strategy_name="MARTIN-AMD",
    parameters={
        "止损幅度": 0.05,
        "允许减盈利网格仓": True
    }
)
```

### send_notice — 发送通知

```python
cta_strategy_send_notice(
    strategy_name="MARTIN-AMD",
    message="注意风控：大盘风险等级升高"
)
```

### 启停策略

```python
cta_strategy_start(strategy_name="MARTIN-AMD")
cta_strategy_stop(strategy_name="MARTIN-AMD")
```

---

## 使用前必读

- 使用任何 vnpy_mcp 工具前，**必须先调用 `get_guide`** 获取工具集完整指南。
- 操作工具需明确 `strategy_name`（策略名称），避免操作错误策略。
- 查询类工具（账户/持仓/状态/报告）见 [vnpy_mcp.md](vnpy_mcp.md)。
- 调仓前建议先用 `cta_strategy_get_status` 查询当前持仓和参数，确认目标仓位合理。
