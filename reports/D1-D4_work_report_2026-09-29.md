# FloodOps Agent D1–D4 工作报告

日期：2026-09-29  
工作目录：`C:\UCL\DissertationAgent`  
状态：**D4 已完成；D1–D3 的可运行回放 demo 保持不变。** 原 `reports/D1-D2_work_report_2026-09-29.md` 保留且未修改。此前的 D1–D3 报告不作为本次必须保留的交付，但未作删除。

## 1. 四天交付概览

| 日程 | 结果 | 主要文件 |
| --- | --- | --- |
| D1 | 定义 MVP 边界、盘点服务与数据库、备份原资产 | `docs/01_scope.md`、`docs/02_asset_inventory.md`、`backup/source_snapshot_2026-09-29/` |
| D2 | 归档预测只读访问；按历史时刻选择已生成、已入库且有效的 run | `app/archive.py`、`app/cli.py` |
| D3 | Pydantic 回放卡、固定问句、预测峰值、声纳观测双时间闸门、CLI demo | `app/replay.py`、`app/demo.py` |
| D4 | 可信来源目录、关键词＋元数据检索、证据定位、缺失/版本冲突拒答 | `knowledge/catalog.json`、`app/knowledge.py`、`app/knowledge_cli.py`、`docs/04_knowledge_sources.md` |

D3 已有可运行 demo：输入 `当时预测峰值是多少`，会从真实归档显示 **3.7832 m**、目标时间 `2026-08-03T16:15:00Z`、run_id、内部等级及“历史回放”标识。D4 另加一个独立的文档问答入口：输入 `项目内部 Watch 是什么`，返回内部阈值、原 README 文件/章节/行号和非官方警报提示。这两类问题目前**分开处理**：历史预测是结构化数据查询；术语解释是文档证据检索，不强行把所有问题放进 RAG。

## 2. 开工前备份与独立环境

在写任何 D4 文件前，先将 D1–D3 已有 `app/`、`docs/`、`reports/`、`tests/`、两份 README 和 `.gitignore` 复制到**新目录** `backup/d1_d3_snapshot_2026-09-29/`。快照 **21 个文件、80,083 字节**，逐文件与原件比对 SHA-256，**0 个不一致**。未再次复制 203 MB 的 D1 原资产备份或旧虚拟环境；它们仍独立存在。D4 新代码、目录和报告均在快照之外；没有编辑两个备份、D1–D3 原代码、原工作报告或原始项目。

新建 `.venv-d4/`（Python **3.12.14**），与 `.venv/`、`.venv-d3/` 分开。D4 检索实现仅用标准库；为在新环境运行全套 D1–D4 测试，另外安装与 D3 相同的 Pydantic **2.13.5** 及其依赖。初次安装遇到沙箱网络限制，经授权后成功。未接入 LLM、向量数据库、云服务或 API key。查询时完全离线；官方网页仅在建立本次来源记录时核对。

## 3. 来源核查结果

