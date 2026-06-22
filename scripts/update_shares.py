"""
获取流通股本数据 (shares) 并存入数据库
=====================================
数据源: 东方财富 API (push2.eastmoney.com)
字段: f84=总股本, f85=流通股本
"""

import json
import sqlite3
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "database" / "quant.db"
DB_SHARES_PATH = PROJECT_ROOT / "database" / "shares_data.json"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def fetch_shares(code: str, market: int = 0) -> dict | None:
    """从东方财富获取流通股本数据
    
    Args:
        code: 股票代码 (如 '000001')
        market: 0=深市, 1=沪市
    Returns:
        {code, total_shares, circulating_shares, mcap} 或 None
    """
    url = (
        f"https://push2.eastmoney.com/api/qt/stock/get"
        f"?secid={market}.{code}"
        f"&fields=f57,f58,f84,f85,f86"
    )
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=10)
        data = resp.json()
        if data.get("data") and data["data"].get("f84"):
            d = data["data"]
            return {
                "code": code,
                "name": d.get("f58", ""),
                "total_shares": d["f84"],       # 总股本(股)
                "circulating_shares": d["f85"],  # 流通股本(股)
                "total_mcap": d.get("f86", 0),   # 总市值
            }
    except Exception as e:
        print(f"  [WARN] {code}: {e}")
    return None


def update_db():
    """批量获取并写入 DB"""
    conn = sqlite3.connect(str(DB_PATH))
    
    # 获取所有股票代码
    codes = conn.execute(
        "SELECT DISTINCT code FROM daily_price UNION "
        "SELECT DISTINCT code FROM prediction_record UNION "
        "SELECT DISTINCT code FROM stock_profile"
    ).fetchall()
    codes = sorted(set(c[0] for c in codes if c[0] and len(c[0]) >= 6))
    print(f"共 {len(codes)} 只股票待处理")

    # 确保 stock_profile 有 shares 列
    existing_cols = [c[1] for c in conn.execute("PRAGMA table_info(stock_profile)").fetchall()]
    if "circulating_shares" not in existing_cols:
        conn.execute("ALTER TABLE stock_profile ADD COLUMN circulating_shares REAL")
        conn.execute("ALTER TABLE stock_profile ADD COLUMN total_shares REAL")
        print("✅ 已添加 shares 列到 stock_profile")

    success = 0
    for i, code in enumerate(codes):
        # 判断市场
        market = 1 if code.startswith("6") else 0
        
        result = fetch_shares(code, market)
        if result and result.get("circulating_shares"):
            conn.execute(
                """UPDATE stock_profile SET 
                   circulating_shares = ?, total_shares = ?
                   WHERE code = ?""",
                (result["circulating_shares"], result["total_shares"], code)
            )
            success += 1
        
        if (i + 1) % 50 == 0:
            conn.commit()
            print(f"  进度: {i+1}/{len(codes)}, 成功: {success}")
        
        time.sleep(0.15)  # 限速
    
    conn.commit()
    conn.close()
    print(f"\n✅ 完成! 成功更新 {success}/{len(codes)} 只股票")


if __name__ == "__main__":
    # 测试单只
    test = fetch_shares("000001", 0)
    print(f"测试 000001: {test}")
    test2 = fetch_shares("600519", 1)
    print(f"测试 600519: {test2}")
    
    if test and test.get("circulating_shares"):
        update_db()
    else:
        print("API 测试失败，请检查网络")
