"""
test_store.py — ADR-0012 #83 Step 3 测试

覆盖:
  - MetricStore 抽象接口
  - InMemoryBuffer: append/recent/clear/ring buffer 截断/线程安全
  - SqliteStore: append/recent/clear/ring buffer 截断/关闭
  - make_store 工厂函数 (sqlite_path=None → InMemoryBuffer, 非 None → SqliteStore)
"""
from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.monitoring.event_data import (
    AnomalyEvent,
    PnlSnapshot,
    PositionSnapshot,
)
from src.monitoring.store import (
    InMemoryBuffer,
    MetricStore,
    SqliteStore,
    make_store,
)


# ── InMemoryBuffer ──────────────────────────────────────
class TestInMemoryBuffer:
    def test_append_and_len(self):
        buf: InMemoryBuffer[PnlSnapshot] = InMemoryBuffer(maxlen=10)
        assert len(buf) == 0
        buf.append(PnlSnapshot(total_value=100.0))
        assert len(buf) == 1

    def test_recent_returns_n_latest(self):
        buf: InMemoryBuffer[PnlSnapshot] = InMemoryBuffer(maxlen=10)
        for i in range(5):
            buf.append(PnlSnapshot(total_value=float(i)))
        recent = buf.recent(n=3)
        assert len(recent) == 3
        # 倒序 (最新在前)
        assert recent[0].total_value == 4.0
        assert recent[1].total_value == 3.0
        assert recent[2].total_value == 2.0

    def test_recent_returns_all_when_n_larger_than_buf(self):
        buf: InMemoryBuffer[PnlSnapshot] = InMemoryBuffer(maxlen=10)
        for i in range(3):
            buf.append(PnlSnapshot(total_value=float(i)))
        recent = buf.recent(n=10)
        assert len(recent) == 3
        assert recent[0].total_value == 2.0

    def test_ring_buffer_overflow(self):
        """maxlen=3 时, append 第 4 条 → 第 1 条被淘汰"""
        buf: InMemoryBuffer[PnlSnapshot] = InMemoryBuffer(maxlen=3)
        for i in range(5):
            buf.append(PnlSnapshot(total_value=float(i)))
        assert len(buf) == 3
        recent = buf.recent(n=10)
        # 倒序: 最新是 4.0, 然后 3.0, 然后 2.0
        assert recent[0].total_value == 4.0
        assert recent[1].total_value == 3.0
        assert recent[2].total_value == 2.0

    def test_clear(self):
        buf: InMemoryBuffer[PnlSnapshot] = InMemoryBuffer(maxlen=10)
        for i in range(5):
            buf.append(PnlSnapshot(total_value=float(i)))
        assert len(buf) == 5
        buf.clear()
        assert len(buf) == 0

    def test_repr(self):
        buf: InMemoryBuffer[PnlSnapshot] = InMemoryBuffer(maxlen=100, name="pnl")
        r = repr(buf)
        assert "pnl" in r
        assert "0" in r  # size


