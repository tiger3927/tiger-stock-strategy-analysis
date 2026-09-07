# vnpy 量化系统全局配置

**用途**：智能体查询/解答/修改 vnpy 量化系统全局配置参数的知识手册。
当用户问"某配置项是什么/默认值/怎么改/改了有什么影响"，或要求修改全局配置时，以本文档为准。

## 配置文件与修改入口

- 文件：`.vntrader/vt_setting.json`（JSON 键值对，项目运行目录下）
- 修改入口：主窗口菜单栏 → **配置**（全局配置对话框，左侧按本文 7 个分类的可折叠分组编辑，确定后写回 vt_setting.json；顶部另有 Goose LLM 配置组 + "设置模型并重启goose"按钮，管的是 `goose/llm.json`，与本文参数无关）
- **所有修改必须重启主程序才生效**：`SETTINGS` 全局字典在 `vnpy/trader/setting.py` import 时一次性加载
- 直接改 JSON 文件也可以，但注意保持合法 JSON（bool 用 `true/false`，不带引号；字符串带引号）

## 参数清单

### 一、界面与日志（vnpy 标准）

| 参数 | 类型 | 默认值 | 当前值 | 说明 |
|---|---|---|---|---|
| `font.family` | str | 微软雅黑 | 微软雅黑 | 全局 UI 字体 |
| `font.size` | int | 12 | 10 | 全局 UI 字号 |
| `log.active` | bool | true | true | 日志系统总开关 |
| `log.level` | int | 20 | 10 | 日志级别：10=DEBUG / 20=INFO / 30=WARNING / 40=ERROR（当前 DEBUG，日志量大） |
| `log.console` | bool | true | true | 输出到终端 |
| `log.file` | bool | true | true | 写入 `.vntrader/log/` |

### 二、邮件（vnpy 标准）

| 参数 | 类型 | 默认值 | 当前值 | 说明 |
|---|---|---|---|---|
| `email.server` | str | smtp.qq.com | smtp.qq.com | SMTP 服务器（主引擎测试邮件用） |
| `email.port` | int | 465 | 465 | SMTP 端口 |
| `email.username` / `email.password` | str | "" | "" | SMTP 账号/密码 |
| `email.sender` / `email.receiver` | str | "" | "" | 发件/收件人 |

### 三、数据源（vnpy 标准）

| 参数 | 类型 | 默认值 | 当前值 | 说明 |
|---|---|---|---|---|
| `datafeed.name` | str | "" | rqdata | 数据源名称（空=不启用；当前 rqdata） |
| `datafeed.username` / `datafeed.password` | str | "" | license/… | 数据源账号/授权 |

### 四、数据库（vnpy 标准）

| 参数 | 类型 | 默认值 | 当前值 | 说明 |
|---|---|---|---|---|
| `database.timezone` | str | 本地时区 | Asia/Shanghai | 数据库时区 |
| `database.name` | str | sqlite | sqlite | 数据库类型 |
| `database.database` | str | database.db | vnpy | 数据库名 |
| `database.host` / `port` / `user` / `password` | — | 空 | 空 | sqlite 模式下不使用 |

### 五、AI 分析链路（项目扩展，核心）

| 参数 | 类型 | 默认值 | 当前值 | 说明 |
|---|---|---|---|---|
| `use_goose` | bool | false | **true** | AI 分析传输层总开关：true=goose ACP 直连（不走 OpenClaw 桥）；false=OpenClaw HTTP 桥 |
| `goose_api_url` | str | http://127.0.0.1:3284 | 未设置（默认） | goose serve ACP 端点（use_goose=true 时生效） |
| `goose_api_timeout` | int | 900 | 未设置（默认） | goose ACP 请求超时（秒） |
| `openclaw_api_url` | str | http://localhost:11399 | http://localhost:11399 | OpenClaw 服务地址（**use_goose=false 时才用**） |
| `openclaw_api_timeout` | int | 900 | 900 | AI 请求超时（秒），goose/OpenClaw 两条路径都用它 |
| `openclaw_api_concurrency` | int | 5 | 5 | OpenClaw 路径最大并发请求数（信号量限流） |

**链路关系（重要）**：

```
use_goose=true  → 数据引擎直连 goose serve (3284)，会话级注入 vnpy_mcp，
                  openclaw_api_url / openclaw_api_concurrency 不参与（timeout 仍用 openclaw_api_timeout）
use_goose=false → 走 OpenClaw 服务 (openclaw_api_url)，经 quart_server 桥 (11399)
```

- 切换 `use_goose` 后，已有的 goose 会话/连接不会自动切换，**必须重启主程序**
- goose 家目录约定：`<项目根>\goose\`（AGENTS.md / .goosehints / .agents\skills 按会话 cwd 自动加载）

### 六、数据代理（项目扩展）

| 参数 | 类型 | 默认值 | 当前值 | 说明 |
|---|---|---|---|---|
| `http_redis_proxy_url` | str | https://ai4.newgoai.com/ | https://ai4.newgoai.com/ | Redis HTTP 代理服务地址（数据引擎读写 Redis 的通道） |
| `http_redis_proxy_db` | int | 11 | 11 | Redis db 编号 |
| `http_redis_proxy_apikey` | str | nokey | nokey | 代理 API 密钥 |

### 七、MCP 认证（项目扩展）

| 参数 | 类型 | 默认值 | 当前值 | 说明 |
|---|---|---|---|---|
| `userid` | str | "" | tiger-code | 用户账号标识：Redis 数据引擎的账号键 + AI 提示词中的"用户账号" |
| `command_token` | str | "" | tiger-code-123456 | MCP **全权限**令牌（查询+操作），Bearer 认证 |
| `query_token` | str | "" | tiger-query-123456 | MCP **只读**令牌（C 类操作工具会被拦截并提示需 command_token） |

- 两个 token 都未配置 → MCP 服务无认证模式（不推荐）
- 外部智能体（goose 会话注入 vnpy_mcp 时）使用的是 `command_token`
- 改 token 后：MCP 服务重启生效；**已建立的 goose 会话注入的是旧 token，需新建会话**才用新 token

## 修改注意事项

1. **重启生效**：所有参数在 import 时加载，改完必须重启主程序
2. **对话框控件类型**（自研对话框，不可能输错格式）：bool→复选框、int→数字框（带范围）、日志级别/时区/数据库类型→下拉框、token/密码→密码回显、字体→系统字体下拉；**直接改 JSON 文件时**注意合法格式（bool 用 `true/false` 不带引号）
3. **敏感项**：`command_token`/`query_token`/`datafeed.password`/`http_redis_proxy_apikey` 是凭据，回答用户时不要全文复述
4. **常见问答**：
   - "日志太多" → `log.level` 调 20（INFO）
   - "AI 分析不走 goose / 走了 OpenClaw" → 查 `use_goose`
   - "外部智能体连不上 MCP / 401" → 查 `command_token` 是否配置、是否改过（旧 goose 会话需重建）
