# #76 VNPY-1 概览

## 结论
**V6 早就完成 VNPY-1 要求的继承关系**——issue 描述里说的"现状"已是历史。本次任务为补齐**测试覆盖 + 端到端 smoke**。

## 现状 (发现)
- `V6ReversalSelectionStrategy(EquityStrategy)` 已实现 (src/strategies/v6_reversal_selection.py:32)
- 全部 abstract methods 已实现: `__abstractmethods__ = frozenset()`
- `MainEngine` StrategyEngine Protocol 6 个方法全部存在 (src/gateway/main_engine.py:241-306)
  - send_order / cancel_order / write_log / get_cash_available / get_holding_value / get_signal
- 已有 `generate_signals()` 返回 EquityStrategy 期望的 DataFrame 格式 (2026-06-26 P0-3 修复)

## 本次新增

| 文件 | 行数 | 角色 |
|------|------|------|
| `tests/test_v6_vnpy_integration.py` | 277 | 13 集成测试 |
| `scripts/vnpy_v6_smoke.py` | 153 | 端到端 smoke |

## 测试覆盖 (13 + 1 跳过)

| Class | 测试 | 验证点 |
|-------|------|--------|
| `TestV6Inheritance` | 3 | MRO 链、abstract 完整性、参数继承 |
| `TestV6CanInstantiate` | 2 | 实例化 + setting 覆盖 |
| `TestV6GenerateSignals` | 2 | DataFrame 形状、vt_symbol 格式 |
| `TestV6OnBars` | 4 | send_order 触发、买卖分支、target 切换 |
| `TestV6BacktestInterop` | 2 | 老 `select()` 路径不破 |
| `TestV6VnpyFlow` | 1 | 完整生命周期: init→start→bars→trade→stop |

## Smoke Test 输出 (节选)

```
[1/6] 检查 V6 继承关系
  ✓ V6 IS-A EquityStrategy (MRO V6→Equity→Alpha)
  ✓ 全部 abstract method 已实现

[2/6] 实例化 MainEngine + V6
  ✓ Cash=500,000, Holding=0

[3-6] on_init / on_bars / set_target / execute_trading
  ✓ 成交后 pos_data[000001.SZ] = 1000.0
  ✓ 平仓目标设置: 000001 → 0

✅ #76 VNPY-1 验证通过
```

## 验证命令

```bash
# 集成测试 (13 + 1 skipped)
python -m pytest tests/test_v6_vnpy_integration.py -v

# 端到端 smoke
python scripts/vnpy_v6_smoke.py
```

## 后续
- issue #76 关闭 (VNPY-1 验证完成)
- 下一个: #77 RiskEngine 骨架

## Issue

https://github.com/QingSongTears/quant-trading-system/issues/76
