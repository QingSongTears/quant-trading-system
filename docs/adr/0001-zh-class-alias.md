# ADR-0001: 中文业务名 + 英文 class alias 双向声明

| 字段 | 值 |
|---|---|
| **状态** | Accepted |
| **日期** | 2026-06-27（反向追溯） |
| **决策人** | @QingSongTears |
| **影响范围** | src/business_strategies / src/scoring / src/backtest |
| **目标阶段** | v2.1 架构治理 |

## 1. 上下文

项目策略类有 3 个命名维度：
- README / Web 界面需要中文业务名（"V6超卖反转"）
- 代码 import 需要英文 class name（`V6ReversalStrategy`）
- API / 日志需要英文别名（`V6 Reversal`）

历史问题：
- 类名不带业务语义（如 `MACDSignalStrategy` 缺"超卖"）
- 多数类继承默认 `name = "base"`，未 override
- 中英文无法互查

## 2. 决策

**所有业务类（继承 `BaseStrategy` / `BaseScorer` / `BaseEngine` / `BaseProvider`）必须显式声明两个 class 属性：**

```python
class V6ReversalStrategy(BaseSelectionStrategy):
    zh_name = "V6超卖反转"        # 必填 — README / Web 界面
    en_name = "V6 Reversal"       # 必填 — 日志 / API
    description = "RSI14≤38 + BB≤0.10 + DD60≤-8% + 反转放量"
```

**约束**：
- `zh_name` 中文业务名 — 在 Web 界面、README、策略对比页显示
- `en_name` 英文短名 — 在 API 返回、日志、CLI 输出使用
- `description` 一句话说明 — 在 tooltip / 调试日志使用
- 守门脚本 `dev_tools/hooks/check_naming.py` 自动校验，缺任一字段 → commit 失败

## 3. 备选方案

### 方案 A：纯英文类名 + 中文写在 docstring
- 优点：简单
- 缺点：Web 界面需要硬编码中文映射表；改名困难
- 否决：与 Web 19 页 Ardot 设计稿冲突

### 方案 B：完全中文化（类名/方法名都用中文）
- 优点：本地化彻底
- 缺点：与 vnpy 借鉴层冲突；IDE 补全差；不利于多 AI 协作文本生成
- 否决：可读性差，与国际开源生态脱节

### 方案 C：英文类名 + i18n 文件
- 优点：国际化标准
- 缺点：当前阶段只有 zh-CN 一个 locale，过度设计
- 否决：等真的有 i18n 需求时再做

## 4. 后果

### 正面
- Web 界面、API、日志用同一来源
- 改名只改一处
- 多 AI 协同时看到 alias 立刻明白业务含义

### 负面
- 每个新类多 2 行
- 既有类需要补 alias（v2.1 重构期一次性补完）

### 风险
- 守门脚本误报：v2.1 治理前允许用 `# allow-naming-missing` 注释豁免
- 中英文不一致：建议同一概念在 docstring / 注释 / commit message 都用同一对

## 5. 实施

- [x] 守门脚本 `check_naming.py` 实现
- [ ] v2.1.1 治理期：补全 9 个生产策略的 alias
- [ ] v2.1.1 治理期：补全 7 个 scorer 的 alias
- [ ] 守门 v2.2 起**强制**（不豁免）

## 6. 关联

- 反对 / 推翻：无
- 关联 issue：#78（v2.1 治理入口）
- 关联 PR：#78
- 实施入口：[dev_tools/hooks/check_naming.py](../../dev_tools/hooks/check_naming.py)
- 规范来源：[AGENTS.md §3.2](../../AGENTS.md)

## 7. 备注

本 ADR 是 v2.1 治理期反向追溯生成。原始约定见 `AI_TASK_BOARD.md §5 关键架构决策`（但缺乏"为什么"和"备选"），本 ADR 补完。
