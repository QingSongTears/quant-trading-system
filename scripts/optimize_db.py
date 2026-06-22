"""
数据库全面优化脚本
==================

1. 创建缺失索引
2. 回填预计算字段 (pct_change / turnover)
3. ANALYZE 收集统计信息
4. REINDEX 重建索引
5. VACUUM 碎片整理

⚠️ 执行前请备份 database/quant.db
"""
import sqlite3
import time
from pathlib import Path
from datetime import datetime

DB_PATH = Path(r"E:\work\work\quant-trading-system\database\quant.db")
BACKUP_PATH = DB_PATH.with_suffix(f".db.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}")

t0 = time.time()


def backup_db():
    """备份数据库"""
    import shutil
    shutil.copy2(DB_PATH, BACKUP_PATH)
    print(f"[backup] ✅ 备份: {BACKUP_PATH.name}")


def optimize():
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    # ===== 1. 缺失索引检查 + 补建 =====
    print("\n[1/6] 索引检查 + 补建")
    print("-" * 60)

    # 列出已有索引
    cur.execute("SELECT name, tbl_name, sql FROM sqlite_master WHERE type='index'")
    existing = {row[1]: set() for row in cur.fetchall()}
    for row in cur.fetchall():
        existing[row[1]].add(row[0])

    # 应有索引 (基于常见查询模式)
    expected_indexes = {
        "daily_price": [
            ("idx_dp_code_date", "(code, trade_date)"),
            ("idx_dp_date", "(trade_date)"),
            ("idx_dp_date_code", "(trade_date, code)"),  # for 全市场某日扫描
        ],
        "benchmark_data": [
            ("idx_bd_code_date", "(index_code, trade_date)"),
            ("idx_bd_date", "(trade_date)"),
        ],
        "stock_basic": [
            ("idx_sb_market", "(market)"),
        ],
        "stock_profile": [
            ("idx_sp_code", "(code)"),
            ("idx_sp_industry", "(industry)"),
            ("idx_sp_sector", "(sector)"),
        ],
        "fund_flow_data": [
            ("idx_ff_code_date", "(code, trade_date)"),
            ("idx_ff_date", "(trade_date)"),
        ],
        "technical_indicators": [
            ("idx_ti_code_date", "(code, trade_date)"),
            ("idx_ti_date", "(trade_date)"),
        ],
        "finance_summary": [
            ("idx_fs_code", "(code)"),
            ("idx_fs_code_date", "(code, _date)"),
        ],
    }

    for table, indexes in expected_indexes.items():
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        if not cur.fetchone():
            print(f"   ⏭ {table} 不存在, 跳过")
            continue

        for idx_name, cols in indexes:
            cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
                (idx_name,),
            )
            if cur.fetchone():
                print(f"   ✅ {table}.{idx_name} 已存在")
            else:
                sql = f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} {cols}"
                cur.execute(sql)
                print(f"   ➕ {table}.{idx_name} 创建 {cols}")

    # ===== 2. 回填预计算字段 =====
    print("\n[2/6] 回填预计算字段 (pct_change / turnover)")
    print("-" * 60)

    # daily_price: pct_change = (close - prev_close) / prev_close * 100
    # turnover: 不在 daily_price 里, 跳过
    cur.execute("SELECT COUNT(*) FROM daily_price WHERE pct_change IS NULL")
    null_count = cur.fetchone()[0]
    print(f"   daily_price.pct_change 空值: {null_count:,}")

    if null_count > 0:
        print("   计算并回填 pct_change (按 code 分组)")
        # 用 SQL 一次性回填 (window function)
        # SQLite 3.25+ 支持 window functions
        cur.execute("""
            UPDATE daily_price
            SET pct_change = (
                SELECT ROUND(
                    (dp2.close - dp2.prev_close) / dp2.prev_close * 100,
                    4
                )
                FROM (
                    SELECT code, trade_date, close,
                           LAG(close) OVER (PARTITION BY code ORDER BY trade_date) AS prev_close
                    FROM daily_price
                ) dp2
                WHERE dp2.code = daily_price.code
                  AND dp2.trade_date = daily_price.trade_date
            )
            WHERE daily_price.pct_change IS NULL
        """)
        conn.commit()
        print(f"   ✅ 更新 {cur.rowcount:,} 行")

    # ===== 3. ANALYZE =====
    print("\n[3/6] ANALYZE 收集统计信息")
    print("-" * 60)
    cur.execute("ANALYZE")
    conn.commit()
    print("   ✅ ANALYZE 完成")

    # ===== 4. REINDEX =====
    print("\n[4/6] REINDEX 重建索引")
    print("-" * 60)
    cur.execute("REINDEX")
    print("   ✅ REINDEX 完成")

    # ===== 5. integrity_check =====
    print("\n[5/6] 数据库完整性检查")
    print("-" * 60)
    cur.execute("PRAGMA integrity_check")
    result = cur.fetchone()[0]
    if result == "ok":
        print("   ✅ integrity_check: ok")
    else:
        print(f"   ❌ integrity_check: {result}")

    # ===== 6. VACUUM =====
    print("\n[6/6] VACUUM 碎片整理")
    print("-" * 60)
    cur.execute("VACUUM")
    print("   ✅ VACUUM 完成")

    # 优化后统计
    print("\n" + "=" * 60)
    print("优化后统计")
    print("=" * 60)

    cur.execute("SELECT page_count, page_size * page_count / 1024.0 / 1024.0 AS size_mb FROM pragma_page_count(), pragma_page_size()")
    pages, size_mb = cur.fetchone()
    print(f"   数据库大小: {size_mb:.2f} MB ({pages} 页)")

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    for (t,) in cur.fetchall():
        cur.execute(f'SELECT COUNT(*) FROM "{t}"')
        n = cur.fetchone()[0]
        print(f"   {t:<25} {n:>12,}")

    conn.close()
    elapsed = time.time() - t0
    print(f"\n✅ 完成 (耗时 {elapsed:.1f}s)")


if __name__ == "__main__":
    print("=" * 60)
    print(f"数据库优化 — {DB_PATH.name}")
    print("=" * 60)
    backup_db()
    optimize()
