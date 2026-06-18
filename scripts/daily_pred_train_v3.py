"""
每日涨跌预测训练脚本 v3
修复 v2 的问题：
1. suppress sklearn FutureWarning
2. 修复特征 NaN 处理
3. 直接输出完整结果
"""

import json, math, warnings
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, accuracy_score
from sklearn.model_selection import cross_val_score

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "database" / "quant.db"


def load_and_build_features():
    conn = __import__("sqlite3").connect(str(DB_PATH))
    df = pd.read_sql(
        "SELECT code, trade_date, open, high, low, close, volume, pct_change "
        "FROM daily_price WHERE pct_change IS NOT NULL "
        "ORDER BY code, trade_date",
        conn, parse_dates=["trade_date"],
    )
    conn.close()
    print(f"加载 daily_price: {len(df):,} 条")

    df["code"] = df["code"].astype(str).str.zfill(6)
    df = df.sort_values(["code", "trade_date"]).reset_index(drop=True)

    # 下一个交易日的 pct_change 作为 label
    df["next_pct"] = df.groupby("code")["pct_change"].shift(-1)
    df = df.dropna(subset=["next_pct"])
    print(f"标注数据: {len(df):,} 条 (有次日涨幅)")
    print(f"上涨比例: {df['next_pct'].mean()*100:.1f}%")

    results = []
    total = df["code"].nunique()
    processed = 0

    for code, group in df.groupby("code"):
        group = group.sort_values("trade_date").reset_index(drop=True)
        n = len(group)
        if n < 30:
            continue

        close = group["close"].values
        high  = group["high"].values
        low   = group["low"].values
        vol   = group["volume"].values

        # 技术指标
        ma5  = pd.Series(close).rolling(5).mean().values
        ma10 = pd.Series(close).rolling(10).mean().values
        ma20 = pd.Series(close).rolling(20).mean().values
        vol_ma5 = pd.Series(vol).rolling(5).mean().values

        # RSI14
        delta = pd.Series(close).diff().values
        gain = np.where(delta > 0, delta, 0.0)
        loss = np.where(delta < 0, -delta, 0.0)
        avg_gain = pd.Series(gain).rolling(14).mean().values
        avg_loss = pd.Series(loss).rolling(14).mean().values
        rs   = np.where(avg_loss > 0, avg_gain / avg_loss, np.nan)
        rsi14 = 100 - 100 / (1 + rs)

        # MACD
        ema12 = pd.Series(close).ewm(span=12, adjust=False).mean().values
        ema26 = pd.Series(close).ewm(span=26, adjust=False).mean().values
        macd   = ema12 - ema26
        signal = pd.Series(macd).ewm(span=9, adjust=False).mean().values
        macd_hist = macd - signal

        # 布林带
        bb_mid  = pd.Series(close).rolling(20).mean().values
        bb_std  = pd.Series(close).rolling(20).std().values
        bb_up   = bb_mid + 2 * bb_std
        bb_dn   = bb_mid - 2 * bb_std
        bb_width = np.where(bb_mid > 0, (bb_up - bb_dn) / bb_mid, np.nan)

        # 量比
        vol_ratio = np.where(vol_ma5 > 0, vol / vol_ma5, np.nan)

        for i in range(20, n):
            results.append({
                "code":      code,
                "trade_date": str(group.iloc[i]["trade_date"])[:10],
                "ma5_pos":    (close[i] - ma5[i])  / ma5[i]  * 100  if ma5[i]  > 0 else np.nan,
                "ma10_pos":   (close[i] - ma10[i]) / ma10[i] * 100  if ma10[i] > 0 else np.nan,
                "ma20_pos":   (close[i] - ma20[i]) / ma20[i] * 100  if ma20[i] > 0 else np.nan,
                "rsi14":      rsi14[i]  if not np.isnan(rsi14[i]) else np.nan,
                "macd_hist":  macd_hist[i] if not np.isnan(macd_hist[i]) else np.nan,
                "bb_width":   bb_width[i] if not np.isnan(bb_width[i]) else np.nan,
                "vol_ratio":  vol_ratio[i] if not np.isnan(vol_ratio[i]) else np.nan,
                "turnover":  vol[i] / 1e8,
                "next_pct":   group.iloc[i]["next_pct"],
                "up":          1 if group.iloc[i]["next_pct"] > 0 else 0,
            })

        processed += 1
        if processed % 500 == 0:
            print(f"  ... {processed}/{total} 只股票处理完成")

    feat_df = pd.DataFrame(results)
    # 用中位数填充 NaN（比 dropna 保留更多数据）
    feat_cols = ["ma5_pos","ma10_pos","ma20_pos","rsi14","macd_hist","bb_width","vol_ratio"]
    for col in feat_cols:
        median_val = feat_df[col].median()
        feat_df[col] = feat_df[col].fillna(median_val)

    feat_df = feat_df.dropna(subset=["up"])
    print(f"✅ 特征工程完成: {len(feat_df):,} 条")
    return feat_df


