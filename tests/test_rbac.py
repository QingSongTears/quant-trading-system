"""
RBAC 单测 (2026-06-25 Phase P4)

覆盖:
  - Role 枚举
  - get_role() 默认 + env var
  - has_permission() 3 档角色权限矩阵
  - require_permission FastAPI Depends 装饰器
  - 写 API 端点加 require_permission
"""
import os
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

from src.web.auth import (
    Role,
    ROLE_PERMISSIONS,
    get_api_key,
    get_role,
    has_permission,
    require_permission,
    verify_api_key,
)


# ============================================================
# Role + 权限矩阵
# ============================================================


def test_role_enum_values():
    """3 档角色"""
    assert Role.READONLY.value == "readonly"
    assert Role.CAN_RUN_BACKTEST.value == "can-run-backtest"
    assert Role.CAN_MANAGE_DATA.value == "can-manage-data"


def test_role_hierarchy():
    """高角色包含低角色权限"""
    readonly = ROLE_PERMISSIONS[Role.READONLY]
    runner = ROLE_PERMISSIONS[Role.CAN_RUN_BACKTEST]
    manager = ROLE_PERMISSIONS[Role.CAN_MANAGE_DATA]
    # runner 包含 readonly
    assert readonly.issubset(runner)
    # manager 包含 runner
    assert runner.issubset(manager)


def test_run_backtest_only_in_runner_plus():
    """run_backtest 不在 readonly, 在 runner 和 manager"""
    assert "run_backtest" not in ROLE_PERMISSIONS[Role.READONLY]
    assert "run_backtest" in ROLE_PERMISSIONS[Role.CAN_RUN_BACKTEST]
    assert "run_backtest" in ROLE_PERMISSIONS[Role.CAN_MANAGE_DATA]


def test_download_data_only_in_manager():
    """download_data 仅 manager"""
    assert "download_data" not in ROLE_PERMISSIONS[Role.READONLY]
    assert "download_data" not in ROLE_PERMISSIONS[Role.CAN_RUN_BACKTEST]
    assert "download_data" in ROLE_PERMISSIONS[Role.CAN_MANAGE_DATA]


# ============================================================
# get_role
# ============================================================


def test_get_role_default_admin(monkeypatch):
    """未设 env, 默认 admin (can-manage-data)"""
    monkeypatch.delenv("QUANT_USER_ROLE", raising=False)
    # 重置 cache
    import src.web.auth as auth_mod
    auth_mod._role_cache = None
    role = get_role()
    assert role == Role.CAN_MANAGE_DATA


def test_get_role_from_env(monkeypatch):
    """从 env 读 role"""
    monkeypatch.setenv("QUANT_USER_ROLE", "readonly")
    import src.web.auth as auth_mod
    auth_mod._role_cache = None
    role = get_role()
    assert role == Role.READONLY


def test_get_role_invalid_falls_back_admin(monkeypatch):
    """非法 role 值 → admin (warning)"""
    monkeypatch.setenv("QUANT_USER_ROLE", "superuser")
    import src.web.auth as auth_mod
    auth_mod._role_cache = None
    role = get_role()
    assert role == Role.CAN_MANAGE_DATA


def test_get_role_cached(monkeypatch):
    """role 进程级缓存"""
    monkeypatch.setenv("QUANT_USER_ROLE", "readonly")
    import src.web.auth as auth_mod
    auth_mod._role_cache = None
    assert get_role() == Role.READONLY
    # 改 env, 但 cache 还在
    monkeypatch.setenv("QUANT_USER_ROLE", "can-manage-data")
    assert get_role() == Role.READONLY  # 还是 readonly (cache)


# ============================================================
# has_permission
# ============================================================


def test_has_permission_admin_full():
    """admin 角色有 run_backtest + download_data"""
    assert has_permission("run_backtest", Role.CAN_MANAGE_DATA)
    assert has_permission("download_data", Role.CAN_MANAGE_DATA)
    assert has_permission("read_dashboard", Role.CAN_MANAGE_DATA)


def test_has_permission_runner_no_download():
    """runner 没有 download_data"""
    assert has_permission("run_backtest", Role.CAN_RUN_BACKTEST)
    assert not has_permission("download_data", Role.CAN_RUN_BACKTEST)


def test_has_permission_readonly_limited():
    """readonly 只有 read_*"""
    assert has_permission("read_dashboard", Role.READONLY)
    assert has_permission("read_stock", Role.READONLY)
    assert not has_permission("run_backtest", Role.READONLY)
    assert not has_permission("download_data", Role.READONLY)


# ============================================================
# require_permission FastAPI Depends
# ============================================================


@pytest.fixture
def test_app():
    """最小 FastAPI app 用于测试依赖"""
    app = FastAPI()

    @app.get("/test/run")
    def run_endpoint(api_key: str = Depends(require_permission("run_backtest"))):
        return {"ok": True, "perm": "run_backtest"}

    @app.get("/test/download")
    def download_endpoint(api_key: str = Depends(require_permission("download_data"))):
        return {"ok": True, "perm": "download_data"}

    return app


def test_require_permission_admin_allow_run(test_app, monkeypatch):
    """admin 允许 run_backtest"""
    monkeypatch.setenv("QUANT_USER_ROLE", "can-manage-data")
    import src.web.auth as auth_mod
    auth_mod._role_cache = None
    api_key = get_api_key()
    c = TestClient(test_app)
    r = c.get("/test/run", headers={"Authorization": f"Bearer {api_key}"})
    assert r.status_code == 200
    assert r.json()["perm"] == "run_backtest"


def test_require_permission_runner_allow_run(test_app, monkeypatch):
    """runner 允许 run_backtest"""
    monkeypatch.setenv("QUANT_USER_ROLE", "can-run-backtest")
    import src.web.auth as auth_mod
    auth_mod._role_cache = None
    api_key = get_api_key()
    c = TestClient(test_app)
    r = c.get("/test/run", headers={"Authorization": f"Bearer {api_key}"})
    assert r.status_code == 200


def test_require_permission_readonly_deny_run(test_app, monkeypatch):
    """readonly 拒绝 run_backtest (403)"""
    monkeypatch.setenv("QUANT_USER_ROLE", "readonly")
    import src.web.auth as auth_mod
    auth_mod._role_cache = None
    api_key = get_api_key()
    c = TestClient(test_app)
    r = c.get("/test/run", headers={"Authorization": f"Bearer {api_key}"})
    assert r.status_code == 403
    assert "readonly" in r.json()["detail"]
    assert "run_backtest" in r.json()["detail"]


def test_require_permission_runner_deny_download(test_app, monkeypatch):
    """runner 拒绝 download_data (只有 manager 允许)"""
    monkeypatch.setenv("QUANT_USER_ROLE", "can-run-backtest")
    import src.web.auth as auth_mod
    auth_mod._role_cache = None
    api_key = get_api_key()
    c = TestClient(test_app)
    r = c.get("/test/download", headers={"Authorization": f"Bearer {api_key}"})
    assert r.status_code == 403


def test_require_permission_no_token_deny(test_app):
    """无 token → 403 (HTTPBearer auto_error=True 返 403)"""
    c = TestClient(test_app)
    r = c.get("/test/run")
    # HTTPBearer 在没 header 时返 403 (FastAPI HTTP_403_FORBIDDEN)
    assert r.status_code in (401, 403)
