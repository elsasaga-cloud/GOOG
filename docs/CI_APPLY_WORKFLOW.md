# 唯一需要手动执行的一步：覆盖 workflow 文件

Arena 的 GitHub App **没有 `workflows` 权限**，无法创建/修改 `.github/workflows/**`
（`git push` 与 Contents API 均返回 403 `Resource not accessible by integration`）。
因此 workflow 文件本身必须由**有权限的账号**写入。

## 操作步骤（约 1 分钟）

1. 打开 https://github.comelsasaga-cloud/GOOG/edit/main/.github/workflows/goog_crawl.yml
2. 用本目录 [`goog_crawl.daily.yml`](./goog_crawl.daily.yml) 的**全部内容**替换文件内容
3. Commit 到 `main`
4. 打开 Actions 页 → `GOOG Daily Crawl` → **Run workflow** 验证

## 为什么必须改

当前 `main` 上的 workflow 有 **3 个必然导致失败**的问题（已实测确认）：

| # | 问题 | 后果 |
|---|------|------|
| 1 | 调用 `scripts/GOOG/gen_full_panel.py` | 该文件此前**不存在** → 步骤直接失败 |
| 2 | `git push origin main` | `actions/checkout@v4` 是 **detached HEAD**，无本地 `main` 分支 → `error: src refspec main does not match any` |
| 3 | 旧版用 `\|\| echo "push failed"` 兜底 | 吞掉错误 → **Actions 显示绿色但什么都没提交** |

本 PR 已修复 #1（新增生成器）。#2/#3 需要上面的 workflow 覆盖才能生效。

## 相对旧版的具体改动

- `git push origin HEAD:main` —— 修复 detached HEAD 推送
- 新增独立断言步骤：最新面板 `<h2>` 必须 **恰好 27**，否则 job 失败
- `grep` 通配改为 `GOOG_OPERATION_PANEL_${DATE}*.html`
  （新日期首个版本 V1 **没有 `_V` 后缀**，旧通配 `*_V*.html` 匹配不到）
- 爬虫步骤 `continue-on-error: true` —— 网络失败不再阻断面板生成
- `persist-credentials: true` —— 否则 push 无凭据
- `concurrency` 组 —— 防止并发 run 互相 push 冲突
- 移除所有 `|| echo ...` 静默兜底
