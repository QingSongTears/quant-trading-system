"""
每日涨跌预测 v2 — 纯技术指标版
======================================
直接用 daily_price 计算技术指标，训练次日涨跌预测模型。
特征: MA5/10/20, RSI14, MACD, 布林带, 量比, 换手率
标签: pct_change > 0 (次日上涨=1)
"""

import sys, sqlite3, math, json
from pathlib import Path
import pandas as pd
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, accuracy_score
from sklearn.model_selection import cross_val_score

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "database" / "quant.db"

def load_and_build_features():
    """从 daily_price 加载数据并计算技术指标"""
    conn = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql(
        "SELECT code, trade_date, open, high, low, close, volume, pct_change "
        "FROM daily_price WHERE pct_change IS NOT NULL "
        "ORDER BY code, trade_date",
        conn,
        parse_dates=["trade_date"],
    )
    conn.close()
    print(f"加载 daily_price: {len(df):,} 条")

    # 留存次日 pct_change 作为标签
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values(["code", "trade_date"]).reset_index(drop=True)

    # 为每条记录找到"下一个交易日"的 pct_change 作为 label
    df["next_pct"] = df.groupby("code")["pct_change"].shift(-1)
    df = df.dropna(subset=["next_pct"])

    print(f"标注数据: {len(df):,} 条 (有次日涨幅)")

    # ── 计算技术指标（按 code 分组）──
    print("计算技术指标...")
    results = []
    total = df["code"].nunique()
    processed = 0

    for code, grp in df.groupby("code"):
        grp = grp.sort_values("trade_date").reset_index(drop=True)
        n = len(grp)
        if n < 30:
            continue

        close = grp["close"].values
        high  = grp["high"].values
        low   = grp["low"].values
        vol   = grp["volume"].values

        # MA5, MA10, MA20
        ma5  = grp["close"].rolling(5).mean()
        ma10 = grp["close"].rolling(10).mean()
        ma20 = grp["close"].rolling(20).mean()

        # RSI14
        delta = grp["close"].diff()
        gain  = delta.clip(lower=0)
        loss  = (-delta).clip(lower=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs   = avg_gain / avg_loss.replace(0, np.nan)
        rsi14 = 100 - (100 / (1 + rs))

        # MACD (12,26,9)
        ema12 = grp["close"].ewm(span=12, adjust=False).mean()
        ema26 = grp["close"].ewm(span=26, adjust=False).mean()
        macd   = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        macd_hist = macd - signal

        # 布林带 (20, 2)
        bb_mid  = grp["close"].rolling(20).mean()
        bb_std  = grp["close"].rolling(20).std()
        bb_up   = bb_mid + 2 * bb_std
        bb_dn   = bb_mid - 2 * bb_std
        bb_width = (bb_up - bb_dn) / bb_mid

        # 量比 = volume / volume.rolling(5).mean()
        vol_ma5 = grp["volume"].rolling(5).mean()
        vol_ratio = grp["volume"] / vol_ma5.replace(0, np.nan)

        # 换手率（估算）= volume / 流通股本 (缺数据用 volume 代替)
        # 这里用 turnover = volume / 100000000 近似
        turnover = grp["volume"] / 1e8

        for i in range(20, len(grp)):
            results.append({
                "code":        grp.iloc[i]["code"],
                "trade_date":  str(grp.iloc[i]["trade_date"])[:10],
                "ma5_pos":     (close[i] - ma5.iloc[i]) / ma5.iloc[i] * 100 if pd.notna(ma5.iloc[i]) else np.nan,
                "ma10_pos":    (close[i] - ma10.iloc[i]) / ma10.iloc[i] * 100 if pd.notna(ma10.iloc[i]) else np.nan,
                "ma20_pos":    (close[i] - ma20.iloc[i]) / ma20.iloc[i] * 100 if pd.notna(ma20.iloc[i]) else np.nan,
                "rsi14":       rsi14.iloc[i] if pd.notna(rsi14.iloc[i]) else np.nan,
                "macd_hist":   macd_hist.iloc[i] if pd.notna(macd_hist.iloc[i]) else np.nan,
                "bb_width":    bb_width.iloc[i] if pd.notna(bb_width.iloc[i]) else np.nan,
                "vol_ratio":   vol_ratio.iloc[i] if pd.notna(vol_ratio.iloc[i]) else np.nan,
                "turnover":    turnover.iloc[i],
                "next_pct":    grp.iloc[i]["next_pct"],
                "up":           1 if grp.iloc[i]["next_pct"] > 0 else 0,
            })

        processed += 1
        if processed % 200 == 0:
            print(f"  ... {processed}/{total} 只股票处理完成")

    feat_df = pd.DataFrame(results)
    feat_df = feat_df.dropna()
    print(f"✅ 特征工程完成: {len(feat_df):,} 条")
    return feat_df


def analyze_features(df):
    """分析每个特征对上涨的预测能力"""
    feat_cols = ["ma5_pos", "ma10_pos", "ma20_pos",
                 "rsi14", "macd_hist", "bb_width", "vol_ratio", "turnover"]

    print(f"\n{'='*60}")
    print("【特征预测能力分析】")
    print(f"{'='*60}")

    results = []
    for col in feat_cols:
        valid = df[[col, "up"]].dropna()
        if len(valid) < 100:
            continue
        ic = valid[col].corr(valid["up"])

        # 按特征中位数分2组，看上涨率差距
        median_val = valid[col].median()
        high = valid[valid[col] >= median_val]["up"].mean()
        low  = valid[valid[col] < median_val]["up"].mean()

        results.append({
            "特征": col,
            "IC":     round(ic, 4),
            "高分组上涨率": round(high * 100, 1),
            "低分组上涨率": round(low * 100, 1),
            "差距":     round((high - low) * 100, 1),
        })

    ic_df = pd.DataFrame(results).sort_values("IC", ascending=False)
    print(ic_df.to_string(index=False))
    print()
    return ic_df


def train_model(df):
    """训练逻辑回归模型"""
    feat_cols = ["ma5_pos", "ma10_pos", "ma20_pos",
                 "rsi14", "macd_hist", "bb_width", "vol_ratio", "turnover"]

    print(f"{'='*60}")
    print("【训练逻辑回归模型】")
    print(f"{'='*60}")

    X = df[feat_cols].fillna(0).values
    y = df["up"].values

    model = LogisticRegression(
        penalty="l2", C=1.0, solver="lbfgs", max_iter=1000, random_state=42,
    )

    scores = cross_val_score(model, X, y, cv=5, scoring="roc_auc")
    print(f"  Cross-val AUC: {scores.mean():.3f} (±{scores.std():.3f})")

    model.fit(X, y)
    y_pred_prob = model.predict_proba(X)[:, 1]
    auc = roc_auc_score(y, y_pred_prob)
    acc = accuracy_score(y, model.predict(X))
    print(f"  全量 AUC:   {auc:.3f}")
    print(f"  全量准确率: {acc*100:.1f}%")

    feat_names = ["MA5位置", "MA10位置", "MA20位置",
                  "RSI14", "MACD柱", "布林带宽", "量比", "换手率"]
    print(f"\n  特征系数:")
    for name, coef in zip(feat_names, model.coef_[0]):
        print(f"    {name:8s}: {coef:+.4f}  (exp={math.exp(coef):.3f}x)")

    print(f"\n  截距 (bias): {model.intercept_[0]:.4f}")

    # 保存权重
    weights = dict(zip(feat_cols, model.coef_[0].tolist()))
    weights["intercept"] = model.intercept_[0]
    with open(PROJECT_ROOT / "data" / "daily_tech_weights.json", "w") as f:
        json.dump(weights, f, indent=2, ensure_ascii=False)
    print(f"\n  💾 权重已保存: data/daily_tech_weights.json")

    return model, feat_cols


def backtest_prediction(df, model, feat_cols):
    """回测: 按预测概率选股，看实际命中率"""
    print(f"\n{'='*60}")
    print("【回测验证】")
    print(f"{'='*60}")

    X = df[feat_cols].fillna(0).values
    df = df.copy()
    df["pred_prob"] = model.predict_proba(X)[:, 1]
    df["pred_up"]  = (df["pred_prob"] >= 0.5).astype(int)

    # 按 pred_prob 分5档
    df["prob_bin"] = pd.qcut(df["pred_prob"], q=5, labels=["Q1最低", "Q2", "Q3", "Q4", "Q5最高"])
    bin_res = df.groupby("prob_bin")["up"].agg(["count", "mean"]).round(3)
    bin_res.columns = ["样本数", "实际上涨率"]
    print(bin_res)
    print()

    # 准确率
    acc = accuracy_score(df["up"], df["pred_up"])
    auc = roc_auc_score(df["up"], df["pred_prob"])
    print(f"  整体准确率: {acc*100:.1f}%  (随机=50%)")
    print(f"  整体 AUC:    {auc:.3f}  (0.5=随机)")

    return df


def main():
    df = load_and_build_features()
    if len(df) < 1000:
        print("❌ 样本太少，无法训练")
        return

    df.to_csv(PROJECT_ROOT / "data" / "daily_tech_dataset.csv",
               index=False, encoding="utf-8-sig")
    print(f"  💾 特征数据已保存: data/daily_tech_dataset.csv")

    analyze_features(df)
    model, feat_cols = train_model(df)
    backtest_prediction(df, model, feat_cols)

    print(f"\n✅ 完成！")
    print(f"  下次可以直接加载 data/daily_tech_dataset.csv 跳过特征工程")


if __name__ == "__main__":
    main()
