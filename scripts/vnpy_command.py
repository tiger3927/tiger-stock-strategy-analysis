"""
VeighNa 统一命令发送工具
通过 Redis Proxy 向 data_engine 发送各类命令并获取执行结果

原理:
    系统命令 key = vnpy:{user}:command:_system
    所有命令统一发送到该队列，data_engine 根据 type 字段路由。
    - type=query_conid    → data_engine 自行处理
    - type=strategy_cmd   → 按 target_strategy 转发给对应策略

支持命令:
    conid         查询股票的 IB 合约 ConID
    close         全平策略持仓
    notice        发送通知消息
    set-target-pos  调整策略目标仓位
    send          发送任意原始命令
    publish       向 Redis 写入/发布信息（SET）
    get           读取 Redis key 的值（GET）

用法:
    python vnpy_command.py [全局参数] <子命令> [子命令参数]

全局参数:
    --token TOKEN         校验令牌
    --wait [N]            等待结果（可选指定超时秒数，默认 30）
    --response-key KEY    自定义结果返回 Key

示例:
    # ConID 查询
    python vnpy_command.py --token tiger-code-123456 conid tiger-code AAPL --wait
    python vnpy_command.py --token tiger-code-123456 conid tiger-code SGPIY --wait 60

    # 策略操作
    python vnpy_command.py --token tiger-code-123456 close tiger-code MARTIN-AMD --comment "紧急平仓"
    python vnpy_command.py --token tiger-code-123456 close tiger-code MARTIN-AMD --wait --comment "平仓并确认"
    python vnpy_command.py --token tiger-code-123456 notice tiger-code MARTIN-AMD --comment "注意风控"
    python vnpy_command.py --token tiger-code-123456 set-target-pos tiger-code MARTIN-AMD --pos -2.0
    python vnpy_command.py --token tiger-code-123456 send tiger-code MARTIN-AMD '{"cmd":"close","comment":"平仓"}'
    # 发布信息
    python vnpy_command.py --token tiger-code-123456 publish tiger-code analysis:result '{"status":"ok"}' --expire 3600
    python vnpy_command.py --token tiger-code-123456 publish tiger-code notice:all "系统维护通知"
"""
import sys
import os
import json
import time
import uuid
import argparse
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tools import load_json

settings = load_json(os.path.join(os.path.dirname(os.path.abspath(__file__)), "setting.json"))


class VnpyCommandClient:
    """通过 Redis Proxy 向 data_engine 发送命令"""

    def __init__(self):
        self.base_url = settings.get("http_redis_proxy_url", "https://ai4.newgoai.com/")
        self.db = settings.get("http_redis_proxy_db", 11)
        self.api_key = settings.get("http_redis_proxy_apikey", "nokey")
        if not self.base_url.endswith('/'):
            self.base_url += '/'

    def _make_request(self, endpoint, data=None):
        """通用请求方法"""
        url = f"{self.base_url}{endpoint}"
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": self.api_key
        }
        if data is None:
            data = {}
        data['db'] = self.db
        try:
            response = requests.post(url, headers=headers, json=data, timeout=10)
            if response.status_code == 200:
                return response.json()
            else:
                print(f"请求失败: HTTP {response.status_code}")
                print(f"响应内容: {response.text}")
                return None
        except requests.exceptions.RequestException as e:
            print(f"请求异常: {e}")
            return None

    def send_command(self, username: str, command: dict) -> dict:
        """
        向系统命令队列发送命令（RPUSH）

        Args:
            username: 用户名
            command: 命令字典（需包含 type 字段）

        Returns:
            dict: API 响应
        """
        key = f"vnpy:{username}:command:_system"
        value = json.dumps(command, ensure_ascii=False)
        print(f"发送命令:")
        print(f"  Key:   {key}")
        print(f"  Value: {value}")
        result = self._make_request("api/v1/redis/rpush", {"key": key, "values": [value]})
        if result and result.get('success'):
            print(f"  结果: ✅ 命令已发送")
        else:
            print(f"  结果: ❌ 发送失败")
        return result

    def get_value(self, key: str) -> str:
        """读取 Redis key 的值（GET）"""
        result = self._make_request("api/v1/redis/get", data={"key": key})
        if result and result.get('success'):
            return result.get('data')
        return None

    def set_value(self, key: str, value: str, expire_seconds: int = None) -> dict:
        """设置 Redis key 的值（SET），可选过期时间"""
        data = {"key": key, "value": value}
        if expire_seconds is not None:
            data["expire_seconds"] = expire_seconds
        return self._make_request("api/v1/redis/set", data=data)

    def poll_result(self, response_key: str, timeout: int = 30, interval: int = 1) -> dict:
        """轮询等待结果"""
        print(f"\n等待结果... (超时 {timeout} 秒)")
        start = time.time()
        while time.time() - start < timeout:
            value = self.get_value(response_key)
            if value:
                try:
                    data = json.loads(value) if isinstance(value, str) else value
                    elapsed = time.time() - start
                    print(f"  耗时: {elapsed:.1f} 秒")
                    return data
                except json.JSONDecodeError:
                    print(f"  结果解析失败: {value[:200]}")
                    return None
            elapsed = time.time() - start
            if int(elapsed) % 5 == 0 and elapsed > 1:
                print(f"  已等待 {elapsed:.0f} 秒...")
            time.sleep(interval)

        print(f"  超时: 已等待 {timeout} 秒，未收到结果")
        return None


