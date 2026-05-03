---
name: "tiger-stock-strategy-analysis"
description: "股票量化策略分析工具：支持为vnpy量化软件，提供多种策略的分析和审核，根据vnpy量化软件提供的数据，或者自身通过redis服务获取的账户数据，提供基本面分析，股票当前趋势分析，当前阶段和对策分析，指标和趋势预测，以及对vnpy量化软件提交的交易订单进行下单前审核；支持的策略包括：智能马丁格尔策略、智能短期趋势策略；"
---

# tiger-stock-strategy-analysis

为vnpy量化系统提供策略分析，趋势预测，下单前审核！
当执行的策略是本技能涉及的策略类型，你必须打开相应策略类型的文档，了解策略的具体方法，而后思考。
如果不是本技能支持的策略类型，应该明确的提示给用户。


## 外部信息采集与存储

### 采集信息的存档

### 存档不足以分析时，再进行信息采集和分析

## 账户信息和策略执行信息获取

你可以通过以下代码获取redis服务上的与自身相关的账户和策略信息数据（非实时，仅为辅助，应当以上下文中vnpy提交的即时信息为主）
如果你不知道用户名，必须向用户询问，以免查询出错！

scripts/stock_redis_query.py

使用说明是
[docs/redis-info.md](docs/redis-info.md)


## vt_symbol 产品代码表与盈透conid

策略信息中的产品代码是vnpy专属格式的时候，对照此文档，获得股票名称，分类：

参考
[scripts/vt_symbol_info.json](scripts/vt_symbol_info.json)


## 输出

- 采用 json 方式输出结果！
- 不要输出json以外的信息！

## 策略类型

以下策略是本技能支持的策略类型：

### 智能马丁格尔策略

当vnpy量化软件执行的是，马丁格尔网格策略，参考如下文档进行分析和判断。

[docs/Martingale-Grid-Trading-Strategy.md](docs/Martingale-Grid-Trading-Strategy.md)

### 智能短期趋势策略

当vnpy量化软件执行的是，智能短期趋势策略，参考如下文档进行分析和判断。

[docs/Short-term-CCI-Trend-Strategy.md](docs/Short-term-CCI-Trend-Strategy.md)

