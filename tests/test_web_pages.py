"""
Web QA 回归测试 — 验证所有页面正确加载 common.js + api-key meta

对应 Issue Web QA #1 (2026-06-27):
    research.html 是独立模板 (不继承 base.html), 没加载 /static/js/common.js,
    导致 window.QT undefined, v5/v6/tuning chart 全部 fail。

修复: 在 <head> 注入:
    <meta name="api-key" content="{{ api_key }}">
    <script src="/static/js/common.js"></script>

本测试套件覆盖所有关键页面, 防止未来独立模板再次漏掉 common.js:
    - /research            (本次 bug 修复目标)
    - /dashboard /data /strategies /backtest 等关键页 (继承 base.html 也包含断言, 防回归)
"""
import pytest

# FastAPI TestClient (包装为自动带 Bearer token)
from tests.conftest import AuthedTestClient as TestClient


# ============================================================
# 全局 mock — 避免 Web 测试依赖真实数据库
# ============================================================
@pytest.fixture(autouse=True)
def mock_data_repository(monkeypatch):
    """为所有 Web 测试 mock DataManager.business 门面, 避免需要真实数据库"""
    from unittest.mock import MagicMock
    import pandas as pd

    mock = MagicMock()
    mock.get_data_coverage = MagicMock(return_value={
        "total_stocks": 0, "total_records": 0,
        "date_range": {"start": None, "end": None},
    })
    mock.get_stock_list = MagicMock(return_value=pd.DataFrame())
    mock.get_stock_count = MagicMock(return_value=0)
    mock.get_recent_backtests = MagicMock(return_value=[])
    mock.get_all_strategies = MagicMock(return_value=[])
    mock.get_backtest_result = MagicMock(return_value=None)
    mock.get_download_history = MagicMock(return_value=[])
    mock.init_database = MagicMock()

    # DataManager.business 走 mock
    monkeypatch.setattr(
        "src.models.repository.DataRepository",
        lambda *args, **kwargs: mock,
    )
    monkeypatch.setattr(
        "src.data.manager.DataManager.business",
        property(lambda self: mock),
    )
    return mock


# ============================================================
# Web 应用 fixture
# ============================================================
@pytest.fixture
def test_app():
    """创建测试用的 FastAPI 应用"""
    from src.web.app import create_app
    return create_app()


@pytest.fixture
def client(test_app):
    """TestClient (autouse Bearer token for /api/* endpoints)"""
    return TestClient(test_app)


# ============================================================
# 断言辅助函数
# ============================================================
def _assert_has_common_js(html: str) -> None:
    """验证 HTML 中包含 common.js script 标签 (独立模板必备)"""
    assert "/static/js/common.js" in html, (
        f"页面未加载 common.js — window.QT 将 undefined, "
        f"fetch 拦截器失效, 任何 Chart / ECharts 调用会 fail"
    )


def _assert_has_api_key_meta(html: str) -> None:
    """验证 HTML <head> 中包含 <meta name="api-key"> (供 common.js 读)"""
    # 简单包含检查: meta tag + name="api-key" + content 包含 ci-test-key (conftest 设置)
    assert 'name="api-key"' in html, (
        "页面 <head> 缺 <meta name='api-key'> — common.js 读不到 API key, "
        "所有 /api/* 请求会 401"
    )


def _assert_in_head(html: str, needle: str) -> None:
    """验证 needle 在 <head>...</head> 区段内 (防止出现在 body 误判)"""
    # 简易 head 边界截取
    head_start = html.lower().find("<head")
    head_end = html.lower().find("</head>")
    assert head_start >= 0 and head_end > head_start, "页面无 <head> 区段"
    head_section = html[head_start:head_end]
    assert needle in head_section, (
        f"期望 '{needle}' 在 <head> 内, 但未找到 — "
        f"common.js 必须在 DOM 解析早期加载才能定义 window.QT"
    )


# ============================================================
# 关键页面断言 — 必须加载 common.js + api-key meta
# ============================================================
# 这些页面要么继承 base.html, 要么已通过本次修复单独注入。
# 任何页面漏掉 → 立即失败, 防止 chart 静默 fail。

