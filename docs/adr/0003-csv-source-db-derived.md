# ADR-0003: CSV 是源，DB 是派生（gitignore）

| 字段 | 值 |
|---|---|
| **状态** | Accepted |
| **日期** | 2026-06-27（反向追溯） |
| **决策人** | @QingSongTears |
| **影响范围** | data 架构 / .gitignore |
| **目标阶段** | v2.1 架构治理 |

## 1. 上下文

数据资产有 2 种形式：
- **原始 CSV**（在 `market_data/`）：外部数据源下载，事实源
- **SQLite 派生**（在 `database/`）：CSV 清洗、归一化后的查询用库

历史问题：
- 偶尔会把 `database/quant.db` commit 进去（300+ MB）
- 重建 DB 不知道依赖哪些 CSV
- 备份和源混淆

## 2. 决策

**`market_data/*.csv` 是源**（Git LFS），**`database/*.db` 是派生**（`.gitignore`）。

```gitignore
# .gitignore 关键规则
database/*.db
database/*.db-*
*.bak
```

**任何时候数据库损坏/丢失，从 CSV 重新构建**：
```bash
python scripts/build_db.py   # 66s 重建 4.18M 行
```

## 3. 备选方案

### 方案 A：CSV 和 DB 都进 git
- 优点：clone 即用
- 缺点：仓库 10+ GB，clone 慢
- 否决：实际不可行

### 方案 B：DB 在云端 S3，CSV 在 git
- 优点：源 + 派生都集中
- 缺点：依赖外部存储，不便携
- 否决：增加运维

### 方案 C：CSV 在 git（LFS），DB 在本地（已选）
- 优点：源版本化，派生可重建
- 缺点：clone 后需跑 build_db
- 否决理由：不适用

## 4. 后果

### 正面
- 仓库大小可控（< 1GB）
- DB 损坏可恢复
- 任何 commit 可重现历史数据

### 负面
- 新成员 clone 后需跑 build_db（66s）
- 测试需自带 database/ 目录或自动 build

### 风险
- CSV 源被外部数据源删改 → 永远丢失
- **缓解**：每年备份到 NAS（owner 负责）

## 5. 实施

- [x] `.gitignore` 已配
- [x] `scripts/build_db.py` 重建脚本
- [ ] CI 加"database/ 目录必须被忽略"守门
- [ ] README 写明新成员 onboarding 流程

## 6. 关联

- 反对 / 推翻：无
- 关联 issue：#78（v2.1 治理入口）
- 实施入口：[scripts/build_db.py](../../scripts/build_db.py)
- 规范来源：`LIVE_TRADING_ROADMAP.md §三`

## 7. 备注

注意：`market_data/block_trade.csv` 和 `margin_trading.csv` 当前 git status 显示 `M`（modified）但无 commit —— 待 owner 检查是 LFS 自动变更还是手动改。
