"""
龙头模型 v2 — 特征工程 + XGBoost 训练
======================================
新增特征:
  1. 行业评分百分位 (行业内排名)
  2. 截面相对强度 (ret_20d / ret_60d 百分位)
  3. 价格动量 (当前close vs N日最高)
  4. 量比突变 (当日量/5日均量)
  5. 8维评分×动量交叉特征
  6. 换手率特征
输出: data/xgb_model.json (XGBoost模型+scaler)
"""
import json, sqlite3, math, sys, pickle, os
from pathlib import Path
import numpy as np
import xgboost as xgb
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, accuracy_score, classification_report
from sklearn.model_selection import train_test_split

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = PROJECT_ROOT / "database" / "quant.db"

# ===== 1. 加载评分数据 =====
print("📂 加载评分数据...")
with open(DATA_DIR / "all_7d_scores.json") as f:
    all_scores = json.load(f)
print(f"   共 {len(all_scores)} 条记录")

# ===== 2. 加载行业映射 =====
# 从score数据中获取股票名称做行业映射
# 实际上industry_map在param_server.py里从stock_list表加载
# 我们先从DB加载行业
print("📂 加载行业信息...")
conn = sqlite3.connect(str(DB_PATH))
industry_rows = conn.execute("SELECT code, industry FROM stock_profile WHERE industry IS NOT NULL").fetchall()
industry_map = {}
for r in industry_rows:
    raw = r[0].replace('sz','').replace('sh','').strip()
    if len(raw) >= 6:
        industry_map[raw.zfill(6)] = r[1]
conn.close()
print(f"   共 {len(industry_map)} 只股票有行业信息")

# ===== 3. 加载日K线数据（用于动量特征）=====
print("📂 加载日K线数据（最后50个交易日）...")
conn = sqlite3.connect(str(DB_PATH))
# 获取所有股票的最近K线用于动量计算
all_daily = conn.execute("""
    SELECT code, trade_date, close, volume, pct_change, turnover
    FROM daily_price
    ORDER BY code, trade_date
""").fetchall()
conn.close()

# 按股票分组
from collections import defaultdict
kline_by_code = defaultdict(list)
for row in all_daily:
    kline_by_code[row[0]].append({
        'date': row[1], 'close': row[2] or 0,
        'volume': row[3] or 0, 'pct': row[4] or 0,
        'turnover': row[5] or 0
    })

def get_momentum_features(code, as_of_date):
    """获取某个股票在某个日期的动量特征"""
    default = {'rs_5d': 0, 'rs_20d': 0, 'vol_ratio': 1, 'pct_5d': 0, 'pct_20d': 0, 'turnover': 0, 'new_high_20d': 0}
    klines = kline_by_code.get(code, [])
    if len(klines) < 20:
        return default
    
    # 找as_of_date之前的K线
    idx = -1
    for i, k in enumerate(klines):
        if k['date'] > as_of_date:
            idx = i
            break
    if idx < 0:
        idx = len(klines)
    
    # 取之前最多60根
    recent = klines[max(0, idx-60):idx]
    if len(recent) < 5:
        return default
    
    closes = [k['close'] for k in recent]
    volumes = [k['volume'] for k in recent]
    last = recent[-1]
    
    # 5日 / 20日涨跌幅
    pct_5d = (closes[-1] / closes[max(0, len(closes)-6)] - 1) * 100 if len(closes) >= 6 else 0
    pct_20d = (closes[-1] / closes[max(0, len(closes)-21)] - 1) * 100 if len(closes) >= 21 else 0
    
    # 量比 (当日量 / 5日均量)
    vol_ma5 = sum(volumes[-6:-1]) / 5 if len(volumes) >= 6 else volumes[-1]
    vol_ratio = last['volume'] / vol_ma5 if vol_ma5 > 0 else 1
    
    # 是否创20日新高
    high_20d = max(closes[-20:]) if len(closes) >= 20 else max(closes)
    new_high_20d = 1 if closes[-1] >= high_20d else 0
    
    # 换手率
    turnover = last['turnover'] or 0
    
    return {
        'pct_5d': round(pct_5d, 2),
        'pct_20d': round(pct_20d, 2),
        'vol_ratio': round(min(vol_ratio, 10), 2),
        'new_high_20d': new_high_20d,
        'turnover': round(turnover, 2),
    }

