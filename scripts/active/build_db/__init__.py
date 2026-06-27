"""build_db — 统一的 CSV → SQLite 重建工具

数据架构 (LIVE_TRADING_ROADMAP §数据流):
  market_data/  (CSV 源, Git 提交)
      ↓
  database/quant.db  (派生, .gitignore, 本地生成)

用法:
    python scripts/build_db.py              # 全量重建 (清空 DB 后从 CSV 重建)
    python scripts/build_db.py --incremental  # 增量更新 (只导入 CSV 中比 DB 新的行)
    python scripts/build_db.py --table daily_price  # 只重建一个表

⚠️ 警告: 全量重建会 DELETE 现有 DB 数据, 然后从 CSV 重新填充
   建议在 run.py 启动前运行一次

模块划分 (2026-06-28 #86 拆 922 行单文件 → 5 个子模块):
  _schema.py      — SQLite DDL (stock_basic/daily_price/fund_flow_data/...)
  _core.py        — 核心 4 importers (stock_basic/daily_price/fund_flow/technical)
  _extended.py    — 扩展 5 importers (block_trade/dividend/announcements/holder_num/benchmark)
  _finance.py     — 财务 2 importers (finance_summary/stock_profile)
  _research.py    — 研究 3 importers (research_report/em_global_news/ths_hot_reason)
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "market_data"
DB_PATH = PROJECT_ROOT / "database" / "quant.db"

t0 = time.time()

from ._schema import SCHEMA_SQL  # noqa: E402
from ._core import (  # noqa: E402
    import_stock_basic,
    import_daily_price,
    import_fund_flow,
    import_technical_indicators,
)
from ._extended import (  # noqa: E402
    import_block_trade,
    import_dividend,
    import_announcements,
    import_holder_num,
    import_benchmark,
)
from ._finance import (  # noqa: E402
    import_finance,
    import_stock_profile,
)
from ._research import (  # noqa: E402
    import_research_report,
    import_em_global_news,
    import_ths_hot_reason,
)

IMPORTERS = {
    "stock_basic": import_stock_basic,
    "daily_price": import_daily_price,
    "fund_flow": import_fund_flow,
    "technical_indicators": import_technical_indicators,
    "block_trade": import_block_trade,
    "dividend": import_dividend,
    "announcements": import_announcements,
    "holder_num": import_holder_num,
    "benchmark": import_benchmark,
    "finance": import_finance,
    "stock_profile": import_stock_profile,
    "research_report": import_research_report,
    "em_global_news": import_em_global_news,
    "ths_hot_reason": import_ths_hot_reason,
}


def build(tables: list[str] | None = None, full: bool = True, data_dir: Path = DATA_DIR):
    """主构建流程"""
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    # 1. 建表
    print("[1/4] 建表 (含索引)")
    cur.executescript(SCHEMA_SQL)
    conn.commit()
    print(f"   ✅ 表结构就绪: {DB_PATH.name}")

    # 2. 选择要导入的表
    if tables is None:
        tables = list(IMPORTERS.keys())
    print(f"\n[2/4] 导入表: {tables}")
    print(f"   模式: {'全量 (DELETE + INSERT)' if full else '增量 (仅新行)'}")

    # 3. 逐表导入
    total = 0
    for t in (tables or list(IMPORTERS.keys())):
        if t not in IMPORTERS:
            print(f"   ⚠️ 未知表: {t}, 跳过")
            continue
        print(f"\n   [{t}]")
        try:
            n = IMPORTERS[t](cur, data_dir, full)
            conn.commit()
            print(f"   ✅ {t}: {n:,} 行")
            total += n
        except Exception as e:
            print(f"   ❌ {t} 失败: {e}")
            conn.rollback()
            continue

    # 4. 后置处理: ANALYZE + VACUUM
    print(f"\n[3/4] 后置处理 (ANALYZE + VACUUM)")
    cur.execute("ANALYZE")
    conn.commit()
    cur.execute("VACUUM")
    print(f"   ✅ ANALYZE + VACUUM 完成")

    # 5. 统计
    print(f"\n[4/4] 数据库统计")
    cur.execute("SELECT page_count * page_size / 1024.0 / 1024.0 FROM pragma_page_count(), pragma_page_size()")
    size_mb = cur.fetchone()[0]
    print(f"   大小: {size_mb:.2f} MB")

    for t in (tables or list(IMPORTERS.keys())):
        cur.execute(f'SELECT COUNT(*) FROM "{t}"')
        n = cur.fetchone()[0]
        print(f"   {t:<25} {n:>12,}")

    conn.close()
    elapsed = time.time() - t0
    print(f"\n✅ 完成 (耗时 {elapsed:.1f}s, 共导入 {total:,} 行)")


def main():
    parser = argparse.ArgumentParser(
        description="从 CSV 重建 quant.db (LIVE_TRADING_ROADMAP 数据流)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--incremental", action="store_true",
        help="增量模式: 仅导入比 DB 现有数据更新的行",
    )
    parser.add_argument(
        "--table", action="append", choices=list(IMPORTERS.keys()),
        help=f"只导入指定表 (可多次指定), 可选: {list(IMPORTERS.keys())}",
    )
    parser.add_argument(
        "--data-dir", type=Path, default=DATA_DIR,
        help=f"数据目录 (默认 {DATA_DIR.name})",
    )
    args = parser.parse_args()

    build(
        tables=args.table,
        full=not args.incremental,
        data_dir=args.data_dir,
    )


if __name__ == "__main__":
    main()
