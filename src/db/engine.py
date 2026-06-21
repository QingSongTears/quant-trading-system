"""
SQLAlchemy Engine 单例模块

提供线程安全的全局 engine 复用，避免重复创建数据库连接池。
需要项目根路径处有 src.config.get_config() 可用。

用法:
  from src.db.engine import get_engine

  engine = get_engine()                     # 默认配置
  engine2 = get_engine(config)              # 自定义配置 (首次设置)

CRON/定时任务可用 reset_engine_cache() 释放陈旧连接。
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from sqlalchemy import Engine, create_engine, event

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_lock = threading.Lock()


def get_engine(config: Any = None) -> Engine:
    """
    获取全局唯一的 SQLAlchemy Engine 实例

    首次调用必须传入 config 或项目已配置好 src.config。
    后续调用无需参数，直接返回缓存的 engine。

    参数:
        config: 项目配置对象 (可选，首次调用时传入)
    返回:
        Engine: SQLAlchemy Engine 实例
    """
    global _engine
    if _engine is not None:
        return _engine

    with _lock:
        if _engine is not None:
            return _engine

        if config is None:
            from src.config import get_config
            config = get_config()

        from src.config import get_db_url
        db_url = get_db_url(config)
        _engine = create_engine(
            db_url,
            echo=False,
            pool_pre_ping=True,
            pool_recycle=3600,
        )

        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            """启用 SQLite WAL 模式 + 外键约束"""
            if "sqlite" in str(db_url):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

        logger.info("Engine 已创建: %s", repr(db_url)[:80])
        return _engine


def reset_engine_cache() -> None:
    """
    释放当前 engine 并清空缓存。

    用于长时间运行的服务（如定时任务）在数据刷新后重建连接池。
    """
    global _engine
    with _lock:
        if _engine is not None:
            _engine.dispose()
            _engine = None
            logger.info("Engine 已释放")
