"""
Web 层认证与策略加载安全工具
============================

提供:
- verify_api_key: HTTPBearer 依赖,校验 Authorization: Bearer <key>
- safe_import_strategy: 白名单 importlib,只允许 src.strategies.* / src.models.*
- get_api_key: API key 来源(env QUANT_API_KEY > 临时生成)
"""
import importlib
import logging
import os
import secrets
from typing import Tuple, Type

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

# ===== API Key 管理 =====

_bearer_scheme = HTTPBearer(auto_error=True, description="API Key 认证")


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


def verify_api_key(
    creds: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> str:
    """FastAPI 依赖:校验 Bearer token
    通过比较 return creds.credentials; 失败抛 403
    """
    expected = get_api_key()
    # 用 compare_digest 防时序攻击
    if not secrets.compare_digest(creds.credentials, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid API key",
        )
    return creds.credentials


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
