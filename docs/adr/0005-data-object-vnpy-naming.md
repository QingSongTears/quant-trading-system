# ADR-0005: 沿用 vnpy 字段名（gateway_name / vt_symbol / vt_orderid）

| 字段 | 值 |
|---|---|
| **状态** | Accepted |
| **日期** | 2026-06-27（反向追溯） |
| **决策人** | @QingSongTears |
| **影响范围** | src/gateway/object.py / 全局 |
| **目标阶段** | v2.1 架构治理 |

## 1. 上下文

vnpy 的数据对象（TickData / OrderData / TradeData）有一套命名前缀：
- `gateway_name` — 网关名
- `vt_symbol` — 内部 symbol（`exchange.symbol` 格式）
- `vt_orderid` — 内部订单号

本项目选择**沿用**而非重命名为 `gateway` / `symbol` / `orderid` 等更短的形式。

## 2. 决策

**所有数据对象字段沿用 vnpy 前缀**：
```python
@dataclass
class OrderData:
    gateway_name: str   # 不改名 gateway
    vt_symbol: str      # 不改名 symbol
    vt_orderid: str     # 不改名 orderid
    ...
```

理由：
- 未来接 xtp/ptrade/qmt 时 vnpy 适配器可直接复用
- 跨项目搜索 vnpy 文档/issue 时关键字一致
- 借鉴代码（strategy/engine/gateway）零修改

## 3. 备选方案

### 方案 A：自定义短名（gateway/symbol/orderid）
- 优点：简短
- 缺点：与 vnpy 生态脱节；未来对接适配器需要做映射层
- 否决：得不偿失

### 方案 B：完全照搬 vnpy 内部命名（CTP/XTP 字段名）
- 优点：与具体券商接口一致
- 缺点：耦合具体网关；字段重名（如 `InstrumentID`）冲突
- 否决：耦合过深

### 方案 C：沿用 vnpy 通用前缀（已选）
- 优点：与 vnpy 兼容 + 抽象层
- 缺点：字段名略长
- 否决理由：不适用

## 4. 后果

### 正面
- vnpy 文档/Issue 搜索"vt_symbol"能直接找到答案
- 未来对接 xtp/ptrade 可借鉴 vnpy adapter
- 借鉴层代码（src/strategy/、src/gateway/）零改动

### 负面
- 字段名偏长（11 字符）
- 新成员需学习"vt_" 前缀含义

### 风险
- 误导以为"一定要用 vnpy" → 实际是借鉴风格
- **缓解**：在 `src/gateway/object.py` 顶部 docstring 明确"借鉴而非依赖"

## 5. 实施

- [x] `src/gateway/object.py` 已用 `vt_` 前缀
- [ ] 在 `src/gateway/__init__.py` 加 docstring 说明
- [ ] 写 `tests/test_object.py` 验证字段对齐

## 6. 关联

- 反对 / 推翻：无
- 关联 issue：#78（v2.1 治理入口）
- 实施入口：[src/gateway/object.py](../../src/gateway/object.py)
- 规范来源：`AI_TASK_BOARD.md §5`

## 7. 备注

`vt_` 是 vnpy 内部"virtual trading" 的缩写，意为"逻辑/虚拟层"的标识符，与具体券商 API 字段解耦。
