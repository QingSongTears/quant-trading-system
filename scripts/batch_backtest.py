"""
批量回测脚本 — Issue #36 (T15)

多股票 × 多策略批量回测，并行执行，结果排序导出。

用法:
    python scripts/batch_backtest.py \\
        --strategies ma_cross macd_signal rsi_reversal \\
        --codes 000001 000002 000003 \\
        --start 2020-01-01 --end 2025-06-01 \\
        --workers 6 --top 20 --sort sharpe_ratio \\
        --export output/batch_results.csv
"""
import sys, json, argparse, csv
from pathlib import Path
from datetime import date
from typing import List, Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.backtest.engine import BacktestEngine
from src.models.repository import DataRepository
from src.config import load_strategies


def load_strategy_classes(strategy_names: List[str]) -> List[type]:
    """从策略配置加载策略类"""
    config = load_strategies()
    classes = []
    for name in strategy_names:
        found = False
        for s in config.get("strategies", []):
            if s["name"] == name and s.get("strategy_type", "signal") == "signal":
                import importlib
                module_path, class_name = s["class_path"].rsplit(".", 1)
                module = importlib.import_module(module_path)
                cls = getattr(module, class_name)
                classes.append(cls)
                found = True
                break
        if not found:
            print(f"⚠️  未找到策略: {name}")
    return classes


def list_available_strategies():
    """列出所有可用策略"""
    config = load_strategies()
    print("\n可用策略列表:")
    print(f"  {'名称':<20} {'类型':<10} {'描述'}")
    print(f"  {'-'*60}")
    for s in config.get("strategies", []):
        if s.get("strategy_type", "signal") == "signal":
            print(f"  {s['name']:<20} {'signal':<10} {s.get('description', '')[:40]}")
        elif s.get("strategy_type") == "portfolio":
            print(f"  {s['name']:<20} {'portfolio':<10} {s.get('description', '')[:40]}")
    print(f"  {'-'*60}")


def get_stock_pool(
    repo: DataRepository,
    industry: Optional[str] = None,
    exclude_st: bool = True,
    min_mcap: Optional[float] = None,
    max_mcap: Optional[float] = None,
    max_stocks: Optional[int] = None,
) -> List[str]:
    """
    获取股票池，支持多种筛选条件

    Args:
        industry: 行业名称 (如 "银行", "计算机"), None=不限
        exclude_st: 是否剔除 ST
        min_mcap: 最小市值(亿)
        max_mcap: 最大市值(亿)
        max_stocks: 最多取多少只

    Returns:
        股票代码列表
    """
    df = repo.get_stock_list()
    if exclude_st:
        df = df[~df["name"].str.contains("ST|退市", na=False)]
    if industry:
        df = df[df.get("industry", "").str.contains(industry, na=False)]
    if min_mcap:
        df = df[df.get("mcap_yi", 999999) >= min_mcap]
    if max_mcap:
        df = df[df.get("mcap_yi", 0) <= max_mcap]
    codes = df["code"].tolist()
    if max_stocks:
        codes = codes[:max_stocks]
    return codes


def export_summary_csv(reports, output_path: str):
    """导出批量结果摘要为 CSV"""
    rows = []
    for r in reports:
        rows.append({
            "策略": r.strategy_name,
            "股票代码": r.stock_code,
            "股票名称": r.stock_name,
            "总收益率%": r.total_return,
            "年化收益率%": r.annual_return,
            "夏普比率": r.sharpe_ratio,
            "最大回撤%": r.max_drawdown,
            "胜率%": r.win_rate,
            "盈亏比": r.profit_factor,
            "交易次数": r.total_trades,
            "年化波动率%": r.annual_volatility,
            "卡玛比率": r.calmar_ratio,
            "基准收益%": r.benchmark_return,
            "超额收益%": r.excess_return,
        })
    df = pd.DataFrame(rows)
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"✅ 摘要已导出: {output_path} ({len(rows)} 条)")


def print_ranking(ranked, sort_by: str, top_n: int = 20):
    """打印排名结果"""
    print(f"\n{'='*80}")
    print(f"批量回测排名 (按 {sort_by} 排序, Top {top_n})")
    print(f"{'='*80}")
    print(f" {'排名':<5} {'策略':<16} {'股票':<8} {'名称':<10} "
          f"{'总收益%':<8} {'年化%':<8} {'夏普':<7} {'回撤%':<7} {'胜率%':<6} {'交易':<5}")
    print(f" {'-'*80}")
    for row in ranked[:top_n]:
        print(f" {row['rank']:<4}  {row['strategy']:<14} {row['stock']:<6} {row['stock_name']:<10} "
              f"{row['total_return']:<8.2f} {row['annual_return']:<8.2f} "
              f"{row['sharpe_ratio']:<7.2f} {row['max_drawdown']:<7.2f} "
              f"{row['win_rate']:<6.1f} {row['total_trades']:<5}")


