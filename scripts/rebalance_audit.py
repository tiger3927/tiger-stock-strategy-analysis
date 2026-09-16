# -*- coding: utf-8 -*-
"""rebalance_audit.py — 调仓报告「源对账 + 时效」审计门（调仓报告第 5 道闸，v1.0）

定位：`verify` 只做**报告内部**数值自洽 + 硬上限；本工具做**报告 ↔ 上游来源**对账，
专治 verify 覆盖不到的一类事故（加密货币组 2026-09-15 复盘查出）：

  1) 时效漂移：K线/报告/快照 as_of 混用，滞后数据按当期口吻写入报告；
  2) 数字凭记忆写入：文本里的评分/涨幅/金额与工具输出不一致或无处可溯；
  3) 手工覆盖无回灌：改写了 score/score2 清单却不留"手工值"痕迹；
  4) 结构化引用漂移：持仓映射 / 资金汇总 与 map / fund / gap 输出不一致；
  5) 空报告空洞：0 指令时 执行建议 未写明「无调仓」依据。

用法（币版标准顺序：… → verify → **audit** → 交付）：

  python rebalance_audit.py --market 加密货币 --report 报告.json \
      --prices crypto_prices_raw.json --fund fund.json --gap gap.json \
      --score score.json --score2 score2.json --map map.json --cycle cycle.json \
      [--leaders leaders.json] [--asof-date 2026-09-15] [--live live_prices.json]

  --prices   K线原始数据 {ticker: {"closes": {date: close}}}（fetch_crypto_prices.py 产物），
             用于判定「行情末根日期」与报告时效声明；不给 → 时效项「无法校验」
  --live     实时价 {ticker: price}（get_tick / cta_strategies_get_all 口径），
             用于「末根收盘价 vs 实时价」偏差提示（> 阈值 → 提示项，不判不通过）
  --asof-date 报告取数日（默认取报告 analysis_time 的日期）

判定：`校验` = 通过 / 不通过 / 无法校验（exit 0 / 1 / 2）；
      「提示项」不计入不通过，但必须逐条人工确认（默认会列出未溯源数字清单）。

只读工具：不修改任何文件；不触碰策略与账户。
"""
import argparse
import json
import os
import re
import sys

VERSION = "1.1"
MARKETS = {"加密货币": "crypto", "crypto": "crypto", "美股": "us", "us_stocks": "us"}

# 阈值/常量白名单（文档条款数字：上限、缓冲、窗口、档位等），不参与"未溯源"指控
THRESHOLD_WHITELIST = {
    0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0,
    17.0, 18.0, 19.0, 20.0, 21.0, 22.0, 24.0, 25.0, 28.0, 30.0, 31.0, 35.0, 36.0, 40.0, 45.0,
    48.0, 50.0, 51.0, 55.0, 60.0, 62.0, 65.0, 66.0, 70.0, 72.0, 75.0, 80.0, 90.0, 100.0,
    0.1, 0.5, 1.2, 0.01, 0.05, 0.2, 0.3, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5,
    200.0, 300.0, 500.0, 1000.0, 2000.0,
}
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{4}\s*年|\d{1,2}\s*月\s*\d{1,2}\s*日")
NUM_RE = re.compile(r"-?\d+(?:\.\d+)?%?")
SCORE_CITE_RE = re.compile(r"score\s*[:：]?\s*(-?\d+(?:\.\d+)?)")
FRESH_KEYWORDS = ("滞后", "截至", "时效", "as_of", "as of", "末根", "as-of")
HANDMADE_KEYWORDS = ("手工", "人工改写", "手工值")
ASOF_RE = re.compile(r"(截至|as[_ \-]?of|数据截止|行情末根|末根日期|数据时点)[^。；\n]{0,24}\d{4}-\d{2}-\d{2}",
                     re.IGNORECASE)
SECTION_REF_RE = re.compile(r"(?:§\s*\d+(?:\.\d+)*)|(?:\d{2}\s*§\s*\d+(?:\.\d+)*)")
THOUSAND_RE = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")


