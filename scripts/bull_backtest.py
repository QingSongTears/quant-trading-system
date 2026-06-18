"""
牛股样本批量回测 — bull_wave 策略
对 bull_sample_pool.json 中的194只牛股逐一跑回测，
统计收益率、命中率、捕获翻倍情况。
"""
import sys, json, time
sys.path.insert(0, '.')

from pathlib import Path
import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).parent.parent
API_BASE = "http://localhost:8081"

# ── 启动 Flask（如果未运行）──
def ensure_flask():
    try:
        r = requests.get(f"{API_BASE}/api/status", timeout=3)
        print(f"✅ Flask 已运行: {r.json().get('loaded')} 条")
        return None
    except:
        print("🚀 启动 Flask 服务...")
        import subprocess
        proc = subprocess.Popen(
            [sys.executable, "scripts/param_server.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(PROJECT_ROOT)
        )
        for _ in range(20):
            time.sleep(2)
            try:
                r = requests.get(f"{API_BASE}/api/status", timeout=3)
                print(f"✅ Flask 启动成功: {r.json().get('loaded')} 条")
                return proc
            except:
                pass
        print("❌ Flask 启动失败")
        return None

def backtest_stock(code, strategy="bull_wave", params=None):
    """调用 API 回测单只股票"""
    if params is None:
        params = {"threshold": 7, "stop_loss": -8}
    param_str = "&".join([f"param_{k}={v}" for k, v in params.items()])
    url = f"{API_BASE}/api/stock/{code}/backtest?strategy={strategy}&{param_str}"
    try:
        r = requests.get(url, timeout=60)
        if r.status_code == 200:
            return r.json()
        else:
            return {"error": r.text[:100]}
    except Exception as e:
        return {"error": str(e)}

def main():
    flask_proc = ensure_flask()
    
    with open(PROJECT_ROOT / "data" / "bull_sample_pool.json") as f:
        bulls = json.load(f)
    
    print(f"\n📊 开始回测 {len(bulls)} 只牛股，策略=bull_wave\n")
    
    results = []
    hit = 0  # 捕获到上涨
    miss = 0
    error = 0
    
    for i, stock in enumerate(bulls):
        code = stock["code"].zfill(6)
        name = stock.get("name", code)
        
        result = backtest_stock(code, "bull_wave", {"threshold": 7, "stop_loss": -8})
        
        if "error" in result:
            error += 1
            print(f"  [{i+1:3d}/{len(bulls)}] {code} {name:6s} ❌ {result['error'][:60]}")
            continue
        
        summary = result.get("summary", {})
        total_return = summary.get("totalReturn", 0) or 0
        win_rate = summary.get("winRate", 0) or 0
        total_trades = summary.get("totalTrades", 0) or 0
        bh_return = summary.get("bhReturn", 0) or 0
        
        # 判断是否"捕获"：策略收益 > 0 且跑赢买入持有
        captured = (total_return > 0) or (total_return > bh_return - 5)
        
        status = "✅" if captured else "⚠️"
        results.append({
            "code": code,
            "name": name,
            "return": total_return,
            "bh_return": bh_return,
            "trades": total_trades,
            "win_rate": win_rate,
            "captured": captured,
        })
        
        if captured:
            hit += 1
        else:
            miss += 1
        
        print(f"  [{i+1:3d}/{len(bulls)}] {code} {name:6s} {status} 策略:{total_return:+.1f}%  持有:{bh_return:+.1f}%  交易:{total_trades}笔  胜率:{win_rate:.0f}%")
        
        # 每20只保存一次
        if (i + 1) % 20 == 0:
            pd.DataFrame(results).to_csv(PROJECT_ROOT / "data" / "bull_backtest_result.csv", index=False, encoding="utf-8-sig")
            print(f"  💾 已保存 {len(results)} 条结果\n")
    
    # 最终结果
    df = pd.DataFrame(results)
    out_path = PROJECT_ROOT / "data" / "bull_backtest_result.csv"
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    
    print(f"\n{'='*60}")
    print(f"回测完成: {len(results)} 只")
    print(f"  捕获上涨: {hit} 只 ({hit/(hit+miss)*100:.0f}%)" if hit+miss > 0 else "  捕获上涨: N/A")
    print(f"  未捕获:   {miss} 只")
    print(f"  错误/跳过: {error} 只")
    if len(results) > 0:
        print(f"\n  平均策略收益: {df['return'].mean():+.1f}%")
        print(f"  平均持有收益: {df['bh_return'].mean():+.1f}%")
        print(f"  平均交易笔数: {df['trades'].mean():.1f} 笔")
        print(f"  平均胜率:     {df['win_rate'].mean():.0f}%")
    print(f"\n💾 结果已保存: {out_path}")
    
    if flask_proc:
        flask_proc.terminate()

if __name__ == "__main__":
    main()
