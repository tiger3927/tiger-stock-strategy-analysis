# TigerGridTemplate 文档索引

> 面向 AI 智能体的路由文档。接到策略相关问题后，先读本文确定加载哪些子文档。

---

## 一、基类与子类的职责边界（⚠️ 必须首先理解）

```
┌─────────────────────────────────────────────────────────────┐
│                        基类提供                              │
│  TigerGridTemplate 封装了交易执行的全链路基础设施             │
│                                                             │
│  ✅ 订单执行引擎    set_target_pos()→trade()→send_new_order  │
│  ✅ 止盈止损系统    固定比例 + ATR动态 + 移动止盈（三系统并行）│
│  ✅ 网格加减仓机制   四段逻辑：减仓→亏损加仓→盈利加仓→主动开仓  │
│  ✅ AI 调用框架     API 通信调度（发请求、收响应、回调分发）  │
│  ✅ 下单风控链      冷却期/超时/滑点/价格保护/资金上限         │
│  ✅ 交易量计算      get_fixed_volume/get_martin_add_volume    │
│  ✅ 状态上报        _build_status_dict()                      │
│  ✅ 参数安全机制    reset_ai_strategy_params()                 │
├─────────────────────────────────────────────────────────────┤
│                   子类必须扩展（基类不提供）                   │
│                                                             │
│  ❓ 开仓信号判断    —— 完全由子类定义，基类不判断任何信号     │
│  ❓ AI prompt 组装  —— build_openclaw_prompt() 虚方法        │
│  ❓ AI 结果解析     —— on_openclaw_analysis_result() 虚方法  │
│  ❓ 策略阶段定义    —— 基类不预设任何阶段枚举                 │
│  ❓ 自定义指标      —— x_script / y_script 参数              │
│  ❓ 何时下单        —— 子类自己决定何时调用 set_target_pos() │
└─────────────────────────────────────────────────────────────┘
```

> **核心：基类解决"怎么执行"，子类解决"用什么信号、什么时候执行、执行多少"。**

---

## 二、子文档索引

| 编号 | 文件 | 内容 | 行数 | 何时加载 |
|:--|:--|:--|:--:|:--|
| 01 | [`01_architecture.md`](01_architecture.md) | AI 完整/非AI 交互流程、订单生命周期 | ~100 | 需要理解整体架构时 |
| 02 | [`02_parameters.md`](02_parameters.md) | 全部 45 个参数定义 + 默认值 + 分类速查 | ~120 | 查参数含义、AI可修改哪些参数 |
| 03 | [`03_variables_and_state.md`](03_variables_and_state.md) | 全部 15 个运行时变量 + `_build_status_dict()` | ~60 | 查变量含义、上报AI的状态内容 |
| 04 | [`04_ai_interface.md`](04_ai_interface.md) | 4个虚方法 + reset默认值 + AI返回JSON约定 | ~100 | 写子类时需要重写哪些方法 |
| 05 | [`05_stop_profit_loss.md`](05_stop_profit_loss.md) | 止盈止损全套机制（固定/ATR/移动止盈） | ~80 | 问止盈止损怎么触发 |
| 06 | [`06_grid_martin.md`](06_grid_martin.md) | 网格加减仓四段逻辑（减仓→加仓→开仓） | ~100 | 问网格怎么加减仓 |
| 07 | [`07_trade_control.md`](07_trade_control.md) | 交易量计算 + 下单风控链 + 参数联动速查 | ~100 | 问下单流程、风控、成交量 |
| 08 | [`08_extension_guide.md`](08_extension_guide.md) | 注意事项 + 基类与子类关系总结 | ~80 | 写子类时参考、查坑点 |
| 09 | [`09_scenarios.md`](09_scenarios.md) | 典型市场场景的完整 JSON 参数配置 | ~120 | 问"震荡/趋势该设什么参数" |
| 10 | [`10_reversal_handling.md`](10_reversal_handling.md) | 变盘逆势应对策略（5层防御+参数调整+子类骨架） | ~320 | 问"大盘方向变了怎么办"、"持仓反向怎么平仓" |

---

## 三、按问题类型加载

| 用户问题 | 加载文档 |
|:--|:--|
| "趋势做多，给一版 JSON 参数" | 02 + 05 + 07 + **09** |
| "震荡低吸高抛，怎么设参数？" | 02 + 06 + 07 + **09** |
| "大盘方向和持仓反了怎么办？" | **10** + 05 + 06 + 07 |
| "变盘了怎么平仓/减仓？" | **10** + 06 + 07 |
| "loss_close_need_manual 有什么用？" | **10** + 02 + 07 |
| "某个参数 `xx` 是什么意思？" | **02** |
| "止盈止损怎么触发的？" | **05** |
| "ATR 和固定止损有什么区别？" | **05** |
| "网格加仓的条件是什么？" | **06** |
| "set_target_pos 有哪些风控？" | **07** |
| "每笔买卖多少怎么算？" | **07** |
| "怎么写一个子类策略？" | 01 + 04 + 08 |
| "AI 和策略怎么交互的？" | 01 + 04 |
| "状态字典里有什么？" | **03** |
| "xxx 变量是什么意思？" | **03** |
| "基类提供了什么？子类要做什么？" | **08**（或直接看本文第一章） |

---

## 四、快速架构概览

```
子类（信号层）
  ├── 技术指标计算 / AI prompt 组装
  ├── 信号判断（方向、仓位、时机）
  └── 调用 set_target_pos(target_pos)
        │
        ▼
基类（执行层）
  ├── 风控链拦截（时段/冷却/价格/资金）
  ├── do_set_target_pos() 记录目标
  ├── trade() 订单引擎（每tick/bar驱动）
  ├── send_new_order() 发送限价单
  ├── on_order() / on_trade() 确认成交
  ├── profit_loss_stop_action() 每tick止盈止损检查
  └── martin_add_sub() 每bar网格加减仓
```

> 注意：基类在 `trade()` 中，若 `enable_openclaw_confirm_target_pos=True`，会先提交 AI 审核订单，子类不需重写此逻辑。

---

## 五、基类的灵活配置变体

通过参数组合，基类可被配置为多种策略形态（无需修改代码）：

| 策略变体 | 关键参数设置 |
|:--|:--|
| **一次性建仓（不用补仓）** | `first_part=1.0`，`max_position_ratio=1.0` |
| **纯网格防守（不加仓不操作）** | 所有 `enable_martin_*` = False，仅止盈止损运行 |
| **纯趋势跟踪（不网格）** | 所有 `enable_martin_*` = False，ATR 止损止盈 + 移动止盈 |
| **金字塔进攻** | `martin_add_pyramid=True`，`enable_martin_add_loss=True` |
| **AI 全托管** | `enable_openclaw_analysis=True`，`enable_openclaw_confirm_target_pos=True` |
| **完全自主（不依赖 AI）** | `enable_openclaw_analysis=False`，子类内置信号逻辑 |
| **变盘防御（趋势反转时）** | `loss_close_need_manual=False`，`enable_martin_add_loss=False`，`enable_martin_sub=True` |

> 详参见 [`09_scenarios.md`](09_scenarios.md) 各场景完整配置。
