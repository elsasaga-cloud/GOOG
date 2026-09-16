# 盘前监控运行环境与配置记录

## 当前配置方式（v7.2）

`premarket_monitor.py` 按以下优先级解析报告目录：

```text
1. 环境变量 HPE_PREMARKET_REPORT_DIR
2. 仓库同目录 premarket_config.json 的 report_dir
3. Windows默认目录：%USERPROFILE%/Desktop/Stock Data Retrivel/Tech monitor 30min
4. 非Windows：仓库下 output/
```

推荐换电脑时使用环境变量，不改代码：

```powershell
$env:HPE_PREMARKET_REPORT_DIR = "D:\\HPE Data\\Tech monitor 30min"
python premarket_monitor.py --once
```

也可以编辑 `premarket_config.json`：

```json
{
  "report_dir": "D:/HPE Data/Tech monitor 30min",
  "earnings_dates": ["2026-09-02", "2026-12-02"],
  "earnings_window_days": 7,
  "archive_history": true
}
```

空字符串 `report_dir` 表示使用默认路径。JSON中建议使用正斜杠，避免Windows反斜杠转义问题。

## 旧版本记录（迁移参照，不再硬编码）

v7.1以前的Windows路径是：

```text
C:\\Users\\simon\\Desktop\\Stock Data Retrivel\\Tech monitor 30min
```

旧路径仅作为历史参照。新代码使用 `Path.home()` 推导用户名，不再把 `simon` 写死；若需要完全迁移旧目录，设置 `HPE_PREMARKET_REPORT_DIR` 指向上面的旧路径即可。

## 输出文件

| 文件 | 保留策略 |
|---|---|
| `report_YYYYMMDD_HHMM.txt` | 当天只保留当前报告（旧版行为） |
| `today_history.json` | 当天盘前轮询趋势 |
| `history_archive.jsonl` | 跨日永久追加，长期回溯使用 |
| `monitor.log` | 运行日志 |

`history_archive.jsonl` 不参与清理。若需要迁移电脑，复制整个报告目录即可。

## 配置原则

- `premarket_config.json` 不放API key；API key仍使用未入库的 `premarket_keys.json`；
- 配置文件、输出目录和代码目录可以分离；
- 每次调整路径或财报日期后，在 `CHANGELOG.md` 记录原因；
- 盘前监控是市场情绪辅助层，不改变 `thesis.md` 的长期状态。


> [2026-09-10 #57] `monitoring/` 旧位置副本已归档至 `archive/monitoring_20260910/`；现行正本=根目录 `premarket_monitor.py`（本文件路径解析说明对应根目录版）。
