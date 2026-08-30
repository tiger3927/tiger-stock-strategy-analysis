# 01 — 协作架构与交易流程

> 说明 AI 的分析结论如何落到策略参数上（协作关系），以及调仓指令的执行生命周期（何时真正下单）。

---

## 一、基类与 AI 的协作架构

AI 交互包含**两个可选环节**，由开关独立控制（均为人工作业层控制参数，人工部署时设定，智能体不可设置，见 02 第七节）：

| 环节 | 控制开关 | 触发时机 | 作用 |
|:--|:--|:--|:--|
| ① AI 分析 | `enable_openclaw_analysis`（人工设定启动智能体分析） | 每 `openclaw_main_interval`（人工设定的分析定时间隔）分钟 | AI 分析市场环境，返回策略建议 |
| ② AI 审核订单 | `enable_openclaw_confirm_target_pos`（人工设定启动智能体审核交易订单） | 每次 `set_target_pos()` 调用 | AI 审核每笔调仓，同意/拒绝 |

两个开关均关闭时，策略完全按其内置信号自主运行（智能体不介入）。

---

## 二、完整 AI 流程（两个环节均开启）

```
AI 返回 JSON                                      ← 环节①：AI 分析
      │                                              enable_openclaw_analysis = True
      ▼
策略代码解析 AI 结果（on_openclaw_analysis_result）
      │
      ├── reset_ai_strategy_params()             清洗上一轮参数
      ├── 解析 JSON，提取 AI 建议
      └── 调用基类方法设置参数
            │
            ▼
      set_target_pos()                          ① 风控校验 + 设定目标仓位
            │     │
            │     └── 若 enable_openclaw_confirm_target_pos = True
            │          先提交 AI 审核（环节②），同意后回调
            │          on_openclaw_confirm_target_pos_result() 才执行
            │          do_set_target_pos() 记录 target_pos，不立即下单
            ▼
      on_tick() / on_bar()  →  trade()          ② 订单执行引擎
            │                              检查冷却期、超时、滑点
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
策略有自己内置的信号/条件逻辑                                ← 环节①关闭
      │                                                     enable_openclaw_analysis = False
      ▼
策略代码直接调用 set_target_pos() 或设置参数
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

## 四、订单生命周期

| 步骤 | 方法 | 功能 | 时机 |
|:--|:--|:--|:--|
| ① 设定目标 | `set_target_pos()` | 风控校验（+ AI 审核，若开启）→ `do_set_target_pos()` 记录 target_pos | 策略代码调用（收到信号或 AI 分析响应时） |
| ② 执行订单 | `trade()` | 检查冷却期 → 检查超时/滑点 → 满足条件则 `send_new_order()` | `on_tick()` 每秒一次，`on_bar()` 每分钟一次 |
| ③ 发送限价 | `send_new_order()` | 计算买卖方向，拆单，发限价单到交易所 | `trade()` 内部调用 |
| ④ 确认成交 | `on_order()` → `on_trade()` | 更新 pos、pos_price、poschange（网格记录） | 交易所返回成交时触发 |

**关键设计：步骤 ① 和 ② 是异步解耦的。** `set_target_pos()` 只设定目标，实际下单在 `trade()` 中由 tick/bar 驱动。这意味着：
- 当期价超出滑点容忍范围或等待超时时，本次调仓被放弃（target_pos 回写为当前持仓）
- 当有活跃订单未成交时，`trade()` 等待冷却期后撤旧单，下一 tick 再按最新目标重新下单
- 策略代码可以在一轮处理中多次调用 `set_target_pos()`，目标被最新值覆盖（重复提交相同目标无效）
