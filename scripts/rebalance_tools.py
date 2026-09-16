# -*- coding: utf-8 -*-
"""
rebalance_tools.py — 板块轮动调仓 · 计算与校验工具 (v2.4.1)

全部子命令支持 --market 美股|加密货币（默认 美股，行为与旧版一致；
加密货币预设依据 docs/调仓/加密货币/06_策略资金分配.md §7.3.1 任务参数表与 §7.3.1.5 闭环 6 条、
07 §8 硬上限（高弹性合计≤15%）、08 §9 资金费率时段、02 §4.2.2 轮动阶段因子表：
不动阈值 2%×B / 单轮幅度默认 20 / 不追高 5% / 不买暴涨 30% / invest_mult 默认 1（B ≤ available USDT×2）/
base_capital 默认 1000 / 池 = crypto_pool.json / 基准 = BTC / 动量窗口 20D/7D/5D）。

子命令（全部输出中文键 JSON 到 stdout；纯标准库、无网络、确定性）：
  fund    资金框架（06 §7.3.1：参数自检+自动调参 → 总预算 B → 策略槽位
          → 每策略目标/初始资金 → 资金闭环自检 6 条）
  gap     缺口计算（06 §7.3.1.3：缺口=目标−当前；|缺口|<1%×B 不动；
          按 |缺口| 降序处理、Σ|缺口|/B ≤ max_delta_pct 超出截断；
          max_new 新仓名额；缺口方向与板块分层一致性提示）
          + 资金匹配（卖出释放/买入使用/净额/剩余现金 → 闭环第 3/4 条，
          06 §7.3.1.5，按截断后最终「动作」计）
  verify  调仓报告 JSON 校验门（10 §11.2 数值纯净规则 + 07 §8 硬上限 +
          资金闭环 6 条 + 分批计划自洽 + 模板完整性 + 顶层 %/$ 互验 +
          组合重建（含未映射持仓，不再静默跳过）+ 层级↔动作一致性；
          汇总显式给出「不适用项数 / 未执行检查项」；
          从报告数值独立复算，不信任报告自带「风控校验」结论）
  score   板块评分（02 §4：动量因子 4 窗口 RS vs SPY（40/30/20/10）
          + 经济周期因子（02 §4.2.2 查表）；排序与 强势/中性/弱势 分层，
          Bottom 3 连弱风控（连续 2 周减仓 / 连续 3 周清仓））
  map     持仓映射与 S1–S5 分层（04 §6：ticker→板块/子板块 以
          stock_pool.json 为准 + 层级矩阵 + 仓位占比（分母=B）+ T3 统计）
  exec    执行约束检查与拆单（05 §7.3.3 不追高/不买暴涨 + 07 §8.3 流动性
          + 08 §9.1 拆单档位 + §9.3 滑点 + §9.2 执行时段提示）
  score2  双层子板块评分（03 §5.3/§5.4：最终得分 = 0.5×板块得分 + 0.5×子板块
          独立得分；独立得分 = 动量(四窗口) + 周期因子(继承板块)；双层决策矩阵
          + Top 6-8/Bottom 5-7 分层 + 超板块 1 标准差钳制）
  cycle   双周期再平衡判定（09 §10 / 07 §8.4：月度大再平衡 ±10% /
          周度周五微调 ±3% / 事件驱动，输出 max_delta_pct 建议值）
  assemble 报告骨架装配（10 §11.1：fund + gap（+ score/score2/map）→ 17 顶层
          字段齐全的报告骨架；数值字段工具算（顶层 %/$ 互验自洽），文本字段 /
          调仓指令 / 新增策略 留 AI 填；AI 填完必须 verify --report 复算）
  leaders 龙头确认（仅 加密货币，02 §4.2.5：逐板块取「T2 首位」（无 T2 → 池内首位）
          的 20 日超额 vs 基准；超额 < 0 → 该板块强制降为中性）
          — 组合层手工步骤的工具化，供 00 §4.2 执行顺序使用

用法（金额一律 USD）：
  python rebalance_tools.py fund --balance 30000 --available 30000
  python rebalance_tools.py --balance 30000 --available 30000          # v1 兼容（省略 fund）
  python rebalance_tools.py gap --snapshot gap_snapshot.json --balance 100000 --available 90000
  python rebalance_tools.py verify --report 调仓报告.json --balance 100000 --available 90000
  python rebalance_tools.py score --prices prices.json --cycle 中期扩张 [--top-strong 4] [--bottom3-history hist.json]
  python rebalance_tools.py map --strategies strategies.json --classifications class.json --balance 100000 --invest-mult 2
  python rebalance_tools.py exec --plan exec_plan.json [--liquidity-pct 1]
  python rebalance_tools.py cycle --date 2026-09-18 [--last-rebalance 2026-08-28] [--trigger top3_change]
  python rebalance_tools.py score2 --prices subs.json [--sector-score sector.json] [--cycle 中期扩张]
  python rebalance_tools.py assemble --fund fund.json --gap gap.json [--score s.json] [--score2 s2.json] [--map m.json] [--out 报告.json]

校验门口径（verify）：判定 = 输出 JSON 的「校验」字段（通过/不通过/无法校验），
退出码 0/1/2 仅为辅助；无输出 / 输出非 JSON / 工具自报错 = 不得视为通过。

失效处理（10_AI调仓输出模板.md「失效代码」铁律）：
  工具报错/崩溃 → 输出 {"成功": false, "工具自报错": ...} 或 「校验":"无法校验"；
  调仓任务内不得修改本文件；失效一律记入报告「失效代码」（工具/子命令/命令行/
  错误摘要/影响环节/降级处理），该环节降级为 AI 手工计算并交叉复核。
"""
import argparse
import datetime
import json
import math
import os
import sys
import traceback
from typing import NoReturn

VERSION = "2.5.0"
DOCS_REF = "06_策略资金分配.md §7.3.1 / 02 §4 / 04 §6 / 05 §7.3.3 / 07 §8 / 08 §9 / 09 §10 / 10 §11.2"

PARAM_DEFAULTS = {
    "invest_mult": 2,        # 投入倍率 1-5
    "mode": "auto",          # auto / 积极 / 正常 / 防御
    "max_delta_pct": 10,     # 单轮调仓幅度上限 3-10
    "max_new": 2,            # 本轮新仓只数 0-3
    "max_single_pct": 5,     # 单票上限 1-10
    "max_sector_pct": 20,    # 单板块上限 10-30
    "base_capital": 10000,   # 每策略基准资金 5000-50000
    "max_strategies": "auto" # auto = B ÷ base_capital，硬顶 50
}

PARAM_RANGES = {
    "invest_mult": (1, 5),
    "max_delta_pct": (3, 10),
    "max_new": (0, 3),
    "max_single_pct": (1, 10),
    "max_sector_pct": (10, 30),
    "base_capital": (5000, 50000),
}

# 输出 JSON 的中文键（与 06 §7.3.1 任务参数表命名一致）
PARAM_CN = {
    "invest_mult": "投入倍率",
    "mode": "操作模式",
    "max_delta_pct": "单轮调仓幅度上限",
    "max_new": "本轮新仓只数",
    "max_single_pct": "单票上限",
    "max_sector_pct": "单板块上限",
    "base_capital": "每策略基准资金",
    "max_strategies": "策略总数上限",
}

MODES = ("auto", "积极", "正常", "防御", "aggressive", "normal", "defensive")
MODE_ALIASES = {"aggressive": "积极", "normal": "正常", "defensive": "防御"}
HARD_CAP_STRATEGIES = 50
BUFFER = 1.2  # 每策略初始资金缓冲系数（06 §7.3.1.4）

EPS_MONEY = 1.0   # 美元容差
EPS_PCT = 0.5     # 百分点容差（顶层 %/$ 互验、板块一致性）
PORTFOLIO_TOL = 1.0  # 组合重建容差（价格时点差异）

# 07 §8.1 硬上限（verify 用；分母 = 总预算 B）
HARD_CAP_SUBSECTOR_PCT = 12.0
HARD_CAP_TOP3_PCT = 50.0
HARD_CAP_T3_TOTAL_PCT = 5.0
HARD_CAP_T3_SINGLE_PCT = 1.0

# ---------------------------------------------------------------------
# 02 §4 评分 —— 板块身份 / 命名 / 周期因子以单一事实源 domain_dict.json 为准
# ---------------------------------------------------------------------
# 单一事实源（SSOT）：scripts/domain_dict.json 是板块身份（代码 ↔ 标准中文名 ↔
# 简称/别名 ↔ 经济周期因子）的唯一权威。02 §4.2.2 周期因子表、03 子板块评分、
# 04 持仓映射、score / score2 / map 全部读这里；改板块命名只改该文件。
DOMAIN_DICT_FILE = "domain_dict.json"

# 事实源缺失时的内置兜底（与 domain_dict.json v1.0 同值；仅保证工具可运行，
# 一旦命中兜底会写入 数据缺失，提示人工恢复 domain_dict.json）
_FALLBACK_SECTORS = {
    "XLK":  ("信息技术", "科技", {"早期复苏": 1, "中期扩张": 2, "晚期过热": 0, "衰退": -2}),
    "XLF":  ("金融", "金融", {"早期复苏": 2, "中期扩张": 1, "晚期过热": -1, "衰退": -2}),
    "XLV":  ("医疗保健", "医疗", {"早期复苏": 0, "中期扩张": 0, "晚期过热": 1, "衰退": 2}),
    "XLY":  ("消费可选", "消费可选", {"早期复苏": 2, "中期扩张": 1, "晚期过热": -1, "衰退": -2}),
    "XLP":  ("消费必需", "消费必需", {"早期复苏": -1, "中期扩张": 0, "晚期过热": 1, "衰退": 2}),
    "XLE":  ("能源", "能源", {"早期复苏": -1, "中期扩张": 1, "晚期过热": 2, "衰退": -1}),
    "XLI":  ("工业", "工业", {"早期复苏": 2, "中期扩张": 1, "晚期过热": 0, "衰退": -1}),
    "XLB":  ("材料", "材料", {"早期复苏": 1, "中期扩张": 1, "晚期过热": 1, "衰退": -2}),
    "XLU":  ("公用事业", "公用事业", {"早期复苏": -2, "中期扩张": -1, "晚期过热": 1, "衰退": 2}),
    "XLRE": ("房地产", "房地产", {"早期复苏": -1, "中期扩张": 0, "晚期过热": 0, "衰退": 1}),
    "XLC":  ("通信服务", "通信", {"早期复苏": 1, "中期扩张": 1, "晚期过热": 0, "衰退": -1}),
}
_FALLBACK_CYCLES = ("早期复苏", "中期扩张", "晚期过热", "衰退")


def default_domain_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), DOMAIN_DICT_FILE)


def load_domain_dict(path=None):
    """读取板块单一事实源；返回 (dict|None, err|None)。

    自包含读文件（不依赖后面的 load_json_file）——本函数在模块导入期被调用。
    """
    p = path or default_domain_path()
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception as e:
        return None, "%s 不可读: %s" % (p, e)
    if not isinstance(d, dict) or not isinstance(d.get("sectors"), dict) or not d["sectors"]:
        return None, "%s 结构不符（缺 sectors）" % p
    return d, None


_DOMAIN, DOMAIN_ERR = load_domain_dict()

def _build_from_domain(d):
    """domain_dict → (cycles, 标准名, 简称, 别名索引, 周期因子表)"""
    cycles = tuple(d.get("cycles") or _FALLBACK_CYCLES)
    names, short, alias, cyc_scores = {}, {}, {}, {}
    for code, v in d["sectors"].items():
        code = str(code).upper()
        names[code] = str(v["name"])
        short[code] = str(v.get("name_short") or v["name"])
        for a in [v.get("name"), v.get("name_short"), v.get("name_en")] + list(v.get("aliases") or []):
            if a:
                alias[str(a).strip()] = code
        alias[code] = code
        for cy, f in (v.get("cycle_factor") or {}).items():
            cyc_scores.setdefault(str(cy), {})[code] = f
    return cycles, names, short, alias, cyc_scores


def _build_fallback():
    """事实源缺失时的内置兜底（与 domain_dict.json v1.0 同值）"""
    names = {c: v[0] for c, v in _FALLBACK_SECTORS.items()}
    short = {c: v[1] for c, v in _FALLBACK_SECTORS.items()}
    alias = {}
    for c, v in _FALLBACK_SECTORS.items():
        alias[v[0]] = c
        alias[v[1]] = c
        alias[c] = c
    cyc_scores = {cy: {c: v[2].get(cy) for c, v in _FALLBACK_SECTORS.items()}
                  for cy in _FALLBACK_CYCLES}
    return _FALLBACK_CYCLES, names, short, alias, cyc_scores


CYCLES, SECTOR_NAMES, SECTOR_SHORT, SECTOR_ALIAS, CYCLE_SCORES = (
    _build_from_domain(_DOMAIN) if _DOMAIN else _build_fallback())


def sector_code(name):
    """任意板块写法（标准中文名 / 简称 / 英文 / ETF 代码）→ ETF 代码；未知返回 None。"""
    if name is None:
        return None
    s = str(name).strip()
    if not s:
        return None
    return SECTOR_ALIAS.get(s) or (s.upper() if s.upper() in SECTOR_NAMES else None)


def canon_sector(name):
    """任意板块写法 → 标准中文名（落库/落报告的权威写法）；无法识别时原样返回。"""
    c = sector_code(name)
    if c:
        return SECTOR_NAMES[c]
    return str(name).strip() if name is not None else name


# ---------------------------------------------------------------------
# 市场预设（--market 美股|加密货币，2026-09-15 新增）
# 加密货币预设值依据 docs/调仓/加密货币/06_策略资金分配.md §7.3.1 任务参数表、
# §7.3.1.5 闭环 6 条、07 §8 硬上限、08 §9 时段、02 §4.2.2 轮动阶段因子表；
# 改预设只改这里，美股预设与 v2.3 行为逐项一致（回归测试兜底）。
# ---------------------------------------------------------------------
MARKETS = ("美股", "加密货币")
MARKET_ALIASES = {"us": "美股", "us_stocks": "美股", "crypto": "加密货币"}

CRYPTO_CYCLES = ("早期", "中期", "晚期")

# 加密货币轮动阶段因子表（镜像 加密货币/02 §4.2.2；板块名以 crypto_pool.json categories 为准）
CRYPTO_CYCLE_SCORES = {
    "早期": {"L1 公链": 2, "L2/模块化": 0, "DeFi": 0, "Meme": -2, "基础设施/AI": 0,
           "交易所平台币": 1, "支付/隐私": 1, "RWA": 0, "GameFi": -1, "DePIN": -1},
    "中期": {"L1 公链": 1, "L2/模块化": 2, "DeFi": 2, "Meme": 0, "基础设施/AI": 1,
           "交易所平台币": 0, "支付/隐私": 0, "RWA": 1, "GameFi": 0, "DePIN": 1},
    "晚期": {"L1 公链": -1, "L2/模块化": 1, "DeFi": -1, "Meme": 2, "基础设施/AI": 1,
           "交易所平台币": -1, "支付/隐私": 0, "RWA": 1, "GameFi": 1, "DePIN": 0},
}

MARKET_PRESETS: dict = {
    "美股": {
        "currency": "USD",
        "report_name": "美股板块轮动调仓",
        "defaults": {"invest_mult": 2, "max_delta_pct": 10, "base_capital": 10000},
        "ranges": {"invest_mult": (1, 5), "max_delta_pct": (3, 10), "base_capital": (5000, 50000)},
        "base_floor": 5000.0, "base_step": 1000.0,
        "acct_cap": 5.0,
        "loop2_kind": "sigma",        # 闭环②：Σ初始资金 ≤ available×5（IB 购买力）
        "no_move_pct": 1.0,
        "chase_high_pct": 3.0, "spike_pct": 15.0,
        "ampl_monthly": 10.0, "ampl_weekly": 3.0, "ampl_event": 10.0,
        "windows": (("6M", 126), ("3M", 63), ("12M", 252), ("20D", 20)),
        "mom_weights": {"6M": 0.40, "3M": 0.30, "12M": 0.20, "20D": 0.10},
        "benchmark": "SPY",
        "pool_file": "stock_pool.json",
        "pool_kind": "us",
        "cycles": CYCLES,
        "cycle_scores": CYCLE_SCORES,
        "subsector_cap_pct": 12.0,
        "hb_buckets": (), "hb_total_pct": None, "hb_single_pct": {},
        "exec_time_key": "执行时间ET",
        "avoid_windows": None,        # None → 用全局 AVOID_WINDOWS（美东）
        "good_windows": None,         # None → 用全局 GOOD_WINDOWS（美东）
        "outside_note": True,         # 交易时段之外提示（美股 9:30-16:00）
        "ccy_note": "一律 USD 口径（多币种换算由取数环节完成，见 06 §7.3.1.1 币种规则）",
        "base_ccy": "USD", "fx_note": 1.0,
    },
    "加密货币": {
        "currency": "USDT",
        "report_name": "加密货币板块轮动调仓",
        "defaults": {"invest_mult": 1, "max_delta_pct": 20, "base_capital": 1000},
        "ranges": {"invest_mult": (1, 2), "max_delta_pct": (5, 20), "base_capital": (100, 10000)},
        "base_floor": 100.0, "base_step": 100.0,
        "acct_cap": 2.0,
        "loop2_kind": "B",            # 闭环②：B ≤ available USDT × 2（账户层杠杆硬顶）
        "no_move_pct": 2.0,
        "chase_high_pct": 5.0, "spike_pct": 30.0,
        "ampl_monthly": 20.0, "ampl_weekly": 5.0, "ampl_event": 20.0,
        "windows": (("20D", 20), ("7D", 7), ("5D", 5)),
        "mom_weights": {"20D": 0.50, "7D": 0.30, "5D": 0.20},
        "benchmark": "BTC",
        "pool_file": "crypto_pool.json",
        "pool_kind": "crypto",
        "cycles": CRYPTO_CYCLES,
        "cycle_scores": CRYPTO_CYCLE_SCORES,
        "subsector_cap_pct": None,    # 无单子板块 12%；改走高弹性规则（07 §8.1）
        "hb_buckets": ("31-60", "60+"), "hb_total_pct": 15.0,
        "hb_single_pct": {"60+": 2.0, "31-60": 3.0},
        "exec_time_key": "执行时间UTC",
        "avoid_windows": ((23 * 60 + 30, 24 * 60, "资金费率结算 00:00 UTC ±30min"),
                          (0, 30, "资金费率结算 00:00 UTC ±30min"),
                          (7 * 60 + 30, 8 * 60 + 30, "资金费率结算 08:00 UTC ±30min"),
                          (15 * 60 + 30, 16 * 60 + 30, "资金费率结算 16:00 UTC ±30min"),
                          (13 * 60 + 30, 15 * 60, "美股开盘高波动窗口 13:30-15:00 UTC")),
        "good_windows": ((4 * 60, 8 * 60, "亚盘 04:00-08:00 UTC"),
                         (11 * 60, 14 * 60, "欧盘 11:00-14:00 UTC")),
        "outside_note": False,        # 7×24 无休市
        "ccy_note": "一律 USDT 口径（单币种，无汇率换算）",
        "base_ccy": "USDT", "fx_note": "不适用（单币种）",
    },
}


