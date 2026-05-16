"""
股票 Redis 查询工具
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
    python stock_redis_query.py strategies 楠总1号                          # 查看楠总1号策略列表
    python stock_redis_query.py distribution 楠总1号                        # 查看楠总1号持仓分布
    python stock_redis_query.py detail 楠总1号 OPENCLAW-NFLX               # 查看OPENCLAW-NFLX策略详情

参数说明:
    用户名: 如 "楠总1号"、"tiger-code"
    策略名: 如 "OPENCLAW-NFLX"、"EMA_QQQ"、"AI-GE" 等

输出说明:
    - overview: 显示所有用户的账户余额和持仓总市值
    - account: 显示指定用户的子账户明细、持仓品种数和持仓明细
    - strategies: 显示持仓中策略和空仓策略列表
    - distribution: 按分类显示持仓分布（强势非AI、科技巨头、非科技巨头、ETF等）
    - detail: 显示策略详细信息，包括持仓概况、交易历史和完整JSON数据
"""
import sys
import os
import requests
import json
import argparse
from collections import defaultdict
from typing import Dict, List, Any, Optional

import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tools import load_json

settings=load_json(os.path.join(os.path.dirname(os.path.abspath(__file__)),"setting.json"))
class StockClassifier:
    """股票分类器 - 从 vt_symbol_info.json 提取分类信息"""

    # 策略名称到公司名/股票代码的映射
    NAME_MAPPINGS = {
        'MICROSOFT': 'MSFT',
        'GOOGLE': 'GOOGL',
        'AMD': 'AMD',
        'NVIDIA': 'NVDA',
        'APPLE': 'AAPL',
        'TESLA': 'TSLA',
        'AMAZON': 'AMZN',
        'META': 'META',
        'FACEBOOK': 'META',
        'Johnson': 'JNJ',
        'JOHNSON': 'JNJ',
        'ExxonMobil': 'XOM',
        'EXXON': 'XOM',
        'VISA': 'V',
        'Caterpillar': 'CAT',
        'CATERPILLAR': 'CAT',
        'WALMART': 'WMT',
        'QQQ': 'QQQ',
        'NFLX': 'NFLX',
        'NETFLIX': 'NFLX',
    }

    def __init__(self, symbol_info_path: str = None):
        if symbol_info_path is None:
            symbol_info_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vt_symbol_info.json')
        
        self.categories = {}
        self.conid_to_category = {}
        self.ticker_to_conid = {}
        self.conid_to_info = {}
        self.vt_symbol_to_info = {}
        self._load_symbol_info(symbol_info_path)

    def _load_symbol_info(self, symbol_info_path: str):
        try:
            with open(symbol_info_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for vt_symbol, info in data.items():
                conid = str(info.get('conid', ''))
                ticker = info.get('ticker', '')
                category = info.get('category', '其他')
                
                self.vt_symbol_to_info[vt_symbol] = info
                if conid:
                    self.conid_to_info[conid] = info
                    self.conid_to_category[conid] = category
                    
                    # 按分类组织
                    if category not in self.categories:
                        self.categories[category] = []
                    self.categories[category].append({
                        'info': info.get('name_cn', '') + ' ' + info.get('name', ''),
                        'ticker': ticker,
                        'conid': conid
                    })
                
                if ticker:
                    self.ticker_to_conid[ticker] = conid
        except Exception as e:
            print(f"加载股票详细信息失败: {e}")

    def get_category_by_symbol(self, symbol: str) -> str:
        if not symbol:
            return '其他'
        if symbol in self.conid_to_category:
            return self.conid_to_category[symbol]
        for category, stocks in self.categories.items():
            for stock in stocks:
                if stock['ticker'].upper() == symbol.upper():
                    return category
        return '其他'

    def get_stock_display_name(self, conid: str) -> str:
        info = self.conid_to_info.get(conid, {})
        if info:
            name_cn = info.get('name_cn', '')
            name = info.get('name', '')
            ticker = info.get('ticker', '')
            industry = info.get('industry', '')
            parts = []
            if name_cn:
                parts.append(name_cn)
            if name and name != name_cn:
                parts.append(name)
            if ticker:
                parts.append(f"[{ticker}]")
            display = ' '.join(parts)
            if industry:
                display += f" | {industry}"
            return display
        return conid


class RedisWebAPI:
    """Redis Web API 客户端"""

    def __init__(self):
        self.base_url = settings.get("http_redis_proxy_url", "https://ai4.newgoai.com/")
        self.db = settings.get("http_redis_proxy_db", 11)
        self.api_key = settings.get("http_redis_proxy_apikey", "nokey")
        if not self.base_url.endswith('/'):
            self.base_url += '/'

    def _make_request(self, endpoint, method="POST", data=None):
        url = f"{self.base_url}{endpoint}"
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": self.api_key
        }
        if data is None:
            data = {}
        data['db'] = self.db
        try:
            if method == "GET":
                response = requests.get(url, headers=headers, params=data, timeout=10)
            elif method == "POST":
                response = requests.post(url, headers=headers, json=data, timeout=10)
            else:
                raise ValueError(f"不支持的 HTTP 方法: {method}")
            if response.status_code == 200:
                return response.json()
            else:
                print(f"请求失败: {response.status_code}")
                return None
        except requests.exceptions.RequestException as e:
            print(f"请求失败: {e}")
            return None

    def get_keys(self, pattern="*"):
        result = self._make_request("api/v1/redis/keys", data={"pattern": pattern})
        if result and result.get('success'):
            return result.get('data', [])
        return []

    def get_value(self, key):
        result = self._make_request("api/v1/redis/get", data={"key": key})
        if result and result.get('success'):
            return result.get('data')
        return None

    def get_and_parse_all_keys(self):
        keys = self.get_keys("*")
        parsed_keys = []
        for key in keys:
            parts = key.split(':')
            if len(parts) >= 4 and parts[0] == 'vnpy':
                parsed_keys.append({
                    'prefix': parts[0],
                    'username': parts[1],
                    'data_type': parts[2],
                    'data_source': ':'.join(parts[3:]) if len(parts) > 3 else parts[3],
                    'raw_key': key
                })
        return parsed_keys

    def get_value_by_pattern(self, username: str = None, data_type: str = None, data_source: str = None):
        parsed_keys = self.get_and_parse_all_keys()
        results = []
        for parsed in parsed_keys:
            if username and parsed['username'] != username:
                continue
            if data_type and parsed['data_type'] != data_type:
                continue
            if data_source and parsed['data_source'] != data_source:
                continue
            value = self.get_value(parsed['raw_key'])
            results.append({'key_info': parsed, 'value': value})
        return results


