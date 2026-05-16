# 金融分析与建议任务 — 智能金字塔策略 v3

***

# 1. 策略基本定义

## 策略 Name

```text
pyramiding_trend_following
```

***

## 策略 Type

```text
趋势跟随 / 动态仓位管理 / 递减加仓策略
```

***

## 策略 Goal

在明确趋势行情中：

- 自动识别趋势
- 自动判断开仓时机
- 自动执行递减加仓
- 自动动态止损
- 自动退出趋势

实现：

```text
让盈利头寸扩大，
让亏损保持极小。
```

***

# 2. 策略核心理念

## 2.1 只加盈利仓位（Add To Winners）

允许：

```text
盈利后继续加仓
```

禁止：

```text
亏损补仓
摊平成本
马丁格尔
```

***

## 2.2 趋势优先

只有：

```text
趋势明确
趋势持续
趋势增强
```

才允许：

- 建仓
- 加仓
- 持有

***

## 2.3 风险优先级最高

优先级：

```text
风险控制
>
趋势判断
>
加仓逻辑
>
利润目标
```

***

# 3. 市场适用性

***

## 3.1 市场适配度

| 市场         | 适配度 | 说明          | 参数建议                  |
| ---------- | --- | ----------- | --------------------- |
| **加密货币**   | 极高  | 强趋势 + 高波动   | target\_vol=20%, 止损放宽 |
| **期货**     | 极高  | 趋势明显 + 流动性好 | 标准参数                  |
| **美股（趋势）** | 高   | 大盘股趋势稳定     | target\_vol=12%       |
| **外汇**     | 高   | 主要货币对趋势     | target\_vol=10%       |
| **A 股**    | 中高  | 政策市 + 高波动   | target\_vol=18%, 收紧止损 |
| **商品期货**   | 高   | 周期性强        | 标准参数                  |

***

## 3.2 不适用场景

| 市场/策略        | 原因      | 建议      |
| ------------ | ------- | ------- |
| **高频交易**     | 策略周期不匹配 | 使用其他策略  |
| **震荡套利**     | 策略逻辑相反  | 使用均值回归  |
| **做市策略**     | 目标不同    | 使用做市策略  |
| **极短线（<1m）** | 噪音过多    | 使用更大周期  |
| **低流动性小盘股**  | 滑点过大    | 避免或大幅减仓 |

***

## 3.3 市场特征对比

| 特征      | Crypto | Futures | Stocks | FX  |
| ------- | ------ | ------- | ------ | --- |
| 波动率     | 极高     | 中高      | 中      | 低   |
| 趋势性     | 强      | 强       | 中强     | 中   |
| 流动性     | 分化     | 高       | 高      | 极高  |
| 7x24 交易 | 是      | 否       | 否      | 部分  |
| 杠杆      | 高      | 高       | 低      | 高   |
| 适配度     | 95%    | 90%     | 85%    | 80% |

***

## 3.4 推荐配置

### 加密货币（BTC/ETH）

```python
target_volatility = 0.20      # 20% 目标波动率
initial_risk_pct = 0.015      # 1.5% 单笔风险
stop_atr_multiple = 2.5       # 2.5ATR 止损
max_layers = 4                # 4 层加仓
adx_threshold = 28            # 提高至 28（过滤假信号）
```

### 期货（股指/商品）

```python
target_volatility = 0.15      # 15% 目标波动率
initial_risk_pct = 0.01       # 1% 单笔风险
stop_atr_multiple = 2.0       # 2ATR 止损
max_layers = 4                # 4 层加仓
adx_threshold = 25            # 标准
```

### 美股（大盘趋势）

```python
target_volatility = 0.12      # 12% 目标波动率
initial_risk_pct = 0.01       # 1% 单笔风险
stop_atr_multiple = 2.0       # 2ATR 止损
max_layers = 3                # 3 层加仓（保守）
adx_threshold = 25            # 标准
```

### 外汇（主要货币对）

```python
target_volatility = 0.10      # 10% 目标波动率
initial_risk_pct = 0.008      # 0.8% 单笔风险
stop_atr_multiple = 1.8       # 1.8ATR 止损（波动小）
max_layers = 4                # 4 层加仓
adx_threshold = 22            # 降低至 22（外汇趋势弱）
```

***

## 3.5 最佳实践

### 高适配度市场特征

```
✅ 强趋势性（ADX 经常>25）
✅ 良好流动性（滑点小）
✅ 连续性（少跳空）
✅ 波动率适中（5%-30%）
```

### 低适配度市场特征

```
❌ 高频震荡（ADX 经常<20）
❌ 流动性差（滑点大）
❌ 频繁跳空（缺口多）
❌ 波动率极端（<5% 或>50%）
```

***

***

# 4. 指标体系

策略**指标**是

| 指标      | 用途              |
| ------- | --------------- |
| EMA     | 趋势方向            |
| ATR     | 波动率 / 止损 / 加仓距离 |
| ADX     | 趋势强度            |
| +DI/-DI | 多空方向            |
| Volume  | 趋势确认            |
| RSI（可选） | 动能确认            |

策略**最适合的主操作周期**是：

| 市场类型          | **推荐主操作周期**           | 理由                    |
| ------------- | --------------------- | --------------------- |
| **加密货币**      | **15分钟 / 30分钟 / 1小时** | 波动大，趋势启动快，需要较灵敏的入场与加仓 |
| **期货（商品/股指）** | **1小时 / 4小时**         | 趋势持续性好，噪音适中           |
| **美股（趋势股）**   | **1小时 / 日线**          | 避免日内噪音，日线趋势更可靠        |
| **外汇**        | **4小时 / 日线**          | 趋势较慢，短周期假信号多          |

***

# 5. 参数定义（Inputs）

## 趋势参数

| 参数             | 默认值 |
| -------------- | --- |
| ema\_period    | 20  |
| atr\_period    | 14  |
| adx\_period    | 14  |
| adx\_threshold | 25  |

***

## 风险参数

| 参数                    | 默认值 | 说明        |
| --------------------- | --- | --------- |
| initial\_risk\_pct    | 1%  | 单笔风险      |
| max\_total\_risk\_pct | 4%  | 总风险上限     |
| stop\_atr\_multiple   | 2   | 止损 ATR 倍数 |
| max\_layers           | 4   | 最大加仓层数    |
| target\_volatility    | 15% | 目标波动率（年化） |
| vol\_lookback         | 20  | 波动率计算周期   |

***

## 加仓参数

| 参数                     | 默认值  | 说明        |
| ---------------------- | ---- | --------- |
| add\_position\_atr     | 2    | ATR 推进距离  |
| pyramid\_decay         | 0.7  | 仓位递减系数    |
| min\_profit\_threshold | 1.5R | 最小浮盈阈值才加仓 |
| max\_layers\_trend     | 4    | 强趋势最大层数   |
| max\_layers\_valid     | 2    | 有效趋势最大层数  |

