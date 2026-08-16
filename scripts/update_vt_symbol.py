"""
管理 vt_symbol_info.json（品种信息表）：查询、新增、修改、删除品种记录。

用途: 配合 SKILL.md「品种缺失 / conid 变化修复」流程使用。
信息查询由智能体通过 vnpy_mcp（search_vt_symbol / get_contract）或
get_market_data.py（yfinance）完成，本脚本只负责读写 JSON，不调用外部接口。

用法:
    E:\\veighna_studio_43\\python.exe skills/tiger-stock-strategy-analysis/scripts/update_vt_symbol.py query --ticker TSLA
    E:\\veighna_studio_43\\python.exe skills/tiger-stock-strategy-analysis/scripts/update_vt_symbol.py add --vt-symbol 265598.SMART --name Apple --name-cn 苹果 --ticker AAPL --conid 265598 --category 科技巨头 --industry "信息技术" --priority high
    E:\\veighna_studio_43\\python.exe skills/tiger-stock-strategy-analysis/scripts/update_vt_symbol.py update --vt-symbol 265598.SMART --conid 265598 --name-cn 苹果
    E:\\veighna_studio_43\\python.exe skills/tiger-stock-strategy-analysis/scripts/update_vt_symbol.py delete --vt-symbol 265598.SMART
    E:\\veighna_studio_43\\python.exe skills/tiger-stock-strategy-analysis/scripts/update_vt_symbol.py list

字段说明:
    vt_symbol: 策略使用的品种代码（如 265598.SMART），JSON 的 key
    name:      英文名称（必填）
    name_cn:   中文名称（必填）
    ticker:    股票代码（必填，如 AAPL）
    conid:     盈透 conid（必填，数字）
    category:  分类（必填，如 科技巨头 / ETF / 蓝筹股）
    industry:  行业（必填）
    priority:  优先级（可选，high / normal / low，默认 normal）
    note:      备注（可选）
    market:    市场（可选，如 IB，默认不写）
"""
import argparse
import json
import os
import sys
import tempfile

# 数据文件路径（与脚本同目录）
_DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vt_symbol_info.json")

# 必填字段（新增时必须提供）
_REQUIRED_FIELDS = ["name", "name_cn", "ticker", "conid", "category", "industry"]
# 可选字段
_OPTIONAL_FIELDS = ["priority", "note", "market"]

# 每个 vt_symbol 记录的完整字段集合（用于校验和排序）
_ALL_FIELDS = _REQUIRED_FIELDS + _OPTIONAL_FIELDS


