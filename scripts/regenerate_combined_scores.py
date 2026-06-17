"""
重新生成 combined_3d_scores.csv — 全量资金面 (优化版)
====================================================
批量加载 + 内存评分，大幅减少数据库查询。

输出: data/combined_3d_scores.csv
"""

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import get_config, get_db_url
from src.scoring.technical_scorer import TechnicalScorer
from src.scoring.fundamental_scorer import FundamentalScorer
from src.scoring.fund_flow_scorer import FundFlowScorer


def get_weekly_dates(engine) -> list:
    """获取所有周五日期作为评分基准日"""
    query = """
        SELECT DISTINCT trade_date FROM daily_price
        WHERE trade_date >= '2024-06-01'
        ORDER BY trade_date
    """
    df = pd.read_sql(query, engine)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["week"] = df["trade_date"].dt.isocalendar().week.astype(int)
    df["year"] = df["trade_date"].dt.isocalendar().year.astype(int)
    weekly = df.groupby(["year", "week"])["trade_date"].max().reset_index()
    return sorted(weekly["trade_date"].dt.strftime("%Y-%m-%d").tolist())


def main():
    print("=" * 60)
    print("重新生成 combined_3d_scores.csv (全量资金面, 批量优化)")
    print("=" * 60)

    config = get_config()
    db_url = get_db_url(config)
    engine = create_engine(db_url, echo=False)

    dates = get_weekly_dates(engine)
    print(f"\n共 {len(dates)} 个周度截面: {dates[0]} ~ {dates[-1]}")

    # 预加载所有未来收益 (一次性查询，大幅减少DB往返)
    print("\n预加载全量收益数据...")
    t0 = time.time()
    all_price = pd.read_sql(
        "SELECT code, trade_date, close FROM daily_price "
        "WHERE trade_date >= '2024-06-01' ORDER BY code, trade_date",
        engine
    )
    all_price["trade_date"] = pd.to_datetime(all_price["trade_date"])
    all_price["code"] = all_price["code"].astype(str).str.zfill(6)

    # 构建每个股票的价格序列字典
    price_by_code = {}
    for code, grp in all_price.groupby("code"):
        grp_sorted = grp.sort_values("trade_date")
        price_by_code[code] = {
            "dates": grp_sorted["trade_date"].tolist(),
            "closes": grp_sorted["close"].values,
        }
    print(f"  预加载完成: {len(price_by_code)} 只股票, {time.time()-t0:.1f}s")

    # 初始化评分器
    print("\n初始化评分器...")
    tech = TechnicalScorer(engine=engine)
    fundam = FundamentalScorer(engine=engine)
    fundflow = FundFlowScorer(engine=engine)

    all_rows = []
    start_time = time.time()

    for i, as_of_date in enumerate(dates):
        iter_start = time.time()
        as_of_dt = pd.Timestamp(as_of_date)

        # 获取当日有交易的股票
        codes_df = pd.read_sql(
            f"SELECT DISTINCT code FROM daily_price WHERE trade_date = '{as_of_date}'",
            engine
        )
        codes = sorted(codes_df["code"].astype(str).str.zfill(6).tolist())
        if not codes:
            continue

        n_stocks = len(codes)

        # 1. 技术面评分 (批量加载优化后 ~22s)
        try:
            tech_df = tech.batch_score(codes, as_of_date)
            tech_map = dict(zip(tech_df["code"], tech_df["weighted"]))
        except Exception as e:
            print(f"  [{as_of_date}] 技术面 ERROR: {e}")
            tech_map = {}

        # 2. 基本面评分 (~0.3s)
        try:
            fundam_df = fundam.batch_score(codes)
            fundam_map = dict(zip(fundam_df["code"], fundam_df["weighted"]))
        except Exception as e:
            print(f"  [{as_of_date}] 基本面 ERROR: {e}")
            fundam_map = {}

        # 3. 资金面评分 (~7s)
        try:
            fundflow_df = fundflow.batch_score(codes, as_of_date)
            fundflow_map = dict(zip(fundflow_df["code"], fundflow_df["weighted"]))
        except Exception as e:
            print(f"  [{as_of_date}] 资金面 ERROR: {e}")
            fundflow_map = {}

        t_score = time.time() - iter_start

        # 4. 从内存计算未来收益
        ret_20_map = {}
        ret_40_map = {}
        ret_60_map = {}
        for code in codes:
            pdata = price_by_code.get(code)
            if pdata is None:
                continue
            dates_list = pdata["dates"]
            closes = pdata["closes"]
            try:
                idx = dates_list.index(as_of_dt)
                base_close = closes[idx]

                # 20日
                if idx + 20 < len(closes):
                    ret_20_map[code] = (closes[idx + 20] - base_close) / base_close * 100
                # 40日
                if idx + 40 < len(closes):
                    ret_40_map[code] = (closes[idx + 40] - base_close) / base_close * 100
                # 60日
                if idx + 60 < len(closes):
                    ret_60_map[code] = (closes[idx + 60] - base_close) / base_close * 100
            except ValueError:
                continue

        # 5. 合并一行
        for code in codes:
            all_rows.append({
                "code": code,
                "as_of_date": as_of_date,
                "tech_weighted": tech_map.get(code, np.nan),
                "fundam_weighted": fundam_map.get(code, np.nan),
                "fund_weighted": fundflow_map.get(code, np.nan),
                "ret_20d": ret_20_map.get(code, np.nan),
                "ret_40d": ret_40_map.get(code, np.nan),
                "ret_60d": ret_60_map.get(code, np.nan),
            })

        # 进度
        n_fund = sum(1 for v in fundflow_map.values() if not np.isnan(v))
        elapsed = time.time() - start_time
        rate = (i + 1) / elapsed if elapsed > 0 else 0
        eta = (len(dates) - i - 1) / rate if rate > 0 else 0
        if (i + 1) % 5 == 0 or i == 0:
            print(f"  [{as_of_date}] {i+1}/{len(dates)} | "
                  f"{n_stocks}只 | 资金面:{n_fund} | "
                  f"评分:{t_score:.1f}s | ETA:{eta/60:.0f}min")

    # 汇总
    result = pd.DataFrame(all_rows)
    result["year_month"] = pd.to_datetime(result["as_of_date"]).dt.strftime("%Y-%m")
    result["combined_score"] = result[["tech_weighted", "fundam_weighted", "fund_weighted"]].mean(axis=1)

    total_elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"生成完成! 总耗时: {total_elapsed/60:.1f}min")
    print(f"  总行数: {len(result)}")
    print(f"  股票数: {result['code'].nunique()}")
    print(f"  日期数: {result['as_of_date'].nunique()}")
    for col in ["tech_weighted", "fundam_weighted", "fund_weighted"]:
        valid = result[col].notna().sum()
        print(f"  {col}: {valid} ({valid/len(result)*100:.0f}%)")

    csv_path = PROJECT_ROOT / "data" / "combined_3d_scores.csv"
    result.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"\n  ✅ 输出: {csv_path} ({len(result)} rows)")

    engine.dispose()


if __name__ == "__main__":
    main()
