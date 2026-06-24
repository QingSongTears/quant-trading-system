# ArrayManager 使用指南 (2026-06-25)

> **TL;DR**: `ArrayManager` 把 K 线转成 numpy 数组, 提供 17 个技术指标 (趋势/动量/波动/成交/辅助)。

## 17 指标速查

| 类别 | 指标 | 方法 | 默认参数 | 返回 | 范围 |
|------|------|------|---------|------|------|
| **趋势 (5)** | SMA 简单移动平均 | `sma(n)` | n=20 | float | — |
| | EMA 指数移动平均 | `ema(n)` | n=20 | float | — |
| | MACD 指数平滑异同 | `macd(fast, slow, signal)` | 12,26,9 | (DIF, DEA, MACD柱) | — |
| | 布林带 | `boll(n, dev)` | 20, 2.0 | (mid, upper, lower) | — |
| | 唐奇安通道 | `donchian(n)` | n=20 | (upper, lower) | — |
| **动量 (5)** | RSI 相对强弱 | `rsi(n)` | n=14 | float | 0~100 |
| | KDJ 随机指标 | `kdj(n, m1, m2)` | 9,3,3 | (K, D, J) | 0~100 |
| | Williams %R | `wr(n)` | n=14 | float | -100~0 |
| | CCI 顺势指标 | `cci(n)` | n=14 | float | 任意 |
| | ROC 变动率 | `roc(n)` | n=12 | float % | 任意 |
| **波动 (3)** | ATR 平均真实波幅 | `atr(n)` | n=14 | float | >0 |
| | StdDev 标准差 | `std(n)` | n=20 | float | >0 |
| | NATR 归一化 ATR | `natr(n)` | n=14 | float % | 0~100 |
| **成交 (2)** | OBV 能量潮 | `obv()` | — | float | 任意 |
| | MFI 资金流量 | `mfi(n)` | n=14 | float | 0~100 |
| **辅助 (2)** | TR 真实波幅 | `tr()` | — | float | >0 |
| | DM 方向移动 | `dm()` | — | (+DM, -DM) | — |

> **算法精度 (2026-06-25 修)**: MACD / KDJ / boll 跟 vnpy/TradingView/同花顺对齐。
> - MACD: DEA = EMA(DIF, signal) 全序列递推
> - KDJ: K/D 持续状态 (不每次重置 50)
> - boll: ddof=1 样本标准差 (ddof=0 会偏窄)

## 基本用法

```python
from src.indicator import ArrayManager
from src.data import data_mgr

# 1. 拉数据
bars = data_mgr.datafeed.get_bars("000001.SZ", "1d", count=60)

# 2. 实例化 (size 控制 FIFO, 超过自动淘汰)
am = ArrayManager(size=60)
am.update_bars(bars)  # 批量推
# 或逐根推 (实盘)
# for bar in bars_stream:
#     am.update_bar(bar)

# 3. 算指标
print(f"MA20 = {am.sma(20):.2f}")
print(f"RSI14 = {am.rsi(14):.1f}")
print(f"MACD = {am.macd()}")  # (DIF, DEA, MACD柱)
print(f"BOLL = {am.boll()}")  # (mid, upper, lower)
```

## 接 BarGenerator (K 线合成)

```python
from src.indicator import BarGenerator, ArrayManager
from src.data import data_mgr

# 1m → 5m 合成
bars_1m = data_mgr.datafeed.get_bars("000001.SZ", "1m", count=240)
am = ArrayManager(size=240)
on_5m_bar = []

def on_5m(bar):
    on_5m_bar.append(bar)
    am.update_bar(bar)

bg = BarGenerator(
    on_bar=lambda b: None,  # 每根 1m 触发
    window=5, interval="5m",
    on_window_bar=on_5m,
)

for bar in bars_1m:
    bg.update_bar(bar)

# 现在 am 里是 5m K 线, 算 5m 指标
print(f"5m MA20 = {am.sma(20):.2f}")
```

## 数据不足时

所有指标在数据不足时**返 None** (不抛), 方便边界处理:

```python
am = ArrayManager(size=5)
am.update_bars(bars[:3])  # 只有 3 根
print(am.sma(20))  # None
print(am.macd())   # None

if am.atr(14) is not None:
    print(f"ATR14 = {am.atr(14):.4f}")
```

## 性能注意

- `update_bar()` 一次更新做 7 次 `arr[:-1] = arr[1:]`, **O(size) 拷贝**
- 大量数据推送时 size 控制在 200 以内 (默认 100)
- 频繁调指标 (每根 K 线) 可能有性能压力, 考虑缓存结果

## A 股适配

- 默认参数 20/60 周期适合日 K
- 短线 (5m/15m) 用更短周期 (5/14/20)
- NATR 用百分比表示波动率, 跨股票可比

## 与 vnpy 4.4 差异

| 维度 | vnpy 4.4 | 本项目 |
|------|----------|--------|
| 指标数 | 25+ | 17 (够用为先) |
| EMA 算法 | pandas ewm | 手动递推 (无 pandas 依赖) |
| 持仓维护 | 单独 PositionArrayManager | 无 (策略层维护) |
| Tick → bar | 实时 OHLCV 累计 | 无 (A 股 tick 不适用) |

## 高级: 持续状态

KDJ 维护 `self._kdj_k` / `self._kdj_d`, 持续递推。`reset()` 时也清回 50。

```python
am = ArrayManager(size=20)
am.update_bars(bars[:10])
k1, d1, _ = am.kdj()
am.update_bar(bars[10])  # K/D 继续递推, 不重置
k2, d2, _ = am.kdj()
# k2 > k1 (上涨趋势)
```

## 错误处理

```python
am = ArrayManager(size=0)
# ValueError: size 必须 >= 1, 当前 0

# 数据不足不抛
am = ArrayManager(size=5)
am.update_bar(bar)
print(am.sma(20))  # None, 不抛
```