# ===== 4. 构建特征矩阵 =====
print("\n🔧 构建特征矩阵...")
dim_cols = ['tech_weighted', 'fundam_weighted', 'fund_weighted',
            'institutional_weighted', 'lh_institutional_weighted',
            'sentiment_weighted', 'news_event_weighted', 'chip_weighted']

# 对每个月的每个股票，计算截面百分位
def compute_percentile(values):
    """计算每个值在其所在列表中的百分位 (0~1)"""
    arr = np.array(values)
    n = len(arr)
    if n == 0: return []
    # 用rank方法
    order = np.argsort(arr)
    ranks = np.empty(n)
    ranks[order] = np.arange(n)
    return (ranks / max(n-1, 1)).tolist()

# 按月+行业分组计算百分位
monthly_data = defaultdict(list)  # year_month → list of records
for s in all_scores:
    ym = s.get('year_month', s['as_of_date'][:7])
    monthly_data[ym].append(s)

print(f"   共 {len(monthly_data)} 个月份的数据")

features_list = []
labels_list = []
codes_list = []
month_list = []
feature_names = None

months_sorted = sorted(monthly_data.keys())
# 用最近24个月做训练
train_months = months_sorted[-24:] if len(months_sorted) >= 24 else months_sorted
print(f"   训练月份: {train_months[0]} ~ {train_months[-1]} ({len(train_months)}个月)")

for ym in train_months:
    records = monthly_data[ym]
    n = len(records)
    if n < 50: continue
    
    # 计算各维度原始值
    dim_vals = {d: [r.get(d, 0) or 0 for r in records] for d in dim_cols}
    ret20s = np.array([r.get('ret_20d', 0) or 0 for r in records])
    
    # 计算每个维度的截面百分位
    dim_pcts = {}
    for d in dim_cols:
        dim_pcts[d + '_pct'] = compute_percentile(dim_vals[d])
    
    # 综合评分百分位
    avg_scores = [sum(dim_vals[d][i] for d in dim_cols) / len(dim_cols) for i in range(n)]
    avg_score_pct = compute_percentile(avg_scores)
    
    # 不使用 ret_20d 的百分位（会泄露标签）
    # 改用 past_return 类特征——用K线计算的过去涨幅
    # 这些已经在 get_momentum_features 中通过 pct_5d, pct_20d 提供了
    
    for i, s in enumerate(records):
        code = s['code']
        as_of_date = s['as_of_date']
        
        # 基础8维评分
        feats = {}
        for d in dim_cols:
            feats[d] = s.get(d, 0) or 0
        
        # 截面百分位特征
        for d in dim_cols:
            feats[d + '_pct'] = dim_pcts[d + '_pct'][i]
        feats['avg_score_pct'] = avg_score_pct[i]
        
        # 动量特征 (从K线计算)
        mf = get_momentum_features(code, as_of_date)
        feats['pct_5d'] = mf['pct_5d']
        feats['pct_20d'] = mf['pct_20d']
        feats['vol_ratio'] = mf['vol_ratio']
        feats['new_high_20d'] = mf['new_high_20d']
        feats['turnover'] = mf['turnover']
        
        # 行业信息
        industry = industry_map.get(code, '其他')
        feats['industry'] = industry
        
        # 交叉特征
        feats['score_x_momentum'] = avg_scores[i] * (mf['pct_20d'] / 100)
        feats['score_x_vol'] = avg_scores[i] * mf['vol_ratio']
        
        # 标签: ret_20d > 0 → 1
        label = 1 if s.get('ret_20d', 0) or 0 > 0 else 0
        
        features_list.append(feats)
        labels_list.append(label)
        codes_list.append(code)
        month_list.append(ym)
    
    if len(features_list) % 10000 == 0:
        print(f"   已处理 {len(features_list)} 条...")

