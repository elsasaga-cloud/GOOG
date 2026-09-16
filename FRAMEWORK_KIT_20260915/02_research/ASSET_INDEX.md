# HPE 2026长期资产索引

## 研究总入口

1. `HANDOFF.md`
2. `docs/FRAMEWORK_TWO_EYES.md`
3. `docs/HPE_SESSION_FULL_COMPENDIUM_20260824.md`
4. `docs/HPE_LONG_TERM_TRACKING_FRAMEWORK.md`
5. `data/HPE/2026_baseline.md`

## 2026季度链

```text
reports/HPE/2026-Q1/
        ↓
reports/HPE/2026-Q2/
        ↓
reports/HPE/2026-Q3/（财报后建立）
        ↓
reports/HPE/2026-Q4/（财年后建立）
```

每个季度包固定分为：

```text
00_source_index
01_raw_facts
02_operating_scorecard
03_thesis_and_forecast
04_decision_review
05_two_eyes_fusion（建议）
```

## 原始档案

- `narrative/HPE_narrative_source_20260823.json`：206条历史AI对话原始叙事；
- `session_records/`：历史对话和账户档案；
- `archive_20260816_snapshot/`：8/16旧快照冷归档；
- `options_tactical/`：上传的期权战术旧版冷参考，不是当前事实源；
- `monitoring/`：上传的盘前监控旧版参考，不是当前v7.2实现。

## 正本规则

- 当前持仓正本：`options/positions.py`；
- 当前长期论点正本：`thesis.md`；
- 当前实际操作正本：`journal/decision_log.md`；
- 当前季度事实正本：`data/HPE/` + 对应 `sources/`；
- 历史文件只追加和归档，不静默删除；
- 重复文件不自动混合，先标明“当前版/历史版/上传原件”。


> [2026-09-10 #57] `options_tactical/` 已归档（正本=`options/`，见 archive/README.md）；新增数据资产：RS 周线 `data/HPE/rs_relative_strength_weekly_20260910.csv`、财报台账 `data/HPE/hpe_earnings_dates.csv`、基准周线/日线续更（SOXX/SPY *_20260910.csv）。