***

# 6. 内部状态（State）

| 状态                | 说明     |
| ----------------- | ------ |
| trend\_state      | 当前趋势状态 |
| current\_layer    | 当前层数   |
| avg\_entry\_price | 平均持仓成本 |
| total\_position   | 当前总仓位  |
| stop\_loss\_price | 当前止损   |
| highest\_price    | 持仓后最高价 |
| unrealized\_pnl   | 当前浮盈   |
| last\_add\_price  | 上次加仓价  |

***

# 7. 趋势识别逻辑（Trend Detection）

***

## 7.1 分级趋势确认体系

采用三级确认，避免过度过滤优质趋势。

***

### Level 1：强趋势（可全力加仓）

满足以下**全部 4 个条件**：

| 条件     | 公式                  | 说明       |
| ------ | ------------------- | -------- |
| 价格位置   | Price > EMA         | 在趋势线上方   |
| EMA 斜率 | EMA\_t > EMA\_{t-1} | EMA 向上倾斜 |
| 趋势强度   | ADX \ge 25          | 强趋势      |
| 方向确认   | +DI > -DI           | 多头主导     |

适用操作：

```text
- 可执行全部加仓层级
- 可使用标准仓位
- 止损可适度放宽
```

***

### Level 2：有效趋势（可建仓/轻度加仓）

满足以下**3 个核心条件**：

| 条件   | 公式          | 说明           |
| ---- | ----------- | ------------ |
| 价格位置 | Price > EMA | 在趋势线上方       |
| 趋势强度 | ADX \ge 20  | 有效趋势（放宽至 20） |
| 方向确认 | +DI > -DI   | 多头主导         |

**不要求** EMA 斜率向上（允许 EMA 走平）

适用操作：

```text
- 可建立初始仓位
- 只允许加 1-2 层（不超过 max_layers 的 50%）
- 止损需更紧（ATR × 1.5 而非 2）
- 浮盈达到 1.5R 后才考虑加仓
```

***

### Level 3：弱趋势（只持有，不加仓）

满足以下**2 个条件**：

| 条件   | 公式          |
| ---- | ----------- |
| 价格位置 | Price > EMA |
| 方向确认 | +DI > -DI   |

但：

```text
- ADX < 20（趋势弱）
- 或 EMA 走平
```

适用操作：

```text
- 只持有已有仓位
- 禁止新开仓
- 禁止加仓
- 收紧止损至 ATR × 1
```

***

## 7.2 EMA 双线确认（高波动市场可选）

在加密货币等高波动市场，使用双 EMA 过滤假信号：

| 条件   | 公式            |
| ---- | ------------- |
| 快线位置 | Price > EMA20 |
| 双线排列 | EMA20 > EMA50 |
| 趋势强度 | ADX \ge 25    |
| 方向确认 | +DI > -DI     |

优点：

```text
- 减少假突破
- 趋势确认更可靠
```

缺点：

```text
- 信号延迟增加
- 可能错过早期趋势
```

***

## 趋势状态定义

| 状态      | 条件           | 操作      |
| ------- | ------------ | ------- |
| trend   | Level 1 强趋势  | 全力加仓    |
| valid   | Level 2 有效趋势 | 建仓/轻度加仓 |
| weak    | Level 3 弱趋势  | 只持有     |
| invalid | 无趋势          | 平仓/观望   |

***

## 7.3 趋势置信度评分系统（新增）

从"规则系统"升级为"概率系统"的核心。

***

### 置信度评分表

| 条件                       | 分数     | 说明         |
| ------------------------ | ------ | ---------- |
| **价格位置**                 | <br /> | <br />     |
| Price > EMA              | +20    | 基础趋势条件     |
| Price > EMA20 > EMA50    | +10    | 双线排列       |
| **EMA 斜率**               | <br /> | <br />     |
| EMA 向上倾斜                 | +15    | 趋势方向确认     |
| EMA 斜率 > 阈值              | +5     | 强斜率加分      |
| **ADX 强度**               | <br /> | <br />     |
| ADX > 25                 | +25    | 强趋势        |
| ADX > 30                 | +10    | 额外加分       |
| ADX 上升（t > t-1）          | +15    | 趋势增强       |
| **成交量**                  | <br /> | <br />     |
| Volume > MA(Vol,20)\*1.5 | +10    | 放量确认       |
| Volume 持续放大              | +5     | 连续 3 根 K 线 |
| **多周期同步**                | <br /> | <br />     |
| 高周期趋势一致                  | +15    | 日线/4H 同步   |
| **动量确认**                 | <br /> | <br />     |
| RSI 在 50-70 区间           | +10    | 健康动能       |
| MACD 金叉                  | +5     | 辅助确认       |
| **扣分项**                  | <br /> | <br />     |
| RSI > 80（超买）             | -15    | 警惕回调       |
| RSI < 30（超卖）             | -15    | 趋势可能反转     |
| 长上影线                     | -10    | 抛压信号       |
| ATR 暴涨 > 50%             | -20    | 波动率异常      |

***

### 置信度分级

| 总分     | 等级 | 状态    | 操作策略           |
| ------ | -- | ----- | -------------- |
| 80-100 | A+ | 强趋势   | 全仓策略，4 层加仓     |
| 60-80  | A  | 有效趋势  | 70% 仓位，2-3 层加仓 |
| 40-60  | B  | 弱趋势   | 30% 仓位，禁止加仓    |
| 20-40  | C  | 震荡    | 禁止开仓，持有观望      |
| <20    | D  | 无效/危险 | 平仓退出           |

***

### 置信度计算示例

```python
def calculate_confidence(market_data):
    score = 0
    
    # 基础趋势（+20）
    if price > ema:
        score += 20
    
    # EMA 斜率（+15）
    if ema_slope > 0:
        score += 15
    
    # ADX 强度（+25/+35）
    if adx > 30:
        score += 35
    elif adx > 25:
        score += 25
    
    # ADX 上升（+15）
    if adx > adx_prev:
        score += 15
    
    # 成交量（+10/+15）
    if volume > ma_volume * 1.5:
        score += 10
        if volume > volume_prev:
            score += 5
    
    # 多周期（+15）
    if higher_tf_trend == "bullish":
        score += 15
    
    # 超买扣分
    if rsi > 80:
        score -= 15
    
    # 波动率异常扣分
    if atr > atr_prev * 1.5:
        score -= 20
    
    return clamp(score, 0, 100)

# 使用
confidence = calculate_confidence(market_data)

if confidence >= 80:
    max_layers = 4
    position_size_multiplier = 1.0
elif confidence >= 60:
    max_layers = 2
    position_size_multiplier = 0.7
elif confidence >= 40:
    max_layers = 0  # 禁止加仓
    position_size_multiplier = 0.3
else:
    # 禁止开仓
    disable_trading()
```

