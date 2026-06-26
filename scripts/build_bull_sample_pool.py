from src.db.engine import get_engine
#!/usr/bin/env python3
"""
牛股样本池构建 — Stage 0 数据驱动漏斗
==========================================
功能: 从全A股日线数据中筛选出历史上符合条件的"翻倍牛股"样本池
用途: 为 BullWaveCatcher 七维共振模型提供训练/验证样本标签

三阶段漏斗:
  阶段一: 市值门槛 (30亿 ≤ 启动市值 ≤ 500亿) + ST剔除 + 上市天数≥250
  阶段二: 翻倍定义 (60交易日内最大涨幅 ≥ 100%, 且启动前60日非高位)
  阶段三: 剔除游资短炒 (5项条件任一命中即剔除)

运行方式:
  cd quant-trading-system
  python scripts/build_bull_sample_pool.py
"""
import sys
import os
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# 把项目根目录加入 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import get_config, get_db_url



# ============================================================
# 配置参数
# ============================================================
MIN_MARKET_CAP_YI = 30        # 启动市值下限 (亿元)
MAX_MARKET_CAP_YI = 500       # 启动市值上限 (亿元)
MIN_LIST_DAYS = 250           # 最少上市交易日
DOUBLING_WINDOW = 60          # 翻倍观察窗口 (交易日)
DOUBLING_THRESHOLD = 1.0      # 翻倍阈值 (60日最高涨幅 ≥ 100%)
PRE_WINDOW = 60               # 翻倍前观察窗口 (交易日)
PRE_MAX_RETURN = 0.20         # 翻倍前60日涨幅不得 ≥ 20% (非高位启动)

# 阶段三: 游资剔除阈值
MAX_DRAWDOWN_AFTER = 0.35     # 翻倍后30日最高回撤 > 35%
MAX_DAILY_TURNOVER = 20.0     # 翻倍期间日均换手率 > 20%
MAX_ZT_COUNT = 7              # 一字涨停 ≥ 7个
# 条件4 (净利润<0且营收<1亿) → 代理: 市值<30亿
# 条件5 (龙虎榜游资>机构*3) → 跳过, 无数据源


def get_engine():
    """创建数据库引擎"""
    config = get_config()
    db_url = get_db_url(config)
    return get_engine()


def check_database(engine) -> bool:
    """检查数据库是否有数据"""
    try:
        stock_count = pd.read_sql("SELECT COUNT(*) as cnt FROM stock_basic", engine)["cnt"].iloc[0]
        price_count = pd.read_sql("SELECT COUNT(*) as cnt FROM daily_price", engine)["cnt"].iloc[0]
        print(f"📊 数据库状态: stock_basic={stock_count}行, daily_price={price_count}行")
        return stock_count > 0 and price_count > 0
    except Exception as e:
        print(f"❌ 数据库检查失败: {e}")
        return False


def load_stock_basics(engine) -> pd.DataFrame:
    """加载所有股票基本信息"""
    df = pd.read_sql("""
        SELECT code, name, market, list_date
        FROM stock_basic
        ORDER BY code
    """, engine)
    if "list_date" in df.columns:
        df["list_date"] = pd.to_datetime(df["list_date"])
    return df


