"""
股票命令发送工具
通过 Redis Proxy (RPUSH) 向量化程序发送操作命令

原理:
    key = vnpy:{user}:command:{策略名}
    将命令 JSON 字符串 RPUSH 到该列表，量化程序从列表 LPOP 取出执行。

命令格式:
    {"cmd": "close",                "comment": "紧急全平"}
    {"cmd": "notice",               "comment": "通知消息"}
    {"cmd": "set_target_pos", "pos": -2.0, "comment": "调整仓位"}

支持 --token 全局参数，如有则附加 "token" 字段到每条命令 JSON 中。

用法:
    python stock_command.py [--token TOKEN] send      <用户名> <策略名> <命令JSON>
    python stock_command.py [--token TOKEN] close     <用户名> <策略名> [--comment TEXT]
    python stock_command.py [--token TOKEN] notice    <用户名> <策略名> [--comment TEXT]
    python stock_command.py [--token TOKEN] set-target-pos <用户名> <策略名> --pos POS [--comment TEXT]
    python stock_command.py [--token TOKEN] list      <用户名> <策略名>
    python stock_command.py [--token TOKEN] clear     <用户名> <策略名>

示例:
    python stock_command.py --token "sk-abc123" close 楠总1号 OPENCLAW-NFLX --comment "紧急平仓"
    python stock_command.py --token "sk-abc123" set-target-pos 楠总1号 OPENCLAW-NFLX --pos -2.0
    python stock_command.py close 楠总1号 OPENCLAW-NFLX --comment "紧急平仓"
"""
import sys
import os
import json
import requests
import argparse
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tools import load_json

settings = load_json(os.path.join(os.path.dirname(os.path.abspath(__file__)), "setting.json"))


class CommandClient:
    """通过 Redis Proxy RPUSH 发送命令到量化程序"""

    def __init__(self):
        self.base_url = settings.get("http_redis_proxy_url", "https://ai4.newgoai.com/")
        self.db = settings.get("http_redis_proxy_db", 11)
        self.api_key = settings.get("http_redis_proxy_apikey", "nokey")
        if not self.base_url.endswith('/'):
            self.base_url += '/'

    def _make_request(self, endpoint, data=None):
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

    def _build_key(self, username: str, strategy_name: str) -> str:
        return f"vnpy:{username}:command:{strategy_name}"

    def send_command(self, username: str, strategy_name: str, command: dict) -> dict:
        """通过 RPUSH 发送命令"""
        key = self._build_key(username, strategy_name)
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

    def list_commands(self, username: str, strategy_name: str, start: int = 0, stop: int = -1) -> list:
        """查看待处理命令列表 (LRANGE)"""
        key = self._build_key(username, strategy_name)
        result = self._make_request("api/v1/redis/lrange", {"key": key, "start": start, "stop": stop})
        if result and result.get('success'):
            data = result.get('data', [])
            if isinstance(data, list):
                return data
            return []
        return []

    def clear_commands(self, username: str, strategy_name: str) -> bool:
        """清空命令队列 (DELETE)"""
        key = self._build_key(username, strategy_name)
        result = self._make_request("api/v1/redis/delete", {"key": key})
        if result and result.get('success'):
            print(f"✅ 命令队列已清空: {key}")
            return True
        print(f"❌ 清空失败")
        return False


def build_command(cmd: str, volume: float = None, comment: str = None) -> dict:
    command = {"cmd": cmd}
    if volume is not None:
        command["volume"] = volume
    if comment:
        command["comment"] = comment
    return command


def inject_token(command: dict, token: str = None) -> dict:
    if token:
        command["token"] = token
    return command


