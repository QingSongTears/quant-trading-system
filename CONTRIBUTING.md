# CONTRIBUTING.md — 贡献指南

> **每个开发者 / AI 会话必读。违反规则的 commit 会被守门拒绝。**

---

## 🚦 30 秒速览

```bash
# 1. 读 AGENTS.md（项目唯一规则，5 分钟）
cat AGENTS.md

# 2. 读 docs/adr/ 全部现有决策（10 分钟）
ls docs/adr/

# 3. 装 hook（一次性）
pip install pre-commit
pre-commit install

# 4. 写代码 + 写测试

# 5. 跑守门
python dev_tools/hooks/run_all.py

# 6. 提交
git add .
git commit -m "feat(scoring): 新增 FundFlowScorer (#78)"
git push

# 7. 开 PR → 等 CI 全绿 → 合并
```

---

## 📐 三大铁律（不可违反）

### 1. 任何决策必须先看 `docs/adr/`

```bash
ls docs/adr/
cat docs/adr/0001-zh-class-alias.md
```

- 已沉淀决策：中文 alias / EventEngine 同步 / Direction 中文 enum / CSV 源-DB 派生
- 不同意某条 ADR？→ 写 `NNNN-oppose-XXX.md` 反驳
- **没有 ADR 的"破坏性 / 跨模块 / ≥3 文件影响"决策 = 不允许 commit**

### 2. 任何 commit 必须通过 7 个 AI 守门

| 守门 | 挡什么 |
|---|---|
| `check_naming.py` | 类名规范 + zh_name + en_name |
| `check_directory.py` | 新建目录需 ADR |
| `check_legacy.py` | 禁止引用已废弃模块 |
| `check_test_required.py` | 新 src/ 文件必须有 test |
| `check_import_canonical.py` | 禁止绕过统一门面 |
| `check_file_size.py` | 单文件 > 500 行禁止 |
| `check_commit_msg.py` | Conventional Commits + issue 关联 |

**全部 7 项通过 = commit 允许**。任一失败 = commit 拒绝。

### 3. 任何代码改动必须关联一个 issue

```
<type>(<scope>): <subject> (#<issue_number>)
```

✅ 正确：
- `feat(scoring): 新增 FundFlowScorer 接入 registry (#78)`
- `fix(web): 修复 dashboard 在空数据时 NPE (#79)`
- `refactor(legacy): 删 stock_screener 死引用 (#80)`

❌ 错误（守门拒绝）：
- `优化` （无 type + 纯中文）
- `update code` （无 type + 无 issue）
- `feat(scoring): 新增 scorer` （无 issue）

---

## 🌿 分支策略

| 分支 | 用途 |
|---|---|
| `main` | 已发布稳定版本（仅通过 PR 合入） |
| `develop` | 主开发分支（PR 合入这里） |
| `feat/issue-N-*` | 新功能 |
| `fix/issue-N-*` | bug 修复 |
| `refactor/issue-N-*` | 重构 |
| `chore/issue-N-*` | 杂项 |
| `docs/issue-N-*` | 仅文档 |

**禁止直接 push 到 `main` / `develop`**（除 owner 紧急修复外）。

---

## 🏷️ 版本策略（SemVer）

```
v<MAJOR>.<MINOR>.<PATCH>[-<PRE>]
```

| 位置 | 升级时机 | 例子 |
|---|---|---|
| MAJOR | 架构破坏性变更 | v2.0 → v3.0（实盘化） |
| MINOR | 新功能（向后兼容） | v2.0 → v2.1（治理） |
| PATCH | bug 修复 | v2.1.0 → v2.1.1 |
| PRE | 预发布 | v2.1.0-refactor-foundation |

**关键里程碑必打 tag**：每个 sprint 结束 / OOS 通过 / 模拟盘上线 / 实盘化。

---

## 🧪 测试要求

- **新 src/ 文件必须有 test**：守门自动挡
- **覆盖率门禁**：当前 30%，每个 minor 提升 15%
- **测试位置**：`tests/unit/` `integration/` `regression/` `e2e/`
- **夹具**：`tests/conftest.py`

```bash
# 跑全部测试
pytest

# 跑某个
pytest tests/unit/scoring/

# 覆盖率
pytest --cov=src --cov-report=term-missing
```

---

## 📝 文档要求

- **新功能** → 更新 `README.md` + `CHANGELOG.md`
- **新决策** → 写 `docs/adr/NNNN-*.md`
- **新页面（Web）** → 更新 `docs/CODE_WIKI.md` 路由表
- **新数据源** → 更新 `docs/DATA_SCHEMA.md`

---

## 🚨 紧急绕过（生产事故）

```bash
git commit --no-verify   # 跳过守门
```

**必须在 24h 内**：
1. 写 ADR `NNNN-emergency-bypass-YYYY-MM-DD.md` 说明原因
2. 修复违规
3. 重新正常 commit

**禁止在绕过守门后继续开发**——会让技术债复利。

---

## 🆘 遇到问题？

| 情况 | 行动 |
|---|---|
| 守门脚本有 bug / 误报 | [开 issue](../../issues/new) 标签 `hook-bug` |
| 不同意某条 ADR | 写反向 ADR（见 §铁律 1） |
| 不知道 X 该放哪 | 看 [AGENTS.md §2](AGENTS.md) 目录地图 |
| 不知道用哪个基类 | 看 [AGENTS.md §3](AGENTS.md) 命名规范 |
| 紧急绕过 | 见上节 |

---

## 🤖 AI 会话启动协议

> 每个新开 AI 会话，**第 1 件事**就是执行这个清单：

1. 读 `AGENTS.md`（5 分钟）
2. 读 `docs/adr/` 所有现有 ADR（10 分钟）
3. 读 `docs/CODE_WIKI.md` 目录树（5 分钟）
4. 读 `LIVE_TRADING_ROADMAP.md` §一 + §十（5 分钟）
5. `git log --oneline -30` 看最近做了什么
6. 开始工作

**任何不确定** → 优先写 ADR 或问 owner，不要"先 commit 再说"。

---

**祝开发愉快！** 🎉