def print_summary(reports):
    """打印批量回测汇总统计"""
    if not reports:
        print("⚠️  无回测结果")
        return
    total_returns = [r.total_return for r in reports]
    sharpes = [r.sharpe_ratio for r in reports]
    drawdowns = [r.max_drawdown for r in reports]
    print(f"\n📊 汇总统计 ({len(reports)} 个回测)")
    print(f"   总收益率: 平均={pd.Series(total_returns).mean():.2f}% "
          f"最高={max(total_returns):.2f}% 最低={min(total_returns):.2f}%")
    print(f"   夏普比率: 平均={pd.Series(sharpes).mean():.2f} "
          f"最高={max(sharpes):.2f} 最低={min(sharpes):.2f}")
    print(f"   最大回撤: 平均={pd.Series(drawdowns).mean():.2f}% "
          f"最低={min(drawdowns):.2f}%")


def main():
    parser = argparse.ArgumentParser(description="批量回测 — 多策略 × 多股票")
    parser.add_argument("--strategies", nargs="+", help="策略名称列表 (如 ma_cross)")
    parser.add_argument("--codes", nargs="+", help="股票代码列表")
    parser.add_argument("--start", default="2020-01-01", help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", default="2025-06-01", help="结束日期 YYYY-MM-DD")
    parser.add_argument("--capital", type=float, default=100000, help="初始资金")
    parser.add_argument("--workers", type=int, default=4, help="并行线程数")
    parser.add_argument("--sort", default="sharpe_ratio",
                        choices=["sharpe_ratio", "total_return", "annual_return",
                                 "max_drawdown", "calmar_ratio", "profit_factor"],
                        help="排序指标")
    parser.add_argument("--top", type=int, default=20, help="显示 Top N")
    parser.add_argument("--export", help="导出 CSV 路径 (如 output/batch_results.csv)")
    parser.add_argument("--industry", help="行业筛选 (如 银行)")
    parser.add_argument("--min-mcap", type=float, help="最小市值(亿)")
    parser.add_argument("--max-mcap", type=float, help="最大市值(亿)")
    parser.add_argument("--list-strategies", action="store_true", help="列出所有可用的策略")

    args = parser.parse_args()

    if args.list_strategies:
        list_available_strategies()
        return

    if not args.strategies or not args.codes:
        parser.print_help()
        print("\n❌ 请指定 --strategies 和 --codes，或用 --list-strategies 查看可用策略")
        sys.exit(1)

    # 加载策略
    strategy_classes = load_strategy_classes(args.strategies)
    if not strategy_classes:
        print("❌ 未找到任何有效策略")
        sys.exit(1)

    # 股票池
    codes = args.codes
    if args.industry or args.min_mcap or args.max_mcap:
        repo = DataRepository()
        codes = get_stock_pool(
            repo, industry=args.industry,
            min_mcap=args.min_mcap, max_mcap=args.max_mcap,
        )
        print(f"🔍 股票池筛选: {len(codes)} 只")

    # 运行回测
    engine = BacktestEngine()
    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end)

    print(f"\n🚀 批量回测开始: {len(strategy_classes)} 策略 × {len(codes)} 股票 "
          f"(workers={args.workers}, sort_by={args.sort})")
    print(f"   区间: {start_date} ~ {end_date} | 资金: {args.capital:,.0f}")

    reports = engine.run_batch(
        strategy_classes=strategy_classes,
        stock_codes=codes,
        start_date=start_date,
        end_date=end_date,
        initial_capital=args.capital,
        max_workers=args.workers,
        progress_callback=lambda d, t: print(f"   进度: {d}/{t}", end="\r"),
    )
    print()

    if not reports:
        print("❌ 所有回测均失败")
        sys.exit(1)

    # 排序排名
    ranked = engine.rank_batch_results(reports, sort_by=args.sort)
    print_ranking(ranked, args.sort, args.top)
    print_summary(reports)

    # 导出
    if args.export:
        export_summary_csv(reports, args.export)

    print(f"\n✅ 批量回测完成: {len(reports)}/{len(strategy_classes)*len(codes)} 成功")


if __name__ == "__main__":
    main()
