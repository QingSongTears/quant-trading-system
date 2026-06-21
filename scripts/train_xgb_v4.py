"""
龙头模型 v4 — 特征工程加强版 + XGBoost
=======================================
新增特征:
  1. 技术指标: MACD/RSI/KDJ/布林位置(从technical_indicators)
  2. 滞后特征: 上月8维评分(lag_1m)
  3. 评分趋势: 评分3个月变化(score_momentum_3m)
  4. 交叉特征: 评分×技术指标
标签: ret_60d > 10% (识别牛股)
划分: 时间序列(前20月训练, 后5月验证)
"""
import json, sqlite3, sys, pickle, time
from pathlib import Path
import numpy as np
import xgboost as xgb
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = PROJECT_ROOT / "database" / "quant.db"

t0 = time.time()

# ===== 1. 加载评分数据 =====
print("📂 加载评分数据...")
with open(DATA_DIR / "all_7d_scores.json") as f:
    all_scores = json.load(f)
print(f"   共 {len(all_scores)} 条")

# ===== 2. 加载行业信息 =====
print("📂 加载行业信息...")
conn = sqlite3.connect(str(DB_PATH))
industry_rows = conn.execute("SELECT code, industry FROM stock_profile WHERE industry IS NOT NULL").fetchall()
industry_map = {}
for r in industry_rows:
    raw = r[0].replace('sz','').replace('sh','').strip()
    if len(raw) >= 6:
        industry_map[raw.zfill(6)] = r[1]
conn.close()
print(f"   共 {len(industry_map)} 只")

# ===== 3. 加载技术指标 =====
print("📂 加载技术指标(MACD/RSI/KDJ)...")
conn = sqlite3.connect(str(DB_PATH))
tech_rows = conn.execute("""
    SELECT code, trade_date, macd_dif, macd_dea, macd_hist, rsi14, kdj_k, kdj_d, kdj_j,
           boll_mid, boll_upper, boll_lower
    FROM technical_indicators
""").fetchall()
conn.close()

# 按(code, date)索引
tech_by_code_date = {}
for r in tech_rows:
    code = str(r[0]).replace('sz','').replace('sh','').strip().zfill(6)
    key = (code, str(r[1])[:10])
    tech_by_code_date[key] = {
        'macd_dif': r[2] or 0, 'macd_dea': r[3] or 0, 'macd_hist': r[4] or 0,
        'rsi14': r[5] or 50, 'kdj_k': r[6] or 50, 'kdj_d': r[7] or 50, 'kdj_j': r[8] or 50,
        'boll_mid': r[9] or 0, 'boll_upper': r[10] or 0, 'boll_lower': r[11] or 0,
    }

def get_tech_features(code, as_of_date):
    """获取某个股票在某个日期的技术指标"""
    d = as_of_date[:10]
    # 精确匹配当日
    val = tech_by_code_date.get((code, d))
    if val: return val
    # 找最近的前一天
    for offset in range(1, 10):
        from datetime import datetime, timedelta
        try:
            dt = datetime.strptime(d, '%Y-%m-%d') - timedelta(days=offset)
            prev = dt.strftime('%Y-%m-%d')
            val = tech_by_code_date.get((code, prev))
            if val: return val
        except: pass
    return None

print(f"   共 {len(tech_by_code_date)} 条技术指标")

# ===== 4. 按月份组织数据 =====
from collections import defaultdict, OrderedDict
monthly_data = defaultdict(list)
for s in all_scores:
    ym = s.get('year_month', s['as_of_date'][:7])
    monthly_data[ym].append(s)

months_sorted = sorted(monthly_data.keys())
print(f"   共 {len(months_sorted)} 个月份: {months_sorted[0]} ~ {months_sorted[-1]}")

# ===== 5. 构建特征向量 =====
dim_cols = ['tech_weighted','fundam_weighted','fund_weighted',
            'institutional_weighted','lh_institutional_weighted',
            'sentiment_weighted','news_event_weighted','chip_weighted']

# 预计算每月的截面百分位 (需要用到当月所有股票)
from collections import defaultdict
monthly_dim_vals = {}
for ym in months_sorted:
    records = monthly_data[ym]
    vals = {}
    for d in dim_cols:
        vals[d] = [s.get(d, 0) or 0 for s in records]
    avg_scores = [sum(vals[d][i] for d in dim_cols) / len(dim_cols) for i in range(len(records))]
    vals['avg_score'] = avg_scores
    monthly_dim_vals[ym] = vals

