"""
预计算全部8维评分并导出JSON，供HTML参数面板使用。
输出: data/all_7d_scores.json (由HTML面板加载)
8维: 技术面、基本面、资金面、机构面、龙虎榜机构、情绪面、新闻面、筹码面
"""

import sys, json
from pathlib import Path
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.db.engine import get_engine
from src.scoring import ScorerRegistry


def main():
    engine = get_engine()
    df = pd.read_csv(PROJECT_ROOT / "data" / "combined_3d_scores.csv")
    df["sample_key"] = df["code"].astype(str) + "_" + df["year_month"].astype(str)
    df_sample = df.drop_duplicates(subset=["sample_key"]).copy()
    print(f"采样: {len(df_sample)} 行")

    # 已有3维
    for col, fill in [("tech_weighted", 8.6), ("fundam_weighted", 7.6), ("fund_weighted", 2.2)]:
        df_sample[col] = df_sample[col].fillna(fill)

    # 收益
    for c in ["ret_20d", "ret_40d", "ret_60d"]:
        df_sample[c] = pd.to_numeric(df_sample[c], errors="coerce")

    df_sample["code"] = df_sample["code"].astype(str).str.zfill(6)
    unique_dates = sorted(df_sample["as_of_date"].unique())

    # 加载后5维 (机构面、情绪面、新闻面、筹码面、龙虎榜机构)
    extra_dims = [
        ("institutional", "机构面"),
        ("sentiment", "情绪面"),
        ("news_event", "新闻面"),
        ("chip", "筹码面"),
        ("lh_institutional", "龙虎榜机构面"),
    ]

    for dim_name, label in extra_dims:
        col_name = f"{dim_name}_weighted"
        print(f"\n{label} 评分...")
        
        # 龙虎榜机构面：直接从数据库聚合，不走batch_score
        if dim_name == "lh_institutional":
            # 对每个股票，取最近龙虎榜净买入汇总
            lhb_df = pd.read_sql("""
                SELECT code, SUM(inst_net) as total_inst_net
                FROM lhb_institutional
                GROUP BY code
            """, engine)
            lhb_df["code"] = lhb_df["code"].astype(str).str.zfill(6)
            # 将 total_inst_net 转换为 0-20 分制评分
            # 正净买入 → 高分，负净买入 → 低分
            max_abs = lhb_df["total_inst_net"].abs().max()
            if max_abs > 0:
                lhb_df["lh_institutional_weighted"] = (
                    (lhb_df["total_inst_net"] / max_abs * 10 + 10).clip(0, 20)
                )
            else:
                lhb_df["lh_institutional_weighted"] = 10.0
            lhb_df = lhb_df[["code", "lh_institutional_weighted"]]
            df_sample = df_sample.merge(lhb_df, on="code", how="left")
            df_sample["lh_institutional_weighted"] = df_sample["lh_institutional_weighted"].fillna(10.0)
            print(f"  完成: {len(lhb_df)} 条")
            continue
        
        scorer = ScorerRegistry.get(dim_name, engine=engine)
        all_scores = []

        for i, dt in enumerate(unique_dates):
            date_codes = df_sample[df_sample["as_of_date"] == dt]["code"].tolist()
            try:
                if dim_name in ("chip",):
                    batch_df = scorer.batch_score(date_codes)
                else:
                    batch_df = scorer.batch_score(date_codes, dt)
                if not batch_df.empty:
                    batch_df["code"] = batch_df["code"].astype(str)
                    batch_df["as_of_date"] = dt
                    all_scores.append(batch_df[["code", "as_of_date", "weighted"]])
            except:
                pass
            if (i + 1) % 30 == 0:
                print(f"  ... {i+1}/{len(unique_dates)}")

        if all_scores:
            dim_df = pd.concat(all_scores, ignore_index=True)
            dim_df = dim_df.rename(columns={"weighted": col_name})
            df_sample = df_sample.merge(dim_df, on=["code", "as_of_date"], how="left")
            print(f"  完成: {len(dim_df)} 条")
        df_sample[col_name] = df_sample[col_name].fillna(df_sample[col_name].median() if dim_name != "chip" else 9.3)

    # 只保留需要的列，转为JSON
    keep_cols = ["code", "as_of_date", "year_month",
                 "tech_weighted", "fundam_weighted", "fund_weighted",
                 "institutional_weighted", "lh_institutional_weighted",
                 "sentiment_weighted", "news_event_weighted", "chip_weighted",
                 "ret_20d", "ret_40d", "ret_60d"]

    export = df_sample[keep_cols].copy()
    for c in keep_cols:
        if c in export.columns:
            export[c] = export[c].fillna(0)

    # 转list of dicts
    records = export.to_dict(orient="records")
    # 处理NaN
    for r in records:
        for k, v in r.items():
            if isinstance(v, float) and np.isnan(v):
                r[k] = None

    out_path = PROJECT_ROOT / "data" / "all_7d_scores.json"
    with open(out_path, "w") as f:
        json.dump(records, f, ensure_ascii=False)
    print(f"\n✅ 导出: {out_path} ({len(records)} rows)")

    engine.dispose()


if __name__ == "__main__":
    main()