def analyze_features(df):
    feat_cols = ["ma5_pos","ma10_pos","ma20_pos","rsi14","macd_hist","bb_width","vol_ratio"]
    feat_names = ["MA5位置","MA10位置","MA20位置","RSI14","MACD柱","布林带宽","量比"]
    results = []
    for col, name in zip(feat_cols, feat_names):
        valid = df[[col, "up"]].dropna()
        if len(valid) < 100:
            continue
        ic = valid[col].corr(valid["up"])
        median_val = valid[col].median()
        high = valid[valid[col] >= median_val]["up"].mean()
        low  = valid[valid[col] <  median_val]["up"].mean()
        results.append({
            "特征": name,
            "IC":     round(ic, 4),
            "高分组上涨率": round(high * 100, 1),
            "低分组上涨率": round(low  * 100, 1),
            "差距":     round((high - low) * 100, 1),
        })
    ic_df = pd.DataFrame(results).sort_values("IC", ascending=False)
    print("=" * 60)
    print("【特征预测能力分析】")
    print("=" * 60)
    print(ic_df.to_string(index=False))
    print()
    return ic_df


def train_model(df):
    feat_cols = ["ma5_pos","ma10_pos","ma20_pos","rsi14","macd_hist","bb_width","vol_ratio"]
    feat_names = ["MA5位置","MA10位置","MA20位置","RSI14","MACD柱","布林带宽","量比"]
    print("=" * 60)
    print("【训练逻辑回归模型】")
    print("=" * 60)

    X = df[feat_cols].fillna(0).values
    y = df["up"].values()

    model = LogisticRegression(
        C=1.0, solver="lbfgs", max_iter=1000, random_state=42,
    )
    scores = cross_val_score(model, X, y, cv=5, scoring="roc_auc")
    print(f"  Cross-val AUC: {scores.mean():.3f} (±{scores.std():.3f})")

    model.fit(X, y)
    y_prob = model.predict_proba(X)[:, 1]
    auc = roc_auc_score(y, y_prob)
    acc = accuracy_score(y, model.predict(X))
    print(f"  全量 AUC:   {auc:.3f}")
    print(f"  全量准确率: {acc*100:.1f}%")

    print(f"\n  特征系数:")
    for name, coef in zip(feat_names, model.coef_[0]):
        print(f"    {name}: {coef:+.4f}  (exp={math.exp(coef):.3f}x)")
    print(f"\n  截距: {model.intercept_[0]:.4f}")

    weights = dict(zip(feat_cols, model.coef_[0].tolist()))
    weights["intercept"] = float(model.intercept_[0])
    with open(PROJECT_ROOT / "data" / "daily_tech_weights.json", "w") as f:
        json.dump(weights, f, indent=2, ensure_ascii=False)
    print(f"\n  💾 权重已保存: data/daily_tech_weights.json")
    return model


def backtest_prediction(df, model):
    feat_cols = ["ma5_pos","ma10_pos","ma20_pos","rsi14","macd_hist","bb_width","vol_ratio"]
    print("\n" + "=" * 60)
    print("【回测验证】")
    print("=" * 60)

    X = df[feat_cols].fillna(0).values()
    df = df.copy()
    df["pred_prob"] = model.predict_proba(X)[:, 1]
    df["pred_up"]   = (df["pred_prob"] >= 0.5).astype(int)

    acc = accuracy_score(df["up"], df["pred_up"])
    auc = roc_auc_score(df["up"], df["pred_prob"])
    print(f"  准确率: {acc*100:.1f}%  (随机=50%)")
    print(f"  AUC:    {auc:.3f}  (0.5=随机)")

    # 按 pred_prob 分5档
    df["prob_bin"] = pd.qcut(df["pred_prob"], q=5, labels=["Q1最低","Q2","Q3","Q4","Q5最高"])
    bin_res = df.groupby("prob_bin")["up"].agg(["count","mean"]).round(3)
    bin_res.columns = ["样本数","实际上涨率"]
    print(f"\n  预测概率分档 → 实际上涨率:")
    print(bin_res.to_string())


def main():
    df = load_and_build_features()
    if len(df) < 1000:
        print("❌ 样本太少，无法训练")
        return

    df.to_csv(PROJECT_ROOT / "data" / "daily_tech_dataset.csv",
                index=False, encoding="utf-8-sig")
    print(f"  💾 特征数据已保存: data/daily_tech_dataset.csv")

    analyze_features(df)
    model = train_model(df)
    backtest_prediction(df, model)

    print(f"\n✅ 完成！")
    print(f"  权重文件: data/daily_tech_weights.json")
    print(f"  数据集:   data/daily_tech_dataset.csv")


if __name__ == "__main__":
    main()
