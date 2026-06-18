"""
导入 finance_summary.csv → quant.db + 基本面评分器 v3
======================================================
基于真实季度财报数据（56,952行/4709只/2023-2025）替换旧的finance_snapshot_v2。

用法: python scripts/import_finance_summary.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
from src.config import get_config, get_db_url


def main():
    csv_path = Path(__file__).parent.parent / "A股全市场数据" / "finance_summary.csv"
    print(f"加载: {csv_path}")

    df = pd.read_csv(csv_path, on_bad_lines='skip', low_memory=False)
    print(f"原始: {len(df)} rows, {df['symbol'].nunique()} stocks")

    # 清洗
    df["code"] = df["symbol"].str.extract(r'([0-9]{6})')
    df = df.dropna(subset=["code"])
    print(f"清洗后: {len(df)} rows")

    # 只保留最新季度的数据（按 code + _date 去重，取最新）
    df = df.sort_values(["_date"], ascending=False)
    df_latest = df.drop_duplicates(subset=["code"], keep="first")
    print(f"去重(每只股票最新季度): {len(df_latest)} rows")

    # 选关键列
    key_cols = [
        "code", "_date", "EndDate",
        "ROE", "ROETTM", "ROEWeighted",
        "EPS", "EPSTTM", "BasicEPS", "DilutedEPS",
        "NAPS",
        "DebtAssetsRatio", "DebtEquityRatio",
        "OperatingRevenue", "OperatingRevenueTTM", "OperatingRevenueGrowRate",
        "OperatingProfit", "OperatingProfitTTM",
        "TotalOperatingRevenue",
        "NPParentCompanyOwners", "NPParentCompanyOwnersTTM",
        "NPParentCompanyYOY",
        "NetOperateCashFlow", "NetOperateCashFlowTTM",
        "NetProfitRatio", "NetProfitRatioTTM",
        "TotalAssets", "TotalShareholderEquity", "TotalLiability",
        "NetAssetGrowRate", "TotalAssetGrowRate",
        "CashFlowPS", "OperCashFlowPS", "MainIncomePS",
    ]
    available = [c for c in key_cols if c in df_latest.columns]
    df_export = df_latest[available].copy()
    print(f"导出列: {len(available)}")

    # 写入 SQLite
    engine = create_engine(get_db_url(get_config()), echo=False)
    df_export.to_sql("finance_summary", engine, if_exists="replace", index=False)

    # 建索引
    with engine.connect() as conn:
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_fs_code ON finance_summary(code)"))
        conn.commit()

    # 验证
    count = pd.read_sql("SELECT COUNT(*) as n FROM finance_summary", engine).iloc[0, 0]
    print(f"\n✅ finance_summary 表已创建: {count} rows")
    print(f"   字段: {', '.join(available[:8])}... 共{len(available)}个")

    engine.dispose()


if __name__ == "__main__":
    main()
