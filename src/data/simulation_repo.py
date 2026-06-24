"""
SimulationRepo — 模拟交易数据访问层 (2026-06-24)

统一从 DataManager 访问模拟交易数据，禁止直接 sqlite3.connect。
底层用 SQLAlchemy engine (src.db.engine.get_engine())。

用法:
    from src.data import data_mgr
    repo = data_mgr.simulation
    runs = repo.list_runs(limit=20)
    perf = repo.get_performance(run_id)
    trades = repo.get_trades(run_id, page=1, page_size=50)
    equity = repo.get_equity(run_id)
    positions = repo.get_positions(run_id)
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import text

from ..db.engine import get_engine

logger = logging.getLogger(__name__)

# ── SQL 常量 ──────────────────────────────────────

_SQL = {
    "LIST_RUNS": (
        "SELECT run_id, model, status, start_date, end_date, "
        "total_return, total_trades, win_rate, "
        "initial_capital, final_capital, created_at "
        "FROM simulation "
        "ORDER BY created_at DESC LIMIT :limit"
    ),
    "GET_RUN": "SELECT * FROM simulation WHERE run_id = :run_id",
    "GET_TRADES": (
        "SELECT * FROM simulation_trades "
        "WHERE run_id = :run_id "
        "ORDER BY exit_date DESC LIMIT :page_size OFFSET :offset"
    ),
    "COUNT_TRADES": "SELECT COUNT(*) FROM simulation_trades WHERE run_id = :run_id",
    "GET_EQUITY": (
        "SELECT date, capital, position_value, total "
        "FROM simulation_equity "
        "WHERE run_id = :run_id ORDER BY date"
    ),
    "GET_POSITIONS": (
        "SELECT DISTINCT code FROM simulation_trades "
        "WHERE run_id = :run_id AND direction = 'BUY'"
    ),
    "GET_ENTRY": (
        "SELECT entry_date, entry_price, entry_size "
        "FROM simulation_trades "
        "WHERE run_id = :run_id AND code = :code AND direction = 'BUY' "
        "ORDER BY entry_date DESC LIMIT 1"
    ),
}


def _row_to_dict(row) -> dict:
    """把 sqlalchemy Row 转 dict"""
    return dict(row._mapping)


class SimulationRepo:
    """模拟交易数据访问 (只读, 写操作由 Simulator 负责)

    设计:
      - 所有查询走 SQLAlchemy engine (单例, 线程安全)
      - 不缓存, 每次查最新数据
      - 查不到时返回 [] / None / {}, 不抛异常 (API 层决定 404)
    """

    def __init__(self) -> None:
        self._engine = None  # lazy

    def _get_engine(self):
        if self._engine is None:
            self._engine = get_engine()
        return self._engine

    # ── 运行记录 ─────────────────────────────────

    def list_runs(self, limit: int = 20) -> list[dict]:
        """列出所有模拟运行 (最新在前)"""
        engine = self._get_engine()
        with engine.connect() as conn:
            rows = conn.execute(
                text(_SQL["LIST_RUNS"]), {"limit": limit}
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_run(self, run_id: str) -> Optional[dict]:
        """获取单次运行记录, 不存在返回 None"""
        engine = self._get_engine()
        with engine.connect() as conn:
            row = conn.execute(
                text(_SQL["GET_RUN"]), {"run_id": run_id}
            ).fetchone()
        return _row_to_dict(row) if row else None

    # ── 交易明细 ─────────────────────────────────

    def get_trades(
        self,
        run_id: str,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        """分页获取交易明细

        Returns:
            {"total": int, "page": int, "page_size": int, "trades": list[dict]}
        """
        engine = self._get_engine()
        offset = (page - 1) * page_size
        with engine.connect() as conn:
            rows = conn.execute(
                text(_SQL["GET_TRADES"]),
                {"run_id": run_id, "page_size": page_size, "offset": offset},
            ).fetchall()
            total = conn.execute(
                text(_SQL["COUNT_TRADES"]), {"run_id": run_id}
            ).fetchone()[0]
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "trades": [_row_to_dict(r) for r in rows],
        }

    # ── 净值曲线 ─────────────────────────────────

    def get_equity(self, run_id: str) -> list[dict]:
        """获取净值曲线 (按日期升序)"""
        engine = self._get_engine()
        with engine.connect() as conn:
            rows = conn.execute(
                text(_SQL["GET_EQUITY"]), {"run_id": run_id}
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    # ── 持仓 ─────────────────────────────────────

    def get_positions(self, run_id: str) -> list[dict]:
        """获取当前持仓 (未平仓的 BUY 记录)

        逻辑: code 有 BUY 但无对应 SELL, 则认为仍持仓
        """
        engine = self._get_engine()
        with engine.connect() as conn:
            # 有 BUY 且无对应 SELL 的 code
            codes = conn.execute(
                text(_SQL["GET_POSITIONS"]), {"run_id": run_id}
            ).fetchall()
            # 排除已平仓的 (有 SELL 的 code)
            # 简化: 直接用 SQL 查 "BUY 且不存在 SELL" 的 code
            result = []
            for (code,) in codes:
                # 检查是否有 SELL
                has_sell = conn.execute(
                    text(
                        "SELECT 1 FROM simulation_trades "
                        "WHERE run_id = :run_id AND code = :code AND direction = 'SELL' "
                        "LIMIT 1"
                    ),
                    {"run_id": run_id, "code": code},
                ).fetchone()
                if not has_sell:
                    entry = conn.execute(
                        text(_SQL["GET_ENTRY"]),
                        {"run_id": run_id, "code": code},
                    ).fetchone()
                    if entry:
                        result.append({
                            "code": code,
                            "entry_date": entry[0],
                            "entry_price": entry[1],
                            "size": entry[2],
                        })
        return result

    # ── 快捷: 组合查询 ─────────────────────────

    def get_performance(self, run_id: str) -> Optional[dict]:
        """获取绩效指标 (即 simulation 表一行)"""
        return self.get_run(run_id)
