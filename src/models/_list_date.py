"""
模型工具函数
==============

集中放置各策略/模型共享的辅助函数, 避免代码重复。

背景:
  三个选股策略 (three_factor / shield_spear / extreme_small_cap)
  都需要把 list_date 转成 "_days_since_list" 过滤列。原实现
  用 `hasattr(d, 'date')` 防御 NaT, 但 NaT 也有 .date() 方法
  (返回 NaT), 导致 `today - NaT.date()` 抛 TypeError,
  整个 universe 过滤崩溃。

  正确做法是用 `pd.isna(d)` 显式判空。
"""
from __future__ import annotations

from datetime import date
from typing import Union

import pandas as pd


def add_days_since_list(
    df: pd.DataFrame,
    list_date_col: str = "list_date",
    as_of: Union[date, pd.Timestamp, None] = None,
) -> pd.DataFrame:
    """
    在 df 上加一列 "_days_since_list", 表示 list_date 到 as_of 的天数。

    NaT / None / 空字符串 / 非法值 都会被替换为 99999 (表示"未上市或未知")。
    调用方应用 `>= min_list_days` 过滤, 自动排除这些行。

    Args:
        df: 包含 list_date 列的 DataFrame
        list_date_col: list_date 列名 (默认 "list_date")
        as_of: 基准日期; 默认 = today()

    Returns:
        原 df 加上 "_days_since_list" 列
    """
    if list_date_col not in df.columns:
        # 列不存在: 用 sentinel 让所有股票都通过过滤 (调用方决定)
        df = df.copy()
        df["_days_since_list"] = 99999
        return df

    if as_of is None:
        as_of_date = date.today()
    elif isinstance(as_of, pd.Timestamp):
        as_of_date = as_of.date()
    elif isinstance(as_of, date):
        as_of_date = as_of
    else:
        as_of_date = as_of

    df = df.copy()
    list_dt = pd.to_datetime(df[list_date_col], errors="coerce")
    days = (pd.Timestamp(as_of_date) - list_dt).dt.days
    # NaT 在减法后保持 NaT, fillna 为 sentinel
    days = days.fillna(99999).astype("int64")
    df["_days_since_list"] = days
    return df


__all__ = ["add_days_since_list"]