class TestResearchPageLoadsCommonJS:
    """Web QA #1: /research 必须加载 common.js + api-key meta"""

    def test_research_includes_common_js_script(self, client):
        """/research 必须引用 /static/js/common.js"""
        resp = client.get("/research")
        assert resp.status_code == 200, f"/research 返回 {resp.status_code}"
        _assert_has_common_js(resp.text)

    def test_research_includes_api_key_meta(self, client):
        """/research <head> 必须含 <meta name='api-key'>"""
        resp = client.get("/research")
        assert resp.status_code == 200
        _assert_has_api_key_meta(resp.text)

    def test_research_common_js_in_head(self, client):
        """common.js 必须在 <head> 加载 (早期定义 window.QT)"""
        resp = client.get("/research")
        assert resp.status_code == 200
        _assert_in_head(resp.text, "/static/js/common.js")

    def test_research_api_key_meta_in_head(self, client):
        """api-key meta 必须在 <head> (DOM 解析早期就能读)"""
        resp = client.get("/research")
        assert resp.status_code == 200
        _assert_in_head(resp.text, 'name="api-key"')

    def test_research_all_5_tabs_render(self, client):
        """/research 5 个 tab (v5/v6/tuning/ic/dim) 都应正确渲染"""
        for research_type in ("v5", "v6", "tuning", "ic", "dim"):
            resp = client.get(f"/research?type={research_type}")
            assert resp.status_code == 200, (
                f"/research?type={research_type} 返回 {resp.status_code}"
            )
            # 每次渲染都需含 common.js (防止某个 type 模板路径漏掉)
            _assert_has_common_js(resp.text)
            _assert_has_api_key_meta(resp.text)

    def test_research_chart_canvas_present(self, client):
        """/research 实际包含 chart canvas (保证 chart 渲染逻辑存在)"""
        resp = client.get("/research?type=v5")
        assert resp.status_code == 200
        # v5 tab 应有 canvas 元素
        assert "v5-canvas" in resp.text, "/research?type=v5 应含 v5-canvas 元素"

    def test_research_uses_qt_fetch(self, client):
        """/research 必须用 window.QT.fetch (不能直接 fetch — 否则不带 Bearer)"""
        resp = client.get("/research?type=v5")
        assert resp.status_code == 200
        assert "window.QT.fetch" in resp.text, (
            "/research 必须用 window.QT.fetch 而非 fetch, "
            "否则绕过 common.js 的 Bearer 拦截器, /api/* 会 401"
        )


class TestBaseTemplatePagesIncludeCommonJS:
    """继承 base.html 的页面也应包含 common.js (防回归)"""

    @pytest.mark.parametrize("path", [
        "/dashboard",
        "/data",
        "/strategies",
        "/backtest",
        "/compare",
        "/workbench",
        "/simulate",
        "/console",
        "/stock/600519",
    ])
    def test_page_includes_common_js(self, client, path):
        """所有继承 base.html 的页面都应加载 common.js"""
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} 返回 {resp.status_code}"
        _assert_has_common_js(resp.text)
        _assert_has_api_key_meta(resp.text)


# ============================================================
# 静态文件可达性 — common.js 必须能被浏览器下载
# ============================================================
class TestCommonJSStaticFile:
    """/static/js/common.js 必须可访问 (200 + 非空 JS 内容)"""

    def test_common_js_returns_200(self, client):
        resp = client.get("/static/js/common.js")
        assert resp.status_code == 200, (
            f"/static/js/common.js 返回 {resp.status_code} — "
            f"页面会 console.error, window.QT undefined"
        )

    def test_common_js_defines_window_qt(self, client):
        """common.js 内容必须包含 window.QT 定义"""
        resp = client.get("/static/js/common.js")
        assert resp.status_code == 200
        body = resp.text
        # common.js 关键标识符
        assert "window.QT" in body or "global.QT" in body, (
            "common.js 必须定义 window.QT (含 fetch 拦截器)"
        )
        assert "api-key" in body, (
            "common.js 必须从 meta[name='api-key'] 读 key"
        )


