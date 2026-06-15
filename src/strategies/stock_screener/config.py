"""
选股策略配置 — A股波段选股系统
目标：每日收盘后扫描全A股，筛选出符合EMA金叉+回调买点的波段标的
"""

from dataclasses import dataclass, field
from typing import List, Dict

# ============================================================
# 一、筛选层配置
# ============================================================

# 排除的行业/概念板块（东方财富行业分类中的板块名称关键词）
EXCLUDED_SECTORS: List[str] = [
    "银行", "证券", "保险", "多元金融", "房地产", "房地产开发",
    "猪肉", "白酒", "中药", "教育",
]

# 市值门槛（亿元）
MIN_MARKET_CAP = 100  # ≥ 100亿

# 日均成交额门槛（万元）
MIN_DAILY_TURNOVER = 5000  # ≥ 5000万

# 排除上市不满 N 个自然日的新股
MIN_LISTED_DAYS = 60

# 排除 ST / *ST
EXCLUDE_ST = True

# 排除停牌
EXCLUDE_SUSPENDED = True

# 排除科创板和创业板？（默认 False，不排除）
EXCLUDE_KECHUANG = False
EXCLUDE_CHUANGYE = False

# ============================================================
# 二、信号层配置 — EMA 金叉 + 多头确认
# ============================================================

# EMA 快线周期
EMA_FAST = 20

# EMA 慢线周期
EMA_SLOW = 60

# 金叉确认：需要收盘价站在两条均线之上
REQUIRE_PRICE_ABOVE_EMA = True

# 金叉质量要求：金叉日成交量 ≥ N 倍 20日均量
VOLUME_RATIO_THRESHOLD = 1.2

# 需要多少根金叉K线（1 = 当日金叉即算，3 = 金叉后站稳3日）
GOLDEN_CROSS_CONFIRM_BARS = 1

# 金叉信号时效性（天），超过此天数的金叉视为过期
MAX_SIGNAL_AGE_DAYS = 30

# ============================================================
# 三、量化检测层配置
# ============================================================

QUANT_THRESHOLD = 0.6  # 量化分 ≥ 0.6 判定为量化票

# 各维度权重
QUANT_WEIGHTS: Dict[str, float] = {
    "turnover_volatility": 0.20,   # 换手率波动率
    "price_sawtooth": 0.18,        # 价格锯齿度（影线占比）
    "tail_manipulation": 0.15,     # 尾盘异动率
    "market_decoupling": 0.17,     # 大盘脱敏度
    "amplitude_yield_ratio": 0.15, # 振幅/涨幅比
    "volume_anomaly": 0.15,        # 成交量异常度
}

# 量化票持仓上限（交易日）
QUANT_MAX_HOLD_DAYS = 5

# ============================================================
# 四、买点层配置
# ============================================================

# 回调目标区间：MA20 ~ MA60
PULLBACK_MA_FAST = 20
PULLBACK_MA_SLOW = 60

# 历史回踩参考次数（取最近 N 次金叉后的回踩数据）
HISTORY_LOOKBACK_TIMES = 5

# 回踩权重评分标准
PULLBACK_SCORE_CONFIG = {
    "shallow_pullback_decay": {"score": 3, "desc": "浅回踩+缩量企稳"},
    "mid_pullback_recovery": {"score": 2, "desc": "中等回踩+放量反弹"},
    "deep_pullback_no_recovery": {"score": 1, "desc": "深回踩+缩量不反弹"},
    "no_pullback": {"score": 0, "desc": "未出现回踩机会"},
}

# ============================================================
# 五、风控层配置
# ============================================================

# 固定止损比例
STOP_LOSS_PCT = -0.10  # -10%

# 量化票止盈
QUANT_TAKE_PROFIT_PCT = 0.08  # +8%

# 正常票止盈
NORMAL_TAKE_PROFIT_PCT = 0.15  # +15%
NORMAL_BREAK_EMA_EXIT = True   # 跌破 EMA20 也触发止盈/离场

# 最大持仓数
MAX_POSITIONS = 5

# 单票最大仓位占比
MAX_SINGLE_POSITION_PCT = 0.20

# 总仓位上限
MAX_TOTAL_POSITION_PCT = 0.80

# ============================================================
# 六、系统配置
# ============================================================
CACHE_DIR = "/workspace/stock-screener/cache"
OUTPUT_DIR = "/workspace/stock-screener/output"
