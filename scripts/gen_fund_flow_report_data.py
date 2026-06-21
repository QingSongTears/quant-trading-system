"""
生成 fund-flow-report 数据(从今天 mini 回测结果固化到 JSON)
============================================================
一次性脚本: 把 mini_backtest_compare.py 的结果存到
  database/fund_flow_report_data.json
路由读这个 JSON,模板用 Jinja2 变量替换硬编码。

用法: python scripts/gen_fund_flow_report_data.py
"""
import sys
import json
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 复用 mini_backtest_compare 的 simulate_nav / compute_metrics
from scripts.mini_backtest_compare import (
    load_trade_dates,
    load_close_matrix,
    simulate_nav,
    compute_metrics,
)
from src.selection import SelectionPipeline
from src.db.sql_utils import read_sql
from src.config import get_config, get_db_url
from sqlalchemy import create_engine

OUT_PATH = PROJECT_ROOT / "database" / "fund_flow_report_data.json"


def main():
    config = get_config()
    engine = create_engine(get_db_url(config), echo=False)

    start = "2025-06-01"
    end = "2026-06-18"
    rebalance_every = 20
    top_n = 30

    print(f"回测区间: {start} ~ {end}, 调仓周期 {rebalance_every}d, 持仓 {top_n} 只")
    trade_dates = load_trade_dates(engine, start, end)
    print(f"交易日数: {len(trade_dates)}")

    print("加载 close 矩阵...")
    all_codes = read_sql(
        "SELECT DISTINCT code FROM daily_price "
        "WHERE trade_date >= :s AND trade_date <= :e",
        engine, {"s": start, "e": end},
    )["code"].tolist()
    close_matrix = load_close_matrix(engine, all_codes, trade_dates)
    print(f"  shape: {close_matrix.shape}")

    # 跑 4 个场景
    scenarios = [
        ("v6_only",     ["technical"],                    "v6 (纯信号)"),
        ("v6_ff_64",    ["technical", "fund_flow"],       "v6 + 资金面 (1:1)"),
        ("v6_all_3",    ["technical", "fund_flow"],        "v6 + 资金面 (平衡)"),
    ]

    results = []
    for sid, dims, name in scenarios:
        print(f"\n[{sid}] 跑 {name} ...")
        pipe = SelectionPipeline(dimensions=dims, verbose=False)
        nav, log = simulate_nav(
            pipe, trade_dates, rebalance_every, top_n, rebalance_every, close_matrix
        )
        m = compute_metrics(nav)
        # 还原成原来页面的字段(总收益、年化、夏普、回撤、胜率、交易、卡玛)
        # 总收益 = (终值 / 1.0) - 1 (nav_series 已归一化)
        total_return = (nav.iloc[-1] / nav.iloc[0] - 1) * 100
        annual_return = float(m["年化收益"].rstrip("%"))
        sharpe = float(m["夏普比率"])
        max_dd = float(m["最大回撤"].rstrip("%"))
        win_rate = float(m["日胜率"].rstrip("%"))
        trades = len(log)
        # 卡玛 = 年化收益 / abs(最大回撤)
        kama = round(annual_return / abs(max_dd), 2) if max_dd != 0 else 0

        results.append({
            "id": sid,
            "name": name,
            "total_return": round(total_return, 2),
            "annual_return": annual_return,
            "sharpe": sharpe,
            "max_drawdown": max_dd,
            "win_rate": win_rate,
            "trades": trades,
            "kama": kama,
        })
        pipe.clear_cache()

    # 找出夏普最高的作为"best"
    best = max(results, key=lambda r: r["sharpe"])

    # 计算关键指标
    baseline = next(r for r in results if r["id"] == "v6_only")
    sharpe_lift_pct = round((best["sharpe"] - baseline["sharpe"]) / max(abs(baseline["sharpe"]), 0.01) * 100, 1)
    annual_lift_x = round(best["annual_return"] / baseline["annual_return"], 1) if baseline["annual_return"] != 0 else None

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "period": {"start": start, "end": end, "rebalance_days": rebalance_every, "top_n": top_n},
        "scenarios": results,
        "best_id": best["id"],
        "metrics": {
            "sharpe_lift_pct": sharpe_lift_pct,
            "annual_lift_x": annual_lift_x,
            "max_dd_unchanged": best["max_drawdown"] == baseline["max_drawdown"],
        },
    }

    OUT_PATH.parent.mkdir(exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 已写入 {OUT_PATH.relative_to(PROJECT_ROOT)}")

    # 简短总结
    print(f"\n场景对比:")
    for r in results:
        marker = " 🏆" if r["id"] == best["id"] else ""
        print(f"  {r['name']:<25} 年化 {r['annual_return']:>6.2f}%  夏普 {r['sharpe']:>5.2f}  回撤 {r['max_drawdown']:>6.2f}%{marker}")


if __name__ == "__main__":
    main()