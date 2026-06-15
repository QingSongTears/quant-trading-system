"""
翻倍股研究 — 阶段3：因子分析 + 概率建模 + 对照组对比
核心问题：翻倍前和没翻倍的股票，特征到底差在哪？
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Tuple
import warnings
warnings.filterwarnings("ignore")

DATA_DIR = Path("/workspace/stock-screener/data")
OUTPUT_DIR = Path("/workspace/stock-screener/output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def run_factor_analysis():
    """
    完整因子分析流程：
    1. 加载翻倍股特征
    2. 采样对照组（未翻倍股）
    3. 特征分布对比
    4. 计算各因子的区分度
    5. 概率模型
    """
    print("=" * 70)
    print("📐 阶段3：多因子分析 + 概率建模")
    print("=" * 70)

    # ===== 1. 加载翻倍股特征 =====
    feat_path = OUTPUT_DIR / "doubler_features.csv"
    if not feat_path.exists():
        print("[ERROR] 请先运行 research_extract_features.py")
        return

    doublers = pd.read_csv(feat_path)
    doublers["is_doubler"] = 1
    print(f"\n[INPUT] 翻倍股: {len(doublers)} 例")

    # ===== 2. 生成对照组 =====
    control = _generate_control_group(doublers)
    if control is not None and not control.empty:
        control["is_doubler"] = 0
        print(f"[CONTROL] 对照组: {len(control)} 例")
        all_data = pd.concat([doublers, control], ignore_index=True)
    else:
        all_data = doublers

    # ===== 3. 特征列 =====
    feature_cols = [c for c in doublers.columns if c not in [
        "code", "name", "start_date", "is_doubler", "actual_gain_pct", "actual_days"
    ]]
    print(f"[FEATURES] {len(feature_cols)} 个特征")

    # ===== 4. 单因子分析：翻倍组 vs 对照组 =====
    print(f"\n{'='*70}")
    print("单因子对比：翻倍股(1) vs 对照组(0)")
    print(f"{'='*70}")

    factor_analysis = []
    for col in feature_cols:
        try:
            d_data = doublers[col].dropna()
            c_data = control[col].dropna() if control is not None else pd.Series()

            d_mean = d_data.mean()
            d_std = d_data.std()

            if not c_data.empty:
                c_mean = c_data.mean()
                c_std = c_data.std()

                # Cohen's d 效应量
                pooled_std = np.sqrt((d_std**2 + c_std**2) / 2)
                cohens_d = (d_mean - c_mean) / (pooled_std + 1e-10)

                # 区分度
                discrimination = abs(cohens_d)
            else:
                c_mean = 0
                discrimination = abs(d_mean) / (d_std + 1e-10)

            # 翻倍概率（按特征四分位）
            prob_high, prob_low, trend = _calc_quartile_probability(
                all_data, col, "is_doubler"
            ) if control is not None else (0.5, 0.5, "flat")

            factor_analysis.append({
                "feature": col,
                "doubler_mean": round(d_mean, 3),
                "doubler_std": round(d_std, 3),
                "control_mean": round(c_mean, 3),
                "control_std": round(c_std, 3) if not c_data.empty else 0,
                "cohens_d": round(abs(cohens_d), 3) if control is not None else 0,
                "discrimination": round(discrimination, 3),
                "prob_Q4": round(prob_high, 3),
                "prob_Q1": round(prob_low, 3),
                "trend": trend,
            })
        except Exception as e:
            pass

    factor_df = pd.DataFrame(factor_analysis).sort_values("discrimination", ascending=False)
    print(factor_df.head(20).to_string(index=False))

    # ===== 5. 多因子概率模型（简化逻辑回归）=====
    print(f"\n{'='*70}")
    print("多因子组合：寻找高效因子组合")
    print(f"{'='*70}")

    top_factors = factor_df.head(10)["feature"].tolist()
    print(f"TOP 10 因子: {top_factors}")

    # 对翻倍股计算每个因子的"理想区间"
    ideal_ranges = {}
    for col in top_factors:
        d_data = doublers[col].dropna()
        q25, q75 = d_data.quantile(0.25), d_data.quantile(0.75)
        ideal_ranges[col] = (q25, q75)

    # 计算组合命中率
    if control is not None and not control.empty:
        all_data["signal_count"] = 0
        for col, (lo, hi) in ideal_ranges.items():
            in_range = (all_data[col] >= lo) & (all_data[col] <= hi)
            all_data["signal_count"] += in_range.astype(int)

        print(f"\n因子命中数分布:")
        for n in range(len(top_factors) + 1):
            subset = all_data[all_data["signal_count"] >= n]
            if len(subset) > 0:
                prob = subset["is_doubler"].mean()
                pct = len(subset) / len(all_data) * 100
                print(f"  命中≥{n}个因子: 翻倍概率={prob:.1%}, 覆盖{pct:.1f}%样本")

    # ===== 6. 保存结果 =====
    factor_df.to_csv(OUTPUT_DIR / "factor_analysis.csv", index=False, encoding="utf-8-sig")
    print(f"\n💾 因子分析: {OUTPUT_DIR / 'factor_analysis.csv'}")

    # 理想区间
    import json
    ideal_serializable = {k: (float(v[0]), float(v[1])) for k, v in ideal_ranges.items()}
    (OUTPUT_DIR / "ideal_ranges.json").write_text(
        json.dumps(ideal_serializable, ensure_ascii=False, indent=2)
    )
    print(f"💾 理想区间: {OUTPUT_DIR / 'ideal_ranges.json'}")

    return factor_df, ideal_ranges


def _generate_control_group(doublers: pd.DataFrame) -> pd.DataFrame:
    """
    生成对照组：从同样时间段随机采样没有翻倍的股票
    使用 doublers_features.csv 中的代码已采样的特征，
    但我们还需要对照组特征。简化方案：直接随机选股票+日期
    """
    kline = _load_kline()
    if kline.empty:
        return None

    # 获取翻倍股代码集合
    doubler_codes = set(doublers["code"].unique())

    # 获取翻倍案例的时间分布
    date_range = doublers["start_date"]
    min_date = date_range.min()
    max_date = date_range.max()

    # 随机选非翻倍股
    all_codes = set(kline["code"].unique())
    non_doubler_codes = list(all_codes - doubler_codes)
    np.random.seed(42)

    sample_codes = np.random.choice(
        non_doubler_codes,
        size=min(1000, len(non_doubler_codes)),
        replace=False,
    )

    # 从每只股票随机选一个时间点
    control_features = []
    for code in sample_codes:
        stock_kline = kline[kline["code"] == code].sort_values("date")

        # 随机选时间
        candidate_dates = stock_kline[
            (stock_kline["date"] >= pd.Timestamp(min_date)) &
            (stock_kline["date"] <= pd.Timestamp(max_date))
        ]
        if len(candidate_dates) < 80:
            continue

        # 随机选一个日期作为"伪起涨点"
        rand_idx = np.random.randint(60, len(candidate_dates))
        rand_date = candidate_dates.iloc[rand_idx]["date"]
        rand_date_str = rand_date.strftime("%Y-%m-%d")

        # 提取特征
        pre_data = stock_kline[stock_kline["date"] < rand_date].tail(80)
        if len(pre_data) < 60:
            continue

        from research_extract_features import _extract_single_case_features

        fake = {
            "code": code,
            "name": str(stock_kline["name"].iloc[0]),
            "start_date": rand_date_str,
            "gain_pct": 50,
            "days": 60,
        }
        feat = _extract_single_case_features(pre_data, fake)
        if feat:
            control_features.append(feat)

    print(f"[CONTROL] 生成 {len(control_features)} 例对照组")
    return pd.DataFrame(control_features)


def _calc_quartile_probability(data: pd.DataFrame, col: str, target: str):
    """计算特征各分位的翻倍概率"""
    try:
        valid = data[[col, target]].dropna()
        if len(valid) < 100:
            return 0.5, 0.5, "flat"

        valid["q"] = pd.qcut(valid[col], 4, labels=["Q1", "Q2", "Q3", "Q4"], duplicates="drop")
        probs = valid.groupby("q")[target].mean()

        if "Q4" in probs.index and "Q1" in probs.index:
            prob_high = probs["Q4"]
            prob_low = probs["Q1"]
            trend = "rising" if prob_high > prob_low else "falling"
            return prob_high, prob_low, trend
        return 0.5, 0.5, "flat"
    except:
        return 0.5, 0.5, "flat"


def _load_kline():
    path = DATA_DIR / "kline_daily.csv"
    if not path.exists():
        return pd.DataFrame()
    col_names = ["code", "market", "name", "date", "open", "high", "low", "close", "volume", "amount"]
    df = pd.read_csv(path, names=col_names, header=None,
                     dtype={"code": str, "market": str, "name": str, "date": str,
                            "open": float, "high": float, "low": float, "close": float,
                            "volume": float, "amount": float})
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df.dropna(subset=["date"]).sort_values(["code", "date"])


if __name__ == "__main__":
    run_factor_analysis()
