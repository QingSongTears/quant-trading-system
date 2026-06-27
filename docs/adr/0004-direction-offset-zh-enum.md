# ADR-0004: Direction / Offset 用中文 enum（多/空/开/平）

| 字段 | 值 |
|---|---|
| **状态** | Accepted |
| **日期** | 2026-06-27（反向追溯） |
| **决策人** | @QingSongTears |
| **影响范围** | src/gateway/object.py / 全局 |
| **目标阶段** | v2.1 架构治理 |

## 1. 上下文

vnpy 用 `Direction.LONG/SHORT` + `Offset.OPEN/CLOSE`（英文 enum）。

本项目选择**中文 enum**：
```python
class Direction(Enum):
    多 = "多"   # LONG
    空 = "空"   # SHORT

class Offset(Enum):
    开 = "开"   # OPEN
    平 = "平"   # CLOSE
```

## 2. 决策

**Direction / Offset 保持中文 enum**，不切换到英文。

理由：
- A 股术语习惯（"做多" "做空" "开仓" "平仓"）
- Web 界面、日志、CLI 都用中文，文案统一
- 与 zh_name 中文化方向一致（ADR-0001）

## 3. 备选方案

### 方案 A：英文 enum（LONG/SHORT/OPEN/CLOSE）
- 优点：与 vnpy 完全兼容
- 缺点：与中文界面风格不一致
- 否决：本地化不彻底

### 方案 B：双语 enum（LONG/多 + SHORT/空）
- 优点：兼容 + 本地化
- 缺点：枚举值翻倍，序列化时易混
- 否决：复杂

### 方案 C：纯中文 enum（已选）
- 优点：一致、本地化
- 缺点：跨语言互操作差
- 否决理由：不适用

## 4. 后果

### 正面
- 代码读起来符合 A 股术语习惯
- Web 界面零映射成本
- API 返回值直接是中文，UI 不用翻译

### 负面
- 与外部 vnpy 库对接时需翻译
- IDE 自动补全可能不识别中文

### 风险
- 编码问题（GBK vs UTF-8）—— 全项目强制 UTF-8
- 跨项目 JSON 序列化时 enum 变字符串——统一 `Enum.value`

## 5. 实施

- [x] `src/gateway/object.py` 已用中文 enum
- [ ] 写 `tests/test_object.py` 验证序列化
- [ ] API 文档明确返回 `"多"/"空"/"开"/"平"` 字符串

## 6. 关联

- 反对 / 推翻：无
- 关联 issue：#78（v2.1 治理入口）
- 实施入口：[src/gateway/object.py](../../src/gateway/object.py)
- 规范来源：`AI_TASK_BOARD.md §5`

## 7. 备注

注：cn / en 切换总是争议大。本 ADR 明确"本项目用中文"，避免未来 AI 看到 vnpy 英文 enum 想"统一一下"再次引发讨论。