def build_market_cap_estimation(engine) -> pd.DataFrame:
    """
    按日估算全市场市值

    优先: 用 turnover 精确计算 (mcap = amount / turnover * 100 / 1e8)
    回退: 当 turnover 缺失时, 用 amount 代理估算
           假设日均换手率 3% → mcap ≈ avg_daily_amount / 0.03 * 100 / 1e8
           即 mcap ≈ avg_amount * 3.33 / 1e4 (亿元)
           考虑到 A 股实际分布, 调整系数:
             avg_amount < 5000万 → 小微盘 (<30亿), mcap ≈ avg_amount/1e4 * 20
             avg_amount 5000万-5亿 → 中盘 (30-500亿), mcap ≈ avg_amount/1e4 * 6
             avg_amount > 5亿 → 大盘 (>500亿), mcap ≈ avg_amount/1e4 * 2

    Returns:
        DataFrame: code, trade_date, market_cap_yi
    """
    # 尝试精确计算 (需要 turnover 数据)
    df_turnover = pd.read_sql("""
        SELECT code, trade_date, amount, turnover
        FROM daily_price
        WHERE amount > 0 AND turnover > 0 AND turnover IS NOT NULL
        ORDER BY code, trade_date
    """, engine)

    if not df_turnover.empty:
        df_turnover["trade_date"] = pd.to_datetime(df_turnover["trade_date"])
        df_turnover["market_cap_yi"] = df_turnover["amount"] / df_turnover["turnover"] * 100 / 1e8
        df_turnover = df_turnover[(df_turnover["market_cap_yi"] > 0) & (df_turnover["market_cap_yi"] < 100000)]
        result_exact = df_turnover[["code", "trade_date", "market_cap_yi"]]
    else:
        result_exact = pd.DataFrame(columns=["code", "trade_date", "market_cap_yi"])

    # 回退: 用 amount 代理 (无 turnover 数据时)
    df_amt = pd.read_sql("""
        SELECT code, trade_date, amount
        FROM daily_price
        WHERE amount > 0
        ORDER BY code, trade_date
    """, engine)

    if not df_amt.empty:
        df_amt["trade_date"] = pd.to_datetime(df_amt["trade_date"])
        df_amt["avg_amount_wan"] = df_amt["amount"] / 10000  # 元→万元

        # 按成交额分段估算市值
        def amount_to_mcap(avg_amt_wan):
            if avg_amt_wan < 3000:
                return avg_amt_wan * 20 / 1e4   # 小微盘
            elif avg_amt_wan < 50000:
                return avg_amt_wan * 6 / 1e4    # 中盘
            else:
                return avg_amt_wan * 2 / 1e4    # 大盘

        df_amt["market_cap_yi"] = df_amt["avg_amount_wan"].apply(amount_to_mcap)
        result_proxy = df_amt[["code", "trade_date", "market_cap_yi"]]

        # 优先用精确值, 缺失时用代理
        if not result_exact.empty:
            result = result_exact.copy()
            print(f"   精确市值: {len(result_exact)}行, 代理: {len(result_proxy)}行")
        else:
            result = result_proxy
            print(f"   ⚠️ 无 turnover 数据, 使用 amount 代理估算市值")
    else:
        result = result_exact

    if result.empty:
        return pd.DataFrame(columns=["code", "trade_date", "market_cap_yi"])

    return result


def stage_1_market_cap(
    stock_basics: pd.DataFrame,
    mcap_df: pd.DataFrame,
) -> tuple:
    """
    阶段一: 市值门槛过滤

    筛选条件:
      1. 非ST (name 不含 ST)
      2. 上市 ≥ 250 交易日
      3. 最近交易日市值 ∈ [30亿, 500亿]

    Returns:
        (eligible_codes, rejected_summary)
    """
    print("\n" + "=" * 60)
    print("🔵 阶段一: 市值门槛过滤")
    print("=" * 60)

    # 1. ST 过滤
    stock_basics["is_st"] = stock_basics["name"].str.contains("ST", na=False)
    st_count = stock_basics["is_st"].sum()
    clean = stock_basics[~stock_basics["is_st"]].copy()
    print(f"  ① ST剔除: {st_count}只 → 剩余 {len(clean)}只")

    # 2. 上市天数过滤 (需要数据确认交易日, 这里用日历日近似)
    # 实际交易日 ≈ 日历日 * 5/7 * 0.96 (排除节假日)
    min_calendar_days = int(MIN_LIST_DAYS / (5 / 7 * 0.96))
    reference_date = pd.Timestamp.now()
    clean["days_since_list"] = (reference_date - clean["list_date"]).dt.days
    clean = clean[clean["days_since_list"] >= min_calendar_days]
    print(f"  ② 上市≥{MIN_LIST_DAYS}交易日: 剩余 {len(clean)}只")

    # 3. 市值过滤 — 取每只股票的最新市值
    if mcap_df.empty:
        print("  ⚠️ 无市值数据，跳过市值过滤")
        return set(clean["code"].tolist()), {
            "st_removed": st_count,
            "short_listing_removed": 0,
            "cap_removed": 0,
            "remaining": len(clean),
        }

    latest_mcap = mcap_df.sort_values("trade_date").groupby("code").last().reset_index()
    latest_mcap = latest_mcap[["code", "market_cap_yi"]]

    merged = clean.merge(latest_mcap, on="code", how="left")
    cap_valid = merged["market_cap_yi"].notna()

    too_small = merged["market_cap_yi"] < MIN_MARKET_CAP_YI
    too_big = merged["market_cap_yi"] > MAX_MARKET_CAP_YI

    eligible = merged[
        cap_valid
        & ~too_small
        & ~too_big
    ]

    cap_removed = len(merged) - len(eligible)
    print(f"  ③ 市值∈[{MIN_MARKET_CAP_YI}亿,{MAX_MARKET_CAP_YI}亿]: 剩余 {len(eligible)}只 (剔除 {cap_removed}只)")

    codes = set(eligible["code"].tolist())
    print(f"\n  ✅ 阶段一最终: {len(codes)}只")

    return codes, {
        "st_removed": st_count,
        "short_listing_removed": 0,
        "cap_removed": cap_removed,
        "remaining": len(codes),
    }


