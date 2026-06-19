"""
月度8维预测模型 - 预测函数
加载训练好的权重，对新股票进行下月收益率方向预测
"""
import json, pandas as pd, numpy as np
import os

MODEL_PATH = os.path.join(os.path.dirname(__file__), '../data/bull_8d_monthly_result.json')

def load_model():
    """加载训练好的模型权重"""
    with open(MODEL_PATH) as f:
        result = json.load(f)
    return result

def predict_proba(df_scores, model_result=None):
    """
    用8维分数预测下月上涨概率
    
    Parameters
    ----------
    df_scores : DataFrame，必须包含8个维度列:
        tech_weighted, fundam_weighted, fund_weighted, 
        institutional_weighted, lh_institutional_weighted,
        sentiment_weighted, news_event_weighted, chip_weighted
    model_result : dict, optional，预加载的模型结果
    
    Returns
    -------
    np.array: 上涨概率 (0-1)
    """
    if model_result is None:
        model_result = load_model()
    
    dim_cols = list(model_result['logistic_coef'].keys())
    coef = np.array([model_result['logistic_coef'][c] for c in dim_cols])
    intercept = model_result['intercept']
    
    X = df_scores[dim_cols].fillna(0).values
    logits = X @ coef + intercept
    proba = 1 / (1 + np.exp(-logits))
    return proba

def predict_batch(scores_json_path=None):
    """
    批量预测：加载 all_7d_scores.json，输出每行的上涨概率
    """
    if scores_json_path is None:
        scores_json_path = os.path.join(os.path.dirname(__file__), '../data/all_7d_scores.json')
    
    with open(scores_json_path) as f:
        scores = json.load(f)
    
    df = pd.DataFrame(scores)
    model_result = load_model()
    proba = predict_proba(df, model_result)
    
    df['pred_proba_up'] = proba
    df['pred_signal'] = np.select(
        [proba < 0.4, proba < 0.55, proba >= 0.55],
        ['回避', '中性', '买入'],
        default='中性'
    )
    return df[['code', 'as_of_date', 'pred_proba_up', 'pred_signal']]

if __name__ == '__main__':
    # 测试
    result = load_model()
    print('模型加载成功')
    print(f'AUC: {result["auc"]}')
    print(f'权重: {result["logistic_coef_cn"]}')
    
    # 批量预测
    df = predict_batch()
    print(f'\n预测样本数: {len(df)}')
    print(df.head(10).to_string())
    print(f'\n信号分布:')
    print(df['pred_signal'].value_counts())