def inject_token(command: dict, token: str = None) -> dict:
    if token:
        command["token"] = token
    return command


# ==================== 结果格式化 ====================

def format_conid_result(data: dict) -> str:
    """格式化 ConID 查询结果"""
    if not data:
        return "查询超时或无结果"

    if data.get("success"):
        lines = [
            f"✅ {data['symbol']}  ConID 查询成功",
            "=" * 55,
            f"  合约 ID (conId):     {data['conid']}",
            f"  符号 (symbol):       {data['symbol']}",
            f"  交易所 (exchange):   {data['exchange']}",
            f"  货币 (currency):     {data['currency']}",
            f"  证券类型 (secType):  {data['secType']}",
        ]
        primary = data.get("primaryExchange", "")
        if primary:
            lines.append(f"  主交易所:            {primary}")
        lines.append(f"  查询时间:            {data.get('timestamp', '')}")
        lines.append("=" * 55)
        return "\n".join(lines)
    else:
        return (
            f"❌ {data.get('symbol', '')}  ConID 查询失败\n"
            f"  错误: {data.get('error', '未知错误')}\n"
            f"  时间: {data.get('timestamp', '')}"
        )


def format_command_result(data: dict) -> str:
    """格式化策略命令处理结果"""
    if not data:
        return "查询超时或无结果"

    if data.get("success"):
        lines = [
            f"✅ 命令执行成功",
            f"  消息: {data.get('message', '')}",
        ]
        # 如果有 data 字段（如 query_strategy_status），展开显示
        result_data = data.get("data")
        if result_data and isinstance(result_data, dict):
            lines.append(f"  {'=' * 50}")
            for key, value in result_data.items():
                if isinstance(value, dict):
                    lines.append(f"  {key}:")
                    for k, v in value.items():
                        lines.append(f"    {k}: {v}")
                elif isinstance(value, list):
                    lines.append(f"  {key}: {json.dumps(value, ensure_ascii=False, indent=2)}")
                else:
                    lines.append(f"  {key}: {value}")
            lines.append(f"  {'=' * 50}")
        lines.append(f"  时间: {data.get('timestamp', '')}")
        return "\n".join(lines)
    else:
        return (
            f"❌ 命令执行失败\n"
            f"  错误: {data.get('error', data.get('message', '未知错误'))}\n"
            f"  时间: {data.get('timestamp', '')}"
        )


# ==================== 主入口 ====================