def main():
    parser = argparse.ArgumentParser(description='量化命令发送工具')
    parser.add_argument('--token', default=None, help='校验令牌，附加到命令 JSON 的 "token" 字段')
    subparsers = parser.add_subparsers(dest='command', help='可用命令')

    # send - 发送原始 JSON 命令
    send_parser = subparsers.add_parser('send', help='发送原始命令 JSON')
    send_parser.add_argument('username', help='用户名')
    send_parser.add_argument('strategy', help='策略名称')
    send_parser.add_argument('command_json', help='命令 JSON 字符串，如 \'{"cmd":"close","comment":"紧急平仓"}\'')

    # close - 全平
    close_parser = subparsers.add_parser('close', help='全平所有持仓')
    close_parser.add_argument('username', help='用户名')
    close_parser.add_argument('strategy', help='策略名称')
    close_parser.add_argument('--comment', default='紧急平仓', help='备注')

    # notice - 通知
    notice_parser = subparsers.add_parser('notice', help='发送通知消息')
    notice_parser.add_argument('username', help='用户名')
    notice_parser.add_argument('strategy', help='策略名称')
    notice_parser.add_argument('--comment', default='', help='通知内容')

    # set-target-pos - 调整目标仓位
    setpos_parser = subparsers.add_parser('set-target-pos', help='调整目标仓位')
    setpos_parser.add_argument('username', help='用户名')
    setpos_parser.add_argument('strategy', help='策略名称')
    setpos_parser.add_argument('--pos', type=float, required=True, help='目标持仓量，正数=多头，负数=空头，0=空仓')
    setpos_parser.add_argument('--comment', default=None, help='备注')

    # list - 查看待处理命令
    list_parser = subparsers.add_parser('list', help='查看待处理命令列表')
    list_parser.add_argument('username', help='用户名')
    list_parser.add_argument('strategy', help='策略名称')

    # clear - 清空命令队列
    clear_parser = subparsers.add_parser('clear', help='清空待处理命令队列')
    clear_parser.add_argument('username', help='用户名')
    clear_parser.add_argument('strategy', help='策略名称')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    token = args.token
    client = CommandClient()

    if args.command == 'send':
        try:
            command = json.loads(args.command_json)
        except json.JSONDecodeError as e:
            print(f"❌ JSON 解析失败: {e}")
            sys.exit(1)
        if 'cmd' not in command:
            print("❌ 命令 JSON 必须包含 'cmd' 字段")
            sys.exit(1)
        username = args.username.replace(' ', '')
        strategy = args.strategy.replace(' ', '')
        client.send_command(username, strategy, inject_token(command, token))

    elif args.command in ('close', 'notice'):
        username = args.username.replace(' ', '')
        strategy = args.strategy.replace(' ', '')
        volume = getattr(args, 'volume', None)
        comment = getattr(args, 'comment', None)
        command = build_command(args.command, volume=volume, comment=comment)
        client.send_command(username, strategy, inject_token(command, token))

    elif args.command == 'set-target-pos':
        username = args.username.replace(' ', '')
        strategy = args.strategy.replace(' ', '')
        command = {"cmd": "set_target_pos", "pos": args.pos}
        if args.comment:
            command["comment"] = args.comment
        client.send_command(username, strategy, inject_token(command, token))

    elif args.command == 'list':
        username = args.username.replace(' ', '')
        strategy = args.strategy.replace(' ', '')
        commands = client.list_commands(username, strategy)
        key = f"vnpy:{username}:command:{strategy}"
        print(f"\n命令队列: {key}")
        print(f"待处理命令数: {len(commands)}")
        if commands:
            print()
            for i, cmd_str in enumerate(commands, 1):
                try:
                    cmd = json.loads(cmd_str)
                    print(f"  [{i}] {json.dumps(cmd, ensure_ascii=False)}")
                except json.JSONDecodeError:
                    print(f"  [{i}] {cmd_str}")
        print()

    elif args.command == 'clear':
        username = args.username.replace(' ', '')
        strategy = args.strategy.replace(' ', '')
        client.clear_commands(username, strategy)


if __name__ == "__main__":
    main()