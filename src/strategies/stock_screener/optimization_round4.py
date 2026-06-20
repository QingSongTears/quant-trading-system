"""
参数扫描 — v5_hybrid 放宽超卖阈值
=====================================
扫描 v5_hybrid 策略的核心入场参数，找到夏普比率最优组合。

扫描维度 (共 6x5x5x2 = 300 种):
  1. MAX_RSI_14: [32, 34, 36, 38, 40, 42]
  2. MAX_BB_POSITION: [0.10, 0.12, 0.15, 0.18, 0.20]
  3. MAX_DRAWDOWN_60D: [-8, -10, -12, -14, -16]
  4. TREND_CHECKS_MIN: [1, 2]

输出: output/param_scan_v5.csv (按夏普排序，取前20)
"""
import sys
import time
import pandas as pd
import numpy as np
from typing import Dict, Any, List
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import OUTPUT_COMBINED_DIR
from core.data_loader import load_quotes, load_finance
from backtest.engine import BacktestEngine
from strategies.v5_hybrid import V5HybridStrategy


OUTPUT_COMBINED_DIR.mkdir(parents=True, exist_ok=True)

# 全局预加载（从缓存 parquet 读取，避免重复计算指标）
_CACHE_PATH = Path(__file__).resolve().parent / "data" / "processed" / "kline_2025plus.parquet"

print("[MAIN] 预加载数据（仅一次）...")
_QUOTES   = load_quotes()
_FINANCE  = load_finance()

if _CACHE_PATH.exists():
    print(f"[MAIN] 读取指标缓存: {_CACHE_PATH} ({_CACHE_PATH.stat().st_size/1e6:.0f}MB)...")
    t0 = time.time()
    _KLINE = pd.read_parquet(_CACHE_PATH)
    print(f"[MAIN] ✅ 缓存加载完成 ({time.time()-t0:.1f}s), "
          f"{len(_KLINE):,} 行, {_KLINE['code'].nunique()} 只")
else:
    print("[MAIN] 缓存不存在，从原始数据加载并计算指标...")
    from core.data_loader import load_kline
    from core.indicators import precompute_indicators
    _KLINE = load_kline()
    print("[MAIN] 计算指标（可能需 3-5 分钟）...")
    t0 = time.time()
    _KLINE = precompute_indicators(_KLINE)
    print(f"[MAIN] ✅ 指标计算完成 ({time.time()-t0:.1f}s)")

# 确保指标列存在（engine 会检测 rsi_14_d 列跳过重复计算）
assert "rsi_14_d" in _KLINE.columns, "指标列 rsi_14_d 不存在，缓存可能损坏"
print(f"[MAIN] 行情: {len(_QUOTES)}只, 财务: {len(_FINANCE)}只\n")


def run_backtest(params: Dict[str, Any]) -> Dict[str, Any]:
    """运行一次回测，返回结果字典"""
    engine = BacktestEngine(
        initial_capital=1_000_000,
        start_date="2025-01-01",
        end_date="2026-06-12",
        max_positions=10,
        single_position_pct=0.08,  # (#67) 由0.10降至0.08，降低回撤
    )

    strategy = V5HybridStrategy(**params)
    engine.add_strategy(strategy, weight=1.0)

    try:
        result = engine.run(
            verbose=False,
            kline=_KLINE,
            quotes=_QUOTES,
            finance=_FINANCE,
        )
    except Exception as e:
        return {"error": str(e)[:200]}

    if result is None:
        return {
            "params": "", "trades": 0, "return": 0.0,
            "annual_ret": 0.0, "max_dd": 0.0, "sharpe": 0.0,
            "win_rate": 0.0, "profit_factor": 0.0, "elapsed": 0.0,
        }

    sr = result.strategies.get(strategy.name)
    if not sr or not sr.trades:
        return {
            "params": "", "trades": 0, "return": 0.0,
            "annual_ret": 0.0, "max_dd": 0.0, "sharpe": 0.0,
            "win_rate": 0.0, "profit_factor": 0.0, "elapsed": 0.0,
        }

    trades = sr.trades
    wins   = [t for t in trades if t.return_pct > 0]
    losses = [t for t in trades if t.return_pct <= 0]
    wr  = len(wins) / len(trades) * 100 if trades else 0
    pf_num = sum(t.return_pct for t in wins)
    pf_den = abs(sum(t.return_pct for t in losses)) if losses else 0
    pf  = pf_num / pf_den if pf_den else 0

    days     = (pd.Timestamp("2026-06-12") - pd.Timestamp("2025-01-01")).days
    annual_ret = ((1 + result.total_return / 100) ** (365 / days) - 1) * 100

    return {
        "params":    ",".join(f"{k}={v}" for k, v in params.items()),
        "trades":    len(trades),
        "return":    round(result.total_return, 2),
        "annual_ret": round(annual_ret, 2),
        "max_dd":    round(result.max_drawdown, 2),
        "sharpe":    round(result.sharpe, 2),
        "win_rate":   round(wr, 1),
        "profit_factor": round(pf, 2),
        "elapsed":    0.0,
    }


