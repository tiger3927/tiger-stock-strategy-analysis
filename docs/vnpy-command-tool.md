# VeighNa 统一命令工具 (vnpy_command.py)

通过 Redis Proxy 向 data_engine 发送各类命令并获取执行结果。

> ⚠️ **前提条件：量化系统必须正在运行**，否则命令堆积不被处理。

### 用户参数说明

`<用户名>` 以及 `--token TOKEN` 参数要与 data_engine 配置的 `userid` 和 `command_token` 一致，否则命令发不到正确的队列，data_engine 收不到。

- **已知用户名和 TOKEN 时**：直接使用即可。
- **不知道用户名或 TOKEN 时**：搜索记忆中或对话历史中已确认的用户名和 TOKEN，或直接向用户询问确认。

---

## 命令速查

| 目的 | 子命令 | 参数 | 说明 |
|------|--------|------|------|
| 查询 ConID | `conid` | `<用户名> <股票代码>` | 必带 `--wait`，结果才有意义 |
| 全平策略 | `close` | `<用户名> <策略名> [--comment]` | 默认 comment="紧急平仓" |
| 发通知 | `notice` | `<用户名> <策略名> [--comment]` |  |
| 调目标仓位 | `set-target-pos` | `<用户名> <策略名> --pos POS [--comment]` | pos: 正数=多，负数=空，0=空仓 |
| 查策略状态 | `query-strategy-status` | `<用户名> <策略名>` | 建议带 `--wait` |
| 原始命令 | `send` | `<用户名> <策略名> '<JSON>'` | JSON 必须含 `cmd` 字段 |

所有子命令都支持以下参数：

| 参数 | 位置 | 说明 |
|------|------|------|
| `--token TOKEN` | 子命令**之前** | 校验令牌，对应 data_engine 的 `command_token` |
| `--wait [N]` | 子命令**之前或之后**均可 | 等待结果，默认超时 30 秒 |
| `--response-key KEY` | 子命令**之前** | 自定义结果 key。不指定则自动生成 |

系统命令队列：`vnpy:{user}:command:_system`，data_engine 根据 `type` 字段路由。

---

## Wait 规则

| 子命令 | 是否必带 `--wait` | 原因 |
|--------|-----------------|------|
| `conid` | **建议必带** | 查询的目的是获取结果，不带 `--wait` 不会在终端看到 ConID |
| 策略命令 | 可选 | 不带 `--wait` 为 fire-and-forget，仅确认 Redis RPUSH 成功 |

**不带 `--wait` 时**，只返回 `✅ 命令已发送`，不保证策略已执行（策略可能不处理或报错，但 data_engine 已转发）。

---

## 引擎命令

### conid — 查询 IB 合约 ConID

```bash
python scripts/vnpy_command.py --token TOKEN conid <用户名> <股票代码> --wait
```

成功时返回 ConID、交易所、货币、证券类型、主交易所。失败返回错误原因（IB 网关未连接 / 未找到合约）。

示例：
```bash
python scripts/vnpy_command.py --token tiger-code-123456 conid tiger-code AAPL --wait
python scripts/vnpy_command.py --token tiger-code-123456 conid tiger-code SGPIY --wait 60
```

---

## 策略命令

### close — 全平

```bash
python scripts/vnpy_command.py [--token TOKEN] [--wait [N]] close <用户名> <策略名> [--comment TEXT]
```

示例：
```bash
python scripts/vnpy_command.py --token tiger-code-123456 close tiger-code MARTIN-AMD --comment "紧急平仓"
python scripts/vnpy_command.py --token tiger-code-123456 close tiger-code MARTIN-AMD --wait --comment "平仓并确认"
```

### notice — 通知

```bash
python scripts/vnpy_command.py [--token TOKEN] notice <用户名> <策略名> [--comment TEXT]
```

### set-target-pos — 调仓

```bash
python scripts/vnpy_command.py [--token TOKEN] [--wait [N]] set-target-pos <用户名> <策略名> --pos POS [--comment TEXT]
```

pos 取值：正=多头，负=空头，0=空仓。

### send — 原始命令

```bash
python scripts/vnpy_command.py [--token TOKEN] [--wait [N]] send <用户名> <策略名> '<JSON>'
```

JSON 必须含 `cmd` 字段，工具自动包装 `type` + `target_strategy`。

示例：
```bash
python scripts/vnpy_command.py --token tiger-code-123456 send tiger-code MARTIN-AMD '{"cmd":"close","comment":"平仓"}'
```

### query-strategy-status — 查策略状态

```bash
python scripts/vnpy_command.py [--token TOKEN] [--wait [N]] query-strategy-status <用户名> <策略名>
```

查询策略的完整状态信息，包括持仓、盈亏、参数设置、网格数据等。**建议带 `--wait`** 以便看到结果。

示例：
```bash
python scripts/vnpy_command.py --token tiger-code-123456 query-strategy-status tiger-code MARTIN-AMD --wait
```

---

## 输出说明

### ConID 查询（`conid --wait`）

```
✅ AAPL  ConID 查询成功
=======================================================
  合约 ID (conId):     265598
  符号 (symbol):       AAPL
  交易所 (exchange):   SMART
  货币 (currency):     USD
  证券类型 (secType):  STK
  主交易所:            NASDAQ
=======================================================
```

错误示例：
```
❌ AAPL  ConID 查询失败
  错误: IB 网关未连接
```

### 策略命令结果（`--wait`）

```
✅ 命令执行成功
  消息: 已全平, 平仓量: -300
```

或：
```
❌ 命令执行失败
  错误: 策略未处理该命令
```

### 超时

```
超时: 已等待 30 秒，未收到结果
```
可能原因：策略未实现结果回写、策略处理耗时过长、策略已退市（处理器未注册）。

---

## 完整命令 JSON 格式

ConID 查询：
```json
{"type": "query_conid", "symbol": "AAPL", "response_key": "vnpy:user:conid_result:AAPL_xxx", "token": "..."}
```

策略操作：
```json
{"type": "strategy_cmd", "target_strategy": "MARTIN-AMD", "cmd": "close", "comment": "紧急平仓", "response_key": "vnpy:user:strategy_cmd_result:xxx", "token": "..."}
```

`response_key` 由 `--wait` 自动生成（格式 `vnpy:{user}:{type}:{name}_{uuid8}`），5 分钟有效期。指定 `--response-key` 可覆盖。