***

## 7.4 市场状态识别（Market Regime，新增）

识别当前市场环境，动态调整策略。

***

### 市场状态定义

| Regime            | 特征   | ADX   | ATR    | 操作     |
| ----------------- | ---- | ----- | ------ | ------ |
| **Strong Trend**  | 强趋势  | >30   | 正常     | 全策略    |
| **Weak Trend**    | 弱趋势  | 20-30 | 正常     | 半仓     |
| **Choppy**        | 震荡   | <20   | 低      | 禁止     |
| **Volatile**      | 高波动  | 任意    | >90 分位 | 减仓 50% |
| **Panic**         | 恐慌   | 飙升    | 暴涨     | 强制平仓   |
| **Low Liquidity** | 低流动性 | 任意    | 任意     | 禁止     |

***

### Regime 识别逻辑

```python
def identify_market_regime(data):
    
    # 计算指标
    adx = data['adx']
    atr = data['atr']
    atr_rank = percentile_rank(atr, lookback=100)
    vix_change = data['vix_change']
    
    # 恐慌状态（最高优先级）
    if atr_rank > 95 and vix_change > 50%:
        return "panic"
    
    # 低流动性
    if volume < ma_volume * 0.3:
        return "low_liquidity"
    
    # 高波动
    if atr_rank > 90:
        return "volatile"
    
    # 强趋势
    if adx > 30:
        return "strong_trend"
    
    # 弱趋势
    if adx > 20:
        return "weak_trend"
    
    # 震荡
    if adx < 20:
        return "choppy"
    
    return "unknown"
```

***

### Regime 对应的策略调整

| Regime        | 仓位系数 | 最大层数 | 止损倍数    | 开仓条件 |
| ------------- | ---- | ---- | ------- | ---- |
| Strong Trend  | 1.0  | 4    | 2.0 ATR | 标准   |
| Weak Trend    | 0.5  | 2    | 1.5 ATR | 收紧   |
| Choppy        | 0    | 0    | -       | 禁止   |
| Volatile      | 0.5  | 2    | 2.5 ATR | 严格   |
| Panic         | 0    | 0    | -       | 强制平仓 |
| Low Liquidity | 0    | 0    | -       | 禁止   |

***

# 8. 开仓逻辑（Entry Logic）

这是整个系统最核心部分之一。

***

# 8.1 开仓前提

必须满足：

```python
trend_state == "trend"
```

且：

```python
current_position == 0
```

***

# 8.2 开仓触发条件（Entry Trigger）

满足以下任一：

***

## 模式 A：突破开仓（推荐）

### 基础条件

价格突破近期高点：

```python
Price > RecentHigh(N)  # N 通常取 20
```

***

### 量价过滤器（必须满足）

**过滤 1：成交量确认**

```python
Volume > MA(Volume, 20) * 1.5
```

说明：突破时成交量需达到 20 日均量的 1.5 倍以上

***

**过滤 2：收盘价确认（防假突破）**

```python
Close > RecentHigh  # 收盘价站稳新高，而非盘中突破
```

或：

```python
Close > Open + (High - Low) * 0.7  # 收在 K 线上半部分
```

***

**过滤 3：ADX 动能确认**

```python
ADX_t > ADX_{t-1}  # ADX 上升，趋势增强
```

***

### 适合：

```text
强趋势启动阶段
```

### 突破失败特征（出现则放弃）：

```text
- 突破时成交量萎缩
- 长上影线（射击之星）
- 突破后迅速回落至区间内
- ADX 下降或走平
```

***

## 模式B：EMA回踩反弹开仓

满足：

- 价格回踩EMA附近
- 收出反弹K线
- 未跌破EMA
- ADX仍 > 25

适合：

```text
趋势中继行情
```

***

# 8.3 禁止开仓条件

满足任一：

禁止开仓。

***

## 禁止条件

### 1. ADX 过低

```python
ADX < 20
```

说明：

```text
市场可能处于震荡
```

***

### 2. EMA 走平

说明趋势失效。

***

### 3. 波动率异常扩大

例如：

```text
ATR 突然暴涨
```

可能是：

- 财报
- 黑天鹅
- 插针行情

***

### 4. 重大消息窗口

例如：

- FOMC
- CPI
- 财报
- 非农

***

### 5. 风险超限

```python
portfolio_risk >= max_total_risk_pct
```

***

# 8.4 震荡市过滤（增强版）

ADX 有滞后性，需结合其他指标提前过滤。

***

## 过滤 1：Bollinger Band 位置

```python
if Price near BB_Middle:  # 价格在中轨附近
    skip_entry()  # 可能是震荡
```

或：

```python
BB_Width = (BB_Upper - BB_Lower) / BB_Middle
if BB_Width < BB_Width_MA(20) * 0.8:  # BB 收窄
    caution()  # 波动率低，可能是震荡前兆
```

***

## 过滤 2：ATR 历史分位

```python
ATR_Rank = percentile(ATR, lookback=100)
if ATR_Rank < 20:  # ATR 处于历史低位
    caution()  # 可能是震荡或暴风雨前宁静
```

***

## 过滤 3：价格结构

```python
# 检查最近 N 根 K 线是否在高点和低点之间来回
recent_high = max(High[-20:])
recent_low = min(Low[-20:])
range_size = recent_high - recent_low

if range_size < ATR * 3:  # 区间过小
    skip_entry()  # 窄幅震荡
```

***

## 过滤 4：多时间框架确认

```python
# 大周期趋势确认
if higher_timeframe_trend == "choppy":
    reduce_position_size(0.5)  # 减仓
```

***

# 9. 初始仓位计算

***

## 9.1 初始止损

使用 ATR：

StopLoss = EntryPrice - ATR \times StopATRMultiple

***

## 9.2 单笔风险金额

RiskAmount = AccountBalance \times InitialRiskPct

***

## 9.3 仓位大小

PositionSize = \frac{RiskAmount}{EntryPrice - StopLoss}

***

# 10. 加仓逻辑（Pyramiding）

***

# 10.1 加仓条件

必须全部满足：

***

### 条件 1：浮盈达到阈值

```python
unrealized_pnl_pct >= min_profit_threshold  # 建议：1.5R 或 50%
```

说明：

```text
- R = 初始风险（EntryPrice - InitialStop）
- 浮盈必须覆盖 1.5 倍风险后才加仓
- 避免在微利时过早加仓
```

***

### 条件 2：趋势仍有效

```python
trend_state in ["trend", "valid"]  # 强趋势或有效趋势
```

且：

