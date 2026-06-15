"""
筛选层 — 候选池构建
逐层过滤：板块排除 → 市值 → 流动性 → ST/停牌
使用股票名称关键词匹配排除板块（快速，无需 F10）
"""

import pandas as pd
import re
from typing import List, Set
from config import (
    EXCLUDE_SECTORS, MIN_MARKET_CAP, MIN_DAILY_TURNOVER,
    EXCLUDE_ST, EXCLUDE_SUSPENDED,
)


def screen_candidates(spot_df: pd.DataFrame) -> pd.DataFrame:
    """
    主筛选函数
    输入：全A股实时行情 DataFrame
    输出：符合条件的候选池
    """
    if spot_df.empty:
        print("[SCREENER] 无行情数据，跳过筛选")
        return pd.DataFrame()

    df = spot_df.copy()
    initial = len(df)

    # ===== 第1层：板块排除（基于名称关键词）=====
    df = _exclude_by_name(df)
    after_sector = len(df)
    print(f"[SCREENER] 板块排除: {initial} → {after_sector} (-{initial - after_sector})")

    # ===== 第2层：市值过滤 =====
    df = df[df["mcap_yi"] >= MIN_MARKET_CAP]
    after_mcap = len(df)
    print(f"[SCREENER] 市值≥{MIN_MARKET_CAP}亿: {after_sector} → {after_mcap} (-{after_sector - after_mcap})")

    # ===== 第3层：排除 ST =====
    if EXCLUDE_ST:
        df = df[~df["name"].str.contains("ST|退|\\*ST", na=False, regex=True)]
        after_st = len(df)
        print(f"[SCREENER] 排除ST: {after_mcap} → {after_st} (-{after_mcap - after_st})")
    else:
        after_st = after_mcap

    # ===== 第4层：流动性过滤 =====
    # amount 单位是元 → 万元
    df["avg_amount_wan"] = df["amount"] / 10000
    df = df[df["avg_amount_wan"] >= MIN_DAILY_TURNOVER]
    after_liq = len(df)
    print(f"[SCREENER] 日均成交≥{MIN_DAILY_TURNOVER}万: {after_st} → {after_liq} (-{after_st - after_liq})")

    # ===== 第5层：排除停牌 =====
    if EXCLUDE_SUSPENDED:
        df = df[(df["price"] > 0) & (df["turnover_pct"] > 0)]
        after_sus = len(df)
        print(f"[SCREENER] 排除停牌: {after_liq} → {after_sus} (-{after_liq - after_sus})")
    else:
        after_sus = after_liq

    # ===== 第7层：排除北交所（8开头）=====
    df = df[~df["code"].str.startswith("8")]
    after_bj = len(df)
    if after_bj < after_sus:
        print(f"[SCREENER] 排除北交所: {after_sus} → {after_bj} (-{after_sus - after_bj})")

    # ===== 第8层：排除PE为负（亏损股不适合波段）=====
    df = df[df["pe_ttm"] > 0]
    after_pe = len(df)
    if after_pe < after_bj:
        print(f"[SCREENER] 排除PE为负: {after_bj} → {after_pe} (-{after_bj - after_pe})")

    # ===== 第7层：排除上市不满60日的次新股 =====
    # 简化：排除代码较新的股票（002xxx, 003xxx, 301xxx, 688/689 等批次）
    # 更精确的做法需要上市日期数据

    print(f"\n[SCREENER] === 筛选完成: {after_bj} 只候选 ===")
    return df.reset_index(drop=True)


def _exclude_by_name(df: pd.DataFrame) -> pd.DataFrame:
    """
    基于股票名称关键词排除板块
    名称中出现板块关键词的直接剔除
    """
    # 对各排除板块的关键词映射
    keyword_map = {
        "银行": ["银行"],
        "证券": ["证券", "券商"],
        "保险": ["保险"],
        "金融": ["金融", "信托", "期货", "租赁", "典当"],
        "房地产": ["地产", "置业", "万科"],
        "猪肉": ["猪肉", "养殖", "牧原", "温氏", "新希望", "正邦", "天邦", "唐人神", "巨星农牧"],
        "白酒": ["白酒", "茅台", "五粮液", "泸州老窖", "汾酒", "古井贡", "洋河", "今世缘", "酒鬼", "水井坊", "舍得", "老白干"],
        "中药": ["中药", "中成药", "片仔癀", "同仁堂", "白云山", "云南白药", "东阿阿胶"],
        "教育": ["教育", "培训", "中公", "学大"],
    }

    exclude_set: Set[str] = set()

    for sector, keywords in keyword_map.items():
        for kw in keywords:
            matched = df[df["name"].str.contains(kw, na=False)]
            for c in matched["code"]:
                exclude_set.add(c)

    # 额外排除已知的行业龙头（以防名称不含关键词）
    extra_exclude = set()
    for code, name in zip(df["code"], df["name"]):
        # 市值特别大的银行/保险股
        pass

    exclude_set |= extra_exclude
    return df[~df["code"].isin(exclude_set)]
