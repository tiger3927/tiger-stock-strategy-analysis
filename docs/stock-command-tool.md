# 量化命令发送工具 (stock_command.py)

通过 Redis Proxy (RPUSH) 向量化程序发送操作命令。

---

## 原理

```
key = vnpy:{user}:command:{策略名}
```

将命令 JSON 字符串 **RPUSH** 到该 Redis List 中，量化程序从列表左侧 **LPOP** 取出并执行。命令按先进先出顺序消费。

---

## 配置文件

工具复用 `scripts/setting.json` 中的 Redis Proxy 连接信息：

| 配置项 | 说明 |
|---|---|
| `http_redis_proxy_url` | Redis Proxy 服务地址 |
| `http_redis_proxy_db` | Redis 数据库编号 |
| `http_redis_proxy_apikey` | API 密钥 |

---

## 支持的命令格式

所有命令均为 JSON 格式，必须包含 `cmd` 字段标识命令类型：

| 命令 | cmd 值 | 附加字段 | 说明 |
|------|--------|---------|------|
| 全平 | `close` | `comment` (备注) | 全部平仓 |
| 通知 | `notice` | `comment` (通知内容) | 发送通知消息 |
| 调整目标仓位 | `set_target_pos` | `pos` (目标持仓量), `comment` (备注) | 设置目标持仓 |

**注意**：`pos` 值为正数表示多头，负数表示空头，0 表示空仓。

---

## 全局参数

所有子命令都支持以下全局参数（放在子命令之前）：

| 参数 | 说明 |
|------|------|
| `--token TOKEN` | 可选参数，校验令牌。如有指定，每条命令 JSON 中会自动附加 `"token"` 字段，供接收方校验。 |

示例：

```bash
python scripts/stock_command.py --token "sk-abc123" close 楠总1号 OPENCLAW-NFLX --comment "紧急平仓"
```

发送的命令 JSON 将包含：

```json
{"cmd": "close", "comment": "紧急平仓", "token": "sk-abc123"}
```

---

## 用法

> ⚠️ **重要：所有参数都建议加双引号**
>
> 用户名、策略名、备注等参数中包含中文或特殊字符时，**必须加双引号**，否则可能导致参数解析错误。
>
> 正确示例：`close "楠总1号" "OPENCLAW-NFLX" --comment "紧急平仓"`
>
> 错误示例：`close 楠总1号 OPENCLAW-NFLX --comment 紧急平仓` ❌

### 1. 发送原始 JSON 命令

```bash
python scripts/stock_command.py send <用户名> <策略名> '<命令JSON>'
```

示例：

```bash
python scripts/stock_command.py send 楠总1号 OPENCLAW-NFLX '{"cmd":"close","comment":"紧急平仓"}'
```

### 2. 便捷命令（自动构建 JSON）

#### 全平

```bash
python scripts/stock_command.py close <用户名> <策略名> [--comment TEXT]
```

示例：

```bash
python scripts/stock_command.py close 楠总1号 OPENCLAW-NFLX --comment "紧急平仓"
```

#### 通知

```bash
python scripts/stock_command.py notice <用户名> <策略名> [--comment TEXT]
```

#### 调整目标仓位

```bash
python scripts/stock_command.py set-target-pos <用户名> <策略名> --pos POS [--comment TEXT]
```

示例（设置空头仓位 -2.0）：

```bash
python scripts/stock_command.py set-target-pos 楠总1号 OPENCLAW-NFLX --pos -2.0 --comment "调整仓位"
```

### 3. 查看待处理命令

```bash
python scripts/stock_command.py list <用户名> <策略名>
```

示例：

```bash
python scripts/stock_command.py list 楠总1号 OPENCLAW-NFLX
```

输出示例：

```
命令队列: vnpy:楠总1号:command:OPENCLAW-NFLX
待处理命令数: 2

  [1] {"cmd": "close", "comment": "紧急平仓"}
  [2] {"cmd": "set_target_pos", "pos": -2.0, "comment": "调整仓位"}
```

### 4. 清空命令队列

```bash
python scripts/stock_command.py clear <用户名> <策略名>
```

示例：

```bash
python scripts/stock_command.py clear 楠总1号 OPENCLAW-NFLX
```

---

## 完整使用示例

```bash
# 查看帮助
python scripts/stock_command.py --help

# 调整目标仓位为空头（不带 token）
python scripts/stock_command.py set-target-pos 楠总1号 OPENCLAW-NFLX --pos -2.0 --comment "看空调整"

# 紧急全平（带 token 校验）
python scripts/stock_command.py --token "sk-abc123" close 楠总1号 OPENCLAW-NFLX --comment "紧急平仓"

# 检查待处理命令
python scripts/stock_command.py list 楠总1号 OPENCLAW-NFLX

# 发送通知（带 token）
python scripts/stock_command.py --token "sk-abc123" notice 楠总1号 OPENCLAW-NFLX --comment "注意止盈止损"

# 发送任意原始命令
python scripts/stock_command.py send 楠总1号 OPENCLAW-NFLX '{"cmd":"set_target_pos","pos":0,"comment":"空仓"}'

# 清空队列
python scripts/stock_command.py clear 楠总1号 OPENCLAW-NFLX
```