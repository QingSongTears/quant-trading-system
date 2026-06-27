import sys, json, math, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, '.')

import pandas as pd
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, accuracy_score
from sklearn.model_selection import cross_val_score

PROJECT_ROOT = "."

print("=== 8维月度预测模型 ===")
print()

# 1. 加载数据
print("[1. 加载数据]")
with open("data/all_7d_scores.json") as f:
    scores = json.load(f)
df = pd.DataFrame(scores)
df["code"] = df["code"].astype(str).str.zfill(6)
df["as_of_date"] = pd.to_datetime(df["as_of_date"])

# 标签：当月评分 → 下月 ret_20d (shift(-1))
df = df.sort_values(["code", "as_of_date"]).reset_index(drop=True)
df["ret_20d_next"] = df.groupby("code")["ret_20d"].shift(-1)
df = df.dropna(subset=["ret_20d_next"])
df["up"] = (df["ret_20d_next"] > 0).astype(int)

print(f"  标注样本: {len(df):,} 条 (当月评分 → 下月收益)")
print(f"  上涨比例: {df['up'].mean()*100:.1f}%")
print()

# 2. IC 分析
print("="*60)
print("[2. 各维度 IC 分析] (月度评分 → 下月收益)")
print("="*60)

dim_cols = ["tech_weighted","fundam_weighted","fund_weighted",
             "institutional_weighted","lh_institutional_weighted",
             "sentiment_weighted","news_event_weighted","chip_weighted"]
dim_names = ["技术面","基本面","资金面","机构面","龙虎榜",
               "情绪面","新闻面","筹码面"]

results = []
for col, name in zip(dim_cols, dim_names):
    valid = df[[col, "ret_20d_next"]].dropna()
    if len(valid) < 100:
        continue
    ic = valid[col].corr(valid["ret_20d_next"])
    
    median_val = valid[col].median()
    high = valid[valid[col] >= median_val]["ret_20d_next"].mean()
    low  = valid[valid[col] <  median_val]["ret_20d_next"].mean()
    
    results.append({
        "维度":   name,
        "IC":     round(ic, 4),
        "高分组均值": round(high, 1),
        "低分组均值": round(low, 1),
        "差距":     round(high - low, 1),
    })

ic_df = pd.DataFrame(results).sort_values("IC", ascending=False)
print(ic_df.to_string(index=False))
print()

# 3. 训练逻辑回归
print("="*60)
print("[3. 训练逻辑回归：月度预测]")
print("="*60))

X = df[dim_cols].fillna(df[dim_cols].median()).values
y = df["up"].values()

model = LogisticRegression(
    C=1.0, solver="liblinear", max_iter=1000, random_state=42,
)
scores = cross_val_score(model, X, y, cv=5, scoring="roc_auc")
print(f"  Cross-val AUC: {scores.mean():.3f} (+/-{scores.std():.3f})")

model.fit(X, y)
y_prob = model.predict_proba(X)[:, 1]
auc = roc_auc_score(y, y_prob)
acc = accuracy_score(y, model.predict(X))
print(f"  Full AUC:   {auc:.3f}")
print(f"  Accuracy:   {acc*100:.1f}%")
print()

print("  维度权重 (系数):")
for name, coef in zip(dim_names, model.coef_[0]):
    print(f"    {name}: {coef:+.4f}  (exp={math.exp(coef):.3f}x)")
print(f"\n  截距 (bias): {model.intercept_[0]:.4f}")

# 保存权重
weights = dict(zip(dim_cols, model.coef_[0].tolist()))
weights["intercept"] = float(model.intercept_[0])
with open("data/bull_8d_monthly_weights.json", "w") as f:
    json.dump(weights, f, indent=2, ensure_ascii=False)
print(f"\n  Weights saved: data/bull_8d_monthly_weights.json")
print()

# 4. 预测概率分档验证
print("="*60)
print("[4. 预测概率分档 → 实际下月上涨率]")
print("="*60))

df2 = df.copy()
df2["pred_prob"] = model.predict_proba(X)[:, 1]
df2["prob_bin"] = pd.qcut(df2["pred_prob"], q=5, 
                               labels=["Q1最低","Q2","Q3","Q4","Q5最高"])
bin_res = df2.groupby("prob_bin")["up"].agg(["count","mean"]).round(3)
bin_res.columns = ["样本数","实际上涨率"]
print(bin_res)
print()

print("✅ 完成！")
print(f"  权重文件: data/bull_8d_monthly_weights.json")
