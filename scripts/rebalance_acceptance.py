# -*- coding: utf-8 -*-
"""rebalance_acceptance.py — 加密货币调仓报告「一键验收」（v1.1）

把第 4 道闸（verify）与第 5 道闸（rebalance_audit）串起来跑，输出汇总表 + 总判定。
供外部智能体对照验收（口径见 docs/调仓/加密货币/13_验收要点.md §2）。

用法：
  python rebalance_acceptance.py --dir <报告与中间产物目录> \
      --balance <最新USDT权益> --available <最新可用USDT> [--asof-date YYYY-MM-DD] \
      [--market 加密货币] [--skip-audit]

目录内约定文件名（缺哪个，对应门的相关项将判「无法校验」）：
  报告.json / fund.json / gap.json / score.json / score2.json / map.json / cycle.json
  crypto_prices_raw.json / live_prices.json / market_report.json / volume_min_unit.json

总判定：PASS（两门均 exit 0）/ FAIL（任一不通过）/ UNKNOWN（无「不通过」但有「无法校验」）
退出码：0 / 1 / 2
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "rebalance_tools.py")
AUDIT = os.path.join(HERE, "rebalance_audit.py")
PY = sys.executable or "python"

FILES = {
    "报告": "报告.json", "fund": "fund.json", "gap": "gap.json", "score": "score.json",
    "score2": "score2.json", "map": "map.json", "cycle": "cycle.json",
    "K线原始": "crypto_prices_raw.json", "实时价": "live_prices.json",
    "大盘报告": "market_report.json", "单位表": "volume_min_unit.json",
    "龙头确认": "leaders.json",
}


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    try:
        return r.returncode, json.loads(r.stdout), None
    except Exception as e:
        return r.returncode, None, "%s | stdout=%r stderr=%r" % (e, r.stdout[:200], r.stderr[:200])


def main():
    ap = argparse.ArgumentParser(prog="rebalance_acceptance",
                                 description="加密货币调仓报告一键验收（verify + audit）")
    ap.add_argument("--dir", required=True, help="含 报告.json 与中间产物的目录")
    ap.add_argument("--balance", required=True, help="最新 USDT 权益（独立交叉核对用）")
    ap.add_argument("--available", required=True, help="最新可用 USDT")
    ap.add_argument("--asof-date", default=None)
    ap.add_argument("--market", default="加密货币")
    ap.add_argument("--skip-audit", action="store_true")
    a = ap.parse_args()

    paths = {k: os.path.join(a.dir, v) for k, v in FILES.items()}
    present = {k: (os.path.exists(p)) for k, p in paths.items()}
    print("== 输入文件 ==")
    for k, p in paths.items():
        print("  %-8s %s" % (k, "OK  " + os.path.basename(p) if present[k] else "缺失 " + os.path.basename(p)))
    if not present["报告"]:
        print("\n总判定: UNKNOWN（缺 报告.json）")
        return 2

    rows, worst = [], 0

    def note(tag, code, d, err):
        nonlocal worst
        if d is None:
            print("  %s: 输出不可解析 -> %s" % (tag, err))
            worst = max(worst, 2)
            rows.append((tag, "无法解析", None, None, None))
            return
        concl = d.get("校验")
        nb, nna = d.get("不通过项数"), d.get("无法校验项数")
        nk = d.get("提示项数", 0) if tag == "audit" else d.get("不适用项数")
        rows.append((tag, concl, nb, nna, nk))
        if d.get("明细"):
            for i in d["明细"]:
                if i.get("状态") in ("不通过", "无法校验", "提示"):
                    print("    [%s] %s -> %s" % (i["状态"], i["项目"], str(i.get("说明"))[:150]))
        if code == 1:
            worst = max(worst, 1)
        elif code == 2:
            worst = max(worst, 2)

    print("\n== 门 1/2: verify（报告内部自洽 + 硬上限 + 闭环） ==")
    c1, d1, e1 = run([PY, "-X", "utf8", TOOLS, "verify", "--market", a.market,
                      "--report", paths["报告"], "--balance", a.balance, "--available", a.available])
    note("verify", c1, d1, e1)

    if not a.skip_audit:
        print("\n== 门 2/2: audit（报告 ↔ 上游来源 + 时效） ==")
        cmd = [PY, "-X", "utf8", AUDIT, "--market", a.market, "--report", paths["报告"]]
        opt = {"--prices": "K线原始", "--fund": "fund", "--gap": "gap", "--score": "score",
               "--score2": "score2", "--map": "map", "--cycle": "cycle", "--live": "实时价",
               "--market-report": "大盘报告", "--units": "单位表", "--leaders": "龙头确认"}
        for flag, key in opt.items():
            if present[key]:
                cmd += [flag, paths[key]]
        if a.asof_date:
            cmd += ["--asof-date", a.asof_date]
        c2, d2, e2 = run(cmd)
        note("audit", c2, d2, e2)

    print("\n== 汇总 ==")
    print("  %-8s %-10s %-8s %-10s %s" % ("门", "校验", "不通过", "无法校验", "提示/不适用"))
    for tag, concl, nb, nna, nk in rows:
        print("  %-8s %-10s %-8s %-10s %s" % (tag, concl, nb, nna, nk))
    verdict = "PASS" if worst == 0 else ("FAIL" if worst == 1 else "UNKNOWN")
    print("\n总判定: %s  （PASS 需两门均『通过』：0 不通过 且 0 无法校验；提示项须逐条人工确认）" % verdict)
    print("详见 docs/调仓/加密货币/13_验收要点.md")
    return worst


if __name__ == "__main__":
    sys.exit(main())
