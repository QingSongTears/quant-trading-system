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
    - sector_cap_pct:  单行业最大权重占比 (T3.2, 默认0.30, None=不限制)
    - sector_max_count: 单行业最大持股数 (T3.2, 默认None=不限制)
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

    # T3.2: 行业暴露约束 (默认启用 30% 上限, 关闭设 None)
    sector_cap_pct: float | None = 0.30
    sector_max_count: int | None = None

    def select(self, rebalance_date, universe_df: pd.DataFrame) -> list[str]:
        """
        选股方法 — 子类必须实现

        注意 (T3.2): 子类应覆盖此方法时, 在末尾调用
        `self.apply_sector_constraint()` 或
        `self._select_with_sector_cap()` 以应用行业暴露约束。
        或者直接在 portfolio_engine 的 _simulate_portfolio
        流水线里通过 `apply_sector_constraint` 后置过滤。

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

    def select_with_sector_cap(
        self, rebalance_date, universe_df: pd.DataFrame
    ) -> list[str]:
        """T3.2 辅助: 选股 + 行业约束 (模板方法, 子类可重写 select())

        用法: 子类的 select() 末尾 return self.select_with_sector_cap(date, df)

        流程:
            1. 调 self.select() 获取候选
            2. 若 universe 有 industry 列, 直接套约束
            3. 否则从 DB 批量加载 industry
            4. 行业约束 reject 后, 候选不足时尝试补全

        Returns:
            通过行业暴露约束的最终选股代码列表
        """
        from ._sector_pipeline import select_with_constraint
        return select_with_constraint(self, rebalance_date, universe_df)

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

    def apply_sector_constraint(
        self, candidates: pd.DataFrame
    ) -> tuple[list[str], dict[str, list[str]]]:
        """T3.2: 应用行业暴露约束 (在 select() 选股前调用)

        Args:
            candidates: 含 'code' 和可选 'industry' 列的候选股 DataFrame
                        若无 'industry' 列, 会自动从 DB 加载

        Returns:
            (selected_codes, rejected_by_industry)
            selected_codes: 通过约束的股票代码列表
            rejected_by_industry: {industry: [code, ...]} 被拒股票明细
        """
        if self.sector_cap_pct is None and self.sector_max_count is None:
            # 约束关闭: 退化到 n_stocks 限制
            return candidates["code"].astype(str).head(self.n_stocks).tolist(), {}

        from src.selection.sector_constraint import (
            SectorConstraint,
            apply_sector_cap,
        )

        df = candidates.copy()
        # 自动加载 industry (若 universe 没有)
        if "industry" not in df.columns and "code" in df.columns:
            from src.selection.sector_constraint import load_industry_map, get_engine
            try:
                engine = get_engine()
                ind_map = load_industry_map(engine, df["code"].astype(str).tolist())
                df["industry"] = df["code"].astype(str).map(ind_map).fillna("未知")
            except Exception:
                df["industry"] = "未知"

        constraint = SectorConstraint(
            max_pct=self.sector_cap_pct or 1.0,
            max_count=self.sector_max_count,
        )
        result = apply_sector_cap(df, constraint, target_n=self.n_stocks)
        return result.selected, result.rejected_by_industry
