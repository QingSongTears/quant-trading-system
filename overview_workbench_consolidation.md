# 回测执行 → 工作台 合并

## 改动摘要
用户报"回测执行和工作台功能重合，保留工作台"。
工作台 = 回测执行 100% 功能 + 一键多模型对比 + 历史回测 + 模型图表，**功能子集**关系。

## 已删
- `src/web/templates/backtest.html` (整个文件)
- `src/web/routes/main.py` 的 `backtest_page` 函数体 (改为重定向)

## 已改

### `src/web/routes/main.py`
`/backtest` 改成 301 重定向到 `/workbench`，URL 参数透传：
```python
@router.get("/backtest", response_class=HTMLResponse)
async def backtest_page_redirect(request: Request):
    qs = request.url.query
    target = f"/workbench{qs}" if qs else "/workbench"
    return RedirectResponse(url=target, status_code=301)
```
保留 `/backtest/{result_id}` (回测详情页)，那个是核心看结果页面。

### `src/web/templates/workbench.html`
新增 `applyUrlParams()`：解析 URL 参数自动预填表单：
- `?code=600519` → 自动选股
- `?name=贵州茅台` → 自动填名字
- `?strategy=双均线交叉` → 自动选策略
- `?start=2024-01-01&end=2025-12-31` → 自动填日期

DOMContentLoaded 末尾调用 `applyUrlParams()`。

### 链接替换 (7 个文件)
| 文件 | 改动 |
|------|------|
| `base.html` | 删"回测执行"导航项；快捷键 g b 改成 /workbench |
| `index.html` | 首页卡片 + "运行新回测" + "开始第一次回测" → /workbench |
| `backtest_detail.html` | 面包屑 + "重新回测"按钮 → /workbench（自动带 code/name） |
| `compare.html` | "在回测执行页"提示 → "在工作台" |
| `data.html` | "对该股回测"按钮 → /workbench?code=&name= |
| `error.html` | 错误页"回测执行"按钮 → /workbench |

## 验证 (graphic_audit.py 跑完)
- 7 页面 HTTP 200，0 console error，0 pageerror，0 failed request
- `/backtest` → 301 → `/workbench` ✓
- backtest_detail: 3 canvas（净值/回撤/月度热力图）
- 工作台 `?code=603986` 链接预填股票 OK

## 收益
- 1 个入口（工作台）替代 2 个（回测执行 + 工作台）
- 用户从首页/数据页能一键直达带预填参数的回测表单
- API 端点（/api/backtest/run 等）保留，后端零改动