class StockRedisQuery:
    """股票 Redis 查询主类"""

    def __init__(self):
        self.client = RedisWebAPI()
        self.classifier = StockClassifier()

    def overview(self):
        """1. 查所有账户概览"""
        print("\n" + "=" * 70)
        print("所有账户概览")
        print("=" * 70)

        accounts = self.client.get_value_by_pattern(data_type="account")
        if not accounts:
            print("\n未找到账户数据")
            return

        # 按用户名分组
        user_stats = defaultdict(lambda: {'accounts': [], 'total_position_value': 0.0})

        for item in accounts:
            info = item['key_info']
            value = item['value']
            username = info['username']
            account_type = info['data_source']

            if value and isinstance(value, str):
                try:
                    data = json.loads(value)
                    # 提取子账户信息
                    if 'account' in data and isinstance(data['account'], dict):
                        for acc_key, acc_data in data['account'].items():
                            if isinstance(acc_data, dict) and 'USD' in acc_key:
                                balance = float(acc_data.get('balance', 0))
                                available = float(acc_data.get('available', 0))
                                user_stats[username]['accounts'].append({
                                    'type': account_type,
                                    'balance': balance,
                                    'available': available
                                })
                                break
                except:
                    pass

        # 从策略数据获取持仓市值
        for username in user_stats:
            positions_info = self._get_positions_from_strategies(username)
            user_stats[username]['total_position_value'] = positions_info['total_position_value']

        print(f"\n找到 {len(user_stats)} 个用户\n")
        for username, stats in sorted(user_stats.items()):
            print(f"【{username}】")
            for acc in stats['accounts']:
                print(f"  {acc['type']}: balance=${acc['balance']:,.2f}, available=${acc['available']:,.2f}")
            print(f"  持仓总市值: ${stats['total_position_value']:,.2f}")
            print()

    def account(self, username: str):
        """2. 查指定账户信息和持仓"""
        print("\n" + "=" * 70)
        print(f"账户详情 - {username}")
        print("=" * 70)

        accounts = self.client.get_value_by_pattern(username=username, data_type="account")
        if not accounts:
            print(f"\n未找到 {username} 的账户数据")
            return

        for item in accounts:
            info = item['key_info']
            value = item['value']
            account_type = info['data_source']

            print(f"\n【账户类型: {account_type}】")

            total_balance = 0.0
            
            if value and isinstance(value, str):
                try:
                    data = json.loads(value)

                    # 子账户信息（仅保留账户资金信息）
                    if 'account' in data and isinstance(data['account'], dict):
                        print("\n  子账户明细:")
                        for acc_key, acc_data in data['account'].items():
                            if isinstance(acc_data, dict):
                                balance = float(acc_data.get('balance', 0))
                                available = float(acc_data.get('available', 0))
                                frozen = float(acc_data.get('frozen', 0))
                                print(f"    {acc_key}: balance=${balance:,.2f}, available=${available:,.2f}, frozen=${frozen:,.2f}")
                                # 取可用资金不为0的账户的balance作为总资产
                                if available > 0:
                                    total_balance = balance

                except json.JSONDecodeError:
                    print("  账户数据解析失败")

        # 从策略数据获取持仓信息
        positions_info = self._get_positions_from_strategies(username)
        total_position_value = positions_info['total_position_value']

        if positions_info['total_position_count'] > 0:
            print(f"\n  持仓品种数: {positions_info['total_position_count']}")
            print(f"  持仓总市值: ${positions_info['total_position_value']:,.2f}")
            print(f"\n  持仓明细（来自策略数据）:")
            sorted_positions = sorted(positions_info['positions'], key=lambda x: x['value'], reverse=True)
            for i, pos in enumerate(sorted_positions, 1):
                stock_name = self.classifier.get_stock_display_name(str(pos['symbol']))
                strategies_str = ', '.join(pos['strategies'])
                print(f"    [{i}] {stock_name}")
                print(f"        持仓: {pos['volume']:,.2f}股 × ${pos['current_price']:,.2f} = ${pos['value']:,.2f}")
                print(f"        均价: ${pos['avg_price']:,.2f}, 盈亏: {pos['pnl_ratio']*100:+.2f}%")
                print(f"        策略: {strategies_str}")
                print()
        else:
            print("\n  无持仓")

        # 总资产
        print(f"  【资产汇总】")
        print(f"  持仓市值: ${total_position_value:,.2f}")
        print(f"  总资产: ${total_balance:,.2f}")

    def strategies(self, username: str):
        """3. 查指定账户策略列表和概要"""
        print("\n" + "=" * 70)
        print(f"策略列表 - {username}")
        print("=" * 70)

        strategies = self.client.get_value_by_pattern(username=username, data_type="strategy")
        if not strategies:
            print(f"\n未找到 {username} 的策略数据")
            return

        print(f"\n找到 {len(strategies)} 个策略\n")

        # 按是否有持仓排序
        active_strategies = []
        inactive_strategies = []

        for item in strategies:
            info = item['key_info']
            value = item['value']
            strategy_name = info['data_source']

            if value and isinstance(value, str):
                try:
                    data = json.loads(value)
                    position = float(data.get('实际持仓', 0))
                    avg_price = float(data.get('持仓均价', 0))
                    position_value = position * avg_price
                    current_price = float(data.get('当前行情价格', 0))
                    pnl_ratio = float(data.get('当前持仓盈亏比', 0))

                    strategy_info = {
                        'name': strategy_name,
                        'position': position,
                        'avg_price': avg_price,
                        'position_value': position_value,
                        'current_price': current_price,
                        'pnl_ratio': pnl_ratio,
                        'vt_symbol': data.get('vt_symbol', '')
                    }

                    if position > 0:
                        active_strategies.append(strategy_info)
                    else:
                        inactive_strategies.append(strategy_info)
                except:
                    inactive_strategies.append({'name': strategy_name, 'position': 0})

        # 显示有持仓的策略
        if active_strategies:
            print("【持仓中策略】")
            for s in sorted(active_strategies, key=lambda x: x['position_value'], reverse=True):
                stock_name = ''
                if s.get('vt_symbol'):
                    conid = s['vt_symbol'].split('.')[0] if '.' in s['vt_symbol'] else s['vt_symbol']
                    stock_name = self.classifier.get_stock_display_name(conid)
                print(f"  {s['name']}")
                print(f"    品种: {stock_name}")
                print(f"    持仓: {s['position']:,.2f}股 × ${s['avg_price']:,.2f} = ${s['position_value']:,.2f}")
                print(f"    现价: ${s['current_price']:,.2f}, 盈亏比: {s['pnl_ratio']*100:+.2f}%")
                print()

        # 显示空仓策略
        if inactive_strategies:
            print("【空仓策略】")
            names = [s['name'] for s in inactive_strategies]
            for name in names:
                print(f"  - {name}")
            print()

    def distribution(self, username: str):
        """4. 查指定账户持仓分布"""
        print("\n" + "=" * 70)
        print(f"持仓分布 - {username}")
        print("=" * 70)

        # 从策略数据获取持仓信息
        positions_info = self._get_positions_from_strategies(username)
        all_positions = positions_info['positions']
        total_value = positions_info['total_position_value']

        if not all_positions:
            print("\n无持仓")
            return

        # 按分类统计
        category_stats = defaultdict(lambda: {'value': 0.0, 'symbols': []})
        for pos in all_positions:
            stock_name = self.classifier.get_stock_display_name(str(pos['symbol']))
            category = self.classifier.get_category_by_symbol(str(pos['symbol']))
            category_stats[category]['value'] += pos['value']
            category_stats[category]['symbols'].append({
                'name': stock_name,
                'volume': pos['volume'],
                'value': pos['value'],
                'strategies': pos['strategies']
            })

        print(f"\n总持仓市值: ${total_value:,.2f}\n")

        # 按分类显示
        for category, stats in sorted(category_stats.items(), key=lambda x: x[1]['value'], reverse=True):
            percentage = (stats['value'] / total_value * 100) if total_value > 0 else 0
            print(f"【{category}】${stats['value']:,.2f} ({percentage:.1f}%)")
            for s in sorted(stats['symbols'], key=lambda x: x['value'], reverse=True):
                sp = (s['value'] / total_value * 100) if total_value > 0 else 0
                strategies_str = ', '.join(s['strategies'])
                print(f"  {s['name']}: {s['volume']:,.2f}股 = ${s['value']:,.2f} ({sp:.1f}%)")
                print(f"    策略: {strategies_str}")
            print()

    def detail(self, username: str, strategy_name: str):
        """6. 查指定账户指定策略详情"""
        print("\n" + "=" * 70)
        print(f"策略详情 - {username} / {strategy_name}")
        print("=" * 70)

        key = f"vnpy:{username}:strategy:{strategy_name}"
        value = self.client.get_value(key)

        if not value:
            print(f"\n未找到策略: {key}")
            return

        try:
            data = json.loads(value)

            # 显示交易品种
            vt_symbol = data.get('vt_symbol', '')
            if vt_symbol:
                conid = vt_symbol.split('.')[0] if '.' in vt_symbol else vt_symbol
                stock_name = self.classifier.get_stock_display_name(conid)
                print(f"\n交易品种: {stock_name}")
                print(f"vt_symbol: {vt_symbol}")

            # 显示持仓概况
            position = float(data.get('实际持仓', 0))
            avg_price = float(data.get('持仓均价', 0))
            current_price = float(data.get('当前行情价格', 0))
            pnl_ratio = float(data.get('当前持仓盈亏比', 0))
            total_pnl = float(data.get('策略累计盈亏', 0))

            print(f"\n【持仓概况】")
            print(f"  实际持仓: {position:,.2f}股")
            print(f"  持仓均价: ${avg_price:,.2f}")
            print(f"  当前价格: ${current_price:,.2f}")
            print(f"  持仓市值: ${position * current_price:,.2f}")
            print(f"  当前盈亏比: {pnl_ratio*100:+.2f}%")
            print(f"  累计盈亏: ${total_pnl:,.2f}")
            print(f"  已使用资金占比: {data.get('已使用资金占比', 0)*100:.2f}%")

            # 显示趋势判断
            trend = data.get('策略当前趋势判断(0为未明确/1为做多/-1为做空)', 0)
            trend_str = {1: '做多', -1: '做空', 0: '未明确'}.get(trend, '未知')
            print(f"  趋势判断: {trend_str}")
            print(f"  允许交易: {'是' if data.get('允许策略交易', False) else '否'}")

            # 显示交易历史
            history = data.get('持仓交易历史', [])
            if history:
                print(f"\n【交易历史】({len(history)} 笔)")
                for i, trade in enumerate(history, 1):
                    print(f"\n  [{i}] {trade.get('交易时间', '')}")
                    print(f"      类型: {trade.get('交易类型', '')} | 方向: {trade.get('方向', '')}")
                    print(f"      价格: ${trade.get('价格', 0):,.2f} | 数量: {trade.get('交易量', 0):,.2f}")
                    print(f"      持仓变化: {trade.get('交易前持仓', 0):,.2f} -> {trade.get('交易后持仓', 0):,.2f}")
                    print(f"      仓位均价: ${trade.get('仓位均价', 0):,.2f}")
                    print(f"      备注: {trade.get('交易备注', '')}")

            # 完整 JSON
            print(f"\n【完整数据】")
            print(json.dumps(data, indent=2, ensure_ascii=False))

        except json.JSONDecodeError as e:
            print(f"JSON 解析失败: {e}")

    def _get_positions_from_strategies(self, username: str) -> dict:
        """从策略数据中获取持仓信息（按品种合并）"""
        strategies = self.client.get_value_by_pattern(username=username, data_type="strategy")

        # 按品种聚合策略持仓
        symbol_strategies = defaultdict(list)

        for item in strategies:
            value = item['value']
            strategy_name = item['key_info']['data_source']

            if value and isinstance(value, str):
                try:
                    data = json.loads(value)
                    position = float(data.get('实际持仓', 0))

                    if position > 0:
                        avg_price = float(data.get('持仓均价', 0))
                        current_price = float(data.get('当前行情价格', 0))
                        pnl_ratio = float(data.get('当前持仓盈亏比', 0))
                        vt_symbol = data.get('vt_symbol', '')
                        symbol = vt_symbol.split('.')[0] if '.' in vt_symbol else vt_symbol

                        position_value = position * current_price

                        symbol_strategies[symbol].append({
                            'strategy_name': strategy_name,
                            'volume': position,
                            'avg_price': avg_price,
                            'current_price': current_price,
                            'value': position_value,
                            'pnl_ratio': pnl_ratio,
                            'vt_symbol': vt_symbol
                        })
                except:
                    continue

        # 合并同一品种
        positions = []
        total_value = 0.0

        for symbol, strategies_list in symbol_strategies.items():
            total_volume = sum(s['volume'] for s in strategies_list)
            total_position_value = sum(s['value'] for s in strategies_list)

            # 加权平均持仓均价
            total_cost = sum(s['volume'] * s['avg_price'] for s in strategies_list)
            weighted_avg_price = total_cost / total_volume if total_volume > 0 else 0

            # 加权平均盈亏比
            total_pnl_value = sum(s['volume'] * s['pnl_ratio'] for s in strategies_list)
            weighted_pnl_ratio = total_pnl_value / total_volume if total_volume > 0 else 0

            strategy_names = [s['strategy_name'] for s in strategies_list]
            current_price = strategies_list[0]['current_price'] if strategies_list else 0
            vt_symbol = strategies_list[0]['vt_symbol'] if strategies_list else symbol

            positions.append({
                'symbol': symbol,
                'volume': total_volume,
                'avg_price': weighted_avg_price,
                'current_price': current_price,
                'value': total_position_value,
                'pnl_ratio': weighted_pnl_ratio,
                'strategies': strategy_names,
                'vt_symbol': vt_symbol
            })
            total_value += total_position_value

        return {
            'positions': positions,
            'total_position_value': total_value,
            'total_position_count': len(positions)
        }

    def _analyze_positions(self, data: dict) -> dict:
        """分析账户持仓（备用方法）"""
        positions_info = {
            'positions': [],
            'total_position_value': 0.0,
            'total_position_count': 0
        }

        if 'positions' in data and isinstance(data['positions'], dict):
            for pos_key, pos_data in data['positions'].items():
                if isinstance(pos_data, dict):
                    volume = 0.0
                    for field in ['volume', '持仓数量', 'size', 'quantity']:
                        if field in pos_data:
                            try:
                                volume = float(pos_data[field])
                                break
                            except:
                                continue

                    price = 0.0
                    for field in ['price', '持仓价格', '持仓均价', 'avg_price']:
                        if field in pos_data:
                            try:
                                price = float(pos_data[field])
                                break
                            except:
                                continue

                    symbol = pos_data.get('symbol', pos_data.get('vt_symbol', pos_key))
                    position_value = volume * price

                    if volume > 0:
                        positions_info['positions'].append({
                            'symbol': symbol,
                            'volume': volume,
                            'price': price,
                            'value': position_value
                        })
                        positions_info['total_position_value'] += position_value
                        positions_info['total_position_count'] += 1

        return positions_info


