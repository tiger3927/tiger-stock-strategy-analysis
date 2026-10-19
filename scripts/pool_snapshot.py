# -*- coding: utf-8 -*-
"""
pool_snapshot.py — 选股池回放沙箱（P0-3，Task-代码取数前视能力.md）

子命令：
  as-of    把生产 stock_pool.json 复制成回放副本 + 按规则重写 meta 时间戳 + 落 pool_before.json
  restore  从 replay/backups/ 恢复生产 stock_pool.json（防回放互相覆盖）
  diff     对比两个池 JSON：新增/剔除/换手率（报告模板 换手率_pct 的数据源）

命名红线（契约）：
  - 回放副本绝不命名为 stock_pool.json、绝不落在 scripts/ 目录
    （refill/sync 默认读同目录 stock_pool.json，会把它当生产池）；
  - 默认输出目录 <skill_root>/replay/（可用 --out-dir 覆盖，如 tests/replay）。

时间戳重写规则（与 P1-3 降级策略联动）：
  --expire all（默认）：tier*_updated_at = as_of - 刷新周期 - 1 天
      → 在 as-of 时点"刚好已到期"，触发对应 tier 的审查流程；
      回放任务书若因历史成分快照缺失而降级（只用价格/流动性规则），
      应改用 --expire none 并在任务书中声明不触发审查。
  --expire T1,T2 / --expire none：按需触发。
"""
import argparse
import datetime
import glob
import json
import os
import shutil
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SKILL_ROOT = os.path.dirname(_SCRIPT_DIR)
PROD_POOL = os.path.join(_SCRIPT_DIR, "stock_pool.json")
DEFAULT_REPLAY_DIR = os.path.join(_SKILL_ROOT, "replay")

# 与 sync_stock_pool.py / 美股选股池管理.md §4.2 对齐的刷新周期
TIER_LIMITS = {"tier1_updated_at": 90, "tier2_updated_at": 30, "tier3_updated_at": 30}


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _tiers_of(data):
    out = {"T1": set(), "T2": set(), "T3": set()}
    for info in data.get("sub_sectors", {}).values():
        for tier in out:
            out[tier].update(info.get("tiers", {}).get(tier, []))
    return out


