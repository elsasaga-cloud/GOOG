# HPE 2026-Q1 季度包合集（6 文件原样合并）

> 合并文件（v1.2.1 瘦身）：原 6 个独立文件按序合并，内容原样保留。



---

<!-- 原文件: 90_reference_HPE/reports/2026-Q1/README.md -->


# HPE FY26 Q1 季度研究包

**对应财报：** FY26 Q1（截至2026-01-31）  
**正式发布日期：** 2026-03-09  
**原始资料：** `sources/HPE/2026-Q1/source_index.md`

| 文件 | 用途 |
|---|---|
| `00_source_index.md` | 原始资料入口 |
| `01_raw_facts.md` | 事实层 |
| `02_operating_scorecard.md` | 经营评分卡 |
| `03_thesis_and_forecast.md` | 论点、预期与Q2验证 |
| `04_decision_review.md` | 决策复盘入口 |

这是2026年度长期资产的第一份季度包。Q2包见 `reports/HPE/2026-Q2/`。



---

<!-- 原文件: 90_reference_HPE/reports/2026-Q1/00_source_index.md -->


# FY26 Q1 资料入口

原始资料索引：`sources/HPE/2026-Q1/source_index.md`  
官方财报新闻稿：https://www.hpe.com/us/en/newsroom/press-release/2026/03/hpe-reports-fiscal-2026-first-quarter-results.html  
结构化事实：`data/HPE/fundamentals_quarterly.csv`



---

<!-- 原文件: 90_reference_HPE/reports/2026-Q1/01_raw_facts.md -->


# FY26 Q1 事实层

> 数据来自 HPE 官方 Q1 FY26 earnings release；事实、判断、预测分开保存。

| 事实 | 数值 | 口径 | 官方来源 |
|---|---:|---|---|
| Q1营收 | $9.3B | reported | 官方Q1 earnings release |
| Q1同比增长 | +18% | reported | 官方Q1 earnings release |
| GAAP毛利率 | 35.9% | reported | 官方Q1 earnings release |
| Non-GAAP毛利率 | 36.6% | reported | 官方Q1 earnings release |
| GAAP EPS | $0.31 | reported | 官方Q1 earnings release |
| Non-GAAP EPS | $0.65 | reported | 官方Q1 earnings release |
| 经营现金流 | $1.2B | reported | 官方Q1 earnings release |
| FCF | $0.7B | reported | 官方Q1 earnings release |
| Networking收入 | $2.7B | reported | 官方Q1 earnings release |
| Networking同比 | +151.5% | reported（并表口径） | 官方Q1 earnings release |
| Networking normalized增长 | +7% | normalized | 官方Q1 earnings call摘要/公司披露 |
| Networking operating margin | 23.7% | reported | 官方Q1 earnings release |
| Cloud & AI收入 | $6.3B | reported | 官方Q1 earnings release |
| Cloud & AI同比 | -2.7% | reported | 官方Q1 earnings release |
| Cloud & AI operating margin | 10.2% | reported | 官方Q1 earnings release |
| AI网络累计订单目标 | $1.7B-$1.9B | FY26目标 | 公司Q1披露 |
| FY26营收增长指引 | 17%-22% | reported guidance | 公司Q1披露 |
| FY26 Networking增长指引 | 68%-73% | reported guidance | 公司Q1披露 |
| FY26 Non-GAAP EPS指引 | $2.30-$2.50 | guidance | 公司Q1披露 |
| FY26 FCF指引 | ≥$2.0B | guidance | 公司Q1披露 |
| Q2营收指引 | $9.6B-$10.0B | guidance | 公司Q1披露 |
| Q2 Non-GAAP EPS指引 | $0.51-$0.55 | guidance | 公司Q1披露 |

## 事实与解释边界

Q1事实显示 Networking 是主要亮点，Cloud & AI收入仍受AI系统交付时点影响；Juniper并表使 Networking reported growth 不可直接等于有机增长。



---

<!-- 原文件: 90_reference_HPE/reports/2026-Q1/02_operating_scorecard.md -->


# FY26 Q1 经营跟踪评分卡

| 模块 | Q1事实 | 状态 | Q2验证问题 |
|---|---|---|---|
| Juniper整合 | 公司称协同快于计划；Networking margin 23.7% | 加强但早期 | margin能否保持，协同是否继续 |
| Networking | $2.7B收入；normalized +7%；DCN、routing强 | 加强 | 订单能否继续超过收入 |
| AI/服务器 | Cloud & AI收入-2.7%；AI交付偏后半年度；AI订单需求强 | 需求强、收入时点待验证 | AI收入与毛利是否兑现 |
| FCF | Q1 FCF约$0.7B，FY26指引上调至≥$2.0B | 加强 | Q2现金流是否继续改善 |
| 有机增长 | 总营收+18%，并表影响明显 | 待验证 | normalized增长是否扩大 |
| 资产负债表 | Juniper收购后的杠杆和商誉风险存在 | 警示 | 去杠杆和整合费用 |
| 管理层 | 上调Networking、EPS、FCF指引 | 正面 | 指引达成率 |

## Q1阶段结论

Q1是“Networking先行、AI交付待兑现”的季度；它首次提供了Juniper整合和网络利润率的早期证据，但不能仅凭Q1判断HPE已完成转型。



---

<!-- 原文件: 90_reference_HPE/reports/2026-Q1/03_thesis_and_forecast.md -->


# FY26 Q1 论点、预期与Q2验证

## Q1后论点状态

| 论点 | 状态 | Q1证据 |
|---|---|---|
| T1 网络成为利润引擎 | ✅早期加强 | Networking margin 23.7%，超过指引；Juniper协同早期兑现 |
| T2 有机增长站稳双位数 | ➖ | Networking normalized +7%，总增长含并表 |
| T3 FCF修复 | ✅初步加强 | Q1 FCF约$0.7B，FY26指引≥$2.0B |
| T4 AI订单持续且不牺牲毛利 | ➖/待验证 | AI需求和网络订单强，但AI收入交付偏后半年度 |
| T5 去杠杆 | ➖ | 收购杠杆尚处整合期 |

## Q2预设

- Q2营收：$9.6B-$10.0B；
- Networking reported growth：142%-152%；
- FY26 FCF至少$2.0B；
- AI系统收入环比增加，但交付节奏和毛利需要验证；
- 网络margin维持低20%区间。

## Q1的核心判断

Q1加强了“Juniper让Networking成为HPE重要利润引擎”的可能性，但尚未证明AI业务和有机增长可以长期抬高公司的增长轨道。



---

<!-- 原文件: 90_reference_HPE/reports/2026-Q1/04_decision_review.md -->


# FY26 Q1 决策复盘入口

## Q1→Q2预期记录

| 项目 | Q1后预期 | Q2实际 | 是否验证 |
|---|---|---|---|
| 总营收 | $9.6B-$10.0B | 见Q2包 | 待回填 |
| 网络业务 | 高增长，margin维持低20% | 见Q2包 | 待回填 |
| AI/Cloud & AI | 收入改善，交付偏后半年 | 见Q2包 | 待回填 |
| FCF | FY26≥$2.0B | 见Q2包 | 待回填 |
| Juniper协同 | 按计划或提前 | 见Q2包 | 待回填 |

## 复盘规则

Q2包的 `03_thesis_and_forecast.md` 和本文件共同构成 Q1 预期→Q2实际的复盘链。不要根据Q2结果反向修改Q1预期。
