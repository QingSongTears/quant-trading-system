#!/usr/bin/env python3
"""
Walk-Forward OOS 验证框架 — 实盘化门票
========================================

实现滚动窗口 OOS 验证，是 LIVE_TRADING_ROADMAP 阶段 1 的核心工具。

为什么需要 walk-forward?
  - in-sample 优化容易过拟合（V5/V6 都出现过 #54 #67 这种参数扫描）
  - walk-forward 把数据切成多个 [训练窗口 / 测试窗口] 段
  - 每段在训练窗口调参, 在测试窗口验证 OOS 表现
  - OOS 夏普稳定 + 参数敏感性低 = 真实可用的策略

用法:
    # V6 超卖反转在 2024-2026 上做 walk-forward
    python scripts/walk_forward.py \
        --strategy v6_reversal_selection \
        --start 2024-01-01 --end 2026-06-22 \
        --train-months 18 --test-months 6 \
        --output docs/oos_validation_report.md

    # V龙头 XGBoost v4
    python scripts/walk_forward.py \
        --strategy v_leader_main_surge \
        --start 2024-01-01 --end 2026-06-22 \
        --train-months 24 --test-months 6

⚠️ 状态：骨架阶段, _run_window / _optimize 逻辑待实现 (依赖 PortfolioBacktestEngine)

门禁标准 (LIVE_TRADING_ROADMAP 阶段 1):
  - OOS 夏普 mean > 0.5
  - OOS 夏普 std < 0.3
  - 参数 ±20% 微调后 OOS 夏普退化 < 30%
  - 95% 滚动窗口最大回撤 < 25%
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("walk_forward")


# ── 数据结构 ─────────────────────────────────────────


@dataclass
class WindowResult:
    """单个 walk-forward 窗口的结果"""
    window_id: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    best_params: dict
    in_sample_sharpe: float
    oos_sharpe: float
    oos_annual_return: float
    oos_max_drawdown: float
    oos_total_trades: int
    oos_win_rate: float


@dataclass
class WalkForwardReport:
    """walk-forward 总报告"""
    strategy_name: str
    config: dict
    windows: List[WindowResult] = field(default_factory=list)

    @property
    def oos_sharpe_mean(self) -> float:
        if not self.windows:
            return 0.0
        return sum(w.oos_sharpe for w in self.windows) / len(self.windows)

    @property
    def oos_sharpe_std(self) -> float:
        if len(self.windows) < 2:
            return 0.0
        mean = self.oos_sharpe_mean
        var = sum((w.oos_sharpe - mean) ** 2 for w in self.windows) / (len(self.windows) - 1)
        return var ** 0.5

    @property
    def worst_max_drawdown(self) -> float:
        if not self.windows:
            return 0.0
        return min(w.oos_max_drawdown for w in self.windows)

    def passes_gate(self) -> Tuple[bool, List[str]]:
        """
        阶段 1 门禁检查

        Returns:
            (passed, reasons)
        """
        reasons = []
        sharpe_mean = self.oos_sharpe_mean
        sharpe_std = self.oos_sharpe_std
        worst_dd = self.worst_max_drawdown

        if sharpe_mean < 0.5:
            reasons.append(
                f"❌ OOS 夏普 mean = {sharpe_mean:.2f} < 0.5"
            )
        else:
            reasons.append(
                f"✅ OOS 夏普 mean = {sharpe_mean:.2f} ≥ 0.5"
            )

        if sharpe_std >= 0.3:
            reasons.append(
                f"❌ OOS 夏普 std = {sharpe_std:.2f} ≥ 0.3 (不稳定)"
            )
        else:
            reasons.append(
                f"✅ OOS 夏普 std = {sharpe_std:.2f} < 0.3"
            )

        if worst_dd < -25.0:
            reasons.append(
                f"❌ 最差窗口最大回撤 = {worst_dd:.1f}% < -25%"
            )
        else:
            reasons.append(
                f"✅ 最差窗口最大回撤 = {worst_dd:.1f}% ≥ -25%"
            )

        return all(r.startswith("✅") for r in reasons), reasons


# ── 主类 ─────────────────────────────────────────


class WalkForwardValidator:
    """
    Walk-Forward 验证器

    把 [start, end] 切成多个 [train_months / test_months] 滚动窗口,
    每窗口调参 + OOS 评估。
    """

    def __init__(
        self,
        strategy_class,
        strategy_params: Dict[str, Any],
        param_grid: Dict[str, List[Any]],
        start_date: date,
        end_date: date,
        train_months: int = 18,
        test_months: int = 6,
        step_months: Optional[int] = None,
        initial_capital: float = 1_000_000,
        n_optimize_samples: int = 20,
    ):
        self.strategy_class = strategy_class
        self.strategy_params = strategy_params
        self.param_grid = param_grid
        self.start_date = start_date
        self.end_date = end_date
        self.train_months = train_months
        self.test_months = test_months
        self.step_months = step_months or test_months  # 默认步长=测试窗口
        self.initial_capital = initial_capital
        self.n_optimize_samples = n_optimize_samples

        self.windows: List[WindowResult] = []



        self.windows: List[WindowResult] = []

    # ================================================================
    #  窗口生成
    # ================================================================

    def _generate_windows(self) -> List[Tuple[date, date, date, date]]:
        """
        生成滚动窗口列表 [(train_start, train_end, test_start, test_end), ...]

        默认步长 = test_months（不重叠），如需重叠可设置 step_months
        """
        windows = []
        cursor = self.start_date

        while True:
            train_start = cursor
            train_end = self._add_months(train_start, self.train_months)
            test_start = train_end
            test_end = self._add_months(test_start, self.test_months)

            if test_end > self.end_date:
                break

            windows.append((train_start, train_end, test_start, test_end))
            cursor = self._add_months(cursor, self.step_months)

        log.info(f"生成 {len(windows)} 个 walk-forward 窗口")
        return windows

    @staticmethod
    def _add_months(d: date, months: int) -> date:
        """日期 + N 月"""
        month = d.month - 1 + months
        year = d.year + month // 12
        month = month % 12 + 1
        day = min(d.day, 28)  # 简化处理, 避免月底溢出
        return date(year, month, day)

    # ================================================================
    #  窗口运行 (待实现)
    # ================================================================

    def _run_window(
        self,
        window_id: int,
        train_start: date, train_end: date,
        test_start: date, test_end: date,
    ) -> WindowResult:
        """
        单窗口 walk-forward:
          1. 在 [train_start, train_end] 上做参数搜索
          2. 取最优参数
          3. 在 [test_start, test_end] 上做 OOS 回测
          4. 返回 OOS 指标
        """
        # TODO: 实现
        # Step 1: 参数搜索
        best_params, in_sample_sharpe = self._optimize(
            train_start, train_end,
        )
        # Step 2: OOS 回测
        oos_metrics = self._backtest(
            test_start, test_end, best_params,
        )
        return WindowResult(
            window_id=window_id,
            train_start=str(train_start),
            train_end=str(train_end),
            test_start=str(test_start),
            test_end=str(test_end),
            best_params=best_params,
            in_sample_sharpe=in_sample_sharpe,
            oos_sharpe=oos_metrics["sharpe"],
            oos_annual_return=oos_metrics["annual_return"],
            oos_max_drawdown=oos_metrics["max_drawdown"],
            oos_total_trades=oos_metrics["total_trades"],
            oos_win_rate=oos_metrics["win_rate"],
        )

    def _optimize(
        self, train_start: date, train_end: date,
    ) -> Tuple[Dict[str, Any], float]:
        """
        训练窗口内参数搜索, 返回 (best_params, best_in_sample_sharpe)

        算法: 随机搜索（覆盖主要 V6 参数）
          - MAX_RSI_14 ∈ [30, 35, 38, 42]
          - MAX_RSI_6  ∈ [20, 23, 26]
          - MAX_BB_POSITION ∈ [0.06, 0.08, 0.10, 0.12]
          - MAX_DRAWDOWN_60D ∈ [-20, -15, -10, -8]
        默认 n_samples=20 (随机组合)
        """
        from src.backtest.portfolio_engine import PortfolioBacktestEngine
        # 默认参数搜索空间 (V6 优化范围, 来自 #54 参数扫描)
        param_space = self.param_grid or {
            "MAX_RSI_14":        [30.0, 35.0, 38.0, 42.0],
            "MAX_RSI_6":         [20.0, 23.0, 26.0],
            "MAX_BB_POSITION":   [0.06, 0.08, 0.10, 0.12],
            "MAX_DRAWDOWN_60D":  [-20.0, -15.0, -10.0, -8.0],
        }
        n_samples = self.n_optimize_samples

        import random
        rng = random.Random(42)
        samples = []
        keys = list(param_space.keys())
        for _ in range(n_samples):
            sample = {k: rng.choice(param_space[k]) for k in keys}
            samples.append(sample)

        engine = PortfolioBacktestEngine()
        best_params: Dict[str, Any] = {}
        best_sharpe = float("-inf")

        for i, params in enumerate(samples):
            try:
                strategy = self.strategy_class(
                    **{**self.strategy_params, **params}
                )
                report = engine.run(
                    strategy=strategy,
                    start_date=train_start,
                    end_date=train_end,
                    initial_capital=self.initial_capital,
                )
                sharpe = report.sharpe_ratio or 0.0
                if sharpe > best_sharpe:
                    best_sharpe = sharpe
                    best_params = params
                log.debug(
                    f"  [optimize {i+1}/{n_samples}] "
                    f"params={params} sharpe={sharpe:.2f}"
                )
            except Exception as e:
                log.debug(f"  [optimize {i+1}/{n_samples}] failed: {e}")
                continue

        if not best_params:
            log.warning(
                f"[optimize] 训练窗口 {train_start}~{train_end} 无有效结果, "
                f"使用默认参数"
            )
            best_params = {k: param_space[k][0] for k in keys}

        log.info(
            f"[optimize] {train_start}~{train_end}: "
            f"best_sharpe={best_sharpe:.2f}, params={best_params}"
        )
        return best_params, best_sharpe

    def _backtest(
        self, test_start: date, test_end: date, params: dict,
    ) -> Dict[str, float]:
        """
        OOS 测试窗口回测, 返回指标 dict

        复用 PortfolioBacktestEngine
        """
        from src.backtest.portfolio_engine import PortfolioBacktestEngine

        strategy = self.strategy_class(
            **{**self.strategy_params, **params}
        )
        engine = PortfolioBacktestEngine()
        report = engine.run(
            strategy=strategy,
            start_date=test_start,
            end_date=test_end,
            initial_capital=self.initial_capital,
        )
        return {
            "sharpe":         report.sharpe_ratio or 0.0,
            "annual_return":  report.annual_return or 0.0,
            "max_drawdown":   report.max_drawdown or 0.0,
            "total_return":   report.total_return or 0.0,
            "total_trades":   report.total_trades or 0,
            "win_rate":       report.win_rate or 0.0,
            "profit_factor":  report.profit_factor or 0.0,
        }

    # ================================================================
    #  主入口
    # ================================================================

    def run(self) -> WalkForwardReport:
        """执行 walk-forward 验证"""
        windows = self._generate_windows()
        if not windows:
            raise ValueError(
                f"[{self.start_date}, {self.end_date}] 范围内无法生成任何窗口"
            )

        results = []
        for wid, (tr_s, tr_e, te_s, te_e) in enumerate(windows):
            log.info(
                f"[Window {wid}] train={tr_s}~{tr_e}, test={te_s}~{te_e}"
            )
            try:
                result = self._run_window(wid, tr_s, tr_e, te_s, te_e)
                results.append(result)
                log.info(
                    f"  OOS sharpe={result.oos_sharpe:.2f}, "
                    f"max_dd={result.oos_max_drawdown:.1f}%"
                )
            except NotImplementedError as e:
                log.error(f"  实现未完成: {e}")
                raise

        report = WalkForwardReport(
            strategy_name=self.strategy_class.__name__,
            config={
                "train_months": self.train_months,
                "test_months": self.test_months,
                "step_months": self.step_months,
                "start_date": str(self.start_date),
                "end_date": str(self.end_date),
            },
            windows=results,
        )
        return report

    def save_report(
        self, report: WalkForwardReport, output_path: Path,
    ) -> None:
        """保存报告 (JSON + Markdown)"""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # JSON
        json_path = output_path.with_suffix(".json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(asdict(report), f, ensure_ascii=False, indent=2)

        # Markdown
        md_path = output_path.with_suffix(".md")
        passed, reasons = report.passes_gate()
        gate_emoji = "✅" if passed else "❌"

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# Walk-Forward OOS 报告 — {report.strategy_name}\n\n")
            f.write(f"**门禁**: {gate_emoji} {'通过' if passed else '未通过'}\n\n")
            f.write(f"## 配置\n\n```yaml\n")
            f.write(yaml_dump(report.config))
            f.write(f"```\n\n")
            f.write(f"## 汇总\n\n")
            f.write(f"- OOS 夏普 mean: **{report.oos_sharpe_mean:.2f}**\n")
            f.write(f"- OOS 夏普 std: **{report.oos_sharpe_std:.2f}**\n")
            f.write(f"- 最差窗口最大回撤: **{report.worst_max_drawdown:.1f}%**\n\n")
            f.write(f"## 门禁检查\n\n")
            for r in reasons:
                f.write(f"- {r}\n")
            f.write(f"\n## 各窗口明细\n\n")
            f.write(f"| 窗口 | 训练段 | 测试段 | IS 夏普 | OOS 夏普 | OOS 年化 | OOS 回撤 |\n")
            f.write(f"|------|--------|--------|---------|----------|----------|----------|\n")
            for w in report.windows:
                f.write(
                    f"| #{w.window_id} | {w.train_start}~{w.train_end} | "
                    f"{w.test_start}~{w.test_end} | "
                    f"{w.in_sample_sharpe:.2f} | {w.oos_sharpe:.2f} | "
                    f"{w.oos_annual_return:.1f}% | {w.oos_max_drawdown:.1f}% |\n"
                )

        log.info(f"报告已保存: {md_path}, {json_path}")


def yaml_dump(obj: dict) -> str:
    """极简 yaml dump, 避免外部依赖"""
    lines = []
    for k, v in obj.items():
        lines.append(f"{k}: {v}")
    return "\n".join(lines)


# ── 合成数据生成 (smoke test 用) ─────────────────


def generate_synthetic_data(
    n_stocks: int = 50,
    days: int = 504,        # 约 2 年交易日
    start_date: date = None,
    db_path: Path = None,
    seed: int = 42,
) -> None:
    """
    用几何布朗运动 (GBM) 生成合成 A 股行情, 灌入 quant.db 用于 pipeline 测试。

    ⚠️ 警告: 会向真实 quant.db 写入数据, 仅用于空 DB 或 smoke test。
    真实数据请用 `python run.py --download`。

    Args:
        n_stocks: 股票数量
        days: 交易日数量
        start_date: 数据起始日期 (默认 2023-01-01)
        db_path: SQLite 路径 (默认 PROJECT_ROOT/database/quant.db)
        seed: 随机种子
    """
    import sqlite3
    import numpy as np

    if start_date is None:
        start_date = date(2023, 1, 1)
    if db_path is None:
        db_path = PROJECT_ROOT / "database" / "quant.db"

    rng = np.random.default_rng(seed)

    # 生成每个股票的参数: 起始价 + 漂移率 + 波动率
    # 故意让 30% 股票是"强势股" (高漂移高波动), 70% 是普通股
    n_strong = int(n_stocks * 0.3)
    drift = np.concatenate([
        rng.normal(0.002, 0.001, n_strong),       # 强势: +0.2%/日
        rng.normal(-0.0002, 0.0005, n_stocks - n_strong),  # 普通: -0.02%/日
    ])
    vol = np.concatenate([
        rng.uniform(0.025, 0.040, n_strong),       # 强势: 高波动
        rng.uniform(0.015, 0.025, n_stocks - n_strong),
    ])
    start_prices = rng.uniform(5.0, 50.0, n_stocks)

    # 生成交易日序列 (跳过周末)
    from datetime import timedelta
    dates: list = []
    d = start_date
    while len(dates) < days:
        if d.weekday() < 5:  # 周一到周五
            dates.append(d)
        d += timedelta(days=1)

    # 生成价格矩阵
    log_returns = rng.normal(
        drift[:, None],
        vol[:, None],
        size=(n_stocks, len(dates)),
    )
    log_prices = np.log(start_prices)[:, None] + np.cumsum(log_returns, axis=1)
    prices = np.exp(log_prices)

    # 生成 OHLCV
    log.info(f"生成合成数据: {n_stocks} 股票 × {len(dates)} 交易日")

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # 创建 stock_basic + daily_price 表 (若不存在)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stock_basic (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            market TEXT NOT NULL,
            list_date DATE,
            delist_date DATE,
            industry TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_price (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            trade_date DATE NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume INTEGER NOT NULL,
            amount REAL,
            pct_change REAL,
            turnover REAL,
            UNIQUE(code, trade_date)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS benchmark_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_code TEXT NOT NULL,
            trade_date DATE NOT NULL,
            close REAL NOT NULL,
            pct_change REAL,
            UNIQUE(index_code, trade_date)
        )
    """)
    # 清空旧数据
    cur.execute("DELETE FROM daily_price")
    cur.execute("DELETE FROM stock_basic")
    cur.execute("DELETE FROM benchmark_data")

    # 插入股票
    for i in range(n_stocks):
        code = f"{600000 + i:06d}"
        cur.execute(
            "INSERT OR REPLACE INTO stock_basic (code, name, market) "
            "VALUES (?, ?, ?)",
            (code, f"测试股{i:03d}", "SH"),
        )

    # 插入价格 + 沪深 300 基准
    rows: list = []
    bench_rows: list = []
    for t, dt in enumerate(dates):
        # 基准 (沪深 300 模拟)
        bench_close = 4000 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, len(dates)))[t])
        bench_rows.append(("sh000300", dt.strftime("%Y-%m-%d"), bench_close, 0.0))

        for i in range(n_stocks):
            close = float(prices[i, t])
            open_ = float(prices[i, max(0, t - 1)]) if t > 0 else close
            high = close * (1 + abs(rng.normal(0, 0.005)))
            low = close * (1 - abs(rng.normal(0, 0.005)))
            volume = int(rng.uniform(5e7, 2e8))  # 提高到 5000万-2亿股, 满足日均成交额 > 3000万
            amount = close * volume
            pct = (close - open_) / open_ * 100 if open_ > 0 else 0.0
            rows.append((
                f"{600000 + i:06d}",
                dt.strftime("%Y-%m-%d"),
                round(open_, 2),
                round(high, 2),
                round(low, 2),
                round(close, 2),
                volume,
                round(amount, 2),
                round(pct, 2),
                3.0,  # turnover 3% (满足大多数 A 股换手率)
            ))

    cur.executemany(
        "INSERT INTO daily_price (code, trade_date, open, high, low, close, "
        "volume, amount, pct_change, turnover) VALUES (?, ?, ?, ?, ?, ?, "
        "?, ?, ?, ?)",
        rows,
    )
    cur.executemany(
        "INSERT INTO benchmark_data (index_code, trade_date, close, pct_change) "
        "VALUES (?, ?, ?, ?)",
        bench_rows,
    )
    conn.commit()
    conn.close()
    log.info(f"合成数据已写入: {db_path} ({n_stocks} 股票, {len(dates)} 交易日)")


