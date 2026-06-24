"""
组合回测引擎
============

独立于 BacktestEngine，专用于选股策略的组合回测。
基于纯 Pandas/NumPy 实现，不依赖 backtesting.py 框架。

核心流程:
1. 从 SQLite 加载全市场 daily_price 数据
2. 为每只股票计算因子 (市值估算、波动率、换手率、收益率)
3. 每隔 N 个交易日执行一次选股 → 等权分配
4. 考虑交易成本 (佣金+印花税)
5. 生成净值曲线和统计指标，复用 BacktestReport 格式
"""
from __future__ import annotations
import logging
from datetime import date, timedelta


import numpy as np
import pandas as pd

from ..config import get_config
from ..db.sql_utils import read_sql
from ..models.repository import DataRepository
from ..metrics import sharpe_ratio as _sharpe_ratio, max_drawdown as _max_drawdown, volatility as _volatility, profit_factor as _profit_factor, win_rate as _win_rate
from .base_selection_strategy import BaseSelectionStrategy
from .engine import BacktestReport

logger = logging.getLogger(__name__)


class PortfolioBacktestEngine:
    """
    组合回测引擎 — 选股策略专用

    使用示例:
        engine = PortfolioBacktestEngine()
        report = engine.run(
            strategy=SmallCapStrategy(),
            start_date=date(2022, 1, 1),
            end_date=date(2024, 12, 31),
            initial_capital=1000000,
        )
    """

    def __init__(self):
        config = get_config()
        self.repo = DataRepository()
        self.repo.init_database()

        # 交易成本
        costs = config["backtest"]["costs"]
        self.commission = costs["commission_rate"]   # 0.0003 (万三)
        self.stamp_duty = costs["stamp_duty_rate"]   # 0.0005 (千五,仅卖出)
        self.slippage = costs.get("slippage", 0.0001)  # 0.0001 (万分之一)
        self.min_commission = costs.get("min_commission", 5.0)  # ¥5 最低佣金
        self.benchmark_code = config["backtest"]["benchmark"]
        self.risk_free_rate = config["backtest"]["risk_free_rate"]

        # Step B 优化 (2026-06-25): _load_all_data LRU 缓存
        # walk_forward 跑 N windows × M samples = 多次相同 (start, end) 调用,
        # 缓存避免每次重读 DB + 重算 pct_change fallback (1M+ 行)
        self._data_cache: dict = {}
        self._data_cache_max = 4

    def run(self,
            strategy: BaseSelectionStrategy,
            start_date: date,
            end_date: date,
            initial_capital: float = 1000000,
            ) -> BacktestReport:
        """
        执行组合回测

        Args:
            strategy: 选股策略实例
            start_date: 回测开始日期
            end_date: 回测结束日期
            initial_capital: 初始资金

        Returns:
            BacktestReport (stock_code="PORTFOLIO")
        """
        logger.info(f"组合回测: {strategy.name} | {start_date} ~ {end_date} | 资金={initial_capital:,.0f}")

        # Step 1: 加载全市场数据（含 lookback 预备数据）
        # _build_universe 需要 start_date 之前的数据计算因子
        lookback_buffer = timedelta(days=strategy.lookback_days * 2)  # 2倍缓冲确保足够
        data_start = start_date - lookback_buffer
        all_data = self._load_all_data(data_start, end_date)
        if all_data.empty:
            raise ValueError(f"在 [{data_start}, {end_date}] 范围内无数据")

        # 截断回测范围内的交易日（预备数据只用于因子计算）
        backtest_dates = sorted(
            all_data[all_data["trade_date"] >= pd.Timestamp(start_date)]["trade_date"].unique()
        )
        if len(backtest_dates) == 0:
            raise ValueError(f"在 [{start_date}, {end_date}] 范围内无交易日")

        # Step 2: 计算每个调仓日的因子和选股（仅回测区间）
        rebalance_dates = self._get_rebalance_dates(all_data, strategy.rebalance_days)
        # 过滤：只保留 >= start_date 的调仓日
        rebalance_dates = [d for d in rebalance_dates if d >= pd.Timestamp(start_date)]
        logger.info(f"共 {len(rebalance_dates)} 个调仓日 (回测区间内)")

        # Step 3: 模拟组合净值曲线
        equity_curve, rebalance_details = self._simulate_portfolio(
            strategy, all_data, rebalance_dates, initial_capital, start_date, end_date
        )

        # Step 4: 计算统计指标
        report = self._build_report(
            strategy, equity_curve, rebalance_details,
            start_date, end_date, initial_capital
        )

        logger.info(
            f"组合回测完成: 总收益={report.total_return:.2f}%, "
            f"夏普={report.sharpe_ratio:.2f}, 最大回撤={report.max_drawdown:.2f}%"
        )
        return report

    # ===== 内部方法 =====

    def _load_all_data(self, start: date, end: date) -> pd.DataFrame:
        """加载全市场 daily_price 数据

        Step B 优化 (2026-06-25): 加 LRU 缓存
        walk_forward 跑 N windows × M samples = 多次相同 (start, end) 调用,
        缓存避免每次重读 DB + 重算 pct_change fallback (1M+ 行)
        """
        cache_key = (start, end)
        if cache_key in self._data_cache:
            return self._data_cache[cache_key].copy()

        sql = """
            SELECT dp.code, sb.name, sb.list_date, NULL AS mcap_yi, dp.trade_date,
                   dp.open, dp.high, dp.low, dp.close,
                   dp.volume, dp.amount, dp.pct_change, dp.turnover
            FROM daily_price dp
            JOIN stock_basic sb ON dp.code = sb.code
            WHERE dp.trade_date >= :start
              AND dp.trade_date <= :end
            ORDER BY dp.code, dp.trade_date
        """
        df = read_sql(sql, self.repo.engine, {"start": start, "end": end})
        if df.empty:
            return df
        df["trade_date"] = pd.to_datetime(df["trade_date"])

        # 修复: pct_change 列可能为 NULL，从 close 价格自动计算
        if df["pct_change"].isna().all():
            logger.info("pct_change 全为 NULL，从 close 价格计算...")
            df = df.sort_values(["code", "trade_date"])
            df["prev_close"] = df.groupby("code")["close"].shift(1)
            df["pct_change"] = (df["close"] - df["prev_close"]) / df["prev_close"] * 100
            df["pct_change"] = df["pct_change"].fillna(0)
            df = df.drop(columns=["prev_close"])

        # 修复: turnover 列可能为 NULL — 选股策略依赖它计算 mcap_yi
        # 兜底策略:
        #   - 全 NULL: 用 1.0 (1% 换手率常量) 作为代理
        #     理由: mcap_yi = amount / turnover, 1.0 是 A 股换手率常见量级
        #     后果: mcap_yi 退化为 amount/1e6, 仅保持相对顺序, 绝对值有偏
        #   - 部分 NULL: 缺失视为 0 (即该日无成交 → mcap 退化为 0)
        if df["turnover"].isna().all():
            logger.warning(
                "turnover 全为 NULL (来自 daily_price 表), 使用 1.0 作为代理值. "
                "这会让 mcap_yi 退化为 amount/1e6 (亿元), 仅保留相对顺序. "
                "如需精确数据, 请重新导入 turnover 字段."
            )
            df["turnover"] = 1.0
        else:
            df["turnover"] = df["turnover"].fillna(0)

        # Step B 优化: 写入 LRU 缓存 (max 4 entries)
        if len(self._data_cache) >= self._data_cache_max:
            # 简单 FIFO: 删除最早插入的
            oldest_key = next(iter(self._data_cache))
            del self._data_cache[oldest_key]
        self._data_cache[cache_key] = df

        return df

    def _get_rebalance_dates(self, data: pd.DataFrame, rebalance_days: int) -> list[pd.Timestamp]:
        """获取调仓日列表 (每 rebalance_days 个交易日)"""
        all_dates = sorted(data["trade_date"].unique())
        return all_dates[::rebalance_days]

    def _build_universe(self, data: pd.DataFrame, as_of_date: pd.Timestamp,
                        lookback: int, strategy: BaseSelectionStrategy) -> pd.DataFrame:
        """
        为指定日期构建股票池 (含因子)

        Returns:
            DataFrame with columns: code, name, close, avg_amount_wan, avg_turnover,
            market_cap_yi, return_Nd, volatility_Nd
        """
        # 截至调仓日的数据
        cutoff_data = data[data["trade_date"] <= as_of_date].copy()

        # 每只股票最近 lookback 天的数据
        # 按股票分组，取最近 lookback 行
        recent = cutoff_data.groupby("code").tail(lookback)

        # 只保留有足够数据的股票
        # 原始要求: stock_counts >= lookback // 2
        # 修复: 当数据区间不足 lookback 时, 放宽要求 (使用实际可用天数的一半)
        #   例: lookback=252, 实际数据=22 → min_required=11 而非 126
        #   这样 lookback_days=252 的策略也能在数据初期运行
        stock_counts = recent.groupby("code").size()
        if len(stock_counts) == 0:
            return pd.DataFrame()
        max_records = int(stock_counts.max())
        min_required = min(lookback // 2, max(max_records // 2, 1))
        if max_records < lookback // 2:
            logger.warning(
                "_build_universe: 数据区间只有 %d 条, 不足 lookback/2=%d, "
                "放宽到 %d (策略: %s)",
                max_records, lookback // 2, min_required,
                type(strategy).__name__ if strategy else "unknown",
            )
        valid_codes = stock_counts[stock_counts >= min_required].index
        recent = recent[recent["code"].isin(valid_codes)]

        if recent.empty:
            return pd.DataFrame()

        # 计算因子
        agg_dict = {
            "name": "last",
            "list_date": "first",
            "mcap_yi": "last",      # 从 stock_basic 获取市值
            "close": "last",
            "amount": "mean",      # 日均成交额
            "turnover": "mean",     # 日均换手率
            "pct_change": ["mean", "std"],  # 收益率均值和标准差
        }

        factors = recent.groupby("code").agg(agg_dict).reset_index()
        factors.columns = [
            "code", "name", "list_date", "mcap_from_db", "close",
            "avg_amount", "avg_turnover",
            "avg_return", "volatility"
        ]

        # 日均成交额 (元→万元)
        factors["avg_amount_wan"] = factors["avg_amount"] / 10000

        # 估算总市值 (亿元)
        # 优先使用 stock_basic.mcap_yi (来自实时行情数据)
        # 回退: 成交额/换手率*100/1e8 (需要换手率数据)
        valid_turnover = factors["avg_turnover"].notna() & (factors["avg_turnover"] > 0.01)
        factors["market_cap_yi"] = None
        factors.loc[valid_turnover, "market_cap_yi"] = (
            factors.loc[valid_turnover, "avg_amount"]
            / factors.loc[valid_turnover, "avg_turnover"]
            * 100 / 100000000
        )
        # 回退到 DB 中的市值数据
        has_db_mcap = factors["mcap_from_db"].notna() & (factors["mcap_from_db"] > 0)
        no_calc_mcap = factors["market_cap_yi"].isna()
        factors.loc[no_calc_mcap & has_db_mcap, "market_cap_yi"] = factors.loc[no_calc_mcap & has_db_mcap, "mcap_from_db"]

        # 近N日累计收益率
        factors["return_Nd"] = (
            factors["avg_return"] * lookback
        )

        # 近N日波动率 (标准差)
        factors["volatility_Nd"] = factors["volatility"]

        # 清理
        factors = factors.drop(columns=["mcap_from_db"], errors="ignore")
        factors = factors.dropna(subset=["close", "avg_amount_wan"])
        factors = factors[factors["close"] > 0]

        return factors

    def _simulate_portfolio(self,
                           strategy: BaseSelectionStrategy,
                           all_data: pd.DataFrame,
                           rebalance_dates: list[pd.Timestamp],
                           initial_capital: float,
                           start_date: date,
                           end_date: date,
                           ) -> tuple:
        """
        模拟组合净值曲线 (向量化版本)

        T+1 语义:
          - holdings_history[i] = 在 Day i 持有的股票列表 (用于赚取 Day i 的收益)
          - 这些股票是在 Day i-1 调仓时选出来的 (T+1)
          - 调仓成本在调仓日当天、收益结算之后扣除 (与原实现一致)

        优化要点:
          1) 调仓决策循环: 仅遍历调仓日 (~24 次) 而非全市场日 (~1000 次)
          2) 日收益矩阵: 用 pivot 一次性生成 [date x code] 的收益率矩阵
          3) 日均收益: 一次 .loc 抽取 + numpy.mean，避免 Python 内层 for 循环
        """
        # 全部交易日 (回测区间内)
        all_dates = sorted([
            d for d in all_data["trade_date"].unique()
            if start_date <= d.date() <= end_date
        ])
        if not all_dates:
            return pd.Series(dtype=float), []

        # === Pass 1: 决定每日持仓 (T+1 语义) ===
        # holdings_today = 在"当前"调仓日应当为下一个交易日选定的股票
        # holdings_history[i] = 在 Day i 实际持有的股票 (= Day i-1 调出的结果)
        holdings_today: list[str] = []
        holdings_history: list[list[str]] = []  # 与 all_dates 等长
        rebalance_details: list[Dict] = []
        rebalance_set = set(rebalance_dates)

        for dt in all_dates:
            # 当前 Day 的持仓 = 上一调仓日选定的 holdings_today
            holdings_history.append(holdings_today[:])

            if dt in rebalance_set:
                universe = self._build_universe(all_data, dt, strategy.lookback_days, strategy)
                if not universe.empty:
                    filtered = strategy.filter_universe(universe)
                    new_selected = strategy.select(dt, filtered)
                else:
                    new_selected = holdings_today  # 数据缺失: 沿用

                if set(new_selected) != set(holdings_today):
                    rebalance_details.append({
                        "date": str(dt.date()),
                        "action": "rebalance",
                        "previous_holdings": holdings_today[:],
                        "new_holdings": new_selected[:],
                        "n_new": len(new_selected),
                    })
                    holdings_today = new_selected  # 下一个交易日生效

        # === Pass 2: 构建日收益矩阵 (一次性, 无循环) ===
        # 单位: 小数 (即 0.01 表示 +1%)
        # pivot_table(fill_value=0) 已经把缺失 cell 填 0, 之后除以 100 转成小数
        returns_pivot = (
            all_data
            .pivot_table(index="trade_date", columns="code",
                         values="pct_change", fill_value=0)
            / 100.0
        )
        all_codes = set(returns_pivot.columns)

        # === Pass 3: 向量化计算每日组合收益 ===
        n_days = len(all_dates)
        portfolio_returns = np.zeros(n_days)
        for i, dt in enumerate(all_dates):
            holdings = holdings_history[i]
            if not holdings:
                continue
            valid = [c for c in holdings if c in all_codes]
            if not valid or dt not in returns_pivot.index:
                continue
            portfolio_returns[i] = float(np.mean(returns_pivot.loc[dt, valid].values))

        # === Pass 4: 组合净值曲线 + 调仓成本 ===
        # 语义 (与原版一致):
        #   Day 0: portfolio_equity[0] = initial_capital (调仓前)
        #          然后应用 Day 0 调仓成本 (如果有)
        #   Day i (i>=1):
        #     1) portfolio_equity[i] = equity * (1 + ret_i)
        #     2) 若 Day i 是调仓日: equity -= 调仓成本
        equity = initial_capital
        equity_curve = [equity]   # Day 0 (回测首日, 调仓前)

        # 提前索引: 哪些天是真正的调仓日 (holdings 变化)
        rebalance_change_indices: Set[int] = set()
        for i in range(n_days - 1):
            if set(holdings_history[i]) != set(holdings_history[i + 1]):
                rebalance_change_indices.add(i)

        def _apply_cost_at(idx: int, base_equity: float):
            """在 idx 日末尾应用调仓成本, 返回扣减后的 equity 和补全 rebalance_details

            成本按"实际换手金额"计算,不是全仓:
            - 卖出侧: 卖出股数 / 原持仓数 × equity (等权假设)
            - 买入侧: 买入股数 / 新持仓数 × equity (等权假设)
            """
            prev_holdings = holdings_history[idx]
            new_holdings = holdings_history[idx + 1] if idx + 1 < n_days else prev_holdings

            prev_set = set(prev_holdings)
            new_set = set(new_holdings)
            n_sold = len(prev_set - new_set)
            n_bought = len(new_set - prev_set)
            n_prev = len(prev_holdings)
            n_new = len(new_holdings)

            # 等权假设:卖出/买入金额按持仓占比折算
            sell_amount = (n_sold / n_prev) * base_equity if n_prev and n_sold else 0.0
            buy_amount = (n_bought / n_new) * base_equity if n_new and n_bought else 0.0

            sell_cost = self._calc_transaction_cost(
                sell_amount, is_sell=True, n_trades=n_sold
            ) if n_sold else 0.0
            buy_cost = self._calc_transaction_cost(
                buy_amount, is_sell=False, n_trades=n_bought
            ) if n_bought else 0.0

            new_equity = base_equity - sell_cost - buy_cost

            # 补全 rebalance_details 中匹配 date 的最后一条
            if rebalance_details:
                last = rebalance_details[-1]
                if last.get("date") == str(all_dates[idx].date()):
                    last["equity_before_rebalance"] = base_equity
                    last["transaction_cost"] = sell_cost + buy_cost
                    last["turnover_pct"] = round(
                        (sell_amount + buy_amount) / base_equity * 100
                        if base_equity > 0 else 0, 2
                    )
            return new_equity

        # 1) 处理 Day 0 的调仓成本 (基线是 initial_capital)
        if 0 in rebalance_change_indices:
            equity = _apply_cost_at(0, equity)

        # 2) 遍历 Day 1..N-1
        for i in range(1, n_days):
            # 应用 Day i 收益
            equity *= (1 + portfolio_returns[i])
            # 记录当日净值 (调仓前)
            equity_curve.append(equity)
            # 应用 Day i 调仓成本 (如有)
            if i in rebalance_change_indices:
                equity = _apply_cost_at(i, equity)

        equity_series = pd.Series(equity_curve, index=pd.to_datetime(all_dates))
        return equity_series, rebalance_details

    def _calc_transaction_cost(
        self,
        turnover_amount: float,
        is_sell: bool = False,
        n_trades: int = 1,
    ) -> float:
        """
        计算调仓的交易成本(基于实际换手金额,不是全仓)

        成本构成:
        1. 佣金: turnover * commission_rate,每笔最低 min_commission 元
        2. 印花税(仅卖出): turnover * stamp_duty_rate
        3. 滑点: turnover * slippage (假设每次成交滑点一致)

        Args:
            turnover_amount: 实际买卖金额(不是全仓 equity)
            is_sell: True=卖出侧, False=买入侧
            n_trades: 本次调仓涉及的交易笔数(用于 min_commission 计算)

        修复历史:
            之前版本按"全仓换手"假设,即 1 只调仓也扣 100% 仓位的佣金,
            实际只调 N 只时,成本高估 N 倍。改为按实际 turnover_amount 计费。
        """
        if turnover_amount <= 0 or n_trades <= 0:
            return 0.0

        # 1. 佣金(双边,按笔数摊销 min_commission)
        commission_raw = turnover_amount * self.commission
        commission_min_floor = self.min_commission * n_trades
        commission = max(commission_raw, commission_min_floor)

        # 2. 印花税(仅卖出)
        stamp = turnover_amount * self.stamp_duty if is_sell else 0.0

        # 3. 滑点(双边都收)
        slip = turnover_amount * self.slippage

        return commission + stamp + slip

    def _build_report(self,
                      strategy: BaseSelectionStrategy,
                      equity_series: pd.Series,
                      rebalance_details: list[Dict],
                      start_date: date,
                      end_date: date,
                      initial_capital: float,
                      ) -> BacktestReport:
        """构建回测报告"""
        if equity_series.empty:
            raise ValueError("净值曲线为空，无法生成报告")

        final_equity = equity_series.iloc[-1]
        total_return = (final_equity / initial_capital - 1) * 100

        # 年化收益
        trading_days = len(equity_series)
        years = trading_days / 250
        if years > 0 and total_return > -100:
            annual_return = ((1 + total_return / 100) ** (1 / years) - 1) * 100
        else:
            annual_return = 0

        # 日收益率
        daily_returns = equity_series.pct_change().dropna()

        # PR2.2: 夏普 / 最大回撤 / 波动率 / 胜率 / 盈亏比 全部委托给 metrics.performance
        sharpe = _sharpe_ratio(daily_returns.values, risk_free=self.risk_free_rate)
        # PR2.2: max_drawdown 委托 metrics.performance, 但 *_max_drawdown 返回 ratio (-0.25 表示 25%),
        # 而 BacktestReport.max_drawdown 字段语义为 %, 故 ×100 转百分比 (与 stock_screener 对齐)
        max_drawdown = _max_drawdown(equity_series.values) * 100  # 转百分比
        annual_vol = _volatility(daily_returns.values)
        win_rate = _win_rate(daily_returns.values)
        profit_factor = _profit_factor(daily_returns.values)

        # 卡玛比率 (依赖上面算出的 annual_return 和 max_drawdown)
        # 卡玛比率: annual_return 是 % 数, max_drawdown 也是 % 数 (已 *100)
        calmar = annual_return / abs(max_drawdown) if max_drawdown != 0 else 0

        # 基准收益
        benchmark_return = self._calc_benchmark_return(start_date, end_date)
        excess_return = total_return - benchmark_return

        # 净值曲线
        equity_curve = [
            {"date": str(dt.date()), "equity": round(eq, 2)}
            for dt, eq in equity_series.items()
        ]

        # 月度收益
        monthly_returns = self._calc_monthly_returns(equity_series)

        # 总交易次数 = 调仓次数
        total_trades = len(rebalance_details)

        report = BacktestReport(
            strategy_name=strategy.name,
            stock_code="PORTFOLIO",
            stock_name=strategy.name,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            final_equity=round(final_equity, 2),
            total_return=round(total_return, 2),
            annual_return=round(annual_return, 2),
            sharpe_ratio=round(sharpe, 2),
            max_drawdown=round(max_drawdown, 2),
            win_rate=round(win_rate, 2),
            profit_factor=round(profit_factor, 2) if profit_factor else 0,
            total_trades=total_trades,
            annual_volatility=round(annual_vol, 2),
            calmar_ratio=round(calmar, 2),
            benchmark_return=round(benchmark_return, 2),
            excess_return=round(excess_return, 2),
            equity_curve=equity_curve,
            trades_detail=rebalance_details,
            monthly_returns=monthly_returns,
            cost_config={
                "commission_rate": self.commission,
                "stamp_duty_rate": self.stamp_duty,
                "slippage": self.slippage,
                "min_commission": self.min_commission,
                "note": "组合回测: 等权配置,按实际换手金额计算(佣金+印花税+滑点)",
            }
        )
        return report

    def _calc_benchmark_return(self, start: date, end: date) -> float:
        """计算基准（沪深300）同期收益率"""
        try:
            bench_df = self.repo.get_benchmark_data(self.benchmark_code, start, end)
            if bench_df.empty:
                return 0
            return (bench_df["close"].iloc[-1] / bench_df["close"].iloc[0] - 1) * 100
        except Exception:
            return 0

    def _calc_monthly_returns(self, equity_series: pd.Series) -> dict[str, float]:
        """计算月度收益率"""
        if equity_series.empty:
            return {}
        monthly = {}
        for dt, eq in equity_series.items():
            key = f"{dt.year}-{dt.month:02d}"
            monthly[key] = round(float(eq), 2)
        return monthly
