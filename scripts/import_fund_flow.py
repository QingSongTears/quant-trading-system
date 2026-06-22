"""
导入 fund_flow_120d.csv → quant.db.fund_flow_data
==================================================
资金流向增量数据导入脚本。

前置条件: fund_flow_120d.csv 必须是通过 Git LFS 拉取的真实文件（非 LFS 指针）。
若当前环境无法解析 LFS，请在有 Git LFS 的机器执行 `git lfs pull` 后运行本脚本。

用法: python scripts/import_fund_flow.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from sqlalchemy import create_engine, text
from src.config import get_config, get_db_url


def main():
    csv_path = Path(__file__).parent.parent / "market_data" / "fund_flow_120d.csv"
    print(f"加载: {csv_path}")

    # 检查是否为 LFS 指针
    with open(csv_path, "r") as f:
        header = f.readline().strip()
    if header.startswith("version https"):
        print("[ERROR] fund_flow_120d.csv 是 Git LFS 指针文件（133 bytes），不是真实数据。")
        print("        请在安装了 Git LFS 的环境中运行: git lfs pull")
        sys.exit(1)

    # 分块读取
    chunksize = 200000
    engine = create_engine(get_db_url(get_config()), echo=False)

    # 清空旧数据
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM fund_flow_data"))
        conn.commit()

    total = 0
    for chunk in pd.read_csv(csv_path, chunksize=chunksize, on_bad_lines='skip', low_memory=False):
        # 清洗列名（统一为 trade_date）
        if 'date' in chunk.columns and 'trade_date' not in chunk.columns:
            chunk = chunk.rename(columns={'date': 'trade_date'})

        # 只保留 fund_flow_data 表需要的列
        keep_cols = ['code', 'trade_date', 'main_net', 'super_large_net',
                     'large_net', 'medium_net', 'small_net']
        available = [c for c in keep_cols if c in chunk.columns]
        chunk = chunk[available].copy()

        # 日期转换
        if 'trade_date' in chunk.columns:
            chunk['trade_date'] = pd.to_datetime(chunk['trade_date'], errors='coerce')

        chunk.to_sql("fund_flow_data", engine, if_exists="append", index=False)
        total += len(chunk)
        print(f"  ... 已导入 {total:,} 行")

    # 建索引
    with engine.connect() as conn:
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_ff_code ON fund_flow_data(code)",
            "CREATE INDEX IF NOT EXISTS idx_ff_date ON fund_flow_data(trade_date)",
            "CREATE INDEX IF NOT EXISTS idx_ff_code_date ON fund_flow_data(code, trade_date)",
        ]:
            conn.execute(text(idx_sql))
        conn.commit()

    # 验证
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT COUNT(*) as n, COUNT(DISTINCT code) as stocks,"
            " MIN(trade_date) as d1, MAX(trade_date) as d2 FROM fund_flow_data"
        )).fetchone()
        print(f"\n✅ fund_flow_data 导入完成:")
        print(f"   总行数: {row[0]:,}")
        print(f"   股票数: {row[1]:,}")
        print(f"   日期范围: {row[2]} ~ {row[3]}")

    engine.dispose()


if __name__ == "__main__":
    main()
