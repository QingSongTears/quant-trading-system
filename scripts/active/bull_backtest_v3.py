"""
牛股批量回测 v3 — 串行重试版
解决 v2 并行请求大量失败的问题。
策略: bull_wave，对194只牛股跑多组参数，串行+自动重试。
"""

import sys, json, time, requests
from pathlib import Path
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
API_BASE = "http://localhost:8081"

# ── 参数组合 ──
PARAMS_LIST = [
    {"threshold": 5,  "stop_loss": -8,  "take_profit": 0},
    {"threshold": 6,  "stop_loss": -8,  "take_profit": 0},
    {"threshold": 6,  "stop_loss": -15, "take_profit": 0},
    {"threshold": 7,  "stop_loss": -8,  "take_profit": 0},
    {"threshold": 5,  "stop_loss": -8,  "take_profit": 30},
    {"threshold": 6,  "stop_loss": -8,  "take_profit": 30},
]

def ensure_flask():
    for _ in range(30):
        try:
            r = requests.get(f"{API_BASE}/api/status", timeout=3)
            print(f"✅ Flask 已运行: {r.json().get('loaded')} 条")
            return
        except:
            print("🚀 启动 Flask...")
            import subprocess
            subprocess.Popen(
                [sys.executable, "scripts/param_server.py"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                cwd=str(PROJECT_ROOT)
            )
            time.sleep(3)
    raise RuntimeError("Flask 启动失败")


def backtest(code, params, max_retry=3):
    """带重试的回测调用"""
    param_str = "&".join(f"param_{k}={v}" for k, v in params.items())
    url = f"{API_BASE}/api/stock/{code}/backtest?strategy=bull_wave&{param_str}"
    for attempt in range(max_retry):
        try:
            r = requests.get(url, timeout=120)
            if r.status_code == 200:
                result = r.json()
                s = result.get("summary", {})
                return {
                    "code":        code,
                    "threshold":   params["threshold"],
                    "stop_loss":   params["stop_loss"],
                    "take_profit": params["take_profit"],
                    "totalReturn":  s.get("totalReturn",  0) or 0,
                    "bhReturn":     s.get("bhReturn",    0) or 0,
                    "totalTrades":  s.get("totalTrades",  0) or 0,
                    "winRate":       s.get("winRate",      0) or 0,
                    "maxDrawdown":  s.get("maxDrawdown", 0) or 0,
                    "sharpe":       s.get("sharpe",       0) or 0,
                    "profitFactor":  s.get("profitFactor", 0) or 0,
                }
            else:
                print(f"  ⚠️ HTTP {r.status_code}: {r.text[:80]}")
        except Exception as e:
            if attempt == max_retry - 1:
                print(f"  ❌ {code} 失败: {e}")
            time.sleep(1)
    return None


def main():
    ensure_flask()

    with open(PROJECT_ROOT / "data" / "bull_sample_pool.json") as f:
        bulls = json.load(f)

    print(f"\n📊 回测 {len(bulls)} 只 × {len(PARAMS_LIST)} 组 = {len(bulls)*len(PARAMS_LIST)} 任务\n")

    results   = []
    total     = len(bulls) * len(PARAMS_LIST)
    done      = 0
    start     = time.time()

    for i, stock in enumerate(bulls):
        code = stock["code"].zfill(6)
        name = stock.get("name", code)

        for j, params in enumerate(PARAMS_LIST):
            done += 1
            pct = done / total * 100

            result = backtest(code, params)
            if result:
                results.append(result)
                tag = "✅" if result["totalReturn"] > 0 else "⚠️"
                print(f"  [{done:4d}/{total}] {tag} {code} {name:6s} "
                      f"th={params['threshold']} sl={params['stop_loss']} tp={params['take_profit']}  "
                      f"收益:{result['totalReturn']:+.1f}%  持有:{result['bhReturn']:+.1f}%  "
                      f"交易:{result['totalTrades']}笔  胜率:{result['winRate']:.0f}%")
            else:
                print(f"  [{done:4d}/{total}] ❌ {code} {name:6s} "
                      f"th={params['threshold']} sl={params['stop_loss']}  — 失败")

            # 每10个任务保存一次
            if done % 10 == 0:
                df = pd.DataFrame(results)
                df.to_csv(PROJECT_ROOT / "data" / "bull_backtest_v3.csv",
                           index=False, encoding="utf-8-sig")
                elapsed = time.time() - start
                speed  = done / elapsed * 60 if elapsed > 0 else 0
                eta    = (total - done) / (done / elapsed) if elapsed > 0 else 9999
                print(f"  💾 已保存 {len(results)} 条  "
                      f"速度:{speed:.0f}任务/分钟  "
                      f"预计剩余:{eta/60:.1f}分钟\n")

    # 最终保存
    df = pd.DataFrame(results)
    out = PROJECT_ROOT / "data" / "bull_backtest_v3.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")

    print(f"\n{'='*60}")
    print(f"回测完成: {len(results)}/{total} 成功")
    print(f"耗时: {(time.time()-start)/60:.1f} 分钟")

    # 参数组合对比
    grp = df.groupby(["threshold", "stop_loss", "take_profit"]).agg(
        平均收益   = ("totalReturn",  "mean"),
        收益中位数 = ("totalReturn",  "median"),
        命中率     = ("totalReturn", lambda x: (x > 0).sum() / len(x) * 100),
        平均胜率   = ("winRate",     "mean"),
        平均交易   = ("totalTrades",  "mean"),
    ).round(1).sort_values("平均收益", ascending=False)

    print(f"\n{'='*60}")
    print("参数组合对比（按平均收益排序）")
    print(grp.to_string())
    print(f"\n💾 结果已保存: {out}")


if __name__ == "__main__":
    main()
