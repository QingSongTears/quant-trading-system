"""
SQL 安全工具 — 统一参数化查询入口
================================

背景:
  旧代码大量使用 f-string 拼接用户可控的 code/date 进入 SQL，存在
  SQL 注入风险（虽然当前 code 通常来自 stock_basic，但仍属于反模式，
  且未来若允许用户搜索框直接拼 code，会瞬间成为高危漏洞）。

设计:
  - 提供 `read_sql()` 替代 `pd.read_sql`，强制走 `sqlalchemy.text()` +
    `bindparams(expanding=True)` 路径。
  - list/tuple/set 类型的参数自动识别为 `IN (:key)` 列表绑定。
  - 提供 `text_only()` 用于 `session.execute()` 场景。

注意:
  - 表名/列名不能参数化（SQLite 不支持），必须由代码硬编码或白名单
    选择。本模块不解决"动态表名"问题。
"""
from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine
from sqlalchemy.sql import Executable


def _split_params(
    params: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    将 params 拆为:
      - expanding: list/tuple/set 类型的值（用于 IN 子句）
      - scalar:    标量值
    """
    expanding: dict[str, Any] = {}
    scalar: dict[str, Any] = {}
    if not params:
        return expanding, scalar
    for k, v in params.items():
        if isinstance(v, (list, tuple, set)):
            expanding[k] = list(v)
        else:
            scalar[k] = v
    return expanding, scalar


def read_sql(
    sql: str,
    engine: Engine,
    params: dict[str, Any] | None = None,
    **kwargs,
) -> pd.DataFrame:
    """
    参数化执行 SQL 并返回 DataFrame。

    Args:
        sql:  SQL 模板，使用 `:name` 占位符，例如
              "SELECT * FROM t WHERE code = :code AND dt <= :end"
        engine: SQLAlchemy engine
        params: 参数字典，list/tuple/set 类型的值会自动按 expanding 绑定，
                 可直接用于 `WHERE code IN :codes`
        kwargs: 透传给 `pd.read_sql`

    Examples:
        df = read_sql(
            "SELECT * FROM daily_price WHERE code = :code",
            engine, {"code": "000001"},
        )

        df = read_sql(
            "SELECT * FROM daily_price WHERE code IN :codes",
            engine, {"codes": ["000001", "000002"]},
        )
    """
    stmt = text(sql)
    expanding, scalar = _split_params(params)

    # Bind expanding params on the statement itself (SQLAlchemy will
    # substitute at execute-time). For scalar params, we still bind them
    # so missing values raise a clear error.
    for key in expanding:
        stmt = stmt.bindparams(bindparam(key, expanding=True))
    if scalar:
        stmt = stmt.bindparams(**scalar)

    # Pass the dict (including expanding values) to pd.read_sql.
    # pd.read_sql forwards `params` to `con.execute(sql, params)` which
    # triggers SQLAlchemy's expanding bindparam substitution.
    merged: dict[str, Any] = {**scalar, **expanding}
    return pd.read_sql(stmt, engine, params=merged if merged else None, **kwargs)


def text_only(sql: str, params: dict[str, Any] | None = None) -> Executable:
    """
    直接返回 SQLAlchemy `text()` 表达式（带绑定参数），供
    `session.execute(...)` 或 `connection.execute(...)` 使用。
    """
    stmt = text(sql)
    if not params:
        return stmt
    expanding, scalar = _split_params(params)
    for key in expanding:
        stmt = stmt.bindparams(bindparam(key, expanding=True))
    stmt = stmt.bindparams(**scalar)
    return stmt


__all__ = ["read_sql", "text_only"]