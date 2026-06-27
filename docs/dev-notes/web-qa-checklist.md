# Web QA Checklist (2026-06-27)

> **目的**: 自助起 web 服务器,用 Playwright 调试每个页面 (数据/按钮/渲染), 有问题记任务单自行修复
> **范围**: src/web/templates/*.html + src/web/routes/{main,api,research}.py
> **状态**: 🟡 进行中

## 任务单（每发现一个 bug 就开一个 issue / 修复 commit）

- [x] T1: dashboard 页 加载 / 数据 / 按钮 (2026-06-27 完成: research.html 加载 common.js + api-key meta)
- [ ] T2: data 页 数据下载 / 状态
- [ ] T3: backtest 列表 / 详情 / lab
- [ ] T4: strategies 页 / compare
- [x] T5: research 页 (2026-06-27 完成: research.html 加载 common.js + api-key meta — Web QA #1)
- [ ] T6: simulate 页（Issue #82 改后验证）
- [ ] T7: screener / sector / stock_detail
- [ ] T8: signal / predict / signal_dashboard
- [ ] T9: portfolio / workbench
- [ ] T10: 其它 (fund-flow / ic_analysis / compare / etc)

## 已修复的 bug

### Web QA #1 — research.html 不加载 common.js (2026-06-27)

**症状**: Playwright 调试 /research 页面时, console 报 3 个 warning:
- `v5 chart load failed TypeError: Cannot read properties of undefined (reading fetch)`
- `v6 chart load failed ...`
- `tuning chart load failed ...`

**根因**: `src/web/templates/research.html` 是独立模板 (不继承 base.html),
没加载 `/static/js/common.js`, 所以 `window.QT` 永远是 `undefined`。
所有 `await window.QT.fetch(...)` 立即抛 TypeError。

**修复**: 在 `<head>` 注入 2 行:
```html
<meta name="api-key" content="{{ api_key }}">
<script src="/static/js/common.js"></script>
```
(2 行 + 一条注释解释原因, 不重构模板)

**回归测试**: `tests/test_web_pages.py` 新增 18 个测试, 覆盖:
- /research 5 个 tab (v5/v6/tuning/ic/dim) 都必须含 common.js + api-key meta
- /research 必须用 `window.QT.fetch` 而非 `fetch` (否则绕过 Bearer 拦截)
- 9 个继承 base.html 的关键页 (dashboard/data/strategies/...) 同样断言 (防回归)
- `/static/js/common.js` 静态文件 200 + 内容含 `window.QT` 和 `api-key`

## 新发现（待后续修复）

- ⚠️ **其他独立模板也可能缺 common.js, 需后续 T11+ 排查**:
  - 全部 23 个独立模板 (不 `{% extends 'base.html' %}`) 都没自动注入 common.js
  - 当前已知不依赖 common.js 的: error.html (无 JS)
  - 风险最高的 (用了 fetch/Chart): bull_backtest_report / bull-report / console / fund-flow-report /
    signal / predict / screener / sector / diagnose / portfolio / walk_forward /
    backtest_lab / signal_dashboard / strategy_compare / tuning_panel /
    v5_tuning / v6_compare / ic_analysis / dim_compare / multi_objective /
    predict_verify / predict_dashboard
  - 自动化排查方案: 在 conftest 写一个 fixture, 遍历所有 /<page>, 抓 HTML,
    assert '/static/js/common.js' 在 head 内。失败则报错是哪个 page 漏了。
  - 见 `tests/test_web_pages.py::TestBaseTemplatePagesIncludeCommonJS` 的 parametrize 模式

## 启动方式

```bash
# 启动 web (后台)
uvicorn src.web.app:app --host 0.0.0.0 --port 8000 &

# 验证
curl http://localhost:8000/
```

## Playwright 调试脚本

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    # 逐个访问
    for path in ["/", "/dashboard", "/data", ...]:
        page.goto(f"http://localhost:8000{path}")
        page.wait_for_load_state("networkidle")
        # 检查 200, 内容, 按钮
        ...
    browser.close()
```

## 当前进度
- [x] web 服务器启动
- [x] dashboard 调试 (T1 一并完成)
- [x] research 调试 (T5 完成, 修 Web QA #1)
- [ ] data 调试
- [ ] ...