```python
ADX_t >= ADX_{t-1} * 0.9  # ADX 没有明显下降（允许 10% 波动）
```

***

### 条件 3：达到 ATR 推进距离

```python
Price - LastAddPrice >= ATR \times AddPositionATR
```

默认：`AddPositionATR = 2`

***

### 条件 4：未超过最大层数

```python
current_layer < max_layers
```

***

### 条件 5：价格结构健康（K 线过滤）

**禁止加仓的 K 线形态**：

```text
- 射击之星（Shooting Star）
- 看跌吞没（Bearish Engulfing）
- 黄昏之星（Evening Star）
- 长上影线 > 实体 2 倍
```

**健康形态**（优先加仓）

```text
- 光头阳线
- 上升三法
- 旗形整理突破
- 收盘价接近最高价
```

***

# 10.2 加仓仓位递减

NextSize = PreviousSize \times PyramidDecay

***

# 10.3 加仓冷却机制（Cooldown，新增）

防止连续暴涨中过度加仓，控制风险暴露。

***

## 冷却时间定义

```python
cooldown_bars = {
    '5m': 3,   # 5 分钟周期：至少间隔 3 根 K 线（15 分钟）
    '15m': 2,  # 15 分钟周期：至少间隔 2 根 K 线（30 分钟）
    '1h': 2,   # 1 小时周期：至少间隔 2 根 K 线（2 小时）
    '4h': 1,   # 4 小时周期：至少间隔 1 根 K 线（4 小时）
    '1d': 1,   # 日线周期：至少间隔 1 根 K 线（1 天）
}
```

***

## 冷却检查逻辑

```python
def can_add_position(current_state):
    
    # 检查冷却时间
    bars_since_last_add = current_bar - last_add_bar
    
    if bars_since_last_add < cooldown_bars[timeframe]:
        return False, f"冷却期：还需等待 {cooldown_bars[timeframe] - bars_since_last_add} 根 K 线"
    
    # 检查是否连续暴涨
    if consecutive_large_gains >= 3:
        # 暴涨后强制冷却
        return False, "连续暴涨后冷却"
    
    return True, "允许加仓"
```

***

## 连续暴涨定义

```python
# 连续 3 根 K 线涨幅超过阈值
if (price_change[-1] > 3% and 
    price_change[-2] > 3% and 
    price_change[-3] > 3%):
    consecutive_large_gains = 3
    cooldown_bars *= 2  # 冷却时间翻倍
```

***

## 冷却期对应的操作

| 场景          | 冷却时间    | 操作      |
| ----------- | ------- | ------- |
| 正常加仓后       | 标准      | 等待冷却结束  |
| 连续 2 次加仓    | 标准×1.5  | 延长冷却    |
| 连续暴涨（3 根大阳） | 标准×2    | 强制冷却    |
| ATR 暴涨后     | 标准×2    | 等待波动率稳定 |
| 刚止损后        | 5 根 K 线 | 防止报复性交易 |

***

# 10.4 加仓禁止条件

满足任一：

禁止加仓。

***

## 禁止条件

- 当前仓位浮亏
- ADX下降明显
- 价格跌破EMA
- 总风险超限
- 波动率失控
- 趋势结构破坏

***

# 11. 止损逻辑（Stop Loss Logic）

***

# 11.1 初始止损

基于 ATR：

```python
InitialStop = EntryPrice - ATR \times StopATRMultiple
```

默认：`StopATRMultiple = 2`

***

# 11.2 分层止损（不同 Layer 不同止损）

| Layer    | 止损倍数      | 说明            |
| -------- | --------- | ------------- |
| Layer 1  | ATR × 1.5 | 首仓止损最紧，快速验证判断 |
| Layer 2  | ATR × 2.0 | 标准止损          |
| Layer 3+ | ATR × 2.5 | 已盈利保护，可适当放宽   |

***

# 11.3 盈利保护（Breakeven 机制）

当整体浮盈达到阈值时，将止损提至成本价以上：

```python
if unrealized_pnl_pct >= 2.0 * R:  # 盈利达到 2R
    stop_loss_price = avg_entry_price + ATR * 0.5  # 提至成本价上方
```

| 浮盈比例 | 止损调整               |
| ---- | ------------------ |
| 1.0R | 止损提至成本价（Breakeven） |
| 2.0R | 止损提至成本价 + 0.5ATR   |
| 3.0R | 止损提至成本价 + 1.0ATR   |

***

# 11.4 动态止损（Trailing Stop）

随着趋势上涨：

止损同步抬高。

***

## ATR 动态止损

```python
TrailingStop = HighestPrice - ATR \times StopATRMultiple
```

***

## 分层 Trailing Stop（推荐）

不同 Layer 使用不同 Trailing 系数：

```python
# Layer 1: 已盈利，紧止损
TrailingStop_1 = HighestPrice - ATR * 1.5

# Layer 2: 标准
TrailingStop_2 = HighestPrice - ATR * 2.0

# Layer 3+: 已大幅盈利，放宽
TrailingStop_3 = HighestPrice - ATR * 2.5
```

***

# 11.5 EMA 止损

也可使用：

```python
Price < EMA
```

作为趋势失效退出条件。

***

# 11.6 时间止损（新增）

若持仓时间过长且趋势走弱：

```python
if bars_held > N and ADX < 25:  # N 通常取 10-20
    reduce_position(0.5)  # 强制减半
```

或：

```python
if bars_held > M and price_change < 5%:  # M 通常取 30
    exit_position()  # 强制平仓，资金效率过低
```

***

# 11.7 止损触发条件（核心）

满足任一：

立即止损或退出。

***

## 条件 1：价格触发 ATR 止损

```python
Price \le TrailingStop
```

***

## 条件 2：价格有效跌破 EMA

趋势失效。

***

## 条件 3：ADX 快速下降

例如：

```python
ADX < 20
```

说明：

```text
趋势可能结束
```

***

## 条件 4：出现反转结构

例如：

- 双顶
- Head & Shoulders
- 放量长阴
- 跳空反转

***

## 条件 5：时间止损触发

```python
bars_held > max_bars and profit < target
```

***

# 11.8 极端行情保护（新增）

应对黑天鹅、闪崩、插针等极端情况。

***

## 11.8.1 跳空保护（Gap Protection）

```python
# 开盘跳空检测
gap = abs(open_price - prev_close) / prev_close

if gap > ATR * 3 / prev_close:  # 跳空超过 3ATR
    if has_position():
        emergency_exit()  # 紧急退出
    else:
        skip_entry()  # 禁止开仓
```

| 跳空幅度   | 动作       |
| ------ | -------- |
| > 2ATR | 警告，收紧止损  |
| > 3ATR | 强制减仓 50% |
| > 5ATR | 全部平仓     |

***

## 11.8.2 闪崩保护（Flash Crash）

