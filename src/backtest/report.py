"""
回测报告生成器
=============

将 BacktestReport 转换为 HTML 可消费的格式，
包含净值曲线、回撤曲线、指标数据等。
"""
from __future__ import annotations
import json
from typing import Any
from .engine import BacktestReport


def report_to_chart_data(report: BacktestReport) -> dict[str, Any]:
    """
    将回测报告转为前端 ECharts 所需的数据格式
    
    Returns:
        {
            "equity_curve": 净值曲线数据,
            "drawdown_curve": 回撤曲线数据,
            "annual_returns": 年度收益数据,
            "monthly_heatmap": 月度热力图数据,
            "metrics": 核心指标,
            "trades": 交易明细
        }
    """
    # 净值曲线
    equity_curve = json.loads(report.equity_curve) if isinstance(report.equity_curve, str) else report.equity_curve

    # 计算回撤曲线
    drawdown_curve = _calc_drawdown_curve(equity_curve)

    # 年度收益
    annual_returns = _calc_annual_returns(equity_curve)

    # 月度热力图
    monthly_heatmap = _build_monthly_heatmap(report.monthly_returns)

    return {
        "equity_curve": equity_curve,
        "drawdown_curve": drawdown_curve,
        "annual_returns": annual_returns,
        "monthly_heatmap": monthly_heatmap,
        "metrics": {
            "total_return": report.total_return,
            "annual_return": report.annual_return,
            "sharpe_ratio": report.sharpe_ratio,
            "max_drawdown": report.max_drawdown,
            "win_rate": report.win_rate,
            "profit_factor": report.profit_factor,
            "total_trades": report.total_trades,
            "annual_volatility": report.annual_volatility,
            "calmar_ratio": report.calmar_ratio,
            "benchmark_return": report.benchmark_return,
            "excess_return": report.excess_return,
        },
        "trades": json.loads(report.trades_detail) if isinstance(report.trades_detail, str) else report.trades_detail,
        "cost_config": json.loads(report.cost_config) if isinstance(report.cost_config, str) else report.cost_config,
    }


def _calc_drawdown_curve(equity_curve: list[Dict]) -> list[Dict]:
    """从净值曲线计算回撤曲线"""
    if not equity_curve:
        return []

    drawdowns = []
    peak = 0

    for point in equity_curve:
        equity = point["equity"]
        if equity > peak:
            peak = equity
        dd = (equity - peak) / peak * 100 if peak > 0 else 0
        drawdowns.append({
            "date": point["date"],
            "drawdown": round(dd, 2)
        })

    return drawdowns


def _calc_annual_returns(equity_curve: list[Dict]) -> list[Dict]:
    """计算年度收益率"""
    if not equity_curve:
        return []

    years = {}
    for point in equity_curve:
        year = point["date"][:4]
        if year not in years:
            years[year] = {"first": point["equity"], "last": point["equity"]}
        years[year]["last"] = point["equity"]

    result = []
    for year, data in sorted(years.items()):
        ret = (data["last"] / data["first"] - 1) * 100 if data["first"] > 0 else 0
        result.append({"year": year, "return": round(ret, 2)})

    return result


def _build_monthly_heatmap(monthly_returns) -> list[Dict]:
    """构建月度热力图数据"""
    if isinstance(monthly_returns, str):
        monthly_returns = json.loads(monthly_returns)

    if not monthly_returns:
        return []

    # 计算月度间收益率
    months = sorted(monthly_returns.keys())
    result = []
    for i in range(1, len(months)):
        prev_val = monthly_returns[months[i - 1]]
        curr_val = monthly_returns[months[i]]
        ret = (curr_val / prev_val - 1) * 100 if prev_val > 0 else 0
        parts = months[i].split("-")
        result.append({
            "year": parts[0],
            "month": parts[1],
            "return": round(ret, 2)
        })

    return result