def stage_2_doubling(
    codes: set,
    engine,
) -> tuple:
    """
    阶段二: 翻倍定义

    筛选条件:
      1. 60个交易日内最高涨幅 >= 100% (close_max / close_min_prev - 1 >= 1.0)
      2. 翻倍启动前60日涨幅 < 20% (非高位启动)

    Returns:
        (bull_candidates dict, rejected_count)
    """
    print("\n" + "=" * 60)
    print("🟡 阶段二: 翻倍定义筛选")
    print("=" * 60)

    if not codes:
        print("  ⚠️ 阶段一没有合格股票，跳过")
        return {}, 0

    # 加载这些股票的日线数据 (近4年足够)
    codes_str = "','".join(sorted(codes)[:500])  # 限制查询大小
    start_date = (date.today() - timedelta(days=365 * 4)).isoformat()

    df = pd.read_sql(f"""
        SELECT code, trade_date, close, volume, amount, turnover, pct_change
        FROM daily_price
        WHERE code IN ('{codes_str}')
          AND trade_date >= '{start_date}'
        ORDER BY code, trade_date
    """, engine)

    if df.empty:
        print("  ⚠️ 无日线数据，跳过")
        return {}, 0

    df["trade_date"] = pd.to_datetime(df["trade_date"])

    bull_candidates = {}
    total_checked = 0
    total_passed = 0

    for code, group in df.groupby("code"):
        if len(group) < DOUBLING_WINDOW + PRE_WINDOW:
            continue

        total_checked += 1
        group = group.sort_values("trade_date").reset_index(drop=True)
        closes = group["close"].values
        dates = group["trade_date"].values

        # 滑动窗口: 每60个交易日检查是否翻倍
        for i in range(len(closes) - DOUBLING_WINDOW):
            window_closes = closes[i : i + DOUBLING_WINDOW]
            entry_price = window_closes[0]
            max_price = window_closes.max()
            max_return = (max_price / entry_price) - 1

            if max_return < DOUBLING_THRESHOLD:
                continue

            # 检查启动前60日是否高位 (涨幅 < 20%)
            pre_start = max(0, i - PRE_WINDOW)
            if i > 0:
                pre_closes = closes[pre_start:i]
                if len(pre_closes) >= 20:
                    pre_return = (pre_closes[-1] / pre_closes[0]) - 1
                    if pre_return >= PRE_MAX_RETURN:
                        continue  # 高位启动, 跳过

            # 通过! 记录这个翻倍事件
            max_idx = window_closes.argmax()
            doubling_date = dates[i + max_idx]
            start_date_ev = dates[i]

            bull_candidates[f"{code}_{i}"] = {
                "code": code,
                "start_date": str(start_date_ev)[:10],
                "doubling_date": str(doubling_date)[:10],
                "entry_price": float(entry_price),
                "peak_price": float(max_price),
                "max_return": float(max_return),
                "pre_return": float(pre_return) if i > 0 and len(pre_closes) >= 20 else None,
                "window_idx": i,
            }
            total_passed += 1
            break  # 一只股票只取第一个翻倍事件

    print(f"  检查: {total_checked}只 → 符合翻倍定义: {total_passed}只")

    # 去重: 每只股票只保留一个事件
    unique_codes = {}
    for key, ev in bull_candidates.items():
        code = ev["code"]
        if code not in unique_codes:
            unique_codes[code] = ev

    print(f"  去重后: {len(unique_codes)}只")
    print(f"\n  ✅ 阶段二最终: {len(unique_codes)}只")

    return unique_codes, total_checked - len(unique_codes)


