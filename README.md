# FloodPred · 历史洪水预测回放 MVP

这是一个面向产品／技术作品集的**本地、只读、历史回放**演示。用户可以一次问“当时预测峰值是多少？项目内部 Warning 是什么？”；系统分别读取归档预测、结构化评估和有出处的项目／论文文档，再展示结果与 trace。多步调查先取证、检查缺口，再按白名单补查，最多 3 轮。P1-2 加入关键词＋轻量本地语义候选检索和来源／日期过滤；P1-3 在调查失败时先尝试用已核验的残余资料给出局部结论，没有可用证据才回退。它**不是实时预测服务、官方洪水警报或现场处置 SOP**。

## 运行（Windows PowerShell）

本地工作目录为 `C:\UCL\CASA0016\FloodPred-Agent`，继续使用此目录已有的 `.venv-d4`：

```powershell
cd C:\UCL\CASA0016\FloodPred-Agent
.\.venv-d4\Scripts\python.exe -m streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false
```

打开 `http://127.0.0.1:8501`。页面默认的 UTC 时间 `2026-08-03T01:48:00Z` 有可演示的归档预测。默认勾选“多步调查（最多 3 轮）”；取消后回到原来的单轮问答。如需使用 DeepSeek，在未纳入版本控制的 `.env` 填入 `DEEPSEEK_API_KEY=...`；检测到本地 key 时页面默认勾选 API 开关，否则关闭。Thinking 可独立关闭。不要把密钥提交到 GitHub。

多步调查先由现有规划器取证；对“区别、联系、为什么”等问题检查证据缺口，仅在原问题相关的白名单里补查。每次最多 **3 轮、8 次只读工具调用、4 次 LLM 调用**，总耗时采用 **60 秒协作式截止时间**，DeepSeek 单次网络请求最多 20 秒；正在执行的本地库操作不能被强制杀掉，因此 60 秒不是操作系统级硬中断。失败时优先从已核验的残余资料生成明确标记的局部结论，只说查到的部分和缺口，不用模型记忆补全；若完全没有可用证据，才停止生成并提示回退。局部卡片或局部结论都不代表完整答案。数值始终来自只读工具，事后评估不能伪装成回放时刻已知事实。

也可在命令行测试，不调用 DeepSeek：

```powershell
.\.venv-d4\Scripts\python.exe -m app.investigate_cli "项目内部 Caution 和英国官方预警有什么区别？"
.\.venv-d4\Scripts\python.exe -m app.investigate_cli "当时预测峰值和 MAE 的联系是什么？" --max-tool-calls 1
.\.venv-d4\Scripts\python.exe -m app.knowledge_cli --question "旧水位设备的安装点在哪里" --semantic
.\.venv-d4\Scripts\python.exe -m app.knowledge_cli --question "项目内部 Watch 是什么" --source-type internal_project --checked-on-or-before 2026-09-29 --semantic
```

第一条应显示 `rounds: 2`、两条来源（内部规则和 GOV.UK 静态定义），以及每轮 trace。第二条故意把工具预算压到 1，应看到 `stop_reason: tool_limit`、`answer_status: partial_verified`、非空 `answer` 和空 `fallback`；它只陈述已查到的峰值，明确说 MAE 仍缺失。第三条测试改写问法的语义候选，不代表已证实问题答案。资料核对日期过滤仅保留在命令行／后端，不占用网页提问表单；它与预测回放时间是不同概念。加 `--llm` 才会调用 DeepSeek；`--thinking` 可与它同时使用。新机器可从 [.env.example](.env.example) 复制本地配置模板，`.env` 被 Git 忽略。

