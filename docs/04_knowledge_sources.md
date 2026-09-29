# D4 可信知识来源与检索边界

## 为什么先做来源元数据

文档型 RAG 的“检索”必须先明确每段文本**来自哪里、属于什么权威层级、哪个版本、能回答什么问题**。否则内部模型等级 `Watch` 容易被误写成官方 `Flood Alert`，评估批次的点级 MAE 也容易被误写成洪水事件检出率。D4 不接生成模型，先把证据层建成可检查的最小单元；以后即使加入 LLM，也应让生成只引用检索到的记录，不把知识库文本视为操作指令。

## 来源分类

| 类型 | 可支持的说法 | 不可支持的说法 |
| --- | --- | --- |
| `internal_project` | 原项目自定义阈值、内部风险等级 | Environment Agency 官方警报；场地正式 SOP |
| `evaluation_report` | 特定数据批次、方法、误差及样本限制 | 未观测过的洪水事件检出率；普遍化准确率 |
| `official_public_guidance` | 英格兰官方预警术语、查询官方服务的路径 | 本项目内部阈值；某地当前警报状态；House Mill 场地决策流程 |

目录 `knowledge/catalog.json` 共 5 条精选记录，每条有 `source_type`、`concept`、`version`、`status`、来源定位、关键词和经核对的中文摘要。项目本地文档还记录 SHA-256 与行号；查询时先核验哈希，再截取原文。官网记录带 URL、发布者、适用地域和核对日期。关键词只决定召回，不决定权威；使用来源类型过滤，且同概念多个 active 版本时默认拒答。若明确指定 `--version`，才选该版本，并在结果中展示版本号。

## 已核对来源

1. 内部阈值：`backup/source_snapshot_2026-09-29/cloud_flood_server/README.md`，`风险等级`，第 13–20 行。内部 Watch 为预测最高水位 **4.20 m 至低于 4.43 m**。已对照同备份的 `server.py` 第 57–58、1507–1549 行：定义 `WATCH_LEVEL_M=4.20`、`WARNING_LEVEL_M=4.43`，分类逻辑一致。它不是官方 Flood Alert。此 README 不能证明阈值经场地管理者批准为 SOP。
2. 总体评估：`backup/source_snapshot_2026-09-29/forecast_evaluation/output/summary.json`，`point_metrics`，第 19–31 行。2026-08-10 评估快照有 76,091 个成熟且匹配的**预测目标点**，MAE 0.212531 m。点不等于独立洪水事件。
3. 高水位限制：`backup/source_snapshot_2026-09-29/forecast_evaluation/output_above_2m/HIGH_WATER_EVALUATION_REPORT.md`，`风险阈值背景`，第 71–78 行。达到内部 Watch/Warning/Severe 阈值的不同实测目标点均为 0；不能据此证明洪水事件召回率。
4. 英格兰官方术语：Environment Agency，[Flood alerts and warnings: what they are and what to do](https://www.gov.uk/guidance/flood-alerts-and-warnings-what-they-are-and-what-to-do#flood-alert)，`Flood alert` 节。2026-09-29 核对；页面说明 Flood Alert 表示洪水**可能**发生，Flood Warning 表示洪水**预计**发生。其适用地域为英格兰；勿将官方术语映射到本项目的 Watch/Warning 字符串。
5. 官方实时查询入口：[Check for flooding](https://www.gov.uk/check-flooding)，`Use this service to check` 节。2026-09-29 核对；可查询英格兰当前 alerts/warnings、水位和未来 5 天风险。D4 只保存入口描述，不抓取实时状态。

## 边界

- 官方网页可能更新；`web-checked-2026-09-29` 是**核对日期，不是永久有效的官方版本号**。用于真实决策前须重访官方页面和实时服务。
- 当前是精选片段的关键词＋元数据检索，不是全文索引、向量检索或开放式问答；未知问题拒答。
- D4 的静态知识不应自动注入 D3 的 `as_of_utc` 历史回放事实。若以后做历史时点文档检索，必须增加每条知识的发布/可用时间闸门。
- 没有得到 House Mill 场地管理者批准的行动 SOP，因此没有创建此类记录，也不会把公共指南伪装成现场指令。
