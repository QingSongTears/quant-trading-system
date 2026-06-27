#!/usr/bin/env python3
"""
Walk-Forward OOS 报告 → 数据库 seed 脚本
==========================================

将 docs/oos_*.json 历史报告灌入 walk_forward_run + walk_forward_window 表。
幂等:同一份 JSON 多次执行不会重复(按 strategy_name + train_months + test_months 去重)。

用法:
    python scripts/seed_walk_forward.py                  # seed 默认文件
    python scripts/seed_walk_forward.py --file path.json # 自定义文件
    python scripts/seed_walk_forward.py --reset          # 清表后再 seed
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_FILES = [
    PROJECT_ROOT / "docs" / "oos_smoke_report.json",
    PROJECT_ROOT / "docs" / "oos_real_baseline.json",
]


def compute_aggregates(windows: list[dict]) -> dict:
    """从 windows 列表计算 run 级汇总指标"""
    oas = [w.get("oos_sharpe", 0) or 0 for w in windows]
    rets = [w.get("oos_annual_return", 0) or 0 for w in windows]
    wrs = [w.get("oos_win_rate", 0) or 0 for w in windows]
    dds = [w.get("oos_max_drawdown", 0) or 0 for w in windows]
    mean_sharpe = statistics.mean(oas) if oas else 0.0
    std_sharpe = statistics.stdev(oas) if len(oas) >= 2 else 0.0
    worst_dd = min(dds) if dds else 0.0
    mean_ret = statistics.mean(rets) if rets else 0.0
    mean_wr = statistics.mean(wrs) if wrs else 0.0
    passes = (mean_sharpe >= 0.5) and (std_sharpe < 0.3) and (worst_dd >= -25.0)
    return {
        "n_windows": len(windows),
        "oos_sharpe_mean": mean_sharpe,
        "oos_sharpe_std": std_sharpe,
        "worst_max_drawdown": worst_dd,
        "avg_oos_annual_return": mean_ret,
        "avg_oos_win_rate": mean_wr,
        "passes_gate": 1 if passes else 0,
    }


def main():
    parser = argparse.ArgumentParser(description="Walk-Forward OOS seed")
    parser.add_argument("--file", type=Path, action="append",
                        help="JSON 报告文件 (可多次指定)")
    parser.add_argument("--reset", action="store_true",
                        help="清空 walk_forward_run 表后再 seed")
    args = parser.parse_args()

    files = args.file or DEFAULT_FILES
    files = [f for f in files if f.exists()]
    if not files:
        print(f"❌ 未找到 JSON 报告文件")
        return 1

    from src.models.repository import DataRepository
    from src.models.database import WalkForwardRun, WalkForwardWindow
    from sqlalchemy import text

    repo = DataRepository()
    # 确保表存在
    from src.models.database import Base
    Base.metadata.create_all(repo.engine)

    if args.reset:
        with repo.engine.connect() as conn:
            conn.execute(text("DELETE FROM walk_forward_window"))
            conn.execute(text("DELETE FROM walk_forward_run"))
            conn.commit()
        print("🧹 清空 walk_forward_run / walk_forward_window 表")

    total_seeded = 0
    for f in files:
        with open(f) as fp:
            data = json.load(fp)
        cfg = data.get("config", {})
        windows = data.get("windows", [])
        if not windows:
            print(f"  ⚠️ {f.name}: 无 windows,跳过")
            continue
        agg = compute_aggregates(windows)
        run_data = {
            "strategy_name": data.get("strategy_name", "Unknown"),
            "start_date": cfg.get("start_date"),
            "end_date": cfg.get("end_date"),
            "train_months": cfg.get("train_months", 18),
            "test_months": cfg.get("test_months", 6),
            "step_months": cfg.get("step_months"),
            "n_optimize_samples": 20,
            **agg,
        }
        run_id = repo.save_walk_forward_run(run_data, windows)
        total_seeded += 1
        print(f"  ✅ {f.name}: run_id={run_id} strategy={data['strategy_name']} "
              f"windows={agg['n_windows']} passes_gate={bool(agg['passes_gate'])}")

    print(f"\n📊 共 seed {total_seeded} 个 walk_forward run")

    # 显示汇总
    summary = repo.get_walk_forward_summary()
    print(f"   total_runs={summary['total_runs']} "
          f"total_windows={summary['total_windows']} "
          f"passed={summary['passed_runs']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())