def pct_rank(values, target):
    arr = np.array(values)
    rank = np.sum(arr < target) + 0.5 * np.sum(arr == target)
    return rank / max(len(arr), 1)

# 预计算每月的滞后分数（上个月同只股票的评分）
def build_lag_scores(monthly_data, months_sorted, dim_cols):
    """构建滞后1个月的评分"""
    lag = {}  # (code, ym) → prev_month_scores
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
print(f"   滞后特征: {len(lag_scores)} 条")

# ===== 6. 构建训练数据 =====
features_list = []
labels_list = []
codes_list = []
month_list = []

# 用所有25个月份，但最后5个月需要留作验证(标签需要ret_60d)
# 实际上所有月份都有ret_60d, 所以全部可用
# 按时间划分: 前20月训练, 后5月验证
train_months = months_sorted[:-5]   # 2024-06 ~ 2025-?? (20个月)
val_months = months_sorted[-5:]     # 最后5个月

print(f"\n训练月份: {train_months[0]} ~ {train_months[-1]} ({len(train_months)}个月)")
print(f"验证月份: {val_months[0]} ~ {val_months[-1]} ({len(val_months)}个月)")

for ym in months_sorted:
    records = monthly_data[ym]
    n = len(records)
    if n < 50: continue
    is_val = ym in val_months
    
    for i, s in enumerate(records):
        code = s['code']
        as_of_date = s['as_of_date']
        
        feats = {}
        # 基础8维评分
        for d in dim_cols:
            feats[d] = s.get(d, 0) or 0
        
        # 截面百分位
        dim_vals_month = monthly_dim_vals[ym]
        for d in dim_cols:
            tgt = s.get(d, 0) or 0
            feats[d + '_pct'] = pct_rank(dim_vals_month[d], tgt)
        
        # 综合评分百分位
        avg_score = sum(feats[d] for d in dim_cols) / len(dim_cols)
        feats['avg_score_pct'] = pct_rank(dim_vals_month['avg_score'], avg_score)
        
        # 滞后特征(上月评分)
        lag = lag_scores.get((code, ym), {})
        for d in dim_cols:
            feats[d + '_lag1m'] = lag.get(d, 0)
        
        # 评分动量(本月-上月)
        for d in dim_cols:
            cur_val = feats.get(d, 0)
            lag_val = lag.get(d, 0)
            feats[d + '_delta'] = cur_val - lag_val
        
        # 技术指标
        tech = get_tech_features(code, as_of_date)
        if tech:
            feats['macd_hist'] = tech['macd_hist']
            feats['rsi14'] = tech['rsi14'] / 100  # 归一化到0-1
            feats['kdj_k'] = tech['kdj_k'] / 100
            feats['kdj_j'] = tech['kdj_j'] / 100
            # 布林位置: (close - boll_lower) / (boll_upper - boll_lower)
            boll_range = tech['boll_upper'] - tech['boll_lower']
            # 没有close数据, 用mid替代
            feats['boll_pos'] = 0.5
        else:
            feats['macd_hist'] = 0
            feats['rsi14'] = 0.5
            feats['kdj_k'] = 0.5
            feats['kdj_j'] = 0.5
            feats['boll_pos'] = 0.5
        
        # 交叉特征: 评分×技术指标
        feats['score_x_rsi'] = avg_score * (feats['rsi14'] - 0.5)
        feats['score_x_macd'] = avg_score * feats['macd_hist']
        feats['score_x_kdj'] = avg_score * (feats['kdj_k'] - 0.5)
        
        # 行业
        feats['industry'] = industry_map.get(code, '其他')
        
        # 标签: ret_60d > 10%
        ret60 = s.get('ret_60d', 0) or 0
        label = 1 if ret60 > 10 else 0
        
        features_list.append(feats)
        labels_list.append(label)
        codes_list.append(code)
        month_list.append(ym)

print(f"\n📊 特征矩阵: {len(features_list)} 条, 正例率: {sum(labels_list)/len(labels_list)*100:.1f}%")

# ===== 7. 准备训练数据 =====
import pandas as pd
df = pd.DataFrame(features_list)

# one-hot行业
ind_dummies = pd.get_dummies(df['industry'], prefix='ind')
df = pd.concat([df.drop('industry', axis=1), ind_dummies], axis=1)