```python
# 短时间内大幅下跌
if (low - close) / close > 5% and volume > MA(volume, 20) * 3:
    # 放量长阴
    emergency_exit()
```

***

## 11.8.3 插针保护（Wick Protection）

```python
# 长下影线（插针）
lower_wick = min(open, close) - low
body = abs(close - open)

if lower_wick > body * 3 and lower_wick > ATR * 0.5:
    # 插针后快速收回，可能是操纵
    tighten_stop_loss()  # 收紧止损
    disable_add_position()  # 禁止加仓
```

***

## 11.8.4 波动率失控保护

```python
# ATR 短时间内暴涨
if ATR > ATR_prev * 2:
    # 波动率翻倍
    regime = "panic"
    reduce_position(0.5)  # 强制减半
    disable_add_position()  # 禁止加仓
```

***

## 11.8.5 熔断机制触发

```python
# 市场熔断（股票/期货）
if market_circuit_breaker_active():
    emergency_exit()  # 全部退出
    disable_trading()  # 禁止交易
```

***

## 11.8.6 极端行情识别表

| 特征     | 阈值        | 动作   |
| ------ | --------- | ---- |
| 跳空     | > 3ATR    | 紧急退出 |
| 闪崩     | 5 分钟跌>5%  | 紧急退出 |
| 插针     | 影线>实体 3 倍 | 收紧止损 |
| ATR 暴涨 | >200%     | 强制减仓 |
| 成交量暴增  | >500%     | 警惕反转 |
| 市场熔断   | 触发        | 全部退出 |

***

# 12. 减仓逻辑（Reduce Position）

满足以下情况可部分减仓：

***

## 减仓条件

- ADX持续下降
- RSI严重超买
- 放量滞涨
- 波动率急剧扩大

***

# 13. 平仓逻辑（Exit Logic）

***

# 13.1 全部平仓条件

满足任一：

```python
trend_state == "invalid"
```

或：

```python
price <= stop_loss_price
```

或：

```python
risk_control_triggered == True
```

***

# 13.2 时间退出

若长期横盘：

```text
主动退出资金效率低的仓位
```

***

# 14. 风险控制（最高优先级）

***

# 14.1 单笔风险限制

```python
single_trade_risk <= 0.02
```

***

# 14.2 总风险限制

```python
portfolio_total_risk <= 0.04
```

***

# 14.3 杠杆限制

```python
effective_leverage <= allowed_leverage
```

***

# 14.4 流动性控制（新增）

实盘执行必需，防止滑点和无法成交。

***

## 14.4.1 买卖价差限制

```python
if spread > max_spread_pct:  # 例如：0.1%
    skip_entry()
    reduce_position_size(0.5)
```

| 市场            | 最大价差  |
| ------------- | ----- |
| 大型股票          | 0.05% |
| 主流期货          | 0.02% |
| 加密货币（BTC/ETH） | 0.1%  |
| 小盘股/山寨币       | 0.2%  |

***

## 14.4.2 订单簿深度检查

```python
if orderbook_depth < min_depth:  # 最小深度（美元）
    reduce_position_size(0.5)

if bid_ask_imbalance > threshold:
    use_limit_order()  # 使用限价单
```

***

## 14.4.3 成交量占比限制

```python
max_order_size = daily_volume * 0.01  # 不超过日成交量 1%

if order_size > max_order_size:
    use_twap_execution()  # 使用 TWAP 分批执行
```

***

# 14.5 决策优先级矩阵（核心）

**解决逻辑冲突**：当多个信号同时触发时，Agent 如何决策。

***

## 优先级定义

| 优先级    | 模块    | 动作   | 触发条件示例                 |
| ------ | ----- | ---- | ---------------------- |
| **P0** | 风险控制  | 强制退出 | Kill Switch 触发         |
| **P1** | 止损触发  | 强制退出 | 价格触及止损                 |
| **P2** | 趋势失效  | 禁止加仓 | Price < EMA + ADX < 20 |
| **P3** | 波动率异常 | 减仓   | ATR 暴涨 > 50%           |
| **P4** | 加仓逻辑  | 允许加仓 | 所有加仓条件满足               |
| **P5** | 盈利目标  | 可选止盈 | 达到目标位                  |

***

## 决策流程（伪代码）

```python
def decide_action(market_data, position_state):
    
    # P0: 全局风险熔断器
    if risk_kill_switch_triggered():
        return emergency_exit()
    
    # P1: 止损检查
    if stop_loss_triggered():
        return exit_position()
    
    # P2: 趋势有效性检查
    if trend_state == "invalid":
        disable_add_position()
        if has_position():
            return reduce_position(0.5)
    
    # P3: 波动率异常检查
    if volatility_explosion_detected():
        if has_position():
            return reduce_position(0.3)
        else:
            return skip_entry()
    
    # P4: 加仓逻辑（仅在以上都未触发时执行）
    if not has_position():
        if entry_conditions_met():
            return open_position()
    else:
        if add_position_conditions_met():
            return add_position()
    
    # P5: 盈利目标（可选）
    if take_profit_reached():
        return reduce_position(0.5)  # 部分止盈
    
    # 默认：持有
    return hold()
```

***

## 冲突处理示例

**场景**：

```text
- ADX 仍 > 25（趋势有效）
- 但 RSI 严重超买（> 80）
- 同时 ATR 暴涨 50%
- 出现长上影 K 线
```

**处理**：

```python
# 按照优先级执行
if atr_explosion:  # P3
    reduce_position(0.3)
    disable_add_position()

# 忽略加仓信号（即使趋势仍有效）
```

***

# 14.6 全局风险熔断器（Kill Switch）

**机构级风控标配**，防止连续亏损和系统性风险。

***

## 14.6.1 日亏损熔断

```python
if daily_drawdown >= 5%:
    disable_all_new_positions()
    if current_position:
        reduce_position(0.5)
```

| 阈值 | 动作           |
| -- | ------------ |
| 3% | 警告，减仓 20%    |
| 5% | 禁止新开仓，减仓 50% |
| 8% | 强制平仓所有头寸     |

***

## 14.6.2 连续亏损熔断

```python
if consecutive_losses >= 5:
    reduce_position_size(50%)
    cooldown_period = 24  # 小时
```

| 连续亏损 | 动作          |
| ---- | ----------- |
| 3 笔  | 仓位减半        |
| 5 笔  | 停止交易 24 小时  |
| 7 笔  | 停止交易 1 周，复盘 |

***

## 14.6.3 周/月亏损熔断

```python
if weekly_drawdown >= 10%:
    disable_trading(days=7)

if monthly_drawdown >= 15%:
    disable_trading(days=30)
    review_strategy()
```

***

# 14.7 Volatility Targeting（波动率目标，新增）

