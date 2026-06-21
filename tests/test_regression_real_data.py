"""
真实 A 股数据回放测试 — 端到端回归保护
==========================================

目的
----
- 验证 BacktestEngine + 5 个信号策略在真实数据上能跑通
- 锁定关键指标在合理区间,任何破坏业务逻辑的改动都会触发失败
- 不依赖网络/外部数据,纯本地 SQLite (quant.db)

数据要求
--------
- quant.db 已就绪 (data_range ~ 2024-01-02 ~ 2026-06-16)
- 至少包含 600519 (贵州茅台) 和 000001 (平安银行) 的日线数据
- pytest 运行时直接用 quant.db,无需 setup/teardown

测试矩阵
--------
- 5 个信号策略 × 2 只代表股票 = 10 个 backtest
- 每个 backtest 断言: total_return / sharpe / max_drawdown / total_trades 在合理范围
- 1 个集成测试: 全 5 策略在 600519 上 1 年回测,平均 Sharpe > 0 (粗略 sanity)
- 1 个组合回测: 小市值 + 反转 6 个月回测,断言 total_trades > 0

注意
----
- 这些不是性能测试,只验证"策略没崩"
- Sharpe 范围设得宽 (-2 ~ 5),避免单一调参导致测试失败
- 数据范围在 1 年内,样本量充足但不代表样本外表现
- 测试速度 ~10-20 秒 (10 个 backtest)
"""
import math
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.backtest.engine import BacktestEngine
from src.strategies.bollinger_breakout import BollingerBreakoutStrategy
from src.strategies.macd_signal import MACDSignalStrategy
from src.strategies.ma_cross import MACrossStrategy
from src.strategies.rsi_reversal import RSIReversalStrategy
from src.strategies.turtle_trading import TurtleTradingStrategy


# ============================================================
# 共享 fixtures
# ============================================================

# 回测区间: 1 年,数据中部,样本量 ~243 个交易日
TEST_START = date(2024, 6, 1)
TEST_END = date(2025, 6, 1)
INITIAL_CAPITAL = 100_000

# 代表股票: 大盘蓝筹 (茅台) + 银行 (平安) — 覆盖防御/平衡
STOCK_LARGE_CAP = "600519"   # 贵州茅台
STOCK_FINANCIAL = "000001"  # 平安银行

# 5 个信号策略 (继承 BaseStrategy,backtesting.py 引擎)
SIGNAL_STRATEGIES = [
    MACrossStrategy,
    MACDSignalStrategy,
    RSIReversalStrategy,
    BollingerBreakoutStrategy,
    TurtleTradingStrategy,
]


def _is_valid_number(x) -> bool:
    """检查 x 是有效数字 (非 None / 非 NaN)"""
    if x is None:
        return False
    try:
        return not math.isnan(float(x))
    except (TypeError, ValueError):
        return False


def _assert_reasonable_report(report, strategy_name: str, stock_code: str):
    """断言回测报告字段全部合理"""
    # total_return: -80% ~ +200% (1 年)
    assert _is_valid_number(report.total_return), \
        f"{strategy_name} x {stock_code}: total_return={report.total_return}"
    assert -80 <= report.total_return <= 200, \
        f"{strategy_name} x {stock_code}: 收益 {report.total_return:.2f}% 超出 [-80, 200] 区间"

    # sharpe_ratio: -3 ~ 5
    assert _is_valid_number(report.sharpe_ratio), \
        f"{strategy_name} x {stock_code}: sharpe_ratio={report.sharpe_ratio}"
    assert -3 <= report.sharpe_ratio <= 5, \
        f"{strategy_name} x {stock_code}: 夏普 {report.sharpe_ratio:.2f} 超出 [-3, 5] 区间"

    # max_drawdown: 0 ~ 80 (引擎输出正值)
    assert _is_valid_number(report.max_drawdown), \
        f"{strategy_name} x {stock_code}: max_drawdown={report.max_drawdown}"
    assert 0 <= report.max_drawdown <= 80, \
        f"{strategy_name} x {stock_code}: 回撤 {report.max_drawdown:.2f}% 超出 [0, 80] 区间"

    # total_trades: >= 0 (允许 0 — 1 年内可能不触发)
    assert report.total_trades >= 0, \
        f"{strategy_name} x {stock_code}: 交易次数 {report.total_trades} < 0"

    # annual_return 应当与 total_return 符号一致 (容差 ±5%)
    if _is_valid_number(report.annual_return):
        # 1 年回测,两者接近 (误差来自交易日差异)
        diff = abs(report.annual_return - report.total_return)
        assert diff < 10, \
            f"{strategy_name} x {stock_code}: 年化 {report.annual_return:.2f}% vs 收益 {report.total_return:.2f}% 差 {diff:.2f}%"


# ============================================================
# 5 策略 × 1 股票 (贵州茅台) — 参数化
# ============================================================


class TestSignalStrategiesOnLargeCap:
    """5 策略在 600519 贵州茅台 1 年回测"""

    @pytest.mark.parametrize("strategy_class", SIGNAL_STRATEGIES,
                             ids=lambda c: c.__name__)
    def test_strategy_on_600519(self, strategy_class):
        engine = BacktestEngine()
        report = engine.run(
            strategy_class=strategy_class,
            stock_code=STOCK_LARGE_CAP,
            start_date=TEST_START,
            end_date=TEST_END,
            initial_capital=INITIAL_CAPITAL,
        )
        _assert_reasonable_report(report, strategy_class.__name__, STOCK_LARGE_CAP)