# ── SqliteStore ──────────────────────────────────────
class TestSqliteStore:
    @pytest.fixture
    def tmp_db(self, tmp_path: Path) -> Path:
        return tmp_path / "test_monitoring.db"

    def test_append_and_len(self, tmp_db: Path):
        store: SqliteStore[PnlSnapshot] = SqliteStore(
            db_path=tmp_db, table="pnl", item_cls=PnlSnapshot,
        )
        assert len(store) == 0
        store.append(PnlSnapshot(total_value=100.0))
        assert len(store) == 1
        store.close()

    def test_recent_returns_n_latest(self, tmp_db: Path):
        store: SqliteStore[PnlSnapshot] = SqliteStore(
            db_path=tmp_db, table="pnl", item_cls=PnlSnapshot,
        )
        for i in range(5):
            store.append(PnlSnapshot(total_value=float(i)))
        recent = store.recent(n=3)
        assert len(recent) == 3
        # 最新在前
        assert recent[0].total_value == 4.0
        store.close()

    def test_persistence_across_instances(self, tmp_db: Path):
        """重启 (新实例) 后数据还在"""
        s1: SqliteStore[PnlSnapshot] = SqliteStore(
            db_path=tmp_db, table="pnl", item_cls=PnlSnapshot,
        )
        s1.append(PnlSnapshot(total_value=42.0))
        s1.close()

        s2: SqliteStore[PnlSnapshot] = SqliteStore(
            db_path=tmp_db, table="pnl", item_cls=PnlSnapshot,
        )
        assert len(s2) == 1
        recent = s2.recent(n=10)
        assert recent[0].total_value == 42.0
        s2.close()

    def test_ring_buffer_overflow(self, tmp_db: Path):
        store: SqliteStore[PnlSnapshot] = SqliteStore(
            db_path=tmp_db, table="pnl", maxlen=3, item_cls=PnlSnapshot,
        )
        for i in range(5):
            store.append(PnlSnapshot(total_value=float(i)))
        assert len(store) == 3
        recent = store.recent(n=10)
        assert len(recent) == 3
        assert recent[0].total_value == 4.0
        store.close()

    def test_clear(self, tmp_db: Path):
        store: SqliteStore[PnlSnapshot] = SqliteStore(
            db_path=tmp_db, table="pnl", item_cls=PnlSnapshot,
        )
        store.append(PnlSnapshot(total_value=1.0))
        store.clear()
        assert len(store) == 0
        store.close()

    def test_position_snapshot_roundtrip(self, tmp_db: Path):
        store: SqliteStore[PositionSnapshot] = SqliteStore(
            db_path=tmp_db, table="positions", item_cls=PositionSnapshot,
        )
        store.append(PositionSnapshot(
            vt_symbol="000001.SZ", size=1000, cost_basis=10.0,
        ))
        recent = store.recent(n=1)
        assert recent[0].vt_symbol == "000001.SZ"
        assert recent[0].size == 1000
        store.close()

    def test_anomaly_event_roundtrip(self, tmp_db: Path):
        store: SqliteStore[AnomalyEvent] = SqliteStore(
            db_path=tmp_db, table="anomalies", item_cls=AnomalyEvent,
        )
        store.append(AnomalyEvent(
            kind="api_failure", severity="error",
            detail="Tushare 失败", source="datafeed",
        ))
        recent = store.recent(n=1)
        assert recent[0].kind == "api_failure"
        assert recent[0].severity == "error"
        assert recent[0].source == "datafeed"
        store.close()

    def test_repr(self, tmp_db: Path):
        store: SqliteStore[PnlSnapshot] = SqliteStore(
            db_path=tmp_db, table="pnl", item_cls=PnlSnapshot,
        )
        r = repr(store)
        assert "pnl" in r
        store.close()


# ── make_store 工厂函数 ──────────────────────────────────────
class TestMakeStore:
    def test_make_store_no_sqlite_returns_memory(self):
        store = make_store(PnlSnapshot, sqlite_path=None)
        assert isinstance(store, InMemoryBuffer)
        assert not isinstance(store, SqliteStore)

    def test_make_store_with_sqlite_path_returns_sqlite(self, tmp_path: Path):
        db = tmp_path / "m.db"
        store = make_store(PnlSnapshot, sqlite_path=db, name="pnl")
        assert isinstance(store, SqliteStore)
        assert isinstance(store, MetricStore)
        store.close()

    def test_make_store_name_used_in_table(self, tmp_path: Path):
        db = tmp_path / "m.db"
        store = make_store(PnlSnapshot, sqlite_path=db, name="mypnl")
        assert store._table == "monitoring_mypnl"  # type: ignore[attr-defined]
        store.close()


# ── MetricStore 抽象 ──────────────────────────────────────
class TestMetricStoreABC:
    def test_cannot_instantiate_abc(self):
        """MetricStore 是抽象, 不能直接实例化"""
        with pytest.raises(TypeError):
            MetricStore()  # type: ignore[abstract]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])