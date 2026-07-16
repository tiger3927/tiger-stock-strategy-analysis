# VeighNa 统一命令工具 (vnpy_command.py)

功能1：通过 HTTP Proxy 的 API 访问 Redis，不需要redis-cli。

功能2：通过 Redis Proxy 向 量化交易系统额 data_engine 发送各类命令并获取执行结果。
> ⚠️ **前提条件：发送命令时，量化系统必须正在运行**，否则命令堆积不被处理。

### 用户参数说明

`<用户名>` 以及 `--token TOKEN` 参数要与 data_engine 配置的 `userid` 和 `command_token` 一致，否则命令发不到正确的队列，data_engine 收不到。

- **已知用户名和 TOKEN 时**：直接使用即可。
- **不知道用户名或 TOKEN 时**：搜索记忆中或对话历史中已确认的用户名和 TOKEN，或直接向用户询问确认。

命令行参数可能带有中文和空格，需要使用引号括起来。

key 如果是"/"开头，表示数据是不属于某个用户的公共数据，避免与用户相关的缓存冲突;否则，key 是用户相关的数据。

---

## 命令速查

| 目的 | 子命令 | 参数 | 说明 |
|------|--------|------|------|
| 查询 ConID | `conid` | `<用户名> <股票代码/conid>` | 必带 `--wait`，结果才有意义；`--reverse` 反向查询；`--exchange`/`--currency` 支持多市场 |
| 全平策略 | `close` | `<用户名> <策略名> [--comment]` | 默认 comment="紧急平仓" |
| 发通知 | `notice` | `<用户名> <策略名> [--comment]` |  |
| 调目标仓位 | `set-target-pos` | `<用户名> <策略名> --pos POS [--comment]` | pos: 正数=多，负数=空，0=空仓 |
| 查策略状态 | `query-strategy-status` | `<用户名> <策略名>` | 建议带 `--wait` |
| 发布信息 | `publish` | `<用户名> <key路径> [<值>] [--file PATH] [--expire N]` | 短内容用 value 参数，长内容用 --file 从文件读取 |
| 读取信息 | `get` | `<用户名> <key路径>` | 读取 Redis key 的值 |
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
python scripts/vnpy_command.py --token TOKEN conid <用户名> <股票代码> [--exchange EXCH] [--currency CCY] [--reverse] --wait
```

**参数说明：**

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--exchange` | `SMART` | 交易所代码。美股 `SMART`，港股 `SEHK`，日股 `TSEJ`，韩股 `KRX`，台股 `TWSE` |
| `--currency` | `USD` | 货币代码。美股 `USD`，港股 `HKD`，日股 `JPY`，韩股 `KRW`，台股 `TWD` |
| `--reverse` / `-r` | 无 | 反向查询：从 conid 查 ticker |

成功时返回 ConID、交易所、货币、证券类型、主交易所。失败返回错误原因（IB 网关未连接 / 未找到合约）。

示例：
```bash
# 美股（默认）
python scripts/vnpy_command.py --token tiger-code-123456 conid tiger-code AAPL --wait
python scripts/vnpy_command.py --token tiger-code-123456 conid tiger-code SGPIY --wait 60

# 港股
python scripts/vnpy_command.py --token tiger-code-123456 conid tiger-code 700 --exchange SEHK --currency HKD --wait

# 日股
python scripts/vnpy_command.py --token tiger-code-123456 conid tiger-code 7203 --exchange TSEJ --currency JPY --wait

# 韩股
python scripts/vnpy_command.py --token tiger-code-123456 conid tiger-code 005930 --exchange KRX --currency KRW --wait

# 台股
python scripts/vnpy_command.py --token tiger-code-123456 conid tiger-code 2330 --exchange TWSE --currency TWD --wait

# 反向查询（从 conid 查 ticker，支持任意市场）
python scripts/vnpy_command.py --token tiger-code-123456 conid tiger-code 265598 --reverse --wait
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

### publish — 发布信息

```bash
python scripts/vnpy_command.py [--token TOKEN] publish <用户名> <key路径> [<值>] [--file PATH] [--expire N]
```

向 Redis 写入一个 key-value（SET 操作）。key 路径规则：

- **带用户前缀**：key_path 不以 `/` 开头，完整 key = `vnpy:{username}:{key_path}`
  - 如 `analysis:result` → `vnpy:楠总1号:analysis:result`
- **全局 key（无用户前缀）**：key_path 以 `/` 开头，完整 key = 去掉 `/` 后的内容
  - 如 `/global:config` → `global:config`
  - 如 `/vnpy:global:notice` → `vnpy:global:notice`

> **注意**：读写以 `/` 开头的全局 key 时，`<用户名>` 参数不会被使用，可以传任意占位符（如 `-`）代替真实用户名。

参数：

- `key_path`：key 路径后缀或全局 key（以 `/` 开头）
- `value`：可选，要写入的值（纯文本或 JSON 字符串）。短内容直接传，长内容建议用 `--file`
- `--file PATH`：从文件读取值（替代 value 位置参数），适合几千字以上的长内容，无命令行长度限制
- `--expire N`：可选，过期时间（秒），不设置则永不过期

> **注意**：`value` 和 `--file` 二选一，同时提供时 `--file` 优先。

#### 短内容示例（直接传 value）

```bash
# 带用户前缀（自定义 key）
python scripts/vnpy_command.py --token tiger-code-123456 publish 楠总1号 user:strategy:status '{"running":true}' --expire 3600

