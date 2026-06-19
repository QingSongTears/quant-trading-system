
import json, pandas as pd, numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
import warnings
warnings.filterwarnings('ignore')

# 1. 加载数据
print('[1] 加载 all_7d_scores.json ...')
with open('data/all_7d_scores.json') as f:
    scores = json.load(f)
print(f'    记录数: {len(scores)}')

df = pd.DataFrame(scores)
dim_cols = [c for c in df.columns if c.endswith('_weighted')]
print(f'    维度列: {dim_cols}')

# 2. 计算下月收益率 (shift(-1) per code)
df['as_of_date'] = pd.to_datetime(df['as_of_date'])
df = df.sort_values(['code','as_of_date']).reset_index(drop=True)
df['ret_20d_next'] = df.groupby('code')['ret_20d'].shift(-1)
df['up'] = (df['ret_20d_next'] > 0).astype(int)

valid = df.dropna(subset=['ret_20d_next']).copy()
print(f'    有效样本: {len(valid)}')

# 3. IC分析
print('\n[2] IC分析 (8维 vs ret_20d_next) ...')
ic_rows = []
for dim in dim_cols:
    mask = valid[[dim, 'ret_20d_next']].notna().all(axis=1)
    n = mask.sum()
    if n < 50:
        ic_rows.append({'dim': dim, 'ic': np.nan, 'n': n})
        continue
    ic = valid.loc[mask, dim].corr(valid.loc[mask, 'ret_20d_next'])
    ic_rows.append({'dim': dim, 'ic': round(ic, 4), 'n': n})
ic_df = pd.DataFrame(ic_rows).sort_values('ic', key=abs, ascending=False)
print(ic_df.to_string(index=False))

# 4. 逻辑回归
print('\n[3] 训练逻辑回归 ...')
X = valid[dim_cols].fillna(0).values
y = valid['up'].values
model = LogisticRegression(C=1.0, max_iter=500, solver='lbfgs')
model.fit(X, y)
proba = model.predict_proba(X)[:,1]
auc = roc_auc_score(y, proba)
print(f'    AUC = {auc:.4f}')

# 5. 概率分层
print('\n[4] 概率分层 ...')
valid['proba'] = proba
valid['bin'] = pd.cut(proba, bins=[0,0.4,0.5,0.6,0.7,0.8,0.9,1.0],
                       labels=['<0.4','0.4-0.5','0.5-0.6','0.6-0.7','0.7-0.8','0.8-0.9','0.9+'])
bin_stats = valid.groupby('bin').agg(
    count=('up','count'),
    pct_up=('up','mean'),
    avg_ret=('ret_20d_next','mean')
).reset_index()
print(bin_stats.to_string(index=False))

# 6. 维度中文名映射
dim_cn = {
    'tech_weighted': '技术面',
    'fundam_weighted': '基本面',
    'fund_weighted': '资金面',
    'institutional_weighted': '机构面',
    'lh_institutional_weighted': '龙虎榜机构',
    'sentiment_weighted': '情绪面',
    'news_event_weighted': '新闻事件',
    'chip_weighted': '筹码面'
}

# 7. 保存结果
weights = {dim_cols[i]: float(model.coef_[0][i]) for i in range(len(dim_cols))}
weights_cn = {dim_cn.get(k,k): v for k,v in weights.items()}
result = {
    'ic_table': ic_df.to_dict('records'),
    'dim_cn_map': dim_cn,
    'logistic_coef': weights,
    'logistic_coef_cn': weights_cn,
    'intercept': float(model.intercept_[0]),
    'auc': round(auc, 4),
    'bin_stats': bin_stats.to_dict('records')
}
with open('data/bull_8d_monthly_result.json', 'w') as f:
    json.dump(result, f, indent=2, ensure_ascii=False)
print('\n结果已保存至 data/bull_8d_monthly_result.json')
