"""
模型四：技术指标投票模型 (Technical Voting Model)
===================================================

定位: 整合现有 5 个单股信号策略，用投票机制产生更稳健的交易信号。

设计理念:
不发明新策略，而是把双均线/MACD/RSI/布林带/海龟五个策略变成"投票委员会"。
每个策略对股票投票（买入=+1，卖出=-1，中性=0），总分 ≥ +2 才买入。

投票规则:
  双均线(5/20): 快线>慢线 → +1，反之 → -1
  MACD(12/26/9): 金叉状态 → +1，死叉 → -1
  RSI(14): <30 → +1，>70 → -1，30-70 → 0
  布林带(20/2): 价格<下轨 → +1，>上轨 → -1，轨内 → 0
  海龟(20/10): 突破买入 → +1，跌破止损 → -1，持仓 → 0

进入/退出:
  总分 ≥ +2 → 买入
  总分 ≤ -2 → 卖出
  其他 → 持有

风险控制:
  单只止损: -8%
  组合止损: -15%
  最大持仓: 10只

数据依赖: ✅ 已有 (daily_price 日线数据)
"""
from __future__ import annotations
import logging
from datetime import date, timedelta


import numpy as np
import pandas as pd

from ..backtest.engine import BacktestReport
from ..config import get_config
from ..db.sql_utils import read_sql
from ..metrics import sharpe_ratio as _sharpe_ratio, max_drawdown as _max_drawdown, volatility as _volatility, profit_factor as _profit_factor, win_rate as _win_rate
from ..models.repository import DataRepository

logger = logging.getLogger(__name__)


