"""
独立页面路由 (Phase D — Ardot 设计稿)
注册 diagnose/sector/screener/portfolio 等 22 个 standalone 页面 (暗色主题, 不继承 base.html)
+ 8 个 301 重定向 (Phase C3 合并后兼容旧链接)

注: 共享 core_pages.py 的 router 和 templates, 注册到同一个 FastAPI app
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.responses import RedirectResponse
from .core_pages import _get_global_context, get_templates

router = APIRouter()
templates = get_templates()


_STANDALONE_PAGES = {
    "/diagnose": "diagnose.html",
    "/sector": "sector.html",
    "/screener": "screener.html",
    "/portfolio": "portfolio.html",
    # 2026-06-25 (Phase C3a): 合并到 /workbench?mode=lab / mode=view
    # "/backtest-lab": "backtest-lab.html",
    # "/backtest-view": "backtest-view.html",
    # "/strategy-compare": "strategy-compare.html",  # 2026-06-25 合并到 /compare?type=strategy
    "/bull-report": "bull-report.html",
    "/signal": "signal.html",
    "/verify": "verify.html",
    "/predict": "predict.html",
    # "/tuning": "tuning.html",  # 2026-06-26: 删 dead route, 改 /research?type=tuning (旧链接自动 redirect 兼容)
    "/data-monitor": "data-monitor.html",
    # "/v5": "v5.html",                          # 2026-06-25 合并到 /research?type=v5
    # "/v6-compare": "v6-compare.html",          # 2026-06-25 合并到 /research?type=v6
    # "/tuning": "tuning.html",                  # 2026-06-25 合并到 /research?type=tuning
    # "/dim-compare": "dim-compare.html",        # 2026-06-25 合并到 /research?type=dim
    # "/ic": "ic.html",                          # 2026-06-25 合并到 /research?type=ic
    "/walk-forward": "walk_forward.html",
    # 2026-06-25 (Phase D — Ardot 新设计稿)
    "/backtest-lab": "backtest_lab.html",       # 原 workbench?mode=lab 优先
    "/signal-dashboard": "signal_dashboard.html",  # 龙头模型 v2 综合信号
    "/strategy-compare": "strategy_compare.html",  # 7大策略全景
    "/tuning-panel": "tuning_panel.html",       # 七维量化评分参数调优
    "/v5-tuning": "v5_tuning.html",             # v5 策略调优 (旧版模板保留兼容入口, ROADMAP 已归档)
    "/v6-compare": "v6_compare.html",           # v6阈值对比
    "/multi-objective": "multi_objective.html", # Ardot spec 03 多目标优化 (2026-06-26)
    "/ic-analysis": "ic_analysis.html",         # IC分析
    "/dim-compare": "dim_compare.html",         # 维度贡献
    # 2026-06-26: 补齐最后 3 个 Ardot 设计稿
    "/predict-verify": "predict_verify.html",   # 预测验证
    "/predict-dashboard": "predict_dashboard.html",  # 预测面板
    "/bull-backtest-report": "bull_backtest_report.html",  # 多头回测报告
}


def _register_standalone_routes():
    """批量注册独立页面路由 (避免闭包变量捕获问题)"""
    if getattr(_register_standalone_routes, "_done", False):
        return
    _register_standalone_routes._done = True
    for path, tmpl in _STANDALONE_PAGES.items():
        async def _handler(request: Request, _t=tmpl):
            return templates.TemplateResponse(request, _t, _get_global_context())
        router.add_api_route(path, _handler, response_class=HTMLResponse, methods=["GET"])


# 2026-06-26: /tuning 旧路由重定向到 /research?type=tuning
# 原 /tuning 模板已删除(2026-06-25 Phase C3 合并到 research),
# 但 dashboard / TopBar 旧链接仍指向 /tuning → 301 redirect 保持兼容
@router.get("/tuning", response_class=HTMLResponse)
async def tuning_redirect():
    """旧 /tuning 路由 → /research?type=tuning (兼容旧链接)"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/research?type=tuning", status_code=301)


# 2026-06-27: Phase C3 redirect 缺失补齐
# 旧路径仍登记在 _STANDALONE_PAGES 直接渲染, 但 Phase C3 期望 301
# 改法: 让独立页继续工作, 但额外提供 301 alias → 统一入口
_REDIRECT_ALIASES = [
    ("/backtest-lab", "/workbench?mode=lab"),
    ("/backtest-view", "/workbench?mode=view"),
    ("/strategy-compare", "/compare?type=strategy"),
    ("/v5", "/research?type=v5"),
    ("/v6", "/research?type=v6"),
    ("/v7", "/research?type=v7"),
    ("/v6-compare", "/research?type=v6"),
    ("/ic", "/research?type=ic"),
    ("/dim-compare", "/research?type=dim"),
]


def _register_redirect_aliases():
    """为 Phase C3 合并的旧路径提供 301 重定向 (兼容旧链接 + e2e 测试)

    实现策略: 把这些路径从 _STANDALONE_PAGES 移除, 改为 301 重定向;
    旧路径仍然可访问, 只是不再直接渲染内容, 而是跳转到新的统一入口.
    """
    from fastapi.responses import RedirectResponse

    # 幂等性: 多次调用不会重复注册 (FastAPI 不支持重名路由)
    if getattr(_register_redirect_aliases, "_done", False):
        return
    _register_redirect_aliases._done = True

    # 从 _STANDALONE_PAGES 移除这些路径, 避免路由冲突
    for old_path, _ in _REDIRECT_ALIASES:
        _STANDALONE_PAGES.pop(old_path, None)

    for old_path, new_path in _REDIRECT_ALIASES:
        async def _alias(_np=new_path):
            return RedirectResponse(url=_np, status_code=301)
        router.add_api_route(
            old_path, _alias, response_class=HTMLResponse, methods=["GET"],
            include_in_schema=False,
        )



# 2026-06-27: Phase C3 redirect 必须比 standalone 优先注册, 否则 FastAPI 按顺序匹配
# 策略: 先去掉 _STANDALONE_PAGES 里的旧路径, 再注册 301, 最后再注册剩余的 standalone
_register_redirect_aliases()  # 这会从 _STANDALONE_PAGES 移除旧路径并注册 301
_register_standalone_routes()  # 注册剩余的 standalone 页面

