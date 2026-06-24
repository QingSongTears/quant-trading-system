# Research 使用指南 (2026-06-25)

> **TL;DR**: `AlphaLab` 组合 `Dataset` (取数据) + `AlphaModel` (训练/推理), 提供 train/predict pipeline。
>
> ⚠️ **生产警告**: 当前 `AStockDataset` / `AStockAlphaModel` 是**占位实现** (用 hash mock + 假 XGBoost), 真实项目需替换为接 `v_leader_features.FeatureBuilder` 和 `XgbV4Model`。

## 三个组件

| 组件 | 角色 | 借鉴 vnpy |
|------|------|-----------|
| `BaseDataset` | 数据接口 (fit/predict) | `vnpy.alpha.dataset.BaseDataset` |
| `BaseAlphaModel` | 模型接口 (fit/predict_proba) | `vnpy.alpha.model.template.AlphaModel` |
| `AlphaLab` | 顶层编排 (train/predict pipeline) | `vnpy.alpha.lab.AlphaLab` |

## 端到端示例 (使用占位实现)

```python
from src.research import AlphaLab, AStockDataset, AStockAlphaModel

# 1. 组装
lab = AlphaLab(
    dataset=AStockDataset(lookback=20, horizon=5),
    model=AStockAlphaModel(),
    lab_name="my_alpha",
)

# 2. 训练 (端到端: 拉数据 → 训练 → 评估 → 保存)
metrics = lab.train_pipeline(
    start="2020-01-01",
    end="2023-12-31",
    save_path="data/xgb_v5.json",
    scaler_path="data/xgb_v5_scaler.json",
)
print(metrics)
# → {'n_samples': 10000, 'ic': 0.05, 'mean_prob': 0.5, 'mean_label': 0.01}

# 3. 推理 (端到端: 拉数据 → 模型预测)
probs = lab.predict_pipeline(date="2024-06-24")
# probs 是 numpy 数组, 长度 = 当日有数据的股票数
# probs 在 [0, 1], 越大越看好

# 4. 排序选股
import numpy as np
top_k = 30
top_idx = np.argsort(probs)[-top_k:][::-1]  # 概率最高的 30 只
print(f"Top {top_k} 股票: {top_idx}")
```

## 加载已训练模型

```python
from src.research import AlphaLab, AStockDataset
from datetime import date

# 1. 准备推理用的 dataset (必须传, 用来拉数据)
#    ⚠️ lookback/horizon 必须跟训练时一致
inference_dataset = AStockDataset(lookback=20, horizon=5)

# 2. 加载模型
lab = AlphaLab.load(
    model_path="data/xgb_v5.json",
    scaler_path="data/xgb_v5_scaler.json",
    dataset=inference_dataset,  # 必传
    n_features=74,              # 跟训练时一致
    lab_name="my_alpha",
)

# 3. 推理
probs = lab.predict_pipeline(date=date(2024, 6, 24))
```

**注意**: `load()` 不传 `dataset` 会抛 `ValueError` (2026-06-25 修)。

## 自定义 Dataset

继承 `BaseDataset`, 实现 2 个抽象方法:

```python
from src.research import BaseDataset
import pandas as pd

class MyStockDataset(BaseDataset):
    def __init__(self, lookback=20, horizon=5):
        super().__init__(lookback, horizon)

    def _fetch_features(self, start, end, vt_symbols=None):
        """返回 DataFrame, columns 含 vt_symbol, trade_date, close + 特征列"""
        from src.data import data_mgr
        # 真实实现: 接 data_mgr.datafeed + v_leader_features.FeatureBuilder
        # 拉 K 线 → 算 74 维特征 → 返回
        rows = []
        for vt_sym in (vt_symbols or self._universe):
            bars = data_mgr.datafeed.get_bars(vt_sym, "1d", start, end)
            for bar in bars:
                rows.append({
                    "vt_symbol": vt_sym,
                    "trade_date": bar.datetime.date(),
                    "close": bar.close_price,
                    "feature1": ...,
                    "feature2": ...,
                })
        return pd.DataFrame(rows)

    def _make_labels(self, close_df):
        """根据 close 计算未来 N 日累计收益"""
        return close_df.pct_change(self.horizon).shift(-self.horizon)
```

