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

        实现建议:
          - 网格搜索: 遍历 param_grid 笛卡尔积
          - 随机搜索: 100 次采样
          - 贝叶斯: optuna
        """
        # TODO: 实现, 复用 PortfolioBacktestEngine
        raise NotImplementedError(
            "参数优化待实现 — 依赖 PortfolioBacktestEngine"
        )

    def _backtest(
        self, test_start: date, test_end: date, params: dict,
    ) -> Dict[str, float]:
        """
        OOS 测试窗口回测, 返回指标 dict
        """
        # TODO: 实现
        # from src.backtest.portfolio_engine import PortfolioBacktestEngine
        # strategy = self.strategy_class(**{**self.strategy_params, **params})
        # engine = PortfolioBacktestEngine()
        # report = engine.run(strategy, test_start, test_end, self.initial_capital)
        # return {
        #     "sharpe": report.sharpe_ratio,
        #     "annual_return": report.annual_return,
        #     ...
        # }
        raise NotImplementedError("OOS 回测待实现")

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
        "--output", default="docs/oos_validation_report.md",
        help="报告输出路径",
    )
    args = parser.parse_args()

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
    )

    try:
        report = validator.run()
        validator.save_report(report, Path(args.output))
    except NotImplementedError as e:
        log.error(f"⚠️ 骨架阶段, 待实现: {e}")
        log.error("提示: 实现 _optimize / _backtest 后即可跑通")
        sys.exit(1)


if __name__ == "__main__":
    main()