在另一台机器上，只需 Python 3.12+ 和本仓库中的 `demo_data/`，**无需云服务器**：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:FLOODPRED_DATA_MODE='demo'
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false
```

未提供原始本机备份时也会自动选用 `demo_data/`；设置 `FLOODPRED_DATA_MODE=demo` 可以在开发机强制测试该模式。`demo_data/` 是最小策展子集：一次归档预测及其 96 个目标点、一个当时可见水位、两个评估摘要和两份引用片段。没有设备标识、原始 MQTT 报文或密钥。公开发布前仍需人工确认数据使用权；见 [来源说明](demo_data/PROVENANCE.md)。

## 可以试的几类问题

| 问题 | 路由 | 主要结果 |
|---|---|---|
| `当时预测峰值是多少` | 归档预测 | 3.7832 m、目标时间、run_id、内部等级 |
| `本批次 MAE 是多少` | 结构化评估 | 0.212531 m、评估版本、76,091 个匹配预测目标点 |
| `项目内部 Caution 是什么` | 经哈希校验的文档 | 项目内部门槛、原文定位；不等于官方 Flood Alert |
| `论文的离线事件召回率是多少？线上验证了洪水检出吗？` | 论文两处证据 | 离线 86.19% 与线上无越线事件分开说明 |
| `House Mill 是什么？为什么会受潮汐影响？` | 建筑档案＋历史研究论文 | 地点、历史身份与进水背景；不表示当前水情 |
| `Duncan Wilson 的旧传感器怎么布置？论文中的 136 次与 42 次有什么区别？` | 旧项目仓库＋研究论文 | 旧监测链路和不同水位阈值下的历史事件数 |
| `旧 House Mill 监测和 FloodPred 是什么关系？` | 毕业论文 | 说明技术承接，但不声称旧设备或云服务仍在线 |
| `项目内部 Caution 和英国官方预警有什么区别？` | 多步调查：内部规则→官方静态定义 | 原词与来源分开，不查询实时警报 |
| `你好，预测峰值和 MAE 的联系是什么？` | 峰值归档＋评估快照 | 解释一次预测与批次平均误差的区别；不推算这次峰值误差 |
| `你好` | 普通对话 | 介绍能查什么，不调用项目数据 |

混合问题 `当时预测峰值、历史水位、MAE 和项目内部 Caution 是什么` 会分别取证。高水位问题 `2米以上高水位 MAE 是多少` 返回不同筛选范围的 0.204788 m、22,658 个匹配点，不能与总体数值混用。

界面统一把 4.20 m 这一档称为 **Caution**。服务端原始 README 使用 **Watch**，论文使用 **Caution**；这里仅做显示别名，不修改源文件、引用 ID 或原始摘要。“证据从哪来”会标出这个对应关系。`官方 Flood Alert 是什么` 才会检索英国官方公开资料的静态定义；本应用不读取当前官方警报。

## 设计边界

- 数据工具仅通过 SQLite `mode=ro&immutable=1` 与 `PRAGMA query_only=ON` 读取，MAE 从固定 SHA-256 的 JSON 快照获取。预测按生成、入库和有效期做 UTC 时间闸门；声纳按观测和入库双时间闸门。
- 多步调查只能补查既有的归档预测、历史水位、评估快照和策展知识；模型的下一步建议须通过原问题相关性与工具白名单，重复调用会被去重。每轮和最终状态留 trace，审计日志只写请求哈希、来源 ID 和预算计数，不写问题正文或思维链。没有任意 SQL、文件写入或 MQTT 发布工具。
- 文档检索把关键词／近似拼写与本地 TF-IDF＋低秩语义向量候选合并，明确标记命中方式。它不是大型预训练 embedding 模型；语义分数只是候选排序，可能误检，不能直接用来支持答案。进入 Agent 回答前仍要求白名单概念唯一、原文件 SHA-256／页码定位／版本检查。来源类型、精确版本和可选核对日期在候选评分前过滤；无可核对完整日期的记录在日期过滤时排除，核对日期不等于原文发表日，也不等于回放时刻。缺失、篡改或版本冲突时拒答。论文知识记录 PDF 文件页和原件指纹，并区分离线事件评估与部署期无越线事件。DeepSeek 可选地规划只读工具与文档主题，再依据已核验的证据写通俗解释；引用 ID 和数字经过机械核验，但这**不等于完整语义真实性证明**。普通聊天不附项目证据，也不能声称项目事实。
- House Mill 背景新增 Historic England 建筑档案、djdunc/housemill 旧项目 README，以及 Wilson 与 Zhang 2025 年论文的人工核对笔记。网页条目保留原始 URL 和核对日期，PDF 条目保留页码和原件指纹；运行时校验本地笔记哈希，原始论文若仍在本机下载目录也校验其哈希。另一台机器可使用仓库内笔记，但**不会自动重新抓取和核对网页或原 PDF**。旧研究结果与现在的回放预测严格分开；详见 [来源和限制](docs/12_housemill_context.md)。
- 修改数据库、发布警报、索取密钥、绕过系统边界等请求在工具调用前拒绝。没有写入工具、MQTT 发布或官方实时状态查询。
- `logs/requests.jsonl` 记录请求 ID、问题 SHA-256、路由、run_id、来源 ID、时间和工具状态；不记录问题原文、证据正文、API key、请求头或 DeepSeek 思维链。日志目录被 `.gitignore` 排除。启用 DeepSeek 时，请求可使用低强度 thinking；若 API 返回 `reasoning_content`，只在本次页面的“运行细节”里显示，且不作为事实依据。
- 页面在“回答／证据从哪来／运行细节”中分别标注历史回放、事后评估和官方类别边界。异常不是“无风险”结论。

## 验证

```powershell
.\.venv-d4\Scripts\python.exe -m unittest discover -s tests -q
.\.venv-d4\Scripts\python.exe -m app.evaluate
$env:FLOODPRED_DATA_MODE='demo'
.\.venv-d4\Scripts\python.exe -m unittest discover -s tests -q
.\.venv-d4\Scripts\python.exe -m app.evaluate
```

2026-10-02 P1-2/P1-3 的实际运行结果见 [最新工作报告](reports/P1_2_P1_3_work_report_2026-10-02.md)；此前 D8 的 **20/20 离线评估案例**没有因本次改动重跑，不能当作新检索器成绩。2026-10-01 P1-1 的结果与限制见 [P1-1 工作报告](reports/P1_1_work_report_2026-10-01.md)。固定测试不能代表开放环境的泛化精度或完整安全覆盖率。

特别注意：论文有**离线历史事件**检出评估，但**部署期没有实测越过阈值的事件**，因此线上事件检出能力仍未验证。点级 MAE 的分母是匹配预测目标点，不是独立洪水事件。2026-08-10 的评估是事后快照，不能当作 2026-08-03 回放时刻已知事实。

更多信息：[问题识别与 DeepSeek 调用](docs/11_input_routing.md)、[论文证据目录](docs/10_thesis_ground_truth.md)、[House Mill 背景来源](docs/12_housemill_context.md)、[简版 PRD](docs/07_PRD.md)、[架构与信任边界](docs/08_architecture.md)、[安全测试与限制](docs/09_security.md)。