## 自定义 Model

继承 `BaseAlphaModel`, 实现 4 个抽象方法:

```python
from src.research import BaseAlphaModel
import numpy as np

class MyXgbModel(BaseAlphaModel):
    def fit(self, X, y, **kwargs):
        # 真实实现: 调 xgboost.train 训 booster
        # 然后把 booster + scaler 存到 self._booster / self._scaler
        ...
        self.is_fitted = True
        return self

    def predict_proba(self, X):
        # 真实实现: 调 booster.predict(dmatrix)
        return probs

    def save(self, model_path, **kwargs):
        # 真实实现: booster.save_model(str(model_path))
        ...

    @classmethod
    def load(cls, model_path, **kwargs):
        # 真实实现: 读 booster + scaler 重建
        ...
```

## 端到端数据流

```
data_mgr.datafeed.get_bars()  # 拉 K 线
        ↓
v_leader_features.FeatureBuilder  # 算 74 维特征 (待集成)
        ↓
AStockDataset._fetch_features()  # 装进 DataFrame
        ↓
AStockDataset._make_labels()  # 算 label (未来 N 日收益)
        ↓
AStockDataset.fit()  # 构造 (X, y) 训练样本
        ↓
AStockAlphaModel.fit(X, y)  # 训练 (XGBoost)
        ↓
booster.save_model()  # 保存
        ↓
[推理时]
booster.load_model()  # 加载
        ↓
AStockDataset.predict(date)  # 拉当日 + 构造推理 batch
        ↓
AStockAlphaModel.predict_proba(X)  # 输出概率
        ↓
[按概率排序, 取 Top-K]
```

## 限制

- **占位实现**: `AStockDataset._fetch_features` 用 hash 生成假 OHLCV, `AStockAlphaModel` 用 mock 线性模型
- **训练评估**: `_evaluate` 只做 in-sample 评估, 实际应分 train/val/test
- **未做时序切分**: 同一时间窗的训练样本可能 data leakage (没禁止 shuffle)

## 生产化 TODO

| 文件 | 当前 | 应替换为 |
|------|------|---------|
| `dataset.py:AStockDataset._fetch_features` | hash mock | `data_mgr.datafeed.get_bars` + `v_leader_features.FeatureBuilder.build_batch_matrix` |
| `dataset.py:_make_labels` | 简单 pct_change | 含交易成本 / 涨跌停 / 行业中性化 |
| `alpha_model.py:AStockAlphaModel.fit` | mock 线性 | `xgboost.train` + `data_mgr.load_scaler` |
| `alpha_model.py:save` | 写字符串 | `booster.save_model` + `save_scaler` |
| `lab.py:_evaluate` | in-sample IC | 时序 CV + 多窗口 IC/RankIC |

## 架构位置

```
src/research/
├── __init__.py           # 公共 API
├── dataset.py            # BaseDataset + AStockDataset
├── alpha_model.py        # BaseAlphaModel + AStockAlphaModel
└── lab.py                # AlphaLab
```

## 与 vnpy 4.4 差异

| 维度 | vnpy 4.4 | 本项目 |
|------|----------|--------|
| 数据源 | AlphaLab 自动管理 (多 dataset) | 单 dataset (用户传) |
| 模型 | AlphaModel 抽象 + Ensemble | BaseAlphaModel + 单 model |
| 评估 | 全套 (IC/RankIC/Sharpe/MDD) | 仅 IC + n_samples |
| 调度 | 定时训练 + 自动 inference | 手动调 pipeline |
| 持久化 | 完整 (model + dataset + metrics) | model + scaler (dataset 重传) |
