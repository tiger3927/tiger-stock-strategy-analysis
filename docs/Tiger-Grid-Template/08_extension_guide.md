# 08 — 注意事项与关系总结

> 编写子类和调试时的要点，以及基类与子类的完整关系。

---

## 一、注意事项

### 1. 基类不定义策略阶段

`策略阶段`（如：空仓观察/等待首仓/首仓建立中等）由子类根据自己的策略逻辑定义，基类不预设任何阶段枚举。

### 2. `reset_ai_strategy_params()` 是安全阀

每次应用 AI 结果前必须调用，防止上次的开关/数值残留导致意外交易。子类重写时记得 `super()`。

### 3. `_build_status_dict()` 提供快照

输出的状态是调用时刻的快照，不是实时流。AI 应基于快照分析，不考虑调用期间的微小价格变化。

### 4. `on_openclaw_confirm_target_pos_result()` 子类不需重写

这是基类内部处理的另一条交互路径：`enable_openclaw_confirm_target_pos=True` 时，每次调仓前提交 AI 审核，AI 返回 `{"decision": "同意|拒绝", "approved_target_pos": 0.0, "reason": "..."}`。

### 5. `target_pos == self.pos` 是加减仓的闸门

当有其他方向的主要订单正在执行中时（`target_pos ≠ pos`），网格加减仓自动暂停。AI 设置的 target_pos 优先级高于自动化网格。

### 6. `first_open_price` 是网格的基准锚点

网格间距以此价计算，而非当前价。`first_open_price` 在首笔成交时记录，后续加仓不会更改此值。

### 7. `volume_change_with_v=True` 的双刃剑效应

盈利时每次加减仓量变大（加速效应），亏损时每次加减仓量变小（减速效应）。AI 如果希望固定仓位，需建议用户将此参数设为 False。

---

## 二、基类与扩展类（子类）的关系总结

### 基类（TigerGridTemplate）— 引擎

基类封装了交易执行的全链路基础设施，子类无需关心实现细节：

| 基类提供的能力 | 具体内容 |
|:--|:--|
| **订单执行引擎** | `set_target_pos()` → `trade()` → `send_new_order()` → `on_order()/on_trade()` 完整闭环 |
| **止盈止损系统** | 三个独立子系统并行：固定比例、ATR 动态、移动止盈 |
| **网格加减仓系统** | 四段逻辑按序执行：减仓→亏损加仓→盈利加仓→主动开仓 |
| **AI 交互框架** | 两个可选环节：AI 分析 + AI 审核订单 |
| **下单风控链** | 交易时段/仓位对比/冷却期/人工审核/价格区间/追高红线/资金上限 |
| **交易量计算** | 首仓量/网格量/减仓量/最大持仓量统一公式，支持 `volume_change_with_v` |
| **状态上报** | `_build_status_dict()` 构建完整策略快照，供子类注入 AI prompt |
| **参数安全机制** | `reset_ai_strategy_params()` 恢复安全默认值 |

### 子类（扩展类）— 方向盘 + 油门

子类负责策略的"灵魂"——信号来源、决策逻辑和个性化参数：

| 子类需要决定的问题 | 说明 |
|:--|:--|
| **信号来源** | 技术指标？AI 分析？混合（AI 定方向、指标定时机）？ |
| **何时下单** | 什么条件下调用 `set_target_pos()`？bar 触发？特定信号？ |
| **下单多少** | 根据行情和资金计算具体的 `target_pos` |
| **AI 对话协议** | `build_openclaw_prompt()` 告诉 AI 什么？`on_openclaw_analysis_result()` 期望收到什么 JSON？ |
| **策略阶段定义** | 自定义阶段枚举和阶段转换逻辑（基类不预设） |
| **自定义指标** | 通过 `x_script` / `y_script` 定义指标，在 prompt 中引用 |
| **扩展参数** | 在基类 45 个参数之上添加子类特有参数 |

### 一句话总结

> **基类解决"怎么执行"，子类解决"什么时候执行、执行多少、用谁的信号"。**
>
> 基类是高速路、汽车和交通规则——基础设施完整，子类只管打方向盘和踩油门。

### 灵活配置变体

通过参数组合，同一套基类代码可变为多种策略形态：

| 策略变体 | 关键参数 |
|:--|:--|
| **一次性建仓（不补仓）** | `first_part=1.0`, `max_position_ratio=1.0` |
| **纯网格防守** | 所有 `enable_martin_*` = False |
| **纯趋势跟踪** | 所有 `enable_martin_*` = False, ATR 止损止盈 + 移动止盈 |
| **金字塔进攻** | `martin_add_pyramid=True`, `enable_martin_add_loss=True` |
| **AI 全托管** | `enable_openclaw_analysis=True`, `enable_openclaw_confirm_target_pos=True` |
| **完全自主** | `enable_openclaw_analysis=False` |