# 全局 key（无用户前缀）
python scripts/vnpy_command.py --token tiger-code-123456 publish 楠总1号 /global:config '{"maintenance":false}'
python scripts/vnpy_command.py --token tiger-code-123456 publish 楠总1号 /vnpy:notice:all "系统维护通知" --expire 86400

# 保存大盘分析缓存（全局 key，6 小时过期）
python scripts/vnpy_command.py --token tiger-code-123456 publish 楠总1号 /vnpy:加密货币:大盘与板块和资金流向分析 '{"direction":"看多","风险观察分":65}' --expire 21600
```

#### 长内容示例（用 --file 避免命令行长度限制）

临时文件统一存放在 `tests\tmp\` 目录下（与项目临时数据目录一致），发布后**必须删除**，避免残留过多。

```powershell
# 1. 将结果写入临时文件
@'
{"result":"几千字的长内容..."}
'@ | Set-Content tests\tmp\publish_result.json -Encoding utf8

# 2. 用 --file 发布
python scripts/vnpy_command.py --token tiger-code-123456 publish 楠总1号 /vnpy:美股:选股分析结果 --file tests\tmp\publish_result.json --expire 43200

# 3. 清理临时文件
Remove-Item tests\tmp\publish_result.json
```

> **为什么需要 `--file`？** Windows 命令行总长度上限约 8191 字符，几千字的 JSON 内容加上 python 路径、token 等很容易超限，导致命令执行失败或内容被截断。`--file` 从文件读取内容，完全绕过此限制。
>
> **注意**：如果中途中断（如 Ctrl+C），请手动执行 `Remove-Item tests\tmp\publish_result.json` 清理残留文件。

### get — 读取信息

```bash
python scripts/vnpy_command.py [--token TOKEN] get <用户名> <key路径>
```

读取 Redis 中指定 key 的值（GET 操作）。key 路径规则同 publish：

- **带用户前缀**：key_path 不以 `/` 开头，完整 key = `vnpy:{username}:{key_path}`
- **全局 key**：key_path 以 `/` 开头，完整 key = 去掉 `/` 后的内容

> **注意**：读写以 `/` 开头的全局 key 时，`<用户名>` 参数不会被使用，可以传任意占位符（如 `-`）代替真实用户名。

示例：
```bash
# 读取带用户前缀的分析结果
python scripts/vnpy_command.py --token tiger-code-123456 get 楠总1号 analysis:result

# 读取全局 key
python scripts/vnpy_command.py --token tiger-code-123456 get 楠总1号 /global:config
# 等价于 Redis GET global:config

# 读取大盘分析缓存（全局 key，不归属用户）
python scripts/vnpy_command.py --token tiger-code-123456 get 楠总1号 /vnpy:加密货币:大盘与板块和资金流向分析
# 等价于 Redis GET vnpy:加密货币:大盘与板块和资金流向分析
```

---

## 输出说明

### ConID 查询（`conid --wait`）

正向查询输出：
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

反向查询输出（`--reverse`）：
```
✅ 265598  合约反查成功
=======================================================
  合约 ID (conId):     265598
  股票代码 (symbol):   AAPL
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

ConID 查询（正向）：
```json
{"type": "query_conid", "symbol": "AAPL", "mode": "forward", "exchange": "SMART", "currency": "USD", "response_key": "vnpy:user:conid_result:AAPL_xxx", "token": "..."}
```

ConID 查询（反向）：
```json
{"type": "query_conid", "symbol": "265598", "mode": "reverse", "exchange": "SMART", "response_key": "vnpy:user:reverse_result:265598_xxx", "token": "..."}
```

策略操作：
```json
{"type": "strategy_cmd", "target_strategy": "MARTIN-AMD", "cmd": "close", "comment": "紧急平仓", "response_key": "vnpy:user:strategy_cmd_result:xxx", "token": "..."}
```

`response_key` 由 `--wait` 自动生成（格式 `vnpy:{user}:{type}:{name}_{uuid8}`），5 分钟有效期。指定 `--response-key` 可覆盖。