class TechnicalVotingModel:
    """
    技术指标投票模型

    不依赖 backtesting.py 框架，使用纯 Pandas/NumPy 实现:
    1. 加载全市场日线数据
    2. 逐股票计算 5 个策略指标
    3. 每日汇总投票 → 管理持仓
    4. 生成 BacktestReport

    使用示例:
        model = TechnicalVotingModel()
        report = model.run(
            stock_pool=["000001", "000002", ...],
            start_date=date(2022, 1, 1),
            end_date=date(2024, 12, 31),
            initial_capital=1000000,
        )
    """

    name = "技术指标投票模型"

    def __init__(self):
        config = get_config()
        self.repo = DataRepository()

        costs = config["backtest"]["costs"]
        self.commission_rate = costs["commission_rate"]
        self.stamp_duty_rate = costs["stamp_duty_rate"]
        self.slippage_rate = costs.get("slippage_rate", 0.0001)
        self.benchmark_code = config["backtest"]["benchmark"]

        # ── 投票策略参数 ──
        self.ma_fast = 5
        self.ma_slow = 20
        self.macd_fast = 12
        self.macd_slow = 26
        self.macd_signal = 9
        self.rsi_period = 14
        self.bb_period = 20
        self.bb_std = 2.0
        self.turtle_entry = 20
        self.turtle_exit = 10

        # ── 风控参数 ──
        self.entry_threshold = 2       # 总分 ≥ 2 买入
        self.exit_threshold = -2       # 总分 ≤ -2 卖出
        self.max_positions = 10        # 最多持仓10只
        self.single_stop_loss = -0.08  # 单只止损 -8%
        self.portfolio_stop = -0.15    # 组合止损 -15%

    def run(self,
            stock_pool: list[str],
            start_date: date,
            end_date: date,
            initial_capital: float = 1000000,
            ) -> BacktestReport:
        """
        执行投票模型回测

        Args:
            stock_pool: 股票池 (如沪深300成分股代码列表)
            start_date: 回测开始日期
            end_date: 回测结束日期
            initial_capital: 初始资金

        Returns:
            BacktestReport
        """
        logger.info(
            f"投票模型: 股票池 {len(stock_pool)} 只 | "
            f"{start_date} ~ {end_date} | 资金={initial_capital:,.0f}"
        )

        # Step 1: 加载数据
        all_data = self._load_pool_data(stock_pool, start_date, end_date)
        if all_data.empty:
            raise ValueError("股票池无数据")

        # Step 2: 为每只股票计算投票信号
        votes_df = self._compute_votes(all_data)
        logger.info(f"投票信号计算完成: {votes_df.shape}")

        # Step 3: 模拟交易
        equity_curve, trades = self._simulate_trading(
            votes_df, all_data, initial_capital, start_date, end_date
        )

        # Step 4: 生成报告
        report = self._build_report(
            equity_curve, trades, initial_capital, start_date, end_date
        )

        logger.info(
            f"投票模型完成: 总收益={report.total_return:.2f}%, "
            f"夏普={report.sharpe_ratio:.2f}, 交易={len(trades)}次"
        )
        return report

    # ─── 数据加载 ─────────────────────────────────────────

    def _load_pool_data(self, stock_pool: list[str],
                        start: date, end: date) -> pd.DataFrame:
        """加载股票池的日线数据"""
        # 需要稍微提前开始以计算指标 (加 120 个交易日)
        query_start = start - timedelta(days=365)

        sql = """
            SELECT dp.code, sb.name, dp.trade_date,
                   dp.open, dp.high, dp.low, dp.close,
                   dp.volume, dp.amount, dp.pct_change
            FROM daily_price dp
            JOIN stock_basic sb ON dp.code = sb.code
            WHERE dp.code IN :codes
              AND dp.trade_date >= :query_start
              AND dp.trade_date <= :end
            ORDER BY dp.code, dp.trade_date
        """
        df = read_sql(sql, self.repo.engine, {
            "codes": list(stock_pool),
            "query_start": query_start,
            "end": end,
        })
        if df.empty:
            return df
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        return df

    # ─── 投票信号计算 ──────────────────────────────────────

    def _compute_votes(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        为每只股票逐日计算 5 个策略的投票

        Returns:
            DataFrame with columns: code, trade_date, vote_sum, vote_detail
        """
        results = []
        for code, group in data.groupby("code"):
            group = group.sort_values("trade_date").copy()
            group = group.reset_index(drop=True)

            close = group["close"].values
            high = group["high"].values
            low = group["low"].values

            # 计算各策略信号
            v_ma = self._vote_ma(close)
            v_macd = self._vote_macd(close)
            v_rsi = self._vote_rsi(close)
            v_bb = self._vote_bollinger(close)
            v_turtle = self._vote_turtle(close, high, low)

            # 汇总投票
            votes = v_ma + v_macd + v_rsi + v_bb + v_turtle

            group["vote_sum"] = votes
            group["vote_ma"] = v_ma
            group["vote_macd"] = v_macd
            group["vote_rsi"] = v_rsi
            group["vote_bb"] = v_bb
            group["vote_turtle"] = v_turtle

            results.append(group)

        return pd.concat(results, ignore_index=True)

    def _vote_ma(self, close: np.ndarray) -> np.ndarray:
        """双均线投票: 快线>慢线 +1, 反之 -1"""
        votes = np.zeros(len(close), dtype=int)
        if len(close) < self.ma_slow:
            return votes

        sma_fast = pd.Series(close).rolling(self.ma_fast).mean().values
        sma_slow = pd.Series(close).rolling(self.ma_slow).mean().values

        for i in range(self.ma_slow, len(close)):
            if np.isnan(sma_fast[i]) or np.isnan(sma_slow[i]):
                continue
            votes[i] = 1 if sma_fast[i] > sma_slow[i] else -1
        return votes

    def _vote_macd(self, close: np.ndarray) -> np.ndarray:
        """MACD投票: DIF>DEA 且 MACD柱>0 +1, 反之 -1"""
        votes = np.zeros(len(close), dtype=int)
        if len(close) < self.macd_slow + self.macd_signal:
            return votes

        s = pd.Series(close)
        ema_fast = s.ewm(span=self.macd_fast, adjust=False).mean().values
        ema_slow = s.ewm(span=self.macd_slow, adjust=False).mean().values
        dif = ema_fast - ema_slow
        dea = pd.Series(dif).ewm(span=self.macd_signal, adjust=False).mean().values
        macd_bar = 2 * (dif - dea)

        start = self.macd_slow + self.macd_signal
        for i in range(start, len(close)):
            votes[i] = 1 if macd_bar[i] > 0 else -1
        return votes

    def _vote_rsi(self, close: np.ndarray) -> np.ndarray:
        """RSI投票: <30 +1, >70 -1, 30-70 0"""
        votes = np.zeros(len(close), dtype=int)
        if len(close) < self.rsi_period + 1:
            return votes

        s = pd.Series(close)
        delta = s.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.rolling(self.rsi_period).mean().values
        avg_loss = loss.rolling(self.rsi_period).mean().values

        for i in range(self.rsi_period, len(close)):
            if avg_loss[i] == 0:
                rsi = 100.0
            else:
                rs = avg_gain[i] / avg_loss[i]
                rsi = 100.0 - (100.0 / (1.0 + rs))
            if rsi < 30:
                votes[i] = 1
            elif rsi > 70:
                votes[i] = -1
        return votes

    def _vote_bollinger(self, close: np.ndarray) -> np.ndarray:
        """布林带投票: <下轨 +1, >上轨 -1, 轨内 0"""
        votes = np.zeros(len(close), dtype=int)
        if len(close) < self.bb_period:
            return votes

        s = pd.Series(close)
        mid = s.rolling(self.bb_period).mean().values
        std = s.rolling(self.bb_period).std().values
        upper = mid + self.bb_std * std
        lower = mid - self.bb_std * std

        for i in range(self.bb_period, len(close)):
            if close[i] < lower[i]:
                votes[i] = 1
            elif close[i] > upper[i]:
                votes[i] = -1
        return votes

    def _vote_turtle(self, close: np.ndarray, high: np.ndarray,
                     low: np.ndarray) -> np.ndarray:
        """海龟投票: 突破20日高点 +1, 跌破10日低点 -1"""
        votes = np.zeros(len(close), dtype=int)
        if len(close) < self.turtle_entry:
            return votes

        s_high = pd.Series(high)
        s_low = pd.Series(low)
        entry_channel = s_high.rolling(self.turtle_entry).max().values
        exit_channel = s_low.rolling(self.turtle_exit).min().values

        for i in range(self.turtle_entry, len(close)):
            if close[i] > entry_channel[i - 1]:
                votes[i] = 1
            elif close[i] < exit_channel[i - 1]:
                votes[i] = -1
        return votes

    # ─── 交易模拟 ──────────────────────────────────────────

    def _simulate_trading(self,
                          votes_df: pd.DataFrame,
                          all_data: pd.DataFrame,
                          initial_capital: float,
                          start_date: date,
                          end_date: date,
                          ) -> tuple[pd.Series, list[Dict]]:
        """
        基于投票信号模拟多股票组合交易

        Returns:
            (equity_series, trades_list)
        """
        cash = initial_capital
        positions: dict[str, Dict] = {}  # code → {shares, avg_cost, entry_date}
        equity_history = {}
        trades = []

        all_dates = sorted(all_data["trade_date"].unique())

        # 每日收盘价 pivot
        close_pivot = all_data.pivot_table(
            index="trade_date", columns="code", values="close"
        )
        close_pivot = close_pivot.ffill()

        # 每日投票 pivot
        vote_pivot = votes_df.pivot_table(
            index="trade_date", columns="code", values="vote_sum"
        ).fillna(0)

        prev_equity = initial_capital

        for dt in all_dates:
            dt_date = dt.date() if hasattr(dt, 'date') else dt
            if dt_date < start_date or dt_date > end_date:
                continue

            # ── 1. 检查现有持仓的退出条件 ──
            codes_to_sell = []
            for code, pos in list(positions.items()):
                if code not in close_pivot.columns:
                    continue
                current_close = close_pivot.loc[close_pivot.index == dt, code]
                if current_close.empty:
                    continue
                price = current_close.values[0]
                if pd.isna(price) or price <= 0:
                    continue

                # 单只止损
                pnl_pct = (price - pos["avg_cost"]) / pos["avg_cost"]
                if pnl_pct <= self.single_stop_loss:
                    codes_to_sell.append((code, "stop_loss"))

                # 投票退出
                vote = vote_pivot.loc[vote_pivot.index == dt, code]
                if not vote.empty and vote.values[0] <= self.exit_threshold:
                    codes_to_sell.append((code, "vote_exit"))

            # 去重
            sell_codes = []
            seen = set()
            for code, reason in codes_to_sell:
                if code not in seen:
                    sell_codes.append((code, reason))
                    seen.add(code)

            # ── 2. 执行卖出 ──
            for code, reason in sell_codes:
                pos = positions[code]
                if code not in close_pivot.columns:
                    continue
                sell_price_row = close_pivot.loc[close_pivot.index == dt, code]
                if sell_price_row.empty:
                    continue
                sell_price = sell_price_row.values[0]
                if pd.isna(sell_price) or sell_price <= 0:
                    continue

                sell_value = pos["shares"] * sell_price
                cost = self._calc_cost(sell_value, is_sell=True)
                cash += sell_value - cost

                pnl_pct = (sell_price - pos["avg_cost"]) / pos["avg_cost"]

                trades.append({
                    "date": str(dt_date),
                    "code": code,
                    "action": "sell",
                    "reason": reason,
                    "price": round(float(sell_price), 2),
                    "shares": pos["shares"],
                    "value": round(float(sell_value), 2),
                    "pnl_pct": round(float(pnl_pct * 100), 2),
                    "entry_date": pos["entry_date"],
                })

                del positions[code]

            # ── 3. 检查组合止损 ──
            equity_now = cash + self._calc_position_value(
                positions, close_pivot, dt
            )
            portfolio_pnl = (equity_now - initial_capital) / initial_capital
            if portfolio_pnl <= self.portfolio_stop and positions:
                # 清仓所有持仓
                for code in list(positions.keys()):
                    pos = positions[code]
                    if code not in close_pivot.columns:
                        continue
                    sp_row = close_pivot.loc[close_pivot.index == dt, code]
                    if sp_row.empty:
                        continue
                    sp = sp_row.values[0]
                    if pd.isna(sp) or sp <= 0:
                        continue

                    sv = pos["shares"] * sp
                    cost = self._calc_cost(sv, is_sell=True)
                    cash += sv - cost

                    trades.append({
                        "date": str(dt_date),
                        "code": code,
                        "action": "sell",
                        "reason": "portfolio_stop",
                        "price": round(float(sp), 2),
                        "shares": pos["shares"],
                        "value": round(float(sv), 2),
                        "pnl_pct": round(float((sp - pos["avg_cost"]) / pos["avg_cost"] * 100), 2),
                        "entry_date": pos["entry_date"],
                    })
                positions.clear()

            # ── 4. 检查买入信号 ──
            available_slots = self.max_positions - len(positions)
            if available_slots > 0:
                # 扫描所有有投票信号的股票
                vote_row = vote_pivot.loc[vote_pivot.index == dt]
                if not vote_row.empty:
                    buy_candidates = []
                    for code in vote_row.columns:
                        if code in positions:
                            continue  # 已持仓
                        vote_val = vote_row[code].values[0]
                        if vote_val >= self.entry_threshold:
                            # 确认有收盘价
                            cp_row = close_pivot.loc[close_pivot.index == dt, code]
                            if not cp_row.empty and not pd.isna(cp_row.values[0]) and cp_row.values[0] > 0:
                                buy_candidates.append((code, vote_val, cp_row.values[0]))

                    # 按投票强度降序排列（信号越强越优先）
                    buy_candidates.sort(key=lambda x: x[1], reverse=True)
                    buy_candidates = buy_candidates[:available_slots]

                    for code, vote_val, buy_price in buy_candidates:
                        # 等权分配
                        position_capital = cash / (available_slots + len(positions) or 1)
                        # 实际分配到当前可用槽位
                        alloc = min(position_capital, cash * 0.2)  # 单只最多20%
                        if alloc < buy_price * 100:
                            continue  # 资金不足买一手

                        shares = int(alloc / buy_price / 100) * 100  # 整手
                        if shares == 0:
                            continue

                        buy_value = shares * buy_price
                        cost = self._calc_cost(buy_value, is_sell=False)
                        if buy_value + cost > cash:
                            continue

                        cash -= (buy_value + cost)

                        positions[code] = {
                            "shares": shares,
                            "avg_cost": buy_price,
                            "entry_date": str(dt_date),
                        }

                        trades.append({
                            "date": str(dt_date),
                            "code": code,
                            "action": "buy",
                            "reason": f"vote_{vote_val}",
                            "price": round(float(buy_price), 2),
                            "shares": shares,
                            "value": round(float(buy_value), 2),
                            "pnl_pct": 0,
                            "entry_date": str(dt_date),
                        })

            # ── 5. 记录当日权益 ──
            equity_now = cash + self._calc_position_value(
                positions, close_pivot, dt
            )
            equity_history[dt] = equity_now

            # 组合止损已触发则停止
            if portfolio_pnl <= self.portfolio_stop and not positions:
                pass  # 可以继续记录净值，但不再交易

            prev_equity = equity_now

        equity_series = pd.Series(equity_history)
        equity_series.index = pd.to_datetime(equity_series.index)
        return equity_series, trades

    def _calc_position_value(self, positions: Dict, close_pivot: pd.DataFrame,
                             dt) -> float:
        """计算当前持仓市值"""
        value = 0.0
        for code, pos in positions.items():
            if code not in close_pivot.columns:
                continue
            row = close_pivot.loc[close_pivot.index == dt, code]
            if row.empty:
                continue
            price = row.values[0]
            if pd.isna(price):
                continue
            value += pos["shares"] * price
        return value

    def _calc_cost(self, value: float, is_sell: bool = False) -> float:
        """计算交易成本"""
        cost = value * self.commission_rate
        if is_sell:
            cost += value * self.stamp_duty_rate
        cost += value * self.slippage_rate
        return cost

    # ─── 报告生成 ──────────────────────────────────────────

    def _build_report(self,
                      equity_series: pd.Series,
                      trades: list[Dict],
                      initial_capital: float,
                      start_date: date,
                      end_date: date,
                      ) -> BacktestReport:
        """构建标准回测报告"""
        if equity_series.empty:
            raise ValueError("净值曲线为空")

        final_equity = equity_series.iloc[-1]
        total_return = (final_equity / initial_capital - 1) * 100

        trading_days = len(equity_series)
        years = trading_days / 250
        annual_return = (
            ((1 + total_return / 100) ** (1 / years) - 1) * 100
            if years > 0 and total_return > -100 else 0
        )

        daily_returns = equity_series.pct_change().dropna()
        # PR2.2: 委托给 metrics.performance (消除硬编码 risk_free=0.02 等)
        sharpe = _sharpe_ratio(daily_returns.values, risk_free=0.02)
        max_drawdown = _max_drawdown(equity_series.values)
        annual_vol = _volatility(daily_returns.values)
        win_rate = _win_rate(daily_returns.values)
        profit_factor = _profit_factor(daily_returns.values)

        calmar = annual_return / abs(max_drawdown) if max_drawdown != 0 else 0

        benchmark_return = self._calc_benchmark(start_date, end_date)
        excess_return = total_return - benchmark_return

        equity_curve = [
            {"date": str(dt.date()) if hasattr(dt, 'date') else str(dt),
             "equity": round(float(eq), 2)}
            for dt, eq in equity_series.items()
        ]

        buy_trades = [t for t in trades if t["action"] == "buy"]
        sell_trades = [t for t in trades if t["action"] == "sell"]
        winning_trades = [t for t in sell_trades if t.get("pnl_pct", 0) > 0]
        trade_win_rate = (
            len(winning_trades) / len(sell_trades) * 100
            if sell_trades else 0
        )

        monthly_returns = {}
        for dt, eq in equity_series.items():
            dt_date = dt.date() if hasattr(dt, 'date') else dt
            key = f"{dt_date.year}-{dt_date.month:02d}"
            monthly_returns[key] = round(float(eq), 2)

        return BacktestReport(
            strategy_name="技术指标投票模型",
            stock_code="VOTING",
            stock_name="技术投票模型",
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
            total_trades=len(buy_trades) + len(sell_trades),
            annual_volatility=round(annual_vol, 2),
            calmar_ratio=round(calmar, 2),
            benchmark_return=round(benchmark_return, 2),
            excess_return=round(excess_return, 2),
            equity_curve=equity_curve,
            trades_detail=trades,
            monthly_returns=monthly_returns,
            cost_config={
                "commission_rate": self.commission_rate,
                "stamp_duty_rate": self.stamp_duty_rate,
                "slippage_rate": self.slippage_rate,
                "entry_threshold": self.entry_threshold,
                "exit_threshold": self.exit_threshold,
                "max_positions": self.max_positions,
                "single_stop_loss": self.single_stop_loss,
                "portfolio_stop": self.portfolio_stop,
                "note": "技术投票模型: 5策略投票委员会，总分≥+2买入/≤-2卖出",
            }
        )

    def _calc_benchmark(self, start: date, end: date) -> float:
        """计算基准(沪深300)同期收益"""
        try:
            bench = self.repo.get_benchmark_data(self.benchmark_code, start, end)
            if bench.empty:
                return 0
            return (bench["close"].iloc[-1] / bench["close"].iloc[0] - 1) * 100
        except Exception:
            return 0