def main():
    parser = argparse.ArgumentParser(description='股票 Redis 查询工具')
    subparsers = parser.add_subparsers(dest='command', help='可用命令')

    # 1. 所有账户概览
    subparsers.add_parser('overview', help='查所有账户概览')

    # 2. 指定账户信息
    account_parser = subparsers.add_parser('account', help='查指定账户信息和持仓')
    account_parser.add_argument('username', help='用户名')

    # 3. 策略列表
    strategies_parser = subparsers.add_parser('strategies', help='查指定账户策略列表和概要')
    strategies_parser.add_argument('username', help='用户名')

    # 4. 持仓分布
    distribution_parser = subparsers.add_parser('distribution', help='查指定账户持仓分布')
    distribution_parser.add_argument('username', help='用户名')

    # 6. 策略详情
    detail_parser = subparsers.add_parser('detail', help='查指定策略详情')
    detail_parser.add_argument('username', help='用户名')
    detail_parser.add_argument('strategy_name', help='策略名称')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    query = StockRedisQuery()

    if args.command == 'overview':
        query.overview()
    elif args.command == 'account':
        query.account(args.username.replace(' ', ''))
    elif args.command == 'strategies':
        query.strategies(args.username.replace(' ', ''))
    elif args.command == 'distribution':
        query.distribution(args.username.replace(' ', ''))
    elif args.command == 'detail':
        query.detail(args.username.replace(' ', ''), args.strategy_name.replace(' ', ''))


if __name__ == "__main__":
    main()
