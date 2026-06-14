"""
FastAPI Web 应用
===============

提供 A 股量化回测系统的 Web 可视化界面。
所有前端资源通过 CDN 引入，无需 Node.js 构建工具。
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from ..config import get_config

# 模板目录
TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


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

    # 静态文件
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # 注册路由
    from .routes import main, api
    app.include_router(main.router)
    app.include_router(api.router, prefix="/api")

    return app
