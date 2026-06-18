"""
牛股次日涨跌预测 — 维度权重分析
==========================================
1. 将 all_7d_scores.json + daily_price 关联，
   生成标注数据: 昨日8维评分 → 今日 pct_change
2. 分析每个维度对"次日上涨"的预测能力（IC、信息系数）
3. 输出最优权重组合（用简单逻辑回归）
4. 回测: 按预测概率选股，看命中率
"""

import sys, json, sqlite3, math
from pathlib import Path
import pandas as pd
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, accuracy_score
from sklearn.model_selection import cross_val_score

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "database" / "quant.db"

# ── 1. 加载数据 ──
print("【1. 加载数据】")
with open(PROJECT_ROOT / "data" / "all_7d_scores.json") as f:
    scores = json.load(f)
print(f"  评分记录: {len(scores):,} 条")

conn = sqlite3.connect(str(DB_PATH))
# 只取牛股样本（如果有 bull_sample_pool 里的 code）
try:
    with open(PROJECT_ROOT / "data" / "bull_sample_pool.json") as f:
        bulls = json.load(f)
    bull_codes = set(str(s["code"]).zfill(6) for s in bulls)
    print(f"  牛股样本: {len(bull_codes)} 只")
except:
    bull_codes = None
    print("  全量股票")

# 加载 daily_price → 用于获取次日 pct_change
print("  加载 daily_price ...")
price_df = pd.read_sql(
    "SELECT code, trade_date, pct_change, close FROM daily_price "
    "WHERE pct_change IS NOT NULL",
    conn,
    parse_dates=["trade_date"],
)
price_df["code"] = price_df["code"].astype(str).str.zfill(6)
price_df = price_df.sort_values(["code", "trade_date"])
print(f"  价格数据: {len(price_df):,} 条")

# ── 2. 生成标注数据 ──
print("\n【2. 生成标注数据: 昨日评分 → 今日涨幅】")

# 先转成 DataFrame
scores_df = pd.DataFrame(scores)
scores_df["code"] = scores_df["code"].astype(str).str.zfill(6)
scores_df["as_of_date"] = pd.to_datetime(scores_df["as_of_date"])

# 每个 code 的评分日期 → 关联后一天的价格
records = []
for code, grp in scores_df.groupby("code"):
    if bull_codes and code not in bull_codes:
        continue  # 只看牛股
    grp = grp.sort_values("as_of_date")
    for i, row in enumerate(grp.itertuples()):
        if i == len(grp) - 1:
            continue  # 最后一天没有"明天"
        next_row = grp.iloc[i + 1]
        # 用 as_of_date 作为"昨天"，下一个交易日作为"今天"
        # 实际上评分是基于 as_of_date 的数据，预测 as_of_date+1 的涨幅
        target_date = next_row["as_of_date"]
        # 从 price_df 找 target_date 的 pct_change
        price_rows = price_df[
            (price_df["code"] == code) &
            (price_df["trade_date"] == target_date)
        ]
        if len(price_rows) == 0:
            continue
        pct = price_rows.iloc[0]["pct_change"]
        if pd.isna(pct):
            continue
        records.append({
            "code":      code,
            "trade_date": str(target_date)[:10],
            "tech":       row.tech_weighted,
            "fundam":    row.fundam_weighted,
            "fund":       row.fund_weighted,
            "inst":       row.institutional_weighted,
            "lh":         row.lh_institutional_weighted,
            "sent":       row.sentiment_weighted,
            "news":       row.news_event_weighted,
            "chip":       row.chip_weighted,
            "pct_change": pct,
            "up":         1 if pct > 0 else 0,
        })

print(f"  标注样本: {len(records):,} 条")
df = pd.DataFrame(records)

if len(df) < 100:
    print("  ❌ 样本太少，尝试包含全部股票...")
    # 重新跑，不限制 bull_codes
    records = []
    for code, grp in scores_df.groupby("code"):
        grp = grp.sort_values("as_of_date")
        for i, row in enumerate(grp.itertuples()):
            if i == len(grp) - 1:
                continue
            next_row = grp.iloc[i + 1]
            target_date = next_row["as_of_date"]
            price_rows = price_df[
                (price_df["code"] == code) &
                (price_df["trade_date"] == target_date)
            ]
            if len(price_rows) == 0:
                continue
            pct = price_rows.iloc[0]["pct_change"]
            if pd.isna(pct):
                continue
            records.append({
                "code":      code,
                "trade_date": str(target_date)[:10],
                "tech":       row.tech_weighted,
                "fundam":    row.fundam_weighted,
                "fund":       row.fund_weighted,
                "inst":       row.institutional_weighted,
                "lh":         row.lh_institutional_weighted,
                "sent":       row.sentiment_weighted,
                "news":       row.news_event_weighted,
                "chip":       row.chip_weighted,
                "pct_change": pct,
                "up":         1 if pct > 0 else 0,
            })
    df = pd.DataFrame(records)
    print(f"  全量标注样本: {len(df):,} 条")

