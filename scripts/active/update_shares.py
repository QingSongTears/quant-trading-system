"""
获取流通股本数据 (shares) 并存入数据库
=====================================
数据源: WeStock Data (腾讯自选股行情)
字段: regCapital = 注册资本(万元) ≈ 总股本(万股, 面值1元)
"""

import json
import sqlite3
import subprocess
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "database" / "quant.db"


def fetch_shares(code: str) -> dict | None:
    """从 WeStock Data 获取总股本数据
    
    使用 profile 命令获取 regCapital (注册资本, 万元)
    A股面值1元, regCapital(万元) = 总股本(万股)
    
    Args:
        code: 股票代码 (如 '000001')
    Returns:
        {code, reg_capital_wan, total_shares_wan} 或 None
    """
    # 转换为 WeStock 格式
    if code.startswith("6"):
        wscode = "sh" + code
    elif code.startswith("0") or code.startswith("3"):
        wscode = "sz" + code
    elif code.startswith("8") or code.startswith("4"):
        wscode = "bj" + code
    else:
        return None
    
    try:
        result = subprocess.run(
            ["npx", "-y", "westock-data-clawhub@1.0.4", "profile", wscode],
            capture_output=True, text=True, timeout=20,
            env={**__import__('os').environ, "NODE_OPTIONS": ""}
        )
        if result.returncode != 0:
            return None
        
        # 解析 Markdown 表格: lines[0]=表头, lines[1]=分隔线, lines[2]=数据
        lines = result.stdout.strip().split("\n")
        if len(lines) < 3:
            return None
        
        header_line = lines[0]
        data_line = lines[2]
        
        headers = [h.strip() for h in header_line.split("|")[1:-1]]
        values = [v.strip() for v in data_line.split("|")[1:-1]]
        
        data = dict(zip(headers, values))
        
        reg_capital_str = data.get("regCapital", "")
        if not reg_capital_str:
            return None
        
        reg_capital_wan = float(reg_capital_str.replace(",", ""))
        
        return {
            "code": code,
            "reg_capital_wan": reg_capital_wan,       # 注册资本(万元)
            "total_shares_wan": reg_capital_wan,       # 总股本(万股, 面值1元)
            "name": data.get("name", ""),
        }
    except Exception as e:
        print(f"  [WARN] {code}: {e}")
    return None


def update_db():
    """批量获取并写入 DB"""
    conn = sqlite3.connect(str(DB_PATH))
    
    # 获取已有 shares 数据的股票（跳过已有数据的）
    existing = set()
    try:
        rows = conn.execute(
            "SELECT code FROM stock_profile WHERE circulating_shares IS NOT NULL AND circulating_shares > 0"
        ).fetchall()
        existing = set(r[0] for r in rows)
    except:
        pass
    
    # 获取所有待处理股票代码
    codes = conn.execute(
        "SELECT DISTINCT code FROM daily_price UNION "
        "SELECT DISTINCT code FROM prediction_record UNION "
        "SELECT DISTINCT code FROM stock_profile"
    ).fetchall()
    codes = sorted(set(c[0] for c in codes if c[0] and len(c[0]) >= 6))
    
    # 跳过已有数据的
    need_update = [c for c in codes if c not in existing]
    print(f"总计 {len(codes)} 只, 已有 {len(existing)} 只, 待处理 {len(need_update)} 只")

    # 确保 stock_profile 有 shares 列
    existing_cols = [c[1] for c in conn.execute("PRAGMA table_info(stock_profile)").fetchall()]
    if "circulating_shares" not in existing_cols:
        conn.execute("ALTER TABLE stock_profile ADD COLUMN circulating_shares REAL")
        conn.execute("ALTER TABLE stock_profile ADD COLUMN total_shares REAL")
        print("✅ 已添加 shares 列到 stock_profile")

    success = 0
    fail = 0
    for i, code in enumerate(need_update):
        result = fetch_shares(code)
        if result and result.get("total_shares_wan"):
            # 总股本(万股) 转为 股数(股) 存储
            shares_count = result["total_shares_wan"] * 10000  # 万股→股
            conn.execute(
                """UPDATE stock_profile SET 
                   circulating_shares = ?, total_shares = ?
                   WHERE code = ?""",
                (shares_count, shares_count, code)
            )
            success += 1
        else:
            fail += 1
        
        if (i + 1) % 30 == 0:
            conn.commit()
            print(f"  进度: {i+1}/{len(need_update)}, 成功: {success}, 失败: {fail}")
        
        time.sleep(0.1)  # 限速
    
    conn.commit()
    conn.close()
    print(f"\n✅ 完成! 成功更新 {success}/{len(need_update)} 只 (失败 {fail})")


if __name__ == "__main__":
    # 测试单只
    print("▶ 测试 sz000001...")
    test = fetch_shares("000001")
    print(f"  结果: {test}")
    
    print("▶ 测试 sh600519...")
    test2 = fetch_shares("600519")
    print(f"  结果: {test2}")
    
    if test and test.get("total_shares_wan"):
        print("\n▶ 开始批量更新...")
        update_db()
    else:
        print("\n❌ API 测试失败，请检查 WeStock Data 是否可用")