def resolve_market(v):
    if v is None:
        return "美股"
    return MARKET_ALIASES.get(str(v).strip(), str(v).strip())


BUCKET_KEYS = ("top10", "11-30", "31-60", "60+")


def bucket_of(subsec):
    """子维度写法（'L1 公链-31-60' / '31-60'）→ 分桶名；不可识别 → None"""
    s = str(subsec or "").strip()
    if not s:
        return None
    for b in BUCKET_KEYS:
        if s == b or s.endswith("-" + b):
            return b
    return None


def add_market_arg(ap):
    ap.add_argument("--market", default="美股", choices=list(MARKETS) + list(MARKET_ALIASES),
                    help="市场预设（加密货币 = 调仓/加密货币 文档组阈值/池/verify 规则；默认 美股，行为不变）")


SCOR_WINDOWS = (("6M", 126), ("3M", 63), ("12M", 252), ("20D", 20))
MOM_WEIGHTS = {"6M": 0.40, "3M": 0.30, "12M": 0.20, "20D": 0.10}
FACTOR_WEIGHTS = {"动量": 0.30, "周期": 0.25}

ACTIONS = ("加仓", "减仓", "清仓", "开仓", "维持")
LAYERS = ("S1 强烈加仓", "S2 适度加仓", "S3 维持", "S4 观察减仓", "S5 优先减仓")
TOP_LEVEL_KEYS = ("market", "report_name", "analysis_time", "操作模式", "任务参数",
                  "分析摘要", "持仓映射", "资金现状", "目标分配", "调仓指令", "新增策略",
                  "资金汇总", "风控校验", "数据缺失与冲突", "待人工确认", "失效代码", "执行建议")
SUBCOMMANDS = ("fund", "gap", "verify", "score", "map", "exec", "cycle", "score2", "assemble", "leaders")

# 中文任务参数键 → 报告模板英文键（10 §11.1 任务参数）
EN_PARAMS = {v: k for k, v in PARAM_CN.items()}


def g(x):
    """数值紧凑格式化（JSON 里统一保留 2 位以内）"""
    return round(x, 2) if isinstance(x, float) else x


