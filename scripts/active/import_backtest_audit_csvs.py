"""
import_backtest_audit_csvs.py
将 2026-06-21 audit 的 CSV 数据回填到 backtest_result + strategy_config 表
让 dashboard.html 的 /api/strategy/compare 等端点有真实数据可展示
"""
from __future__ import annotations
import sys
from pathlib import Path
from datetime import datetime
import csv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import load_strategies
from src.models.repository import DataRepository


def main():
    repo = DataRepository()
    audit_dir = ROOT / "output" / "audit_20260621"
    summary_csv = audit_dir / "backtest_strategy_summary.csv"
    detail_csv = audit_dir / "backtest_results_detail.csv"

    if not summary_csv.exists() or not detail_csv.exists():
        print(f"❌ 找不到 CSV: {summary_csv} 或 {detail_csv}")
        return 1

    print(f"📂 读取 {summary_csv.name} 和 {detail_csv.name}")

    # ===== 1. 注册策略 =====
    yaml_strategies = load_strategies().get("strategies", [])
    print(f"📋 YAML 策略: {len(yaml_strategies)} 个")

    name_to_id: dict[str, int] = {}
    with repo.get_session() as session:
        # 注册所有 YAML 策略
        for s in yaml_strategies:
            repo.save_strategy_config(
                session,
                name=s["name"],
                class_path=s["class_path"],
                params=str(s.get("params", {})),
                description=s.get("description"),
                source=s.get("source"),
            )
        session.commit()

        # 再查一次拿 id
        from src.models.database import StrategyConfig
        all_cfg = session.query(StrategyConfig).all()
        for c in all_cfg:
            name_to_id[c.name] = c.id
        print(f"✅ 策略配置: {len(name_to_id)} 条")

    # ===== 2. 导入回测结果 =====
    inserted = 0
    skipped = 0
    strategy_not_found: set[str] = set()
    with repo.get_session() as session, open(detail_csv, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            strategy_name = row["strategy"]
            strategy_id = name_to_id.get(strategy_name)
            if not strategy_id:
                # 模糊匹配: 取最相近
                for k in name_to_id:
                    if strategy_name in k or k in strategy_name:
                        strategy_id = name_to_id[k]
                        break
            if not strategy_id:
                strategy_not_found.add(strategy_name)
                skipped += 1
                continue

            try:
                result_data = {
                    "strategy_id": strategy_id,
                    "stock_code": row["stock_code"],
                    "stock_name": row.get("stock_name") or None,
                    "start_date": datetime.strptime(row["start_date"], "%Y-%m-%d").date(),
                    "end_date": datetime.strptime(row["end_date"], "%Y-%m-%d").date(),
                    "initial_capital": float(row["initial_capital"]),
                    "final_equity": float(row["final_equity"]),
                    "total_return": float(row["total_return_pct"]),
                    "annual_return": float(row["annual_return_pct"]) if row.get("annual_return_pct") else None,
                    "sharpe_ratio": float(row["sharpe"]) if row.get("sharpe") else None,
                    "max_drawdown": float(row["max_drawdown_pct"]) if row.get("max_drawdown_pct") else None,
                    "win_rate": float(row["win_rate_pct"]) if row.get("win_rate_pct") else None,
                    "total_trades": int(float(row["total_trades"])) if row.get("total_trades") else None,
                    "annual_volatility": float(row["annual_vol"]) if row.get("annual_vol") else None,
                    "calmar_ratio": float(row["calmar"]) if row.get("calmar") else None,
                    "benchmark_return": float(row["benchmark_return_pct"]) if row.get("benchmark_return_pct") else None,
                    "excess_return": float(row["excess_return_pct"]) if row.get("excess_return_pct") else None,
                    "equity_curve": None,
                    "trades_detail": None,
                    "monthly_returns": None,
                    "cost_config": None,
                    "created_at": datetime.now(),
                }
                repo.save_backtest_result(session, result_data)
                inserted += 1
            except Exception as e:
                print(f"⚠️ 跳过 {row.get('stock_code')}/{strategy_name}: {e}")
                skipped += 1
        session.commit()

    print(f"\n✅ 回测结果: 导入 {inserted} 条, 跳过 {skipped} 条")
    if strategy_not_found:
        print(f"⚠️ 未匹配策略: {sorted(strategy_not_found)}")

    # ===== 3. 验证 =====
    with repo.get_session() as session:
        from src.models.database import BacktestResult, StrategyConfig
        n_str = session.query(StrategyConfig).count()
        n_bt = session.query(BacktestResult).count()
        print(f"\n📊 DB 现状: {n_str} 策略 / {n_bt} 回测")
    return 0


if __name__ == "__main__":
    sys.exit(main())