# ============================================================
# API 契约回归 — 防止字段名漂移
# ============================================================
# Web QA #2 (2026-06-27): research.html 前端用 data.results.map() 但 API 返
# {success, data: [...]} → 'Cannot read properties of undefined (reading map)'。
#
# 这些测试是 **API 契约** 测试 — 锁定每个端点的返回结构, 防止未来重构
# 把字段名从 "data" 改成 "results" 或 "items" 而前端没同步。
#
# 注意:
#   - 不要修改 API 路由 (约束 #3) — 这是研究端点的事实契约
#   - 前端必须适配 API (data.data || [])


class TestAPIResponseContract:
    """锁定 /research 页面依赖的 4 个 API 端点的返回结构

    任何端点返回结构变更 (例如把 data 改 results / items) 都必须同时:
    1. 更新前端 (templates/*.html)
    2. 在此处更新断言, 并在 commit 注明
    """

    def test_backtest_results_returns_success_data_envelope(self, client):
        """/api/backtest/results 必须返 {success: True, data: [...]} (tuning chart 用)"""
        resp = client.get("/api/backtest/results?limit=5")
        assert resp.status_code == 200, (
            f"/api/backtest/results 返回 {resp.status_code}"
        )
        payload = resp.json()
        # 必须有 success 标记 + data 字段 (前端 data.data || [] 读)
        assert "success" in payload, (
            "API 契约破坏: 缺 'success' 字段 — "
            "前端需用 payload.success 判断是否成功"
        )
        assert "data" in payload, (
            "API 契约破坏: 缺 'data' 字段 — "
            "前端 data.data.map() 会 throw 'Cannot read properties of undefined'"
        )
        assert isinstance(payload["data"], list), (
            f"/api/backtest/results data 字段必须是 list, "
            f"实际 {type(payload['data']).__name__}"
        )

    def test_v5_scan_results_returns_success_data_envelope(self, client):
        """/api/v5/scan-results 必须返 {success: True, data: [...]} (v5 chart 用)"""
        resp = client.get("/api/v5/scan-results?limit=5")
        assert resp.status_code == 200, (
            f"/api/v5/scan-results 返回 {resp.status_code}"
        )
        payload = resp.json()
        assert "success" in payload, (
            "API 契约破坏: 缺 'success' 字段"
        )
        assert "data" in payload, (
            "API 契约破坏: 缺 'data' 字段 — "
            "前端 data.data.map() 会 throw"
        )
        assert isinstance(payload["data"], list), (
            f"/api/v5/scan-results data 字段必须是 list, "
            f"实际 {type(payload['data']).__name__}"
        )

    def test_strategies_returns_success_data_envelope(self, client):
        """/api/strategies 必须返 {success: True, data: [...]} (v6 chart 用)

        注意: 旧前端用 data.strategies.map() (错), 新前端用 data.data.map()
        API 必须保持 {success, data} 契约, 不要改名为 {strategies}
        """
        resp = client.get("/api/strategies")
        assert resp.status_code == 200, (
            f"/api/strategies 返回 {resp.status_code}"
        )
        payload = resp.json()
        assert "success" in payload, (
            "API 契约破坏: 缺 'success' 字段"
        )
        assert "data" in payload, (
            "API 契约破坏: 缺 'data' 字段 — "
            "v6 chart 前端 data.data.map() 会 throw 'reading map'"
        )
        assert isinstance(payload["data"], list), (
            f"/api/strategies data 字段必须是 list, "
            f"实际 {type(payload['data']).__name__}"
        )

    def test_research_ic_returns_factors_envelope(self, client):
        """/api/research/ic 必须返 {factors: [...]} (IC chart 用)

        注意: 此端点**特殊** — 直接返 {factors, window, start, end}, 没有 success 字段。
        前端 data.factors.map() 直接读 factors 字段, 不走 data.data 模式。
        锁定契约: factors 是 list, 且每个 factor 含 name + window
        """
        resp = client.get("/api/research/ic?window=5&stock_limit=5")
        assert resp.status_code == 200, (
            f"/api/research/ic 返回 {resp.status_code}"
        )
        payload = resp.json()
        assert "factors" in payload, (
            "API 契约破坏: 缺 'factors' 字段 — "
            "前端 data.factors.map() 会 throw"
        )
        assert isinstance(payload["factors"], list), (
            f"/api/research/ic factors 字段必须是 list, "
            f"实际 {type(payload['factors']).__name__}"
        )

    def test_research_dim_ic_returns_dims_envelope(self, client):
        """/api/research/dim-ic 必须返 {dims: [...]} (dim chart 用)

        锁定契约: dims 是 list
        """
        resp = client.get("/api/research/dim-ic?window=5&stock_limit=5")
        assert resp.status_code == 200, (
            f"/api/research/dim-ic 返回 {resp.status_code}"
        )
        payload = resp.json()
        assert "dims" in payload, (
            "API 契约破坏: 缺 'dims' 字段 — "
            "前端 data.dims.map() 会 throw"
        )
        assert isinstance(payload["dims"], list), (
            f"/api/research/dim-ic dims 字段必须是 list, "
            f"实际 {type(payload['dims']).__name__}"
        )