def main():
    parser = argparse.ArgumentParser(
        description='VeighNa 统一命令发送工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
示例:
  # ConID 查询
  python vnpy_command.py --token tiger-code-123456 conid tiger-code AAPL --wait
  
  # 策略操作
  python vnpy_command.py --token tiger-code-123456 close tiger-code MARTIN-AMD --comment "平仓"
  python vnpy_command.py --token tiger-code-123456 close tiger-code MARTIN-AMD --wait --comment "平仓并确认"
  python vnpy_command.py --token tiger-code-123456 notice tiger-code MARTIN-AMD --comment "注意风控"
  python vnpy_command.py --token tiger-code-123456 set-target-pos tiger-code MARTIN-AMD --pos -2.0
  python vnpy_command.py --token tiger-code-123456 send tiger-code MARTIN-AMD '{"cmd":"close"}'
  # 发布信息
  python vnpy_command.py --token tiger-code-123456 publish tiger-code analysis:result '{"status":"ok"}' --expire 3600
  python vnpy_command.py --token tiger-code-123456 publish tiger-code notice:all "系统维护通知"
        '''
    )

    # 全局参数
    parser.add_argument('--token', default=None, help='校验令牌，对应 data_engine 的 command_token 设置')
    parser.add_argument('--response-key', default=None,
                        help='自定义结果返回 Key，自动生成则无需指定')

    subparsers = parser.add_subparsers(dest='command', help='可用命令')

    # --- conid ---
    conid_parser = subparsers.add_parser('conid', help='查询股票合约的 IB ConID')
    conid_parser.add_argument('username', help='用户名')
    conid_parser.add_argument('symbol', help='股票代码，如 AAPL')
    conid_parser.add_argument('--wait', nargs='?', const=30, type=int, default=0,
                              help='等待结果，可选指定超时秒数（默认 30）')

    # --- close ---
    close_parser = subparsers.add_parser('close', help='全平策略持仓')
    close_parser.add_argument('username', help='用户名')
    close_parser.add_argument('strategy', help='策略名称')
    close_parser.add_argument('--comment', default='紧急平仓', help='备注')
    close_parser.add_argument('--wait', nargs='?', const=30, type=int, default=0,
                              help='等待结果，可选指定超时秒数（默认 30）')

    # --- notice ---
    notice_parser = subparsers.add_parser('notice', help='发送通知消息')
    notice_parser.add_argument('username', help='用户名')
    notice_parser.add_argument('strategy', help='策略名称')
    notice_parser.add_argument('--comment', default='', help='通知内容')
    notice_parser.add_argument('--wait', nargs='?', const=30, type=int, default=0,
                               help='等待结果，可选指定超时秒数（默认 30）')

    # --- set-target-pos ---
    setpos_parser = subparsers.add_parser('set-target-pos', help='调整策略目标仓位')
    setpos_parser.add_argument('username', help='用户名')
    setpos_parser.add_argument('strategy', help='策略名称')
    setpos_parser.add_argument('--pos', type=float, required=True, help='目标持仓量，正数=多头，负数=空头，0=空仓')
    setpos_parser.add_argument('--comment', default=None, help='备注')
    setpos_parser.add_argument('--wait', nargs='?', const=30, type=int, default=0,
                               help='等待结果，可选指定超时秒数（默认 30）')

    # --- query-strategy-status ---
    qss_parser = subparsers.add_parser('query-strategy-status', help='查询策略完整状态信息')
    qss_parser.add_argument('username', help='用户名')
    qss_parser.add_argument('strategy', help='策略名称')
    qss_parser.add_argument('--wait', nargs='?', const=30, type=int, default=0,
                            help='等待结果，可选指定超时秒数（默认 30）')

    # --- send ---
    send_parser = subparsers.add_parser('send', help='发送任意原始命令 JSON')
    send_parser.add_argument('username', help='用户名')
    send_parser.add_argument('strategy', help='策略名称')
    send_parser.add_argument('command_json', help='命令 JSON，如 \'{"cmd":"close","comment":"平仓"}\'')
    send_parser.add_argument('--wait', nargs='?', const=30, type=int, default=0,
                             help='等待结果，可选指定超时秒数（默认 30）')

    # --- publish ---
    publish_parser = subparsers.add_parser('publish', help='向 Redis 写入/发布信息（SET）')
    publish_parser.add_argument('username', help='用户名')
    publish_parser.add_argument('key_path', help='key 路径后缀，完整 key 为 vnpy:{username}:{key_path}')
    publish_parser.add_argument('value', help='要写入的值（纯文本或 JSON 字符串）')
    publish_parser.add_argument('--expire', type=int, default=None,
                                help='过期时间（秒），不设置则永不过期')

    # --- get ---
    get_parser = subparsers.add_parser('get', help='读取 Redis key 的值（GET）')
    get_parser.add_argument('username', help='用户名')
    get_parser.add_argument('key_path', help='key 路径后缀，完整 key 为 vnpy:{username}:{key_path}')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    token = args.token
    wait = args.wait
    response_key = args.response_key
    client = VnpyCommandClient()

    # 统一提取 username（所有子命令的第一个位置参数）
    username = args.username.replace(' ', '')
    # 提取 strategy（策略类子命令有，conid 没有）
    strategy = args.strategy.replace(' ', '') if hasattr(args, 'strategy') else None

    if args.command == 'conid':
        symbol = args.symbol.upper().strip()

        # 自动生成 response_key
        if not response_key and wait > 0:
            short_uuid = uuid.uuid4().hex[:8]
            response_key = f"vnpy:{username}:conid_result:{symbol}_{short_uuid}"

        if response_key:
            print(f"响应 Key: {response_key}")

        command = {"type": "query_conid", "symbol": symbol}
        if response_key:
            command["response_key"] = response_key

        client.send_command(username, inject_token(command, token))

        if wait > 0 and response_key:
            print()
            result = client.poll_result(response_key, timeout=wait)
            print()
            print(format_conid_result(result))
        else:
            print(f"\n命令已发送，结果将写入: {response_key}")

    elif args.command == 'publish':
        key = f"vnpy:{username}:{args.key_path}"
        print(f"发布信息:")
        print(f"  Key:   {key}")
        print(f"  Value: {args.value}")
        if args.expire:
            print(f"  过期:  {args.expire} 秒")
        result = client.set_value(key, args.value, expire_seconds=args.expire)
        if result and result.get('success'):
            print(f"  结果: ✅ 发布成功")
        else:
            print(f"  结果: ❌ 发布失败")

    elif args.command == 'get':
        key = f"vnpy:{username}:{args.key_path}"
        print(f"读取 Key: {key}")
        value = client.get_value(key)
        if value is not None:
            print(f"  结果: ✅ 读取成功")
            print(f"  值:   {value}")
        else:
            print(f"  结果: ❌ Key 不存在或读取失败")

    else:
        # --- 策略命令（close / notice / set-target-pos / send）---
        # 自动生成 response_key
        if not response_key and wait > 0 and strategy:
            short_uuid = uuid.uuid4().hex[:8]
            response_key = f"vnpy:{username}:strategy_cmd_result:{strategy}_{short_uuid}"

        if response_key:
            print(f"响应 Key: {response_key}")

        if args.command == 'send':
            try:
                inner_command = json.loads(args.command_json)
            except json.JSONDecodeError as e:
                print(f"❌ JSON 解析失败: {e}")
                sys.exit(1)
            if 'cmd' not in inner_command:
                print("❌ 命令 JSON 必须包含 'cmd' 字段")
                sys.exit(1)
            wrapped = {"type": "strategy_cmd", "target_strategy": strategy, **inner_command}
            if response_key:
                wrapped["response_key"] = response_key
            client.send_command(username, inject_token(wrapped, token))

        elif args.command in ('close', 'notice'):
            command = {
                "type": "strategy_cmd",
                "target_strategy": strategy,
                "cmd": args.command,
            }
            if args.comment:
                command["comment"] = args.comment
            if response_key:
                command["response_key"] = response_key
            client.send_command(username, inject_token(command, token))

        elif args.command == 'set-target-pos':
            command = {
                "type": "strategy_cmd",
                "target_strategy": strategy,
                "cmd": "set_target_pos",
                "pos": args.pos,
            }
            if args.comment:
                command["comment"] = args.comment
            if response_key:
                command["response_key"] = response_key
            client.send_command(username, inject_token(command, token))

        elif args.command == 'query-strategy-status':
            command = {
                "type": "strategy_cmd",
                "target_strategy": strategy,
                "cmd": "query_strategy_status",
            }
            if response_key:
                command["response_key"] = response_key
            client.send_command(username, inject_token(command, token))

        else:
            print(f"❌ 未知命令: {args.command}")
            sys.exit(1)

        # 等待结果
        if wait > 0 and response_key:
            print()
            result = client.poll_result(response_key, timeout=wait)
            print()
            print(format_command_result(result))


if __name__ == "__main__":
    main()