def cmd_as_of(args):
    as_of = datetime.datetime.strptime(args.as_of, "%Y-%m-%d").date()
    today = datetime.datetime.now(datetime.timezone.utc).date()
    if as_of > today:
        print(f"❌ --as-of 不能晚于今天（{today}）")
        sys.exit(1)
    if not os.path.exists(args.pool):
        print(f"❌ 生产池不存在: {args.pool}")
        sys.exit(1)

    os.makedirs(args.out_dir, exist_ok=True)
    data = _load(args.pool)

    # 1) 备份生产池（restore 的依据；只读生产，不修改）
    backups = os.path.join(args.out_dir, "backups")
    os.makedirs(backups, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backups, f"stock_pool_{ts}.json")
    shutil.copy2(args.pool, backup_path)

    # 2) pool_before.json（换手率基线，回放结束 diff 用）
    before_path = os.path.join(args.out_dir, f"pool_before_{args.as_of}.json")
    with open(before_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # 3) 回放副本 + 时间戳重写
    expire = set()
    if args.expire == "all":
        expire = set(TIER_LIMITS)
    elif args.expire != "none":
        want = {t.strip().upper() for t in args.expire.split(",") if t.strip()}
        expire = {f"tier{t[1:]}_updated_at" for t in want if t in ("T1", "T2", "T3")}

    meta = data.setdefault("meta", {})
    rewritten = {}
    for key, limit in TIER_LIMITS.items():
        if key in expire:
            new_d = as_of - datetime.timedelta(days=limit + 1)  # as-of 当天刚好到期
            rewritten[key] = {"from": meta.get(key), "to": new_d.isoformat()}
            meta[key] = new_d.isoformat()
        else:
            meta[key] = as_of.isoformat()  # 未到期（锚定 as-of）
    meta["replay_as_of"] = args.as_of

    copy_path = os.path.join(args.out_dir, f"pool_{args.as_of}.json")
    if os.path.abspath(copy_path) == os.path.abspath(PROD_POOL):
        print("❌ 命名红线：回放副本不得覆盖生产 stock_pool.json")
        sys.exit(1)
    with open(copy_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    tiers = _tiers_of(data)
    print("✅ 回放沙箱已创建")
    print(f"  回放副本:   {copy_path}")
    print(f"  基线副本:   {before_path}")
    print(f"  生产备份:   {backup_path}")
    print(f"  到期触发:   {', '.join(sorted(expire)) if expire else '（无，--expire none）'}")
    for k, v in rewritten.items():
        print(f"    {k}: {v['from']} → {v['to']}（as-of 当天刚好到期）")
    print(f"  池规模:     T1={len(tiers['T1'])} T2={len(tiers['T2'])} T3={len(tiers['T3'])}")
    print(f"  后续校验:   python sync_stock_pool.py --pool-file \"{copy_path}\" --as-of {args.as_of} --output json")


def cmd_restore(args):
    backups = os.path.join(args.out_dir, "backups")
    files = sorted(glob.glob(os.path.join(backups, "stock_pool_*.json")))
    if not files:
        print(f"❌ 无备份可恢复: {backups}/stock_pool_*.json")
        sys.exit(1)
    src = files[-1] if not args.backup else os.path.join(backups, args.backup)
    if not os.path.exists(src):
        print(f"❌ 指定备份不存在: {src}")
        sys.exit(1)
    shutil.copy2(src, PROD_POOL)
    print(f"✅ 已恢复生产池: {src} → {PROD_POOL}")


def cmd_diff(args):
    a, b = _load(args.a), _load(args.b)
    ta, tb = _tiers_of(a), _tiers_of(b)
    print(f"diff: {args.a}  →  {args.b}")
    total_add = total_rem = total_prev = 0
    for tier in ("T1", "T2", "T3"):
        added = sorted(tb[tier] - ta[tier])
        removed = sorted(ta[tier] - tb[tier])
        pct = round(len(removed) / max(1, len(ta[tier])) * 100, 1)
        total_add += len(added)
        total_rem += len(removed)
        total_prev += len(ta[tier])
        print(f"  {tier}: +{len(added)} / -{len(removed)}  换手率={pct}%")
        if added:
            print(f"    新增: {', '.join(added)}")
        if removed:
            print(f"    剔除: {', '.join(removed)}")
    print(f"  合计: +{total_add} / -{total_rem}  整体换手率="
          f"{round(total_rem / max(1, total_prev) * 100, 1)}%")


def main():
    ap = argparse.ArgumentParser(description="选股池回放沙箱（as-of 副本 / restore / diff）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("as-of", help="创建回放副本并重写 meta 时间戳")
    p1.add_argument("--as-of", required=True, metavar="YYYY-MM-DD")
    p1.add_argument("--pool", default=PROD_POOL, help="生产池路径（默认 scripts/stock_pool.json）")
    p1.add_argument("--out-dir", default=DEFAULT_REPLAY_DIR,
                    help="回放产物目录（默认 <skill>/replay；不得指向 scripts/）")
    p1.add_argument("--expire", default="all",
                    help="as-of 时点判定为已到期的 tier：all|none|T1,T2,T3 逗号列表（默认 all，触发审查流程）")
    p1.set_defaults(func=cmd_as_of)

    p2 = sub.add_parser("restore", help="从备份恢复生产 stock_pool.json")
    p2.add_argument("--out-dir", default=DEFAULT_REPLAY_DIR)
    p2.add_argument("--backup", default=None, help="备份文件名（缺省=最新）")
    p2.set_defaults(func=cmd_restore)

    p3 = sub.add_parser("diff", help="对比两个池 JSON（新增/剔除/换手率）")
    p3.add_argument("a", help="基线池（before）")
    p3.add_argument("b", help="对比池（after）")
    p3.set_defaults(func=cmd_diff)

    args = ap.parse_args()
    if getattr(args, "out_dir", None):
        # Windows 路径大小写不敏感但字符串比较敏感 → normcase（d: vs D: 曾致防护失效）
        out_abs = os.path.normcase(os.path.abspath(args.out_dir))
        scripts_abs = os.path.normcase(os.path.abspath(_SCRIPT_DIR))
        if out_abs == scripts_abs or out_abs.startswith(scripts_abs + os.sep):
            print("❌ 命名红线：--out-dir 不得位于 scripts/（生产目录）；用 <skill>/replay 或 tests/ 下目录")
            sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