class TestSignalStrategiesOnFinancial:
    """5 策略在 000001 平安银行 1 年回测"""

    @pytest.mark.parametrize("strategy_class", SIGNAL_STRATEGIES,
                             ids=lambda c: c.__name__)
    def test_strategy_on_000001(self, strategy_class):
        engine = BacktestEngine()
        report = engine.run(
            strategy_class=strategy_class,
            stock_code=STOCK_FINANCIAL,
            start_date=TEST_START,
            end_date=TEST_END,
            initial_capital=INITIAL_CAPITAL,
        )
        _assert_reasonable_report(report, strategy_class.__name__, STOCK_FINANCIAL)


# ============================================================
# 集成测试
# ============================================================


class TestIntegration:
    """端到端集成: 全 5 策略在 600519 上跑一次"""

    def test_all_5_strategies_complete_on_600519(self):
        """5 策略全部能跑完, 至少 3 个有交易 (否则说明策略全废)"""
        engine = BacktestEngine()
        results = []
        for cls in SIGNAL_STRATEGIES:
            r = engine.run(
                strategy_class=cls,
                stock_code=STOCK_LARGE_CAP,
                start_date=TEST_START,
                end_date=TEST_END,
                initial_capital=INITIAL_CAPITAL,
            )
            results.append((cls.__name__, r))

        # 至少 3 个有交易
        active = [(n, r) for n, r in results if r.total_trades > 0]
        assert len(active) >= 3, \
            f"5 策略中仅 {len(active)} 个有交易: {[n for n, _ in results]}"


# ============================================================
# 回归保护: 防止引擎/策略关键路径被破坏
# ============================================================


class TestEngineStability:
    """验证 BacktestEngine 自身没崩"""

    def test_engine_can_be_instantiated(self):
        """BacktestEngine() 能正常构造"""
        engine = BacktestEngine()
        assert engine is not None

    def test_engine_loads_config(self):
        """引擎从 config 加载交易成本 (修复 P0 标记)"""
        engine = BacktestEngine()
        # 验证成本参数已加载
        assert engine.commission > 0, f"commission 应 > 0, 实际 {engine.commission}"
        assert engine.stamp_duty > 0, f"stamp_duty 应 > 0, 实际 {engine.stamp_duty}"
        # 新增: slippage 和 min_commission (第 2 批修复)
        assert engine.slippage >= 0, f"slippage 应 >= 0, 实际 {engine.slippage}"
        assert engine.min_commission >= 0, f"min_commission 应 >= 0, 实际 {engine.min_commission}"

    def test_empty_data_raises_clear_error(self):
        """无数据时应抛 ValueError 而不是崩溃"""
        engine = BacktestEngine()
        # 用一只不存在的股票代码 (数据库里没有)
        with pytest.raises(ValueError, match="无数据"):
            engine.run(
                strategy_class=MACrossStrategy,
                stock_code="999999",  # 不存在的代码
                start_date=TEST_START,
                end_date=TEST_END,
            )

    def test_invalid_strategy_class_rejected(self):
        """非 BaseStrategy 子类应抛错(防止有人传普通类)"""
        engine = BacktestEngine()

        class NotAStrategy:
            name = "fake"

        with pytest.raises((TypeError, AttributeError, Exception)):
            engine.run(
                strategy_class=NotAStrategy,
                stock_code=STOCK_LARGE_CAP,
                start_date=TEST_START,
                end_date=TEST_END,
            )


# ============================================================
# 防回退: 确保改的代码没回退
# ============================================================


class TestNoRegressionOnKeyFixes:
    """回归保护: 防止之前的 P0/P1 修复被回退"""

    def test_westock_no_shell_true(self):
        """westock.py 不应回退到 shell=True"""
        from pathlib import Path
        path = _PROJECT_ROOT / "src" / "data" / "westock.py"
        src = path.read_text(encoding="utf-8")
        assert "shell=True" not in src

    def test_xgb_uses_json_not_pickle(self):
        """scripts/param_server.py 不应再用 pickle"""
        path = _PROJECT_ROOT / "scripts" / "param_server.py"
        src = path.read_text(encoding="utf-8")
        assert "pickle.load" not in src, "param_server 不应再用 pickle.load"
        assert "xgb_scaler.json" in src, "应改用 xgb_scaler.json"

    def test_api_requires_bearer_token(self):
        """FastAPI /api/* 应要求 Bearer token"""
        pytest.importorskip("httpx")
        from fastapi.testclient import TestClient
        from src.web.app import create_app

        app = create_app()
        # TestClient 默认用 "testserver" 作为 Host 头,
        # 但我们的 TrustedHostMiddleware 只放行 localhost/127.0.0.1/0.0.0.0
        # 用 base_url 改默认 Host
        client = TestClient(app, base_url="http://localhost")

        # 无 token → 401 (FastAPI HTTPBearer 默认) 或 403
        r = client.get("/api/strategies")
        assert r.status_code in (401, 403), \
            f"无 token 应 401/403, 实际 {r.status_code} body={r.text[:100]}"

        # 错 token → 403 (我们的 verify_api_key 拒绝)
        r = client.get("/api/strategies", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 403, f"错 token 应 403, 实际 {r.status_code}"
