# dev_tools/hooks/ — AI 守门脚本

> 7 个 Python 脚本，由 `.githooks/pre-commit` 在每次 commit 前自动调用。
> 任一返回非 0 → commit 失败。

| # | 脚本 | 职责 | 失败示例 |
|---|---|---|---|
| 1 | `check_naming.py` | 类名规范 + zh_name + en_name | 新 `class V6Strategy` 缺 `zh_name` |
| 2 | `check_directory.py` | 新建目录需 ADR 引用 | 新建 `src/strategies_v2/` 但无 ADR |
| 3 | `check_legacy.py` | 禁止引用已废弃模块 | `from src.strategies.stock_screener import ...` |
| 4 | `check_test_required.py` | 新 src/ 文件必须有 test | 新 `src/scoring/new_scorer.py` 无 `tests/test_*.py` |
| 5 | `check_import_canonical.py` | 禁止绕过统一门面 | `from src.models.database import *` 绕过 Repository |
| 6 | `check_file_size.py` | 单文件 > 500 行禁止 | `param_server.py` 2846 行 |
| 7 | `check_commit_msg.py` | Conventional Commits + issue 关联 | `优化 (#75)` 中文 + 无 type |

## 本地运行

```bash
# 跑全部守门
python dev_tools/hooks/run_all.py

# 单独跑某一个
python dev_tools/hooks/check_naming.py src/scoring/

# 跑 git hook（自动）
git commit -m "..."   # 自动触发 .githooks/pre-commit
```

## 绕过（仅紧急）

```bash
git commit --no-verify   # 跳过
# 必须在 24h 内补 ADR 说明为何绕过
```

## 调试

每个脚本都支持 `--verbose` 输出具体行号和原因：

```bash
python dev_tools/hooks/check_naming.py src/ --verbose
```
