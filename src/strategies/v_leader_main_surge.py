"""
V龙头 主升浪选股策略 — 月度高胜率候选池
====================================

基于近 2 年 A 股强势票样本，每日日 K 提取多维特征，
训练 XGBoost 模型预测未来 60 日大涨概率，输出月度调仓候选池。

设计思路:
  - 样本：近 2 年涨幅前列的强势票（涨幅 > 60% 或 ret_60d > 30%）
  - 特征：8 维评分 + 技术指标 + 滞后特征 + 评分动量 + 行业 one-hot
  - 标签：ret_60d > 10% (识别主升浪)
  - 划分：时间序列（18 个月训练 → 6 个月验证 → 滚动）
  - 模型：XGBoost v4 (AUC 0.7912, 见 scripts/train_xgb_v4.py)

架构 (参考 vnpy.alpha 三段式):
  - src/data/xgb_loader.py      → XgbV4Model (模型加载/预测, 类似 AlphaModel)
  - src/strategies/v_leader_features.py → FeatureBuilder (特征工程, 类似 AlphaDataset)
  - 本文件                        → 策略类 (类似 AlphaStrategy)
  策略不感知 XGBoost 细节, 通过 loader 接口调用。

调度流程（月度）:
  T0 (每月初): 加载当月全市场 → 提取特征 → XGBoost 预测 → top 30 → 候选池
  T1+ (每日) : 候选池内叠加 V6 反转信号 → 实际入场

来源文献:
  - Fama-French (1993) 三因子模型 (SMB/HML)
  - Jegadeesh (1990) 动量/反转
  - XGBoost v4 增强 (本项目实证, AUC 0.7912)
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

from ..backtest.base_selection_strategy import BaseSelectionStrategy
from ..db.engine import get_engine

# 懒导入: 避免在策略 import 时强依赖 xgboost/Scoring


class VLeaderMainSurgeStrategy(BaseSelectionStrategy):
    """
    V龙头 主升浪 — 月度高胜率候选池

    信号来源：XGBoost v4 (scripts/train_xgb_v4.py)
    调仓周期：月度 (rebalance_days=20 约 1 个月)
    输出数量：top N (候选池，叠加 V6 触发实际入场)
    行业暴露：单行业不超过 max_sector_pct
    """

    name: str = "V龙头主升"
    description: str = (
        "V龙头 主升浪 — 8维评分 + 技术指标 + 滞后特征 + 行业 one-hot → "
        "XGBoost v4 月度预测 → 高胜率候选池 + 行业分散"
    )
    source: str = (
        "近 2 年强势票样本 + XGBoost v4 (AUC 0.7912) + "
        "Fama-French(1993) + Jegadeesh(1990)"
    )

    # ── 选股参数 ──
    n_stocks: int = 30              # 候选池大小
    rebalance_days: int = 20         # 月度调仓
    lookback_days: int = 60          # 特征提取回看

    # ── 流动性过滤 ──
    min_amount_wan: float = 3000.0   # 最低日均成交 3000 万
    exclude_st: bool = True

    # ── 行业暴露控制 ──
    max_sector_pct: float = 0.30     # 单行业不超过 30% (e.g. n=30 → 每行业最多 9)

    # ── 模型配置 ──
    # 实际模型产物在 data/ 下, 与 train_xgb_v4.py 的 save_model 对应
    ml_model_path: str = "data/xgb_model.json"
    ml_scaler_path: str = "data/xgb_scaler.json"

    # ── 缓存 (类级别共享, 按 model_path 区分以支持多配置) ──
    _class_model: dict = {}  # {(class, model_path): XgbV4Model}
    _class_builder: dict = {}  # {class: FeatureBuilder}
    _class_load_failed: set = set()  # 加载失败的 (class, model_path) 集合

    def __init__(self, **kwargs):
        # 支持 BaseSelectionStrategy 的属性注入 (n_stocks / rebalance_days 等)
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)

        # 引擎: 优先用父类注册的, 否则用全局
        try:
            self.engine = get_engine()
        except Exception:
            self.engine = None

        # 懒加载标志 (按 model_path 区分, 多实例/多配置不互相干扰)
        self._model_load_attempted: bool = False
        cache_key = (self.__class__, self.ml_model_path)
        self._model_load_failed: bool = (
            cache_key in VLeaderMainSurgeStrategy._class_load_failed
        )

    # ================================================================
    #  样本构建 (训练用, 见 scripts/train_xgb_v4.py)
    # ================================================================

    @staticmethod
    def label_bull_stocks(
        df: pd.DataFrame,
        ret_col: str = "ret_60d",
        threshold: float = 10.0,
    ) -> pd.Series:
        """
        标注强势票标签：ret_60d > threshold

        Args:
            df: 包含 ret_col 列的 DataFrame
            ret_col: 收益列名
            threshold: 涨幅阈值 (%)

        Returns:
            Series[bool], True = 强势票
        """
        return (df[ret_col] > threshold).astype(int)

    # ================================================================
    #  模型加载 (懒加载, 失败容错)
    # ================================================================

    def _load_ml_model(self):
        """
        懒加载 XGBoost v4 模型 + Scaler

        失败容错: 模型文件不存在/损坏 → 设 _model_load_failed,
        后续 _predict 直接返回空 DataFrame, 策略优雅降级 (返回空候选池)。

        缓存策略: 按 (class, model_path) 共享, 多策略实例共用同一模型
        (但不同 model_path 的实例各自独立, 避免配置干扰)
        """
        if self._model_load_attempted:
            return None if self._model_load_failed else self._model

        self._model_load_attempted = True

        # 类级别缓存: 同 (class, model_path) 共享
        cache_key = (self.__class__, self.ml_model_path)
        if cache_key in VLeaderMainSurgeStrategy._class_model:
            self._model = VLeaderMainSurgeStrategy._class_model[cache_key]
            return self._model

        try:
            from src.data.xgb_loader import XgbV4Model
            self._model = XgbV4Model.load(
                Path(self.ml_model_path).resolve(),
                Path(self.ml_scaler_path).resolve(),
            )
            VLeaderMainSurgeStrategy._class_model[cache_key] = self._model
        except Exception as e:
            print(f"  [V龙头] 模型加载失败, 跳过: {e}")
            self._model_load_failed = True
            VLeaderMainSurgeStrategy._class_load_failed.add(cache_key)
            self._model = None
        return self._model

    @property
    def _model(self):
        """代理访问 (避免 __init__ 没显式声明, 兼容旧代码)"""
        return getattr(self, "__model", None)

    @_model.setter
    def _model(self, value):
        object.__setattr__(self, "__model", value)

    # ================================================================
    #  特征构建 (委托 FeatureBuilder, 策略不感知细节)
    # ================================================================

    def _get_feature_builder(self):
        """获取/懒建 FeatureBuilder (类级别共享缓存)"""
        if self.engine is None:
            return None
        class_id = id(self.__class__)
        if class_id in VLeaderMainSurgeStrategy._class_builder:
            return VLeaderMainSurgeStrategy._class_builder[class_id]
        try:
            from .v_leader_features import FeatureBuilder
            builder = FeatureBuilder(self.engine)
            VLeaderMainSurgeStrategy._class_builder[class_id] = builder
            return builder
        except Exception as e:
            print(f"  [V龙头] FeatureBuilder 初始化失败: {e}")
            return None

    def _extract_features(
        self, code: str, as_of_date: str,
    ) -> Optional[pd.Series]:
        """
        提取单只股票的多维特征向量 (vnpy.alpha 风格 — 委托给 FeatureBuilder)

        Returns:
            pd.Series 索引为 feature_name, 或 None (模型未加载/数据缺失)
        """
        model = self._load_ml_model()
        builder = self._get_feature_builder()
        if model is None or builder is None:
            return None
        ym = str(as_of_date)[:7]
        row = builder.build_feature_row(
            code, ym, as_of_date, model.feature_names, model.industry_columns,
        )
        if row is None:
            return None
        return pd.Series(row)

    # ================================================================
    #  预测 (主入口, 批量)
    # ================================================================

    def _predict(
        self, codes: List[str], as_of_date: str,
    ) -> pd.DataFrame:
        """
        对候选股票运行 XGBoost 预测, 返回概率 + 排名

        Args:
            codes: 候选股票代码列表
            as_of_date: 基准日期 (YYYY-MM-DD)

        Returns:
            DataFrame[code, predict_proba, rank] 按 predict_proba 降序
        """
        empty = pd.DataFrame(columns=["code", "predict_proba", "rank"])
        if not codes:
            return empty

        model = self._load_ml_model()
        if model is None:
            return empty

        builder = self._get_feature_builder()
        if builder is None:
            return empty

        ym = str(as_of_date)[:7]
        try:
            X, valid_codes = builder.build_batch_matrix(
                codes, ym, as_of_date,
                model.feature_names, model.industry_columns,
            )
        except Exception as e:
            print(f"  [V龙头] 特征构建失败: {e}")
            return empty

        if X.shape[0] == 0:
            return empty

        try:
            proba = model.predict_proba(X)  # (n,)
        except Exception as e:
            print(f"  [V龙头] 预测失败: {e}")
            return empty

        df = pd.DataFrame({
            "code": valid_codes,
            "predict_proba": proba,
        }).sort_values("predict_proba", ascending=False).reset_index(drop=True)
        df["rank"] = df.index + 1
        return df

    # ================================================================
    #  候选池构建 (top N + 行业分散)
    # ================================================================

    def _get_industries(self, codes: List[str]) -> dict[str, str]:
        """批量查 industry (委托 FeatureBuilder)"""
        builder = self._get_feature_builder()
        if builder is None:
            return {c: "其他" for c in codes}
        return builder._load_industries(list(codes))

    def _build_pool(
        self, predictions: pd.DataFrame, as_of_date: str,
    ) -> List[str]:
        """
        top N + 行业分散 (max_sector_pct)

        策略:
          1. 按 predict_proba 降序遍历
          2. 维护 industry_count 字典
          3. 当某行业已选满 (>= max_per_industry) 时跳过
          4. 选满 n_stocks 返回

        失败容错: 无 predictions 或行业查询失败 → 仅按 proba 取 top N
        """
        if predictions.empty:
            return []
        if self.max_sector_pct >= 1.0:
            return predictions.head(self.n_stocks)["code"].tolist()

        codes = predictions["code"].tolist()
        try:
            industries = self._get_industries(codes)
        except Exception as e:
            print(f"  [V龙头] 行业查询失败, 退回 top N: {e}")
            return predictions.head(self.n_stocks)["code"].tolist()

        max_per_industry = max(1, int(self.n_stocks * self.max_sector_pct))
        selected: list[str] = []
        industry_count: dict[str, int] = {}

        for _, row in predictions.iterrows():
            code = row["code"]
            ind = industries.get(code, "其他")
            if industry_count.get(ind, 0) >= max_per_industry:
                continue
            selected.append(code)
            industry_count[ind] = industry_count.get(ind, 0) + 1
            if len(selected) >= self.n_stocks:
                break

        return selected

    # ================================================================
    #  选股 (BaseSelectionStrategy 接口)
    # ================================================================

    def select(
        self, rebalance_date, universe_df: pd.DataFrame,
    ) -> List[str]:
        """
        月度选股 — 输出 V龙头 候选池 (top N + 行业分散)

        流程:
          1. universe 流动性/ST 过滤 (BaseSelectionStrategy.filter_universe)
          2. XGBoost 批量预测 → predict_proba 排序
          3. 行业分散约束 (单行业 ≤ max_sector_pct)
          4. 取 top n_stocks

        Returns:
            List[str], 候选股票代码列表
        """
        if universe_df is None or universe_df.empty:
            return []

        date_str = (
            str(rebalance_date.date())[:10]
            if hasattr(rebalance_date, "date")
            else str(rebalance_date)[:10]
        )

        # Step 1: 流动性 + ST 过滤
        filtered = self.filter_universe(universe_df)
        if filtered.empty:
            return []

        # Step 2: XGBoost 预测 (内部处理模型加载失败, 返回空时自然走完 0 候选)
        predictions = self._predict(
            filtered["code"].astype(str).tolist(), date_str,
        )
        if predictions.empty:
            return []

        # Step 3+4: 行业分散 + top N
        return self._build_pool(predictions, date_str)

    # ================================================================
    #  预计算钩子 (供 walk_forward.py 加速)
    # ================================================================

    def precompute_all(self, start_date, end_date):
        """
        按 (code, ym) 预热特征缓存 — walk_forward.py 自动调用

        流程:
          1. 拿 start_date..end_date 期间所有调仓日
          2. 每个调仓日对当月 universe 跑 _predict (触发 FeatureBuilder 缓存)
          3. 后续 select() 直接走缓存

        注意: 本钩子**只预热**, 不改变 _predict 行为.
        兼容 walk_forward.py: 同时填充 self._indicator_cache (按日期, 用于 walk_forward 日志)
        """
        from sqlalchemy import text

        if self.engine is None:
            return

        try:
            sql = """
                SELECT DISTINCT trade_date FROM daily_price
                WHERE trade_date >= :start AND trade_date <= :end
                ORDER BY trade_date
            """
            with self.engine.connect() as conn:
                rows = conn.execute(
                    text(sql),
                    {"start": str(start_date), "end": str(end_date)},
                ).fetchall()
            dates = [r[0] for r in rows]
        except Exception as e:
            print(f"  [V龙头 precompute] 加载交易日失败: {e}")
            return

        if not dates:
            return

        # 取每月最后一个交易日 (月度调仓)
        months: dict[str, str] = {}
        for d in dates:
            ym = str(d)[:7]
            if ym not in months or str(d) > months[ym]:
                months[ym] = str(d)[:10]
        rebalance_dates = sorted(months.values())

        print(f"  [V龙头 precompute] 预热 {len(rebalance_dates)} 个月度调仓日")

        model = self._load_ml_model()
        builder = self._get_feature_builder()
        if model is None or builder is None:
            return

        # 兼容 walk_forward 的 _indicator_cache 命名 (按日期索引)
        self._indicator_cache: dict[str, dict] = {}

        for i, as_of in enumerate(rebalance_dates):
            ym = as_of[:7]
            # 取当月 universe (流动性过滤前, 简化: 用全市场样本)
            try:
                sql_u = """
                    SELECT DISTINCT code FROM daily_price
                    WHERE trade_date = :d
                    LIMIT 300
                """
                with self.engine.connect() as conn:
                    rows = conn.execute(text(sql_u), {"d": as_of}).fetchall()
                codes = [str(r[0]).zfill(6) for r in rows]
            except Exception:
                codes = []

            if not codes:
                continue

            try:
                X, valid = builder.build_batch_matrix(
                    codes, ym, as_of,
                    model.feature_names, model.industry_columns,
                )
                # 记录每个调仓日的预测结果 (供 walk_forward 日志使用)
                self._indicator_cache[as_of] = {
                    "n_codes": len(codes),
                    "n_valid": len(valid),
                    "ym": ym,
                }
            except Exception as e:
                print(f"  [V龙头 precompute] {as_of} 失败: {e}")
                continue

            if (i + 1) % 5 == 0:
                print(f"  [V龙头 precompute] {i + 1}/{len(rebalance_dates)} 完成")

        print(
            f"  [V龙头 precompute] 完成: {len(builder._dim_cache)} 个 (code, ym) 已缓存, "
            f"{len(self._indicator_cache)} 个调仓日"
        )

    # ================================================================
    #  __main__ 自测入口
    # ================================================================


def _self_test(date_str: str = "2026-05-30", n: int = 50):
    """
    单策略自测: 对指定日期取 n 只样本股票, 走通 select 全链路

    用法: python -m src.strategies.v_leader_main_surge --selftest
    """
    print(f"=== V龙头 自测 @ {date_str} (取 {n} 只样本) ===")
    engine = get_engine()
    from sqlalchemy import text

    rows = None
    # 1. 优先: 当日有数据的股票 + 对应 stock_basic 信息
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT b.code, b.name, d.close, d.amount / 1e4 AS avg_amount_wan "
                    "FROM daily_price d "
                    "JOIN stock_basic b ON b.code = d.code "
                    "WHERE d.trade_date = :d "
                    "ORDER BY d.amount DESC LIMIT :n"
                ),
                {"d": date_str, "n": n},
            ).fetchall()
    except Exception:
        pass

    # 2. 兜底: 任意交易日 + 聚合 20 日均成交
    if not rows:
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT b.code, b.name, d.close, "
                        "AVG(d.amount) / 1e4 AS avg_amount_wan "
                        "FROM daily_price d "
                        "JOIN stock_basic b ON b.code = d.code "
                        "WHERE d.trade_date >= :start "
                        "GROUP BY b.code, b.name "
                        "ORDER BY avg_amount_wan DESC LIMIT :n"
                    ),
                    {"start": "2026-01-01", "n": n},
                ).fetchall()
        except Exception:
            pass

    # 3. 最后兜底: 只取 code + close
    if not rows:
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT b.code, b.name, d.close "
                        "FROM daily_price d JOIN stock_basic b ON b.code = d.code "
                        "ORDER BY d.trade_date DESC LIMIT :n"
                    ),
                    {"n": n},
                ).fetchall()
        except Exception as e:
            print(f"取样本失败: {e}")

    if not rows:
        print("FAIL: 无样本数据 (数据库可能为空)")
        return

    universe = pd.DataFrame(rows, columns=[
        c if i < len(rows[0]) else None
        for i, c in enumerate(["code", "name", "close", "avg_amount_wan"])
    ])
    # 兼容 3 列返回
    if "avg_amount_wan" not in universe.columns:
        universe["avg_amount_wan"] = 100000.0  # 兜底全过流动性
    if "close" not in universe.columns:
        universe["close"] = 10.0
    if "name" not in universe.columns:
        universe["name"] = "未知"
    universe["code"] = universe["code"].astype(str).str.zfill(6)
    universe["market_cap_yi"] = 100.0  # 兜底

    print(f"样本 {len(universe)} 只, 含 avg_amount_wan 列: {'avg_amount_wan' in universe.columns}")

    strategy = VLeaderMainSurgeStrategy(n_stocks=10, max_sector_pct=0.30)
    selected = strategy.select(date_str, universe)
    print(f"选中 {len(selected)} 只: {selected[:10]}{'...' if len(selected) > 10 else ''}")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        idx = sys.argv.index("--selftest")
        date_arg = "2026-05-30"
        if idx + 1 < len(sys.argv):
            date_arg = sys.argv[idx + 1]
        _self_test(date_arg)
    else:
        print("用法: python -m src.strategies.v_leader_main_surge --selftest [YYYY-MM-DD]")


# ── 业务名别名 (LIVE_TRADING_ROADMAP.md 命名规范化) ─────────
VLeaderStrategy = VLeaderMainSurgeStrategy
