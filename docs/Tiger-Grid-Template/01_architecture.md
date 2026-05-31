# 01 — 协作架构与交易流程

> 说明基类、子类、AI 之间的协作关系和订单生命周期。

---

## 一、基类与 AI 的协作架构

AI 交互包含**两个可选环节**，由开关独立控制：

| 环节 | 控制开关 | 触发时机 | 作用 |
|:--|:--|:--|:--|
| ① AI 分析 | `enable_openclaw_analysis` | 每 `openclaw_main_interval` 分钟 | AI 分析市场环境，返回策略建议 |
| ② AI 审核订单 | `enable_openclaw_confirm_target_pos` | 每次 `set_target_pos()` 调用 | AI 审核每笔调仓，同意/拒绝 |

两个开关均关闭时，策略完全按子类的内置逻辑自主运行。

---

## 二、完整 AI 流程（两个环节均开启）

```
AI 返回 JSON                                      ← 环节①：AI 分析
      │                                              enable_openclaw_analysis = True
      ▼
子类重写 on_openclaw_analysis_result(result)
      │
      ├── reset_ai_strategy_params()             清洗上一轮参数
      ├── 解析 JSON，提取 AI 建议
      └── 调用基类方法设置参数
            │
            ▼
      set_target_pos() → do_set_target_pos()    ① 设定目标仓位
            │                                         仅记录 target_pos，不立即下单
            ▼
      on_tick() / on_bar()  →  trade()          ② 订单执行引擎
            │     │                                  检查冷却期、超时、滑点
            │     └── 若 enable_openclaw_confirm_target_pos = True
            │          则提交 AI 审核订单               ← 环节②：AI 审核订单
            ▼
      send_new_order()                           ③ 发送限价单
            │
            ▼
      on_order() / on_trade()                    ④ 订单确认回调
            │                                         更新 pos、poschange、pos_price
            ▼
      profit_loss_stop_action()                  ⑤ 止盈止损检查（每 tick 执行）
      martin_add_sub()                            ⑥ 网格加减仓（每 bar 执行）
      calc_profit_loss_stop_price()               ⑦ 止盈止损价计算（每 bar 执行）
```

---

## 三、非 AI 模式（独立自主运行）

```
子类策略有自己的内置条件逻辑                                ← 环节①关闭
      │                                                     enable_openclaw_analysis = False
      ▼
子类直接调用 set_target_pos() 或设置参数
      │
      ▼
（后续流程与 AI 模式相同）
      │
      ▼
      trade() → send_new_order()                  不经过 AI 审核
      on_order() / on_trade()                     直接执行
      止盈止损/网格加减仓                          完全自主
```

---

## 四、核心虚方法分工

| 方法 | 归属 | 作用 |
|:--|:--|:--|
| `build_openclaw_prompt(tick) → dict` | 子类重写 | 组装发给 AI 的 prompt（指标数据、市场状态） |
| `on_openclaw_analysis_result(result)` | 子类重写 | 解析 AI 返回的 JSON，设置策略参数 |
| `reset_ai_strategy_params()` | 子类重写（先 super()） | 每次应用 AI 结果前将参数恢复为安全默认值 |
| `_build_status_dict()` | 基类提供，子类可扩展 | 构建当前策略状态字典，注入 prompt 供 AI 参考 |
| `on_openclaw_confirm_target_pos_result()` | 基类内部处理 | AI 订单审核回调，子类不需重写 |

---

## 五、订单生命周期

| 步骤 | 方法 | 功能 | 时机 |
|:--|:--|:--|:--|
| ① 设定目标 | `set_target_pos()` | 风控校验 → `do_set_target_pos()` 记录 target_pos | 子类调用（收到信号或 AI 响应时） |
| ② 执行订单 | `trade()` | 检查冷却期 → 检查超时/滑点 → 满足条件则 `send_new_order()` | `on_tick()` 每秒一次，`on_bar()` 每分钟一次 |
| ③ 发送限价 | `send_new_order()` | 计算买卖方向，拆单，发限价单到交易所 | `trade()` 内部调用 |
| ④ 确认成交 | `on_order()` → `on_trade()` | 更新 pos、pos_price、poschange（网格记录） | 交易所返回成交时触发 |

**关键设计：步骤 ① 和 ② 是异步解耦的。** `set_target_pos()` 只设定目标，实际下单在 `trade()` 中由 tick/bar 驱动。这意味着：
- 当期价超出滑点容忍范围时，订单可能被取消而不执行
- 当有活跃订单未成交时，`trade()` 不会发新单
- 子类可以在一个响应中多次调用 `set_target_pos()`，只有最后一次生效
