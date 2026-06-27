"""
回测报告 dataclass + ECharts 转换器
===================================

ADR-0009: BacktestReport dataclass 保留在 report.py (D2-A 决策)。
原 engine.py 内嵌的 dataclass 已迁移至此, 公开 API 路径不变:
  - from src.backtest.engine import BacktestReport (向后兼容)
  - from src.backtest.report import BacktestReport (直接导入)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import date
from typing import Any, Dict


@dataclass
class BacktestReport:
    """标准化回测报告"""
    strategy_name: str
    stock_code: str
    stock_name: str
    start_date: date
    end_date: date
    initial_capital: float
    final_equity: float
    total_return: float          # 总收益率(%)
    annual_return: float         # 年化收益率(%)
    sharpe_ratio: float          # 夏普比率
    max_drawdown: float          # 最大回撤(%)
    win_rate: float              # 胜率(%)
    profit_factor: float         # 盈亏比
    total_trades: int            # 总交易次数
    annual_volatility: float     # 年化波动率(%)
    calmar_ratio: float          # 卡玛比率

    # 基准对比
    benchmark_return: float = 0.0
    excess_return: float = 0.0

    # 序列化数据（供前端渲染图表）
    equity_curve: list[Dict] = None
    trades_detail: list[Dict] = None
    monthly_returns: dict[str, float] = None

    # 成本配置快照
    cost_config: Dict = None

    def __post_init__(self):
        if self.equity_curve is None:
            self.equity_curve = []
        if self.trades_detail is None:
            self.trades_detail = []
        if self.monthly_returns is None:
            self.monthly_returns = {}
        if self.cost_config is None:
            self.cost_config = {}

    def to_dict(self) -> dict:
        """转为字典，用于 JSON 序列化"""
        d = asdict(self)
        d["start_date"] = str(d["start_date"])
        d["end_date"] = str(d["end_date"])
        d["equity_curve"] = json.dumps(d["equity_curve"])
        d["trades_detail"] = json.dumps(d["trades_detail"])
        d["monthly_returns"] = json.dumps(d["monthly_returns"])
        d["cost_config"] = json.dumps(d["cost_config"])
        return d

    def to_db_dict(self, strategy_id: int) -> dict:
        """转为数据库存储格式（排除非DB字段）"""
        d = asdict(self)
        db_fields = {
            "stock_code", "start_date", "end_date", "initial_capital",
            "final_equity", "total_return", "annual_return", "sharpe_ratio",
            "max_drawdown", "win_rate", "profit_factor", "total_trades",
            "annual_volatility", "calmar_ratio", "benchmark_return",
            "excess_return", "equity_curve", "trades_detail",
            "monthly_returns", "cost_config"
        }
        result = {k: v for k, v in d.items() if k in db_fields}
        result["strategy_id"] = strategy_id
        result["stock_name"] = self.stock_name
        result["start_date"] = self.start_date
        result["end_date"] = self.end_date
        result["equity_curve"] = json.dumps(self.equity_curve) if self.equity_curve else "[]"
        result["trades_detail"] = json.dumps(self.trades_detail) if self.trades_detail else "[]"
        result["monthly_returns"] = json.dumps(self.monthly_returns) if self.monthly_returns else "{}"
        result["cost_config"] = json.dumps(self.cost_config) if self.cost_config else "{}"
        return result


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