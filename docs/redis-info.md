账户信息和策略执行信息获取

你可以通过以下代码获取redis服务上的与自身相关的账户和策略信息数据（非实时，仅为辅助，应当以上下文中vnpy提交的即时信息为主）
如果你不知道用户名，必须向用户询问，以免查询出错！

scripts/stock_redis_query.py

使用说明是


使用方法为：
```
账户 Redis 查询工具
通过 Web API 读取 Redis db11 中的账户和策略信息

用法:
    python stock_redis_query.py overview              # 1. 查所有账户概览
    python stock_redis_query.py account 用户名         # 2. 查指定账户信息和持仓
    python stock_redis_query.py strategies 用户名      # 3. 查指定账户策略列表和概要
    python stock_redis_query.py distribution 用户名    # 4. 查指定账户持仓分布
    python stock_redis_query.py detail 用户名 策略名   # 6. 查指定策略详情

示例:
    python stock_redis_query.py overview                                    # 查看所有账户
    python stock_redis_query.py account 楠总1号                             # 查看楠总1号账户详情
    python stock_redis_query.py strategies 楠总1号                          # 查看楠总1号全部策略的信息列表
    python stock_redis_query.py distribution 楠总1号                        # 查看楠总1号的策略持仓分布
    python stock_redis_query.py detail 楠总1号 OPENCLAW-NFLX               # 查看OPENCLAW-NFLX策略详情（包括当前持仓的交易历史）

参数说明:
    用户名: 如 "楠总1号"、"tiger-code"
    策略名: 如 "OPENCLAW-NFLX"、"EMA_QQQ"、"AI-GE" 等

输出说明:
    - overview: 显示所有用户的账户余额和持仓总市值
    - account: 显示指定用户的子账户明细、持仓品种数和持仓明细
    - strategies: 显示持仓中策略和空仓策略列表
    - distribution: 按分类显示持仓分布（强势非AI、科技巨头、非科技巨头、ETF等）
    - detail: 显示策略详细信息，包括持仓概况、交易历史和完整JSON数据
```