def load_data() -> dict:
    """从 vt_symbol_info.json 加载品种表"""
    if not os.path.exists(_DATA_FILE):
        print(f"找不到数据文件: {_DATA_FILE}")
        sys.exit(1)
    with open(_DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        print(f"数据文件格式错误: 顶层应为 JSON 对象（vt_symbol -> 信息字典）")
        sys.exit(1)
    return data


def save_data(data: dict):
    """原子写入 vt_symbol_info.json（tmp + os.replace，避免写坏文件）"""
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(_DATA_FILE), suffix=".tmp", prefix="vt_symbol_info_"
    )
    try:
        # newline="\r\n" 保持与原有 JSON 文件一致的 CRLF 行尾
        with os.fdopen(fd, "w", encoding="utf-8", newline="\r\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, _DATA_FILE)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def _normalize_vt_symbol(vt_symbol: str) -> str:
    """标准化 vt_symbol：去掉空格；无后缀时补 .SMART（美股默认）"""
    vt_symbol = vt_symbol.strip()
    if "." not in vt_symbol:
        vt_symbol = f"{vt_symbol}.SMART"
    return vt_symbol


def _validate_new_record(vt_symbol: str, fields: dict) -> None:
    """校验新增记录的必填字段和类型"""
    if not vt_symbol:
        print("错误: vt_symbol 不能为空")
        sys.exit(1)

    missing = [k for k in _REQUIRED_FIELDS if not fields.get(k)]
    if missing:
        print(f"错误: 新增记录缺少必填字段: {', '.join(missing)}")
        sys.exit(1)

    # conid 必须为整数
    try:
        fields["conid"] = int(fields["conid"])
    except (TypeError, ValueError):
        print(f"错误: conid 必须为整数，得到: {fields.get('conid')!r}")
        sys.exit(1)

    # priority 限定取值
    priority = fields.get("priority")
    if priority is not None and priority not in ("high", "normal", "low"):
        print(f"错误: priority 只能是 high/normal/low，得到: {priority!r}")
        sys.exit(1)


def _build_fields_from_args(args) -> dict:
    """从命令行参数提取非空字段字典"""
    fields = {}
    for key in _ALL_FIELDS:
        value = getattr(args, key, None)
        if value is not None:
            fields[key] = value
    return fields


def cmd_query(args):
    """查询品种记录，支持按 vt_symbol / ticker / conid / name / name_cn 精确或模糊匹配"""
    data = load_data()

    key_lower = (args.query or "").strip().lower()
    if not key_lower:
        print("用法: query 需要提供查询词（--query），如 --query TSLA")
        sys.exit(1)

    results = []
    for vt_key, info in data.items():
        info_lower = {k: str(v).lower() for k, v in info.items() if v is not None}
        if (
            key_lower == vt_key.lower()
            or key_lower == vt_key.split(".")[0].lower()
            or key_lower in info_lower.get("ticker", "")
            or key_lower in info_lower.get("name", "")
            or key_lower in info_lower.get("name_cn", "")
            or key_lower == info_lower.get("conid", "")
        ):
            results.append((vt_key, info))

    if not results:
        print(f"未找到匹配 '{args.query}' 的品种")
        sys.exit(0)

    for vt_key, info in results:
        _print_record(vt_key, info)


def cmd_list(args):
    """列出全部品种记录"""
    data = load_data()
    if not data:
        print("品种表为空")
        return
    for vt_key, info in data.items():
        _print_record(vt_key, info)


def cmd_add(args):
    """新增品种记录（已存在则报错）"""
    data = load_data()
    vt_symbol = _normalize_vt_symbol(args.vt_symbol)
    fields = _build_fields_from_args(args)

    if vt_symbol in data:
        print(f"错误: 品种 {vt_symbol} 已存在，如需修改请用 update 命令")
        sys.exit(1)

    _validate_new_record(vt_symbol, fields)
    data[vt_symbol] = fields
    save_data(data)
    print(f"已新增品种 {vt_symbol}")
    _print_record(vt_symbol, data[vt_symbol])


def cmd_update(args):
    """修改已有品种记录（只更新传入的字段）"""
    data = load_data()
    vt_symbol = _normalize_vt_symbol(args.vt_symbol)
    fields = _build_fields_from_args(args)

    if vt_symbol not in data:
        print(f"错误: 品种 {vt_symbol} 不存在，请先 add")
        sys.exit(1)

    if not fields:
        print("错误: 未提供任何要修改的字段")
        sys.exit(1)

    # 修改 conid / priority 时做校验
    if "conid" in fields:
        try:
            fields["conid"] = int(fields["conid"])
        except (TypeError, ValueError):
            print(f"错误: conid 必须为整数，得到: {fields['conid']!r}")
            sys.exit(1)
    if fields.get("priority") and fields["priority"] not in ("high", "normal", "low"):
        print(f"错误: priority 只能是 high/normal/low，得到: {fields['priority']!r}")
        sys.exit(1)

    data[vt_symbol].update(fields)
    save_data(data)
    print(f"已更新品种 {vt_symbol}")
    _print_record(vt_symbol, data[vt_symbol])


def cmd_delete(args):
    """删除品种记录"""
    data = load_data()
    vt_symbol = _normalize_vt_symbol(args.vt_symbol)

    if vt_symbol not in data:
        print(f"错误: 品种 {vt_symbol} 不存在")
        sys.exit(1)

    # 交互确认
    if not args.force:
        answer = input(f"确认删除 {vt_symbol}（{data[vt_symbol].get('name', '')}）？[y/N]: ")
        if answer.strip().lower() != "y":
            print("已取消")
            return

    del data[vt_symbol]
    save_data(data)
    print(f"已删除品种 {vt_symbol}")


def _print_record(vt_key: str, info: dict):
    """打印一条品种记录"""
    print(f"  {vt_key}")
    for key in _ALL_FIELDS:
        if key in info:
            print(f"    {key}: {info[key]}")
    # 打印 JSON 中可能存在的其他自定义字段
    for key, value in info.items():
        if key not in _ALL_FIELDS:
            print(f"    {key}: {value}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="管理 vt_symbol_info.json：查询、新增、修改、删除品种记录"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # query: --query 关键词
    p_query = subparsers.add_parser("query", help="查询品种（按 vt_symbol/ticker/conid/name 匹配）")
    p_query.add_argument("--query", required=True, help="查询关键词，如 TSLA、特斯拉、265598、265598.SMART")
    p_query.set_defaults(func=cmd_query)

    # list
    p_list = subparsers.add_parser("list", help="列出全部品种")
    p_list.set_defaults(func=cmd_list)

    # add / update 共用字段
    p_add = subparsers.add_parser("add", help="新增品种")
    p_update = subparsers.add_parser("update", help="修改已有品种（只更新传入的字段）")
    for p in (p_add, p_update):
        p.add_argument("--vt-symbol", dest="vt_symbol", required=True, help="品种代码，如 265598.SMART（可省略 .SMART）")
        p.add_argument("--name", help="英文名称，如 Apple")
        p.add_argument("--name-cn", dest="name_cn", help="中文名称，如 苹果")
        p.add_argument("--ticker", help="股票代码，如 AAPL")
        p.add_argument("--conid", help="盈透 conid（整数）")
        p.add_argument("--category", help="分类，如 科技巨头 / ETF / 蓝筹股")
        p.add_argument("--industry", help="行业，如 信息技术")
        p.add_argument("--priority", choices=["high", "normal", "low"], help="优先级（默认 normal）")
        p.add_argument("--note", help="备注")
        p.add_argument("--market", help="市场，如 IB")
    p_add.set_defaults(func=cmd_add)
    p_update.set_defaults(func=cmd_update)

    # delete
    p_delete = subparsers.add_parser("delete", help="删除品种")
    p_delete.add_argument("--vt-symbol", dest="vt_symbol", required=True, help="品种代码，如 265598.SMART")
    p_delete.add_argument("--force", action="store_true", help="跳过确认提示")
    p_delete.set_defaults(func=cmd_delete)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