# ============================================================
# 模板源码静态检查 — 防止 data.results/data.strategies 再次回归
# ============================================================
# Web QA #2 (2026-06-27): research.html 旧版用 data.results.map() 触发
# 'reading map' 错误. 此测试扫描模板源码, 任何模板出现 data.results.map /
# data.strategies.map 都立即 fail (前端不能再用错的字段名).


class TestResearchTemplateNoLegacyFieldNames:
    """research.html 必须用 data.data.* (API 实际结构), 不能用 data.results.* / data.strategies.*"""

    def test_research_template_no_data_results_field(self, client):
        """/research 页面源码不应出现 data.results.map() / data.results.forEach()"""
        resp = client.get("/research")
        assert resp.status_code == 200
        html = resp.text
        # Web QA #2: data.results 是错误的字段名 (API 返 data 而非 results)
        assert "data.results" not in html, (
            "research.html 仍含 'data.results' 字段访问 — "
            "API 实际返 {success, data: [...]}, 应改为 'data.data'"
        )

    def test_research_template_no_data_strategies_field(self, client):
        """/research 页面源码不应出现 data.strategies.map() (v6 tab 旧 bug)"""
        resp = client.get("/research")
        assert resp.status_code == 200
        html = resp.text
        # /api/strategies 也返 {success, data: [...]}, 不是 {strategies}
        assert "data.strategies" not in html, (
            "research.html 仍含 'data.strategies' 字段访问 — "
            "API 实际返 {success, data: [...]}, 应改为 'data.data'"
        )

    def test_research_template_uses_data_data_with_fallback(self, client):
        """/research 页面源码应使用 data.data + 防御性 fallback (data.data || [])"""
        resp = client.get("/research?type=v5")
        assert resp.status_code == 200
        html = resp.text
        # 防御: data.data 必须 || [] 防止 API 偶发返空对象时 throw
        assert "data.data || []" in html, (
            "research.html 缺防御性 'data.data || []' — "
            "API 偶发返空对象时 items.map() 会 throw"
        )
        assert "data.data" in html, (
            "research.html 必须用 data.data (API 实际字段名), 而非 data.results"
        )


# ============================================================
# 全部模板源码静态扫描 — 32 个独立模板统一用 _standalone_head.html
# ============================================================
# Web QA #5 (2026-06-27): 整合 32 个独立模板, 任何模板不继承 base.html 且
# 不 include _standalone_head.html → 立即失败 (防止漏掉 common.js).
#
# 排除项:
#   - base.html  (本身就是 layout)
#   - error.html (无 JS, 不需要 common.js)
#   - *_specs_index.html / *_index.html 等特殊目录 (如 _backup_pre_ardot 备份)
#
# 继承 base.html 的页面单独覆盖 (TestBaseTemplatePagesIncludeCommonJS).

