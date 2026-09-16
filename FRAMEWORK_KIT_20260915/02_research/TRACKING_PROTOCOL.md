# HPE 长期跟踪协议（Tracking Protocol）v1.0

> 确立：2026-09-05，用户要求"综合、深度、全方位的长期跟踪 + 时间戳记忆 + 逻辑变化检测 + 生态/同行联动"（重仓在身，专业执行）。
> 分工：本文件=**每轮对话怎么跑（OS）**；`HPE_LONG_TERM_TRACKING_FRAMEWORK.md`=**跟踪什么（A-G 模块）**；`AGENT-README.md`=会话快照；`HANDOFF.md`=深度交接；`journal/logic_baseline.md`=活性判词基线（diff 记忆）。

## 一、会话协议（每次深度对话的标准动作）

1. **加载**：AGENT-README → HANDOFF §六 → `journal/logic_baseline.md`（13 条活性判词）
2. **对账**：positions.py vs 用户口述（规矩19 口述为准）；decision_log 尾部未回填判据逐条核
3. **快照**：收盘/MA/ATR/IV/异常新闻 → 与 baseline 有实质差异才记 decision_log，无变化不制造噪音
4. **逻辑 diff**：对照 baseline 逐维判——变化处打 `[变更 日期]`、写三件套（变化内容/触发证据/影响动作），旧值进变更史
5. **时间戳纪律**：所有新记录带北京时间日期+当日收盘锚；判据回填带触发日；引用历史判词必查是否已被勘误（例：Q2"+30%"已勘误为两日+17.3%，#13）

## 二、生态雷达（合作方 → HPE 读法映射）

| 对象 | 关系 | 跟踪点 | 对 HPE 读法 |
|---|---|---|---|
| **NVIDIA** | 认证 OEM（GB200 系；Vera Rubin 名单细节**待核**） | NVDA 财报（11 月中预期）、新平台 ramp | 平台节奏=AI 系统收入β；强=顺风，暴雷=全板块流弹 |
| **Oracle** | Q3 电话会宣布协作扩展（AI 数据中心） | **ORCL 财报 ~9/10（最近事件，待确认）**：capex 指引与 AI 订单 | hyperscaler capex 前瞻指标→网络+服务器订单预期 |
| **AMD** | Helios 未入 FY27 框架 | ramp 进度；**进框架=上修事件** | 免费期权，兑现=中枢上修窗口 |
| **Juniper** | 已并表（整合提前完成） | 协同兑现、网络 OM mid-high 20s | 转型②的心跳线 |

## 三、同行雷达（业绩/指引 → 读法映射）

| 对象 | 定位 | 财报窗口（预期） | 读法 |
|---|---|---|---|
| **DELL** | AI 服务器纯 β 对标（剧本早两季） | 11 月下旬（8/28 已过） | DELL beat=板块顺风+HPE 盘后 pop（1/29 先例 +4-6%）；miss=流弹（β1.44） |
| **SMCI** | 需求温度计（带财务治理折价） | 11 月（8 月已过） | 订单强→行业需求确认；其治理噪音不外溢判 HPE |
| **ANET/CSCO** | 网络叙事倍数锚 | CSCO 11 月中 | 网络估值锚：CSCO 20x+ vs HPE 网络仍按硬件倍数=折价来源之一 |

**读法铁律**：同行事件只改**节奏与情绪**（β 层），永远不改 HPE 论点本身（只被自家 T1-T5 改）；流弹日（板块无消息大跌 ≥4-5%）= 阶梯二档的候选触发（跌因甄别闸）+ 做T 候选日，不是恐慌日。

## 四、深度核查清单（每次财报/季度/重大事件，8 维全跑）

| 维 | 内容 | 载体 |
|--:|---|---|
| 1 | 论点 T1-T5 + 破坏阈值 | thesis.md |
| 2 | 三支柱（论点/折价/租金） | 教义 v1.1、#23 |
| 3 | 转型三轨（身份/利润结构/回报） | #22 |
| 4 | 趋势（MA/ATR/R8'/State） | #20 技术位 |
| 5 | 估值（中枢/折价/PE/PEG/FCF yield） | #19/#20 |
| 6 | 机构（PT 中枢≥65 判据/13F 变动） | #20 |
| 7 | 期权（IV/链/GEX/CSP 门槛） | #21、chip v5.2 |
| 8 | 筹码（四维仪重跑，财报后必跑） | scripts/chip_analysis_v5.2.py |

输出：decision_log 条目（预设判据）+ logic_baseline diff + 季度包归档 `reports/HPE/<quarter>/`。

## 五·五、链上侦察（见文末 §六）

## 五、滚动日历

见 `journal/logic_baseline.md` 待回填队列（9/9 CSP → 9/10 ORCL → 9/17 ex-div → 9 月底 10-Q → 10/15 TSM → 11 月 NVDA/13F/CSCO/DELL → **12/2 三道闸总闸**）。


## 六、链上侦察规程（Chain Recon SOP，2026-09-05 实测生效）

**触发**：用户说"拉链"或任何期权决策前。**管线**：`fetch_page` 抓 `https://cdn.cboe.com/api/global/delayed_quotes/options/HPE.json`（延时~15min，全链 IV/希腊/OI/量，62 块）→ 二分定位（单块~25 条目，到期升序×行权价升序；先探块看 symbol 前缀定 expiry，再线性扫至目标 K）→ 结合 logic_baseline/走势/量化输出**《链上决策单》五件套**：①IV 面板（ATM IV vs 模型假设）②backbone 核价（Oct16 P50 vs ≥$2.30）③rotator 核价（周权/次月 vs 0.40/1.00）④OI 墙/MP/skew 异常 ⑤三选一指令（开/等/禁）+对错判据。⑥**增量对比**【#56 增补】：OI/IV/量 vs 上一张快照的 Δ（基线=20260905 快照；磁钉/OI 墙是移动的，截面看不出趋势）；IV 行沉淀至 `data/HPE/iv_history.csv`（date,exp,strike,iv,oi）。**存档**：`data/HPE/HPE_options_chain_YYYYMMDD_cboe.md`（格式同 20260905 快照）。**边界**：延时 15min（精确挂单以券商实盘为准）；块定位成本高时只取目标腿；开仓前必须当日重拉（隔日快照 ±30% 内有效）。用户端补充：`chip_analysis_v5.2.py` 本机实时链（yfinance），两源互核。首战记录：9/5 实测成功，9/11+9/18 两到期全档到手，AAWS 引擎参数实盘验证（ATM IV 55%、9/18 P50=$1.30、P50 OI 6,095）。