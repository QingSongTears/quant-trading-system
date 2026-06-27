# ADR-0008: AStockDataset 真接 LeaderFeatureBuilder（v_leader 训练/推理分布统一）

| 字段 | 值 |
|---|---|
| **状态** | ✅ Accepted |
| **日期** | 2026-06-27 |
| **决策人** | @QingSongTears |
| **影响范围** | src/research/dataset.py, src/research/features/leader_features.py, tests/test_research_dataset.py |
| **目标阶段** | v2.2 vnpy 走通 / v3.0 实盘化 |
| **实施 commit** | (待 Step 7 commit 后回填) |

## 0. 实施记录

- **代码改动**:
  - `src/research/dataset.py`: 删除 301-326 行 (内嵌 RSI/MACD, -44 行); `__init__` 默认 `LeaderFeatureBuilder()` (sentinel 区分 "未传" vs "显式 None"); `_fetch_features` 统一走 `feature_builder.build()` (支持 dict / DataFrame 两种返回)
  - 新增 `tests/test_research_dataset.py`: 7 个测试覆盖默认行为 / 显式降级 / 列名一致 / 74 维路径 / fit 形状 / 同源训练推理 / 失败兜底
- **守门结果**:
  - `tests/test_research_dataset.py`: 7/7 ✅
  - `tests/test_risk_engine.py` + `test_risk_engine_concentration.py` + `test_event_data.py` (Issue #77): 65/65 ✅ (未破坏)
  - 合计: 72/72 ✅
- **关键决策点**:
  1. **sentinel `_USE_DEFAULT_BUILDER`** 区分 "未传" vs "显式 None" → Python 参数默认值无法区分, 故用 sentinel 对象
  2. **双 return 类型支持**: `feature_builder.build()` 可能返 dict (`LeaderFeatureBuilder`) 或 DataFrame (`v_leader_features.FeatureBuilder`), `_fetch_features` 两种都处理
  3. **保留 `_get_data_mgr` lazy 加载**: akshare 缺失时 fallback 到 `_mock_features` (生产不影响, 单测 mock 用)

## 1. 上下文（Context）

### 1.1 现况

`AStockDataset`（`src/research/dataset.py:209`）是研究层数据抽象（VNPY 借鉴 v2.1 落地），`fit()` / `predict()` 是离线训练 + 在线推理的 (X, y) 拉取入口。

**当前 `_fetch_features` 的两套逻辑**（`src/research/dataset.py:255-340`）：

| 路径 | 触发条件 | 实现 | 维度 | 风险 |
|---|---|---|---|---|
| **A. 内嵌 5 维** | 默认（`feature_builder=None`） | 行 303-326：临时用 `IndicatorRegistry` + `rolling.apply` 算 RSI14 / MACD，加到 `df` | `open/high/low/close/volume` + `rsi14` + `macd` = **7 列**（但 feature_cols 过滤后实际 5-7 维）| 与 `v_leader_features.TECH_COLS` 不一致（缺 `kdj_k/kdj_j/boll_pos`）|
| **B. feature_builder 注入** | 调用方传 | 行 329-334：`feature_builder(bars_df)` → concat 新列 | 调用方决定（典型 `LeaderFeatureBuilder` 5 维，或 `v_leader_features.FeatureBuilder` 74 维）| 必须显式传，默认不开箱 |

**`v_leader_features.FeatureBuilder`**（`src/strategies/v_leader_features.py:153`，572 行）—— 当前生产策略的"事实标准"特征工程：

- 维度：**74 维**（7 评分 raw + 8 评分 pct + 1 avg + 8 lag1m + 8 delta + **5 技术指标** + 3 交叉 + 33 行业 one-hot）
- 技术指标来源（`_load_tech_features` 第 300-331 行）：**从 DB 表 `technical_indicators` 查**（预计算），缺值回溯 9 天
- 评分来源：8 个 Scorer（`ScorerRegistry.batch_score()`）
- 与 `scripts/train_xgb_v4.py` 训练时**完全一致**（74 维），是其 docstring 明文承诺

**`LeaderFeatureBuilder`**（`src/research/features/leader_features.py:50`，153 行）—— 2026-06-25 新建：

- 维度：**5 维**（`macd_hist, rsi14, kdj_k, kdj_j, boll_pos`），与 `v_leader_features.TECH_COLS` 严格一致
- 来源：**实时算**（`IndicatorRegistry`）
- docstring 写明："实时 5 维技术指标算子（替代 `v_leader_features._load_tech_features`）"
- 用法：单股 `build(bars_df)` 或批量 `build_batch({vt_symbol: df})`

### 1.2 痛点：训练/推理分布不一致

**P3.2 TODO 的真实含义**（`TODO.md:35`）：

> v_leader 真接 AStockDataset（替换 hash mock）

字面是"替换 hash mock"，但 hash mock 路径（`_mock_features` 第 342-360 行）**生产已不调用**——只在 `data_mgr` 初始化失败时 fallback。**真实问题**：

1. **5 维 vs 74 维**：生产 v_leader 走 `v_leader_features.FeatureBuilder` 74 维；`AStockDataset` 默认 7 维（OHLCV + rsi14 + macd），若不做特征工程注入直接 `fit()` 训练 XGBoost，**与生产特征分布不一致**，模型加载即失效
2. **内嵌 RSI/MACD 与 LeaderFeatureBuilder 重复实现**：`_fetch_features` 303-326 行手动 `rolling(15).apply(...)` 算 RSI，与 `LeaderFeatureBuilder` 的 `IndicatorRegistry.get("rsi")` 走不同代码路径，结果可能微差
3. **缺 `kdj_k` / `kdj_j` / `boll_pos`**：内嵌路径算不出这三个技术指标，而 `v_leader_features.TECH_COLS` 包含 5 个 → 训练时即使选 5 维特征，缺 3 维会显著影响 XGBoost split

### 1.3 现状评估

- ✅ `data_mgr` 真接已完成（2026-06-25 `Phase B4e`，commit 历史 1db2e9bf 系列）
- ✅ `LeaderFeatureBuilder` 已实现（5 维 + 实时算 + `IndicatorRegistry` 注入）
- ✅ `v_leader_features` 74 维 `FeatureBuilder` 是生产事实标准
- ❌ `AStockDataset.feature_builder` 默认 `None`，调用方必须显式构造 `LeaderFeatureBuilder()` 才能用
- ❌ `_fetch_features` 内嵌 RSI/MACD 与 `LeaderFeatureBuilder` 重复，且维度不全（5 → 实际 7 维但少 kdj/boll）

### 1.4 关联上下文

- ADR-0005 已确立 vnpy 命名前缀（`vt_symbol` / `vt_orderid`），`AStockDataset` 沿用
- ADR-0006 已收敛策略基类 4→2，`AStockDataset` 是 `EquityStrategy` 上游的数据抽象
- ADR-0007（已 Accepted）已落定 RiskEngine 下单前拦截，本 ADR 是其**上游数据源**的"训练-推理分布统一"补完
- TODO P3.2 明确 1-2d 估时，"中"风险

## 2. 决策（Decision）

### D1. 默认行为：默认 5 维 LeaderFeatureBuilder（开箱即用）

**决策**：`AStockDataset.__init__()` 默认 `feature_builder=LeaderFeatureBuilder()`，5 维技术指标 + OHLCV = **10 维总特征**（其中 5 维技术指标与 `v_leader_features.TECH_COLS` 严格一致）

**理由**：
- 5 维 + OHLCV = 10 维，能覆盖"快验证 / 单元测试 / 教学"场景
- 与 `v_leader_features.TECH_COLS` 严格一致 → 5 维子集训练结果可被生产 74 维模型加载时**用前 5 维做 partial match**（XGBoost 友好）
- `LeaderFeatureBuilder` 是**实时算**而非 DB 查表 → 不依赖 `technical_indicators` 表存在，**单测无需 DB fixture**

### D2. 删除 `_fetch_features` 内嵌 RSI/MACD（统一入口）

**决策**：删除 `src/research/dataset.py:301-326`（44 行内嵌实现），改为**统一走 `feature_builder.build()`**

**理由**：
- 消除两套 RSI/MACD 实现路径（_fetch_features 内嵌 vs LeaderFeatureBuilder）
- `LeaderFeatureBuilder.build()` 内部已包含完整 5 维 + KDJ 状态重置 + Boll clamp 边界
- 内嵌 `rolling.apply(lambda)` 性能差（每窗口 1 次 IndicatorRegistry 查表），实时算路径用 NumPy 向量化更快

**影响**：现有 `_fetch_features` 行为变更，但默认行为**升级**（5 维 → 实际 10 维 OHLCV + 5 技术指标）

### D3. feature_builder 默认 = LeaderFeatureBuilder()

**决策**：`AStockDataset.__init__` 的 `feature_builder: Optional[Any] = None` 改为 `feature_builder: Optional[Any] = Field(default_factory=LeaderFeatureBuilder)`

**理由**：
- **开箱即用**：调用方 `AStockDataset(lookback=20, horizon=5)` 即可拿到 10 维训练样本，无需记忆"必须传 feature_builder"
- **显式覆盖仍可能**：传 `feature_builder=v_leader_features.FeatureBuilder(engine)` 拿 74 维
- **彻底禁用法**：传 `feature_builder=None` 拿纯 OHLCV 5 维（向后兼容 test 桩）

### D4. 训练分布一致性策略：同源实时算

**决策**：训练 / 推理**同走 `LeaderFeatureBuilder.build()`**，**禁止**混用 `v_leader_features._load_tech_features`（DB 查表）

**理由**：
- `v_leader_features._load_tech_features` 从 `technical_indicators` 查（预计算，离线脚本生成），与 `LeaderFeatureBuilder` 实时算结果在 KDJ 边界 / Boll 归一化上**可能微差**（DB 写入时的 close 与实时 close 的尾数差）
- 同源后 → 训练用 `fit()` 拉的数据，推理用 `predict()` 拉的数据，**逐 bit 一致**
- 兜底：单测可用 `feature_builder=None` 拿 OHLCV 5 维（仅测试，生产禁用）

## 3. 备选方案（Alternatives Considered）

### D1 备选：默认特征行为

#### 方案 A：默认 5 维 LeaderFeatureBuilder（已选）
- 优点：开箱即用；与 `v_leader_features.TECH_COLS` 严格一致；实时算不依赖 DB
- 缺点：默认行为升级（5 维 → 10 维），可能破坏 `_mock_features` 路径的旧测试期望
- 否决理由：不适用（已选）

#### 方案 B：默认 74 维 v_leader_features.FeatureBuilder
- 优点：与生产 v_leader 策略**100% 一致**；无需调用方额外配置
- 缺点：74 维需要 engine (DB) + 8 个 Scorer 初始化，**默认构造耗时 >2s**；单测全部需要 DB fixture；阻塞 P3.2 1-2d 估时
- 否决：v2.2 阶段研究层不应强依赖生产 scorer，留作 D1 备选未来扩展

#### 方案 C：保持现状（feature_builder=None 默认 5 维）
- 优点：零行为变更
- 缺点：5 维少 kdj/boll，与生产 74 维分布不一致；调用方需记忆"必须传 feature_builder"
- 否决：直接违反 TODO P3.2 目标

---

### D2 备选：内嵌 RSI/MACD 处理

#### 方案 A：删除内嵌，统一入口（已选）
- 优点：单一来源；性能更好；代码行数 -44
- 缺点：行为升级（默认 5 → 10 维）
- 否决理由：不适用（已选）

#### 方案 B：保留内嵌，向后兼容
- 优点：旧测试零修改
- 缺点：两套实现路径长期共存，新人不知道用哪个；`rolling.apply` 性能差
- 否决：违反"单一入口"原则

#### 方案 C：标记 deprecated，保留 1 版本再删
- 优点：渐进式迁移
- 缺点：研究层非生产路径，**不必渐进**；当前 65 测试 + 单测 fixture 可一次性覆盖
- 否决：过度工程

---

### D3 备选：feature_builder 默认值

#### 方案 A：默认 `LeaderFeatureBuilder()` 实例（已选）
- 优点：开箱即用；调用方代码最少
- 缺点：每次 `AStockDataset()` 构造会创建 `IndicatorRegistry.get(...)` 4 个算子（一次性 ~1ms）
- 否决理由：不适用（已选）

#### 方案 B：默认 `None`（显式注入）
- 优点：构造最轻；调用方控制力最强
- 缺点：调用方必须懂"必须传 feature_builder"；违反"开箱即用"原则
- 否决：违反 TODO P3.2 "默认集成"语义

#### 方案 C：工厂模式 `feature_builder_factory: Callable[[], FeatureBuilder] = LeaderFeatureBuilder`
- 优点：构造延迟（lazy）；可注入 mock 工厂
- 缺点：API 复杂化；v2.2 研究层无此需求
- 否决：v3.0 再考虑

---

### D4 备选：训练分布一致性策略

#### 方案 A：同源实时算（已选）
- 优点：训练/推理逐 bit 一致；不依赖 `technical_indicators` 表存在
- 缺点：`LeaderFeatureBuilder` 每次构造 KDJ 状态需 `reset_kdj=True`（已实现）
- 否决理由：不适用（已选）

#### 方案 B：双套对齐（LeaderFeatureBuilder 与 v_leader_features._load_tech_features 跑同一只股票对比）
- 优点：能发现微差
- 缺点：成本高（DB fixture + 离线指标生成）；结论只是"接近"，无法消除
- 否决：违反"训练/推理必须同源"原则

#### 方案 C：文档化两条路径并存
- 优点：零成本
- 缺点：长期隐患；v3.0 实盘时仍要面对分布不一致
- 否决：延后问题不解决

---

### 最终选择（实施计划）

| 决策点 | 选哪个 | 实际落地 |
|---|---|---|
| 默认特征 | **D1-A**（5 维 LeaderFeatureBuilder） | `AStockDataset()` 默认 10 维（OHLCV + 5 技术） |
| 内嵌 RSI/MACD | **D2-A**（删除内嵌） | `src/research/dataset.py:301-326` 删 44 行 |
| feature_builder 默认 | **D3-A**（`default_factory=LeaderFeatureBuilder`） | `__init__` 默认实例化 |
| 训练分布一致性 | **D4-A**（同源实时算） | 训练/推理都走 `feature_builder.build()` |

**未选方案 B（D1 备选 74 维）**：保留为 P3.x 后续任务，单列 issue

## 4. 后果（Consequences）

### 正面
- **训练/推理分布统一**：XGBoost 模型训练用 `fit()`（10 维），生产 v_leader 推理取前 5 维技术指标，partial match 兼容
- **简化 `_fetch_features`**：删除 44 行内嵌实现，行为升级由 `LeaderFeatureBuilder` 单点负责
- **单测不再依赖 DB**：`LeaderFeatureBuilder` 实时算，`technical_indicators` 表不需要 fixture
- **v_leader 走通 AStockDataset**：TODO P3.2 完成，研究层 / 策略层有共同数据抽象

### 负面
- **默认行为变更**：5 → 10 维（OHLCV + 5 技术指标），现有 `tests/test_research_dataset.py` 期望可能需更新
  - 缓解：提供 `feature_builder=None` 显式降级路径，单测可改 fixture 维持向后兼容
- **74 维路径需显式构造**：生产 v_leader 走 `feature_builder=v_leader_features.FeatureBuilder(engine)`，**不是默认**
  - 缓解：v_leader 策略类构造时显式注入，README 标注
- **LeaderFeatureBuilder 内部 KDJ 状态机**：`reset_kdj=True` 跨股票重置，**单测需注意**（已在 v1 实现中标注）
  - 缓解：现有 `tests/test_leader_features.py` 已有 reset 测试

### 风险
- **`feature_builder.build()` 失败回退**：单股失败 → 整只股票无技术指标
  - 缓解：`LeaderFeatureBuilder.build()` 内部已 try/except + `TECH_DEFAULTS` 兜底（`leader_features.py:84-90`）
- **KDJ 状态跨股票重置**：批量算时若忘记 `reset_kdj=True` → 上一只股票的 K/D 值泄漏
  - 缓解：`LeaderFeatureBuilder.build_batch` 第 149 行已 `reset_kdj=True` 强制重置
- **数据源失败时 mock 路径**：`_mock_features` 仍存在（`dataset.py:342-360`），生产已不调用，但**单测 fallback 路径仍会触发** → 需确认 mock 路径兼容新默认
  - 缓解：单测明确 mock `data_mgr.datafeed.get_bars` 抛异常，验证 mock 路径输出 10 维（含默认 rsi=0.5, kdj=0.5, boll=0.5, macd=0.0）
- **`LeaderFeatureBuilder` 与 `v_leader_features._load_tech_features` 微差**：KDJ `reset_kdj` 时机不同 / Boll 边界 clamp 不同
  - 缓解：D4-A 明确"训练推理同源" → 两者不混用；`v_leader_features` 自身有 DB 查表路径，本 ADR 不动其 74 维主路径

## 5. 实施（Implementation）

| 阶段 | 行动 | 关联 issue |
|---|---|---|
| **Step 1** | 删除 `src/research/dataset.py:301-326`（44 行内嵌 RSI/MACD 实现），改用 `feature_builder.build()` 统一入口 | #78 |
| **Step 2** | `AStockDataset.__init__` 改 `feature_builder: Optional[Any] = None` → `default_factory=LeaderFeatureBuilder`（D1 + D3） | #78 |
| **Step 3** | 加 `tests/test_research_dataset.py::test_default_uses_leader_feature_builder` —— 验证 `AStockDataset()` 默认产出 10 维（OHLCV + 5 技术指标），且技术指标列名与 `v_leader_features.TECH_COLS` 严格一致 | #78 |
| **Step 4** | 加 `tests/test_research_dataset.py::test_74dim_via_v_leader_features` —— 验证显式传 `feature_builder=v_leader_features.FeatureBuilder(engine)` 产出 74 维（需 DB fixture 或 mock engine） | #78 |
| **Step 5** | 守门：65 测试仍全绿；`dev_tools/hooks/check_naming.py` 无新违例；`docs/CODE_WIKI.md` §研究层 增加 AStockDataset 默认行为说明 | #78 |

**约束**：
- Step 1 与 Step 2 必须在同一 commit（行为升级原子化）
- Step 3 测试必须先**跑失败**再改 Step 1-2（红绿循环，避免"测试通过但行为未升级"）
- Step 4 测试**不在本 ADR 强制**——74 维路径依赖 `v_leader_features.FeatureBuilder` 完整 mock，可作 P3.x 后续

## 6. 关联

- 反对 / 推翻：无
- 关联 issue：#78（VNPY-3 P3.2 v_leader 真接 AStockDataset）
- 关联 ADR：
  - ADR-0005（vnpy 命名前缀 → `AStockDataset` 沿用 `vt_symbol`）
  - ADR-0006（策略基类收敛 → `AStockDataset` 是 `EquityStrategy` 上游数据抽象）
  - ADR-0007（RiskEngine 完善 → 本 ADR 是其上游数据源"训练-推理分布统一"补完）
- 实施入口：`src/research/dataset.py` + `src/research/features/leader_features.py`
- 守门：65 测试 + `dev_tools/hooks/check_naming.py`
- 关联文件（不动）：`src/strategies/v_leader_features.py`（74 维主路径保留，本 ADR 仅补研究层默认）

## 7. 备注

**为什么不直接把 v_leader_features 74 维默认集成？**

- 74 维需 `engine` (DB) + 8 个 Scorer 初始化，**默认构造 >2s**
- 研究层（`AStockDataset`）常被单测调用 → 单测全部要 DB fixture
- 5 维 LeaderFeatureBuilder 是"够用且快"的折中：实时算不依赖 DB，0 依赖构造 <10ms
- 74 维是"全量生产路径"，**显式注入**符合"按需加载"

**为什么 LeaderFeatureBuilder 而不是 v_leader_features 内嵌路径？**

- v_leader_features._load_tech_features 依赖 `technical_indicators` 表（252MB 预计算，2026-06-18 LFS 导入）
- LeaderFeatureBuilder 实时算，**与训练时 XGBoost split 完全同源**（都用 `IndicatorRegistry`）
- v_leader_features 走 DB 查表 → 训练时若 DB 写入时的 close 与实时 close 尾数不同 → 微差 → 模型加载时分布漂移
- D4-A 同源策略**只允许 LeaderFeatureBuilder** → v_leader_features 74 维主路径保留（生产不动），但研究层默认走 LeaderFeatureBuilder

**为什么不删 v_leader_features？**

- v_leader_features 74 维含 8 个 Scorer（fund_flow/institutional/sentiment/...），是**生产 v_leader 策略的事实标准**
- 删除会破坏 9 个生产策略的继承链，**超出 P3.2 范围**
- 本 ADR 仅在 `AStockDataset` 层面"默认用 LeaderFeatureBuilder"，**不动** v_leader_features 572 行
- 长期：v3.0 可考虑把 v_leader_features 的 74 维 FeatureBuilder 拆出"研究层版本"（无需 engine），但不在本 ADR 范围
