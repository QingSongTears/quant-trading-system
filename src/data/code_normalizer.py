"""
代码归一化工具
==============

统一不同来源的股票代码格式:
- 整数: 1 → '000001'
- 带前缀: 'sz000001' → 拆出 market + code
- 不带前缀: '000001' → 推断 market (sz/sh/bj)

输出统一格式: 6位数字字符串 '000001'
"""
import re
from typing import Tuple

# 板块前缀规则
SH_PREFIXES = ("60", "68", "90")  # 沪市主板/科创板/B股
SZ_PREFIXES = ("00", "30", "20")  # 深市主板/创业板/B股
BJ_PREFIXES = ("8", "43", "92")   # 北交所


def split_code(raw_code: str) -> Tuple[str, str]:
    """
    把任意格式的 code 拆成 (market, code_normalized)
    返回: ('sz', '000001') / ('sh', '600000') / ('bj', '830001')
    """
    if raw_code is None:
        return "", ""
    s = str(raw_code).strip().lower()
    s = s.replace(".sz", "").replace(".sh", "").replace(".bj", "")

    # 提取前缀
    market = ""
    if s.startswith(("sh", "sz", "bj")):
        market = s[:2]
        s = s[2:]

    # 提取数字
    digits = re.sub(r"\D", "", s)
    if not digits:
        return market, s

    # 推断 market (如果没有)
    if not market:
        if digits.startswith(SH_PREFIXES):
            market = "sh"
        elif digits.startswith(SZ_PREFIXES):
            market = "sz"
        elif digits.startswith(BJ_PREFIXES):
            market = "bj"
        else:
            # 默认: 000xxx=sz, 6xxxxx=sh, 4xxx/8xxx=bj
            if len(digits) == 6 and digits[0] == "6":
                market = "sh"
            elif len(digits) == 6 and digits[0] == "0":
                market = "sz"
            elif len(digits) >= 4 and digits[0] in ("4", "8"):
                market = "bj"
            else:
                market = "sz"  # 默认深市

    code_norm = digits.zfill(6)
    return market, code_norm


def normalize_code(raw_code: str) -> str:
    """统一成 6位字符串 '000001'"""
    _, code = split_code(raw_code)
    return code


def to_westock_code(raw_code: str) -> str:
    """转 westock 接口格式 'sz000001'"""
    market, code = split_code(raw_code)
    if market and code:
        return f"{market}{code}"
    return code


def to_market(raw_code: str) -> str:
    """转市场 'SZ' / 'SH' / 'BJ'"""
    market, _ = split_code(raw_code)
    return market.upper() if market else ""


# 测试
if __name__ == "__main__":
    cases = [
        "1",        # 整数 → 000001
        "000001",   # 已标准化
        "sz000001", # 带前缀
        "000001.SZ",# 带后缀
        "SZ000001", # 大写
        "600000",   # 沪市
        "sh600000",
        "830001",   # 北证
        "400001",   # 北证老格式
    ]
    print(f"{'原值':15s} → {'归一化':10s} {'westock':12s} {'market':8s}")
    print("-" * 50)
    for c in cases:
        n = normalize_code(c)
        w = to_westock_code(c)
        m = to_market(c)
        print(f"{c:15s} → {n:10s} {w:12s} {m:8s}")