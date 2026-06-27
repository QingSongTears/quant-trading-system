# ADR-0002: EventEngine 同步派发（vs vnpy 异步 Queue）

| 字段 | 值 |
|---|---|
| **状态** | Accepted |
| **日期** | 2026-06-27（反向追溯） |
| **决策人** | @QingSongTears |
| **影响范围** | src/event/ |
| **目标阶段** | v2.1 架构治理 |

## 1. 上下文

vnpy 4.x 的 `EventEngine` 用 `Queue` 做异步派发：
- Producer put → Queue → Consumer get (阻塞)
- 多线程消费者，天然的 back-pressure

我们项目的 `EventEngine` 选择了**同步派发**：
- Producer put → 同步遍历所有 handler 调用
- 单线程 or GIL 下并发安全（CPython）

## 2. 决策

**本项目 EventEngine 保持同步派发**：
```python
# src/event/engine.py 简化示意
def put(self, event):
    for handler in self._handlers[event.type]:
        handler(event)   # 同步调用，捕获异常不影响其他 handler
```

## 3. 备选方案

### 方案 A：沿用 vnpy 异步 Queue
- 优点：与 vnpy 完全兼容
- 缺点：调试时序复杂；测试需 mock Queue；增加 1 个线程
- 否决：当前没有 high-frequency event 流，async 优势用不上

### 方案 B：asyncio 事件循环
- 优点：现代、生态丰富
- 缺点：与 FastAPI 同步栈混用复杂；学习成本高
- 否决：过度工程

### 方案 C：回调注册 + 同步派发（已选）
- 优点：实现简单、测试容易、调试直接
- 缺点：高并发场景需手动加锁
- 否决理由：不适用

## 4. 后果

### 正面
- 测试不需要 mock Queue
- 异常隔离好（一个 handler 抛错不影响其他）
- 单步调试可以打断点

### 负面
- 慢 handler 阻塞整个 dispatch
- 需要在文档强调"handler 不能做 I/O 阻塞操作"

### 风险
- 实盘化时如有高频事件（每只股票每 tick 一次），需要切到异步
- **触发回滚条件**：当 `put()` 调用频率 > 1000/s 且 P99 latency > 50ms

## 5. 实施

- [x] 当前 EventEngine 实现
- [ ] 写 `docs/event_engine_usage.md` 强调 handler 不能阻塞
- [ ] 加 latency 监控埋点（v2.3）

## 6. 关联

- 反对 / 推翻：无
- 关联 issue：#78（v2.1 治理入口）
- 实施入口：[src/event/engine.py](../../src/event/engine.py)
- 规范来源：`AI_TASK_BOARD.md §5`

## 7. 备注

与 ADR-0001 同批反向追溯生成。原始决策散落 3 处（AI_TASK_BOARD / LIVE_TRADING_ROADMAP / 口头约定），本 ADR 集中沉淀。