**机构级风险管理核心**，Bridgewater、CTA、Risk Parity 的标准配置。

***

## 14.7.1 核心理念

**问题**：

```text
固定风险（1%）在不同波动率市场不公平：

- 低波动市场：仓位过大，风险不足
- 高波动市场：仓位过小，风险过度
```

**解决方案**：

```text
波动率归一化

让风险在所有市场环境下保持一致
```

***

## 14.7.2 波动率计算

```python
def calculate_realized_volatility(prices, lookback=20):
    """
    计算已实现波动率（年化）
    """
    # 计算收益率
    returns = prices.pct_change().dropna()
    
    # 滚动标准差
    vol_std = returns.rolling(lookback).std()
    
    # 年化（假设 252 交易日）
    annualized_vol = vol_std * np.sqrt(252)
    
    return annualized_vol
```

***

## 14.7.3 波动率调整系数

```python
def calculate_volatility_adjustment(target_vol, realized_vol):
    """
    计算波动率调整系数
    """
    # 避免除以零
    if realized_vol == 0 or np.isnan(realized_vol):
        return 1.0
    
    # 计算调整系数
    vol_ratio = target_vol / realized_vol
    
    # 限制范围（0.5 - 2.0）
    vol_ratio = np.clip(vol_ratio, 0.5, 2.0)
    
    return vol_ratio
```

***

## 14.7.4 动态仓位调整

```python
def adjust_position_by_volatility(base_position, target_vol, realized_vol):
    """
    根据波动率调整仓位
    """
    vol_adjustment = calculate_volatility_adjustment(target_vol, realized_vol)
    
    adjusted_position = base_position * vol_adjustment
    
    return adjusted_position

# 使用示例
base_risk = 0.01  # 基础风险 1%
target_vol = 0.15  # 目标波动率 15%
realized_vol = calculate_realized_volatility(prices, lookback=20)

# 波动率调整后的风险
adjusted_risk = base_risk * (target_vol / realized_vol)
adjusted_risk = np.clip(adjusted_risk, 0.005, 0.02)  # 限制在 0.5%-2%

# 计算仓位
position_size = (account_balance * adjusted_risk) / (entry_price - stop_price)
```

***

## 14.7.5 波动率场景示例

| 场景        | 已实现波动率 | 调整系数 | 仓位调整        |
| --------- | ------ | ---- | ----------- |
| **低波动市场** | 10%    | 1.5  | 加仓 50%      |
| **正常波动**  | 15%    | 1.0  | 标准仓位        |
| **高波动市场** | 20%    | 0.75 | 减仓 25%      |
| **极端波动**  | 30%    | 0.5  | 减仓 50%（下限）  |
| **极低波动**  | 5%     | 2.0  | 加仓 100%（上限） |

***

## 14.7.6 完整流程示例

```python
class VolatilityTargeting:
    def __init__(self, target_volatility=0.15, lookback=20):
        self.target_vol = target_volatility
        self.lookback = lookback
    
    def calculate_position_size(self, account_balance, entry_price, 
                                 stop_price, prices):
        """
        完整的波动率目标仓位计算
        """
        # 1. 计算已实现波动率
        realized_vol = self.calculate_realized_vol(prices)
        
        # 2. 计算波动率调整系数
        vol_adjustment = self.calculate_vol_adjustment(realized_vol)
        
        # 3. 基础风险（调整后）
        adjusted_risk = 0.01 * vol_adjustment  # 基础 1%
        
        # 4. 限制风险范围
        adjusted_risk = np.clip(adjusted_risk, 0.005, 0.02)
        
        # 5. 计算仓位
        risk_amount = account_balance * adjusted_risk
        stop_distance = entry_price - stop_price
        
        position_size = risk_amount / stop_distance
        
        return position_size, adjusted_risk, realized_vol
    
    def calculate_realized_vol(self, prices):
        returns = prices.pct_change().dropna()
        vol = returns.rolling(self.lookback).std()
        annualized_vol = vol * np.sqrt(252)
        return annualized_vol.iloc[-1]
    
    def calculate_vol_adjustment(self, realized_vol):
        if realized_vol == 0:
            return 1.0
        ratio = self.target_vol / realized_vol
        return np.clip(ratio, 0.5, 2.0)
```

***

## 14.7.7 与其他模块的集成

```python
def final_position_size(base_calculation, volatility_targeting, 
                        regime_adjustment, confidence_adjustment):
    """
    综合所有因素的最终仓位
    """
    # 1. 基础仓位（基于止损）
    size = base_calculation
    
    # 2. 波动率调整
    size *= volatility_targeting
    
    # 3. Regime 调整
    size *= regime_adjustment
    
    # 4. 置信度调整
    size *= confidence_adjustment
    
    # 5. 最终检查
    size = np.clip(size, min_size, max_size)
    
    return size
```

***

## 14.7.8 优势总结

| 优势        | 说明                 |
| --------- | ------------------ |
| **风险一致性** | 不同市场风险保持一致         |
| **自动风控**  | 高波动自动减仓            |
| **机会捕捉**  | 低波动自动加仓            |
| **机构级**   | Bridgewater/CTA 使用 |
| **简单有效**  | 计算简单，效果显著          |

***

## 14.8 Agent 行为约束（Behavioral Constraints）

**防止 AI 失控**，明确禁止的行为。

***

## 绝对禁止（NEVER）

```python
NEVER:
    - revenge_trade()  # 报复性交易
    - average_down()   # 亏损补仓
    - remove_stop_loss()  # 移除止损
    - increase_leverage_after_losses()  # 亏损后加杠杆
    - open_position_after_emergency_stop()  # 熔断后开仓
    - override_risk_limits()  # 突破风险限制
    - trade_during_blackout()  # 禁交易期开仓
```

***

## 必须遵守（MUST）

```python
MUST:
    - always_use_stop_loss()  # 必须设止损
    - follow_position_sizing()  # 遵守仓位管理
    - respect_cooldown_period()  # 遵守冷却期
    - log_all_decisions()  # 记录所有决策
    - report_anomalies()  # 上报异常
```

***

## 建议行为（SHOULD）

```python
SHOULD:
    - prefer_limit_orders()  # 优先限价单
    - scale_in_gradually()  # 分批建仓
    - take_partial_profits()  # 部分止盈
    - reduce_after_large_wins()  # 大胜后减仓
```

***

# 15. 状态机（State Machine）

```text
IDLE
  ↓
TREND_DETECTED
  ↓
WAIT_ENTRY
  ↓
INITIAL_ENTRY
  ↓
PYRAMIDING
  ↓
TRAILING_PROFIT
  ↓
REDUCE_POSITION
  ↓
EXIT
  ↓
IDLE
```

***

# 16. 多周期同步确认（新增）

