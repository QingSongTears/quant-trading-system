"""
一次性数据修复: fund_flow_data.code 去前缀
============================================
fund_flow_data 表的 code 列保留了 'sh'/'sz' 前缀,
与 daily_price.code (6 位纯数字) 不匹配,导致 fund_flow_scorer 永远查不到股票。

修复: UPDATE 去前缀, 统一为 6 位数字 code。
- sh600000 → 600000
- sz000001 → 000001
- bj830xxx → 830xxx (如有)

⚠️  这是 DDL, 修复前已自动备份。
"""
import sys
from pathlib import Path
import shutil
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text
from src.db.engine import get_engine
from src.config import get_config

DB_PATH = PROJECT_ROOT / "database" / "quant.db"
BACKUP_PATH = PROJECT_ROOT / "database" / f"quant.db.bak.{datetime.now():%Y%m%d_%H%M%S}"


def main():
    engine = get_engine()

    # 1. 备份数据库
    print(f"备份数据库 → {BACKUP_PATH.name}")
    shutil.copy2(DB_PATH, BACKUP_PATH)
    print(f"  ✅ 已备份")

    # 2. 检查当前状态
    with engine.connect() as conn:
        before = conn.execute(text(
            "SELECT code, COUNT(*) as n FROM fund_flow_data "
            "GROUP BY code ORDER BY code LIMIT 5"
        )).fetchall()
        print(f"\n修复前样例:")
        for c, n in before:
            print(f"  {c}: {n} 行")

        total = conn.execute(text(
            "SELECT COUNT(*) FROM fund_flow_data"
        )).scalar()
        distinct_codes = conn.execute(text(
            "SELECT COUNT(DISTINCT code) FROM fund_flow_data"
        )).scalar()
        print(f"\n  总行数: {total:,}")
        print(f"  不同 code 数: {distinct_codes:,}")

        # 3. 检查需要修复的代码
        prefix_cnt = conn.execute(text(
            "SELECT "
            "  SUM(CASE WHEN code LIKE 'sh%' THEN 1 ELSE 0 END) as sh, "
            "  SUM(CASE WHEN code LIKE 'sz%' THEN 1 ELSE 0 END) as sz, "
            "  SUM(CASE WHEN code LIKE 'bj%' THEN 1 ELSE 0 END) as bj, "
            "  SUM(CASE WHEN LENGTH(code) = 6 THEN 1 ELSE 0 END) as ok_6 "
            "FROM fund_flow_data"
        )).fetchone()
        print(f"\n  'sh%': {prefix_cnt[0]:,} 行")
        print(f"  'sz%': {prefix_cnt[1]:,} 行")
        print(f"  'bj%': {prefix_cnt[2]:,} 行")
        print(f"  已是6位: {prefix_cnt[3]:,} 行")

    # 4. 执行修复
    print(f"\n执行 UPDATE ...")
    with engine.connect() as conn:
        # 去前缀(只处理 sh/sz/bj 前缀, 安全)
        result = conn.execute(text(
            "UPDATE fund_flow_data "
            "SET code = SUBSTR(code, 3) "
            "WHERE code LIKE 'sh%' OR code LIKE 'sz%' OR code LIKE 'bj%'"
        ))
        conn.commit()
        print(f"  ✅ 影响行数: {result.rowcount:,}")

    # 5. 验证
    with engine.connect() as conn:
        after = conn.execute(text(
            "SELECT code, COUNT(*) as n FROM fund_flow_data "
            "GROUP BY code ORDER BY code LIMIT 5"
        )).fetchall()
        print(f"\n修复后样例:")
        for c, n in after:
            print(f"  {c}: {n} 行")

        # 与 daily_price 对齐验证
        overlap = conn.execute(text(
            "SELECT COUNT(DISTINCT ff.code) FROM fund_flow_data ff "
            "JOIN daily_price dp ON ff.code = dp.code"
        )).scalar()
        ff_total = conn.execute(text(
            "SELECT COUNT(DISTINCT code) FROM fund_flow_data"
        )).scalar()
        dp_total = conn.execute(text(
            "SELECT COUNT(DISTINCT code) FROM daily_price"
        )).scalar()
        print(f"\n  fund_flow 不同 code: {ff_total:,}")
        print(f"  daily_price 不同 code: {dp_total:,}")
        print(f"  重叠 code: {overlap:,}")

    print(f"\n备份保留在: {BACKUP_PATH}")


if __name__ == "__main__":
    main()