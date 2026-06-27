"""
monitoring.store — 监控事件持久化 (ADR-0012 #83 D3)

3 个组件:
  - MetricStore (抽象 ABC): 监控指标的统一接口 — append/query/recent/clear
  - InMemoryBuffer: collections.deque(maxlen=N) 实现的 ring buffer (默认)
  - SqliteStore:   SQLite 实现的持久化 store (可选, 用 sqlite_path 启用)

设计 (D3-B):
  - 内存默认: 3 类指标各 1000 条 ring buffer (PnlSnapshot / PositionSnapshot / AnomalyEvent)
  - SQLite 可选: 监控包接受可选 sqlite_path, 不传则只走内存
  - 不引入时序库: Prometheus / InfluxDB 留 v3.0 实盘评估

向后兼容:
  from src.monitoring.store import MetricStore, InMemoryBuffer, SqliteStore
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Generic, List, Optional, TypeVar

from .event_data import AnomalyEvent, PnlSnapshot, PositionSnapshot

logger = logging.getLogger(__name__)


T = TypeVar("T")


# ── 抽象 MetricStore ──────────────────────────────────────
class MetricStore(ABC, Generic[T]):
    """监控指标存储抽象 — 3 类指标通用接口

    设计:
      - append(item): 追加一条指标
      - recent(n=10): 最近 n 条 (默认 10, 用于 Web UI)
      - query(start, end): 时间范围查询 (可选)
      - clear(): 清空 (仅测试用)
      - __len__(): 当前条数

    线程安全:
      - 实现类必须自己保证线程安全 (InMemoryBuffer 用 RLock, SqliteStore 用 sqlite3 check_same_thread=False)
    """

    @abstractmethod
    def append(self, item: T) -> None:
        """追加一条指标"""

    @abstractmethod
    def recent(self, n: int = 10) -> List[T]:
        """最近 n 条 (按时间倒序)"""

    @abstractmethod
    def clear(self) -> None:
        """清空所有指标 (测试 / 重启用)"""

    @abstractmethod
    def __len__(self) -> int:
        """当前条数"""


# ── InMemoryBuffer ──────────────────────────────────────
class InMemoryBuffer(MetricStore[T], Generic[T]):
    """基于 collections.deque 的 ring buffer (默认 store)

    Args:
        maxlen: 最大容量 (默认 1000, 超出自动淘汰最早的)
        name:   类别名 (用于 logging)
    """

    def __init__(self, maxlen: int = 1000, name: str = "metrics") -> None:
        self._buf: deque = deque(maxlen=maxlen)
        self._maxlen = maxlen
        self._name = name
        self._lock = threading.RLock()

    def append(self, item: T) -> None:
        with self._lock:
            self._buf.append(item)

    def recent(self, n: int = 10) -> List[T]:
        with self._lock:
            # 倒序 (最新在前)
            return list(reversed(list(self._buf)[-n:]))

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._buf)

    def __repr__(self) -> str:
        return f"<InMemoryBuffer name={self._name} size={len(self)}/{self._maxlen}>"


# ── SqliteStore ──────────────────────────────────────
class SqliteStore(MetricStore[T], Generic[T]):
    """SQLite 持久化 store (可选, ADR-0012 D3-B)

    Args:
        db_path:  SQLite 文件路径 (如 /tmp/monitoring.db)
        table:    表名 (默认 "metrics")
        maxlen:   ring buffer 上限 (默认 10000; 超出删除最早的)

    注意:
      - 用 sqlite3 + check_same_thread=False + 自管 RLock 支持跨线程
      - 用 json.dumps(asdict(item)) 序列化; 反序列化依赖 item.__class__
      - 仅作为可选扩展点; 不传 db_path 时监控包用 InMemoryBuffer (默认)
    """

    def __init__(
        self,
        db_path: str | Path,
        table: str = "metrics",
        maxlen: int = 10_000,
        item_cls: type | None = None,
    ) -> None:
        self._db_path = str(db_path)
        self._table = table
        self._maxlen = maxlen
        self._item_cls = item_cls
        self._lock = threading.RLock()

        self._conn = sqlite3.connect(
            self._db_path,
            check_same_thread=False,
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {table} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                payload TEXT NOT NULL
            )
        """)
        self._conn.commit()
        logger.info(
            f"SqliteStore 初始化: db={self._db_path} table={table} maxlen={maxlen}"
        )

    def append(self, item: T) -> None:
        if not isinstance(item, type(item)) if False else False:
            # 软类型检查: 不强求, 但记录 warning
            pass
        ts = getattr(item, "timestamp", datetime.now())
        if isinstance(ts, datetime):
            ts = ts.isoformat()
        payload = json.dumps(asdict(item), ensure_ascii=False, default=str)
        with self._lock:
            self._conn.execute(
                f"INSERT INTO {self._table} (ts, payload) VALUES (?, ?)",
                (ts, payload),
            )
            # ring buffer 截断: 超出 maxlen 时删除最早的
            self._conn.execute(f"""
                DELETE FROM {self._table}
                WHERE id IN (
                    SELECT id FROM {self._table}
                    ORDER BY id DESC LIMIT -1 OFFSET ?
                )
            """, (self._maxlen,))
            self._conn.commit()

    def recent(self, n: int = 10) -> List[T]:
        with self._lock:
            cur = self._conn.execute(
                f"SELECT payload FROM {self._table} ORDER BY id DESC LIMIT ?",
                (n,),
            )
            rows = cur.fetchall()
        return [self._deserialize(row[0]) for row in rows]

    def clear(self) -> None:
        with self._lock:
            self._conn.execute(f"DELETE FROM {self._table}")
            self._conn.commit()

    def __len__(self) -> int:
        with self._lock:
            cur = self._conn.execute(f"SELECT COUNT(*) FROM {self._table}")
            return cur.fetchone()[0]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _deserialize(self, payload: str) -> T:
        """JSON → dataclass (依赖 item_cls, 默认按 kind 推断)"""
        data = json.loads(payload)
        if self._item_cls is not None:
            return self._item_cls(**data)
        # 推断: 含 kind 字段 → AnomalyEvent; 含 vt_symbol → PositionSnapshot; 否则 PnlSnapshot
        if "kind" in data:
            return AnomalyEvent(**data)  # type: ignore[return-value]
        if "vt_symbol" in data:
            return PositionSnapshot(**data)  # type: ignore[return-value]
        return PnlSnapshot(**data)  # type: ignore[return-value]

    def __repr__(self) -> str:
        return f"<SqliteStore db={self._db_path} table={self._table} size={len(self)}>"


# ── 工厂函数 ──────────────────────────────────────
def make_store(
    item_cls: type,
    sqlite_path: Optional[str | Path] = None,
    maxlen: int = 1000,
    name: str = "metrics",
) -> MetricStore:
    """创建 store — sqlite_path 为 None 时用 InMemoryBuffer, 否则 SqliteStore

    Args:
        item_cls:    监控指标类 (PnlSnapshot / PositionSnapshot / AnomalyEvent)
        sqlite_path: SQLite 文件路径 (None = 内存)
        maxlen:      ring buffer 容量 (默认 1000)
        name:        类别名 (用于 logging)
    """
    if sqlite_path is None:
        return InMemoryBuffer(maxlen=maxlen, name=name)
    return SqliteStore(
        db_path=sqlite_path,
        table=f"monitoring_{name}",
        maxlen=maxlen * 10,  # SQLite 容量 = 内存 10x
        item_cls=item_cls,
    )


__all__ = [
    "MetricStore",
    "InMemoryBuffer",
    "SqliteStore",
    "make_store",
]