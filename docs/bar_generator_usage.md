# BarGenerator 使用指南 (2026-06-24)

> **TL;DR**: `BarGenerator` 把低周期 K 线合成高周期 (1m→5m→15m→1h→1d), 也支持 tick 合成 1m。

## 核心概念

**低周期 → 高周期** 累计: 给定 `window=N`, 每收到 N 根低周期 K 线, 输出 1 根高周期 K 线。

OHLCV 规则:
- **open** = 第 1 根 open
- **high** = max(所有 low_period.high)
- **low** = min(所有 low_period.low)
- **close** = 最后 1 根 close
- **volume** = sum
- **turnover** = sum

## 两种用法

### 1. 一次性批量 (回测常用)

```python
from src.indicator import bars_from_lower
from src.data import data_mgr

bars_1m = data_mgr.datafeed.get_bars("000001.SZ", "1m", count=240)
bars_5m = bars_from_lower(bars_1m, window=5, interval="5m")
# 240 / 5 = 48 根 5m K 线
```

**特点**:
- 简单, 无回调
- 末尾不足 `window` 的丢弃 (10 根 1m → 2 根 5m, 剩 0 根丢)
- 适合**批量回测** (一次性拉数据, 一次合成)

### 2. 实时逐根 (实盘/模拟盘)

```python
from src.indicator import BarGenerator

on_5m_bars = []
bg = BarGenerator(
    on_bar=lambda b: print(f"1m: {b.close_price}"),  # 每根 1m 都触发
    window=5, interval="5m",
    on_window_bar=on_5m_bars.append,  # 每 5 根触发
)

# 模拟实时推
for bar_1m in bars_1m_stream:
    bg.update_bar(bar_1m)
```

**特点**:
- 流式处理
- 支持 `update_tick()` (从 tick 合成 1m)
- 适合**实盘/回测 step-by-step**

## 嵌套 (高级用法)

低 → 中 → 高 多级合成, 典型: 1m → 5m → 30m。

```python
bg_30m = BarGenerator(on_bar=on_30m, window=6, interval="30m")
bg_5m = BarGenerator(
    on_bar=on_5m,
    window=5, interval="5m",
    on_window_bar=bg_30m.update_bar,  # 关键: 5m bar 喂给 30m
)

# 推 1m 数据
for bar_1m in bars_1m:
    bg_5m.update_bar(bar_1m)
# 30 根 1m → 6 根 5m → 1 根 30m
```

## 支持的 interval

| interval | 用途 |
|----------|------|
| `1m` | 1 分钟 (基础) |
| `5m` / `15m` / `30m` | 短线 |
| `1h` / `4h` | 中线 |
| `1d` | 日 K |

**注意**: `3m` / `2h` / `10m` 等不规则 interval **不支持** (INTERVAL_TO_MINUTES 字典里没注册)。

## 状态查询 / 控制

```python
bg = BarGenerator(on_bar=cb, window=5, interval="5m")
bg.update_bar(bar_1m_1)
bg.update_bar(bar_1m_2)

bg.is_in_window     # True (正在累计 5m)
bg.bars_in_window   # 2 (当前 5m 已收了 2 根 1m)

bg.reset()  # 丢弃当前未完成的 5m, 重置状态
```

## 错误处理

```python
BarGenerator(on_bar=cb, interval="3m")
# ValueError: 未知 interval: '3m' (支持: ['1m', '5m', '15m', '30m', '1h', '4h', '1d'])

BarGenerator(on_bar=cb, window=0)
# ValueError: window 必须 >= 1, 当前 0
```

## A 股适配说明

- 日 K 自然按日累计, 不需特殊处理
- 分钟 K 实际只有 09:30-11:30 + 13:00-15:00 = 4 小时, 240 分钟
- A 股 tick 没有"实时刷新", BarGenerator.update_tick() 主要为未来对接实时行情预留
- 节假日/周末天然不产生 K 线 (无数据流入 → BarGenerator 不触发)

## 与 data_mgr.datafeed 协作

```python
from src.data import data_mgr
from src.indicator import BarGenerator

# 拉 1 天 1m → 合 5m
bars_1m = data_mgr.datafeed.get_bars("000001.SZ", "1m", count=240)
on_5m = []
bg = BarGenerator(on_bar=lambda b: None, window=5, interval="5m", on_window_bar=on_5m.append)
for bar in bars_1m:
    bg.update_bar(bar)
print(f"5m bars: {len(on_5m)}")
```

## 设计取舍 (vs vnpy)

| 维度 | vnpy 4.4 | 本项目 |
|------|----------|--------|
| Tick → 1m | 实时 OHLCV 累计 | 同 (但未对接实时) |
| 多级嵌套 | 支持 (N 层) | 支持 |
| 窗口类型 | 任意 window | 同 |
| 回调粒度 | on_bar / on_window_bar | 同 |
| A 股交易时段 | 不感知 | 不感知 (依赖数据正确性) |

## 后续: ArrayManager

BarGenerator 只管"合成", K 线 → 指标 (MA/MACD/KDJ/ATR/...) 由 `ArrayManager` 负责 (下一阶段实现, T6.3 follow-up)。
