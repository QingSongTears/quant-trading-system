"""
src.db.repository - 数据仓库 (兼容旧 import 路径)

实际实现位于 src.models.repository；
此模块为兼容旧代码 (from src.db.repository import DataRepository) 而保留。
"""
from __future__ import annotations
from src.models.repository import DataRepository

__all__ = ["DataRepository"]