专业趋势系统的标准配置。

***

## 16.1 多周期定义

| 周期         | 用途     | 权重  |
| ---------- | ------ | --- |
| 日线（1D）     | 主趋势方向  | 40% |
| 4 小时（4H）   | 中期趋势确认 | 35% |
| 1 小时（1H）   | 入场时机   | 15% |
| 15 分钟（15M） | 精准执行   | 10% |

***

## 16.2 多周期趋势确认逻辑

```python
def multi_timeframe_confirmation():
    
    # 获取各周期趋势
    daily_trend = get_trend('1D')    # 日线
    h4_trend = get_trend('4H')       # 4 小时
    h1_trend = get_trend('1H')       # 1 小时
    m15_trend = get_trend('15M')     # 15 分钟
    
    # 计算一致性得分
    bullish_count = sum([
        daily_trend == 'bullish',
        h4_trend == 'bullish',
        h1_trend == 'bullish',
        m15_trend == 'bullish'
    ])
    
    # 完全一致（4/4）
    if bullish_count == 4:
        return "strong_bullish", 1.0  # 全仓
    
    # 高度一致（3/4）
    elif bullish_count == 3:
        if daily_trend == 'bullish' and h4_trend == 'bullish':
            return "bullish", 0.7  # 70% 仓位
        else:
            return "weak_bullish", 0.3  # 30% 仓位
    
    # 分歧（2/4）
    elif bullish_count == 2:
        return "neutral", 0.0  # 观望
    
    # 完全相反（0-1/4）
    else:
        return "bearish", -1.0  # 禁止开仓
```

***

## 16.3 多周期共振场景

| 场景       | 日线 | 4H | 1H | 操作           |
| -------- | -- | -- | -- | ------------ |
| **完美共振** | 多  | 多  | 多  | 全仓，4 层加仓     |
| **强趋势**  | 多  | 多  | 空  | 70% 仓位，2-3 层 |
| **趋势中继** | 多  | 空  | 多  | 50% 仓位，1-2 层 |
| **分歧**   | 多  | 空  | 空  | 30% 仓位，禁止加仓  |
| **反转预警** | 空  | 多  | 多  | 观望，准备做空      |
| **完全相反** | 空  | 空  | 空  | 禁止开多         |

***

## 16.4 多周期入场示例

```python
# 理想的多周期入场时机
if (daily_trend == 'bullish' and      # 日线多头
    h4_trend == 'bullish' and         # 4H 多头
    h1_trend == 'bullish' and         # 1H 突破
    m15_trend == 'bullish' and        # 15M 精准入场
    volume_confirmation()):           # 成交量确认
    
    open_position(size=1.0)           # 全仓入场

# 次优但可接受
elif (daily_trend == 'bullish' and
      h4_trend == 'bullish' and
      h1_trend == 'pullback' and      # 1H 回踩
      m15_trend == 'reversal'):       # 15M 反转
    
    open_position(size=0.7)           # 70% 仓位
```

***

# 17. 智能体执行循环（Pseudo Code）

***

## 17.1 主循环

```python
while market_open:

    # 1. 更新数据
    update_market_data()
    
    # 2. 计算指标
    calculate_indicators()
    
    # 3. 识别市场状态（Regime）
    regime = identify_market_regime()
    
    # 4. 多周期确认
    mtf_signal, mtf_score = multi_timeframe_confirmation()
    
    # 5. 计算置信度
    confidence = calculate_confidence()
    
    # 6. 评估趋势
    trend_state = evaluate_trend()
    
    # 7. 检查风控（P0 优先级）
    if risk_kill_switch_triggered():
        emergency_exit()
        continue
    
    # 8. 检查止损（P1 优先级）
    if stop_loss_triggered():
        exit_position()
        continue
    
    # 9. 无仓位时
    if no_position:
        
        # 检查是否允许开仓
        if regime not in ['choppy', 'panic', 'low_liquidity']:
            if confidence >= 60:
                if entry_conditions_met():
                    open_position(size=mtf_score)
    
    # 10. 有仓位时
    else:
        
        # 更新动态止损
        update_trailing_stop()
        
        # 检查加仓（P4 优先级）
        if can_add_position():  # 包含冷却检查
            if add_position_conditions_met():
                add_position()
        
        # 检查减仓（P3 优先级）
        if reduce_conditions_met():
            reduce_position()
        
        # 检查趋势失效（P2 优先级）
        if trend_state == "invalid":
            reduce_position(0.5)
```

***

## 17.2 执行层（Execution Layer，新增）

真实世界交易所需模块。

***

### 17.2.1 订单类型选择

```python
def choose_order_type(signal, market_conditions):
    
    # 紧急退出：使用市价单
    if signal == 'emergency_exit':
        return 'MARKET'
    
    # 正常开仓：使用限价单
    elif signal == 'open_position':
        if spread < max_spread:
            return 'LIMIT'  # 限价单，节省成本
        else:
            return 'MARKET'  # 价差过大，市价单
    
    # 加仓：分批执行
    elif signal == 'add_position':
        return 'TWAP'  # 时间加权平均
    
    # 减仓：部分市价
    elif signal == 'reduce_position':
        return 'PARTIAL_MARKET'
```

***

### 17.2.2 TWAP 执行（时间加权平均）

```python
def twap_execution(total_size, duration_minutes):
    
    # 分成 N 份执行
    num_slices = max(3, duration_minutes // 5)
    slice_size = total_size / num_slices
    interval = duration_minutes / num_slices
    
    for i in range(num_slices):
        place_order(slice_size)
        wait(interval)
        
        # 根据市场情况调整
        if market_conditions_change():
            adjust_remaining_slices()
```

***

### 17.2.3 部分成交处理

```python
def handle_partial_fill(order):
    
    filled = order.filled_size
    remaining = order.total_size - filled
    
    if remaining > 0:
        # 选择：继续等待或取消
        if time_remaining > threshold:
            cancel_order(remaining)
        else:
            wait_for_fill()
```

***

### 17.2.4 滑点控制

```python
def check_slippage(order):
    
    expected_price = order.price
    actual_price = execution_price
    
    slippage = abs(actual_price - expected_price) / expected_price
    
    if slippage > max_allowed_slippage:
        report_anomaly()
        adjust_execution_strategy()
```

***

### 17.2.5 执行总结表

| 场景           | 订单类型      | 执行方式 | 滑点控制 |
| ------------ | --------- | ---- | ---- |
| 紧急退出         | MARKET    | 立即   | 容忍   |
| 正常开仓         | LIMIT     | 分批   | 严格   |
| 加仓           | TWAP      | 多份   | 中等   |
| 减仓           | PARTIAL   | 部分市价 | 中等   |
| 大单（>1% 日成交量） | TWAP/VWAP | 多份   | 严格   |