def stage_3_filter_retail(
    candidates: dict,
    engine,
) -> tuple:
    """
    阶段三: 剔除游资短炒

    五项条件, 任一命中即剔除:
      1. 翻倍后30日最高点回撤 > 35%
      2. 翻倍期间日均换手率 > 20%
      3. 翻倍期间一字涨停 >= 7个
      4. 净利润<0 且 营收<1亿 → 代理: 市值<30亿 (TODO: 接入财务数据)
      5. 龙虎榜游资买入 > 机构买入*3 → 跳过 (TODO: 接入龙虎榜数据)

    Returns:
        (final_pool, rejected_reasons)
    """
    print("\n" + "=" * 60)
    print("🔴 阶段三: 剔除游资短炒")
    print("=" * 60)

    if not candidates:
        print("  ⚠️ 阶段二没有候选，跳过")
        return {}, {}

    rejected = {cond: 0 for cond in ["回撤>35%", "换手率>20%", "一字板≥7", "代理:市值<30亿", "跳过:龙虎榜"]}

    # 加载所有这些候选的日线数据 (翻倍前后各90天)
    codes = list(set(ev["code"] for ev in candidates.values()))
    codes_str = "','".join(codes)

    df = pd.read_sql(f"""
        SELECT code, trade_date, close, volume, amount, turnover, pct_change
        FROM daily_price
        WHERE code IN ('{codes_str}')
        ORDER BY code, trade_date
    """, engine)

    if df.empty:
        print("  ⚠️ 无日线数据，全部保留")
        return candidates, rejected

    df["trade_date"] = pd.to_datetime(df["trade_date"])

    final_pool = {}
    total_checked = 0

    for key, ev in candidates.items():
        code = ev["code"]
        start_date = ev["start_date"]
        doubling_date = ev["doubling_date"]

        stock_df = df[df["code"] == code].sort_values("trade_date")
        if stock_df.empty:
            continue

        total_checked += 1
        is_rejected = False

        # 翻倍区间数据
        doubling_mask = (stock_df["trade_date"] >= start_date) & (stock_df["trade_date"] <= doubling_date)
        doubling_data = stock_df[doubling_mask]

        # 翻倍后30日数据
        after_mask = (stock_df["trade_date"] > doubling_date) & (
            stock_df["trade_date"] <= str(pd.to_datetime(doubling_date) + pd.Timedelta(days=45))[:10]
        )
        after_data = stock_df[after_mask]

        # ---- 条件1: 翻倍后30日最高回撤 > 35% ----
        if len(after_data) >= 5:
            after_closes = after_data["close"].values
            peak_idx = after_closes.argmax()
            peak = after_closes[peak_idx]
            post_peak_min = after_closes[peak_idx:].min()
            drawdown = (peak - post_peak_min) / peak
            if drawdown > MAX_DRAWDOWN_AFTER:
                rejected["回撤>35%"] += 1
                is_rejected = True

        # ---- 条件2: 翻倍期间日均换手率 > 20% ----
        if len(doubling_data) >= 20:
            turnover_vals = doubling_data["turnover"].dropna()
            if len(turnover_vals) > 0:
                avg_turnover = turnover_vals.mean()
                if avg_turnover > MAX_DAILY_TURNOVER:
                    rejected["换手率>20%"] += 1
                    is_rejected = True
            # 无 turnover 数据时跳过此条件

        # ---- 条件3: 一字涨停 >= 7个 ----
        if len(doubling_data) >= 7:
            zt_count = (doubling_data["pct_change"] >= 9.8).sum()  # 近似一字板
            if zt_count >= MAX_ZT_COUNT:
                rejected["一字板≥7"] += 1
                is_rejected = True

        # ---- 条件4: 市值<30亿 (代理) ----
        # 使用翻倍启动时的成交额/换手率估算
        if len(doubling_data) > 0:
            first_row = doubling_data.iloc[0]
            if first_row["turnover"] and first_row["turnover"] > 0:
                est_mcap = first_row["amount"] / first_row["turnover"] * 100 / 1e8
                if est_mcap < MIN_MARKET_CAP_YI:
                    rejected["代理:市值<30亿"] += 1
                    is_rejected = True

        # ---- 条件5: 龙虎榜 (跳过) ----
        # 无数据源, 所有候选默认通过此项

        if not is_rejected:
            final_pool[key] = ev

    n_rejected = total_checked - len(final_pool)
    print(f"  检查: {total_checked}个翻倍事件")

    for reason, count in rejected.items():
        if count > 0:
            print(f"  剔除 [{reason}]: {count}个")

    print(f"\n  ✅ 阶段三最终: {len(final_pool)}只 (剔除 {n_rejected}个)")

    return final_pool, rejected


