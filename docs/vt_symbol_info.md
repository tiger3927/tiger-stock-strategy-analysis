# vt_symbol 产品信息表

用途：对照查询策略中 vt_symbol（如 265598.SMART / ETHUSDT_SWAP_BINANCE.GLOBAL）对应的品种信息——名称、中文名、ticker、分类、行业等（美股品种可查盈透 conid），供策略分析、品种识别与下单前审核使用。

策略信息中的产品代码是 vnpy 专属格式的时候，如 265598.SMART，对照本表获得股票名称、分类：

推荐的美股的盈透 conid 参考：
[../scripts/vt_symbol_info.json](../scripts/vt_symbol_info.json)
如果上述文档中不包含，可通过 vnpy_mcp 的 `search_vt_symbol` / `get_contract` 工具实时查询。

## 品种缺失 / conid 变化的修复

策略信息中的 vt_symbol（如 265598.SMART / ETHUSDT_SWAP_BINANCE.GLOBAL）在品种表中查不到，或盈透IB交易所的 conid 已变化时：

1. 用 vnpy_mcp `search_vt_symbol` 按 conid / ticker 查询真实品种信息（返回 ticker、名称、交易所等）
2. 用 `get_contract` 获取合约详情，确认 conid 是否已变化
3. 若 vnpy_mcp 查不到，可用 `../scripts/get_market_data.py`（yfinance）验证 ticker 有效性
4. 用 [../scripts/update_vt_symbol.py](../scripts/update_vt_symbol.py) 将最新信息新增/更新到 [../scripts/vt_symbol_info.json](../scripts/vt_symbol_info.json)（字段：name、name_cn、ticker、conid、category、industry、priority）：
   - 新增品种：`python ../scripts/update_vt_symbol.py add --vt-symbol 265598.SMART --name Apple --name-cn 苹果 --ticker AAPL --conid 265598 --category 科技巨头 --industry "信息技术 – 消费电子/软件" --priority high`
   - 修改已有品种（如 conid 变化）：`python ../scripts/update_vt_symbol.py update --vt-symbol 265598.SMART --conid 265598 --name-cn 苹果`
   - 查询确认：`python ../scripts/update_vt_symbol.py query --query TSLA` 或 `list`（列出全部）
   - `vt-symbol` 可省略 `.SMART` 后缀，脚本自动补全；`--force` 可跳过删除确认
5. 未补录/未修复前，不得对该品种做开仓分析与下单

工具具体参数见 [vnpy_mcp.md](vnpy_mcp.md) 第 7 节「行情与合约」。
