"""
重新生成 combined_3d_scores.csv — 全量资金面 (SQL批量版)
========================================================
优化策略: 
  - 评分器用 batch_score (已优化), 不逐只查询
  - 未来收益用 SQL 窗口函数一次性计算, 不预加载内存
  - 对每10个日期批量提交一次

输出: data/combined_3d_scores.csv
"""

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text
from src.db.engine import get_engine

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import get_config, get_db_url
from src.scoring.technical_scorer import TechnicalScorer
from src.scoring.fundamental_scorer import FundamentalScorer
from src.scoring.fund_flow_scorer import FundFlowScorer


def get_weekly_dates(engine) -> list:
    query = """
        SELECT DISTINCT trade_date FROM daily_price
        WHERE trade_date >= '2024-06-01' ORDER BY trade_date
    """
    df = pd.read_sql(query, engine)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["week"] = df["trade_date"].dt.isocalendar().week.astype(int)
    df["year"] = df["trade_date"].dt.isocalendar().year.astype(int)
    weekly = df.groupby(["year", "week"])["trade_date"].max().reset_index()
    return sorted(weekly["trade_date"].dt.strftime("%Y-%m-%d").tolist())


def compute_future_returns(engine, as_of_date: str) -> pd.DataFrame:
    """用SQL窗口函数一次性计算所有股票的未来收益 (交易日位移)"""
    query = f"""
        WITH future_prices AS (
            SELECT code, trade_date, close,
                   ROW_NUMBER() OVER (PARTITION BY code ORDER BY trade_date) as rn
            FROM daily_price
            WHERE trade_date > '{as_of_date}'
        ),
        base AS (
            SELECT code, close as base_close
            FROM daily_price WHERE trade_date = '{as_of_date}'
        )
        SELECT b.code,
               (MAX(CASE WHEN fp.rn = 20 THEN fp.close END) - b.base_close) 
                   / b.base_close * 100 as ret_20d,
               (MAX(CASE WHEN fp.rn = 40 THEN fp.close END) - b.base_close) 
                   / b.base_close * 100 as ret_40d,
               (MAX(CASE WHEN fp.rn = 60 THEN fp.close END) - b.base_close) 
                   / b.base_close * 100 as ret_60d
        FROM base b
        LEFT JOIN future_prices fp ON b.code = fp.code
        GROUP BY b.code
    """
    return pd.read_sql(query, engine)


def main():
    print("=" * 60, flush=True)
    print("重新生成 combined_3d_scores.csv", flush=True)
    print("=" * 60, flush=True)

    config = get_config()
    db_url = get_db_url(config)
    engine = get_engine()

    dates = get_weekly_dates(engine)
    print(f"\n共 {len(dates)} 个周度截面: {dates[0]} ~ {dates[-1]}", flush=True)

    print("初始化评分器...", flush=True)
    tech = TechnicalScorer(engine=engine)
    fundam = FundamentalScorer(engine=engine)
    fundflow = FundFlowScorer(engine=engine)
    print("  ✅ 技术v3 + 基本面v2 + 资金面v2.1\n", flush=True)

    all_rows = []
    start_time = time.time()

    for i, as_of_date in enumerate(dates):
        t_iter = time.time()

        # 获取当日股票
        codes_df = pd.read_sql(
            f"SELECT DISTINCT code FROM daily_price WHERE trade_date = '{as_of_date}'",
            engine
        )
        codes = sorted(codes_df["code"].astype(str).str.zfill(6).tolist())
        if not codes:
            continue

        # 技术面 (批量加载 ~22s)
        try:
            tech_df = tech.batch_score(codes, as_of_date)
            tech_map = dict(zip(tech_df["code"], tech_df["weighted"]))
        except Exception as e:
            print(f"  [{as_of_date}] tech ERROR: {e}", flush=True)
            tech_map = {}

        # 基本面 (~0.3s)
        try:
            fundam_df = fundam.batch_score(codes)
            fundam_map = dict(zip(fundam_df["code"], fundam_df["weighted"]))
        except Exception as e:
            print(f"  [{as_of_date}] fundam ERROR: {e}", flush=True)
            fundam_map = {}

        # 资金面 (~7s)
        try:
            fundflow_df = fundflow.batch_score(codes, as_of_date)
            fundflow_map = dict(zip(fundflow_df["code"], fundflow_df["weighted"]))
        except Exception as e:
            print(f"  [{as_of_date}] fundflow ERROR: {e}", flush=True)
            fundflow_map = {}

        # 未来收益 (SQL窗口函数, ~1-2s)
        try:
            ret_df = compute_future_returns(engine, as_of_date)
            ret_map = ret_df.set_index("code").to_dict("index")
        except Exception as e:
            print(f"  [{as_of_date}] returns ERROR: {e}", flush=True)
            ret_map = {}

        # 合并
        for code in codes:
            ret_info = ret_map.get(code, {})
            all_rows.append({
                "code": code,
                "as_of_date": as_of_date,
                "tech_weighted": tech_map.get(code, np.nan),
                "fundam_weighted": fundam_map.get(code, np.nan),
                "fund_weighted": fundflow_map.get(code, np.nan),
                "ret_20d": ret_info.get("ret_20d", np.nan),
                "ret_40d": ret_info.get("ret_40d", np.nan),
                "ret_60d": ret_info.get("ret_60d", np.nan),
            })

        t_iter_elapsed = time.time() - t_iter
        elapsed = time.time() - start_time
        rate = (i + 1) / elapsed if elapsed > 0 else 0
        eta = (len(dates) - i - 1) / rate if rate > 0 else 0
        n_fund = sum(1 for v in fundflow_map.values() if not np.isnan(v))

        if (i + 1) % 5 == 0 or i == 0 or i == len(dates) - 1:
            print(f"  [{as_of_date}] {i+1}/{len(dates)} | "
                  f"{len(codes)}只 | 资金:{n_fund} | "
                  f"{t_iter_elapsed:.1f}s | 总ETA:{eta/60:.0f}min", flush=True)

    # 汇总输出
    result = pd.DataFrame(all_rows)
    result["year_month"] = pd.to_datetime(result["as_of_date"]).dt.strftime("%Y-%m")
    result["combined_score"] = result[["tech_weighted", "fundam_weighted", "fund_weighted"]].mean(axis=1)

    total_elapsed = time.time() - start_time
    print(f"\n{'='*60}", flush=True)
    print(f"生成完成! 总耗时: {total_elapsed/60:.1f}min", flush=True)
    print(f"  总行数: {len(result)}", flush=True)
    print(f"  股票数: {result['code'].nunique()}", flush=True)
    print(f"  日期数: {result['as_of_date'].nunique()}", flush=True)
    for col in ["tech_weighted", "fundam_weighted", "fund_weighted"]:
        valid = result[col].notna().sum()
        print(f"  {col}: {valid} ({valid/len(result)*100:.0f}%)", flush=True)

    csv_path = PROJECT_ROOT / "data" / "combined_3d_scores.csv"
    result.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"\n  ✅ 输出: {csv_path} ({len(result)} rows)", flush=True)

    engine.dispose()


if __name__ == "__main__":
    main()