def print_summary(stage1_result, stage2_result, stage3_result, rejected_reasons):
    """打印汇总报告"""
    stage1_codes = stage1_result
    stage2_candidates = stage2_result
    stage3_pool = stage3_result

    print("\n")
    print("=" * 60)
    print("📊 牛股样本池构建 — 汇总报告")
    print("=" * 60)

    print(f"""
┌──────────────────────────────────────────────┐
│  阶段一 (市值门槛)     │  通过: {len(stage1_codes):>5}只          │
│  阶段二 (翻倍定义)     │  通过: {len(stage2_candidates):>5}只          │
│  阶段三 (剔除游资)     │  通过: {len(stage3_pool):>5}只          │
├──────────────────────────────────────────────┤
│  最终合格样本池       │  {len(stage3_pool):>5}只          │
└──────────────────────────────────────────────┘
""")

    if rejected_reasons:
        print("剔除原因分布:")
        for reason, count in sorted(rejected_reasons.items(), key=lambda x: -x[1]):
            bar = "█" * min(count, 50)
            print(f"  {reason:<20s}: {count:>4}个 {bar}")

    # 判定
    MIN_POOL_SIZE = 150
    if len(stage3_pool) >= MIN_POOL_SIZE:
        print(f"\n✅ 合格! 样本池 ≥ {MIN_POOL_SIZE}只, 可进入七维评分训练")
    else:
        print(f"\n⚠️ 样本不足! 当前 {len(stage3_pool)}只 < {MIN_POOL_SIZE}只目标")
        print("   建议: 放宽市值范围 / 降低翻倍阈值 / 检查数据覆盖度")

    return len(stage3_pool)


def main():
    """主流程"""
    print("🚀 牛股样本池构建 — build_bull_sample_pool.py")
    print(f"   项目根目录: {PROJECT_ROOT}")

    engine = get_engine()

    # 检查数据库
    if not check_database(engine):
        print("\n❌ 数据库为空! 请先运行数据下载:")
        print("   python -m src.data.downloader --full")
        print("\n   或使用以下命令生成测试数据验证脚本:")
        print("   python scripts/build_bull_sample_pool.py --test")
        return

    # 加载基础数据
    stock_basics = load_stock_basics(engine)
    print(f"   全市场股票: {len(stock_basics)}只")

    mcap_df = build_market_cap_estimation(engine)
    print(f"   市值估算数据: {len(mcap_df)}行")

    # 阶段一
    stage1_codes, _ = stage_1_market_cap(stock_basics, mcap_df)

    # 阶段二
    stage2_candidates, _ = stage_2_doubling(stage1_codes, engine)

    # 阶段三
    stage3_pool, rejected = stage_3_filter_retail(stage2_candidates, engine)

    # 汇总
    print_summary(stage1_codes, stage2_candidates, stage3_pool, rejected)


