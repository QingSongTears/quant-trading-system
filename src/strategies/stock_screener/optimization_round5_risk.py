"""
参数扫描 — v5_hybrid 风控参数优化 (#67)
=========================================
扫描 STOP_LOSS / TAKE_PROFIT / TRAILING_STOP / TRAILING_DD

入口参数固定为 #54 最优值：
  MAX_RSI_14=38, MAX_RSI_6=26, MAX_BB_POSITION=0.10
  MAX_DRAWDOWN_60D=-8, TREND_CHECKS_MIN=2

扫描维度 (3x3x3x2 = 54 种):
  1. STOP_LOSS:     [-5%, -7%, -10%]
  2. TAKE_PROFIT:   [8%, 12%, 15%]
  3. TRAILING_STOP: [8%, 12%, 15%]
  4. TRAILING_DD:   [-3%, -5%]

输出: output/param_scan_risk.csv (按最大回撤升序排列)
"""
import sys, time, pandas as pd, numpy as np
from typing import Dict, Any
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import OUTPUT_COMBINED_DIR
from core.data_loader import load_quotes, load_finance
from backtest.engine import BacktestEngine
from strategies.v5_hybrid import V5HybridStrategy

# 入口参数（#54 最优）
ENTRY_PARAMS = {
    "MAX_RSI_14": 38,
    "MAX_RSI_6": 26,
    "MAX_BB_POSITION": 0.10,
    "MAX_DRAWDOWN_60D": -8,
    "TREND_CHECKS_MIN": 2,
}

OUTPUT_COMBINED_DIR.mkdir(parents=True, exist_ok=True)

# 加载数据 + 指标缓存
_CACHE_PATH = Path(__file__).resolve().parent / "data" / "processed" / "kline_2025plus.parquet"
print("[MAIN] 加载数据...")
_QUOTES  = load_quotes()
_FINANCE = load_finance()

if _CACHE_PATH.exists():
    print(f"[MAIN] 读取指标缓存: {_CACHE_PATH}...")
    _KLINE = pd.read_parquet(_CACHE_PATH)
    print(f"[MAIN] 就绪: {len(_KLINE):,}行 / {_KLINE['code'].nunique()}只 / "
          f"{len(_QUOTES)}只行情 / {len(_FINANCE)}只财务")
else:
    print("[MAIN] 缓存不存在，请先跑 generate_indicator_cache.py")
    sys.exit(1)