# ── CLI ─────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Walk-Forward OOS 验证框架",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--strategy", required=True,
        help="策略类路径, 如 v6_reversal_selection.V6ReversalSelectionStrategy",
    )
    parser.add_argument(
        "--start", required=True,
        help="起始日期 YYYY-MM-DD",
    )
    parser.add_argument(
        "--end", required=True,
        help="结束日期 YYYY-MM-DD",
    )
    parser.add_argument(
        "--train-months", type=int, default=18,
        help="训练窗口月数 (默认 18)",
    )
    parser.add_argument(
        "--test-months", type=int, default=6,
        help="测试窗口月数 (默认 6)",
    )
    parser.add_argument(
        "--step-months", type=int, default=None,
        help="滚动步长月数 (默认 = test-months)",
    )
    parser.add_argument(
        "--n-optimize-samples", type=int, default=20,
        help="每窗口随机参数采样数 (默认 20)",
    )
    parser.add_argument(
        "--output", default="docs/oos_validation_report.md",
        help="报告输出路径",
    )
    parser.add_argument(
        "--smoke", action="store_true",
        help="smoke test: 生成合成数据 + 跑 walk_forward",
    )
    parser.add_argument(
        "--smoke-stocks", type=int, default=50,
        help="smoke test 合成股票数 (默认 50)",
    )
    parser.add_argument(
        "--smoke-days", type=int, default=504,
        help="smoke test 合成交易日数 (默认 504 ≈ 2 年)",
    )
    args = parser.parse_args()

    # Smoke test: 先生成合成数据
    if args.smoke:
        log.info("🔧 Smoke test: 生成合成数据")
        generate_synthetic_data(
            n_stocks=args.smoke_stocks,
            days=args.smoke_days,
        )

    # 动态加载策略类
    import importlib
    module_path, class_name = args.strategy.rsplit(".", 1)
    module = importlib.import_module(module_path)
    strategy_class = getattr(module, class_name)

    validator = WalkForwardValidator(
        strategy_class=strategy_class,
        strategy_params={},
        param_grid={},
        start_date=date.fromisoformat(args.start),
        end_date=date.fromisoformat(args.end),
        train_months=args.train_months,
        test_months=args.test_months,
        step_months=args.step_months,
        n_optimize_samples=args.n_optimize_samples,
    )

    try:
        report = validator.run()
        validator.save_report(report, Path(args.output))
        passed, reasons = report.passes_gate()
        if passed:
            log.info("✅ OOS 门禁通过")
        else:
            log.warning("⚠️ OOS 门禁未通过:")
            for r in reasons:
                log.warning(f"  {r}")
    except NotImplementedError as e:
        log.error(f"⚠️ 实现未完成: {e}")
        sys.exit(1)
    except Exception as e:
        log.error(f"❌ walk_forward 失败: {e}")
        raise


if __name__ == "__main__":
    main()