- **内部项目源**：原服务 README `风险等级` 第 13–20 行写明 `1 Watch` 为未来 24 小时预测最高水位 `4.20 m <= level < 4.43 m`。服务代码的 `WATCH_LEVEL_M=4.20`、`WARNING_LEVEL_M=4.43` 与其一致。这是项目内部分类；**不是** Environment Agency 的官方 `Flood Alert`，更不是 House Mill 经批准的行动 SOP。
- **模型评估源**：总体 `summary.json` 的 MAE **0.212531 m** 对应本批次 **76,091 个预测目标点**；不能把点数当作独立洪水事件。高水位报告写明达到项目 Watch、Warning、Severe 阈值的不同实测目标点均为 **0**，所以现有报告不能证明洪水事件检出能力。
- **官方公开源**：2026-09-29 核对 Environment Agency 的[英格兰预警术语指南](https://www.gov.uk/guidance/flood-alerts-and-warnings-what-they-are-and-what-to-do)（`Flood alert` 节）及 GOV.UK 的[实时查询服务说明](https://www.gov.uk/check-flooding)。官方 `Flood Alert` 表示洪水可能发生；是否对某地点**当前**发布警报，必须去官方实时服务核对。本地目录并不宣称当前状态。

5 条精选知识记录分为 `internal_project`、`evaluation_report`、`official_public_guidance` 三类。每条有概念、关键词、版本、active 状态和精确来源定位。本地文件条目保存 SHA-256 与行号；检索时验证文件哈希，返回相应原文片段。官方来源条目标记发布者、英格兰适用范围和核对日期，返回官方链接与章节。来源与适用边界详见 `docs/04_knowledge_sources.md`。

## 4. 检索与安全边界

`search()` 首先按问题措辞或显式 `--source-type` 做来源类型过滤，再按关键词召回，支持 `--version` 精确筛选。检索不会把内部项目文字当作官方指南。若无匹配证据，返回 `NoEvidence`，不编造答案；同一概念有多个 active 版本且未指定版本时，返回 `VersionConflict`，不悄悄选择较新的一个。若本地源文件哈希变化，返回 `SourceIntegrityError`，避免目录摘要和原文不一致。

目录是精选片段，不是全文搜索或语义向量检索；这一阶段的重点是**证据可定位、版本可见、权威层级不混淆**。官方页面可能变化，`web-checked-2026-09-29` 只表示本次核对时间，不保证日后内容不变。D4 的静态知识也未注入 D3 的历史 `as_of_utc` 回放；将来若要这样做，还需对文档加入发布/可用时间闸门。

## 5. 用户如何测试

打开 PowerShell：

```powershell
cd C:\UCL\DissertationAgent
$env:PYTHONIOENCODING='utf-8'
.\.venv-d4\Scripts\python.exe -m app.knowledge_cli
```

在 `请输入知识问题：` 后输入 `项目内部 Watch 是什么` 并回车。也可直接输入：

```powershell
.\.venv-d4\Scripts\python.exe -m app.knowledge_cli --question '项目内部 Watch 是什么'
```

本次真实运行的关键输出为：

```text
项目内部 Watch（level 1）表示未来24小时预测最高水位达到4.20 m但未达到4.43 m。它是项目预测分类，不是 Environment Agency 的官方 Flood Alert，也不是 House Mill 专属行动 SOP。
来源类型：internal_project；版本：source-snapshot-2026-09-29
来源定位：C:\UCL\DissertationAgent\backup\source_snapshot_2026-09-29\cloud_flood_server\README.md:13-20 § 风险等级
```

输出下方还有该章节的原文片段。另可验证分类与拒答：

```powershell
.\.venv-d4\Scripts\python.exe -m app.knowledge_cli --question '官方 Flood Alert 是什么'
.\.venv-d4\Scripts\python.exe -m app.knowledge_cli --question '高水位评估能证明 Watch 召回率吗'
.\.venv-d4\Scripts\python.exe -m app.knowledge_cli --question 'House Mill 专属 SOP 是什么'
```

前两问分别应定位官方网页与本地高水位评估报告；第三问应返回“未找到匹配的已核对来源”，退出码为 2，而不是编造现场流程。D3 回放 demo 在新的 D4 环境仍可运行：

```powershell
.\.venv-d4\Scripts\python.exe -m app.demo --as-of 2026-08-03T01:48:00Z --question '当时预测峰值是多少'
```

聚焦测试：

```powershell
.\.venv-d4\Scripts\python.exe -m unittest discover -s tests -v
```

本次结果 **11/11 通过**：D1–D2 原有 4 个、D3 3 个、D4 新增 4 个。D4 测试涵盖 README 来源定位与“非官方”文字、找不到与错误来源过滤、有效版本冲突、源文件哈希漂移；另用真实目录手动 smoke test 了内部 Watch、官方 Alert、高水位限制和缺失 SOP。验证规模与 D4 目标相称，未跑模型训练或外部服务。

## 6. 尚未完成及需支持事项

当前没有获得经 House Mill 场地管理者批准的专属 SOP，**没有创建**此类知识条目。如以后要增加现场行动建议，需用户提供获授权、标明版本与适用范围的文件。若要查询某地当前官方 Flood Alert，则应另接官方实时服务；本次离线知识库不能代替它。D4 无需恢复云服务器。