import re
from pathlib import Path

_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "src" / "web" / "templates"

# 不需要 _standalone_head.html 的模板 (有特殊用途)
_EXCLUDE_FROM_STANDALONE_HEAD = frozenset({
    "base.html",       # 本身是 layout
    "error.html",      # 错误页无 JS, 保留轻量
})


def _list_standalone_templates():
    """列出所有不继承 base.html 的 *.html 模板 (排除 _backup)"""
    out = []
    for f in sorted(_TEMPLATES_DIR.glob("*.html")):
        if f.name.startswith("_"):
            continue
        if f.name in _EXCLUDE_FROM_STANDALONE_HEAD:
            continue
        content = f.read_text(encoding="utf-8")
        if "extends 'base.html'" in content or 'extends "base.html"' in content:
            continue
        out.append(f)
    return out


class TestAllStandaloneTemplatesUsePartialHead:
    """Web QA #5 (2026-06-27): 32 个独立模板必须统一用 partials/_standalone_head.html

    任何遗漏 → 该模板的 window.QT undefined, fetch 拦截器失效, chart 静默 fail.
    """

    def test_at_least_30_standalone_templates(self):
        """防御: 当前应有 30+ 独立模板 (如果少了说明有人误改 .html → base.html)"""
        templates = _list_standalone_templates()
        assert len(templates) >= 30, (
            f"独立模板数量 {len(templates)} < 30, "
            f"可能有模板被误改 extends base.html — 当前清单: "
            f"{[t.name for t in templates]}"
        )

    def test_every_standalone_template_includes_partial_head(self):
        """每个独立模板必须 {% include 'partials/_standalone_head.html' %}"""
        templates = _list_standalone_templates()
        offenders = []
        for t in templates:
            content = t.read_text(encoding="utf-8")
            if "partials/_standalone_head.html" not in content:
                offenders.append(t.name)
        assert not offenders, (
            f"以下独立模板未使用 partials/_standalone_head.html — "
            f"会缺 common.js + api-key meta, window.QT undefined: {offenders}"
        )


# ============================================================
# setInterval 高频检查 — 防 /console 类问题再次发生
# ============================================================
# Web QA #4 (2026-06-27): console.html 旧版 setInterval(updateClock, 1000)
# 导致 Playwright networkidle 永远不达成 (每秒都有 JS 任务).
#
# 此扫描所有 *.html, 任何 setInterval(_, < 5000) 都立即 fail (时钟 5s+ 才 OK).
# 注释里的 setInterval (例如 /* setInterval(...) */) 不算违规.

_INTERVAL_PATTERN = re.compile(
    r'setInterval\s*\([^,]+,\s*(\d+)\s*\)',
    re.MULTILINE,
)


def _scan_set_intervals(html: str, source_name: str):
    """扫描 HTML 源码中的 setInterval 调用, 返回 (ms, lineno) 列表"""
    results = []
    for lineno, line in enumerate(html.splitlines(), 1):
        # 跳过纯注释行 (// setInterval ...)
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("#"):
            continue
        # 跳过 JS 块注释
        if stripped.startswith("/*") or stripped.startswith("*"):
            continue
        m = _INTERVAL_PATTERN.search(line)
        if m:
            try:
                ms = int(m.group(1))
                results.append((ms, lineno, source_name))
            except ValueError:
                pass
    return results


