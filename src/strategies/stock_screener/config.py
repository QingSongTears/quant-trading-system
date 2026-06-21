"""
选股策略统一配置 — 所有路径基于本文件位置计算，无硬编码绝对路径
"""
from pathlib import Path

# ============================================================
# 路径（相对 — 唯一来源）
# ============================================================
ROOT_DIR = Path(__file__).resolve().parent
# 项目根目录：从 src/strategies/stock_screener/ 向上三级到 quant-trading-system/
PROJECT_ROOT = ROOT_DIR.parent.parent.parent
# 数据文件的实际位置（优先用 A股全市场数据/raw/，fallback 到 data/raw/）
RAW_DATA_DIR = PROJECT_ROOT / "A股全市场数据" / "raw"
REFERENCE_DATA_DIR = PROJECT_ROOT / "A股全市场数据" / "reference"
IMPORTED_DATA_DIR = PROJECT_ROOT / "A股全市场数据" / "imported"
if RAW_DATA_DIR.exists():
    DATA_DIR = RAW_DATA_DIR
else:
    DATA_DIR = ROOT_DIR / "data" / "raw"      # 向后兼容：旧代码直接用 DATA_DIR 加载 CSV
DATA_RAW_DIR = DATA_DIR                  # 新代码用 DATA_RAW_DIR
DATA_PROCESSED_DIR = DATA_DIR / "processed"
OUTPUT_DIR = ROOT_DIR / "output"
CACHE_DIR = ROOT_DIR / "cache"

# 输出子目录（按策略分类）
OUTPUT_V2_DIR = OUTPUT_DIR / "v2"
OUTPUT_V3_DIR = OUTPUT_DIR / "v3"
OUTPUT_V4_DIR = OUTPUT_DIR / "v4"
OUTPUT_COMBINED_DIR = OUTPUT_DIR / "combined"

# 确保目录存在
for _d in [DATA_DIR, DATA_RAW_DIR, DATA_PROCESSED_DIR,
           OUTPUT_DIR, OUTPUT_V2_DIR, OUTPUT_V3_DIR,
           OUTPUT_V4_DIR, OUTPUT_COMBINED_DIR, CACHE_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

# ============================================================
# 筛选层
# ============================================================
MIN_MARKET_CAP = 100              # 市值 ≥ 100 亿
MIN_DAILY_TURNOVER = 50_000_000   # 日成交额 ≥ 5000 万
MAX_PE = 200                      # PE ≤ 200
EXCLUDE_ST = True                 # 排除 ST
EXCLUDE_SUSPENDED = True          # 排除停牌
EXCLUDE_BEI_JIAO_SUO = True       # 排除北交所
EXCLUDE_SECTORS = [
    "银行", "证券", "保险", "地产", "金融",
    "白酒", "中药", "教育", "猪肉",
]

# ============================================================
# 信号层 (EMA 金叉)
# ============================================================
EMA_FAST = 20
EMA_SLOW = 60
REQUIRE_PRICE_ABOVE_EMA = True    # 价格需在 EMA20 上方
VOLUME_RATIO_THRESHOLD = 1.2      # 放量确认阈值
GOLDEN_CROSS_CONFIRM_BARS = 3     # 金叉确认 K 线数
GOLDEN_CROSS_MAX_AGE = 30         # 金叉有效期（天）
MAX_SIGNAL_AGE_DAYS = 30          # 信号最大年龄
MIN_BULLISH_DAYS = 3

# ============================================================
# 量化检测层
# ============================================================
QUANT_THRESHOLD = 0.55
QUANT_SCORE_THRESHOLD = 0.55
QUANT_WEIGHTS = {
    "turnover_volatility": 0.20,
    "price_zigzag":        0.18,
    "tail_anomaly":        0.15,
    "market_decoupling":   0.17,
    "amplitude_return":    0.15,
    "volume_anomaly":      0.15,
}
QUANT_MAX_HOLD_DAYS = 10          # 量化票最长持有天数

# ============================================================
# 买点层
# ============================================================
PULLBACK_MIN = -8.0
PULLBACK_MAX = -0.5
PULLBACK_MA_FAST = 20
PULLBACK_MA_SLOW = 60
HISTORY_LOOKBACK_TIMES = 3
MA_ZONE_MIN = 0.98
MA_ZONE_MAX = 1.05
# 买点评分表 — key 必须与 buy_point.py 中的 category 严格一致
# 评分语义: final_score = min(5, base_score + bonus), buy_ready = final_score >= 2
# 浅回踩 + 缩量企稳 = 最佳买点; 深回踩无反弹 = 风险高(仅靠加分勉强过线)
PULLBACK_SCORE_CONFIG = {
    "no_pullback": {
        "score": 0,
        "desc": "未出现回踩 — 价格在均线上方，等待回调",
    },
    "shallow_pullback_decay": {
        "score": 4,
        "desc": "浅回踩 + 缩量企稳 — 最佳买点",
    },
    "mid_pullback_recovery": {
        "score": 3,
        "desc": "中等回踩 + 缩量企稳 — 良好买点",
    },
    "deep_pullback_no_recovery": {
        "score": 1,
        "desc": "深回踩无反弹 — 风险偏高",
    },
}

# ============================================================
# 风控层
# ============================================================
STOP_LOSS_PCT = -0.10
QUANT_TAKE_PROFIT_PCT = 0.08
NORMAL_TAKE_PROFIT_PCT = 0.15
TAKE_PROFIT_NORMAL = 0.15
TAKE_PROFIT_QUANT = 0.08
MAX_POSITIONS = 5
MAX_POSITION_PCT = 0.22
MAX_SINGLE_POSITION_PCT = 0.22
MAX_TOTAL_POSITION_PCT = 0.95
NORMAL_BREAK_EMA_EXIT = True
MAX_DAILY_SIGNALS = 10

# ============================================================
# 系统
# ============================================================
BACKTEST_START = "2024-06-01"
BACKTEST_END = "2026-06-12"
INITIAL_CAPITAL = 1_000_000