***

# 18. 输出结构（Outputs）

| 字段             | 类型     | 说明                       |
| -------------- | ------ | ------------------------ |
| action         | string | buy/add/hold/reduce/exit |
| confidence     | float  | 趋势置信度                    |
| position\_size | float  | 建议仓位                     |
| current\_layer | int    | 当前层级                     |
| stop\_loss     | float  | 当前止损                     |
| take\_profit   | float  | 可选止盈                     |
| total\_risk    | float  | 当前风险                     |
| trend\_state   | string | trend/weak/invalid       |
| reason         | string | 动作原因                     |

***

# 18. 输出结构（Outputs）

| 字段                | 类型     | 说明                                              |
| ----------------- | ------ | ----------------------------------------------- |
| action            | string | buy/add/hold/reduce/exit                        |
| confidence        | float  | 趋势置信度（0-100）                                    |
| confidence\_grade | string | A+/A/B/C/D 等级                                   |
| position\_size    | float  | 建议仓位                                            |
| current\_layer    | int    | 当前层级                                            |
| stop\_loss        | float  | 当前止损                                            |
| take\_profit      | float  | 可选止盈                                            |
| total\_risk       | float  | 当前风险                                            |
| trend\_state      | string | trend/valid/weak/invalid                        |
| market\_regime    | string | strong\_trend/weak\_trend/choppy/volatile/panic |
| mtf\_signal       | string | 多周期信号                                           |
| reason            | string | 动作原因                                            |
| priority          | string | 触发的优先级（P0-P5）                                   |

***

# 19. 常见错误（Critical Warnings）

***

## 错误1：亏损补仓

错误：

```text
价格下跌继续买入
```

这是：

```text
马丁格尔
```

不是：

```text
金字塔
```

***

## 错误2：震荡市使用

ADX低时：

```text
极易连续止损
```

***

## 错误3：止损过宽

会导致：

- 盈亏比恶化
- 仓位过大
- 回撤失控

***

# 20. 策略口诀

## 基础版

```text
趋势确认先开仓，
盈利之后递减加；

只加赢家不补亏，
止损跟随不幻想；

ADX 弱则停加仓，
趋势反转立刻撤。
```

***

## 进阶版（2026 更新）

```text
三级趋势分强弱，
强潮全力弱潮观；

突破需把量来看，
收盘站稳是真金；

浮盈不到不加仓，
一点五倍是门槛；

ADX 升才加仓，
K 线形态要看清；

首仓止损一点五，
盈利提到成本上；

持仓若超二十根，
ADX 低就减仓；

BB 收窄 ATR 低，
震荡过滤要记牢。
```

***

## 专业版（机构级 Agent）

```text
置信评分定仓位，
八十以上才全开；

市场状态先识别，
恐慌震荡要避开；

多周期里看共振，
日线四小时同向；

优先级里 P0 重，
风控熔断保命脉；

流动性差不上单，
插针闪崩快离开；

连胜连败要冷却，
行为约束不胡来；

执行层里 TWAP 好，
滑点控制记心怀。
```

***

## 核心心法

```text
1. 分级确认：不强求完美信号，抓住早期趋势
2. 量价过滤：假突破是最大敌人，必须过滤
3. 浮盈门槛：微利不加仓，避免过早暴露风险
4. 分层止损：不同阶段不同止损，首仓最紧
5. 盈利保护：2R 提 Breakeven，锁定利润
6. 时间止损：横盘也是成本，效率优先
7. 震荡过滤：ADX 滞后，需多指标配合
8. 置信评分：从规则系统升级到概率系统
9. 市场状态：不同 Regime 不同策略
10. 优先级矩阵：解决逻辑冲突的关键
11. 行为约束：防止 AI 失控的保险丝
12. 风险熔断：机构级风控的标配
```

***

# 21. 机构级 Agent Skill 总结

***

## 21.1 从"策略文档"到"生产级 Skill"的进化

| 版本   | 特征                         | 成熟度         |
| ---- | -------------------------- | ----------- |
| v1.0 | 基础趋势 + 金字塔加仓               | 60 分（入门）    |
| v2.0 | 分级趋势 + 过滤器 + 分层止损          | 85 分（高级交易员） |
| v3.0 | 置信度+Regime+ 优先级 + 熔断 + 执行层 | 95 分（机构级）   |

***

## 21.2 核心模块清单

### 趋势识别系统

- [x] 分级趋势确认（Level 1/2/3）
- [x] 置信度评分系统
- [x] 市场状态识别（Regime）
- [x] 多周期同步确认

### 开仓系统

- [x] 突破开仓 + 量价过滤
- [x] EMA 回踩开仓
- [x] 震荡市过滤（BB+ATR）
- [x] 流动性检查

### 加仓系统

- [x] 浮盈阈值（1.5R）
- [x] 递减仓位
- [x] K 线形态过滤
- [x] 冷却机制（Cooldown）

### 止损系统

- [x] 初始止损（ATR）
- [x] 分层止损
- [x] 动态 Trailing Stop
- [x] Breakeven 盈利保护
- [x] 时间止损
- [x] 极端行情保护

### 风控系统

- [x] 单笔风险限制
- [x] 总风险限制
- [x] 决策优先级矩阵（P0-P5）
- [x] 全局风险熔断器（Kill Switch）
- [x] 流动性控制
- [x] Agent 行为约束

### 执行系统

- [x] 订单类型选择
- [x] TWAP 执行
- [x] 部分成交处理
- [x] 滑点控制

***

## 21.3 使用建议

### 模拟盘测试（1-3 个月）

```
1. 验证置信度评分系统
2. 测试 Regime 识别准确率
3. 优化冷却时间参数
4. 记录所有决策日志
```

### 小仓位实盘（3-6 个月）

```
1. 从 10-20% 正常仓位开始
2. 重点监控执行质量
3. 对比模拟盘和实盘差异
4. 调整滑点和流动性参数
```

### 正式运行

```
1. 逐步提升至正常仓位
2. 持续监控风控指标
3. 定期复盘和优化
4. 根据市场变化调整 Regime 阈值
```

***

## 21.4 后续优化方向

1. **机器学习增强**
   - 使用 ML 优化置信度权重
   - Regime 自动识别（聚类）
   - 动态参数调整
2. **情绪指标融合**
   - 新闻情绪分析
   - 社交媒体情绪
   - 资金流向
3. **组合管理**
   - 多策略组合
   - 相关性控制
   - 风险预算分配
4. **自适应系统**
   - 根据市场变化自动调整
   - 在线学习
   - 强化学习优化

***

**文档版本**：v3.0\
**更新日期**：2026-05-07\
**策略等级**：机构级 AI Agent Skill\
**适用场景**：自动交易系统 / Quant Engine / AI Agent