def generate_test_data():
    """
    生成合成测试数据, 用于验证脚本逻辑
    模拟 200 只股票 × 3 年的日线数据
    """
    import sqlite3
    import random

    random.seed(42)
    np.random.seed(42)

    config = get_config()
    db_path = config["database"]["path"]
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 创建表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock_basic (
            code TEXT PRIMARY KEY, name TEXT, market TEXT, list_date TEXT, industry TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_price (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT, trade_date TEXT,
            open REAL, high REAL, low REAL, close REAL,
            volume INTEGER, amount REAL, pct_change REAL, turnover REAL,
            UNIQUE(code, trade_date)
        )
    """)

    # 清空
    cursor.execute("DELETE FROM stock_basic")
    cursor.execute("DELETE FROM daily_price")

    # 生成股票
    markets = {"SH": "600", "SZ": "000", "BJ": "830"}
    stocks = []
    for i in range(200):
        mkt = random.choice(["SH", "SZ"])
        prefix = markets[mkt]
        code = f"{prefix}{i+100:03d}"
        name = f"测试股{i+1:03d}"
        if i >= 185:  # 部分ST
            name = f"ST测试{i+1:03d}"
        list_date = f"2019-{random.randint(1,12):02d}-{random.randint(1,28):02d}"
        stocks.append((code, name, mkt, list_date, "测试行业"))

    cursor.executemany(
        "INSERT INTO stock_basic (code, name, market, list_date, industry) VALUES (?,?,?,?,?)",
        stocks
    )

    # 生成日线数据
    dates = pd.date_range(start="2022-01-01", end="2025-12-31", freq="B")
    batch = []
    batch_id = 0

    for code, name, mkt, list_date, _ in stocks:
        base_price = random.uniform(3, 30)

        # 设计市值目标: 使得估算市值在 30-500亿 区间
        # 市值 ≈ price * volume / turnover → 需要调整 volume 和 turnover
        # target_mcap 在 30-500亿, 取对数均匀分布
        target_mcap = 10 ** random.uniform(1.5, 2.7)  # 30-500亿

        is_bull = random.random() < 0.30  # 30% 概率是牛股
        bull_start_idx = random.randint(100, len(dates) - 120) if is_bull else -1
        price = base_price

        for idx, d in enumerate(dates):
            if idx == 0:
                daily_return = 0.0
            elif is_bull and bull_start_idx <= idx < bull_start_idx + 60:
                # 翻倍阶段: 确保60日涨幅≥100%
                # 按60日均摊, 每天约1.2% (1.012^60 ≈ 2.04)
                daily_return = np.random.normal(0.012, 0.015)
            else:
                daily_return = np.random.normal(0.0003, 0.020)

            price = price * (1 + daily_return)
            price = max(price, 1.0)

            # 控制换手率和成交量使市值在目标范围
            # 市值 = amount / turnover * 100 / 1e8
            # amount = price * volume
            # 所以 volume = (target_mcap * 1e8 * turnover / 100) / price
            target_turnover = np.random.uniform(0.5, 8.0)

            # 部分股票高换手率 (>20%, 游资特征)
            if is_bull and bull_start_idx <= idx < bull_start_idx + 60:
                if random.random() < 0.25:
                    target_turnover = np.random.uniform(20, 45)

            target_volume = (target_mcap * 1e8 * target_turnover / 100) / price
            volume = int(np.random.lognormal(np.log(target_volume), 0.3))
            volume = max(volume, 10000)

            amount = price * volume
            turnover = (amount / price) / (target_mcap * 1e8 / 100) * 100
            turnover = max(turnover, 0.01)

            pct_change = daily_return * 100

            high = price * (1 + abs(np.random.normal(0, 0.015)))
            low = price * (1 - abs(np.random.normal(0, 0.015)))
            open_p = low + np.random.random() * (high - low)

            batch.append((
                code, d.strftime("%Y-%m-%d"),
                round(open_p, 2), round(high, 2), round(low, 2), round(price, 2),
                volume, round(amount, 2), round(pct_change, 2), round(turnover, 2)
            ))

            if len(batch) >= 5000:
                cursor.executemany(
                    "INSERT OR IGNORE INTO daily_price (code, trade_date, open, high, low, close, volume, amount, pct_change, turnover) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    batch
                )
                batch = []
                batch_id += 1
                print(f"\r  写入批次 {batch_id}...", end="", flush=True)

    if batch:
        cursor.executemany(
            "INSERT OR IGNORE INTO daily_price (code, trade_date, open, high, low, close, volume, amount, pct_change, turnover) VALUES (?,?,?,?,?,?,?,?,?,?)",
            batch
        )

    conn.commit()
    conn.close()

    stock_count = len(stocks)
    price_count = len(dates) * len(stocks)
    print(f"\n✅ 测试数据已生成: {stock_count}只股票 × {len(dates)}个交易日 = {price_count}行")
    print(f"   数据库: {db_path}")
    print(f"   其中约25%为牛股模式 (25只)")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="牛股样本池构建")
    parser.add_argument("--test", action="store_true", help="生成测试数据后运行")
    args = parser.parse_args()

    if args.test:
        print("🔧 生成合成测试数据...")
        generate_test_data()

    main()
