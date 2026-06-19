"""
导入 stock_profile.csv → quant.db + 补充 stock_basic
====================================================
从 A股全市场数据/stock_profile.csv 导入股票概况，
并将 industry/list_date 回填到 stock_basic 表的缺失行。

用法: python scripts/import_stock_profile.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from sqlalchemy import create_engine, text
from src.config import get_config, get_db_url


def main():
    csv_path = Path(__file__).parent.parent / "A股全市场数据" / "stock_profile.csv"
    print(f"加载: {csv_path}")

    df = pd.read_csv(csv_path, on_bad_lines='skip', low_memory=False)
    print(f"原始: {len(df)} rows, {df['code'].nunique()} stocks")

    # 清洗 code（去掉 sz/sh/bj 前缀）
    df["code"] = df["code"].str.replace(r'^(sz|sh|bj)', '', regex=True).str.zfill(6)
    df = df.dropna(subset=["code"])
    print(f"清洗后: {len(df)} rows")

    # 去重保留第一条
    df = df.drop_duplicates(subset=["code"], keep="first")

    # 选列导出
    export_cols = [
        "code", "name", "listedDate", "industry", "sector",
        "issuePrice", "regCapital", "chairman", "establishDate",
        "website", "business", "regAddress"
    ]
    available = [c for c in export_cols if c in df.columns]
    df_export = df[available].copy()

    # 重命名列以匹配 ORM
    col_map = {
        "listedDate": "listed_date",
        "issuePrice": "issue_price",
        "regCapital": "reg_capital",
        "establishDate": "establish_date",
        "regAddress": "reg_address",
    }
    df_export = df_export.rename(columns=col_map)

    # 数值类型转换
    for c in ["issue_price", "reg_capital"]:
        if c in df_export.columns:
            df_export[c] = pd.to_numeric(df_export[c], errors="coerce")

    # 日期转换
    if "listed_date" in df_export.columns:
        df_export["listed_date"] = pd.to_datetime(df_export["listed_date"], errors="coerce")

    print(f"导出: {len(df_export)} rows, {len(df_export.columns)} cols")

    # 写入 SQLite
    engine = create_engine(get_db_url(get_config()), echo=False)
    df_export.to_sql("stock_profile", engine, if_exists="replace", index=False)

    # 建索引
    with engine.connect() as conn:
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_sp_code ON stock_profile(code)",
            "CREATE INDEX IF NOT EXISTS idx_sp_industry ON stock_profile(industry)",
            "CREATE INDEX IF NOT EXISTS idx_sp_sector ON stock_profile(sector)",
        ]:
            conn.execute(text(idx_sql))
        conn.commit()

    # ===== 补充 stock_basic 的 industry 和 list_date =====
    sp_df = df_export[["code", "industry", "listed_date"]].dropna(subset=["code"])
    sb_df = pd.read_sql("SELECT code, industry, list_date FROM stock_basic", engine)

    updated_industry = 0
    updated_list_date = 0

    with engine.connect() as conn:
        # 遍历 stock_profile，更新 stock_basic 中缺失的字段
        for _, row in sp_df.iterrows():
            code = row["code"]
            ind_val = row.get("industry")
            ld_val = row.get("listed_date")

            # 检查 stock_basic 中该股票是否存在
            sb_row = sb_df[sb_df["code"] == code]
            if sb_row.empty:
                continue

            need_ind = ind_val and pd.notna(ind_val)
            if need_ind:
                existing_ind = sb_row.iloc[0].get("industry") if "industry" in sb_row.columns else None
                need_ind = pd.isna(existing_ind) or existing_ind == ""

            need_ld = ld_val and pd.notna(ld_val)
            if need_ld:
                existing_ld = sb_row.iloc[0].get("list_date") if "list_date" in sb_row.columns else None
                need_ld = pd.isna(existing_ld)

            if need_ind and need_ld:
                conn.execute(
                    text("UPDATE stock_basic SET industry = :ind, list_date = :ld WHERE code = :code"),
                    {"ind": ind_val, "ld": str(ld_val)[:10], "code": code}
                )
                updated_industry += 1
                updated_list_date += 1
            elif need_ind:
                conn.execute(
                    text("UPDATE stock_basic SET industry = :ind WHERE code = :code"),
                    {"ind": ind_val, "code": code}
                )
                updated_industry += 1
            elif need_ld:
                conn.execute(
                    text("UPDATE stock_basic SET list_date = :ld WHERE code = :code"),
                    {"ld": str(ld_val)[:10], "code": code}
                )
                updated_list_date += 1

        conn.commit()

    print(f"\n✅ stock_profile 表已创建: {len(df_export)} rows")
    print(f"   补充 stock_basic: industry +{updated_industry}, list_date +{updated_list_date}")

    # 验证
    count = pd.read_sql("SELECT COUNT(*) as n FROM stock_profile", engine).iloc[0, 0]
    print(f"   stock_profile 验证: {count} rows")

    engine.dispose()


if __name__ == "__main__":
    main()
