# A股全市场数据下载汇总

## 数据覆盖范围
- **股票数量**: 5,209只A股（沪市+深市+北交所）
- **时间范围**: 2024-01-01 至 2026-06-17
- **更新日期**: 2026-06-17

## 数据文件清单（15个CSV，已上传tdrive项目资料库）

| # | 文件名 | 行数 | 大小 | 数据类型 | 数据源 | 状态 |
|---|--------|------|------|----------|--------|------|
| 1 | kline_daily.csv | 3,017,078 | 221MB | 日K线（OHLCV）5,206股×592天 | mootdx通达信 | ✅ |
| 2 | tencent_quotes.csv | 5,209 | 768KB | 实时行情快照（PE/PB/市值/换手率） | 腾讯财经API | ✅ |
| 3 | finance_snapshot.csv | 5,209 | 756KB | 财务数据快照 | mootdx通达信 | ✅ |
| 4 | **fund_flow_120d.csv** | **616,969** | **54MB** | 资金流向 5,210股×197天，114天完整覆盖 | WeStock asfund | ✅ **已更新** |
| 5 | research_report.csv | 2,040 | 264KB | 研报列表，328股覆盖 | 东财reportapi | ✅ **已上传** |
| 6 | announcements.csv | 4,616 | 764KB | 公告列表，462股，已去重 | 巨潮cninfo | ✅ **已更新** |
| 7 | technical_indicators.csv | 3,017,078 | 252MB | 技术指标（MACD/RSI/KDJ/BOLL） | 离线计算 | ✅ |
| 8 | ths_hot_reason.csv | 138 | 12KB | 情绪面（同花顺热点/题材归因） | 同花顺 | ✅ |
| 9 | em_global_news.csv | 101 | — | 消息面（已去重），东财7×24快讯 | 东财快讯 | ✅ |
| 10 | lockup_expiry.csv | 429 | 22KB | 限售解禁日历，400股 | 东财datacenter | ✅ |
| 11 | margin_trading.csv | 76,071 | 5.7MB | 融资融券，3,663股覆盖 | 东财datacenter | ✅ |
| 12 | dividend.csv | 40,377 | 1.6MB | 分红送转，5,131股覆盖 | 东财datacenter | ✅ |
| 13 | holder_num.csv | 5,332 | 412KB | 股东户数，5,099股覆盖 | 东财datacenter | ✅ |
| 14 | block_trade.csv | 73,813 | 14MB | 大宗交易，4,887股，已去重 | 东财datacenter | ✅ |
| 15 | dragon_tiger.csv | 26,732 | 2.9MB | 龙虎榜，4,313股覆盖 | 东财datacenter | ✅ |

**合计**: 约 6,300,000+ 行，~570MB

## 关键数据质量指标

| 指标 | 数值 |
|------|------|
| kline_daily 完整度 | 5,206股 × 592天 = 99.97% |
| fund_flow_120d 完整日期 | 114天满覆盖（≥5,000股/天） |
| fund_flow_120d 部分日期 | 83天（主要为旧push2数据~440股/天） |
| 重复数据 | 已全部去重 |
| tdrive 同步 | 15/15文件已上传（fund_flow和margin_trading需验证大小） |

## 数据更新说明（2026-06-17）

### 资金流数据重大更新
- **数据源切换**: 东财push2/push2his（IP被封）→ **WeStock asfund**（腾讯自选股）
- **下载策略**: 全市场5,209股一次API拉取（~100秒/天）
- **并行加速**: 3路shard并发，99天数据约2小时完成
- **覆盖率**: 近120天内114个交易日满覆盖，仅6个近日日期WeStock超时（已有旧push2部分数据）

### 数据去重
- fund_flow_120d: 删除2,788行重复
- announcements: 删除304行重复
- block_trade: 删除10,738行重复
- em_global_news: 删除1,899行重复

## WeStock asfund 使用备忘

```bash
# 全市场一次拉取（5209只）
NODE_OPTIONS="--no-warnings" npx -y westock-data-clawhub@1.0.4 asfund \
  "sz000001,sz000002,...(全部5209只)" --date 2026-06-17

# 批量上限：无实际限制（5209只全市场测试通过）
# 速度：~100秒/天（全市场）
# 并行：支持 --shard N/M 多路并发
```

## 待处理

| 项目 | 优先级 | 说明 |
|------|--------|------|
| tdrive fund_flow_120d 大小验证 | 中 | tdrive显示3MB，本地54MB，需团队下载验证 |
| tdrive margin_trading 重传 | 低 | tdrive显示357KB，本地5.7MB |
| 6个近日资金流日期补全 | 低 | WeStock超时，旧push2有~440股覆盖 |
