# 在你电脑跑 baostock，补 finance 2017Q2-2024Q4

> 为什么不在我这里跑：本机的 baostock 账号（IP）已被加入黑名单（错误信息：`黑名单用户，请与管理员联系`），无法登录。你的电脑 IP 没被封，由你跑 → 输出 CSV → push 到 git → 我这边 import 入库。

---

## 1. 你电脑环境准备

```bash
# 1. 装 baostock（如果没装过）
pip install baostock

# 2. 拉最新代码
cd /path/to/quant-trading-system   # 或 Windows: cd E:\work\work\quant-trading-system
git pull
git status  # 应该 clean
```

---

## 2. 跑 baostock 多年财务

> **省时间提示**：脚本支持 `--start-year/--end-year/--quarters` 多年模式，会**自动跳过已存在**的 `(code, stat_date)`，所以放心重跑。

### 2.1 一键跑（推荐：晚上睡前启动，第二天看结果）

```bash
# Windows PowerShell
python scripts\import_from_westock_baostock_akshare.py --task finance --start-year 2017 --end-year 2024 --quarters 1 2 3 4

# 或 Git Bash / Linux
python scripts/import_from_westock_baostock_akshare.py --task finance --start-year 2017 --end-year 2024 --quarters 1 2 3 4
```

**任务量**：8 年 × 4 quarter × ~5569 只股 = **178,208 次 baostock 查询**
**预计耗时**：
- 网络好 + 你的 IP 没限流：**~3-5 小时**（实测 12-15 股/秒）
- 如果中途 baostock 也限流你的 IP：脚本会自动 retry 3 次 + 退避 30s，但**单条被限的代码会进 `fail_codes`**

### 2.2 后台跑（不占前台）

```bash
# Windows: start /B
start /B python scripts\import_from_westock_baostock_akshare.py --task finance --start-year 2017 --end-year 2024 --quarters 1 2 3 4 > C:\Users\admin\AppData\Local\Temp\finance_baostock.log 2>&1

# 或用 PowerShell 启 BG
Start-Process -NoNewWindow -FilePath python -ArgumentList "scripts/import_from_westock_baostock_akshare.py","--task","finance","--start-year","2017","--end-year","2024","--quarters","1","2","3","4" -RedirectStandardOutput C:\Users\admin\AppData\Local\Temp\finance_baostock.out -RedirectStandardError C:\Users\admin\AppData\Local\Temp\finance_baostock.err
```

### 2.3 进度查看

```bash
# 实时 tail
Get-Content C:\Users\admin\AppData\Local\Temp\finance_baostock.log -Wait -Tail 30

# 看 CSV 行数变化（每 quarter 完会 flush）
Get-Item market_data\finance_summary.csv | Select-Object Length, LastWriteTime
```

### 2.4 失败处理

**如果中途 baostock 限流你的 IP**：
- 脚本会退避 30s 后继续，但已失败的 code 不会重试
- **解决**：再跑一次（`--start-year 2017 --end-year 2024 --quarters 1 2 3 4`），自动增量跳过已成功的

**如果 baostock 升级要 token 之类**：
- 看错误信息贴给我
- baostock 0.9.10 是当前稳定版，匿名登录即可

---

## 3. 跑完后推 CSV

```bash
# 检查 CSV 行数（应该从 28888 涨到 ~18-20 万）
python -c "import csv; print(len(list(csv.DictReader(open('market_data/finance_summary.csv', encoding='utf-8-sig')))))"

# 推代码
git add market_data/finance_summary.csv
git commit -m "feat: baostock finance_summary 2017Q2-2024Q4 多年补全"
git push origin develop
```

**注意**：
- CSV 文件可能 50-150 MB（取决于行数），git push 可能要一会儿
- 如果太大被 GitHub 拒（>100MB），告诉我，我用 git-lfs 或者你分批 push

---

## 4. 我这边接手

你 push 完后告诉我，我这边会：

1. `git pull` 拉最新
2. 跑 `python scripts/consolidate_db.py --import-only --no-validate` 把 CSV 增量 import 入库（已支持 INSERT OR IGNORE）
3. 跑 `python scripts/show_db_stats.py` 验收：`finance_summary` 行数应该 ~18-20 万，`max(stat_date) = 2024-12-31`
4. 跑 `python scripts/fill_circulating_shares.py` 增量填 `stock_profile.circulating_shares`（因为 finance 多了 8 年的 `liqaShare`）
5. 报结果 + commit

---

## 5. 验收标准

完成后 DB 状态：
- `finance_summary` 行数：**~180,000 - 200,000**（按 8 年 × 4 quarter × 5500 股 × 0.8 成功估算）
- `finance_summary.max(stat_date) = 2024-12-31`（2024Q4）
- `stock_profile.circulating_shares` 填充率：5083 → 5500+ (提升 8 个百分点)
- `fund_flow_scorer` 的 regCapital 兜底逻辑占比下降（**更高比例**用真实流通股）

---

## 6. 时间线

| 步骤 | 你 | 我 |
|---|---|---|
| 1. 装 baostock + git pull | ✅ 5 min | — |
| 2. 启动 BG 跑 (晚上/午休启动) | ✅ 3-5 小时 BG | — |
| 3. 检查 CSV 行数 + push | ✅ 10 min | — |
| 4. import 入库 + 验收 | — | ✅ 5-10 min |
| 5. 重新填 circulating_shares | — | ✅ 10-20 min |
| 6. 报结果 | — | ✅ |

---

## 7. 不需要做的事

- ❌ 不需要我写新 baostock 脚本（现有 `task_finance` 已支持多年模式 + retry + 增量）
- ❌ 不需要新建 CSV 文件（脚本会追加到现有 `market_data/finance_summary.csv`）
- ❌ 不需要重启 baostock / 换 token（0.9.10 匿名登录即可）
- ❌ 不需要担心你电脑的 akshare / westock（这个任务只调 baostock）

---

## 8. 关联文件

- `scripts/import_from_westock_baostock_akshare.py` line 277-414：`task_finance` 函数（核心）
- `market_data/finance_summary.csv`：输出文件（28,888 行 → ~180k 行）
- `scripts/consolidate_db.py`：CSV → DB importer
- `scripts/fill_circulating_shares.py`：用新 finance 数据填流通股本
- `scripts/show_db_stats.py`：DB 状态统计

---

*创建：2026-06-24 16:39*
*创建者：agent-b83a3c808168*
*接收者：用户（在用户电脑执行）*