df.to_csv(PROJECT_ROOT / "data" / "daily_pred_dataset.csv", index=False, encoding="utf-8-sig")
print(f"  已保存: data/daily_pred_dataset.csv")
print(f"  上涨比例: {df['up'].mean()*100:.1f}%")

# ── 3. 维度分析 ──
print("\n【3. 各维度预测能力分析】")
dim_cols = ["tech", "fundam", "fund", "inst", "lh", "sent", "news", "chip"]
dim_names = ["技术面", "基本面", "资金面", "机构面", "龙虎榜", "情绪面", "新闻面", "筹码面"]

results = []
for col, name in zip(dim_cols, dim_names):
    # IC (Information Coefficient) = corr(dim, pct_change)
    valid = df[[col, "pct_change"]].dropna()
    if len(valid) < 50:
        continue
    ic = valid[col].corr(valid["pct_change"])
    
    # 单边IC: 只看 >0 的部分（维度越高 → 涨幅越大？）
    high_mask = valid[col] > valid[col].median()
    high_up_rate = valid[high_mask]["up"].mean() if high_mask.sum() > 0 else 0.5
    low_up_rate = valid[~high_mask]["up"].mean() if (~high_mask).sum() > 0 else 0.5
    
    results.append({
        "维度":   name,
        "IC":     round(ic, 4),
        "高分组上涨率": round(high_up_rate * 100, 1),
        "低分组上涨率": round(low_up_rate * 100, 1),
        "差距":   round((high_up_rate - low_up_rate) * 100, 1),
    })

ic_df = pd.DataFrame(results).sort_values("IC", ascending=False)
print(ic_df.to_string(index=False))
print()

# ── 4. 逻辑回归求权重 ──
print("【4. 逻辑回归: 学习最优权重】")
X = df[dim_cols].fillna(df[dim_cols].median()).values
y = df["up"].values

model = LogisticRegression(
    penalty="l2",
    C=1.0,
    solver="liblinear",
    random_state=42,
)
scores = cross_val_score(model, X, y, cv=5, scoring="roc_auc")
print(f"  Cross-val AUC: {scores.mean():.3f} (±{scores.std():.3f})")

model.fit(X, y)
print(f"  模型 AUC: {roc_auc_score(y, model.predict_proba(X)[:,1]):.3f}")
print()
print("  维度权重 (系数）:")
for name, coef in zip(dim_names, model.coef_[0]):
    print(f"    {name:8s}: {coef:+.4f}  (exp={math.exp(coef):.3f}x)")
print()
print(f"  截距 (bias): {model.intercept_[0]:.4f}")
print()

# 保存权重
weights = dict(zip(dim_cols, model.coef_[0]))
weights["intercept"] = model.intercept_[0]
with open(PROJECT_ROOT / "data" / "daily_pred_weights.json", "w") as f:
    json.dump(weights, f, ensure_ascii=False, indent=2)
print("  💾 权重已保存: data/daily_pred_weights.json")

# ── 5. 回测: 按预测概率选股 ──
print("\n【5. 回测验证】")
df["pred_prob"] = model.predict_proba(X)[:, 1]
df["pred_up"] = (df["pred_prob"] >= 0.5).astype(int)

acc = accuracy_score(y, df["pred_up"])
auc = roc_auc_score(y, df["pred_prob"])
print(f"  准确率: {acc*100:.1f}%  (随机=50%)")
print(f"  AUC:    {auc:.3f}  (0.5=随机)")

# 按 pred_prob 分5档，看实际上涨率
df["prob_bin"] = pd.qcut(df["pred_prob"], q=5, labels=["Q1","Q2","Q3","Q4","Q5"])
print(f"\n  预测概率分档 → 实际上涨率:")
bin_res = df.groupby("prob_bin")["up"].agg(["count","mean"]).round(3)
bin_res.columns = ["样本数", "实际上涨率"]
print(bin_res)

print("\n✅ 完成！下一步: 用权重集成到 param_server.py 做实时预测")
