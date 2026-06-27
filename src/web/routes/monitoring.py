"""
monitoring Web 路由 (ADR-0012 #83 D2-B)

4 个 GET 端点 — Web UI 实时拉取监控数据:
  - GET /api/monitoring/pnl        最近 PnL 快照
  - GET /api/monitoring/positions  最近持仓快照
  - GET /api/monitoring/alerts     最近报警
  - GET /api/monitoring/health     健康检查 (返回 hub 状态)

设计:
  - 全部需要 Bearer token (与现有 /api/* 一致)
  - 返回结构: {success, data: [...]} (与现有 research API 风格一致)
  - Web QA #2 (2026-06-27) 修复: 统一 {success, data} 格式
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query

from ..auth import verify_api_key
from ...monitoring import MonitoringHub, get_hub

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/monitoring",
    tags=["monitoring"],
    dependencies=[Depends(verify_api_key)],
)


# ── JSON 序列化辅助 ──────────────────────────────────────
def _serialize_dt(obj: Any) -> Any:
    """datetime → ISO 格式 (JSON 序列化友好)"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def _to_jsonable(item: Any) -> Any:
    """dataclass / dict → JSON 可序列化 dict"""
    if hasattr(item, "__dict__"):
        return {
            k: _serialize_dt(v) for k, v in item.__dict__.items()
        }
    if isinstance(item, dict):
        return {k: _serialize_dt(v) for k, v in item.items()}
    return item


# ── 端点 ──────────────────────────────────────
@router.get("/pnl")
def get_pnl(n: int = Query(default=10, ge=1, le=1000)) -> dict:
    """最近 n 条 PnL 快照 (倒序, 最新在前)"""
    hub = get_hub()
    data = hub.recent_pnl(n)
    return {
        "success": True,
        "data": [_to_jsonable(item) for item in data],
    }


@router.get("/positions")
def get_positions(n: int = Query(default=50, ge=1, le=1000)) -> dict:
    """最近 n 条持仓快照"""
    hub = get_hub()
    data = hub.recent_positions(n)
    return {
        "success": True,
        "data": [_to_jsonable(item) for item in data],
    }


@router.get("/alerts")
def get_alerts(n: int = Query(default=50, ge=1, le=1000)) -> dict:
    """最近 n 条报警 (AlertDispatcher.recent)"""
    hub = get_hub()
    data = hub.recent_alerts(n)
    return {
        "success": True,
        "data": data,  # dict 列表, 已经是 JSON 友好
    }


@router.get("/risk-alerts")
def get_risk_alerts(n: int = Query(default=50, ge=1, le=1000)) -> dict:
    """最近 n 条风控告警 (EVENT_RISK_ALERT 镜像)"""
    hub = get_hub()
    data = hub.recent_risk_alerts(n)
    return {
        "success": True,
        "data": data,
    }


@router.get("/health")
def get_health() -> dict:
    """监控 hub 健康检查"""
    hub = get_hub()
    return {
        "success": True,
        "data": hub.health(),
    }


__all__ = ["router"]