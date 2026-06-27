"""
导入龙虎榜机构交易数据到 quant.db
数据源: AKShare stock_lhb_detail_em (东方财富龙虎榜)
用法:
    python scripts/import_lhb_institutional.py
"""
import sys, os, time
from pathlib import Path
import akshare as ak
import pandas as pd
import sqlite3

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "database" / "quant.db"

# 游资席位关键词（用于区分机构/游资）
INST_KEYWORDS = ["机构", "外资", "北向", "QFII", "社保", "养老金"]
TOUR_KEYWORDS = ["游资", "营业部", "证券", "私募"]


def fetch_lhb(symbol="全部", max_pages=5):
    """获取龙虎榜明细（多页）"""
    all_rows = []
    try:
        df = ak.stock_lhb_detail_em(symbol=symbol)
        return df
    except Exception as e:
        print(f"❌ 获取失败: {e}")
        return pd.DataFrame()


def classify_seat(name):
    """判断席位类型: 机构/游资/外资"""
    for kw in INST_KEYWORDS:
        if kw in name:
            return "机构"
    for kw in TOUR_KEYWORDS:
        if kw in name:
            return "游资"
    return "其他"


def import_to_db(df, db_path=DB_PATH):
    """导入到 SQLite"""
    conn = sqlite3.connect(str(db_path))
    # 建表
    conn.execute("""
    CREATE TABLE IF NOT EXISTS lhb_institutional (
        code TEXT NOT NULL,
        name TEXT,
        trade_date TEXT NOT NULL,
        topic TEXT,
        reason TEXT,
        net_amount REAL,
        inst_net REAL,
        tour_net REAL,
        north_net REAL,
        buy_inst_names TEXT,
        sell_inst_names TEXT,
        PRIMARY KEY (code, trade_date)
    )
    """)

    # 解析每行的买卖席位
    rows = []
    for _, row in df.iterrows():
        code = str(row.get("代码", "")).zfill(6)
        name = row.get("名称", "")
        date = row.get("上榜日", "")
        topic = row.get("题材", "")
        reason = row.get("原因", "")
        net = float(row.get("净买额", 0) or 0)

        # 买席位分析
        buy_names = []
        sell_names = []
        # 东方财富的龙虎榜数据没有直接的席位列，需要从详情获取
        # 这里先存净买额，后续可以扩展

        rows.append((
            code, name, date, topic, reason,
            net, 0, 0, 0,  # inst_net/tour_net/north_net 缺数据
            ",".join(buy_names),
            ",".join(sell_names)
        ))

    conn.executemany("""
    INSERT OR REPLACE INTO lhb_institutional
    (code, name, trade_date, topic, reason,
     net_amount, inst_net, tour_net, north_net,
     buy_inst_names, sell_inst_names)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM lhb_institutional").fetchone()[0]
    conn.close()
    print(f"✅ 导入完成: {len(rows)} 条, 表共 {count} 条")


if __name__ == "__main__":
    print("📥 龙虎榜机构数据导入")
    print("  数据源: 东方财富龙虎榜 (AKShare)")
    df = fetch_lhb()
    if df.empty:
        print("❌ 无数据")
        sys.exit(1)
    print(f"  获取: {len(df)} 条")
    import_to_db(df)
