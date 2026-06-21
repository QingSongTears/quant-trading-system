"""
生成 xgb_scaler.json — 与 train_xgb_v4.py 的 feature_cols 完全一致
"""
import json, sqlite3, sys
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

PROJECT_ROOT = Path(".")
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = PROJECT_ROOT / "database" / "quant.db"

# 1. 加载 all_7d_scores.json（用于获取 dim_cols 顺序）
with open(DATA_DIR / "all_7d_scores.json") as f:
    all_scores = json.load(f)
print(f"评分数据: {len(all_scores)} 条")

# 2. 加载行业 map
conn = sqlite3.connect(str(DB_PATH))
industry_rows = conn.execute(
    "SELECT code, industry FROM stock_profile WHERE industry IS NOT NULL"
).fetchall()
industry_map = {}
for r in industry_rows:
    raw = str(r[0]).replace("sz", "").replace("sh", "").strip()
    if len(raw) >= 6:
        industry_map[raw.zfill(6)] = r[1]
conn.close()
all_industries = sorted(set(industry_map.values()))
print(f"行业数: {len(all_industries)}")

# 3. 与 train_xgb_v4.py 完全相同的特征构建逻辑（仅前3只股票用于拟合 scaler）
dim_cols = ['tech_weighted','fundam_weighted','fund_weighted',
            'institutional_weighted','lh_institutional_weighted',
            'sentiment_weighted','news_event_weighted','chip_weighted']

def pct_rank(values, target):
    arr = np.array(values)
    rank = np.sum(arr < target) + 0.5 * np.sum(arr == target)
    return rank / max(len(arr), 1)

# 按月分组
from collections import defaultdict
monthly_data = defaultdict(list)
for s in all_scores:
    ym = s.get('year_month', s['as_of_date'][:7])
    monthly_data[ym].append(s)
months_sorted = sorted(monthly_data.keys())
print(f"月份数: {len(months_sorted)}")

# 预计算每月截面百分位（与 train_xgb_v4.py 一致）
monthly_dim_vals = {}
for ym in months_sorted:
    records = monthly_data[ym]
    vals = {}
    for d in dim_cols:
        vals[d] = [s.get(d, 0) or 0 for s in records]
    vals['avg_score'] = [sum(s.get(d,0) or 0 for d in dim_cols)/len(dim_cols) for s in records]
    monthly_dim_vals[ym] = vals
print("月度截面百分位预计算完成")

# 构建特征矩阵（仅用一小部分数据拟合 scaler）
features_list = []
# 只用最后一个月的数据（足够拟合 scaler 了）
test_ym = months_sorted[-3]  # 用3个月前的数据
records = monthly_data[test_ym]
print(f"用 {test_ym} 月数据拟合 scaler（{len(records)} 条）")

# 预计算滞后分数（与 train_xgb_v4.py 完全一致）
def build_lag_scores(monthly_data, months_sorted, dim_cols):
    """构建滞后1个月的评分（全量月份）"""
    lag = {}
    prev_data = {}
    for ym in months_sorted:
        records = monthly_data[ym]
        cur = {}
        for s in records:
            code = s['code']
            scores = {d: s.get(d, 0) or 0 for d in dim_cols}
            cur[code] = scores
        for code, scores in cur.items():
            if code in prev_data:
                lag[(code, ym)] = prev_data[code]
        prev_data = cur
    return lag

lag_scores = build_lag_scores(monthly_data, months_sorted, dim_cols)
print(f"滞后特征: {len(lag_scores)} 条")

# 构建特征
for s in records[:200]:  # 只用200条拟合 scaler
    code = s['code']
    as_of_date = s['as_of_date']
    feats = {}
    # 基础8维
    for d in dim_cols:
        feats[d] = s.get(d, 0) or 0
    # 截面百分位
    dim_vals_month = monthly_dim_vals[test_ym]
    for d in dim_cols:
        tgt = feats[d]
        vals = dim_vals_month[d]
        feats[d + '_pct'] = pct_rank(vals, tgt)
    # 综合评分百分位
    avg_score = sum(feats[d] for d in dim_cols) / len(dim_cols)
    feats['avg_score_pct'] = pct_rank(dim_vals_month['avg_score'], avg_score)
    # 滞后特征
    lag = lag_scores.get((code, test_ym), {})
    for d in dim_cols:
        feats[d + '_lag1m'] = lag.get(d, 0)  # 无上月数据用0（与train_xgb_v4.py一致）
    # 动量特征
    for d in dim_cols:
        feats[d + '_delta'] = feats[d] - feats[d + '_lag1m']
    # 从 DB 加载技术指标（与 train_xgb_v4.py 一致）
    try:
        import sqlite3
        tech_conn = sqlite3.connect(str(DB_PATH))
        tech_row = tech_conn.execute("""
            SELECT macd_hist, rsi14, kdj_k, kdj_j FROM technical_indicators
            WHERE code=? AND trade_date=? ORDER BY trade_date DESC LIMIT 1
        """, (code, as_of_date)).fetchone()
        tech_conn.close()
        if tech_row:
            feats['macd_hist'] = tech_row[0] or 0
            feats['rsi14'] = (tech_row[1] or 50) / 100.0  # 归一化0-1
            feats['kdj_k'] = (tech_row[2] or 50) / 100.0
            feats['kdj_j'] = (tech_row[3] or 50) / 100.0
        else:
            feats['macd_hist'] = 0
            feats['rsi14'] = 0.5
            feats['kdj_k'] = 0.5
            feats['kdj_j'] = 0.5
    except:
        feats['macd_hist'] = 0
        feats['rsi14'] = 0.5
        feats['kdj_k'] = 0.5
        feats['kdj_j'] = 0.5
    # 布林位置（默认0.5）
    feats['boll_pos'] = 0.5
    # 交叉特征（与 train_xgb_v4.py 一致）
    feats['score_x_rsi'] = avg_score * (feats['rsi14'] - 0.5)
    feats['score_x_macd'] = avg_score * feats['macd_hist']
    feats['score_x_kdj'] = avg_score * (feats['kdj_k'] - 0.5)
    # 行业
    feats['industry'] = industry_map.get(code, '其他')
    features_list.append(feats)

# DataFrame → one-hot 行业
df = pd.DataFrame(features_list)
ind_dummies = pd.get_dummies(df['industry'], prefix='ind')
df = pd.concat([df.drop('industry', axis=1), ind_dummies], axis=1)

feature_cols = [c for c in df.columns if c not in ('label', 'code', 'month')]
X = df[feature_cols].values.astype(np.float32)
print(f"特征矩阵: {X.shape}, 特征数: {len(feature_cols)}")

# 拟合 StandardScaler
scaler = StandardScaler()
scaler.fit(X)
print(f"Scaler 拟合完成: mean_形状={scaler.mean_.shape}, scale_形状={scaler.scale_.shape}")

# 保存为 xgb_scaler.json
from src.data.xgb_scaler import save_scaler
save_scaler(scaler, feature_cols, DATA_DIR / "xgb_scaler.json")
print(f"已保存: {DATA_DIR / 'xgb_scaler.json'}")
