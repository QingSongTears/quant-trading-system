"""
Web 层认证与策略加载安全工具
============================

提供:
- check_bearer_token: 框架无关的 Bearer token 校验
- verify_api_key: FastAPI Depends,使用 check_bearer_token
- require_api_key: Flask before_request / view decorator
- safe_import_strategy: 白名单 importlib,只允许 src.strategies.* / src.models.*
- get_api_key: API key 来源(env QUANT_API_KEY > 临时生成)
"""
import importlib
import logging
import os
import secrets
from typing import Optional, Tuple, Type

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

# ===== API Key 管理 =====


def _generate_key() -> str:
    """生成临时 API key(进程级,重启会变)"""
    return secrets.token_urlsafe(32)


# 进程级缓存:保证 verify_api_key 和 get_api_key 看到同一个值
_api_key_cache: str | None = None


def get_api_key() -> str:
    """获取 API key,优先级:
    1. 环境变量 QUANT_API_KEY(推荐,生产)
    2. 临时生成(开发用,启动时打印一次)

    进程内稳定:首次调用确定后,后续调用返回相同值
    """
    global _api_key_cache
    if _api_key_cache is not None:
        return _api_key_cache

    key = os.environ.get("QUANT_API_KEY", "").strip()
    if key:
        _api_key_cache = key
        return _api_key_cache

    key = _generate_key()
    logger.warning(
        "QUANT_API_KEY 环境变量未设置,使用临时 key (重启进程会变): %s", key
    )
    _api_key_cache = key
    return _api_key_cache


def check_bearer_token(authorization_header: Optional[str]) -> bool:
    """框架无关:校验 Authorization header 的 Bearer token

    Args:
        authorization_header: 完整 header 值,形如 "Bearer xxx" 或 None

    Returns:
        True = 通过, False = 拒绝
    """
    if not authorization_header:
        return False
    parts = authorization_header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False
    token = parts[1].strip()
    if not token:
        return False
    expected = get_api_key()
    return secrets.compare_digest(token, expected)


# ===== FastAPI 适配 =====

_bearer_scheme = HTTPBearer(auto_error=True, description="API Key 认证")


def verify_api_key(
    creds: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> str:
    """FastAPI 依赖:校验 Bearer token
    通过比较 return creds.credentials; 失败抛 403
    """
    if not check_bearer_token(f"Bearer {creds.credentials}"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid API key",
        )
    return creds.credentials


# ===== Flask 适配 =====


def require_api_key(protected_prefix: str = "/api/"):
    """Flask before_request 工厂:对特定前缀的路由要求 Bearer token

    用法:
        app.before_request(require_api_key("/api/"))

    Args:
        protected_prefix: 需要保护的路径前缀,默认 "/api/"
    """
    def _check():
        from flask import request, jsonify
        # 跳过非保护路径
        if not request.path.startswith(protected_prefix):
            return None
        # 跳过 CORS 预检
        if request.method == "OPTIONS":
            return None
        if not check_bearer_token(request.headers.get("Authorization")):
            logger.warning(
                "拒绝访问 %s %s — 来自 %s",
                request.method, request.path, request.remote_addr,
            )
            resp = jsonify({"success": False, "error": "invalid or missing API key"})
            resp.status_code = 401
            return resp
        return None
    return _check


# ===== 策略类安全 import =====

# 策略类只能来自这些模块前缀
ALLOWED_STRATEGY_PREFIXES = (
    "src.strategies.",
    "src.models.",
)


def safe_import_strategy(class_path: str) -> Type:
    """白名单导入策略类

    防止通过 class_path 字段触发任意模块加载 → 任意代码执行。

    Args:
        class_path: 形如 "src.strategies.ma_cross.MaCrossStrategy"

    Returns:
        策略类(必须是 class,不能是函数/模块/变量)

    Raises:
        ValueError: 不在白名单、格式错误、加载到非 class
    """
    if not isinstance(class_path, str) or not class_path:
        raise ValueError(f"class_path 必须是字符串,得到: {type(class_path).__name__}")

    if not any(class_path.startswith(p) for p in ALLOWED_STRATEGY_PREFIXES):
        raise ValueError(
            f"class_path {class_path!r} 不在白名单,仅允许: {ALLOWED_STRATEGY_PREFIXES}"
        )

    module_path, sep, class_name = class_path.rpartition(".")
    if not sep or not module_path or not class_name:
        raise ValueError(f"class_path 格式错误: {class_path!r} (需为 'module.path.ClassName')")

    # 类名必须是合法 Python 标识符
    if not class_name.isidentifier():
        raise ValueError(f"类名不合法: {class_name!r}")

    # 路径中也不能含奇怪的字符
    for part in module_path.split("."):
        if not part.isidentifier():
            raise ValueError(f"模块路径段不合法: {part!r}")

    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        raise ValueError(f"无法 import 模块 {module_path!r}: {e}") from e

    try:
        cls = getattr(module, class_name)
    except AttributeError as e:
        raise ValueError(f"模块 {module_path!r} 中不存在 {class_name!r}") from e

    if not isinstance(cls, type):
        raise ValueError(f"{class_path!r} 不是类,得到 {type(cls).__name__}")

    return cls