def emit(obj, code=0):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    return code


def load_json(path, label):
    if not path:
        return None, "%s 未提供" % label
    if not os.path.exists(path):
        return None, "%s 文件不存在: %s" % (label, path)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except Exception as e:  # 含 PowerShell 重定向产生的 UTF-16/BOM 情况
        return None, "%s 读取失败（注意 Windows 下勿用 PowerShell > 重定向）: %s" % (label, e)


def num(v):
    try:
        if isinstance(v, bool) or v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def walk_numbers(obj, out):
    """递归收集 JSON 中的全部数值（含字符串里的数字）"""
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        out.add(round(float(obj), 4))
        return
    if isinstance(obj, str):
        for m in NUM_RE.findall(THOUSAND_RE.sub("", obj)):  # 78,592 → 78592
            v = num(m.rstrip("%"))
            if v is not None:
                out.add(round(v, 4))
        return
    if isinstance(obj, dict):
        for v in obj.values():
            walk_numbers(v, out)
        return
    if isinstance(obj, (list, tuple)):
        for v in obj:
            walk_numbers(v, out)


def collect_text(report):
    """报告中的自由文本（数字溯源扫描范围）"""
    chunks = []
    for key in ("分析摘要", "目标分配", "执行建议", "数据缺失与冲突", "风控校验", "待人工确认", "任务参数"):
        v = report.get(key)
        if isinstance(v, str):
            chunks.append(v)
        elif isinstance(v, (dict, list)):
            chunks.append(json.dumps(v, ensure_ascii=False))
    return "\n".join(chunks)