def run_backtest(params: Dict[str, Any]) -> Dict[str, Any]:
    """运行一次回测"""
    # 合并入口参数 + 风控参数
    full_params = {**ENTRY_PARAMS, **params}

    engine = BacktestEngine(
        initial_capital=1_000_000,
        start_date="2025-01-01",
        end_date="2026-06-12",
        max_positions=10,
        single_position_pct=0.08,  # (#67) 由0.10降至0.08
    )

    strategy = V5HybridStrategy(**full_params)
    engine.add_strategy(strategy, weight=1.0)

    try:
        result = engine.run(verbose=False, kline=_KLINE, quotes=_QUOTES, finance=_FINANCE)
    except Exception as e:
        return {"error": str(e)[:200]}

    empty = {"trades": 0, "return": 0.0, "annual_ret": 0.0,
             "max_dd": 0.0, "sharpe": 0.0, "win_rate": 0.0,
             "profit_factor": 0.0, "elapsed": 0.0}

    if result is None:
        return empty

    sr = result.strategies.get(strategy.name)
    if not sr or not sr.trades:
        return empty

    trades = sr.trades
    wins = [t for t in trades if t.return_pct > 0]
    losses = [t for t in trades if t.return_pct <= 0]
    wr = len(wins) / len(trades) * 100
    pf_num = sum(t.return_pct for t in wins)
    pf_den = abs(sum(t.return_pct for t in losses)) if losses else 0
    pf = pf_num / pf_den if pf_den else 0

    days = (pd.Timestamp("2026-06-12") - pd.Timestamp("2025-01-01")).days
    annual_ret = ((1 + result.total_return / 100) ** (365 / days) - 1) * 100

    # 统计退出原因
    reasons = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1

    return {
        "params": ";".join(f"{k}={v}" for k, v in sorted(params.items())),
        "trades": len(trades),
        "return": round(result.total_return, 2),
        "annual_ret": round(annual_ret, 2),
        "max_dd": round(result.max_drawdown, 2),
        "sharpe": round(result.sharpe, 2),
        "win_rate": round(wr, 1),
        "profit_factor": round(pf, 2),
        "sl_pct": f"{strategy.STOP_LOSS*100:.0f}%",
        "tp_pct": f"{strategy.TAKE_PROFIT*100:.0f}%",
        "trail_pct": f"{strategy.TRAILING_STOP*100:.0f}%",
        "trail_dd": f"{strategy.TRAILING_DD*100:.0f}%",
        "exit_reasons": ";".join(f"{k}:{v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1])),
        "elapsed": 0.0,
    }


def main():
    stop_loss    = [-0.05, -0.07, -0.10]
    take_profit  = [0.08, 0.12, 0.15]
    trailing_stop = [0.08, 0.12, 0.15]
    trailing_dd  = [-0.03, -0.05]

    grid = []
    for sl in stop_loss:
        for tp in take_profit:
            for ts in trailing_stop:
                for td in trailing_dd:
                    grid.append({
                        "STOP_LOSS": sl,
                        "TAKE_PROFIT": tp,
                        "TRAILING_STOP": ts,
                        "TRAILING_DD": td,
                    })

    # 快速验证模式
    test_mode = False
    total = len(grid)
    if test_mode:
        grid = grid[:2]
        total = len(grid)
        print(f"[TEST MODE] 仅前 {total} 组\n")

    print(f"📊 风控参数扫描启动")
    print(f"  网格: {total} 组")
    print(f"  STOP_LOSS: {stop_loss}")
    print(f"  TAKE_PROFIT: {take_profit}")
    print(f"  TRAILING_STOP: {trailing_stop}")
    print(f"  TRAILING_DD: {trailing_dd}")
    print(f"  入口参数: {ENTRY_PARAMS}")
    print()

    results = []
    t0 = time.time()

    for i, params in enumerate(grid):
        t_start = time.time()
        r = run_backtest(params)
        t_elapsed = time.time() - t_start

        if "error" in r:
            print(f"  [{i+1}/{total}] ❌ error: {r['error'][:60]}")
            continue

        r["elapsed"] = round(t_elapsed, 1)
        results.append(r)

        # 打印回撤 < -50% 或 夏普 > 0.5 的结果
        dd = r.get("max_dd", 0)
        sr = r.get("sharpe", 0)
        ar = r.get("annual_ret", 0)
        tr = r.get("trades", 0)
        if sr >= 0.3 and ar >= -20:
            print(f"  [{i+1}/{total}] sharpe={sr:.2f} annual={ar:+.1f}% "
                  f"dd={dd:+.1f}% trades={tr} "
                  f"SL={r.get('sl_pct','?')} TP={r.get('tp_pct','?')} "
                  f"TS={r.get('trail_pct','?')} TD={r.get('trail_dd','?')}")

        # 每 10 个保存一次
        if (i + 1) % 10 == 0:
            df_batch = pd.DataFrame(results)
            df_batch.to_csv(
                OUTPUT_COMBINED_DIR / "param_scan_risk_partial.csv",
                index=False, encoding="utf-8-sig"
            )
            elapsed_total = time.time() - t0
            avg_each = elapsed_total / (i + 1)
            eta_min = (total - i - 1) * avg_each / 60
            best_dd = df_batch["max_dd"].max() if not df_batch.empty else 0
            best_sr = df_batch["sharpe"].max() if not df_batch.empty else 0
            print(f"  💾 [{i+1}/{total}] 当前最佳: 夏普={best_sr:.2f} "
                  f"最大回撤={best_dd:.1f}% ETA≈{eta_min:.0f}min")

    # 汇总
    df = pd.DataFrame(results)

    # 按回撤升序（越小越好），夏普降序
    df = df.sort_values(["max_dd", "sharpe"], ascending=[True, False])

    out_path = OUTPUT_COMBINED_DIR / "param_scan_risk.csv"
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n{'='*70}")
    print(f"📊 风控参数扫描完成 — {len(results)} 个有效结果")
    print(f"{'='*70}")

    # 打印前 15（回撤最低的）
    print(f"\n🏆 回撤最低 TOP 15:")
    hdr = (f"  {'排名':<4s} {'夏普':>6s} {'年化':>7s} {'回撤':>7s} "
           f"{'交易':>4s} {'胜率':>5s} SL TP TS TD")
    print(hdr)
    print(f"  {'-'*95}")
    for idx, (_, r) in enumerate(df.head(15).iterrows()):
        print(f"  {idx+1:<4.0f} {r.get('sharpe',0):>6.2f} "
              f"{r.get('annual_ret',0):>+7.1f}% "
              f"{r.get('max_dd',0):>+7.1f}% "
              f"{r.get('trades',0):>4.0f} "
              f"{r.get('win_rate',0):>5.1f}% "
              f"{r.get('sl_pct','?'):>3s}  {r.get('tp_pct','?'):>3s}  "
              f"{r.get('trail_pct','?'):>3s}  {r.get('trail_dd','?'):>3s}")

    print(f"\n✅ 结果已保存: {out_path}")


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
    main()