def num(v):
    """宽松数值提取：int/float/数字串 → float；bool/None/其他 → None"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", "").strip())
        except ValueError:
            return None
    return None


def load_json_file(path, label="文件"):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), None
    except Exception as e:
        return None, "%s 不可读: %s" % (label, e)


_OUT_FILE = None  # --out 模式（v2.5.0）：全量结果写文件，stdout 只留摘要


def _brief_rows(rows):
    """明细逐行精简：每行保留前 6 个键 + 状态/通过/建议（长串截 100 字）"""
    out = []
    for r in rows:
        if not isinstance(r, dict):
            out.append(r)
            continue
        d = {}
        for k, v in r.items():
            if len(d) >= 6:
                break
            if isinstance(v, str) and len(v) > 100:
                v = v[:100] + "…"
            d[k] = v
        for k in ("状态", "通过", "建议"):
            if k in r and k not in d:
                d[k] = r[k]
        out.append(d)
    return out


def summarize(obj):
    """--out 模式 stdout 摘要：保留 AI 判读所需键，大字段（明细/输入/预算/策略槽位…）降级"""
    if not isinstance(obj, dict):
        return obj
    out = {"_摘要": True, "成功": obj.get("成功"), "工具": obj.get("工具"),
           "市场": obj.get("市场"), "已存文件": _OUT_FILE}
    if "明细" in obj:
        out["明细"] = _brief_rows(obj["明细"])
    for k, v in obj.items():
        if k in ("明细", "输入", "参数自检", "预算", "策略槽位", "资金闭环自检",
                 "节奏参考", "时段参考", "报告", "明细_全量"):
            continue
        if isinstance(v, list):
            out[k] = {"条数": len(v), "前8": v[:8]} if len(v) > 8 else v
        else:
            out[k] = v
    return out


def emit(obj, code=0) -> NoReturn:
    if _OUT_FILE and isinstance(obj, dict) and obj.get("成功") is True:
        try:
            with open(_OUT_FILE, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
        except OSError as e:
            fail("写文件失败: %s" % e, code=1)
        print(json.dumps(summarize(obj), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    sys.exit(code)


def parsed(ap, argv, cmd):
    """argparse 统一入口：用法错误 → 失败 JSON（退出码 2），保证 stdout 恒为单个 JSON"""
    try:
        return ap.parse_args(argv)
    except SystemExit:
        fail("参数解析失败：参数缺失或取值不在允许范围（用法提示见 stderr）",
             code=2, **{"工具": "rebalance_tools %s" % cmd})


def fail(msg, code=1, **extra) -> NoReturn:
    d = {"成功": False, "错误": msg}
    d.update(extra)
    emit(d, code)


def has_cjk(s):
    """中文/全角字符检测（纯净性检查用）"""
    for c in s:
        o = ord(c)
        if 0x4E00 <= o <= 0x9FFF or 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF:
            return True
    return False


def default_pool_path(market="美股"):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        MARKET_PRESETS[market]["pool_file"])


def load_pool_index(path, market="美股"):
    """选股池 → {ticker: {板块, 子板块, T层, gics[, bucket]}}；返回 (索引|None, err|None)。

    美股 stock_pool.json：子板块 = Industry Group 名；
    加密货币 crypto_pool.json：板块 = category（叙事板块），子板块 = "板块-市值分桶"
    组合名（如 "L1 公链-31-60"），另附 bucket/chain 字段（07 §8.1 高弹性约束用）。
    """
    pool, err = load_json_file(path, "选股池")
    if err or not isinstance(pool, dict):
        return None, err or "选股池不是 JSON 对象"
    idx = {}
    if market == "加密货币":
        for sym, v in (pool.get("coins") or {}).items():
            if not isinstance(v, dict):
                continue
            cat = v.get("category")
            if not cat:
                continue
            bucket = v.get("mcap_bucket")
            idx[str(sym)] = {"板块": cat,
                             "子板块": ("%s-%s" % (cat, bucket)) if bucket else None,
                             "T层": v.get("tier"), "gics": None,
                             "bucket": bucket, "chain": v.get("chain")}
        return idx, None
    for code, v in (pool.get("sub_sectors") or {}).items():
        if not isinstance(v, dict):
            continue
        for tier, tickers in (v.get("tiers") or {}).items():
            if not isinstance(tickers, list):
                continue
            for t in tickers:
                idx[str(t)] = {"板块": v.get("sector"), "子板块": v.get("name"),
                               "T层": tier, "gics": code}
    return idx, None


def s_layer(sec, sub, market="美股"):
    """04 §6.2 五层矩阵 → (层级, 依据/备注)；矩阵按市场取（加密货币组 04 §6.2 覆盖全部 9 组合）"""
    if market == "加密货币":
        if sec == "强势" and sub == "强势":
            return "S1 强烈加仓", "板块强 + 子维度强"
        if sec == "强势" and sub == "中性":
            return "S2 适度加仓", "板块强 + 子维度中"
        if sec == "强势" and sub == "弱势":
            return "S3 维持", "板块强 + 子维度弱（持有观察，不加仓）"
        if sec == "中性" and sub == "强势":
            return "S2 适度加仓", "板块中性 + 子维度强"
        if sec == "中性" and sub == "中性":
            return "S3 维持", "板块中性 + 子维度中"
        if sec == "中性" and sub == "弱势":
            return "S4 观察减仓", "板块中性 + 子维度弱"
        if sec == "弱势" and sub == "强势":
            return "S4 观察减仓", "板块弱 + 子维度强（保留底仓，不加仓）"
        if sec == "弱势" and sub == "中性":
            return "S5 优先减仓", "板块弱 + 子维度中（弱势板块，按 05 §7.2 节奏减仓）"
        if sec == "弱势" and sub == "弱势":
            return "S5 优先减仓", "板块弱 + 子维度弱（双重弱势）"
        return None, "分层缺失（板块=%s 子维度=%s）" % (sec, sub)
    if sec == "强势" and sub == "强势":
        return "S1 强烈加仓", "板块强 + 子板块强"
    if sec == "强势" and sub == "中性":
        return "S2 适度加仓", "板块强 + 子板块中性"
    if sec == "中性":
        return "S3 维持", "板块中性 + 子板块%s" % sub
    if sec == "弱势" and sub == "强势":
        return "S4 观察减仓", "板块弱 + 子板块强（保留底仓，不加仓）"
    if sec == "弱势" and sub == "弱势":
        return "S5 优先减仓", "板块弱 + 子板块弱（双重弱势）"
    if sec == "弱势" and sub == "中性":
        return "S4 观察减仓", "矩阵未覆盖组合（板块弱+子板块中性）：按 07 §8.2 弱势板块减仓节奏执行，AI 可上调至 S5"
    return None, "分层缺失（板块=%s 子板块=%s）" % (sec, sub)


# =====================================================================
# fund — 资金框架（v1 原有逻辑，行为不变）
# =====================================================================

def adjust_params(p, B, mk=None):
    """§7.3.1.2 自检：base_capital ≤ max_single_pct% × B。

    违反时按文档补救规则两步调整（留痕）：
      1) 优先调低 base_capital（向 市场档位整数 靠拢，下限 = 市场 base_floor）
      2) 下限仍不足 → 抬 max_single_pct 至 ceil(base_capital/B)（上限 10）
      3) 抬到 10 仍不满足 → 返回 None（账户规模低于方法论最小要求）
    返回 (violations, adjustments, ok)
    """
    mk = mk or MARKET_PRESETS["美股"]
    violations = []
    adjustments = []
    cap = B * p["max_single_pct"] / 100.0
    if p["base_capital"] > cap:
        violations.append(
            "每策略基准资金(%s) > 单票上限%%×B(%s%%×%s=%s)"
            % (g(p["base_capital"]), g(p["max_single_pct"]), g(B), g(cap))
        )
        # 步骤 1：优先调低 base_capital
        old_base = p["base_capital"]
        new_base = max(mk["base_floor"], math.floor(cap / mk["base_step"]) * mk["base_step"])
        if new_base < old_base:
            p["base_capital"] = new_base
            adjustments.append({"参数": "每策略基准资金", "原值": old_base,
                                "新值": new_base,
                                "规则": "降至 单票上限%%×B 的 %d 整数档" % int(mk["base_step"])})
        # 步骤 2：下限仍不足 → 抬 max_single_pct
        if p["base_capital"] > cap:
            new_pct = math.ceil(p["base_capital"] / B * 100.0)
            if new_pct > 10:
                return violations, adjustments, None
            adjustments.append({"参数": "单票上限",
                                "原值": p["max_single_pct"], "新值": new_pct,
                                "规则": "上取整(基准资金÷B)，上限 10"})
            p["max_single_pct"] = new_pct
    return violations, adjustments, True


def calc_budget(balance, available, p, mk=None):
    """§7.3.1.2：总预算 B = 权益 × invest_mult；目标仓位总和 = B ÷ 1.2（防御压至 50%）"""
    mk = mk or MARKET_PRESETS["美股"]
    m = p["invest_mult"]
    B = balance * m
    target_total = B / BUFFER
    defensive_applied = False
    if p["mode"] == "防御":
        capped = B * 0.5
        if target_total > capped:
            target_total = capped
            defensive_applied = True
    margin_usage = max(0.0, target_total - available)
    return {
        "投入倍率": m,
        "总预算B": g(B),
        "目标仓位总和": g(target_total),
        "目标仓位占B百分比": g(target_total / B * 100),
        "防御上限已启用": defensive_applied,
        "保证金占用": g(margin_usage),
        "购买力上限": g(available * mk["acct_cap"]),
    }


def alloc_slots(B, target_total, p):
    """单位分解：N 个策略 × k 个基准单位 = 目标仓位总和。

    返回 (slots, error)；error 非 None 时 slots 为 None。
    """
    base = p["base_capital"]
    if p["max_strategies"] == "auto":
        max_strategies = min(int(B // base), HARD_CAP_STRATEGIES)
        max_strategies_rule = "auto = B ÷ 基准资金（%s），硬顶 %d" % (g(B // base), HARD_CAP_STRATEGIES)
    else:
        max_strategies = p["max_strategies"]
        max_strategies_rule = "任务指定 %d（硬顶 %d）" % (max_strategies, HARD_CAP_STRATEGIES)

    unit = base
    U_total = int(math.floor(target_total / unit + 1e-9))  # 可分配的基准单位总数
    if U_total < 1:
        return None, ("目标仓位总和(%s) < 每策略基准资金(%s)，最小开仓门槛无法达到（账户规模过小）"
                      % (g(target_total), g(unit)))

    single_cap = B * p["max_single_pct"] / 100.0
    k_max = max(1, int(math.floor(single_cap / unit + 1e-9)))  # 每策略最多单位数（单票上限约束）

    N = min(U_total, max_strategies)
    if U_total > N * k_max:
        need = math.ceil(U_total / k_max)
        return None, ("策略总数上限=%d 过小：%d 个基准单位每策略最多 %d 单位，需 ≥%d 个策略"
                      % (N, U_total, k_max, need))

    q, r = divmod(U_total, N)
    units = [q + 1] * r + [q] * (N - r)  # 保高弃低：前 r 个策略多 1 单位（T1 优先）
    targets = [unit * u for u in units]
    initials = [int(math.floor(unit * u * BUFFER + 1e-9)) for u in units]
    sigma_initial = sum(initials)

    # 组合压缩表示
    if len(set(units)) == 1:
        composition = "%d×%d 单位" % (N, units[0])
        per_strategy = {"单位数": units[0], "目标资金": g(targets[0]), "初始资金": initials[0]}
    else:
        comp = {}
        for u in sorted(set(units), reverse=True):
            comp[u] = units.count(u)
        composition = " + ".join("%d×%d 单位" % (n, u) for u, n in comp.items())
        per_strategy = [{"单位数": u, "目标资金": g(t), "初始资金": i}
                        for u, t, i in zip(units, targets, initials)]

    return {
        "基准资金": g(unit),
        "单单位占B百分比": g(unit / B * 100),
        "策略数上限": max_strategies,
        "策略数上限规则": max_strategies_rule,
        "单票上限金额": g(single_cap),
        "每策略最多单位数": k_max,
        "策略数": N,
        "组合": composition,
        "每策略": per_strategy,
        "目标资金合计": g(sum(targets)),
        "初始资金合计": g(sigma_initial),
    }, None


def sector_layout_example(slots, p):
    """板块落法示例（仅示例，实际板块 % 由 AI 按评分档 × 模式区间表决定）"""
    N = slots["策略数"]
    unit_pct = slots["单单位占B百分比"]
    if N % 2 == 0:
        n_sectors = N // 2
        layout = "%d 板块 × 2 只" % n_sectors
    else:
        n_sectors = (N + 1) // 2
        layout = "%d 板块 × 2 只 + 1 板块 × 1 只" % (n_sectors - 1)
    sector_pct = 2 * unit_pct
    top3_pct = min(3, n_sectors) * sector_pct
    return {
        "示例": layout,
        "单板块占比": g(sector_pct),
        "前三大板块占比": g(top3_pct),
        "单板块上限校验": sector_pct <= p["max_sector_pct"],
        "前三大板块50%校验": top3_pct <= 50.0,
        "说明": "示例仅供参考；弱势板块目标=0、板块只数/占比由 AI 按 06 §7.3.1.2 区间表与场景强度决定",
    }


def closed_loop_check(B, available, p, budget, slots, mk=None):
    """§7.3.1.5 资金闭环自检 6 条。状态: 通过/不通过/待执行/不适用（后两者不影响「全部通过」）

    第 2 条按市场取：美股 = Σ初始资金 ≤ 购买力 available×5（IB）；
    加密货币 = B ≤ 可用USDT × 2（账户层杠杆硬顶，加密货币/06 §7.3.1.5 ②）。
    """
    mk = mk or MARKET_PRESETS["美股"]
    m = p["invest_mult"]
    sigma = slots["初始资金合计"]
    items = []

    def item(name, calc, status):
        items.append({"项目": name, "计算": calc, "状态": status})

    # 1 预算
    item("Σ 初始资金 ≤ 总预算 B", "%s ≤ %s" % (g(sigma), g(B)),
         "通过" if sigma <= B + 1e-9 else "不通过")
    # 2 购买力/账户层杠杆
    if mk["loop2_kind"] == "sigma":
        item("Σ 初始资金 ≤ 购买力 available×5", "%s ≤ %s" % (g(sigma), g(available * 5)),
             "通过" if sigma <= available * 5 + 1e-9 else "不通过")
    else:
        item("B ≤ 可用%s × %s（账户层杠杆硬顶）" % (mk["currency"], g(mk["acct_cap"])),
             "%s ≤ %s" % (g(B), g(available * mk["acct_cap"])),
             "通过" if B <= available * mk["acct_cap"] + 1e-9 else "不通过")
    # 3 资金来源（需执行指令，v2 gap 子命令补全）
    item("买入使用 ≤ 卖出释放 + 可用现金×投入倍率",
         "待执行指令后确定（gap/verify 子命令补全）", "待执行")
    # 4 现金下限（仅无保证金）
    if m == 1:
        remaining = available - slots["目标资金合计"]  # 最坏情形：无卖出释放
        item("剩余现金 ≥ B×5%（仅 投入倍率=1）",
             "最坏情形(无卖出释放) %s ≥ %s" % (g(remaining), g(B * 0.05)),
             "通过" if remaining >= B * 0.05 - 1e-9 else "不通过")
    else:
        item("现金下限（仅 投入倍率=1）", "不适用：保证金模式，现金可为负", "不适用")
    # 5 每策略缓冲
    detail = slots["每策略"]
    pairs = detail if isinstance(detail, list) else [detail] * slots["策略数"]
    ok5 = all(i["初始资金"] >= i["目标资金"] * BUFFER - 1e-9 for i in pairs)
    item("每条 初始资金 ≥ %s目标资金×1.2" % ("个股" if mk["pool_kind"] == "us" else "币"),
         "%s" % ("全部满足" if ok5 else "存在不足"), "通过" if ok5 else "不通过")
    # 6 基准与数量
    min_target = min(i["目标资金"] for i in pairs)
    ok6 = (min_target >= p["base_capital"] - 1e-9
           and slots["策略数"] <= B / p["base_capital"] + 1e-9
           and slots["策略数"] <= HARD_CAP_STRATEGIES)
    item("每策略目标资金 ≥ 基准资金 且 策略数 ≤ B÷基准资金（硬顶50）",
         "min(%s) ≥ %s 且 %d ≤ %s" % (g(min_target), g(p["base_capital"]),
                                      slots["策略数"], g(B / p["base_capital"])),
         "通过" if ok6 else "不通过")
    return items


def parse_fund(argv):
    ap = argparse.ArgumentParser(
        description="板块轮动调仓 · 资金框架计算（--market 美股|加密货币；%s）" % DOCS_REF,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    add_market_arg(ap)
    ap.add_argument("--balance", type=float, required=True,
                    help="账户权益 balance（账户权益币种：美股 USD / 加密货币 USDT）")
    ap.add_argument("--available", type=float, required=True,
                    help="可用现金 available（同币种）")
    ap.add_argument("--invest-mult", type=float, default=None,
                    help="缺省 = 市场预设（美股 2 / 加密货币 1）")
    ap.add_argument("--mode", default=PARAM_DEFAULTS["mode"], choices=MODES)
    ap.add_argument("--max-delta-pct", type=float, default=None,
                    help="缺省 = 市场预设（美股 10 / 加密货币 20）")
    ap.add_argument("--max-new", type=int, default=PARAM_DEFAULTS["max_new"])
    ap.add_argument("--max-single-pct", type=float, default=PARAM_DEFAULTS["max_single_pct"])
    ap.add_argument("--max-sector-pct", type=float, default=PARAM_DEFAULTS["max_sector_pct"])
    ap.add_argument("--base-capital", type=float, default=None,
                    help="缺省 = 市场预设（美股 10000 / 加密货币 1000）")
    ap.add_argument("--max-strategies", default="auto",
                    help="auto 或 1-50 的整数")
    ap.add_argument("--no-adjust", action="store_true",
                    help="参数自检不通过时只报错、不自动调参")
    return parsed(ap, argv, "fund")


def run_fund(argv):
    args = parse_fund(argv)
    market = resolve_market(args.market)
    mk = MARKET_PRESETS[market]

    if args.balance <= 0:
        print(json.dumps({"成功": False, "错误": "balance 必须 > 0"}, ensure_ascii=False, indent=2))
        sys.exit(1)
    if args.available < 0:
        print(json.dumps({"成功": False, "错误": "available 必须 ≥ 0"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    p = {
        "invest_mult": args.invest_mult if args.invest_mult is not None else mk["defaults"]["invest_mult"],
        "mode": MODE_ALIASES.get(args.mode, args.mode),
        "max_delta_pct": args.max_delta_pct if args.max_delta_pct is not None
                         else mk["defaults"]["max_delta_pct"],
        "max_new": args.max_new,
        "max_single_pct": args.max_single_pct,
        "max_sector_pct": args.max_sector_pct,
        "base_capital": args.base_capital if args.base_capital is not None
                        else mk["defaults"]["base_capital"],
        "max_strategies": args.max_strategies if args.max_strategies == "auto" else int(args.max_strategies),
    }

    # 参数范围校验（文档 §7.3.1 范围；市场差异项取市场预设）
    ranges = dict(PARAM_RANGES)
    ranges.update(mk["ranges"])
    for k, (lo, hi) in ranges.items():
        v = p[k]
        if not isinstance(v, (int, float)):
            continue
        if v < lo or v > hi:
            print(json.dumps({"成功": False,
                              "错误": "参数 %s(%s)=%s 超出范围 [%s, %s]" % (PARAM_CN[k], k, v, lo, hi)},
                             ensure_ascii=False, indent=2))
            sys.exit(1)
    ms = p["max_strategies"]
    if ms != "auto" and (not isinstance(ms, int) or not 1 <= ms <= HARD_CAP_STRATEGIES):
        print(json.dumps({"成功": False, "错误": "max_strategies 须为 auto 或 1-50"},
                         ensure_ascii=False, indent=2))
        sys.exit(1)

    B = args.balance * p["invest_mult"]

    # 参数自检（自动调参 + 留痕）
    violations, adjustments, ok_adjust = adjust_params(p, B, mk)
    param_check = {
        "通过": not violations,
        "违规项": violations,
        "调整记录": adjustments,
        "已应用": (not args.no_adjust) and bool(adjustments),
        "调整策略": "只警告不调整（--no-adjust）" if args.no_adjust else "自动调整并留痕（06 §7.3.1.2 补救规则）",
    }
    if violations and args.no_adjust:
        print(json.dumps({"成功": False, "错误": "参数自检不通过（--no-adjust 模式不自动调参）",
                          "参数自检": param_check}, ensure_ascii=False, indent=2))
        sys.exit(1)
    if violations and not ok_adjust:
        if market == "美股":
            min_err = ("账户规模低于方法论最小要求：每策略基准资金下限 5000 需 B ≥ 50000"
                       "（投入倍率=2 时权益 ≥ 25000 USD），且 单票上限 10 仍无法容纳")
        else:
            min_err = ("账户规模低于加密货币方法论最小要求：每策略基准资金下限 %d USDT，"
                       "需 B ≥ %d USDT，且 单票上限 10 仍无法容纳（加密货币/06 §7.3.1.2）"
                       % (int(mk["base_floor"]), int(mk["base_floor"] / 0.05)))
        print(json.dumps({"成功": False, "错误": min_err, "参数自检": param_check},
                         ensure_ascii=False, indent=2))
        sys.exit(1)

    budget = calc_budget(args.balance, args.available, p, mk)
    slots, err = alloc_slots(B, budget["目标仓位总和"], p)
    if err or slots is None:
        print(json.dumps({"成功": False, "错误": err,
                          "参数自检": param_check, "预算": budget},
                         ensure_ascii=False, indent=2))
        sys.exit(1)
    slots["板块落法示例"] = sector_layout_example(slots, p)
    checks = closed_loop_check(B, args.available, p, budget, slots, mk)
    all_passed = all(c["状态"] != "不通过" for c in checks)

    result = {
        "成功": True,
        "工具": "rebalance_tools fund (v" + VERSION + ")",
        "版本": VERSION,
        "市场": market,
        "文档依据": DOCS_REF,
        "输入": {
            "权益balance": g(args.balance),
            "可用现金available": g(args.available),
            "币种说明": mk["ccy_note"],
            "任务参数": {PARAM_CN[k]: g(v) if isinstance(v, float) else v for k, v in p.items()},
            "参数说明": "单轮调仓幅度上限 / 本轮新仓只数 在执行阶段生效（gap 子命令）",
        },
        "参数自检": param_check,
        "预算": budget,
        "策略槽位": slots,
        "资金闭环自检": checks,
        "全部通过": all_passed,
    }
    emit(result)


# =====================================================================
# gap — 缺口计算（06 §7.3.1.3）
# =====================================================================

def run_gap(argv):
    ap = argparse.ArgumentParser(
        prog="rebalance_tools gap",
        description="缺口计算（06 §7.3.1.3）：缺口 = 目标资金 − 当前市值；"
                    "|缺口| < 市场不动阈值×B（美股 1% / 加密货币 2%）→ 维持；"
                    "按 |缺口| 降序处理，Σ|缺口|/B 超 max_delta_pct 的其余票截断为「维持+触发条件」；max_new 新仓名额",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "snapshot JSON 格式（AI 由 cta_strategies_get_all + 目标分配整理）：\n"
            '[{"策略名称":"MARTIN-AMD","板块":"信息技术","板块分层":"强势",\n'
            '  "当前市值":5000,"目标资金":10000}]\n'
            "板块分层 ∈ 强势/中性/弱势（score 输出或 AI 判断）；当前市值/目标资金 单位 = 账户币种；\n"
            "目标资金 = 落地口径（＝目标持仓 × 现价；见 加密货币/06 §7.3.1.2「两口径落位表」）；\n"
            "本轮拟开新仓只放 ≤ max_new 个、并按板块优先级排列 —— max_new 名额按 |缺口| 降序占用，\n"
            "混入非优先候选会被其更大的缺口挤占（2026-09-15 验收复盘，06 §7.3.1.6）"))
    add_market_arg(ap)
    ap.add_argument("--snapshot", required=True, help="持仓快照 JSON 数组（格式见 epilog）")
    ap.add_argument("--balance", type=float, required=True, help="账户权益 balance（USD/USDT）")
    ap.add_argument("--available", type=float, required=True, help="可用现金 available（同币种）")
    ap.add_argument("--invest-mult", type=float, default=None,
                    help="缺省 = 市场预设（美股 2 / 加密货币 1）")
    ap.add_argument("--max-delta-pct", type=float, default=None,
                    help="缺省 = 市场预设（美股 10 / 加密货币 20）")
    ap.add_argument("--max-new", type=int, default=PARAM_DEFAULTS["max_new"])
    ap.add_argument("--base-capital", type=float, default=None,
                    help="缺省 = 市场预设（美股 10000 / 加密货币 1000）")
    ap.add_argument("--open-priority", default="amount", choices=("amount", "order"),
                    help="「新仓名额」的占用顺序：amount = 按 |缺口| 降序（缺省，与旧版逐字一致）；"
                         "order = 按快照顺序（= 板块优先级，币版推荐：06 §7.3.1.6 —— 否则落地金额更大的"
                         "非优先候选会挤掉优先板块的名额）")
    a = parsed(ap, argv, "gap")
    mk = MARKET_PRESETS[resolve_market(a.market)]

    if a.balance <= 0:
        fail("balance 必须 > 0")
    data, err = load_json_file(a.snapshot, "快照")
    if err or not isinstance(data, list):
        fail("快照须为 JSON 数组: %s" % err)

    a.invest_mult = a.invest_mult if a.invest_mult is not None else mk["defaults"]["invest_mult"]
    a.max_delta_pct = a.max_delta_pct if a.max_delta_pct is not None else mk["defaults"]["max_delta_pct"]
    a.base_capital = a.base_capital if a.base_capital is not None else mk["defaults"]["base_capital"]

    B = a.balance * a.invest_mult
    no_move = mk["no_move_pct"]
    threshold = B * no_move / 100.0
    threshold_label = "不动阈值_%spct_B" % (str(int(no_move)) if float(no_move) == int(no_move) else str(no_move))
    cap = B * a.max_delta_pct / 100.0
    rows, bad = [], []
    for i, s in enumerate(data):
        if not isinstance(s, dict):
            bad.append("第%d条不是对象" % (i + 1))
            continue
        name = s.get("策略名称") or s.get("ticker") or ("第%d条" % (i + 1))
        cur, tgt = num(s.get("当前市值")), num(s.get("目标资金"))
        if cur is None or tgt is None:
            bad.append("%s: 当前市值/目标资金 缺失或非数值（期望字段: 策略名称/当前市值/目标资金, "
                       "板块/板块分层 可选且 板块分层 ∈ 强势/中性/弱势）" % name)
            continue
        gap = tgt - cur
        if abs(gap) < threshold:
            act = "维持"
        elif gap > 0:
            act = "开仓" if cur == 0 else "加仓"
        else:
            act = "清仓" if tgt <= 0 else "减仓"
        tier = s.get("板块分层")
        tier = tier if tier in ("", "强势", "中性", "弱势") else ""
        if tier == "" and s.get("板块分层") not in (None, ""):
            bad.append("%s: 板块分层=%r 非法（须 ∈ 强势/中性/弱势，或留空），已按缺失处理"
                       % (name, s.get("板块分层")))
        rows.append({"策略名称": name, "板块": s.get("板块", ""), "板块分层": tier,
                     "当前市值": cur, "目标资金": tgt, "缺口": gap,
                     "缺口占B": abs(gap) / B * 100.0, "动作": act, "被截断": False, "截断原因": ""})
    if not rows:
        fail("快照无可计算条目", 明细=bad)

    # 幅度上限：按 |缺口| 降序处理
    active = [r for r in rows if r["动作"] != "维持"]
    active.sort(key=lambda r: -abs(r["缺口"]))
    acc = 0.0
    for r in active:
        if acc + abs(r["缺口"]) <= cap + 1e-6:
            acc += abs(r["缺口"])
        else:
            r["被截断"] = True
            r["截断原因"] = "幅度上限（达到 max_delta_pct=%s%% 后转 维持+触发条件）" % a.max_delta_pct
            r["动作"] = "维持"
    # 新仓名额：占用 max_new（--open-priority amount|order，2026-09-15 验收复盘新增）
    #   amount（缺省）：按 |缺口| 降序（与旧版逐字一致）
    #   order：按快照顺序 —— 快照由 AI 按板块优先级排列，故这就是「板块优先级占名额」；
    #          否则落地金额更大的非优先候选会挤掉优先板块（06 §7.3.1.6）
    opens = [r for r in active if not r["被截断"] and r["动作"] == "开仓"]
    if a.open_priority == "order":
        _idx = {r["策略名称"]: i for i, r in enumerate(rows)}
        opens.sort(key=lambda r: _idx.get(r["策略名称"], 1 << 30))
    for r in opens[a.max_new:]:
        r["被截断"] = True
        r["截断原因"] = "新仓名额（超出 max_new=%d）" % a.max_new
        r["动作"] = "维持"

    # 一致性提示（缺口方向 vs 板块分层 / 最小开仓门槛）
    anomalies = []
    for r in rows:
        if r["被截断"] or r["动作"] == "维持":
            continue
        if r["动作"] in ("加仓", "开仓") and r["板块分层"] == "弱势":
            anomalies.append("%s: 弱势板块出现正缺口（%s）——请核对目标分配" % (r["策略名称"], r["动作"]))
        if r["动作"] in ("减仓", "清仓") and r["板块分层"] == "强势":
            anomalies.append("%s: 强势板块出现负缺口（%s）——请核对目标分配" % (r["策略名称"], r["动作"]))
        if r["动作"] in ("加仓", "开仓") and r["目标资金"] < a.base_capital - 1e-9:
            anomalies.append("%s: 目标资金 %s < 基准资金 %s（最小开仓门槛，应并入同板块其他票）"
                             % (r["策略名称"], g(r["目标资金"]), g(a.base_capital)))

    # 资金匹配（06 §7.3.1.5 第 3/4 条落地：按截断后的最终「动作」计，维持/截断票不计入）
    sell_free = sum((r["当前市值"] - r["目标资金"]) for r in rows if r["动作"] in ("减仓", "清仓"))
    buy_use = sum((r["目标资金"] - r["当前市值"]) for r in rows if r["动作"] in ("加仓", "开仓"))
    net_flow = sell_free - buy_use
    remain_cash = a.available + net_flow
    free_total = sell_free + a.available * a.invest_mult
    loop3_ok = buy_use <= free_total + 1e-9
    if a.invest_mult == 1:
        loop4_status = "通过" if remain_cash >= B * 0.05 - 1e-9 else "不通过"
        loop4_calc = "剩余现金 %s ≥ B×5%%=%s" % (g(remain_cash), g(B * 0.05))
    else:
        loop4_status = "不适用（保证金模式，现金可为负）"
        loop4_calc = "仅 投入倍率=1 适用（当前倍率=%s，剩余现金=%s）" % (a.invest_mult, g(remain_cash))

    n_open_used = sum(1 for r in rows if r["动作"] == "开仓")
    total_abs = sum(abs(r["缺口"]) for r in rows)
    result = {
        "成功": True,
        "工具": "rebalance_tools gap (v" + VERSION + ")",
        "版本": VERSION,
        "市场": resolve_market(a.market),
        "文档依据": "06_策略资金分配.md §7.3.1.3",
        "总预算B": g(B),
        "参数": {"invest_mult": a.invest_mult, "max_delta_pct": a.max_delta_pct,
                 "max_new": a.max_new, "base_capital": a.base_capital,
                 "新仓名额顺序": a.open_priority},
        threshold_label: g(threshold),
        "本轮幅度": g(total_abs / B * 100.0),
        "幅度上限": a.max_delta_pct,
        "是否超幅度上限": total_abs / B * 100.0 > a.max_delta_pct + 1e-9,
        "新仓使用": "%d/%d" % (n_open_used, a.max_new),
        "明细": rows,
        "不动名单": [r["策略名称"] for r in rows if r["动作"] == "维持" and not r["被截断"]],
        "截断名单": [{"策略名称": r["策略名称"], "原因": r["截断原因"]} for r in rows if r["被截断"]],
        "异常": anomalies,
        "快照告警": bad,
        "资金匹配": {
            "卖出释放": g(sell_free),
            "买入使用": g(buy_use),
            "净额": g(net_flow),
            "剩余现金": g(remain_cash),
            "口径": "按截断后最终「动作」计（维持/截断票不计入）；对应报告「资金汇总」同名字段",
        },
        "闭环3_资金来源": {
            "状态": "通过" if loop3_ok else "不通过",
            "计算": "买入使用 %s ≤ 卖出释放 %s + available %s × 倍率 %s = %s"
                    % (g(buy_use), g(sell_free), g(a.available), a.invest_mult, g(free_total)),
        },
        "闭环4_现金下限": {"状态": loop4_status, "计算": loop4_calc},
    }
    for r in rows:
        for k in ("当前市值", "目标资金", "缺口", "缺口占B"):
            r[k] = g(r[k])
    emit(result)


# =====================================================================
# verify — 调仓报告 JSON 校验门（10 §11.2 + 07 §8 + 06 §7.3.1.5）
# =====================================================================

def run_verify(argv):
    ap = argparse.ArgumentParser(
        prog="rebalance_tools verify",
        description="调仓报告 JSON 校验门：从报告数值独立复算（不信任报告自带「风控校验」结论）。"
                    "「校验」= 通过/不通过/无法校验，退出码 0/1/2；判定以输出 JSON「校验」字段为准",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--report", required=True, help="调仓报告 JSON（10_AI调仓输出模板.md §11.1 结构）")
    ap.add_argument("--balance", type=float, default=None,
                    help="独立交叉核对：最新 get_accounts.balance（账户币种）；省略 → 按报告自报值校验")
    ap.add_argument("--available", type=float, default=None,
                    help="独立交叉核对：最新 get_accounts.available（同币种）")
    ap.add_argument("--pool", default=None,
                    help="选股池（T3/高弹性约束用）；缺省 = 市场默认池")
    add_market_arg(ap)
    a = parsed(ap, argv, "verify")
    market = resolve_market(a.market)
    mk = MARKET_PRESETS[market]
    ccy = mk["currency"]
    a.pool = a.pool or default_pool_path(market)

    report, err = load_json_file(a.report, "报告")
    if err or not isinstance(report, dict):
        emit({"成功": True, "工具": "rebalance_tools verify (v" + VERSION + ")", "版本": VERSION,
              "校验": "无法校验", "原因": "报告不可读或不是 JSON 对象: %s" % err, "明细": []}, code=2)

    items = []

    def add(name, status, note=""):
        items.append({"项目": name, "状态": status, "说明": note})

    # ---------- 任务参数 / B ----------
    tp = report.get("任务参数") or {}
    if not isinstance(tp, dict):
        tp = {}

    def tp_val(key):
        for k in (key, PARAM_CN.get(key, "")):
            if k and k in tp and num(tp[k]) is not None:
                return num(tp[k])
        return None

    fs = report.get("资金现状") or {}
    if not isinstance(fs, dict):
        fs = {}
    im_lo, im_hi = mk["ranges"]["invest_mult"]
    invest_mult = tp_val("invest_mult")
    if invest_mult is None:
        invest_mult = num(fs.get("invest_mult"))
    if invest_mult is None or not (im_lo <= invest_mult <= im_hi):
        add("任务参数.invest_mult", "无法校验",
            "缺失或超出 [%s,%s]（当前: %s）；无法计算总预算 B" % (im_lo, im_hi, tp.get("invest_mult")))
    ranges = dict(PARAM_RANGES)
    ranges.update(mk["ranges"])
    for k in ("max_delta_pct", "max_new", "max_single_pct", "max_sector_pct", "base_capital"):
        v = tp_val(k)
        if v is not None:
            lo, hi = ranges[k]
            if not (lo <= v <= hi):
                add("任务参数.%s 范围" % k, "不通过", "%s 超出 [%s, %s]" % (v, lo, hi))
    base_capital = tp_val("base_capital") or float(mk["defaults"]["base_capital"])
    max_single = tp_val("max_single_pct") or float(PARAM_DEFAULTS["max_single_pct"])
    max_sector = tp_val("max_sector_pct") or float(PARAM_DEFAULTS["max_sector_pct"])

    fs_balance = num(fs.get("权益_balance(%s)" % ccy))
    fs_avail = num(fs.get("可用现金(%s)" % ccy))
    B = invest_mult * fs_balance if (invest_mult and fs_balance is not None) else None
    if B is not None:
        fs_B = num(fs.get("总预算_B(权益×invest_mult,%s)" % ccy))
        if fs_B is None:
            add("资金现状.总预算", "不通过", "缺 总预算_B(权益×invest_mult,%s) 字段" % ccy)
        elif abs(fs_B - B) > EPS_MONEY:
            add("资金现状.总预算", "不通过",
                "自报 %s ≠ 权益%s×倍率%s=%s" % (g(fs_B), g(fs_balance), invest_mult, g(B)))
        else:
            add("资金现状.总预算", "通过", "B = %s × %s = %s" % (g(fs_balance), invest_mult, g(fs_B)))
        if a.balance is not None:
            if fs_balance is None or abs(a.balance - fs_balance) > 0.01:
                add("账户快照交叉核对(权益)", "不通过",
                    "独立 balance=%s vs 报告权益=%s（快照过旧或报告误报）" % (g(a.balance), g(fs_balance)))
            else:
                add("账户快照交叉核对(权益)", "通过", "独立 balance=%s 与报告一致" % g(a.balance))
        if a.available is not None:
            if fs_avail is None or abs(a.available - fs_avail) > 0.01:
                add("账户快照交叉核对(可用)", "不通过",
                    "独立 available=%s vs 报告可用现金=%s" % (g(a.available), g(fs_avail)))
            else:
                add("账户快照交叉核对(可用)", "通过", "独立 available=%s 与报告一致" % g(a.available))

    # ---------- 操作模式 / 模板完整性 ----------
    mode = report.get("操作模式")
    add("操作模式纯枚举", "通过" if mode in ("积极", "正常", "防御") else "不通过",
        "实际值: %s（判定理由只能写 风控校验.大盘状态确认）" % mode)
    missing = [k for k in TOP_LEVEL_KEYS if k not in report]
    add("模板完整性(17 顶层字段)", "通过" if not missing else "不通过",
        "齐全" if not missing else "缺失: %s" % ", ".join(missing))
    if "report_name" in report and report.get("report_name") != mk["report_name"]:
        add("报告名固定", "不通过", "report_name=%s，必须为 %s" % (report.get("report_name"), mk["report_name"]))

    # ---------- 数据溯源与时效（仅加密货币：00 §4.6-7，第 18 字段） ----------
    # 复盘事故（2026-09-15）：K线滞后 2 日、快照/报告/行情三种 as_of 混用却无任何声明，
    # 导致"当期口吻"引用滞后数据。币版强制声明数据时点；美股组无此项，行为不变。
    if mk["currency"] == "USDT":
        prov_keys = ("行情末根日期", "账户快照时间", "大盘报告时间")
        prov = report.get("数据溯源")
        if not isinstance(prov, dict):
            add("数据溯源块（币版第 18 字段：%s）" % "/".join(prov_keys), "不通过",
                "缺顶层 `数据溯源`；币版报告必须声明数据时点（00 §4.6-7），否则无法判定时效")
        else:
            miss_prov = [k for k in prov_keys if not prov.get(k)]
            add("数据溯源块（币版第 18 字段）", "通过" if not miss_prov else "不通过",
                "齐全" if not miss_prov else "缺: %s" % ", ".join(miss_prov))
        if not (report.get("调仓指令") or []) and not (report.get("新增策略") or []):
            adv = str(report.get("执行建议") or "")
            ok_adv = ("无调仓" in adv or "不调仓" in adv) and len(adv) >= 80
            add("空报告（0 指令）执行建议依据", "通过" if ok_adv else "不通过",
                "已写明无调仓及依据（%d 字）" % len(adv) if ok_adv else
                "0 指令但 执行建议 未写明「无调仓/不调仓」或依据过短（00 §4.1-1）")

    # ---------- 指令 / 新增策略 逐条 ----------
    instrs = report.get("调仓指令") or []
    news = report.get("新增策略") or []
    if not isinstance(instrs, list) or not isinstance(news, list):
        add("调仓指令/新增策略类型", "不通过", "必须为数组")
        instrs = instrs if isinstance(instrs, list) else []
        news = news if isinstance(news, list) else []

    single_bad, buffer_bad, floor_bad, batch_bad, purity_bad, field_bad = [], [], [], [], [], []
    sector_sum, subsec_sum = {}, {}
    target_pos_count = 0
    for src, lst in (("指令", instrs), ("新增", news)):
        for it in lst:
            if not isinstance(it, dict):
                field_bad.append("%s: 不是对象" % src)
                continue
            nm = it.get("策略名称") or it.get("ticker") or "(无名)"
            if src == "指令":
                act = it.get("动作")
                if act not in ACTIONS:
                    field_bad.append("%s: 动作=%s 不在 %s" % (nm, act, "/".join(ACTIONS)))
                if it.get("层级") not in LAYERS:
                    field_bad.append("%s: 层级=%s 不在 S1–S5" % (nm, it.get("层级")))
                if num(it.get("目标持仓")) is None or num(it.get("当前持仓")) is None:
                    field_bad.append("%s: 目标持仓/当前持仓 缺失" % nm)
            else:
                act = "开仓"
            tpct = num(it.get("目标仓位占比_pct"))
            tpct = 0.0 if tpct is None else tpct
            if tpct < 0:
                field_bad.append("%s: 目标仓位占比_pct 为负" % nm)
            target_money = tpct / 100.0 * B if B else None
            # 目标资金的两个口径（06 §7.3.1.2「取整损失条款」）：
            #   决策口径 = 指令可选字段「目标资金」（未取整，门槛/缓冲的判定基准）
            #   落地口径 = 目标仓位占比_pct × B（= 目标持仓 × 现价，整股取整后）
            # 门槛与缓冲一律按决策口径判；缺该字段时退回落地口径并在报错中提示补写。
            # 兼容 10 §11.1 早期模板写法「目标资金（决策口径，可选）」——
            # 若只认「目标资金」，按模板照抄的报告会退回落地的 tpct×B 作门槛/缓冲基准，
            # 复算出假「不通过」（2026-09-15 验收复盘：93.29 < 99 触发最小开仓门槛）。
            decl_money = None
            if src == "指令":
                for _k in ("目标资金", "目标资金（决策口径）", "目标资金（决策口径，可选）"):
                    _v = num(it.get(_k))
                    if _v is not None:
                        decl_money = _v
                        break
            gate_money = decl_money if decl_money is not None else target_money
            if tpct > 0 and B:
                sector_sum[it.get("板块")] = sector_sum.get(it.get("板块"), 0.0) + tpct
                subsec_sum[it.get("子板块")] = subsec_sum.get(it.get("子板块"), 0.0) + tpct
            tgt_pos = num(it.get("目标持仓"))
            if tgt_pos is not None and tgt_pos > 0:
                target_pos_count += 1
            init = None
            if src == "指令":
                adj = it.get("资金调整") or {}
                init = num(adj.get("初始资金")) if isinstance(adj, dict) else None
            else:
                init = num(it.get("初始资金"))
            if act in ("加仓", "开仓"):
                if init is None:
                    buffer_bad.append("%s: %s 缺 初始资金（06 §7.3.1.4 所有策略必写）" % (nm, act))
                elif gate_money is not None and init < gate_money * BUFFER - EPS_MONEY:
                    buffer_bad.append("%s: 初始资金 %s < 目标资金 %s×1.2=%s"
                                     % (nm, g(init), g(gate_money), g(gate_money * BUFFER)))
            if gate_money is not None and gate_money > 0 and gate_money < base_capital - EPS_MONEY:
                floor_bad.append("%s: 目标资金 %s < 基准资金 %s（最小开仓门槛）%s"
                                 % (nm, g(gate_money), g(base_capital),
                                    "" if decl_money is not None else
                                    "；当前按「整股取整后金额」判定，若系取整所致，请补写指令字段"
                                    " 目标资金（决策口径，未取整）（06 §7.3.1.2 取整损失条款）"))
            if B and target_money is not None and target_money > max_single / 100.0 * B + EPS_MONEY:
                single_bad.append("%s: 目标 %s%% > 单票上限 %s%%" % (nm, g(tpct), g(max_single)))
            batches = it.get("分批计划") or []
            if batches and isinstance(batches, list):
                tps = [num(b.get("目标持仓")) for b in batches if isinstance(b, dict)]
                if any(t is None or t < -1e-9 for t in tps):
                    batch_bad.append("%s: 分批目标持仓缺失或为负（16+16=-1 类错误）" % nm)
                else:
                    vals = [t for t in tps if t is not None]
                    if act in ("减仓", "清仓") and any(vals[i + 1] > vals[i] + 1e-9 for i in range(len(vals) - 1)):
                        batch_bad.append("%s: 减仓/清仓 分批目标持仓未单调递减: %s" % (nm, vals))
                    elif act in ("加仓", "开仓") and any(vals[i + 1] < vals[i] - 1e-9 for i in range(len(vals) - 1)):
                        batch_bad.append("%s: 加仓/开仓 分批目标持仓未单调递增: %s" % (nm, vals))
                    if tgt_pos is not None and vals and abs(vals[-1] - tgt_pos) > 1e-9:
                        batch_bad.append("%s: 末批目标持仓 %s ≠ 指令目标持仓 %s" % (nm, vals[-1], int(tgt_pos)))
            prm = it.get("调仓相关参数") or {}
            if not isinstance(prm, dict):
                purity_bad.append("%s: 调仓相关参数不是对象" % nm)
            else:
                for pk, pv in prm.items():
                    if pv is None:
                        continue
                    if isinstance(pv, bool) or isinstance(pv, (int, float)):
                        continue
                    if isinstance(pv, str) and not has_cjk(pv) and len(pv) <= 20:
                        continue
                    purity_bad.append("%s: 调仓相关参数[%s]=%s（值只留数值/布尔/枚举，说明进意图备注）" % (nm, pk, pv))
    add("指令字段与枚举", "通过" if not field_bad else "不通过", "OK" if not field_bad else "; ".join(field_bad))
    add("单票上限 ≤ %s%%（分母=B）" % g(max_single), "通过" if not single_bad else "不通过",
        "OK" if not single_bad else "; ".join(single_bad))
    add("每策略缓冲 初始资金 ≥ 目标资金×1.2", "通过" if not buffer_bad else "不通过",
        "OK" if not buffer_bad else "; ".join(buffer_bad))
    add("目标资金 ≥ 基准资金（最小开仓门槛）", "通过" if not floor_bad else "不通过",
        "OK" if not floor_bad else "; ".join(floor_bad))
    add("分批计划自洽", "通过" if not batch_bad else "不通过",
        "OK" if not batch_bad else "; ".join(batch_bad))
    add("调仓相关参数纯净（仅数值/布尔/枚举）", "通过" if not purity_bad else "不通过",
        "OK" if not purity_bad else "; ".join(purity_bad))

    # ---------- 板块/子板块汇总（分母 = B） ----------
    if B:
        sec_bad = {s: v for s, v in sector_sum.items() if v > max_sector + 1e-9}
        add("单板块上限 ≤ %s%%（分母=B）" % g(max_sector),
            "通过" if not sec_bad else "不通过",
            "OK" if not sec_bad else "; ".join("%s=%s" % (s, g(v)) for s, v in sec_bad.items()))
        if mk["subsector_cap_pct"] is not None:
            sub_bad = {s: v for s, v in subsec_sum.items() if v > HARD_CAP_SUBSECTOR_PCT + 1e-9}
            add("单子板块上限 ≤ 12%（分母=B）", "通过" if not sub_bad else "不通过",
                "OK" if not sub_bad else "; ".join("%s=%s" % (s, g(v)) for s, v in sub_bad.items()))
        else:
            # 加密货币：无单子板块 12%，改走高弹性合计（加密货币/07 §8.1）
            hb_sum = sum(v for s, v in subsec_sum.items() if bucket_of(s) in mk["hb_buckets"])
            add("高弹性合计（31-60+60+）≤ %s%%（分母=B）" % g(mk["hb_total_pct"]),
                "通过" if hb_sum <= mk["hb_total_pct"] + 1e-9 else "不通过",
                "高弹性分桶合计=%s" % g(hb_sum))
        top3 = sorted(sector_sum.items(), key=lambda kv: -kv[1])[:3]
        top3_sum = sum(v for _, v in top3)
        add("前三大板块合计 ≤ 50%（调仓后板块%降序取最大三个）",
            "通过" if top3_sum <= HARD_CAP_TOP3_PCT + 1e-9 else "不通过",
            "前三: " + "; ".join("%s=%s" % (s, g(v)) for s, v in top3)
            + "，合计=%s" % g(top3_sum))
        n_total = target_pos_count + len(news)
        add("最大持仓数量 ≤ 50", "通过" if n_total <= HARD_CAP_STRATEGIES else "不通过",
            "目标持仓>0 的指令 %d 只 + 新增 %d 只 = %d" % (target_pos_count, len(news), n_total))
        # T3 / 高弹性 约束（选股池）
        pool_idx, pool_err = load_pool_index(a.pool, market)
        if pool_err or pool_idx is None:
            add("T3 小盘约束（总 ≤5% / 单只 ≤1%）" if mk["pool_kind"] == "us"
                else "高弹性单币约束（60+ ≤2% / 31-60 ≤3%）", "不适用",
                "选股池不可读: %s" % (pool_err or "空索引"))
        elif mk["pool_kind"] == "us":
            t3_single, t3_total = [], 0.0
            for it in list(instrs) + list(news):
                if not isinstance(it, dict):
                    continue
                tk = it.get("ticker")
                if tk in pool_idx and pool_idx[tk]["T层"] == "T3":
                    v = num(it.get("目标仓位占比_pct")) or 0.0
                    t3_total += v
                    if v > HARD_CAP_T3_SINGLE_PCT + 1e-9:
                        t3_single.append("%s=%s" % (tk, g(v)))
            ok_t3 = t3_total <= HARD_CAP_T3_TOTAL_PCT + 1e-9 and not t3_single
            add("T3 小盘约束（总 ≤5% / 单只 ≤1%）", "通过" if ok_t3 else "不通过",
                ("T3 合计=%s" % g(t3_total)) + ("; 超限单只: " + "; ".join(t3_single) if t3_single else ""))
        else:
            # 加密货币：高弹性单币约束（60+ ≤2% / 31-60 ≤3%，加密货币/07 §8.1）
            hb_single_bad, hb_total = [], 0.0
            for it in list(instrs) + list(news):
                if not isinstance(it, dict):
                    continue
                tk = it.get("ticker")
                row = pool_idx.get(tk)
                b = row.get("bucket") if isinstance(row, dict) else None
                cap_v = mk["hb_single_pct"].get(b)
                if cap_v is None:
                    continue
                v = num(it.get("目标仓位占比_pct")) or 0.0
                hb_total += v
                if v > cap_v + 1e-9:
                    hb_single_bad.append("%s=%s(上限%s%%)" % (tk, g(v), g(cap_v)))
            ok_hb = not hb_single_bad
            add("高弹性单币约束（60+ ≤2% / 31-60 ≤3%）", "通过" if ok_hb else "不通过",
                ("高弹性合计=%s" % g(hb_total))
                + ("; 超限单只: " + "; ".join(hb_single_bad) if hb_single_bad else ""))
        # 目标分配一致性
        ta = report.get("目标分配") or {}
        ta_bad = []
        for s, v in sector_sum.items():
            if s not in ta:
                ta_bad.append("板块 %s 有指令但 目标分配 无条目" % s)
                continue
            tv = num((ta.get(s) or {}).get("目标_pct"))
            if tv is None:
                ta_bad.append("板块 %s 目标_pct 缺失" % s)
            elif abs(tv - v) > EPS_PCT:
                ta_bad.append("板块 %s: 目标分配 %s vs 个股指令合计 %s" % (s, g(tv), g(v)))
        add("目标分配一致性（板块目标% = 个股指令%之和）",
            "通过" if not ta_bad else "不通过", "OK" if not ta_bad else "; ".join(ta_bad))
    else:
        subsec_nm = "单子板块上限" if mk["subsector_cap_pct"] is not None else "高弹性合计"
        for nm in ("单板块上限", subsec_nm, "前三大板块合计", "目标分配一致性"):
            add(nm, "无法校验", "B 不可计算")

    # ---------- 资金闭环 6 条（06 §7.3.1.5，从 资金汇总 复算） ----------
    fz = report.get("资金汇总") or {}
    if not isinstance(fz, dict):
        fz = {}
    s_init = num(fz.get("策略初始资金合计"))
    sell_free = num(fz.get("卖出释放"))
    buy_use = num(fz.get("买入使用"))
    remain = num(fz.get("剩余现金"))
    n_target = num(fz.get("目标组合策略数"))
    avail_for = a.available if a.available is not None else fs_avail
    if B is not None:
        add("闭环1 预算（Σ初始资金 ≤ B）",
            "通过" if s_init is not None and s_init <= B + EPS_MONEY else
            ("无法校验" if s_init is None else "不通过"),
            "Σ初始资金=%s vs B=%s" % (g(s_init) if s_init is not None else "缺失", g(B)))
        if mk["loop2_kind"] == "sigma":
            add("闭环2 IB保证金上限（Σ初始资金 ≤ available×5）",
                "通过" if s_init is not None and avail_for is not None and s_init <= avail_for * 5 + EPS_MONEY else
                ("无法校验" if (s_init is None or avail_for is None) else "不通过"),
                "Σ初始资金=%s vs available×5=%s"
                % (g(s_init) if s_init is not None else "缺失",
                   g(avail_for * 5) if avail_for is not None else "缺失"))
        else:
            add("闭环2 账户杠杆硬顶（B ≤ 可用%s×%s）" % (ccy, g(mk["acct_cap"])),
                "通过" if avail_for is not None and B <= avail_for * mk["acct_cap"] + EPS_MONEY else
                ("无法校验" if avail_for is None else "不通过"),
                "B=%s vs available×%s=%s"
                % (g(B), g(mk["acct_cap"]),
                   g(avail_for * mk["acct_cap"]) if avail_for is not None else "缺失"))
        free_total = None
        if sell_free is not None and avail_for is not None and invest_mult is not None:
            free_total = sell_free + avail_for * invest_mult
        ok3 = (buy_use is not None and free_total is not None
               and buy_use <= free_total + EPS_MONEY)
        add("闭环3 资金来源（买入使用 ≤ 卖出释放 + available×倍率）",
            "通过" if ok3 else ("无法校验" if (buy_use is None or free_total is None) else "不通过"),
            "买入=%s ≤ 卖出释放%s + 可用%s×倍率%s=%s"
            % (g(buy_use) if buy_use is not None else "缺失",
               g(sell_free) if sell_free is not None else "缺失",
               g(avail_for) if avail_for is not None else "缺失", invest_mult,
               g(free_total) if free_total is not None else "缺失"))
        if invest_mult == 1:
            add("闭环4 现金下限（剩余现金 ≥ B×5%，仅倍率=1）",
                "通过" if remain is not None and remain >= B * 0.05 - EPS_MONEY else
                ("无法校验" if remain is None else "不通过"),
                "剩余现金=%s vs B×5%%=%s" % (g(remain) if remain is not None else "缺失", g(B * 0.05)))
        else:
            add("闭环4 现金下限（仅 投入倍率=1）", "不适用",
                "保证金模式，现金可为负（当前剩余现金=%s）" % (g(remain) if remain is not None else "缺失"))
        add("闭环5 每策略缓冲（初始资金 ≥ 目标资金×1.2）",
            "通过" if not buffer_bad else "不通过", "OK" if not buffer_bad else "; ".join(buffer_bad))
        N = n_target if n_target is not None else float(target_pos_count + len(news))
        ok6 = not floor_bad and N <= B / base_capital + 1e-9 and N <= HARD_CAP_STRATEGIES
        add("闭环6 基准与数量（目标资金≥基准资金；策略数 ≤ B÷基准资金，硬顶50）",
            "通过" if ok6 else "不通过",
            "N=%s ≤ B÷基准=%s；%s" % (g(N), g(B / base_capital),
                                     "OK" if not floor_bad else "; ".join(floor_bad)))
    else:
        for i in range(1, 7):
            add("闭环%d" % i, "无法校验", "B 不可计算")

    # ---------- 顶层 % 与 $ 互验（10 §11.2 规则 4） ----------
    # 口径（2026-09-15 修订）：调仓后总仓位_pct 的分子 = 调仓后持仓市值；现金_pct = 100 − 总仓位_pct。
    # 不得再用「剩余现金 ÷ B」当作现金_pct —— 剩余现金是资金记账量，与「仓位占比」不是
    # 同一维度的互补量（历史事故：报告显示总仓位 49.16%，而实际持仓仅 3.86%）。
    asum = report.get("分析摘要") or {}
    pos_pct = num(asum.get("调仓后总仓位_pct"))
    cash_pct = num(asum.get("调仓后现金_pct"))
    fz_pos = num(fz.get("调仓后总仓位_pct"))
    if pos_pct is not None and cash_pct is not None:
        add("顶层%互验（总仓位 + 现金 = 100）",
            "通过" if abs(pos_pct + cash_pct - 100.0) <= EPS_PCT else "不通过",
            "%s + %s = %s" % (g(pos_pct), g(cash_pct), g(pos_pct + cash_pct)))
    else:
        add("顶层%互验（总仓位 + 现金 = 100）", "无法校验",
            "分析摘要 缺 调仓后总仓位_pct/调仓后现金_pct")
    if fz_pos is not None and pos_pct is not None:
        add("调仓后总仓位_pct 交叉核对（资金汇总 = 分析摘要）",
            "通过" if abs(fz_pos - pos_pct) <= EPS_PCT else "不通过",
            "资金汇总 %s vs 分析摘要 %s" % (g(fz_pos), g(pos_pct)))
    else:
        add("调仓后总仓位_pct 交叉核对（资金汇总 = 分析摘要）", "无法校验",
            "资金汇总.调仓后总仓位_pct 缺失" if fz_pos is None else "分析摘要.调仓后总仓位_pct 缺失")

    # 组合重建：报告声明的「调仓后总仓位」必须能由报告自身的持仓明细重建。
    # 未映射持仓是持仓的一部分，必须计入；缺 仓位占比_pct 则报「无法校验」而不是「不适用」
    # （历史事故：未映射持仓被排除后该检查静默跳过，是当时唯一的交叉核对项）。
    instr_names = {it.get("策略名称") for it in instrs if isinstance(it, dict)}
    pm = report.get("持仓映射") or {}
    others, has_others = 0.0, False
    unmapped_sum, unmapped_missing = 0.0, []
    if isinstance(pm, dict):
        for key, lst in pm.items():
            if not isinstance(lst, list):
                continue
            for e in lst:
                if not isinstance(e, dict):
                    continue
                if key == "未映射持仓":
                    v = num(e.get("仓位占比_pct"))
                    if v is None:
                        unmapped_missing.append(str(e.get("ticker") or e.get("策略名称") or "?"))
                    else:
                        unmapped_sum += v
                        has_others = True
                    continue
                if e.get("策略名称") not in instr_names:
                    v = num(e.get("仓位占比_pct"))
                    if v is not None:
                        others += v
                        has_others = True
    touched = sum((num(it.get("目标仓位占比_pct")) or 0.0) for it in instrs if isinstance(it, dict))
    item_name = "组合重建（Σ指令目标% + Σ未涉及持仓% + Σ未映射持仓% = 调仓后总仓位%）"
    calc_pos = touched + others + unmapped_sum
    if pos_pct is None:
        add(item_name, "无法校验", "报告缺 分析摘要.调仓后总仓位_pct")
    elif unmapped_missing:
        add(item_name, "无法校验",
            "未映射持仓缺 仓位占比_pct（未映射持仓必须计入组合重建，否则总仓位口径不完整）: %s"
            % "、".join(unmapped_missing))
    elif not has_others and not instrs:
        add(item_name, "无法校验", "报告既无调仓指令也无未涉及持仓，无从重建")
    else:
        add(item_name,
            "通过" if abs(calc_pos - pos_pct) <= PORTFOLIO_TOL else "不通过",
            "指令 %s + 未涉及 %s + 未映射 %s = %s vs 报告 %s"
            % (g(touched), g(others), g(unmapped_sum), g(calc_pos), g(pos_pct)))

    # ---------- 层级 ↔ 动作 一致性（04 §6.2 矩阵 / §6.3 二次筛选） ----------
    # 报告内部不得出现「层级=S1 强烈加仓 但 动作=清仓」这类互斥组合。
    # 若判断确需减仓，正确做法是先把 持仓映射 的层级下调（并写明依据），
    # 使「层级 → 动作」链条一致，而不是把矛盾留到报告里让读者裁决。
    layer_of = {}
    if isinstance(pm, dict):
        for key, lst in pm.items():
            if key == "未映射持仓" or not isinstance(lst, list):
                continue
            for e in lst:
                if isinstance(e, dict) and e.get("策略名称"):
                    layer_of[str(e["策略名称"])] = key
    layer_bad = []
    for it in instrs:
        if not isinstance(it, dict):
            continue
        nm = str(it.get("策略名称") or it.get("ticker") or "(无名)")
        act = it.get("动作")
        lay = layer_of.get(str(it.get("策略名称"))) or it.get("层级")
        if not lay or not act:
            continue
        if lay in ("S1_强烈加仓", "S2_适度加仓") and act in ("减仓", "清仓"):
            layer_bad.append("%s: 层级=%s 但 动作=%s（04 §6.2/§6.3：S1/S2 只加仓或保留，"
                             "确需减仓须先下调 持仓映射 层级并写明依据）" % (nm, lay, act))
        elif lay == "S5_优先减仓" and act in ("加仓", "开仓"):
            layer_bad.append("%s: 层级=%s 但 动作=%s（S5 禁止加仓/开仓）" % (nm, lay, act))
    add("层级 ↔ 动作 一致性（S1/S2 不得减仓清仓；S5 不得加仓/开仓）",
        "通过" if not layer_bad else "不通过",
        "OK" if not layer_bad else "; ".join(layer_bad))

    # ---------- 报告自带风控结论 ----------
    rk = report.get("风控校验") or {}
    rk_bad = []
    if isinstance(rk, dict):
        for k, v in rk.items():
            if k == "大盘状态确认":
                continue
            sv = v.strip() if isinstance(v, str) else ""
            if sv and not sv.startswith("通过") and not sv.startswith("不适用"):
                rk_bad.append("%s=%s" % (k, sv))
    add("报告 风控校验 自报结论（复算为唯一准）",
        "通过" if not rk_bad else "不通过",
        "OK（全部自报 通过）" if not rk_bad else "; ".join(rk_bad))

    # ---------- 结论 ----------
    statuses = [i["状态"] for i in items]
    n_bad = statuses.count("不通过")
    n_na = statuses.count("无法校验")
    n_skip = statuses.count("不适用")
    if n_bad:
        concl, code = "不通过", 1
    elif n_na:
        concl, code = "无法校验", 2
    else:
        concl, code = "通过", 0
    skipped = [i["项目"] for i in items if i["状态"] == "不适用"]
    emit({"成功": True, "工具": "rebalance_tools verify (v" + VERSION + ")", "版本": VERSION,
          "市场": market,
          "校验": concl,
          "总预算B": g(B) if B else None,
          "不通过项数": n_bad,
          "无法校验项数": n_na,
          "不适用项数": n_skip,
          "未执行检查项": skipped,
          "说明": "「校验=通过」= 0 不通过 且 0 无法校验；「不适用项数」>0 时必须逐条人工确认"
                  "确属条件不满足（如 仅倍率=1 时的现金下限），不得用来掩盖未执行的交叉核对",
          "明细": items}, code=code)


# =====================================================================
# score — 板块多因子评分（02 §4）
# =====================================================================

def run_score(argv):
    ap = argparse.ArgumentParser(
        prog="rebalance_tools score",
        description="板块多因子评分（02 §4）：动量因子（RS vs 市场基准 多窗口；美股 6M/3M/12M/20D 权重 40/30/20/10，"
                    "加密货币 20D/7D/5D 权重 50/30/20）+ 轮动/经济周期因子（查表）；"
                    "排序 + 强势/中性/弱势 分层 + Bottom3 连弱风控（基准/窗口/周期表由 --market 决定）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    add_market_arg(ap)
    ap.add_argument("--prices", required=True,
                    help="收盘价序列 JSON：基准键（美股 SPY / 加密货币 BTC）必填，其余键 = 板块（美股 ETF / 加密货币板块名）")
    ap.add_argument("--cycle", default=None, choices=list(CYCLES) + list(CRYPTO_CYCLES),
                    help="周期阶段（美股: 01 §3.3 经济周期 / 加密货币: 01 §3.3 轮动阶段）；省略 → 只算动量（权重归一化）")
    ap.add_argument("--top-strong", type=int, default=4, help="强势板块数量（02 §4.3: Top 3-4）")
    ap.add_argument("--bottom3-history", default=None,
                    help="历史 Bottom3 JSON 数组（旧→新）：[\"XLE\",\"XLI\"], ... 用于连弱判定")
    a = parsed(ap, argv, "score")
    market = resolve_market(a.market)
    mk = MARKET_PRESETS[market]
    if a.cycle and a.cycle not in mk["cycles"]:
        fail("周期阶段 %s 与 市场=%s 不匹配（允许: %s）" % (a.cycle, market, "/".join(mk["cycles"])))
    benchmark = mk["benchmark"]
    windows, weights = mk["windows"], mk["mom_weights"]

    data, err = load_json_file(a.prices, "价格序列")
    if err or not isinstance(data, dict):
        fail("价格序列须为 JSON 对象 {ticker: {date: close}}: %s" % err)
    spy = data.get(benchmark)
    if not isinstance(spy, dict) or len(spy) < 21:
        fail("%s 基准缺失或可用交易日 < 21（建议 ≥ 253 天覆盖最大窗口）" % benchmark)
    etfs = {t: v for t, v in data.items() if t != benchmark and isinstance(v, dict) and v}
    if not etfs:
        fail("除 %s 外无板块序列" % benchmark)
    spy_map = {}
    for d, v in spy.items():
        fv = num(v)
        if fv and fv > 0:
            spy_map[str(d)] = fv

    rows, missing = [], []
    for t, series in etfs.items():
        pairs = []
        for d in sorted(str(x) for x in series.keys()):
            e = num(series[d])
            s = spy_map.get(d)
            if e and s:
                pairs.append((e, s))
        if len(pairs) < 21:
            missing.append("%s: 与 %s 重合可用交易日 %d < 21，无法评分" % (t, benchmark, len(pairs)))
            rows.append({"ETF": t, "板块": SECTOR_NAMES.get(t, t), "评分": None, "排名": None,
                         "分层": "无法评分", "动量分": None, "周期分": None,
                         "因子明细": {}, "可用交易日": len(pairs)})
            continue
        ve, vs = [p[0] for p in pairs], [p[1] for p in pairs]
        roc = {}
        for name, n in windows:
            if len(pairs) > n:
                roc[name] = (ve[-1] / vs[-1]) / (ve[-1 - n] / vs[-1 - n]) * 100.0 - 100.0
        if not roc:
            missing.append("%s: 全部窗口数据不足" % t)
            rows.append({"ETF": t, "板块": SECTOR_NAMES.get(t, t), "评分": None, "排名": None,
                         "分层": "无法评分", "动量分": None, "周期分": None,
                         "因子明细": {}, "可用交易日": len(pairs)})
            continue
        wsum = sum(weights[w] for w in roc)
        mom_raw = sum(weights[w] * roc[w] for w in roc) / wsum
        cyc_table = mk["cycle_scores"]
        cyc_raw = (cyc_table or {}).get(a.cycle, {}).get(t) if a.cycle else None
        rows.append({"ETF": t, "板块": SECTOR_NAMES.get(t, t), "评分": None, "排名": None,
                     "分层": "", "动量分": None, "周期分": None,
                     "因子明细": {k: g(v) for k, v in roc.items()},
                     "可用交易日": len(pairs), "_mom": mom_raw, "_cyc": cyc_raw})

    scored = [r for r in rows if r.get("_mom") is not None]
    if not scored:
        fail("所有板块均无法评分（数据不足）", 数据缺失=missing)
    lo, hi = min(r["_mom"] for r in scored), max(r["_mom"] for r in scored)
    span = hi - lo
    for r in scored:
        r["动量分"] = g(50.0 if span == 0 else (r["_mom"] - lo) / span * 100.0)
        if a.cycle and r["_cyc"] is not None:
            r["周期分"] = g((r["_cyc"] + 2) / 4.0 * 100.0)
        elif a.cycle:
            missing.append("%s: 不在 02 §4.2.2 周期因子表，周期分缺失" % r["ETF"])
        w = FACTOR_WEIGHTS["动量"] + (FACTOR_WEIGHTS["周期"] if r["周期分"] is not None else 0.0)
        val = FACTOR_WEIGHTS["动量"] * r["动量分"]
        if r["周期分"] is not None:
            val += FACTOR_WEIGHTS["周期"] * r["周期分"]
        r["评分"] = g(val / w)
    for r in scored:
        r.pop("_mom", None)
        r.pop("_cyc", None)

    scored.sort(key=lambda r: (-r["评分"], r["ETF"]))
    n_sc = len(scored)
    top_n = min(a.top_strong, n_sc - 3) if n_sc > 3 else 0
    for i, r in enumerate(scored):
        r["排名"] = i + 1
        r["分层"] = "强势" if i < top_n else ("弱势" if i >= n_sc - 3 else "中性")
    strong = [r["ETF"] for r in scored if r["分层"] == "强势"]
    neutral = [r["ETF"] for r in scored if r["分层"] == "中性"]
    weak = [r["ETF"] for r in scored if r["分层"] == "弱势"]

    # 连弱风控（02 §4.3 / 07 §8.2）
    hist = []
    if a.bottom3_history:
        h, herr = load_json_file(a.bottom3_history, "Bottom3 历史")
        if herr or not isinstance(h, list):
            missing.append("Bottom3 历史不可用: %s" % herr)
        else:
            hist = [x for x in h if isinstance(x, list)]
    weak_risk = []
    for r in scored:
        if r["ETF"] not in weak:
            r["连弱周数"] = 0
            continue
        streak = 1
        for week in reversed(hist):
            if r["ETF"] in week:
                streak += 1
            else:
                break
        r["连弱周数"] = streak
        if streak >= 3:
            note = "连续 %d 周 Bottom 3 → 强制清仓（02 §4.3 / 07 §8.2）" % streak
        elif streak == 2:
            note = "连续 2 周 Bottom 3 → 板块内持仓减仓 50%（07 §8.2）"
        else:
            note = "本周进入 Bottom 3（首周观察）"
        weak_risk.append({"ETF": r["ETF"], "周数": streak, "提示": note})

    result = {
        "成功": True,
        "工具": "rebalance_tools score (v" + VERSION + ")",
        "版本": VERSION,
        "市场": market,
        "文档依据": "02_板块评分与排序.md §4",
        "基准": benchmark,
        "周期阶段": a.cycle,
        "窗口": {n: k for n, k in windows},
        "因子覆盖": "动量(30%)" + (" + 周期(25%)" if a.cycle else "（未提供周期，权重归一化）"),
        "说明": ("价值/质量/情绪因子无数据源，本得分 = 已提供因子按原权重归一化；"
                 if market == "美股" else
                 "资金确认/流动性/叙事催化剂 因子无内置数据源，本得分 = 动量+周期 按原权重归一化（加密货币/02 §4.2）；")
                + "排序即 强势/中性/弱势 分层依据",
        "明细": [{k: r[k] for k in ("ETF", "板块", "排名", "分层", "评分", "动量分", "周期分",
                                   "因子明细", "可用交易日", "连弱周数") if k in r} for r in rows],
        "强势板块": strong,
        "中性板块": neutral,
        "弱势板块": weak,
        "连弱风控": weak_risk,
        "数据缺失": missing,
    }
    emit(result)


# =====================================================================
# score2 — 双层子板块评分（03 §5.3/§5.4）
# =====================================================================


def run_score2(argv):
    ap = argparse.ArgumentParser(
        prog="rebalance_tools score2",
        description="双层子板块评分（03 §5.3/§5.4）：最终子板块得分 = 0.5×所属板块得分 + 0.5×子板块独立得分；"
                    "独立得分 = 动量（RS vs 市场基准，同 score）+ 轮动/经济周期因子（继承所属板块，02 §4.2.2）；"
                    "03 §5.3.2 双层决策矩阵 + 03 §5.4 分层（默认 Top7/Bottom6）+ 超板块 1 标准差钳制"
                    "（基准/窗口/周期表由 --market 决定）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("prices JSON: {\"基准\": {日期: 收盘}, \"<子板块名>\": {日期: 收盘}, ...} —— "
                "基准键 = 美股 SPY / 加密货币 BTC；"
                "子板块（加密货币 = 板块-市值分桶 组合，如 L1 公链-11-30）等权合成价格序列；"
                "子板块名 → 板块 映射以 市场选股池 为准"))
    add_market_arg(ap)
    ap.add_argument("--prices", required=True, help="子板块等权合成价格序列 JSON（基准键必填）")
    ap.add_argument("--pool", default=None, help="选股池（子板块名→板块）；缺省 = 市场默认池")
    ap.add_argument("--sector-score", default=None,
                    help="score 子命令输出 JSON（取各板块 评分/分层；省略 → 最终得分=独立得分）")
    ap.add_argument("--cycle", default=None, choices=list(CYCLES) + list(CRYPTO_CYCLES),
                    help="周期阶段（周期因子，继承板块）")
    ap.add_argument("--top-strong", type=int, default=7, help="强势子板块数（03 §5.4 Top 6-8）")
    ap.add_argument("--bottom-weak", type=int, default=6, help="弱势子板块数（03 §5.4 Bottom 5-7）")
    a = parsed(ap, argv, "score2")
    market = resolve_market(a.market)
    mk = MARKET_PRESETS[market]
    if a.cycle and a.cycle not in mk["cycles"]:
        fail("周期阶段 %s 与 市场=%s 不匹配（允许: %s）" % (a.cycle, market, "/".join(mk["cycles"])))
    benchmark = mk["benchmark"]
    windows, weights = mk["windows"], mk["mom_weights"]
    a.pool = a.pool or default_pool_path(market)

    data, err = load_json_file(a.prices, "价格序列")
    if err or not isinstance(data, dict):
        fail("价格序列须为 JSON 对象 {子板块名: {日期: 收盘}}: %s" % err)
    spy = data.get(benchmark)
    if not isinstance(spy, dict) or len(spy) < 21:
        fail("%s 基准缺失或可用交易日 < 21" % benchmark)
    subs = {t: v for t, v in data.items() if t != benchmark and isinstance(v, dict) and v}
    if not subs:
        fail("除 %s 外无子板块序列" % benchmark)
    spy_map = {}
    for d, v in spy.items():
        fv = num(v)
        if fv and fv > 0:
            spy_map[str(d)] = fv

    # 子板块名 → 板块（市场选股池）；美股 = sub_sectors.name，加密货币 = "板块-分桶" 组合名
    pool, perr = load_json_file(a.pool, "选股池")
    name2sector, crypto_cats = {}, set()
    if not perr and isinstance(pool, dict):
        if mk["pool_kind"] == "crypto":
            crypto_cats = {str(k) for k in (pool.get("categories") or {}).keys()}
            for t in subs:
                b = bucket_of(t)
                if b is None:
                    continue
                name2sector[str(t)] = (str(t)[:-(len(b) + 1)] if t != b else None) or None
        else:
            for code, v in (pool.get("sub_sectors") or {}).items():
                if isinstance(v, dict) and v.get("name") and v.get("sector"):
                    name2sector[str(v["name"])] = canon_sector(v["sector"])

    # 板块得分 / 分层（score 输出）；键名归一到标准中文名（兼容历史简称写法）
    sec_score, sec_tier, sec_src = {}, {}, ""
    if a.sector_score:
        sd, serr = load_json_file(a.sector_score, "板块评分")
        if serr or not isinstance(sd, dict):
            fail("板块评分文件不可读: %s" % serr)
        for r in sd.get("明细") or []:
            if isinstance(r, dict) and r.get("板块") and num(r.get("评分")) is not None:
                key = canon_sector(r.get("板块标准名") or r["板块"])
                sec_score[key] = num(r["评分"])
                sec_tier[key] = r.get("分层") or ""
        sec_src = "score 输出（%s）" % a.sector_score
    if not sec_score:
        sec_src = "缺失 → 最终得分 = 子板块独立得分（0.5/0.5 权重归一化）"

    rows, missing = [], []

    # 单一事实源漂移检测：
    # 美股 = stock_pool.json 板块名必须在 domain_dict.json 登记；
    # 加密货币 = 板块名必须在 crypto_pool.json categories 登记（00 §4.6-1）
    if mk["pool_kind"] == "crypto" and name2sector:
        _drift = sorted({s for s in name2sector.values() if s and s not in crypto_cats})
        if _drift:
            missing.append("crypto_pool.json categories 未登记该板块: %s"
                           "（板块命名以 crypto_pool.json categories 为准，请先统一命名再评分）" % "、".join(_drift))
    else:
        if name2sector:
            _drift = sorted({s for s in name2sector.values() if s and sector_code(s) is None})
            if _drift:
                missing.append("stock_pool.json 子板块所属板块未在单一事实源 domain_dict.json 登记: %s"
                               "（板块命名以 domain_dict.json 为准，请先统一命名再评分）" % "、".join(_drift))
        if DOMAIN_ERR:
            missing.append("板块单一事实源 domain_dict.json 读取失败（已用内置兜底值）: %s" % DOMAIN_ERR)

    def unscorable(t, sector, why, ndays):
        missing.append(why)
        rows.append({"子板块": t, "板块": sector, "评分": None, "排名": None, "分层": "无法评分",
                     "动量分": None, "周期分": None, "因子明细": {}, "可用交易日": ndays})

    for t, series in subs.items():
        sector = name2sector.get(t)
        if not sector:
            missing.append("%s: 不在 %s，板块映射缺失" % (t, mk["pool_file"]))
        pairs = []
        for d in sorted(str(x) for x in series.keys()):
            e = num(series[d])
            s = spy_map.get(d)
            if e and s:
                pairs.append((e, s))
        if len(pairs) < 21:
            unscorable(t, sector, "%s: 与 %s 重合可用交易日 %d < 21，无法评分"
                       % (t, benchmark, len(pairs)), len(pairs))
            continue
        ve, vs = [p[0] for p in pairs], [p[1] for p in pairs]
        roc = {}
        for name, n in windows:
            if len(pairs) > n:
                roc[name] = (ve[-1] / vs[-1]) / (ve[-1 - n] / vs[-1 - n]) * 100.0 - 100.0
        if not roc:
            unscorable(t, sector, "%s: 全部窗口数据不足" % t, len(pairs))
            continue
        wsum = sum(weights[w] for w in roc)
        mom_raw = sum(weights[w] * roc[w] for w in roc) / wsum
        cyc_raw = None
        if a.cycle and sector:
            if mk["pool_kind"] == "crypto":
                cyc_raw = (mk["cycle_scores"] or {}).get(a.cycle, {}).get(sector)
            else:
                etf = next((k for k, v in SECTOR_NAMES.items() if v == sector), None)
                if etf:
                    cyc_raw = CYCLE_SCORES.get(a.cycle, {}).get(etf)
        rows.append({"子板块": t, "板块": sector, "评分": None, "排名": None, "分层": "",
                     "动量分": None, "周期分": None,
                     "因子明细": {k: g(v) for k, v in roc.items()},
                     "可用交易日": len(pairs), "_mom": mom_raw, "_cyc": cyc_raw})

    scored = [r for r in rows if r.get("_mom") is not None]
    if not scored:
        fail("所有子板块均无法评分（数据不足）", 数据缺失=missing)
    lo, hi = min(r["_mom"] for r in scored), max(r["_mom"] for r in scored)
    span = hi - lo
    for r in scored:
        r["动量分"] = g(50.0 if span == 0 else (r["_mom"] - lo) / span * 100.0)
        if a.cycle and r["_cyc"] is not None:
            r["周期分"] = g((r["_cyc"] + 2) / 4.0 * 100.0)
        elif a.cycle and r["板块"]:
            missing.append("%s: 所属板块 %s 不在 02 §4.2.2 周期因子表，周期分缺失" % (r["子板块"], r["板块"]))
        w = FACTOR_WEIGHTS["动量"] + (FACTOR_WEIGHTS["周期"] if r["周期分"] is not None else 0.0)
        val = FACTOR_WEIGHTS["动量"] * r["动量分"]
        if r["周期分"] is not None:
            val += FACTOR_WEIGHTS["周期"] * r["周期分"]
        r["独立得分"] = g(val / w)
    for r in scored:
        r.pop("_mom", None)
        r.pop("_cyc", None)

    # 最终得分 = 0.5 × 板块得分 + 0.5 × 独立得分（03 §5.3）
    for r in scored:
        sc = sec_score.get(r["板块"]) if r["板块"] else None
        r["板块得分"] = g(sc) if sc is not None else None
        r["板块分层"] = sec_tier.get(r["板块"] or "") if sc is not None else None
        if sc is not None:
            r["评分"] = g(0.5 * sc + 0.5 * r["独立得分"])
        else:
            r["评分"] = r["独立得分"]
            missing.append("%s: 板块 %s 得分缺失（score 输出未含或子板块未映射），最终得分取独立得分"
                           % (r["子板块"], r["板块"] or "未知"))

    # 03 §5.4：子板块得分不得高于所属板块得分 1 个标准差（板块得分分布的总体标准差）
    clamp_applied = []
    sec_vals = [v for v in sec_score.values() if v is not None]
    if len(sec_vals) >= 2:
        mean = sum(sec_vals) / len(sec_vals)
        std = math.sqrt(sum((v - mean) ** 2 for v in sec_vals) / len(sec_vals))
        for r in scored:
            sc = sec_score.get(r["板块"]) if r["板块"] else None
            if sc is not None:
                cap = sc + std
                if r["评分"] > cap + 1e-9:
                    r["评分"] = g(cap)
                    clamp_applied.append("%s: 最终得分钳制为 板块得分+1σ=%s（03 §5.4）" % (r["子板块"], g(cap)))
    else:
        missing.append("板块得分分布 < 2 个值，1σ 钳制不适用（03 §5.4）")

    # 排序 / 分层（03 §5.4）
    scored.sort(key=lambda r: (-r["评分"], r["子板块"]))
    n_sc = len(scored)
    top_n = min(a.top_strong, n_sc // 2) if n_sc > 1 else 0
    weak_n = min(a.bottom_weak, n_sc // 2)
    for i, r in enumerate(scored):
        r["排名"] = i + 1
        r["分层"] = "强势" if i < top_n else ("弱势" if i >= n_sc - weak_n else "中性")

    # 双层决策矩阵（03 §5.3.2）
    for r in scored:
        st, su = r.get("板块分层"), r["分层"]
        if st == "强势" and su == "强势":
            r["决策"] = "强烈加仓（板块-子板块共振）"
        elif st == "强势" and su == "弱势":
            r["决策"] = "仅保留，不加仓"
        elif st == "弱势" and su == "强势":
            r["决策"] = "保留底仓观察"
        elif st == "弱势" and su == "弱势":
            r["决策"] = "优先减仓/清仓"
        elif st is None:
            r["决策"] = "矩阵未覆盖（板块分层缺失）"
        else:
            r["决策"] = "矩阵未覆盖（按得分与排名判断）"
        if st == "弱势" and su == "强势":
            r["提示"] = "板块弱但子板块强 → 子板块仓位上限减半（03 §5.4）"

    strong = [r["子板块"] for r in scored if r["分层"] == "强势"]
    neutral = [r["子板块"] for r in scored if r["分层"] == "中性"]
    weak = [r["子板块"] for r in scored if r["分层"] == "弱势"]
    decision_sum = {}
    for r in scored:
        decision_sum.setdefault(r["决策"], []).append(r["子板块"])

    _w_desc = "/".join("%s %d%%" % (n, round(weights[n] * 100)) for n, _ in windows)
    result = {
        "成功": True,
        "工具": "rebalance_tools score2 (v" + VERSION + ")",
        "版本": VERSION,
        "市场": market,
        "文档依据": "03_子板块评分体系.md §5.3/§5.4 / 02_板块评分与排序.md §4",
        "基准": benchmark,
        "周期阶段": a.cycle,
        "窗口": {n: k for n, k in windows},
        "权重": {"所属板块得分": 0.5, "子板块独立得分": 0.5},
        "板块得分来源": sec_src,
        "说明": "独立得分 = 动量(%s, min-max 归一) + 周期因子(继承所属板块 02 §4.2.2)；"
                "其余因子无内置数据源，权重归一化；钳制 = 板块得分 + 1×板块得分分布总体标准差；"
                "板块映射以 %s 为准" % (_w_desc, mk["pool_file"]),
        "明细": [{k: r[k] for k in ("子板块", "板块", "排名", "分层", "评分", "独立得分", "动量分", "周期分",
                                   "板块得分", "板块分层", "决策", "提示", "因子明细", "可用交易日") if k in r}
                for r in rows],
        "强势子板块": strong,
        "中性子板块": neutral,
        "弱势子板块": weak,
        "决策汇总": decision_sum,
        "1σ钳制": clamp_applied,
        "数据缺失": missing,
    }
    emit(result)


# =====================================================================
# map — 持仓映射与 S1–S5 分层（04 §6）
# =====================================================================

def run_map(argv):
    ap = argparse.ArgumentParser(
        prog="rebalance_tools map",
        description="持仓映射与 S1–S5 分层（04 §6）：ticker→板块/子板块 以 市场选股池 为准"
                    "（美股 stock_pool.json / 加密货币 crypto_pool.json）；"
                    "层级矩阵（矩阵按市场取）；仓位占比（分母 = B）；T3/高弹性 统计",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    add_market_arg(ap)
    ap.add_argument("--strategies", required=True,
                    help="cta_strategies_get_all 原始 JSON（数组或 {策略名: {...}}）；"
                         "每元素须含 策略名称/ticker(或 品种信息.ticker)/实际持仓/当前行情价格")
    ap.add_argument("--classifications", required=True,
                    help='分层 JSON：{"板块": {"信息技术": "强势", ...}, "子板块": {"半导体与半导体设备": "中性", ...}}；'
                         '加密货币 子板块键 = "板块-分桶" 组合名（如 "L1 公链-11-30"）')
    ap.add_argument("--balance", type=float, required=True, help="账户权益 balance（USD/USDT）")
    ap.add_argument("--invest-mult", type=float, default=None,
                    help="缺省 = 市场预设（美股 2 / 加密货币 1）")
    ap.add_argument("--pool", default=None, help="选股池；缺省 = 市场默认池")
    a = parsed(ap, argv, "map")
    market = resolve_market(a.market)
    mk = MARKET_PRESETS[market]
    a.invest_mult = a.invest_mult if a.invest_mult is not None else mk["defaults"]["invest_mult"]
    a.pool = a.pool or default_pool_path(market)

    if a.balance <= 0:
        fail("balance 必须 > 0")
    data, err = load_json_file(a.strategies, "策略快照")
    if err:
        fail(err)
    if isinstance(data, dict):
        entries = []
        for k, v in data.items():
            if isinstance(v, dict):
                e = dict(v)
                e.setdefault("策略名称", k)
                entries.append(e)
            else:
                entries.append({"策略名称": k})
    elif isinstance(data, list):
        entries = [e for e in data if isinstance(e, dict)]
    else:
        fail("策略快照须为 JSON 数组或对象")
    if not entries:
        fail("策略快照无有效条目（期望: 数组或对象，每元素含 策略名称/ticker(或 品种信息.ticker)/实际持仓/当前行情价格）")
    cls, err = load_json_file(a.classifications, "分层")
    if err or not isinstance(cls, dict):
        fail("分层文件不可读或不是 JSON 对象: %s" % err)
    sec_cls = {str(k): str(v) for k, v in (cls.get("板块") or {}).items()} if isinstance(cls.get("板块"), dict) else {}
    sub_cls = {str(k): str(v) for k, v in (cls.get("子板块") or {}).items()} if isinstance(cls.get("子板块"), dict) else {}

    pool_idx, pool_err = load_pool_index(a.pool, market)
    if pool_idx is None:
        pool_idx = {}
    B = a.balance * a.invest_mult
    rows, unmapped, notes = [], [], []
    layer_sum, sec_sum, sub_sum = {}, {}, {}
    t3_total, t3_single_max, t3_count = 0.0, 0.0, 0
    hb_total, hb60_max, hb3160_max, hb_count = 0.0, 0.0, 0.0, 0
    if pool_err:
        notes.append("选股池不可读（%s）：映射降级为快照自带 板块/子板块，T3/高弹性统计不适用" % pool_err)
    if all(not num(e.get("实际持仓")) for e in entries):
        notes.append("全部 %d 条策略 实际持仓 为 0/缺失（空仓或快照落盘有误，请人工确认输入文件）" % len(entries))

    for e in entries:
        name = e.get("策略名称") or "(无名)"
        info = e.get("品种信息") or {}
        ticker = e.get("ticker") or (info.get("ticker") if isinstance(info, dict) else None)
        if not ticker and e.get("vt_symbol"):
            ticker = str(e["vt_symbol"]).split(".")[0]
            notes.append("%s: ticker 取自 vt_symbol 首段（%s），建议核对" % (name, e.get("vt_symbol")))
        pos = num(e.get("实际持仓"))
        price = num(e.get("当前行情价格"))
        value = pos * price if (pos and pos > 0 and price) else None
        if pos and pos > 0 and (price is None or price <= 0):
            notes.append("%s: 当前行情价格缺失/非正，市值无法计算" % name)
        pct = value / B * 100.0 if value is not None else None

        pool_row = pool_idx.get(ticker) if ticker else None
        if pool_row:
            sector, subsec, t_tier, src = pool_row["板块"], pool_row["子板块"], pool_row["T层"], mk["pool_file"]
        else:
            sector, subsec = e.get("板块") or (info.get("行业") if isinstance(info, dict) else None), e.get("子板块")
            t_tier = None
            src = "快照" if (sector or subsec) else None
        if not sector and not subsec:
            unmapped.append({"策略名称": name, "ticker": ticker,
                             "原因": "品种信息无行业 / 不在 %s" % mk["pool_file"]
                             if market == "美股" else
                             "品种信息缺失 / 不在 %s（策略名无法解析出币）" % mk["pool_file"]})
            continue
        s_v = sec_cls.get(sector, "缺失") if sector else "缺失"
        u_v = sub_cls.get(subsec, "缺失") if subsec else "缺失"
        layer, basis = s_layer(s_v, u_v, market)
        if layer is None:
            layer, basis = "未分层", basis
            notes.append("%s: %s，请补充分层" % (name, basis))
        rows.append({"策略名称": name, "ticker": ticker, "板块": sector, "子板块": subsec,
                     "T层": t_tier, "层级": layer, "层级依据": basis, "映射来源": src,
                     "实际持仓": g(pos) if pos is not None else None,
                     "当前行情价格": g(price) if price is not None else None,
                     "市值": g(value), "仓位占比_pct": g(pct) if pct is not None else None})
        if value is not None and pct is not None:
            L = layer_sum.setdefault(layer, {"策略数": 0, "市值": 0.0, "占比_pct": 0.0})
            L["策略数"] += 1
            L["市值"] += value
            L["占比_pct"] += pct
            if sector:
                sec_sum[sector] = sec_sum.get(sector, 0.0) + pct
            if subsec:
                sub_sum[subsec] = sub_sum.get(subsec, 0.0) + pct
        if t_tier == "T3" and pct is not None:
            t3_count += 1
            t3_total += pct
            t3_single_max = max(t3_single_max, pct)
        bucket = (pool_row or {}).get("bucket") if pool_row else None
        if bucket in mk["hb_buckets"] and pct is not None:
            hb_count += 1
            hb_total += pct
            if bucket == "60+":
                hb60_max = max(hb60_max, pct)
            else:
                hb3160_max = max(hb3160_max, pct)

    stat_block = ({
        "T3统计": {"T3只数": t3_count, "总占比_pct": g(t3_total), "单只最大_pct": g(t3_single_max),
                   "总超5pct上限": t3_total > HARD_CAP_T3_TOTAL_PCT + 1e-9,
                   "单只超1pct上限": t3_single_max > HARD_CAP_T3_SINGLE_PCT + 1e-9}}
        if market == "美股" else {
        "高弹性统计": {"高弹性只数": hb_count, "总占比_pct": g(hb_total),
                       "60+单只最大_pct": g(hb60_max), "31-60单只最大_pct": g(hb3160_max),
                       "总超15pct上限": hb_total > mk["hb_total_pct"] + 1e-9,
                       "60+单只超2pct上限": hb60_max > mk["hb_single_pct"].get("60+", 0) + 1e-9,
                       "31-60单只超3pct上限": hb3160_max > mk["hb_single_pct"].get("31-60", 0) + 1e-9}})
    result = {
        "成功": True,
        "工具": "rebalance_tools map (v" + VERSION + ")",
        "版本": VERSION,
        "市场": market,
        "文档依据": "04_持仓映射与分层.md §6",
        "总预算B": g(B),
        "明细": rows,
        "未映射持仓": unmapped,
        "层级汇总": {k: {"策略数": v["策略数"], "市值": g(v["市值"]), "占比_pct": g(v["占比_pct"])}
                    for k, v in layer_sum.items()},
        "板块汇总": {k: g(v) for k, v in sorted(sec_sum.items(), key=lambda kv: -kv[1])},
        "子板块汇总": {k: g(v) for k, v in sorted(sub_sum.items(), key=lambda kv: -kv[1])},
        **stat_block,
        "告警": notes,
    }
    emit(result)


# =====================================================================
# exec — 执行约束检查与拆单（05 §7.3.3 / 07 §8.3 / 08 §9）
# =====================================================================

AVOID_WINDOWS = ((9 * 60 + 30, 9 * 60 + 45, "开盘前 15 分钟"),
                 (12 * 60, 12 * 60 + 30, "午休前后"))
GOOD_WINDOWS = ((9 * 60 + 30, 10 * 60, "开盘后 30 分钟"),
                (11 * 60, 14 * 60, "午盘"),
                (15 * 60 + 30, 16 * 60, "收盘前 30 分钟"))
DIRECTION_ALIASES = {"买入": "buy", "卖出": "sell"}


def run_exec(argv):
    ap = argparse.ArgumentParser(
        prog="rebalance_tools exec",
        description="执行约束检查与拆单（05 §7.3.3 不追高/不买暴涨 + 07 §8.3 流动性 + 08 §9 拆单/滑点/时段）。"
                    "plan JSON: {\"指令\": [{名称, ticker, 方向(买入/卖出), 金额, 日均成交额, "
                    "当日涨幅_pct(买), 五日涨幅_pct(买), 平均价差_pct, 执行时间ET/执行时间UTC(HH:MM), 价格, MA20}, ...]}；"
                    "不追高/不买暴涨阈值与时段窗口由 --market 决定（美股 3%/15% 美东时段；加密货币 5%/30% UTC 资金费率窗口）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="金额/日均成交额单位 = 账户币种；当日/五日涨幅与 平均价差_pct 单位均为 %；"
               "否决项（否决原因非空）不得执行，提示项不阻断")
    add_market_arg(ap)
    ap.add_argument("--plan", required=True, help="执行计划 JSON（格式见 description）")
    ap.add_argument("--liquidity-pct", type=float, default=1.0,
                    help="单日单只买卖金额上限 = 日均成交额 × 该百分比（07 §8.3 默认 1；加密货币 60+ 建议收紧 0.5）")
    a = parsed(ap, argv, "exec")
    market = resolve_market(a.market)
    mk = MARKET_PRESETS[market]
    avoid_win = mk["avoid_windows"] or AVOID_WINDOWS
    good_win = mk["good_windows"] or GOOD_WINDOWS

    data, err = load_json_file(a.plan, "执行计划")
    if err or not isinstance(data, dict):
        fail("执行计划须为 JSON 对象（含 指令 数组）: %s" % err)
    raw = data.get("指令")
    if not isinstance(raw, list) or not raw:
        fail("指令 须为非空数组")

    rows, bad = [], []
    for i, it in enumerate(raw):
        if not isinstance(it, dict):
            bad.append("第%d条不是对象" % (i + 1))
            continue
        nm = it.get("名称") or it.get("ticker") or ("第%d条" % (i + 1))
        raw_dir = it.get("方向")
        d = DIRECTION_ALIASES.get(raw_dir, raw_dir) if isinstance(raw_dir, str) else raw_dir
        amt = num(it.get("金额"))
        adtv = num(it.get("日均成交额"))
        reasons, notes = [], []
        if d not in ("buy", "sell"):
            reasons.append("方向 须为 买入/buy 或 卖出/sell（实际: %s）" % it.get("方向"))
        if amt is None or amt <= 0:
            reasons.append("金额 缺失或非正")
        ratio = amt / adtv * 100.0 if (amt and amt > 0 and adtv and adtv > 0) else None
        if amt and amt > 0:
            if ratio is None:
                notes.append("日均成交额 缺失：流动性检查无法判定")
            elif ratio > a.liquidity_pct + 1e-12:
                reasons.append("单日单只金额占日均成交额 %s%% > 上限 %s%%（07 §8.3）"
                               % (g(ratio), g(a.liquidity_pct)))
        split = None
        if ratio is not None:
            if ratio < 5:
                split = {"档位": "小单(<5%日均成交额)", "方式": "一次性市价单", "周期": "当日"}
            elif ratio <= 20:
                split = {"档位": "中单(5%-20%日均成交额)", "方式": "分 3-5 笔，每 30 分钟一笔", "周期": "当日"}
            else:
                split = {"档位": "大单(>20%日均成交额)", "方式": "分 2-3 天，每天均匀拆单", "周期": "2-3 天"}
        if d == "buy":
            day_chg = num(it.get("当日涨幅_pct"))
            if day_chg is not None and day_chg > mk["chase_high_pct"]:
                reasons.append("当日涨幅 %s%% > %s%%：不追高，不买入（05 §7.3.3）"
                               % (g(day_chg), g(mk["chase_high_pct"])))
            f5 = num(it.get("五日涨幅_pct"))
            if f5 is not None and f5 > mk["spike_pct"]:
                reasons.append("5 日涨幅 %s%% > %s%%：不买暴涨过的（05 §7.3.3）"
                               % (g(f5), g(mk["spike_pct"])))
            price, ma20 = num(it.get("价格")), num(it.get("MA20"))
            if price and ma20 and price > ma20:
                notes.append("价格高于 20 日均线：优先回踩 MA20 再入场（05 §7.3.3 回踩买入）")
        slip = None
        spread = num(it.get("平均价差_pct"))
        if ratio is not None and spread is not None and spread > 0:
            slip = ratio * spread * 2 / 100.0
            if slip > 0.3 + 1e-12:
                notes.append("预期滑点 %s%% > 0.3%%：缩小单笔规模 / 延长执行周期 / 改用限价单（08 §9.3）" % g(slip))
        t = it.get(mk["exec_time_key"]) or it.get("执行时间ET" if market == "美股" else "执行时间UTC")
        if isinstance(t, str) and t.strip():
            try:
                hh, mm = t.strip().split(":")
                minutes = int(hh) * 60 + int(mm)
            except ValueError:
                notes.append("%s 格式无效（应为 HH:MM）: %s" % (mk["exec_time_key"], t))
            else:
                avoid = [lab for lo, hi, lab in avoid_win if lo <= minutes < hi]
                good = [lab for lo, hi, lab in good_win if lo <= minutes < hi]
                if avoid:
                    notes.append("执行时段 %s（08 §9.2 应避免）" % "/".join(avoid))
                elif good:
                    notes.append("执行时段 %s（08 §9.2 最佳窗口）" % "/".join(good))
                elif mk["outside_note"] and (minutes < 9 * 60 + 30 or minutes >= 16 * 60):
                    notes.append("执行时间 %s 在正常交易时段 9:30-16:00 之外（美东）" % t.strip())
        rows.append({"名称": nm, "ticker": it.get("ticker"), "方向": d if d in ("buy", "sell") else it.get("方向"),
                     "金额": g(amt) if amt is not None else None,
                     "日均成交额": g(adtv) if adtv is not None else None,
                     "金额占日均成交额_pct": g(ratio) if ratio is not None else None,
                     "预期滑点_pct": g(slip) if slip is not None else None,
                     "拆单计划": split, "否决原因": reasons, "提示": notes,
                     "结论": "否决" if reasons else "通过"})
    n_reject = sum(1 for r in rows if r["结论"] == "否决")
    result = {
        "成功": True,
        "工具": "rebalance_tools exec (v" + VERSION + ")",
        "版本": VERSION,
        "文档依据": "05_调仓执行流程.md §7.2/§7.3.3 / 07_仓位约束与风控.md §8.3 / 08_拆单与执行优化.md §9",
        "流动性上限_日均成交额百分比": g(a.liquidity_pct),
        "明细": rows,
        "汇总": {"指令数": len(rows), "通过": len(rows) - n_reject, "否决": n_reject},
        "存在否决": n_reject > 0,
        "节奏参考": {
            "减仓（05 §7.2.1）": "Day1 弱势板块 30-50% → Day2 仍弱再减剩余 30% → 连续 3 周 Bottom3 清仓",
            "加仓（05 §7.3.2）": "释放资金 Day1 50% / Day2 30% / Day3 20%，剩余留现金缓冲",
            "建仓（05 §7.3.3）": "新开仓分 3 档 30% / 30% / 40%",
        },
        ("时段参考（08 §9.2，美东）" if market == "美股" else "时段参考（08 §9.2，UTC）"): (
            {"最佳": "9:30-10:00 / 11:00-14:00 / 15:30-16:00",
             "避免": "9:30-9:45 / 12:00-12:30"}
            if market == "美股" else
            {"最佳": "04:00-08:00（亚盘）/ 11:00-14:00（欧盘）",
             "避免": "00:00/08:00/16:00 ±30min 资金费率结算 / 13:30-15:00 美股开盘"}),
        "计划告警": bad,
    }
    result["市场"] = market
    emit(result)


# =====================================================================
# cycle — 双周期再平衡判定（09 §10 / 07 §8.4）
# =====================================================================

WEEKDAYS_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
CYCLE_TRIGGERS = ("top3_change", "subsector_top6_change", "macro_switch",
                  "sector_over_cap", "subsector_over_cap", "defensive_mode", "abnormal_event",
                  "rotation_phase_switch")  # 加密货币/07 §8.4 #4 轮动阶段切换


def parse_iso_date(s):
    try:
        return datetime.date.fromisoformat(s.strip())
    except (ValueError, AttributeError):
        return None


def run_cycle(argv):
    ap = argparse.ArgumentParser(
        prog="rebalance_tools cycle",
        description="双周期再平衡判定（09 §10 / 07 §8.4）：月度大再平衡（幅度 = 市场预设，"
                    "默认 美股 ±10% / 加密货币 ±20%）/ 周度微调（每周五，默认 美股 ±3% / 加密货币 ±5%）"
                    "/事件驱动；输出 周期 与 max_delta_pct 建议值",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    add_market_arg(ap)
    ap.add_argument("--date", required=True, help="执行日期 YYYY-MM-DD")
    ap.add_argument("--last-rebalance", default=None,
                    help="上次调仓日期 YYYY-MM-DD；跨月且本月未调 → 月度窗口到期")
    ap.add_argument("--trigger", action="append", choices=CYCLE_TRIGGERS, default=[],
                    help="07 §8.4/09 §10.3 触发条件（可重复）：top3_change / subsector_top6_change / "
                         "macro_switch / sector_over_cap / subsector_over_cap / defensive_mode / "
                         "abnormal_event / rotation_phase_switch（加密货币）")
    a = parsed(ap, argv, "cycle")
    market = resolve_market(a.market)
    mk = MARKET_PRESETS[market]
    amp_m, amp_w, amp_e = mk["ampl_monthly"], mk["ampl_weekly"], mk["ampl_event"]

    d = parse_iso_date(a.date)
    if d is None:
        fail("--date 须为 YYYY-MM-DD: %s" % a.date)
    last = parse_iso_date(a.last_rebalance) if a.last_rebalance else None
    if a.last_rebalance and last is None:
        fail("--last-rebalance 须为 YYYY-MM-DD: %s" % a.last_rebalance)

    is_fri = d.weekday() == 4
    is_weekend = d.weekday() >= 5
    new_month = last is not None and (d.year, d.month) > (last.year, last.month)
    notes = []
    if is_weekend and market == "美股":
        notes.append("该日为周末，非美股交易日，请核对日期")
    if last is None:
        notes.append("未提供 --last-rebalance：无法判定「月度窗口到期」，请补传以启用月度判定")
    if new_month:
        cycle, sugg = "月度大再平衡", amp_m
        notes.append(("09 §10.1：月度大再平衡（每月第一个交易日）——11 板块 + 29 子板块全量重算重排，"
                      "幅度 ±%g%% 以内；是否第一个交易日由 AI 按交易日历判定，非首个交易日则为补做" % amp_m)
                     if market == "美股" else
                     ("09 §10.1：月度大再平衡（每月 1 日，7×24 无节假日）——10 板块 + 4 子维度（市值分桶）"
                      "全量重算重排 + 轮动阶段复判；同步 选币池校准/池外探测，幅度 ±%g%% 以内" % amp_m))
        if is_fri:
            notes.append("同时为周五：月度优先于周度（%g%% > %g%%）" % (amp_m, amp_w))
    elif is_fri:
        cycle, sugg = "周度微调", amp_w
        notes.append(("09 §10.2：周度小幅微调——仅更新动量+情绪因子、检查排名重大变化，幅度 ±%g%% 以内" % amp_w)
                     if market == "美股" else
                     ("09 §10.2：周度小幅微调——仅更新动量+资金确认（费率/TVL/OI）+ 轮动阶段复核，"
                      "幅度 ±%g%% 以内" % amp_w))
    elif a.trigger:
        cycle, sugg = "事件驱动", amp_e
        notes.append("07 §8.4 / 09 §10.3 触发条件命中 → 即时评估；双周期机制未对事件触发设定专属幅度，"
                     "按任务参数默认 %g 建议" % amp_e)
    else:
        cycle, sugg = "无固定周期", None
        notes.append("未到月度/周五窗口且无触发条件（07 §8.4）：本轮无需调仓，除非条件触发")
    if a.trigger and new_month:
        notes.append("触发条件与月度窗口并存：并入月度大再平衡统一执行")

    result = {
        "成功": True,
        "工具": "rebalance_tools cycle (v" + VERSION + ")",
        "版本": VERSION,
        "市场": market,
        "文档依据": "09_双周期再平衡机制.md §10 / 07_仓位约束与风控.md §8.4",
        "日期": a.date,
        "星期": WEEKDAYS_CN[d.weekday()],
        "上次调仓": a.last_rebalance,
        "月度窗口到期": new_month,
        "周期": cycle,
        "max_delta_pct建议": sugg,
        "触发条件": a.trigger,
        "说明": notes,
    }
    emit(result)


# =====================================================================
# assemble — 调仓报告骨架装配（10 §11.1/§11.2）
# =====================================================================

LAYER_TO_KEY = {"S1 强烈加仓": "S1_强烈加仓", "S2 适度加仓": "S2_适度加仓",
                "S3 维持": "S3_维持", "S4 观察减仓": "S4_观察减仓", "S5 优先减仓": "S5_优先减仓"}


def run_assemble(argv):
    ap = argparse.ArgumentParser(
        prog="rebalance_tools assemble",
        description="调仓报告骨架装配（10 §11.1）：fund + gap（+可选 score/score2/map 输出）→ "
                    "17 顶层字段齐全的报告骨架；数值字段工具计算（顶层 %/$ 互验自洽，10 §11.2 规则 4），"
                    "文本字段 / 调仓指令 / 新增策略 留 AI 填；AI 填完必须 verify --report 复算",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--fund", required=True, help="fund 子命令输出 JSON")
    ap.add_argument("--gap", required=True, help="gap 子命令输出 JSON")
    ap.add_argument("--score", default=None, help="score 输出 JSON（强势/弱势板块）")
    ap.add_argument("--score2", default=None, help="score2 输出 JSON（强势/弱势子板块）")
    ap.add_argument("--map", default=None, help="map 输出 JSON（S1–S5 持仓映射）")
    ap.add_argument("--analysis-time", default=None, help="分析时间（带时区，如 2026-09-15 18:00:00 +08:00）")
    ap.add_argument("--mode", default=None, choices=("积极", "正常", "防御"),
                    help="操作模式（01 §3.4 大盘总开关；省略 → 正常 + 待人工确认）")
    ap.add_argument("--cycle-stage", default=None, choices=list(CYCLES) + list(CRYPTO_CYCLES),
                    help="周期阶段（美股: 01 §3.3 经济周期 / 加密货币: 01 §3.3 轮动阶段）")
    ap.add_argument("--out", default=None, help="结果另存文件（stdout 仍输出完整 JSON）")
    add_market_arg(ap)
    a = parsed(ap, argv, "assemble")
    market = resolve_market(a.market)
    mk = MARKET_PRESETS[market]
    if a.cycle_stage and a.cycle_stage not in mk["cycles"]:
        fail("周期阶段 %s 与 市场=%s 不匹配（允许: %s）" % (a.cycle_stage, market, "/".join(mk["cycles"])))
    ccy = mk["currency"]

    fund, f1 = load_json_file(a.fund, "fund 输出")
    gap, f2 = load_json_file(a.gap, "gap 输出")
    if f1 or not isinstance(fund, dict) or fund.get("成功") is not True:
        fail("fund 输出不可读或不是 成功=true 的 JSON: %s" % f1)
    if f2 or not isinstance(gap, dict) or gap.get("成功") is not True:
        fail("gap 输出不可读或不是 成功=true 的 JSON: %s" % f2)

    fi = fund.get("输入") or {}
    balance = num((fi.get("权益balance") if isinstance(fi, dict) else None))
    available = num((fi.get("可用现金available") if isinstance(fi, dict) else None))
    # fund 输出的 总预算B 在「预算」对象内（旧口径兼容顶层）
    B = num((fund.get("预算") or {}).get("总预算B")) or num(fund.get("总预算B"))
    if balance is None or balance <= 0 or B is None or B <= 0:
        fail("fund 输出缺 输入.权益balance / 总预算B（或为非正）")
    cn_params = fi.get("任务参数") or {}
    if not isinstance(cn_params, dict):
        cn_params = {}
    task_params = {en: cn_params.get(cn) for cn, en in EN_PARAMS.items()}
    mult = num(task_params.get("invest_mult"))
    if mult is None:
        mult = B / balance
        task_params["invest_mult"] = g(mult)
    adj = (fund.get("参数自检") or {}).get("调整记录") or []

    fm = gap.get("资金匹配") or {}
    sell_free = num(fm.get("卖出释放"))
    buy_use = num(fm.get("买入使用"))
    net_flow = num(fm.get("净额"))
    remain = num(fm.get("剩余现金"))
    g_rows = [r for r in (gap.get("明细") or []) if isinstance(r, dict)]
    if not g_rows:
        fail("gap 输出 明细 为空（gap 快照为空或未产生条目，请先核对 gap 命令行的 --snapshot）")
    truncated = [x.get("策略名称") for x in (gap.get("截断名单") or [])
                 if isinstance(x, dict) and x.get("策略名称")]
    n_open = sum(1 for r in g_rows if r.get("动作") == "开仓")

    # 顶层 % 与 $ 互验（10 §11.2 规则 4）
    # 口径（2026-09-15 修订）：调仓后总仓位_pct 的分子 = 调仓后持仓市值
    #   = Σ(gap 明细.目标资金) ÷ B。现金_pct = 100 − 总仓位_pct。
    # 旧口径 (B − 剩余现金)/B 是「资金记账恒等式」：在 available ≈ 权益 的保证金账户下
    #   恒等于 ~50%，与真实持仓无关（历史事故：报告 49.16% vs 实际持仓 3.86%）。
    #   现金类指标另有 资金现状.现金占比_pct = 可用现金 ÷ 权益，两者不得互代。
    tgt_value = None
    if g_rows:
        tgt_value = sum((num(r.get("目标资金")) or 0.0) for r in g_rows)
    if tgt_value is not None and B:
        pos_pct = g(tgt_value / B * 100.0)
        cash_pct = g(100.0 - pos_pct)
    else:
        pos_pct = cash_pct = None

    # 目标分配（仅本次涉及板块：存在 非维持 动作 的板块）
    sec_cur, sec_tgt, sec_active = {}, {}, set()
    for r in g_rows:
        s = r.get("板块") or ""
        sec_cur[s] = sec_cur.get(s, 0.0) + (num(r.get("当前市值")) or 0.0)
        sec_tgt[s] = sec_tgt.get(s, 0.0) + (num(r.get("目标资金")) or 0.0)
        if r.get("动作") not in (None, "", "维持"):
            sec_active.add(s)
    target_alloc = {}
    for s in sorted(sec_active, key=lambda x: -sec_tgt.get(x, 0.0)):
        target_alloc[s] = {"当前_pct": g(sec_cur.get(s, 0.0) / B * 100.0),
                           "目标_pct": g(sec_tgt.get(s, 0.0) / B * 100.0),
                           "依据": ""}

    # 强势/弱势 板块与子板块
    pending = []
    strong_sec, weak_sec, strong_sub, weak_sub = [], [], [], []
    if a.score:
        so, so_err = load_json_file(a.score, "score 输出")
        if so_err or not isinstance(so, dict) or so.get("成功") is not True:
            fail("score 输出不可读或不是 成功=true 的 JSON: %s" % so_err)
        strong_sec, weak_sec = so.get("强势板块") or [], so.get("弱势板块") or []
    if a.score2:
        s2, s2_err = load_json_file(a.score2, "score2 输出")
        if s2_err or not isinstance(s2, dict) or s2.get("成功") is not True:
            fail("score2 输出不可读或不是 成功=true 的 JSON: %s" % s2_err)
        strong_sub, weak_sub = s2.get("强势子板块") or [], s2.get("弱势子板块") or []
    if not a.score:
        pending.append("强势/弱势板块 未提供（--score），AI 按 02 §4 排名填写")
    if not a.score2:
        pending.append("强势/弱势子板块 未提供（--score2），AI 按 03 §5.4 排名填写")

    # 持仓映射（map 输出 → 模板五层键）
    mapping = {"S1_强烈加仓": [], "S2_适度加仓": [], "S3_维持": [],
               "S4_观察减仓": [], "S5_优先减仓": [], "未映射持仓": []}
    if a.map:
        mo, mo_err = load_json_file(a.map, "map 输出")
        if mo_err or not isinstance(mo, dict) or mo.get("成功") is not True:
            fail("map 输出不可读或不是 成功=true 的 JSON: %s" % mo_err)
        for r in mo.get("明细") or []:
            if not isinstance(r, dict):
                continue
            layer_v = r.get("层级")
            k = LAYER_TO_KEY.get(layer_v) if isinstance(layer_v, str) else None
            if k is None:
                mapping["未映射持仓"].append({"ticker": r.get("ticker"),
                                              "原因": "层级=%s 未分层，请补充分层后归层" % r.get("层级")})
                continue
            mapping[k].append({"策略名称": r.get("策略名称"), "ticker": r.get("ticker"),
                               "板块": r.get("板块"), "子板块": r.get("子板块"),
                               "实际持仓": r.get("实际持仓"), "持仓盈亏比": None,
                               "仓位占比_pct": r.get("仓位占比_pct"), "层级依据": r.get("层级依据")})
        for u in mo.get("未映射持仓") or []:
            if isinstance(u, dict):
                mapping["未映射持仓"].append({"ticker": u.get("ticker"),
                                              "原因": u.get("reason") or u.get("原因") or ""})
    else:
        pending.append("持仓映射 未提供（--map），AI 按 04 §6 填写 S1–S5")

    base_cap = num(task_params.get("base_capital"))
    cap_n = g(min(B / base_cap, float(HARD_CAP_STRATEGIES))) if base_cap and base_cap > 0 else None
    if remain is None:
        pending.append("gap 资金匹配.剩余现金 缺失：顶层 调仓后总仓位/现金 与 资金汇总 相关项请 AI 手算并复核")

    pending += [
        "操作模式 %s%s；判定依据写 风控校验.大盘状态确认（01 §3.4）"
        % (a.mode or "正常", "" if a.mode else "（默认值，未显式提供 --mode）"),
        (("经济周期阶段 / 大盘趋势状态 / 轮动预测 由 AI 按 01 填写")
         if market == "美股" else
         "轮动阶段 / 大盘状态 / 轮动预测 由 AI 按 加密货币/01 填写") +
        ("" if a.cycle_stage else "（周期阶段未提供 --cycle-stage）"),
        "分析摘要.本轮净调仓金额 取 gap 净额（卖出释放−买入使用），口径请复核",
        "资金现状.策略资金(仅本次涉及策略) 由 AI 按 06 §7.3.1.1（cta_strategies_get_all）填写",
        "调仓指令 由 AI 按 05/10 §11.2 填写（目标持仓/分批计划/资金调整/意图备注/调仓相关参数/调仓理由）",
        "资金汇总: 策略初始资金合计/目标仓位总和/目标组合策略数/资金来源/保证金占用 由 AI 填写"
        "（初始资金 ≥ 目标资金×1.2，06 §7.3.1.4；数据源 = fund 策略槽位）",
        "落盘前必须运行 verify --report <本报告> --balance <最新> --available <最新>，"
        "按工具复算结论填写 风控校验 各项",
    ]
    if not a.analysis_time:
        pending.append("analysis_time 未提供，AI 填写（带时区）")
    if adj:
        pending.append("fund 参数自检有调整记录（%s），必须写入报告（10 §11.2 任务参数）" % "；".join(str(x) for x in adj))
    if truncated:
        pending.append("gap 截断名单: %s —— 压缩过程写入 执行建议（10 §11.2 规则 5）" % "、".join(str(x) for x in truncated))
    if n_open:
        pending.append("gap 显示 %d 个 开仓 名额：AI 填写 新增策略（初始资金 ≥ 目标资金×1.2）" % n_open)

    if market == "美股":
        summary_head = {"经济周期阶段": a.cycle_stage or "", "大盘趋势状态": ""}
    else:
        summary_head = {"轮动阶段": a.cycle_stage or "", "大盘状态": ""}
    report = {
        "market": market,
        "report_name": mk["report_name"],
        "analysis_time": a.analysis_time or "",
        "操作模式": a.mode or "正常",
        "任务参数": task_params,
        "分析摘要": {
            **summary_head,
            "轮动预测": {"宏观驱动": [], "领先板块": [], "滞后受益板块": [], "拐点信号": "",
                        "所处阶段": ""},
            "强势板块": strong_sec,
            "弱势板块": weak_sec,
            "强势子板块": strong_sub,
            "弱势子板块": weak_sub,
            "本轮净调仓金额": g(net_flow) if net_flow is not None else None,
            "调仓后总仓位_pct": pos_pct,
            "调仓后现金_pct": cash_pct,
        },
        "持仓映射": mapping,
        "资金现状": {
            "权益_balance(%s)" % ccy: g(balance),
            "可用现金(%s)" % ccy: g(available) if available is not None else None,
            "invest_mult": task_params.get("invest_mult"),
            "总预算_B(权益×invest_mult,%s)" % ccy: g(B),
            "基准币种": mk["base_ccy"],
            ("美元换算汇率" if market == "美股" else "换算汇率"): mk["fx_note"],
            "现金占比_pct": g(available / balance * 100.0) if available is not None else None,
            "策略资金(仅本次涉及策略)": [],
        },
        "目标分配": target_alloc,
        "调仓指令": [],
        "新增策略": [],
        "资金汇总": {
            ("总预算(权益×invest_mult)" if market == "美股"
             else "总预算(权益×invest_mult,%s)" % ccy): g(B),
            "策略初始资金合计": None,
            "目标仓位总和(初始资金÷1.2)": None,
            "目标组合策略数": None,
            "策略数上限(总预算÷基准资金)": cap_n,
            "卖出释放": g(sell_free) if sell_free is not None else None,
            "买入使用": g(buy_use) if buy_use is not None else None,
            "资金来源": "",
            "净额": g(net_flow) if net_flow is not None else None,
            "剩余现金": g(remain) if remain is not None else None,
            "保证金占用": None,
            "调仓后总仓位_pct": pos_pct,
        },
        "风控校验": {},
        "数据缺失与冲突": [],
        "待人工确认": pending,
        "失效代码": [],
        "执行建议": "骨架由 assemble 工具装配（数值字段自洽：顶层 % = $ ÷ B）；AI 填写文本字段 / 调仓指令 / 新增策略后，"
                    "必须 verify --report 复算并核对「待人工确认」逐项闭环",
    }
    if a.out:
        try:
            with open(a.out, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
        except OSError as e:
            fail("写文件失败: %s" % e)

    result = {"成功": True, "工具": "rebalance_tools assemble (v" + VERSION + ")", "版本": VERSION,
              "市场": market,
              "文档依据": "10_AI调仓输出模板.md §11.1/§11.2",
              "已存文件": a.out,
              "待人工确认": pending}
    if a.out:
        result["报告顶层键"] = list(report.keys())
        result["说明"] = "完整报告已写入 已存文件；stdout 不再含报告全文（v2.5.0，--out 时）"
    else:
        result["报告"] = report
    emit(result)


# =====================================================================
# leaders — 龙头确认（02 §4.2.5，仅 加密货币）
# =====================================================================

def run_leaders(argv):
    ap = argparse.ArgumentParser(
        prog="rebalance_tools leaders",
        description="龙头确认（加密货币/02 §4.2.5）：逐板块取「T2 首位」（无 T2 → 池内首位）的 20 日超额 vs 基准；"
                    "超额 < 0 → 该板块强制降为中性。属组合层手工步骤的工具化（score/score2 只做板块/分桶合成，"
                    "不含龙头确认），供 00 §4.2 标准执行顺序使用。仅支持 --market 加密货币。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    add_market_arg(ap)
    ap.add_argument("--prices", required=True,
                    help='每币价格序列 JSON：{ticker: {date: close}} 或 K线原始产物 '
                         '{ticker: {"closes": {date: close}}}（须含市场基准键，加密货币 = BTC）')
    ap.add_argument("--strong", default=None,
                    help="逗号分隔的强势板块清单（score 输出的「强势板块」）；给出 → 额外输出「确认后强势板块 / 降档板块」")
    ap.add_argument("--window", type=int, default=20, help="超额窗口（交易日；02 §4.2.5 = 20 日）")
    ap.add_argument("--pool", default=None, help="选币池；缺省 = 市场默认池（crypto_pool.json）")
    a = parsed(ap, argv, "leaders")
    market = resolve_market(a.market)
    mk = MARKET_PRESETS.get(market, MARKET_PRESETS["美股"])
    if market != "加密货币":
        fail("leaders 仅支持 --market 加密货币（02 §4.2.5 是币版龙头确认规则；美股组无对应条款）")
    benchmark = mk["benchmark"]
    a.pool = a.pool or default_pool_path(market)
    pool, perr = load_json_file(a.pool, "选币池")
    if perr or not isinstance(pool, dict):
        fail("选币池不可读: %s" % perr)
    cats = pool.get("categories") or {}
    if not cats:
        fail("选币池无 categories（02 §4.1 单一事实源）")
    data, derr = load_json_file(a.prices, "每币价格序列")
    if derr or not isinstance(data, dict):
        fail("价格序列不可读: %s" % derr)

    def closes_of(t):
        v = data.get(t) if t else None
        if not isinstance(v, dict):
            return None
        c = v.get("closes") if isinstance(v.get("closes"), dict) else v
        out = {}
        for k, x in c.items():
            fv = num(x)
            if fv and fv > 0:
                out[str(k)] = fv
        return out or None

    bc = closes_of(benchmark)
    if not bc or len(bc) < a.window + 1:
        fail("基准 %s 缺失或可用交易日 < %d（实得 %d）"
             % (benchmark, a.window + 1, len(bc) if bc else 0))
    bds = sorted(bc)
    bret = (bc[bds[-1]] / bc[bds[-a.window - 1]] - 1) * 100.0

    def ret_of(t):
        c = closes_of(t)
        if not c:
            return None
        ds = sorted(set(c) & set(bc))          # 与基准对齐，禁止跨时点混算（01 §3.1-4）
        if len(ds) < a.window + 1:
            return None
        return (c[ds[-1]] / c[ds[-a.window - 1]] - 1) * 100.0

    want = [s.strip() for s in (a.strong or "").split(",") if s.strip()]
    rows, missing, ok_sec, demoted, undecided = [], [], [], [], []
    for sec, tiers in cats.items():
        if not isinstance(tiers, dict):
            continue
        t2 = [x for x in (tiers.get("T2") or []) if x]
        t3 = [x for x in (tiers.get("T3") or []) if x]
        if t2:
            lead, how = t2[0], "T2 第 1 个"
        elif t3:
            lead, how = t3[0], "无 T2 -> 池内首位（T3 第 1 个）"
        else:
            lead, how = None, "池内无标的"
        r = ret_of(lead)
        if lead is None or r is None:
            missing.append("%s: 龙头无法判定（%s）-> 保守按中性处理（02 §4.2.5）" % (sec, how))
            rows.append({"板块": sec, "T2首位": lead, "取法": how, "20日收益_pct": None,
                         "基准20日收益_pct": g(bret), "20日超额_pct": None,
                         "通过": None, "建议": "无法判定 -> 保守按中性"})
            undecided.append(sec)
            continue
        ex = r - bret
        passed = ex >= -1e-9
        rows.append({"板块": sec, "T2首位": lead, "取法": how,
                     "20日收益_pct": g(r), "基准20日收益_pct": g(bret),
                     "20日超额_pct": g(ex), "通过": passed,
                     "建议": "保持（可进 Top 3-4 强势）" if passed else "强制降为中性（02 §4.2.5）"})
        if sec in want:
            (ok_sec if passed else demoted).append(sec)

    result = {
        "成功": True, "工具": "rebalance_tools leaders (v" + VERSION + ")", "版本": VERSION,
        "市场": market, "文档依据": "02_板块评分与排序.md §4.2.5",
        "基准": benchmark, "窗口": "%dD" % a.window,
        "口径": "个股 %d 日收益 − %s %d 日收益（两市日期求交，标 as_of；01 §3.1-4 禁跨时点混算）"
                % (a.window, benchmark, a.window),
        "明细": rows,
        "未通过板块": [r["板块"] for r in rows if r["通过"] is False],
        "无法判定板块": undecided,
        "数据缺失": missing,
        "落位提示": "本输出用于填 分析摘要.强势板块（写**降档后**清单）与 数据缺失与冲突 的手工留痕"
                    "（手工值 + 原因 + 条款 + score 原始清单差异，00 §4.6-4）；"
                    "audit 仅在传 --leaders 时对账（'强制降中性' ≠ '不适用'）。",
    }
    if want:
        result["输入强势板块（score 原始）"] = want
        result["确认后强势板块"] = ok_sec
        result["降档板块"] = demoted
    emit(result)


# =====================================================================
# 入口：子命令分派（v1 兼容：首个参数以 -- 开头 → fund）
# =====================================================================

def main():
    reconf = getattr(sys.stdout, "reconfigure", None)
    if callable(reconf):
        try:
            reconf(encoding="utf-8", errors="replace")
        except Exception:
            pass
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        if __doc__:
            sys.stdout.write(__doc__)
        print("子命令: " + "  ".join(SUBCOMMANDS))
        return
    if argv[0] not in SUBCOMMANDS:
        argv = ["fund"] + argv  # v1 兼容：python rebalance_tools.py --balance ...
    global _OUT_FILE
    rest = argv[1:]
    if argv[0] != "assemble":  # assemble 自带 --out（报告文件）；其余子命令全局 --out：全量写文件 + stdout 摘要
        for i, t in enumerate(rest):
            if t == "--out" and i + 1 < len(rest):
                _OUT_FILE = rest[i + 1]
                del rest[i:i + 2]
                break
            if t.startswith("--out="):
                _OUT_FILE = t[len("--out="):]
                rest.pop(i)
                break
    runner = {"fund": run_fund, "gap": run_gap, "verify": run_verify,
              "score": run_score, "map": run_map, "exec": run_exec,
              "cycle": run_cycle, "score2": run_score2, "assemble": run_assemble,
              "leaders": run_leaders}[argv[0]]
    try:
        runner(rest)  # 正常退出路径均已自行输出 JSON（emit/fail）
    except Exception:
        emit({"成功": False, "工具": "rebalance_tools %s" % argv[0], "版本": VERSION,
              "工具自报错": traceback.format_exc(limit=8),
              "说明": "工具失效：按 10_AI调仓输出模板.md「失效代码」铁律，调仓任务内不得修改本文件；"
                     "请记入报告「失效代码」（工具/子命令/命令行/错误摘要/影响环节/降级处理），"
                     "该环节降级为 AI 手工计算并交叉复核。"}, code=3)


if __name__ == "__main__":
    main()
