#!/usr/bin/env python3
"""
参数扫描 CLI — 入口脚本
=========================

用法:
    python scripts/param_scan.py grid --space config/scans/sector_cap.yaml \\
        --strategy SmallCapStrategy --start 2025-06-01 --end 2025-12-31

    python scripts/param_scan.py bayes --space config/scans/sector_cap.yaml \\
        --strategy SmallCapStrategy --start 2025-06-01 --end 2025-12-31 --n-trials 30

    python scripts/param_scan.py pareto --results output/scans/grid_xxx.json \\
        --objectives sharpe total_return --minimize max_drawdown
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def cmd_grid(args):
    from src.optimization import ParamSpace, GridSearcher, ScanStore, ParetoFront

    space = ParamSpace.from_yaml(args.space)
    n_combos = sum(1 for _ in space.grid_iter())
    print(f"[GRID] space: {args.space}  params: {space.names()}  combos: {n_combos}")
    if args.dry_run:
        print("[DRY] 仅展示空间, 不跑回测")
        for i, c in enumerate(space.grid_iter(), 1):
            print(f"  [{i:>3}] {c}")
        return

    gs = GridSearcher(
        space, strategy=args.strategy,
        start=args.start, end=args.end,
        scoring=args.scoring,
    )

    store = ScanStore(args.db) if args.db else None
    if store:
        sid = store.create_run(args.strategy, space.names()[0] if space.names() else "scan", args.start, args.end)
        print(f"[DB] scan_id: {sid}")

    results = gs.run(verbose=args.verbose)
    print(f"[GRID] 完成 {len(results)} trials")

    if store:
        for r in results:
            store.save_trial(sid, r)

    # 排序输出 Top-K
    top = gs.topk(results, k=args.top_k, by=args.scoring)
    print(f"\n[Top {args.top_k} by {args.scoring}]")
    for r in top:
        m = " | ".join(f"{k}={r.metrics.get(k, 0):+.3f}" for k in ("sharpe", "total_return", "max_drawdown"))
        print(f"  #{r.trial_id}: {r.params} -> {m}")

    # 帕累托
    pf = ParetoFront(results, objectives=["sharpe", "total_return"], minimize={"max_drawdown"})
    print("\n" + pf.summarize(top_n=3))

    if args.output:
        gs.export(results, args.output)
        print(f"\n[EXPORT] {args.output}")


def cmd_bayes(args):
    from src.optimization import ParamSpace, BayesianOpt, ScanStore

    space = ParamSpace.from_yaml(args.space)
    print(f"[BAYES] space: {args.space}  params: {space.names()}  trials: {args.n_trials}")

    opt = BayesianOpt(
        space, strategy=args.strategy,
        start=args.start, end=args.end,
        scoring=args.scoring, n_trials=args.n_trials,
        seed=args.seed,
    )
    best = opt.run(verbose=args.verbose)
    print(f"\n[BEST] #{best.trial_id} -> {args.scoring}={best.metrics.get(args.scoring, 0):.4f}")
    print(f"  params: {best.params}")
    m = " | ".join(f"{k}={best.metrics.get(k, 0):+.3f}" for k in ("sharpe", "total_return", "max_drawdown"))
    print(f"  metrics: {m}")

    history = opt.history()
    if args.output:
        opt.export(args.output)
        print(f"[EXPORT] {args.output}")

    store = ScanStore(args.db) if args.db else None
    if store:
        sid = store.create_run(args.strategy, args.space, args.start, args.end)
        for r in history:
            store.save_trial(sid, r)
        print(f"[DB] saved {len(history)} trials to {sid}")


def cmd_pareto(args):
    from src.optimization import ParetoFront
    data = json.loads(Path(args.results).read_text(encoding="utf-8"))
    # 兼容 GridSearcher.export (list) 和 BayesianOpt.export (dict)
    if isinstance(data, dict) and "trials" in data:
        results_raw = data["trials"]
    else:
        results_raw = data

    from src.optimization.runner import TrialResult
    results = [TrialResult(
        trial_id=r["trial_id"], params=r["params"], metrics=r["metrics"],
        elapsed_sec=r.get("elapsed_sec", 0), error=r.get("error"),
        timestamp=r.get("timestamp", ""),
    ) for r in results_raw]

    pf = ParetoFront(results, objectives=args.objectives, minimize=set(args.minimize or []))
    print(pf.summarize(top_n=args.top_k))


def cmd_list_runs(args):
    from src.optimization import ScanStore
    store = ScanStore(args.db) if args.db else ScanStore()
    runs = store.list_runs()
    print(f"[SCAN STORE] {len(runs)} runs in {args.db or ScanStore.DEFAULT_PATH}")
    for r in runs:
        print(f"  {r['created_at']} | {r['scan_id']}")


def main():
    parser = argparse.ArgumentParser(description="参数扫描 CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", help="ScanStore SQLite 路径")

    # grid
    p_grid = sub.add_parser("grid", help="网格搜索", parents=[common])
    p_grid.add_argument("--space", required=True)
    p_grid.add_argument("--strategy", required=True)
    p_grid.add_argument("--start", required=True)
    p_grid.add_argument("--end", required=True)
    p_grid.add_argument("--scoring", default="sharpe")
    p_grid.add_argument("--top-k", type=int, default=5)
    p_grid.add_argument("--output", help="导出 JSON 路径")
    p_grid.add_argument("--dry-run", action="store_true")
    p_grid.add_argument("--verbose", action="store_true")
    p_grid.set_defaults(func=cmd_grid)

    # bayes
    p_bayes = sub.add_parser("bayes", help="贝叶斯优化 (Optuna)")
    p_bayes.add_argument("--space", required=True)
    p_bayes.add_argument("--strategy", required=True)
    p_bayes.add_argument("--start", required=True)
    p_bayes.add_argument("--end", required=True)
    p_bayes.add_argument("--n-trials", type=int, default=30)
    p_bayes.add_argument("--seed", type=int, default=42)
    p_bayes.add_argument("--scoring", default="sharpe")
    p_bayes.add_argument("--output")
    p_bayes.add_argument("--verbose", action="store_true")
    p_bayes.add_argument("--db", help="ScanStore SQLite 路径")
    p_bayes.set_defaults(func=cmd_bayes)

    # pareto
    p_pf = sub.add_parser("pareto", help="从已有 results 算帕累托")
    p_pf.add_argument("--results", required=True)
    p_pf.add_argument("--objectives", nargs="+", default=["sharpe", "total_return"])
    p_pf.add_argument("--minimize", nargs="+", default=["max_drawdown"])
    p_pf.add_argument("--top-k", type=int, default=5)
    p_pf.set_defaults(func=cmd_pareto)

    # list-runs
    p_list = sub.add_parser("list-runs", help="列出 ScanStore 里的扫描", parents=[common])
    p_list.set_defaults(func=cmd_list_runs)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()