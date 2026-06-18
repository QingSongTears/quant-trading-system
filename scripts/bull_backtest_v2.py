"""
牛股样本批量回测 v2 — 多参数并行对比
对 bull_sample_pool.json 中的194只牛股，同时跑多组参数，
一次性输出对比结果，找出最优参数组合。

参数组合:
  threshold: 5/6/7/8/9  (评分入场阈值)
  stop_loss: -5/-8/-10/-15  (止损线)
  take_profit: 20/30/50  (止盈线, 0=不止盈)
"""

import sys, json, time, itertools
sys.path.insert(0, '.')

from pathlib import Path
import pandas as pd
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

PROJECT_ROOT = Path(__file__).parent.parent
API_BASE = "http://localhost:8081"

# ── 参数网格 ──
THRESHOLDS  = [5, 6, 7, 8, 9]
STOP_LOSSES  = [-5, -8, -10, -15]
TAKE_PROFITS = [0, 20, 30, 50]

# 为了减少组合数，可以用 "fast" 模式
MODE = "fast"   # "fast" = 各取2个值; "full" = 全组合

if MODE == "fast":
    THRESHOLDS   = [6, 7]
    STOP_LOSSES  = [-8, -15]
    TAKE_PROFITS = [0, 30]

PARAM_COMBOS = list(itertools.product(THRESHOLDS, STOP_LOSSES, TAKE_PROFITS))
print(f"参数组合数: {len(PARAM_COMBOS)}  (mode={MODE})")


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


def backtest_stock(code, params):
    """调用 API 回测单只股票，返回 summary dict"""
    param_str = "&".join([f"param_{k}={v}" for k, v in params.items()])
    url = f"{API_BASE}/api/stock/{code}/backtest?strategy=bull_wave&{param_str}"
    try:
        r = requests.get(url, timeout=60)
        if r.status_code == 200:
            result = r.json()
            s = result.get("summary", {})
            trades = result.get("trades", [])
            return {
                "totalReturn":   s.get("totalReturn", 0) or 0,
                "bhReturn":     s.get("bhReturn", 0) or 0,
                "totalTrades":   s.get("totalTrades", 0) or 0,
                "winRate":       s.get("winRate", 0) or 0,
                "maxDrawdown":  s.get("maxDrawdown", 0) or 0,
                "sharpe":        s.get("sharpe", 0) or 0,
                "profitFactor":  s.get("profitFactor", 0) or 0,
            }
        else:
            return None
    except Exception as e:
        return None


def run_single(code, name, params):
    """对单只股票跑一组参数"""
    result = backtest_stock(code, params)
    if result is None:
        return None
    return {
        "code": code,
        "name": name,
        "threshold":   params.get("threshold", 7),
        "stop_loss":  params.get("stop_loss", -8),
        "take_profit": params.get("take_profit", 0),
        **result
    }


def main():
    flask_proc = ensure_flask()

    with open(PROJECT_ROOT / "data" / "bull_sample_pool.json") as f:
        bulls = json.load(f)

    print(f"\n📊 开始回测 {len(bulls)} 只牛股 × {len(PARAM_COMBOS)} 组参数")
    print(f"   总任务数: {len(bulls)} × {len(PARAM_COMBOS)} = {len(bulls)*len(PARAM_COMBOS)}\n")

    # 扁平任务列表
    tasks = []
    for stock in bulls:
        code = stock["code"].zfill(6)
        name = stock.get("name", code)
        for th, sl, tp in PARAM_COMBOS:
            params = {"threshold": th, "stop_loss": sl, "take_profit": tp}
            tasks.append((code, name, params))

    results = []
    n = len(tasks)
    start = time.time()

    # 多线程跑（Flask 是多线程的，可以并行）
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(run_single, code, name, params): i
                   for i, (code, name, params) in enumerate(tasks)}

        done = 0
        for future in as_completed(futures):
            done += 1
            result = future.result()
            if result:
                results.append(result)

            if done % 50 == 0:
                elapsed = time.time() - start
                rate = done / elapsed * 60 if elapsed > 0 else 0
                eta = (n - done) / (done / elapsed) if elapsed > 0 else 9999
                print(f"  ... {done}/{n}  ({done/n*100:.0f}%)  "
                      f"成功:{len(results)}  速度:{rate:.0f}任务/分钟  "
                      f"预计剩余:{eta/60:.1f}分钟")

    elapsed = time.time() - start
    print(f"\n✅ 回测完成: {len(results)}/{n} 成功, 耗时 {elapsed/60:.1f} 分钟")

    if not results:
        print("❌ 没有成功结果，请检查 Flask 是否正常运行")
        return

    # ── 汇总分析 ──
    df = pd.DataFrame(results)

    print(f"\n{'='*70}")
    print("参数组合对比（按平均收益排序）")
    print(f"{'='*70}")

    # 按参数组合分组统计
    grp = df.groupby(["threshold", "stop_loss", "take_profit"]).agg(
        avg_return     = ("totalReturn",   "mean"),
        median_return = ("totalReturn",   "median"),
        avg_bh        = ("bhReturn",     "mean"),
        win_rate      = ("winRate",     "mean"),
        avg_trades    = ("totalTrades",  "mean"),
        hit_rate      = ("totalReturn",   lambda x: (x > 0).sum() / len(x) * 100),
        count         = ("totalReturn",   "count"),
    ).round(1).sort_values("avg_return", ascending=False)

    print(grp.to_string())
    print()

    # 最优组合
    best = grp.iloc[0]
    best_idx = grp.index[0]
    print(f"🏆 最优组合: threshold={best_idx[0]}, stop_loss={best_idx[1]}%, take_profit={best_idx[2]}%")
    print(f"   平均收益: {best['avg_return']:+.1f}%")
    print(f"   胜率:     {best['win_rate']:.0f}%")
    print(f"   命中率:   {best['hit_rate']:.0f}%")
    print()

    # 保存详细结果
    out_detail = PROJECT_ROOT / "data" / "bull_backtest_detail.csv"
    df.to_csv(out_detail, index=False, encoding="utf-8-sig")
    print(f"💾 详细结果已保存: {out_detail}")

    out_summary = PROJECT_ROOT / "data" / "bull_backtest_summary.csv"
    grp.to_csv(out_summary, encoding="utf-8-sig")
    print(f"💾 汇总结果已保存: {out_summary}")

    if flask_proc:
        flask_proc.terminate()


if __name__ == "__main__":
    main()
