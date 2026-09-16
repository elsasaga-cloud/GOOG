# SANDBOX_RECOVERY_PROTOCOL · 沙箱 git 元数据重置备案（#107 立档 2026-09-15）

> 依据：宪法（AGENT-CONSTITUTION.md）附注 10=已授权例外。本文档=该预案的**操作手册细化**；两者冲突以宪法为准。
> 状态：**八犯实录 · 零丢失**。任何 agent 遇到本症状必须走本预案，禁止自行发明修法。

---

## 一、症状识别（满足任一即触发）

1. `git log -1` 显示 HEAD = fork 点 `a9b0d1e`（或明显早于上轮已知 tip）；
2. `git status` 冒出几十~上百个**本轮未触碰**的 M / D / ?? 文件；
3. `git mv` / `git rm` 报 `not under version control`（文件明明在磁盘上）；
4. `git log` 里上轮 commit 消失，但远程 `git ls-remote` 还在。

**判别金标准**：`git ls-remote origin <工作分支>` vs 本地 `git rev-parse HEAD`——两者不一致且本地偏旧 = 已中招。

## 二、恢复预案（宪法附注 10 标准六步 · 实测每次奏效）

```bash
# 1. 显式 refspec fetch（禁裸 fetch）
git fetch origin arena/01a057f3-ai-quant-script-hpe
# 2. 核对 FETCH_HEAD == 上轮已知远程 tip
git rev-parse --short FETCH_HEAD
# 3. mixed reset（必须 mixed；#87 教训：soft 会留 index 假象/staged 删除幻影）
git reset -q FETCH_HEAD
# 4. 核对：只剩当轮未提交文件 + 上轮文件全部回"已提交净"状态；磁盘文件零丢失
git status --short
# 5. 重做当轮未完成动作 → 提交
git add -A && git commit -m "..."
# 6. push + 提交内容抽验（#105 教训：commit ≠ 内容落盘）
git push origin arena/01a057f3-ai-quant-script-hpe
git show HEAD:<关键文件> | grep <关键标记>
```

## 三、八犯实录（历史只增不删）

| # | 轮次 | 备注 |
|---|---|---|
| 1-4 | #34 / #78 / #87 / #89 | 同型重置；预案在此期间成型（soft reset index 陷阱在 #87 定性） |
| 5 | 0bfe77d 丢失 | 2026-09-02 确认根因：沙箱快照回滚只保工作区文件，远程无此对象 |
| 6 | #99 | git 元数据重置 + 脚本 rw 未定义漏改指针 |
| 7 | #100 | 连续同型 |
| 8 | #106 | HEAD 打回 a9b0d1e·远程 5fb39de 完好·磁盘全在 → 六步恢复后 redo 搬迁，零丢失 |

（另有 #105 非重置型故障：**平台编辑写入竞态**——同文件多点并行编辑 5 丢 1 损（头部 U+FFFD）；对策=同文件多点编辑禁并行 batch + 写后 grep 验证 + git show 抽验。）

## 四、预防清单（每轮纪律）

1. **每轮必 push**——远程 tip=唯一连续性锚（交接指令 Step 4 已固化）；
2. 新会话第一动作=git status/log + ls-remote 对齐远程头（宪法第二条）；
3. commit 后 `git show` 抽验关键标记（不信任空 commit/半 commit）；
4. 批量动作（mv/rm/重构）前先 `git status --short` 快照对照；
5. **同文件多点编辑必须串行**（单脚本 replace+assert 或逐个 edit_file），禁并行 batch；
6. 重要脚本/中间产物即时入库（/tmp 跨会话即焚——build_ops_panel #91 教训）；
7. 每轮 log 写流程注记（犯型/恢复/丢失数）——账本闭环；
8. reset/force push 类操作仍须用户确认；本预案的 mixed reset=已授权例外（附注 10），不需逐次确认。

## 五、数据层同型备案（行情源不可达）

- stooq 实录（#103）：curl `SSL_ERROR_SYSCALL` / fetch 404 / `Access denied`；Yahoo query1 chart curl 空；
- **取数顺位**：官方 OHLC（约束 29）→ 沙箱源全挡时用官方页快照（fetch_page stockanalysis）+ web_search 交叉 → 入库必标**来源+时点**；
- daily CSV=唯一事实源：补数据**先补 CSV 再构建**，禁面板/builder 层补根（#104 勘误第七例）；
- 数据时效（#103 用户钦定）：已收盘必须拉最新官方收盘，不得停留旧交易日（成文=profile §16）。