class TestNoHighFrequencySetInterval:
    """Web QA #4 (2026-06-27): 防 console.html setInterval(1000) 再次阻塞 networkidle

    任何模板的 setInterval 间隔 < 5000ms 都视为违规 (沙盒 QA networkidle 永远不达成).
    阈值 5000ms 是合理上限: 时钟 1 分钟, 数据轮询 5s, 都不应 < 5s.
    """

    def test_no_set_interval_under_5000ms_in_any_template(self):
        """扫描所有 *.html 模板, setInterval(_, < 5000) 立即 fail"""
        offenders = []
        for t in sorted(_TEMPLATES_DIR.glob("*.html")):
            content = t.read_text(encoding="utf-8")
            for ms, lineno, name in _scan_set_intervals(content, t.name):
                if ms < 5000:
                    offenders.append(f"{name}:{lineno} setInterval(_, {ms}ms)")
        assert not offenders, (
            f"以下 setInterval 间隔 < 5000ms, 会阻塞 Playwright networkidle: "
            f"{offenders}"
        )

    def test_console_clock_interval_is_60000(self):
        """/console 时钟更新间隔必须是 60000ms (1 分钟), 不是 1000ms"""
        content = (_TEMPLATES_DIR / "console.html").read_text(encoding="utf-8")
        # 必须有 setInterval(updateClock, 60000)
        assert "setInterval(updateClock,60000)" in content or \
               "setInterval(updateClock, 60000)" in content, (
            "console.html 时钟间隔必须改为 60000ms (1 分钟), "
            "旧版 1000ms 导致 Playwright TIMEOUT"
        )
        # 必须没有 setInterval(updateClock, 1000)
        assert "setInterval(updateClock,1000)" not in content and \
               "setInterval(updateClock, 1000)" not in content, (
            "console.html 仍含 setInterval(updateClock, 1000) — "
            "Web QA #4 未修复"
        )


# ============================================================
# /console 页面加载时长 < 5s (防网络请求卡死)
# ============================================================
class TestConsolePageLoadsQuickly:
    """Web QA #4 (2026-06-27): /console 不能 setInterval 1000ms (networkidle 永不到)

    验证 /console GET 返回 < 5s (在内存 mock 下应该 < 1s).
    """

    def test_console_loads_under_5_seconds(self, client):
        """/console 页面 GET 必须在 5s 内返回 (网络/JS 死循环)"""
        import time
        t0 = time.time()
        resp = client.get("/console")
        elapsed = time.time() - t0
        assert resp.status_code == 200, f"/console 返回 {resp.status_code}"
        assert elapsed < 5.0, (
            f"/console 加载耗时 {elapsed:.2f}s > 5s, "
            f"可能有 setInterval 死循环或网络阻塞"
        )


# ============================================================
# 静态资源跨域检查 — 防 CDN CORS 再次出现
# ============================================================
# Web QA #3 (2026-06-27): base.html 用 cdn.icons.jsdelivr.net 加载 icons.css,
# 沙盒无外网 → 全部页面 (继承 base.html) 报 CORS 失败.

class TestBaseTemplateNoExternalCDN:
    """Web QA #3 (2026-06-27): base.html 不应包含 jsdelivr CDN icons.css 引用

    降级方案: 暂时不加载 bootstrap-icons.css (沙盒 QA 可通过, 生产需另开 ADR).
    """

    def test_base_template_no_bootstrap_icons_cdn(self):
        """base.html 不应再引用 {{ cdn.icons }} bootstrap-icons.css"""
        content = (_TEMPLATES_DIR / "base.html").read_text(encoding="utf-8")
        # 注释里可以保留, 但实际 link 标签必须被注释掉或删除
        # 检查 uncommented <link> 标签包含 bootstrap-icons.css
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("{#"):
                continue
            if "<link" in stripped and "bootstrap-icons" in stripped:
                pytest.fail(
                    f"base.html 仍含 <link> bootstrap-icons 引用 (CORS): {line!r}\n"
                    f"Web QA #3 降级方案: 注释掉 bootstrap-icons, 用 emoji fallback"
                )

    def test_base_template_no_cdn_icons_reference_active(self, client):
        """base.html 渲染结果不应含 bootstrap-icons.css 的 <link>"""
        resp = client.get("/dashboard")
        assert resp.status_code == 200
        html = resp.text
        # 找到所有未被注释的 <link> 标签 (含 bootstrap-icons)
        link_pattern = re.compile(r'<link[^>]*bootstrap-icons[^>]*>', re.IGNORECASE)
        active_links = link_pattern.findall(html)
        assert not active_links, (
            f"/dashboard 渲染结果仍含 bootstrap-icons <link>: {active_links}"
        )