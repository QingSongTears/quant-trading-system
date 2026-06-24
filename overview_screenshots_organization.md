# 截图统一收拢改造

## 背景
项目里散落了 111 个审计/冒烟截图，13 个已被 git 跟踪，剩下 untracked 散落在 `output/` 各处。
每次运行 `git status` 都被一堆 PNG 干扰，commit 时容易误带。

## 改造内容

### 1. 新增统一目录 `screenshots/`
按用途分子目录，与原 `output/*_audit/` 一一对应：

| 子目录       | 用途                 | 来源              |
|-------------|--------------------|------------------|
| console/    | /console 7 个 tab   | audit_console.py |
| nav/        | 全量导航页审计        | audit_all_nav_pages.py |
| graphic/    | 图形专项审查          | graphic_audit.py  |
| smoketest/  | 冒烟测试             | headless_smoketest.py / audit_chart_render.py |
| header/     | header 高度审计     | verify_headers_fixed.py |
| home_tabs/  | 主页 Tab 切换        | audit_home_tabs.py |
| stock_detail/ | 个股详情页         | verify_stock_detail.py |
| route/      | 全量路由探针         | audit_all_routes.py |
| dbg/        | 临时调试             | debug_*.py       |

### 2. `.gitignore` 加固
```gitignore
# 截图/审计产物 (本地生成, 不入 git)
screenshots/
# 兼容旧的散落位置
output/*.png
output/*.jpg
output/*.jpeg
output/*_audit/*.png
```

### 3. 历史截图解绑
`git rm --cached` 13 个 tracked PNG，文件保留在 `screenshots/` 对应子目录。

### 4. 脚本写入路径更新（9 个）
全部从 `Path("output/...")` 改为 `ROOT / "screenshots" / <类别>`：
- scripts/audit_console.py
- scripts/audit_all_nav_pages.py
- scripts/audit_all_routes.py
- scripts/audit_home_tabs.py
- scripts/graphic_audit.py
- scripts/headless_smoketest.py
- scripts/audit_chart_render.py
- scripts/verify_headers_fixed.py
- scripts/verify_stock_detail.py
- scripts/deep_functional_test.py
- scripts/full_html_audit.py
- scripts/realistic_interaction_test.py
- scripts/debug_market.py
- scripts/debug_workbench.py

## 验证
- `git ls-files | grep -E "\.(png|jpg|jpeg)$"` → 空 ✅
- `git check-ignore -v screenshots/console/tab_overview.png` → ✅ 匹配
- `git check-ignore -v output/audit_nav_dashboard.png` → ✅ 匹配
- 111 个 PNG 全部归档到 `screenshots/`，按子目录分类
- 14 个脚本全部切换到新路径

## 下一步
- [ ] 提交 git (含 .gitignore + 脚本修改 + 13 个 staged deletions)
- [ ] 后续审计脚本默认输出到 `screenshots/<类别>/`