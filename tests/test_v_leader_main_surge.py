"""
V龙头 主升浪 — 单元测试
========================

覆盖:
  - XgbV4Model 加载/预测/错误处理
  - FeatureBuilder 特征工程 (pct_rank/缓存/行业 one-hot)
  - VLeaderMainSurgeStrategy select() 端到端 + 边界情况
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.data.xgb_loader import (
    XgbV4Model,
    XgbV4LoadError,
    get_xgb_v4,
    clear_xgb_v4_cache,
)
from src.strategies.v_leader_features import (
    DIM_COLS,
    DIM_TO_SCORER,
    FeatureBuilder,
    TECH_COLS,
    TECH_DEFAULTS,
    _month_end,
    _prev_month,
    pct_rank,
)


# ============================================================
#  工具函数
# ============================================================


class TestPctRank:
    def test_median(self):
        """中位数排名应为 0.5"""
        assert pct_rank([1, 2, 3, 4, 5], 3) == 0.5

    def test_lowest(self):
        """最小值排名应为 0.1 (0+0.5/5)"""
        result = pct_rank([1, 2, 3, 4, 5], 1)
        assert result == 0.1

    def test_highest(self):
        """最大值排名应为 0.9 (4+0.5/5)"""
        result = pct_rank([1, 2, 3, 4, 5], 5)
        assert result == 0.9

    def test_empty(self):
        """空列表返回 0.5 (默认值)"""
        assert pct_rank([], 5) == 0.5

    def test_ties(self):
        """重复值: (0+0.5*2) / 3 = 1/3"""
        result = pct_rank([1, 1, 3], 1)
        assert abs(result - 1 / 3) < 1e-6


class TestDateUtils:
    def test_month_end_mid(self):
        assert _month_end("2026-05-15") == "2026-05-31"

    def test_month_end_dec(self):
        assert _month_end("2026-12-01") == "2026-12-31"

    def test_month_end_feb_non_leap(self):
        assert _month_end("2025-02-10") == "2025-02-28"

    def test_prev_month_normal(self):
        assert _prev_month("2026-05") == "2026-04"

    def test_prev_month_january(self):
        assert _prev_month("2026-01") == "2025-12"

    def test_prev_month_invalid(self):
        assert _prev_month("bad") is None


# ============================================================
#  XgbV4Model
# ============================================================

_REAL_MODEL = _PROJECT_ROOT / "data" / "xgb_model.json"
_REAL_SCALER = _PROJECT_ROOT / "data" / "xgb_scaler.json"


@pytest.mark.skipif(
    not _REAL_MODEL.exists() or not _REAL_SCALER.exists(),
    reason="data/xgb_model.json 或 data/xgb_scaler.json 不存在",
)
class TestXgbV4ModelReal:
    """真实模型产物测试 (依赖 data/xgb_*.json)"""

    def setup_method(self):
        clear_xgb_v4_cache()

    def test_load_succeeds(self):
        m = XgbV4Model.load(_REAL_MODEL, _REAL_SCALER)
        assert m.booster is not None
        assert m.scaler is not None
        assert len(m.feature_names) == 74
        assert len(m.industry_columns) == 33

    def test_predict_proba_shape(self):
        m = XgbV4Model.load(_REAL_MODEL, _REAL_SCALER)
        X = np.random.RandomState(42).rand(5, 74).astype(np.float32)
        proba = m.predict_proba(X)
        assert proba.shape == (5,)
        assert (proba >= 0).all() and (proba <= 1).all()

    def test_predict_proba_1d_input(self):
        """1D 输入会被 reshape 为 2D"""
        m = XgbV4Model.load(_REAL_MODEL, _REAL_SCALER)
        X = np.random.RandomState(42).rand(74).astype(np.float32)
        proba = m.predict_proba(X)
        assert proba.shape == (1,)

    def test_predict_proba_wrong_feature_count_raises(self):
        m = XgbV4Model.load(_REAL_MODEL, _REAL_SCALER)
        with pytest.raises(ValueError, match="X 列数"):
            m.predict_proba(np.zeros((1, 5), dtype=np.float32))

    def test_singleton(self):
        """get_xgb_v4 返回同一实例"""
        m1 = get_xgb_v4()
        m2 = get_xgb_v4()
        assert m1 is m2

    def test_reload_returns_fresh(self):
        """reload=True 返回新实例"""
        m1 = get_xgb_v4()
        m2 = get_xgb_v4(reload=True)
        assert m1 is not m2

    def test_industry_columns_prefix(self):
        """所有 industry_columns 必须以 ind_ 开头"""
        m = XgbV4Model.load(_REAL_MODEL, _REAL_SCALER)
        for col in m.industry_columns:
            assert col.startswith("ind_"), f"行业列名 {col!r} 不以 ind_ 开头"


class TestXgbV4ModelErrors:
    """错误路径测试 (不需要真实文件)"""

    def test_load_missing_model_raises(self, tmp_path):
        scaler = tmp_path / "sc.json"
        scaler.write_text(json.dumps({
            "mean": [0.0, 0.0, 0.0],
            "scale": [1.0, 1.0, 1.0],
            "n_features": 3,
            "feature_names": ["a", "b", "c"],
        }))
        with pytest.raises(XgbV4LoadError, match="模型文件不存在"):
            XgbV4Model.load(tmp_path / "missing.json", scaler)

    def test_load_missing_scaler_raises(self, tmp_path):
        with pytest.raises(XgbV4LoadError):
            XgbV4Model.load(tmp_path / "m.json", tmp_path / "missing_sc.json")

    def test_load_malformed_scaler_raises(self, tmp_path):
        bad_sc = tmp_path / "bad.json"
        bad_sc.write_text("{not valid json")
        with pytest.raises(XgbV4LoadError):
            XgbV4Model.load(tmp_path / "m.json", bad_sc)

    def test_load_feature_count_mismatch_raises(self, tmp_path):
        """feature_names 数量 != n_features"""
        sc = tmp_path / "sc.json"
        sc.write_text(json.dumps({
            "mean": [0.0, 0.0, 0.0],
            "scale": [1.0, 1.0, 1.0],
            "n_features": 5,  # 与 feature_names 数量 (3) 不符
            "feature_names": ["a", "b", "c"],
        }))
        # 无对应模型文件, 先造一个
        m = tmp_path / "m.json"
        m.write_text("{}")
        with pytest.raises(XgbV4LoadError, match="Scaler"):
            XgbV4Model.load(m, sc)

    def test_repr_contains_metadata(self):
        """__repr__ 应含关键元信息"""
        # 创建一个最小的合法 XgbV4Model
        from src.data.xgb_scaler import JsonScaler
        sc = JsonScaler([0.0, 0.0], [1.0, 1.0], 2, ["a", "b"])
        m = XgbV4Model(
            booster=xgb_stub_booster(),
            scaler=sc,
            feature_names=["a", "b"],
            industry_columns=[],
        )
        r = repr(m)
        assert "XgbV4Model" in r
        assert "n_features=2" in r


def xgb_stub_booster():
    """构造一个最小的合法 XGBoost Booster (用于 repr 测试)"""
    import xgboost as xgb
    import tempfile
    import os
    # 用最简 XGBoost 训练一个 2 特征的模型
    X = np.array([[0, 0], [0, 1], [1, 0], [1, 1]], dtype=np.float32)
    y = np.array([0, 0, 0, 1], dtype=np.float32)
    dtrain = xgb.DMatrix(X, label=y, feature_names=["a", "b"])
    booster = xgb.train(
        {"objective": "binary:logistic", "verbosity": 0},
        dtrain,
        num_boost_round=2,
    )
    return booster


# ============================================================
#  FeatureBuilder
# ============================================================


class TestFeatureBuilderSchema:
    """schema 一致性测试 (不需要 DB)"""

    def test_dim_cols_count(self):
        # 2026-06-25: lhb_institutional 删除后, DIM_COLS 从 8 维 → 7 维
        assert len(DIM_COLS) == 7

    def test_dim_to_scorer_keys_match_dim_cols(self):
        assert set(DIM_TO_SCORER.keys()) == set(DIM_COLS)

    def test_tech_cols_count(self):
        assert len(TECH_COLS) == 5

    def test_tech_defaults_keys_match_tech_cols(self):
        assert set(TECH_DEFAULTS.keys()) == set(TECH_COLS)


@pytest.mark.skipif(
    not _REAL_MODEL.exists(),
    reason="需要 data/xgb_model.json 用于校验 feature_order",
)
class TestFeatureBuilderIntegration:
    """集成测试 (需要 DB + 真实模型)"""

    def setup_method(self):
        from src.db.engine import get_engine
        try:
            self.engine = get_engine()
        except Exception:
            pytest.skip("无法连接 quant.db")
        self.model = XgbV4Model.load(_REAL_MODEL, _REAL_SCALER)

    def test_build_batch_matrix_shape(self):
        builder = FeatureBuilder(self.engine)
        codes = ["000001", "000002", "600000"]
        X, valid = builder.build_batch_matrix(
            codes, "2026-05", "2026-05-30",
            self.model.feature_names, self.model.industry_columns,
        )
        # 数据缺失的 code 会被过滤, valid 数量 <= len(codes)
        assert X.shape[0] == len(valid)
        if X.shape[0] > 0:
            assert X.shape[1] == 74

    def test_industry_one_hot_at_most_one_true(self):
        """每只股票的 ind_* 列最多 1 个为 1"""
        builder = FeatureBuilder(self.engine)
        codes = ["000001", "000002"]
        X, valid = builder.build_batch_matrix(
            codes, "2026-05", "2026-05-30",
            self.model.feature_names, self.model.industry_columns,
        )
        if X.shape[0] == 0:
            pytest.skip("无可用样本")
        ind_cols = self.model.industry_columns
        ind_idx = [self.model.feature_names.index(c) for c in ind_cols]
        for row in X:
            assert int(row[ind_idx].sum()) <= 1, "行业 one-hot 异常"

    def test_lag_missing_uses_zero(self):
        """无上月数据 → lag 填 0 (与 train_xgb_v4.py 一致)"""
        # 构造一个 FeatureBuilder, 预填本月 + 上月 dim_scores, 强制 lag=0
        builder = FeatureBuilder(self.engine)
        builder._dim_cache = {
            ("000001", "2026-05"): {d: 10.0 for d in DIM_COLS},
            ("000001", "2026-04"): {d: 0.0 for d in DIM_COLS},  # 强制 lag=0
        }
        builder._industry_cache = {"000001": "其他"}

        X, valid = builder.build_batch_matrix(
            ["000001"], "2026-05", "2026-05-30",
            self.model.feature_names, self.model.industry_columns,
        )
        if X.shape[0] == 0:
            pytest.skip("无法构造有效样本")
        # 找 lag 字段在 X 中的列位置
        lag_idx = [i for i, f in enumerate(self.model.feature_names) if f.endswith("_lag1m")]
        assert len(lag_idx) == 8
        for idx in lag_idx:
            val = float(X[0, idx])
            assert val == 0.0, f"lag 字段 {self.model.feature_names[idx]} 应为 0, 实际 {val}"

    def test_industry_cache_reused(self):
        """同 code 第二次查 industry 走缓存"""
        builder = FeatureBuilder(self.engine)
        builder._load_industries(["000001"])
        assert "000001" in builder._industry_cache
        cached_value = builder._industry_cache["000001"]
        # 第二次查, 缓存命中
        builder._load_industries(["000001"])
        assert builder._industry_cache["000001"] == cached_value

    def test_industry_unknown_defaults_to_other(self):
        """不存在的 code → '其他'"""
        builder = FeatureBuilder(self.engine)
        result = builder._load_industries(["999999"])
        assert result["999999"] == "其他"


# ============================================================
#  VLeaderMainSurgeStrategy
# ============================================================


def _make_universe():
    return pd.DataFrame({
        "code": ["000001", "000002", "600000", "600519", "000858",
                 "300750", "000333", "002594", "600276", "601318"],
        "name": ["平安银行", "万科A", "浦发银行", "贵州茅台", "五粮液",
                 "宁德时代", "美的集团", "比亚迪", "恒瑞医药", "中国平安"],
        "close": [12.0, 8.5, 9.2, 1700.0, 165.0, 230.0, 65.0, 250.0, 45.0, 48.0],
        "avg_amount_wan": [50000.0, 80000.0, 30000.0, 100000.0, 90000.0,
                           120000.0, 70000.0, 110000.0, 60000.0, 95000.0],
        "market_cap_yi": [2300.0, 900.0, 2700.0, 21000.0, 6400.0,
                          10300.0, 4400.0, 7000.0, 2900.0, 8700.0],
    })


class TestVLeaderStrategy:
    """策略类测试 (用真实模型 + mock 行业)"""

    def setup_method(self):
        # 重置类级缓存, 避免测试间污染
        from src.strategies import v_leader_main_surge as mod
        mod.VLeaderMainSurgeStrategy._class_model.clear()
        mod.VLeaderMainSurgeStrategy._class_load_failed.clear()

    def test_label_bull_stocks(self):
        """训练标签: ret_60d > 10%"""
        from src.strategies.v_leader_main_surge import VLeaderMainSurgeStrategy
        df = pd.DataFrame({"ret_60d": [5.0, 10.5, 9.9, 100.0]})
        labels = VLeaderMainSurgeStrategy.label_bull_stocks(df)
        assert list(labels) == [0, 1, 0, 1]

    def test_label_bull_stocks_custom_threshold(self):
        from src.strategies.v_leader_main_surge import VLeaderMainSurgeStrategy
        # 严格 > threshold: 20.0 不算, 25.0 才算
        df = pd.DataFrame({"ret_60d": [15.0, 20.0, 25.0]})
        labels = VLeaderMainSurgeStrategy.label_bull_stocks(df, threshold=20.0)
        assert list(labels) == [0, 0, 1]

    def test_select_returns_list(self):
        """select() 返回 list[str]"""
        from src.strategies.v_leader_main_surge import VLeaderMainSurgeStrategy
        s = VLeaderMainSurgeStrategy(n_stocks=5)
        result = s.select("2026-05-30", _make_universe())
        assert isinstance(result, list)
        # 数据可能部分缺失, 至少返回 [] 或 [str, ...]
        for code in result:
            assert isinstance(code, str)

    def test_select_filters_st(self):
        """ST 股票被剔除"""
        from src.strategies.v_leader_main_surge import VLeaderMainSurgeStrategy
        universe = _make_universe()
        # 插入一个 ST 票
        universe.loc[10] = ["ST0001", "ST测试", 5.0, 50000.0, 50.0]
        s = VLeaderMainSurgeStrategy(n_stocks=20)
        result = s.select("2026-05-30", universe)
        assert "ST0001" not in result

    def test_select_filters_low_liquidity(self):
        """低流动性股票被剔除"""
        from src.strategies.v_leader_main_surge import VLeaderMainSurgeStrategy
        universe = _make_universe()
        # 修改一只股票流动性 < 3000 万
        universe.loc[universe["code"] == "000001", "avg_amount_wan"] = 100.0
        s = VLeaderMainSurgeStrategy(n_stocks=20)
        # 由于数据缺失, 可能返回 0 只, 但 000001 不应在结果中
        result = s.select("2026-05-30", universe)
        assert "000001" not in result

    def test_select_empty_universe(self):
        """空 universe 返回空"""
        from src.strategies.v_leader_main_surge import VLeaderMainSurgeStrategy
        s = VLeaderMainSurgeStrategy()
        assert s.select("2026-05-30", pd.DataFrame()) == []

    def test_select_with_bad_model_returns_empty(self):
        """模型损坏 → 优雅返回 []"""
        from src.strategies import v_leader_main_surge as mod
        from src.strategies.v_leader_main_surge import VLeaderMainSurgeStrategy
        mod.VLeaderMainSurgeStrategy._class_load_failed.clear()
        s = VLeaderMainSurgeStrategy(
            n_stocks=5,
            ml_model_path="data/nonexistent_model.json",
            ml_scaler_path="data/nonexistent_scaler.json",
        )
        result = s.select("2026-05-30", _make_universe())
        assert result == []

    def test_sector_diversification_caps(self):
        """max_sector_pct 限制单行业占比"""
        from src.strategies.v_leader_main_surge import VLeaderMainSurgeStrategy
        from src.strategies import v_leader_main_surge as mod
        mod.VLeaderMainSurgeStrategy._class_model.clear()
        mod.VLeaderMainSurgeStrategy._class_load_failed.clear()

        # 用真实模型 + 注入 industry mock
        s = VLeaderMainSurgeStrategy(n_stocks=10, max_sector_pct=0.30)
        # 强制注入 10 只同行业 + builder 注入
        builder = s._get_feature_builder()
        assert builder is not None
        # mock 行业: 全部返回 "银行"
        builder._industry_cache = {c: "银行" for c in _make_universe()["code"]}

        # 此时 _build_pool 应该把同行业的卡在 max_per_industry = 3
        mock_predictions = pd.DataFrame({
            "code": _make_universe()["code"].tolist(),
            "predict_proba": [0.5 + i * 0.01 for i in range(10)],
            "rank": list(range(1, 11)),
        })
        result = s._build_pool(mock_predictions, "2026-05-30")
        assert len(result) == 3, f"期望 3 只 (30% × 10), 实际 {len(result)}"
        assert len(result) <= s.n_stocks

    def test_sector_diversification_picks_top_proba(self):
        """同行业内取 predict_proba 最高的"""
        from src.strategies.v_leader_main_surge import VLeaderMainSurgeStrategy
        from src.strategies import v_leader_main_surge as mod
        mod.VLeaderMainSurgeStrategy._class_model.clear()

        s = VLeaderMainSurgeStrategy(n_stocks=10, max_sector_pct=0.30)
        builder = s._get_feature_builder()
        builder._industry_cache = {c: "银行" for c in _make_universe()["code"]}

        # mock_predictions 按 predict_proba 倒序: 600519 > 000858 > 600000 > ...
        mock_predictions = pd.DataFrame({
            "code": ["600519", "000858", "600000", "000001", "000002",
                     "300750", "000333", "002594", "600276", "601318"],
            "predict_proba": [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.05],
            "rank": list(range(1, 11)),
        })
        result = s._build_pool(mock_predictions, "2026-05-30")
        # 3 只 (n=10 * 30% = 3), 应该是 proba 最高的 3 只
        assert result == ["600519", "000858", "600000"]

    def test_max_sector_pct_1_means_no_cap(self):
        """max_sector_pct=1.0 表示不限制行业"""
        from src.strategies.v_leader_main_surge import VLeaderMainSurgeStrategy
        from src.strategies import v_leader_main_surge as mod
        mod.VLeaderMainSurgeStrategy._class_model.clear()

        s = VLeaderMainSurgeStrategy(n_stocks=5, max_sector_pct=1.0)
        builder = s._get_feature_builder()
        builder._industry_cache = {c: "银行" for c in _make_universe()["code"]}

        mock_predictions = pd.DataFrame({
            "code": ["600519", "000858", "600000", "000001", "000002"],
            "predict_proba": [0.9, 0.8, 0.7, 0.6, 0.5],
            "rank": list(range(1, 6)),
        })
        result = s._build_pool(mock_predictions, "2026-05-30")
        assert len(result) == 5

    def test_industry_fallback_to_other(self):
        """行业查询失败 → 全部行业视为 '其他' → 仍走 cap 逻辑"""
        from src.strategies.v_leader_main_surge import VLeaderMainSurgeStrategy
        from src.strategies import v_leader_main_surge as mod
        mod.VLeaderMainSurgeStrategy._class_model.clear()

        s = VLeaderMainSurgeStrategy(n_stocks=5, max_sector_pct=0.30)
        # 模拟 builder 失败: _get_industries 会返回 {c: "其他"} 全部
        s._get_feature_builder = lambda: None  # type: ignore

        mock_predictions = pd.DataFrame({
            "code": ["000001", "000002", "600000"],
            "predict_proba": [0.5, 0.4, 0.3],
            "rank": [1, 2, 3],
        })
        result = s._build_pool(mock_predictions, "2026-05-30")
        # builder=None → 全部行业='其他' → max_per_industry=1 (5*0.3) → 选 1 只
        assert result == ["000001"]

    def test_industry_fallback_no_cap_when_pct_1(self):
        """builder=None + max_sector_pct=1.0 → 全部选上"""
        from src.strategies.v_leader_main_surge import VLeaderMainSurgeStrategy
        from src.strategies import v_leader_main_surge as mod
        mod.VLeaderMainSurgeStrategy._class_model.clear()

        s = VLeaderMainSurgeStrategy(n_stocks=5, max_sector_pct=1.0)
        s._get_feature_builder = lambda: None  # type: ignore

        mock_predictions = pd.DataFrame({
            "code": ["000001", "000002", "600000"],
            "predict_proba": [0.5, 0.4, 0.3],
            "rank": [1, 2, 3],
        })
        result = s._build_pool(mock_predictions, "2026-05-30")
        # max_sector_pct >= 1.0 → 不走行业分支, 直接 top N
        assert result == ["000001", "000002", "600000"]


# ============================================================
#  YAML 注册验证
# ============================================================


class TestYamlRegistration:
    """V龙头 在 strategies.yaml 中已注册"""

    def test_v_leader_in_strategies_yaml(self):
        import yaml
        yaml_path = _PROJECT_ROOT / "config" / "strategies.yaml"
        with open(yaml_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        v_leader_entries = [
            s for s in cfg["strategies"]
            if "v_leader_main_surge" in s.get("class_path", "")
        ]
        assert len(v_leader_entries) == 1, "V龙头 应有且只有 1 个注册"
        entry = v_leader_entries[0]
        assert entry["params"]["n_stocks"] == 30
        assert entry["params"]["max_sector_pct"] == 0.30
        assert entry["params"]["ml_model_path"] == "data/xgb_model.json"
        assert entry["strategy_type"] == "portfolio"
