"""
选股策略基类
============

与 BaseStrategy (单股信号策略) 并行的组合选股策略基类。

核心区别:
- BaseStrategy: 继承 backtesting.Strategy，每只股票单独运行 init()/next()
- BaseSelectionStrategy: 从全市场股票池中选股，等权持有，定期调仓

子类只需实现 select() 方法，返回选中的股票代码列表。

策略来源文献标注在策略类的 docstring 中，确保可考证。
"""
from __future__ import annotations


import pandas as pd


class BaseSelectionStrategy:
    """
    选股策略基类 — 从股票池中选股，等权组合，定期调仓

    子类只需实现:
    - select(date, universe_df) → list[str]

    策略元信息 (必须覆盖):
    - name: str        策略名称
    - description: str 策略描述
    - source: str      策略来源 (文献/论文引用)

    可配置参数:
    - n_stocks:        选股数量 (默认20)
    - rebalance_days:  调仓周期，交易日 (默认20，约1个月)
    - lookback_days:   因子回看天数 (默认20)
    - min_amount_wan:  最低日均成交额(万元)，流动性过滤 (默认3000)
    - exclude_st:      是否剔除ST股票 (默认True)
    """

    # 子类必须覆盖的属性
    name: str = "base_selection"
    description: str = "选股策略基类"
    source: str = ""

    # 可配置参数
    n_stocks: int = 20
    rebalance_days: int = 20
    lookback_days: int = 20
    min_amount_wan: float = 3000.0   # 日均成交额门槛(万元)
    exclude_st: bool = True

    def select(self, rebalance_date, universe_df: pd.DataFrame) -> list[str]:
        """
        选股方法 — 子类必须实现

        Args:
            rebalance_date: 当前调仓日
            universe_df: 股票池数据 DataFrame，列包括:
                - code: 股票代码
                - name: 股票名称
                - close: 最新收盘价
                - avg_amount_wan: 近N日日均成交额(万元)
                - avg_turnover: 近N日日均换手率(%)
                - market_cap_yi: 估算总市值(亿元)
                - return_Nd: 近N日累计收益率
                - volatility_Nd: 近N日收益率标准差

        Returns:
            选中的股票代码列表 (最多 n_stocks 只)
        """
        raise NotImplementedError("子类必须实现 select() 方法")

    def filter_universe(self, universe_df: pd.DataFrame) -> pd.DataFrame:
        """
        通用过滤: 流动性门槛 + ST剔除

        子类可覆盖此方法添加额外过滤逻辑
        """
        df = universe_df.copy()

        # 1. 流动性过滤: 日均成交额 >= min_amount_wan 万元
        if "avg_amount_wan" in df.columns:
            df = df[df["avg_amount_wan"] >= self.min_amount_wan]

        # 2. 剔除ST
        if self.exclude_st and "name" in df.columns:
            df = df[~df["name"].str.contains("ST", na=False)]

        # 3. 剔除数据不完整的股票 (市值为负或为零)
        if "market_cap_yi" in df.columns:
            df = df[df["market_cap_yi"] > 0]

        return df