feature_cols = [c for c in df.columns if c not in ['label', 'code', 'month']]
X = df[feature_cols].values.astype(np.float32)
y = np.array(labels_list, dtype=np.int32)

print(f"特征数: {len(feature_cols)}")
print(f"特征: {feature_cols[:10]}...")

# 归一化
scaler = StandardScaler()
X_scaled = scaler.fit_transform(X)

# 时间序列划分
train_idx = [i for i, m in enumerate(month_list) if m not in val_months]
val_idx = [i for i, m in enumerate(month_list) if m in val_months]
X_train, X_val = X_scaled[train_idx], X_scaled[val_idx]
y_train, y_val = y[train_idx], y[val_idx]

pos_rate_train = sum(y_train)/len(y_train)*100
pos_rate_val = sum(y_val)/len(y_val)*100
print(f"\n训练集: {len(X_train)}条 (正例率{pos_rate_train:.1f}%)")
print(f"验证集: {len(X_val)}条 (正例率{pos_rate_val:.1f}%)")
print(f"训练月份: {train_months[0]} ~ {train_months[-1]}")
print(f"验证月份: {val_months[0]} ~ {val_months[-1]}")

# ===== 8. 训练 XGBoost =====
print("\n🚀 训练 XGBoost v4...")
model = xgb.XGBClassifier(
    n_estimators=500,
    max_depth=6,
    learning_rate=0.03,
    subsample=0.8,
    colsample_bytree=0.7,
    min_child_weight=3,
    gamma=0.1,
    eval_metric='auc',
    use_label_encoder=False,
    random_state=42,
)
model.fit(X_train, y_train, eval_set=[(X_train, y_train), (X_val, y_val)], verbose=False)

# ===== 9. 评估 =====
train_pred = model.predict_proba(X_train)[:, 1]
val_pred = model.predict_proba(X_val)[:, 1]
train_auc = roc_auc_score(y_train, train_pred)
val_auc = roc_auc_score(y_val, val_pred)

print(f"\n📊 模型评估:")
print(f"   训练集 AUC: {train_auc:.4f}")
print(f"   验证集 AUC: {val_auc:.4f}")
print(f"   v3 AUC: 0.5984")
print(f"   旧模型 AUC: 0.5515")

# 特征重要性
importance = model.feature_importances_
top_features = sorted(zip(feature_cols, importance), key=lambda x: -x[1])[:25]
print(f"\n🔝 TOP 25 特征:")
for name, imp in top_features:
    print(f"   {name}: {imp:.4f}")

# 概率分组
for threshold in [0.4, 0.45, 0.5, 0.55, 0.6]:
    preds = (val_pred >= threshold).astype(int)
    hits = sum((preds == 1) & (y_val == 1))
    total_pos = sum(preds)
    actual_pos = sum(y_val)
    prec = hits / max(total_pos, 1) * 100
    rec = hits / max(actual_pos, 1) * 100
    print(f"   阈值={threshold:.2f}: 预测正例={total_pos} 精确率={prec:.1f}% 召回率={rec:.1f}%")

# ===== 10. 保存 =====
model_path = DATA_DIR / "xgb_model.json"
scaler_path = DATA_DIR / "xgb_scaler.pkl"
model.save_model(str(model_path))
with open(scaler_path, 'wb') as f:
    pickle.dump({'scaler': scaler, 'feature_names': feature_cols}, f)

elapsed = time.time() - t0
print(f"\n💾 模型已保存: {model_path}")
print(f"💾 Scaler已保存: {scaler_path}")
print(f"⏱️ 总耗时: {elapsed/60:.1f}分钟")
print(f"\n📈 v4 vs v3 vs 旧模型:")
print(f"{'指标':>20} | {'v4(加强)':>12} | {'v3':>10} | {'旧模型':>10}")
print("-" * 60)
print(f"{'AUC':>20} | {val_auc:>10.4f} | {'0.5984':>10} | {'0.5515':>10}")
print(f"{'特征数':>20} | {len(feature_cols):>10} | {'57':>10} | {'8':>10}")
print(f"{'训练正例率':>20} | {pos_rate_train:>9.1f}% | {'39.5%':>10} | {'95.3%':>10}")
print(f"{'验证正例率':>20} | {pos_rate_val:>9.1f}% | {'8.9%':>10} | {'-':>10}")
