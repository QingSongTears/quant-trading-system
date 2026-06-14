"""
FastAPI Web 应用
===============

提供 A 股量化回测系统的 Web 可视化界面。
所有前端资源通过 CDN 引入，无需 Node.js 构建工具。
"""
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ..config import get_config

# 模板目录
TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

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

    app = FastAPI(
        title=web_config.get("title", "QuantTrading - A股量化回测系统"),
        version=config.get("system", {}).get("version", "0.1.0"),
        docs_url=None,      # 生产环境关闭 API 文档
        redoc_url=None,
    )

    # 全局异常处理器
    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc):
        """自定义 404 页面"""
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        ctx = {
            "request": request,
            "app_name": web_config.get("title", "QuantTrading"),
            "ai_disclaimer": config.get("ai_disclaimer", {}),
            "cdn": web_config.get("cdn", {}),
            "error_code": 404,
            "error_message": "页面未找到",
        }
        return HTMLResponse(
            templates.get_template("error.html").render(ctx),
            status_code=404,
        )

    @app.exception_handler(500)
    async def server_error_handler(request: Request, exc):
        """自定义 500 页面"""
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        ctx = {
            "request": request,
            "app_name": web_config.get("title", "QuantTrading"),
            "ai_disclaimer": config.get("ai_disclaimer", {}),
            "cdn": web_config.get("cdn", {}),
            "error_code": 500,
            "error_message": "服务器内部错误",
        }
        return HTMLResponse(
            templates.get_template("error.html").render(ctx),
            status_code=500,
        )

    # 全局 JSON 异常处理
    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        """API 错误返回 JSON"""
        if request.url.path.startswith("/api"):
            return JSONResponse(
                {"success": False, "error": str(exc)},
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
    app.include_router(api.router, prefix="/api")

    return app
