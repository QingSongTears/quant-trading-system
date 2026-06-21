"""
市场代码工具 — SH/SZ/BJ 推断
==============================

⚠️ 历史 bug (PR2.4 修复):
5+ 处文件硬编码 `code.startswith("6")` 推断市场,缺失 BJ(北交所)支持:
- tests/conftest.py:242 `"SH" if code.startswith("6") else "SZ"`
- scripts/test_downloader.py:53
- scripts/import_csv_to_db.py:85
- scripts/import_csv_to_db_v2.py:76
本模块是单一来源,支持 SH/SZ/BJ 三市场。
"""
import re
from typing import Literal

MarketCode = Literal["SH", "SZ", "BJ"]

# A 股市场代码段对照表
# SH: 6xxxxx (沪市主板/科创板), 9xxxxx (B 股)
# SZ: 0xxxxx (深市主板/中小板), 2xxxxx (B 股), 3xxxxx (创业板)
# BJ: 4xxxxx (北交所原新三板精选层), 8xxxxx (北交所)
_MARKET_PATTERNS = [
    ("SH", re.compile(r"^[69]\d{5}$")),
    ("SZ", re.compile(r"^[02]\d{5}$|^3\d{5}$")),
    ("BJ", re.compile(r"^[48]\d{5}$")),
]


def normalize_code(code: str) -> str:
    """剥前缀 + 纯数字

    Examples:
        >>> normalize_code("sh600519") == "600519"
        >>> normalize_code("SZ000001") == "000001"
        >>> normalize_code("600519.SH") == "600519"
        >>> normalize_code("  600519  ") == "600519"
    """
    if not code:
        return ""
    code = code.strip()
    # 剥前缀 sh/SH/sz/SZ/bj/BJ
    code = re.sub(r"^(sh|sz|bj)", "", code, flags=re.IGNORECASE)
    # 剥后缀 .SH/.SZ/.BJ
    code = re.sub(r"\.(sh|sz|bj)$", "", code, flags=re.IGNORECASE)
    return code


def infer_market(code: str) -> MarketCode:
    """根据股票代码推断市场: 'SH' / 'SZ' / 'BJ'

    支持带前缀输入 (sh600519/SZ000001/600519.SH/600519 都识别)
    未知代码默认返回 'SZ' (与历史 startswith 行为兼容)

    Examples:
        >>> infer_market("600519") == "SH"   # 贵州茅台
        >>> infer_market("000001") == "SZ"   # 平安银行
        >>> infer_market("300750") == "SZ"   # 宁德时代 (创业板)
        >>> infer_market("830799") == "BJ"   # 北交所
        >>> infer_market("sh600519") == "SH"
        >>> infer_market("invalid") == "SZ"  # fallback
    """
    pure = normalize_code(code)
    for market, pattern in _MARKET_PATTERNS:
        if pattern.match(pure):
            return market  # type: ignore[return-value]
    return "SZ"  # fallback, 兼容老代码 behavior