"""
FastAPI Web 应用
===============

提供 A 股量化回测系统的 Web 可视化界面。
所有前端资源通过 CDN 引入，无需 Node.js 构建工具。

安全加固 (2026-06-21)
---------------------
- 默认监听 127.0.0.1 (run.py)
- TrustedHostMiddleware 防 Host header 攻击
- /api/* 全部需要 Bearer token 认证
- openapi_url 关掉避免接口被枚举
- class_path 走白名单 (auth.safe_import_strategy)
"""
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ..config import get_config

logger = logging.getLogger(__name__)

# 模板目录
TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

# 共享 Jinja2 模板引擎（注册自定义过滤器）
_shared_templates = None


def get_templates() -> Jinja2Templates:
    """获取共享的 Jinja2 模板引擎实例（懒加载，注册自定义过滤器/全局函数）"""
    global _shared_templates
    if _shared_templates is None:
        _shared_templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        _shared_templates.env.filters["abs"] = abs
        _shared_templates.env.globals["abs"] = abs  # 同时支持函数调用 abs(...)
    return _shared_templates

# 全局下载状态管理器
download_status = {
    "running": False,
    "mode": None,
    "progress": 0,
    "total": 0,
    "current": "",
    "error": None,
    "result": None,
    "started_at": None,
}


def get_download_status() -> dict:
    """获取全局下载状态（线程安全需考虑，当前简化实现）"""
    return dict(download_status)


def reset_download_status():
    """重置下载状态"""
    download_status.update({
        "running": False, "mode": None, "progress": 0,
        "total": 0, "current": "", "error": None,
        "result": None, "started_at": None,
    })


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例"""
    config = get_config()
    web_config = config.get("web", {})

    # TrustedHost 白名单 — 默认仅本地访问
    # 生产部署时通过 QUANT_ALLOWED_HOSTS 环境变量覆盖(逗号分隔)
    # testserver: Starlette TestClient 默认 Host 头,本地测试用
    import os
    allowed_hosts = os.environ.get(
        "QUANT_ALLOWED_HOSTS",
        ",".join(web_config.get("allowed_hosts",
            ["localhost", "127.0.0.1", "0.0.0.0", "testserver"]))
    ).split(",")

    app = FastAPI(
        title=web_config.get("title", "QuantTrading - A股量化回测系统"),
        version=config.get("system", {}).get("version", "0.1.0"),
        docs_url=None,      # 生产环境关闭 API 文档
        redoc_url=None,
        openapi_url=None,   # 关闭 OpenAPI schema 枚举
    )

    # ===== 安全中间件 =====
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    # 全局异常处理器
    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc):
        """自定义 404: API 返回 JSON,页面返回 HTML 错误页"""
        if request.url.path.startswith("/api"):
            return JSONResponse(
                {"success": False, "error": "not found", "path": request.url.path},
                status_code=404,
            )
        tmpl = get_templates()
        ctx = {
            "request": request,
            "app_name": web_config.get("title", "QuantTrading"),
            "ai_disclaimer": config.get("ai_disclaimer", {}),
            "cdn": web_config.get("cdn", {}),
            "error_code": 404,
            "error_message": "页面未找到",
        }
        return HTMLResponse(
            tmpl.get_template("error.html").render(ctx),
            status_code=404,
        )

    @app.exception_handler(500)
    async def server_error_handler(request: Request, exc):
        """自定义 500 页面"""
        tmpl = get_templates()
        ctx = {
            "request": request,
            "app_name": web_config.get("title", "QuantTrading"),
            "ai_disclaimer": config.get("ai_disclaimer", {}),
            "cdn": web_config.get("cdn", {}),
            "error_code": 500,
            "error_message": "服务器内部错误",
        }
        return HTMLResponse(
            tmpl.get_template("error.html").render(ctx),
            status_code=500,
        )

    # 全局 JSON 异常处理
    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        """API 错误返回 JSON(不泄漏 traceback)"""
        if request.url.path.startswith("/api"):
            # 仅记录到服务端日志,不返回 detail
            logger.exception("API 错误: %s %s", request.method, request.url.path)
            return JSONResponse(
                {"success": False, "error": "internal server error"},
                status_code=500,
            )
        # 非 API 请求交给 Starlette 默认处理
        from starlette.responses import Response
        return Response(status_code=500)

    # 静态文件
    STATIC_DIR.mkdir(exist_ok=True)
    (STATIC_DIR / "css").mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # 注册路由
    from .routes import main, api
    app.include_router(main.router)
    # /api/* 全部需要 Bearer token
    # 依赖在路由模块内部用 Depends 显式标注,这里不再做 router 级依赖
    # (避免影响未来添加的 public 端点)
    app.include_router(api.router, prefix="/api")

    return app