print(f"\n📊 特征矩阵: {len(features_list)} 条, 正例率: {sum(labels_list)/len(labels_list)*100:.1f}%")

# ===== 5. 准备训练数据 =====
import pandas as pd
df = pd.DataFrame(features_list)

# 行业做 one-hot
industry_dummies = pd.get_dummies(df['industry'], prefix='ind')
df = pd.concat([df.drop('industry', axis=1), industry_dummies], axis=1)

# 特征列
feature_cols = [c for c in df.columns if c not in ['label', 'code', 'month']]
feature_names = feature_cols

X = df[feature_cols].values.astype(np.float32)
y = np.array(labels_list, dtype=np.int32)

print(f"\n特征数: {len(feature_cols)}")
print(f"特征列表: {feature_cols[:15]}...")

# 归一化
scaler = StandardScaler()
X_scaled = scaler.fit_transform(X)

# 划分训练/验证集
X_train, X_val, y_train, y_val = train_test_split(
    X_scaled, y, test_size=0.2, random_state=42, stratify=y
)
print(f"\n训练集: {len(X_train)} 验证集: {len(X_val)}")

# ===== 6. 训练 XGBoost (不使用scale_pos_weight，保持概率校准) =====
print("\n🚀 训练 XGBoost 模型...")

model = xgb.XGBClassifier(
    n_estimators=300,
    max_depth=5,
    learning_rate=0.05,
    subsample=0.8,
    colsample_bytree=0.8,
    min_child_weight=3,
    gamma=0.1,
    eval_metric='auc',
    use_label_encoder=False,
    random_state=42,
)

model.fit(
    X_train, y_train,
    eval_set=[(X_train, y_train), (X_val, y_val)],
    verbose=False
)

# ===== 7. 评估 =====
print("\n📊 模型评估:")

y_train_pred = model.predict_proba(X_train)[:, 1]
y_val_pred = model.predict_proba(X_val)[:, 1]

train_auc = roc_auc_score(y_train, y_train_pred)
val_auc = roc_auc_score(y_val, y_val_pred)
print(f"   训练集 AUC: {train_auc:.4f}")
print(f"   验证集 AUC: {val_auc:.4f}")
print(f"   旧模型 AUC: 0.5515")
print(f"   提升: {val_auc - 0.5515:+.4f}")

# 特征重要性
importance = model.feature_importances_
top_features = sorted(zip(feature_cols, importance), key=lambda x: -x[1])[:20]
print(f"\n🔝 TOP 20 特征重要性:")
for name, imp in top_features:
    print(f"   {name}: {imp:.4f}")

# 概率校准后的效果
for threshold in [0.4, 0.45, 0.5, 0.55, 0.6]:
    preds = (y_val_pred >= threshold).astype(int)
    acc = accuracy_score(y_val, preds)
    prec = sum((preds == 1) & (y_val == 1)) / max(sum(preds), 1)
    rec = sum((preds == 1) & (y_val == 1)) / max(sum(y_val), 1)
    print(f"   阈值={threshold:.2f}: 准确率={acc:.3f} 精确率={prec:.3f} 召回率={rec:.3f}")

# ===== 8. 保存模型 =====
model_path = DATA_DIR / "xgb_model.json"
scaler_path = DATA_DIR / "xgb_scaler.pkl"
model.save_model(str(model_path))
with open(scaler_path, 'wb') as f:
    pickle.dump({'scaler': scaler, 'feature_names': feature_cols}, f)

print(f"\n💾 模型已保存: {model_path}")
print(f"💾 Scaler已保存: {scaler_path}")

# ===== 9. 旧模型对比 =====
print("\n📈 新旧模型对比:")
print(f"{'指标':>20} | {'旧模型(Logistic)':>16} | {'新模型(XGBoost)':>16}")
print("-" * 60)
print(f"{'AUC':>20} | {'0.5515':>16} | {f'{val_auc:.4f}':>16}")
print(f"{'特征数':>20} | {'8':>16} | {f'{len(feature_cols)}':>16}")
print(f"{'样本量':>20} | {'~128K':>16} | {f'{len(features_list)}':>16}")