def main():
    """
    参数网格
    small_grid=True:  ~60组，预计~4小时
    small_grid=False: 300组，预计~20小时
    """
    small_grid = True   # ← 改为 False 跑全量

    if small_grid:
        rsi_list   = [34, 38, 42]
        bb_list    = [0.10, 0.15, 0.20]
        dd_list    = [-8, -12, -16]
        trend_list = [1, 2]
    else:
        rsi_list   = [32, 34, 36, 38, 40, 42]
        bb_list    = [0.10, 0.12, 0.15, 0.18, 0.20]
        dd_list    = [-8, -10, -12, -14, -16]
        trend_list = [1, 2]

    grid = []
    for rsi in rsi_list:
        for bb in bb_list:
            for dd in dd_list:
                for tr in trend_list:
                    grid.append({
                        "MAX_RSI_14":       rsi,
                        "MAX_RSI_6":         26,
                        "MAX_BB_POSITION":   bb,
                        "MAX_DRAWDOWN_60D": dd,
                        "TREND_CHECKS_MIN":  tr,
                    })

    # 快速验证模式：只跑前 2 组
    test_mode = False   # ← 改为 True 只跑前2组
    total = len(grid)
    if test_mode:
        grid  = grid[:2]
        total = len(grid)
        print(f"[TEST MODE] 仅跑前 {total} 组参数\n")

    print("📊 参数扫描启动")
    print(f"  网格大小: {total} 种组合")
    print(f"  RSI14: {rsi_list}")
    print(f"  BB位置: {bb_list}")
    print(f"  DD60:  {dd_list}")
    print(f"  趋势确认: {trend_list}")
    print(f"  预计耗时: ~{total * 4 // 60} 分钟（实测校准）")
    print()

    results = []
    t0 = time.time()

    for i, params in enumerate(grid):
        t_start = time.time()
        r = run_backtest(params)
        t_elapsed = time.time() - t_start

        if "error" in r:
            print(f"  [{i+1}/{total}] ❌ {params} → {r['error'][:60]}")
            continue

        r["elapsed"] = round(t_elapsed, 1)
        results.append(r)

        # 实时打印夏普>0.5 的结果
        if r.get("sharpe", 0) >= 0.5 and r.get("trades", 0) >= 10:
            print(f"  🏆 [{i+1}/{total}] sharpe={r['sharpe']:.2f} "
                  f"annual={r.get('annual_ret',0):+.1f}% "
                  f"dd={r.get('max_dd',0):+.1f}% trades={r['trades']}")

        # 每 10 个保存一次
        if (i + 1) % 10 == 0:
            df_batch = pd.DataFrame(results)
            df_batch.to_csv(
                OUTPUT_COMBINED_DIR / "param_scan_v5_partial.csv",
                index=False, encoding="utf-8-sig"
            )
            elapsed_total = time.time() - t0
            avg_each = elapsed_total / (i + 1)
            eta_min = (total - i - 1) * avg_each / 60
            best = df_batch["sharpe"].max() if not df_batch.empty else 0
            print(f"  💾 [{i+1}/{total}] 完成, "
                  f"最佳夏普={best:.2f}, ETA≈{eta_min:.0f}min")

    # 汇总
    df = pd.DataFrame(results)
    df = df.sort_values("sharpe", ascending=False)

    out_path = OUTPUT_COMBINED_DIR / "param_scan_v5.csv"
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n{'='*70}")
    print(f"📊 参数扫描完成 — 共 {len(results)} 个有效结果")
    print(f"{'='*70}")

    # 打印前 10
    print(f"\n🏆 夏普比率 TOP 10:")
    hdr = f"  {'排名':<4s} {'夏普':>6s} {'年化':>7s} {'回撤':>7s} {'交易':>4s} {'胜率':>5s} 参数"
    print(hdr)
    print(f"  {'-'*90}")
    for idx, (_, r) in enumerate(df.head(10).iterrows()):
        print(f"  {idx+1:<4.0f} {r.get('sharpe',0):>6.2f} {r.get('annual_ret',0):>+7.1f}% "
              f"{r.get('max_dd',0):>+7.1f}% {r.get('trades',0):>4.0f} {r.get('win_rate',0):>5.1f}% "
              f"{r.get('params','')}")

    print(f"\n✅ 结果已保存: {out_path}")


if __name__ == "__main__":
    # 强制实时刷新输出（方便 nohup/后台查看日志）
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
    main()
