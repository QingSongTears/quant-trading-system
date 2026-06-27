"""
A 股 TOP20 持仓股行业字典 (VNPY-3, ADR-0007 修复 3)

设计:
  - v2.2 简化版: 硬编码 20 只常见持仓股, 便于快速落地
  - v3.0 接申万行业分类 (stock_basic.industry), 通过 src.selection.sector_constraint.load_industry_map
  - 默认返回 UNKNOWN ("未知"), 未知行业视为"单独一类" (与 sector_constraint 一致)

用法:
    from src.risk.sector_map import get_sector

    sector = get_sector("600519.SH")   # → "白酒"
    sector = get_sector("999999.SH")   # → "未知"
"""
from __future__ import annotations

UNKNOWN = "未知"  # 与 src.selection.sector_constraint.UNKNOWN 对齐

# 行业字典 — TOP20 持仓股 (示例覆盖: 消费/银行/科技/医药/新能源/汽车/地产 等)
# vt_symbol 格式: "<6位代码>.<交易所>" (SH=沪 / SZ=深)
_SECTOR_MAP: dict[str, str] = {
    # ── 消费 / 白酒 ──
    "600519.SH": "白酒",      # 贵州茅台
    "000858.SZ": "白酒",      # 五粮液
    "000568.SZ": "白酒",      # 泸州老窖
    "600809.SH": "白酒",      # 山西汾酒

    # ── 银行 ──
    "600036.SH": "银行",      # 招商银行
    "601318.SH": "银行",      # 中国平安 (保险, 归金融)
    "601398.SH": "银行",      # 工商银行
    "601939.SH": "银行",      # 建设银行
    "000001.SZ": "银行",      # 平安银行

    # ── 保险 / 证券 ──
    "601628.SH": "保险",      # 中国人寿
    "600030.SH": "证券",      # 中信证券

    # ── 家电 / 汽车 ──
    "000333.SZ": "家电",      # 美的集团
    "000651.SZ": "家电",      # 格力电器
    "600690.SH": "家电",      # 海尔智家
    "601633.SH": "汽车",      # 长城汽车
    "002594.SZ": "汽车",      # 比亚迪

    # ── 科技 / 半导体 ──
    "002415.SZ": "科技",      # 海康威视
    "300750.SZ": "新能源",    # 宁德时代
    "300059.SZ": "金融",      # 东方财富 (券商 + 互联网金融)

    # ── 医药 ──
    "600276.SH": "医药",      # 恒瑞医药
    "000538.SZ": "医药",      # 云南白药
}


def get_sector(vt_symbol: str) -> str:
    """查询标的所属行业 (默认 "未知")

    Args:
        vt_symbol: vt 格式 symbol, 如 "600519.SH" / "000001.SZ"

    Returns:
        行业字符串; 未命中则返回 "未知" (与 sector_constraint 对齐, 视为单独一类)

    设计:
        - 不抛异常 (风控热路径, 异常 → 未知, 不阻断)
        - 大小写不敏感 (兼容 "600519.sh" / "600519.SH")
        - 缺 vt_symbol 后缀时按原 key 查找 (宽松模式)
    """
    if not vt_symbol:
        return UNKNOWN
    key = vt_symbol.upper().strip()
    return _SECTOR_MAP.get(key, UNKNOWN)


def known_count() -> int:
    """返回字典大小 (调试 / 单测用)"""
    return len(_SECTOR_MAP)


def all_sectors() -> list[str]:
    """返回字典覆盖的所有去重行业 (调试 / 单测用)"""
    return sorted(set(_SECTOR_MAP.values()))


__all__ = ["UNKNOWN", "get_sector", "known_count", "all_sectors"]