def last_bar_date(prices):
    dates = set()
    for v in (prices or {}).values():
        if isinstance(v, dict):
            for d in (v.get("closes") or {}):
                dates.add(str(d)[:10])
    return max(dates) if dates else None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rebalance_audit", description="调仓报告源对账 + 时效审计门")
    ap.add_argument("--market", default="加密货币")
    ap.add_argument("--report", required=True)
    ap.add_argument("--fund", default=None)
    ap.add_argument("--gap", default=None)
    ap.add_argument("--score", default=None)
    ap.add_argument("--score2", default=None)
    ap.add_argument("--map", default=None)
    ap.add_argument("--cycle", default=None)
    ap.add_argument("--prices", default=None)
    ap.add_argument("--live", default=None)
    ap.add_argument("--leaders", default=None,
                    help="龙头确认输出（rebalance_tools.py leaders 产物，02 §4.2.5）："
                         "用于核对报告的 分析摘要.强势板块 是否已按「龙头 20 日超额 <0 → 降中性」落位；"
                         "未提供 → 该项跳过（不加判定项）")
    ap.add_argument("--market-report", default=None,
                    help="大盘报告 JSON（cta_report_get 产物）：其中的数字视为已溯源，"
                         "用于减少「未溯源数字」误报")
    ap.add_argument("--units", default=None,
                    help="最小交易单位表 JSON（tests/fetch_volume_min_unit.py 产物）："
                         "{ticker: {\"volume_min_unit\": x}} 或 {ticker: x}；"
                         "用于校验「目标持仓 = 最小交易单位的整数倍」")
    ap.add_argument("--asof-date", default=None)
    ap.add_argument("--live-tol-pct", type=float, default=1.5,
                    help="末根收盘价 vs 实时价 偏差提示阈值（%%）")
    a = ap.parse_args(argv)

    report, err = load_json(a.report, "报告")
    if err or not isinstance(report, dict):
        return emit({"成功": True, "工具": "rebalance_audit (v%s)" % VERSION, "版本": VERSION,
                     "校验": "无法校验", "原因": "报告不可读或不是 JSON 对象: %s" % err,
                     "明细": []}, 2)

    items = []

    def add(name, status, note=""):
        items.append({"项目": name, "状态": status, "说明": note})

    # ---------- 0. 取数日 ----------
    at = str(report.get("analysis_time") or "")
    asof = a.asof_date or (at[:10] if re.match(r"\d{4}-\d{2}-\d{2}", at[:10]) else None)
    if asof is None:
        add("取数日（analysis_time/--asof-date）", "无法校验", "报告 analysis_time=%r 无法解析日期" % at)
        asof = ""

    # ---------- 1. 数据溯源块（第 18 字段，币版强制） ----------
    prov = report.get("数据溯源")
    prov_keys = ("行情末根日期", "账户快照时间", "大盘报告时间")
    if not isinstance(prov, dict):
        add("数据溯源块存在（币版第 18 字段）", "不通过",
            "缺 顶层 `数据溯源`（须含 %s）；无此块无法判定时效与来源" % "/".join(prov_keys))
        prov = {}
    else:
        miss = [k for k in prov_keys if not prov.get(k)]
        add("数据溯源块字段齐全", "通过" if not miss else "不通过",
            "齐全" if not miss else "缺: %s" % ", ".join(miss))

    # ---------- 2. 行情时效闸（K线末根 vs 取数日） ----------
    prices, perr = load_json(a.prices, "K线原始数据")
    bar_last = last_bar_date(prices) if isinstance(prices, dict) else None
    if bar_last is None:
        add("行情时效（K线末根日期）", "无法校验",
            "未提供 --prices：%s；报告自报 行情末根日期=%s" % (perr or "空", prov.get("行情末根日期")))
    else:
        try:
            import datetime as _dt
            d1 = _dt.date.fromisoformat(bar_last)
            d2 = _dt.date.fromisoformat(asof)
            lag = (d2 - d1).days
        except Exception:
            lag = None
        # 报告是否留痕了该末根日期 / 时效声明（须是"日期 + 时效"配对，防止
        # 因"滞后受益板块"这类词误判为已留痕）——先算，供"时效"项判定用
        blob = collect_text(report)
        has_claim = ((bar_last in json.dumps(report, ensure_ascii=False))
                     or bool(ASOF_RE.search(blob)))
        if lag is None:
            add("行情时效（K线末根日期）", "无法校验", "日期不可解析: 末根=%s 取数日=%s" % (bar_last, asof))
        elif lag <= 1:
            add("行情时效（K线末根日期 vs 取数日）", "通过",
                "末根=%s 取数日=%s 滞后 %s 日（≤1 日，可按当期口径表述）" % (bar_last, asof, lag))
        else:
            # 滞后 ≥ 2 日：网关日K固有滞后（12 §13.6）。**允许通过，但必须显式声明 as_of 并降级说明**；
            # 未声明才是不通过——强制"声明"而不是强制"换数据源"（K线只有这一条通道）。
            add("行情时效（K线末根日期 vs 取数日）", "通过" if has_claim else "不通过",
                "末根=%s 取数日=%s 滞后 %s 日；%s"
                % (bar_last, asof, lag,
                   "已声明 as_of 并降级说明（允许）" if has_claim else
                   "**未声明**：滞后 ≥2 日时必须在报告写明「截至 <末根日期>」并降级说明（00 §4.6-7 / 11 §12.8）"))
        add("行情时效留痕（报告须注明数据 as_of）", "通过" if has_claim else "不通过",
            "已留痕" if has_claim else
            "报告未出现「截至/as_of/末根 + 日期」配对（末根 %s）；违反 00 §4.6-7" % bar_last)
        if prov.get("行情末根日期") and str(prov.get("行情末根日期"))[:10] != bar_last:
            add("数据溯源.行情末根日期 与实测一致", "不通过",
                "报告自报 %s vs 实测 %s" % (prov.get("行情末根日期"), bar_last))
        else:
            add("数据溯源.行情末根日期 与实测一致", "通过" if prov.get("行情末根日期") else "无法校验",
                "一致" if prov.get("行情末根日期") else "报告未自报")

    # ---------- 3. 时效声明扫描（大盘报告 < 3 日，01 §3.1） ----------
    if prov.get("大盘报告时间"):
        blob = collect_text(report)
        if str(prov.get("大盘报告时间"))[:10] not in blob and not any(k in blob for k in FRESH_KEYWORDS):
            add("大盘报告时效留痕（< 3 日，01 §3.1）", "不通过",
                "自报大盘报告时间=%s，但报告正文未注明该时间/时效" % prov.get("大盘报告时间"))
        else:
            add("大盘报告时效留痕（< 3 日，01 §3.1）", "通过", "自报时间=%s 并已留痕" % prov.get("大盘报告时间"))
    else:
        add("大盘报告时效留痕（< 3 日，01 §3.1）", "无法校验", "数据溯源.大盘报告时间 缺失")

    # ---------- 4. 末根收盘 vs 实时价（偏差提示） ----------
    live, lerr = load_json(a.live, "实时价")
    dev_hits = []
    if isinstance(live, dict) and isinstance(prices, dict):
        for tk, px in live.items():
            p = num(px)
            if p is None:
                p = num((px or {}).get("price")) if isinstance(px, dict) else None
            closes = (prices.get(tk) or {}).get("closes") or {}
            if p is None or not closes:
                continue
            last_close = num(closes[max(closes)])
            if not last_close:
                continue
            dev = abs(last_close - p) / p * 100.0
            if dev > a.live_tol_pct:
                dev_hits.append("%s 末根 %s vs 实时 %s（%.2f%%）" % (tk, last_close, p, dev))
        add("末根收盘 vs 实时价 偏差 ≤ %.1f%%" % a.live_tol_pct,
            "通过" if not dev_hits else "提示",
            "无显著偏差" if not dev_hits else "; ".join(dev_hits))
    else:
        add("末根收盘 vs 实时价 偏差", "无法校验", "未提供 --live 或 --prices")

    # ---------- 5. 结构化引用对账：报告 ↔ score / score2 ----------
    score, serr = load_json(a.score, "score 输出")
    score2, s2err = load_json(a.score2, "score2 输出")
    tool_scores = {}
    if isinstance(score, dict):
        for it in (score.get("明细") or []):
            if isinstance(it, dict) and it.get("板块") is not None:
                v = num(it.get("评分"))
                if v is not None:
                    tool_scores[str(it["板块"])] = v

    bad_cited = []
    ta = report.get("目标分配") or {}
    if isinstance(ta, dict):
        for sec, cfg in ta.items():
            if not isinstance(cfg, dict):
                continue
            basis = str(cfg.get("依据") or "")
            m = SCORE_CITE_RE.search(basis)
            if not m:
                continue  # 未引用评分则跳过（无评分最小闭环 00 §4.4）
            cited = num(m.group(1))
            real = tool_scores.get(sec)
            if real is None:
                bad_cited.append("%s 引用了 score %s，但 score 输出无该板块" % (sec, cited))
            elif cited is None or abs(cited - real) > 0.05:
                bad_cited.append("%s 引用 score %s ≠ 工具 %s" % (sec, cited, real))
    add("目标分配 引用的板块评分 ↔ score 输出一致",
        "通过" if not bad_cited else "不通过",
        "无引用或全部一致" if not bad_cited else "; ".join(bad_cited))

    # 强/弱势清单：与工具清单比对，差异必须留「手工」痕迹
    diffs = []
    pairs = [("强势板块", score, "强势板块"), ("弱势板块", score, "弱势板块"),
             ("强势子板块", score2, "强势子板块"), ("弱势子板块", score2, "弱势子板块"),
             ("强势子维度", score2, "强势子板块"), ("弱势子维度", score2, "弱势子板块")]
    for rkey, tool, tkey in pairs:
        rv = (report.get("分析摘要") or {}).get(rkey)
        tv = tool.get(tkey) if isinstance(tool, dict) else None
        if not isinstance(rv, list) or not isinstance(tv, list):
            continue
        extra = sorted(set(rv) - set(tv))
        lost = sorted(set(tv) - set(rv))
        if extra or lost:
            diffs.append("%s: 报告多 %s / 少 %s" % (rkey, extra or "无", lost or "无"))
    conflict_txt = json.dumps(report.get("数据缺失与冲突") or [], ensure_ascii=False)
    handmade = any(k in conflict_txt for k in HANDMADE_KEYWORDS)
    if not diffs:
        add("强势/弱势清单 ↔ score/score2 输出一致", "通过", "无差异")
    else:
        add("强势/弱势清单 ↔ score/score2 输出一致（差异须留痕）",
            "通过" if handmade else "不通过",
            ("已留痕（数据缺失与冲突含手工说明）: " if handmade else "未留痕（00 §4.6-4 手工覆盖留痕）: ") + "; ".join(diffs))

    # ---------- 5b. 龙头确认 ↔ 报告强势板块（02 §4.2.5，v1.1 新增） ----------
    # 立门理由（2026-09-15 验收复盘）：龙头确认是组合层手工步骤，丢失后报告仍能 verify 全绿；
    # 必须在「报告 ↔ leaders 工具输出」这一侧对账，否则「漏降档」永远不会被发现。
    if a.leaders:
        lv, lverr = load_json(a.leaders, "leaders 输出")
        rep_strong = (report.get("分析摘要") or {}).get("强势板块")
        rep_strong = [str(x) for x in rep_strong] if isinstance(rep_strong, list) else []
        if not isinstance(lv, dict):
            add("龙头确认 ↔ 报告强势板块（02 §4.2.5）", "不通过",
                "leaders 输出不可读: %s" % lverr)
        elif "降档板块" not in lv and "确认后强势板块" not in lv:
            add("龙头确认 ↔ 报告强势板块（02 §4.2.5）", "不适用",
                "leaders 未带 --strong（未提供 降档板块/确认后强势板块）；"
                "请重跑 leaders 并传 --strong（= score 的强势板块清单）后再对账")
        else:
            demoted = [str(x) for x in (lv.get("降档板块") or [])]
            keep = [str(x) for x in (lv.get("确认后强势板块") or [])]
            bad_lv, info_lv = [], []
            still = sorted(set(demoted) & set(rep_strong))
            if still:
                bad_lv.append("未按 02 §4.2.5 降档（T2 首位 20 日超额 <0 却仍列强势）: %s" % still)
            if keep and not still:
                extra = sorted(set(rep_strong) - set(keep))
                lost = sorted(set(keep) - set(rep_strong))
                if extra or lost:
                    if handmade:
                        info_lv.append("报告与 确认后强势板块 的差异已留痕（允许）: 多 %s / 少 %s"
                                       % (extra or "无", lost or "无"))
                    else:
                        bad_lv.append("报告强势板块 ≠ leaders 确认后清单（且无手工留痕）: 多 %s / 少 %s"
                                      % (extra or "无", lost or "无"))
            note = "降档板块 %s；报告强势板块 %s" % (demoted or "无", rep_strong)
            if info_lv:
                note += "；" + "；".join(info_lv)
            add("龙头确认 ↔ 报告强势板块（02 §4.2.5）",
                "通过" if not bad_lv else "不通过",
                note if not bad_lv else "; ".join(bad_lv))

    # ---------- 6. 持仓映射 S1–S5 ↔ map 输出 ----------
    mp, merr = load_json(a.map, "map 输出")
    if isinstance(mp, dict) and isinstance(mp.get("明细"), list):
        map_layer = {}
        for it in mp["明细"]:
            if isinstance(it, dict) and it.get("策略名称"):
                map_layer[str(it["策略名称"])] = str(it.get("层级") or "")
        pm = report.get("持仓映射") or {}
        bad_layer = []
        for key, lst in (pm.items() if isinstance(pm, dict) else []):
            if key == "未映射持仓" or not isinstance(lst, list):
                continue
            for e in lst:
                if not isinstance(e, dict) or not e.get("策略名称"):
                    continue
                nm = str(e["策略名称"])
                exp = map_layer.get(nm)
                if exp is None:
                    bad_layer.append("%s 不在 map 明细中" % nm)
                elif not exp.startswith(str(key)[:2]):
                    bad_layer.append("%s 报告=%s / map=%s" % (nm, key, exp))
        # map 有、报告无 → 也报
        rep_names = set()
        for key, lst in (pm.items() if isinstance(pm, dict) else []):
            if key == "未映射持仓" or not isinstance(lst, list):
                continue
            for e in lst:
                if isinstance(e, dict) and e.get("策略名称"):
                    rep_names.add(str(e["策略名称"]))
        lost = sorted(set(map_layer) - rep_names)
        if lost:
            bad_layer.append("map 有但报告缺: %s" % lost)
        add("持仓映射 S1–S5 ↔ map 输出一致", "通过" if not bad_layer else "不通过",
            "一致" if not bad_layer else "; ".join(bad_layer))
    else:
        add("持仓映射 S1–S5 ↔ map 输出一致", "无法校验", "未提供 --map：%s" % (merr or "空"))

    # ---------- 7. 资金数值 ↔ fund / gap ----------
    fund, ferr = load_json(a.fund, "fund 输出")
    gap, gerr = load_json(a.gap, "gap 输出")
    fz = report.get("资金汇总") or {}
    bad_money = []
    if isinstance(fund, dict):
        slots = fund.get("策略槽位") or {}
        for rk, fk in (("策略初始资金合计", "初始资金合计"), ("目标组合策略数", "策略数")):
            r, f = num(fz.get(rk)), num(slots.get(fk))
            if r is not None and f is not None and abs(r - f) > 0.5:
                bad_money.append("%s 报告 %s ≠ fund %s" % (rk, r, f))
    init, tgt = num(fz.get("策略初始资金合计")), num(fz.get("目标仓位总和(初始资金÷1.2)"))
    if init is not None and tgt is not None and abs(tgt - init / 1.2) > 0.5:
        bad_money.append("目标仓位总和 %s ≠ 初始资金÷1.2 = %.2f" % (tgt, init / 1.2))
    if isinstance(gap, dict):
        gm = gap.get("资金匹配") or {}
        for rk, gk in (("净额", "净额"), ("剩余现金", "剩余现金")):
            r, g = num(fz.get(rk)), num(gm.get(gk))
            if r is not None and g is not None and abs(r - g) > 0.01:
                bad_money.append("%s 报告 %s ≠ gap %s" % (rk, r, g))
    if not isinstance(fund, dict) and not isinstance(gap, dict):
        add("资金汇总 ↔ fund/gap 对账", "无法校验",
            "未提供 --fund/--gap（%s / %s）" % (ferr or "空", gerr or "空"))
    else:
        add("资金汇总 ↔ fund/gap 对账", "通过" if not bad_money else "不通过",
            "一致" if not bad_money else "; ".join(bad_money))

    # ---------- 8. 空报告空洞检查（00 §4.1-1 / 11 §12.7-7.2） ----------
    instrs = report.get("调仓指令") or []
    news = report.get("新增策略") or []
    adv = str(report.get("执行建议") or "")
    if not instrs and not news:
        ok = ("无调仓" in adv or "不调仓" in adv) and len(adv) >= 80
        add("空报告（0 指令）执行建议依据", "通过" if ok else "不通过",
            "已写明无调仓及依据（%d 字）" % len(adv) if ok else "0 指令但 执行建议 未写明「无调仓/不调仓」或依据过短")
        tgt_list = report.get("目标分配") or {}
        add("空报告 目标分配 非空（须逐板块说明目标=当前的理由）",
            "通过" if isinstance(tgt_list, dict) and len(tgt_list) > 0 else "不通过",
            "%d 个板块条目" % (len(tgt_list) if isinstance(tgt_list, dict) else 0))
    else:
        add("空报告（0 指令）执行建议依据", "不适用", "本轮有指令，见 verify")

    # ---------- 8.5 目标持仓 = 最小交易单位（volume_min_unit）整数倍 ----------
    units, uerr = load_json(a.units, "最小交易单位表")
    umap = {}
    if isinstance(units, dict):
        for tk, v in units.items():
            u = v.get("volume_min_unit") if isinstance(v, dict) else v
            u = num(u)
            if u:
                umap[str(tk)] = u
    if not umap:
        add("目标持仓 = 最小交易单位整数倍", "无法校验",
            "未提供 --units（tests/fetch_volume_min_unit.py 产物）：%s" % (uerr or "空表"))
    else:
        bad_div = []
        for it in list(report.get("调仓指令") or []) + list(report.get("新增策略") or []):
            if not isinstance(it, dict):
                continue
            tk = str(it.get("ticker") or "")
            q = num(it.get("目标持仓"))
            if not tk or q is None or q <= 0:
                continue
            u = umap.get(tk)
            if u is None:
                bad_div.append("%s 不在最小交易单位表" % tk)
                continue
            k = q / u
            if abs(k - round(k)) > 1e-6:
                bad_div.append("%s 目标持仓 %s ÷ 单位 %s = %.6f（非整数倍 → 不可下单）" % (tk, q, u, k))
        add("目标持仓 = 最小交易单位整数倍", "通过" if not bad_div else "不通过",
            "全部为整数倍（单位来源：volume_min_unit 实测）" if not bad_div else "; ".join(bad_div))

    # ---------- 9. 数字溯源扫描（提示项，不判不通过） ----------
    pool = set()
    mrep, _ = load_json(a.market_report, "大盘报告")
    for obj in (score, score2, fund, gap, mp, mrep,
                report.get("资金现状"), report.get("资金汇总"),
                report.get("数据溯源"), report.get("任务参数")):
        walk_numbers(obj, pool)
    scan_text = collect_text(report)
    scan_text = THOUSAND_RE.sub("", scan_text)   # 76,890 → 76890
    scan_text = SECTION_REF_RE.sub(" ", scan_text)  # 剔除 §3.1 / 01 §4.6 类章节号
    unmatched = []
    for tok in NUM_RE.findall(scan_text):
        v = num(tok.rstrip("%"))
        if v is None:
            continue
        if abs(v) in THRESHOLD_WHITELIST:
            continue
        hit = any(abs(v - p) <= 0.02 or abs(abs(v) - abs(p)) <= 0.02 for p in pool)
        if not hit:
            unmatched.append(tok)
    unmatched = sorted(set(unmatched), key=lambda x: (len(x), x))
    add("文本数字可溯源（提示项，不计不通过）", "提示",
        "全部可溯" if not unmatched else "未在工具输出/白名单中命中（须逐条确认出处）: %s" % ", ".join(unmatched[:40]))

    statuses = [i["状态"] for i in items]
    n_bad = statuses.count("不通过")
    n_na = statuses.count("无法校验")
    n_hint = statuses.count("提示")
    if n_bad:
        concl, code = "不通过", 1
    elif n_na:
        concl, code = "无法校验", 2
    else:
        concl, code = "通过", 0

    return emit({
        "成功": True,
        "工具": "rebalance_audit (v%s)" % VERSION,
        "版本": VERSION,
        "市场": MARKETS.get(a.market, a.market),
        "取数日": asof or None,
        "行情末根日期": bar_last,
        "校验": concl,
        "不通过项数": n_bad,
        "无法校验项数": n_na,
        "提示项数": n_hint,
        "说明": "「校验=通过」= 0 不通过 且 0 无法校验；「提示项」不计入判定但必须逐条人工确认"
                "（未溯源数字 = 凭记忆写入的高危信号，00 §4.6-7）",
        "未溯源数字": unmatched,
        "明细": items,
    }, code)


if __name__ == "__main__":
    sys.exit(main())
