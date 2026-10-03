# FloodPred · 历史洪水预测回放 MVP

这是一个面向志愿者的**本地、只读、历史回放**演示，也可作为产品／技术作品集。用户可以一次问“当时预测峰值是多少？项目内部 Warning 是什么？”；系统分别读取归档预测、结构化评估和有出处的项目／论文资料，再逐项核对能否回答。原话问题图、混合检索、证据支持检查、按缺口限次补查已接入；回答里的每条结论都有自己的引用。最多 3 轮，有可靠局部事实就说这一部分，没有事实就说明缺什么。它**不是实时预测服务、官方洪水警报或现场处置 SOP**。

## 当前实现：第 3–4 步（2026-10-03）

```text
原话 → 问题图 → 原话/片段/改写混合检索 → 逐项证据检查
                                              ├─ 足够 → 核验结论 → 表达整理 → 最终复核
                                              ├─ 有缺口 → 限次定向补查 → 再检查
                                              └─ 冲突/无增益/超预算 → 局部回答或回退
```

每个问题项都有五种证据状态：`direct_support`（直接支持）、`explicit_derivation`（可明确推导）、`related_only`（仅相关）、`not_supported`（不支持）、`conflict`（存在冲突）。检查具体定位、SHA-256、版本、时间范围、数字／单位和所求属性。文档核对日期不证明它在回放时刻已可用；MAE 是事后评估，必须附生成版本、样本范围、匹配点数和米。数值仍由只读工具提供，不能从相似文段猜数。

**小型人工知识库采用保守的支持契约，不声称实现通用语义蕴含判断。** `app/support_profiles.py` 定义 15 类已人工核对的事实、可回答属性和具体摘录指纹；`app/evidence_gate.py` 逐项核对。指纹独立于可编辑目录，修改原文后仅更新目录哈希仍不能套用旧结论。未知属性或新段落可作为候选，但未复核时不能生成项目结论。部分背景来源是已有的人工整理笔记，不是网页／PDF 逐字转录；引用定位会明确标为“整理笔记”，不会冒充新获取的原件原文。

关系题需要两端证据及直接关系记录。分别查到旧声纳和新预测方法，不能据此说继承了同一套设备。毕业论文中的承接记载支持**采集实现思路的承接**，不证明实体设备相同或今天在线。“峰值和 MAE 的联系”只允许一种有明示依据的有限推导：一次预测极值和批次平均误差不是同一层级，不能推算本次实际误差或置信区间。相似度高、引用 ID 正确、模型自报“证据足够”，均不能独立放行结论。

第 4 步只补查仍未满足的问题项，保留原话和片段，再加入属性／直接关系的缺口查询；不同轮次改变补查重点。每轮最多 3 个缺口任务，一次知识任务内的混合子查询另留 trace，不伪装成一次单段查询。已尝试的数值工具不放宽时间条件重试。证据状态没有增益就提前停止；资料版本冲突直接展示，不让模型裁决。默认最多 **3 轮、8 次只读工具、4 次 LLM 调用、60 秒协作式预算**。如果有可靠局部事实，显示 `partial_verified` 和未确认项；完全没有可靠事实则 `fallback`。模型最后只返回已核验结论的排序／表达选择 JSON；不接受自由文本新增项目事实。表达选择失败时使用已核验的通俗表述，并再次检查来源。

### 联网资料的取舍

本次**没有新增运行时联网搜索**，现有本地项目与论文材料足以验证这两步，也避免把搜索摘要当证据。未来可在**第 4 步缺口补查**增加可选只读搜索：内部归档负责项目数值和规则，官方来源负责官方类别，研究／历史网页负责背景；外部原文另存 URL、抓取时间、发布日期、版本和哈希，再走同一支持关口，不覆盖内部文件。当前 GOV.UK 条目只有链接与目录摘要，缺少本地原文快照，故不能支撑官方定义；界面会展示候选和缺口。DeepSeek API 不是联网搜索服务。

### 怎样测试第 3–4 步

沿用已有 `.venv-d4`，不需恢复云服务器。以下命令均不调用 API：

```powershell
cd C:\UCL\CASA0016\FloodPred-Agent
.\.venv-d4\Scripts\python.exe -X utf8 -m app.investigate_cli "旧传感器怎么布置？它和FloodPred预测方法有什么关系？"
.\.venv-d4\Scripts\python.exe -X utf8 -m app.investigate_cli "你好，预测峰值和MAE的联系是什么？"
.\.venv-d4\Scripts\python.exe -X utf8 -m app.investigate_cli "预测峰值和MAE的联系是什么？" --max-tool-calls 1
.\.venv-d4\Scripts\python.exe -X utf8 -m app.investigate_cli "旧传感器多少钱？"
.\.venv-d4\Scripts\python.exe -X utf8 -m app.investigate_cli "官方 Flood Alert 是什么？"
.\.venv-d4\Scripts\python.exe -X utf8 -m unittest tests.test_evidence_gate tests.test_streamlit_app -q
.\.venv-d4\Scripts\python.exe -X utf8 -m app.evaluate_evidence
```

- 第一条：3 个问题项直接支持，结论附各自原文引用；承接结论明确不证明同一套实体设备。
- 第二条：两个端点直接支持、关系为 `explicit_derivation`；MAE 附版本、76,091 个匹配目标点和 `m`，不声称这次峰值实际误差。
- 第三条：`tool_limit`、`partial_verified`，仅保留已核验峰值，不生成缺失 MAE 的关系结论。因达到限制有错误记录，CLI 退出码 1 是预期。
- 第四条：资料相关但不回答价格，`related_only`；第 2 轮无增益后停止，最终回退。
- 第五条：官方来源只有链接，没有原文，回退并说明缺口，不用模型记忆补出定义。

网页启动命令见下方“运行”。关闭 DeepSeek，依次输入前述问题，在“证据”检查**逐项状态表、具体结论对应引用、原文、定位、版本和单位**；“运行细节”看 `question_evidence`、`retrieval_runs`、`source_rejections` 和停止原因。版本冲突会单独列出来源与版本；自动化测试在临时目录制造冲突／缺失／恶意资料，不需要你改真实知识文件。取消“多步调查”只执行单轮，仍经过证据和最终回答关口。

加 `--llm` 使用已有 DeepSeek，`--thinking` 可选。它可以提议问题图和缺口优先级、选择已核验结论的排序／通俗前缀；不能绕过程序或编写新的事实结论。新选择接口已用 `deepseek-v4-pro` 真实联调 1 次（关闭 Thinking）。完整端到端网络和高并发未在本次反复测试。

实际验证：**155 项离线测试通过**；其中证据／网页专项 **30 项**，固定评估案例 **18/18**。这只是有限回归样本，不是开放问题准确率，也没有验证超阈值洪水事件检出能力。评估保存期望、实际回答、每项状态、引用、失败原因和预算计数到 [结果 JSON](reports/stage3_4_evidence_results.json)。详见 [第 3–4 步工作报告](reports/P2_step3_4_evidence_2026-10-03.md)。

### 当前技术栈与备份

Python 3.12＋普通 Python 有界编排；Pydantic 工具参数；dataclasses 问题图／证据状态／结论引用；BM25＋NumPy TF-IDF/SVD-LSA＋加权 RRF；JSON 人工目录与固定支持契约、Markdown／PDF 页码、SHA-256；SQLite 只读工具和评估 JSON；标准库 HTTP 调 DeepSeek；Streamlit 和 unittest/AppTest。**没有新增依赖、环境、向量数据库、LangChain 或 LangGraph。**

备份统一为 `backup/01-d1-d2-20260929` 至 `06-hybrid-20261003`，后者是本次修改前 90 文件快照。原始数据 `source_snapshot_2026-09-29` 因被引用仍保留原名。12 个零散快照共 526 文件已逐文件校验后打包到 `archived-legacy-20261003.zip`，原目录已移除，可解压恢复；映射见 `backup/index.json`。不复制虚拟环境或 API key，已归档内容、原始数据／知识文件和旧工作报告不修改；根目录只维护这个 `README.md`，来源文件／历史备份中的 README 是不可变取证材料，不删除。

## 历史交付：原话混合检索第 2 步（2026-10-03）

以下为第 2 步交付记录；当时“只展示候选”的行为和 132 项成绩已被上方第 3–4 步支持检查／回退流程覆盖，当前验收请以上方为准。

每个可识别的文档问题使用完整原话、问题图中的原文片段，以及最多两条受约束改写。改写只允许固定同义词补充和从原文提取对象／请求词／限定词，不能编入新日期、地点、数值或结论。预设概念查询仍用于补充召回，但权重低于原话。相同原话与片段只执行一次，标为 `original+fragment`；对象不在同一片段内时，只有已通过问题图校验的指代对象才可进入改写。纯峰值、历史水位、MAE 数值问题仍查只读工具，不拿文档相似度猜数值。

关键词通道使用 BM25＋精确目录别名加分，语义通道沿用本地 TF-IDF／SVD-LSA。两路各自排序，按加权 RRF 合并：`sum(weight / (60 + rank))`，**不比较 BM25 与语义原始分数大小**。原话、片段、改写、概念补充的权重分别为 0.8、1.0、0.65、0.25；去重后的相同原话／片段使用 1.0。每节点最多 5 条查询，每通道取前 5 个候选，每节点最终最多 8 个不同来源段落。相同文件／版本／行范围的段落去重，但保留其不同目录 ID 和概念，避免 Caution／Warning 共用一段原文时丢失引用。一次逻辑只读知识工具调用可批量处理最多 6 个问题节点；本地子查询数量单独留 trace，不冒充新增 LLM 调用。60 秒预算仍是协作式截止时间，不是强制中断本地计算。

不指定来源时跨来源召回，“旧”不会自动限制为旧项目仓库。“论文中”允许毕业论文和研究论文；“毕业论文中”只筛毕业论文，“Wilson 论文中”只筛研究论文。明确来源优先于预设概念的默认来源。精确版本和可选目录快照日期仍在评分前过滤。`checked_on` 缺失时，兼容旧目录使用版本中的完整日期作快照过滤依据；调试区以 `filter_date_basis=version_stamp` 标出，不能把它当作真实核验日。没有完整日期的记录在日期过滤时排除；不能由年份猜月份和日期。**文档快照日期、原文发表日期、回放 `as_of_utc` 是不同时间，前两者不能自动证明资料在回放时已可用。**

“证据”页新增候选原文、具体定位和版本／指纹；“运行细节”的 `retrieval_runs` 显示每条实际查询、来源过滤、各通道排名和 RRF 贡献。PDF 条目保留精确文件页和原始 PDF 指纹，本地摘录先做 SHA-256 校验再参加排序；损坏条目隔离，目标来源损坏、版本冲突仍不能用于回答。官方条目中原有的“只有 URL＋目录摘要、没有本地网页原文快照”的情况明确标注，不能声称已重新核验网页全文。

这一步交付的是**候选资料召回**，不是第 3 步的逐项证据充分性验证。新发现的未映射文档问题只展示候选，不直接交给 DeepSeek 补写结论；原有可回答的策展条目仍通过唯一概念／定位／版本检查进入回答。第 3 步还需逐项检查两端事实、直接关系证据和冲突；“查到来源”不等于“能答整个问题”。本阶段未新增运行时联网搜索。建议完成第 3 步支持检查后，在第 4 步把联网作为**有缺口才触发的限次补查**：内部来源负责归档数值和项目规则，官方来源负责官方定义／状态，外部论文与历史网页负责背景；外部资料单独缓存并标日期／URL／哈希，不覆盖内部事实。DeepSeek API 不等于联网搜索。

### 怎样测试原话检索

下列测试不需要 API key、不调用 DeepSeek，也不需要云服务器：

```powershell
cd C:\UCL\CASA0016\FloodPred-Agent
.\.venv-d4\Scripts\python.exe -X utf8 -m app.knowledge_cli --question "你好，旧水位设备的安装点在哪里？它和FloodPred预测方法有什么关系？" --fragment "旧水位设备的安装点在哪里" --hybrid --json
.\.venv-d4\Scripts\python.exe -X utf8 -m app.investigate_cli "旧传感器怎么布置？它和FloodPred预测方法有什么关系？"
.\.venv-d4\Scripts\python.exe -X utf8 -m app.knowledge_cli --question "毕业论文中的内部 Caution 是什么" --hybrid --json
.\.venv-d4\Scripts\python.exe -X utf8 -m app.knowledge_cli --question "项目内部 Caution 是什么" --hybrid --checked-on-or-before 2026-09-28
.\.venv-d4\Scripts\python.exe -X utf8 -m unittest tests.test_hybrid_retrieval tests.test_streamlit_app -q
.\.venv-d4\Scripts\python.exe -X utf8 -m app.evaluate_retrieval
```

第一条应显示原话、精确片段和改写，候选含旧项目笔记及毕业论文；未指定来源，不应只剩 `prior_project`。第二条可看到 q1／q2／q3 的实际检索和旧项目／承接说明两条来源。第三条只返回 `thesis` 候选，不返回服务端 Watch 记录；第四条故意使用太早的目录日期，应检索失败、退出码 2，而不是降低约束偷偷答题。专项测试目前 **27 项通过**（20 项检索检查＋7 项网页测试）；完整回归 **132 项通过**。6 个固定口语检索案例中目标段落都进入前 5，但只有 2 个排第一；这是小型候选召回回归，不是回答准确率，也不是与旧 Agent 的端到端对比。实际成绩和反例见 [第 2 步工作报告](reports/P2_step2_raw_hybrid_2026-10-03.md)。

网页按下方方式启动，关闭 DeepSeek，输入 `FloodPred以前测水的东西摆在哪里`。应看到“只展示候选段落”的提示；“证据”能展开原文，“运行细节”有 `retrieval_runs`，不应出现凭候选编出的完整答案。输入 `旧传感器怎么布置？它和FloodPred预测方法有什么关系？` 可检查多片段查询。含糊的“现在的预测”仍先澄清。资料日期输入框没有恢复。启用 DeepSeek 可沿用原有规划／解释功能，新检索器本身不消耗 API token。

### 当前技术栈

| 部分 | 技术与用途 |
|---|---|
| 运行与编排 | Python 3.12、普通 Python 显式路由／有界调查循环、dataclasses；沿用 `.venv-d4` |
| 参数与问答结构 | Pydantic 校验只读工具参数；原文字符位置和结构化问题图 |
| 文档召回 | 自实现 BM25＋目录别名、NumPy TF-IDF／SVD-LSA、加权 RRF；没有新增预训练 embedding 或向量数据库 |
| 来源管理 | JSON 人工目录、Markdown 摘录／整理笔记、PDF 页码、SHA-256、来源／版本／快照日期过滤 |
| 数据工具 | SQLite 只读历史归档、经哈希校验的 JSON 评估快照、UTC 时间闸门 |
| 语言模型 | 可选 DeepSeek 规划与证据解释，标准库 HTTP；既有 `.env`，不新增密钥或联网搜索服务 |
| 界面与验证 | Streamlit、unittest、Streamlit AppTest；没有引入 LangChain／LangGraph |

本次没有新增依赖或新 Python 环境，原始知识资料、数据和所有既有备份保持不变；改动前另做了独立源码／资料快照，不重复备份虚拟环境或 API key。

## 历史交付：原话问题图第 1 步（2026-10-03）

提问会先生成可核实的问题图，再进入原有取证流程。原文保留在本次请求内存中，另有 NFKC／大小写／空格规范化副本；每个对象、请求词和限定词都有原文字符位置（从 0 开始，结束位置不包含在片段内）。程序保留“旧、当时、现在、不是、内部、官方”等限定词，区分单独事实、含义、关系、比较和原因。关系节点依赖两端问题；明确的代词会指向前面的原文对象，多个候选时先澄清。

DeepSeek 在原来的**同一次规划请求**中可提议拆题 JSON，不额外增加一次拆题 API 请求。它只能提议最多 6 个节点、有原文位置的片段／对象、问题类型和向前依赖；不能提议执行代码、SQL 或新工具。格式匹配允许大小写、空格与全半角差异，最终片段均映射回原文；跨句对象仅在程序已确认代词指向时接受。Python 检查对象／片段存在、依赖合法、关系两端匹配，并从原文重新派生否定、时间和来源范围；较短的模型片段会继承其所属本地问题的限定。模型没有写出问题图时仍使用本地拆题；模型图不合格时回到本地规则。未知口语对象可以由模型细化；其解释是否正确仍需要人工案例验证，原文定位本身并不证明语义理解完全正确。

“不要查 MAE，只看预测峰值”中的 MAE 工具会被抑制，后续补查也不能重新加入；文档主题的明确排除同样生效。含糊的“现在的预测”不会被当成实时数据；程序先问你指预测方法还是当前水情。“峰值和 MAE 的联系”会记录两个端点和关系，标明批次 MAE 不是本次峰值误差。第 1 步交付时只新增拆题与调用约束；当前第 3–4 步已新增逐项证据检查及按缺口补查。现有数值路由可继续为关系题读取回放示例，不能把它说成用户明确要求了具体数值。

本阶段没有新增运行时联网搜索工具。DeepSeek API 调用与联网搜索是两回事。现有本地策展来源继续使用；未来若接搜索，归档数值与项目规则由内部来源负责，官方定义／当前官方状态由官方来源负责，背景资料保留出处后独立核对；搜索摘要不能直接成为项目事实，也不能覆盖源文件或时间闸门。

### 怎样测试拆题

沿用 `.venv-d4`，以下第一组只拆题，**不查询数据库或文档、不调用 DeepSeek**：

```powershell
cd C:\UCL\CASA0016\FloodPred-Agent
.\.venv-d4\Scripts\python.exe -X utf8 -m app.question_graph_cli "你好，预测峰值和 MAE 的联系是什么？"
.\.venv-d4\Scripts\python.exe -X utf8 -m app.question_graph_cli "你好，旧传感器是怎么布置的？它和现在的预测有什么关系？"
.\.venv-d4\Scripts\python.exe -X utf8 -m app.question_graph_cli "旧传感器怎么布置？它和现在的 FloodPred 预测方法有什么关系？"
```

第一条应有 3 个节点，关系的 `depends_on` 为 `q1,q2`，端点属性为 `meaning_and_scope`，MAE 记录 `aggregate_mae_is_not_single_peak_error`。第二条会把“它”指向旧传感器，但 `needs_clarification=true`，因为“现在的预测”仍有时间歧义。第三条明确说预测方法，应该没有该歧义。节点数量不是唯一正确性的指标，请同时检查对象、限定词和依赖。

加 `--llm` 使用已配置的 DeepSeek；加 `--thinking` 才启用 Thinking。命令行拆题接口的单次网络超时为 20 秒，失败会输出本地问题图及 `llm_error`，退出码为 1：

```powershell
.\.venv-d4\Scripts\python.exe -X utf8 -m app.question_graph_cli "旧传感器怎么布置？它和FloodPred预测方法有什么关系？" --llm
.\.venv-d4\Scripts\python.exe -X utf8 -m app.investigate_cli "不要查 MAE，只看当时预测峰值是多少"
.\.venv-d4\Scripts\python.exe -X utf8 -m unittest tests.test_question_graph -q
```

第一条成功时 `origin=deepseek_checked`、`llm_error=null`；第二条应仅查询预测，没有评估结果。网页沿用下方启动方式，在“运行细节”查看 `question_graph`。输入第二个含糊示例，应先看到澄清问题，不展示预测／评估卡片；输入两个候选代词示例“旧传感器和新传感器怎么布置？它在哪里？”也应先澄清。关闭 DeepSeek 仍能测试本地规则。

原文与完整问题图不写入持久请求日志，日志只增加拆题来源、节点数、歧义数和被排除的路由／概念。拆题过程不是事实证据，也不会绕过只读工具白名单。变更、备份和实际验证详见 [本次工作报告](reports/P2_step1_question_graph_2026-10-03.md)。

本次最终离线回归 **111 项通过**，其中新增 33 项拆题／调用边界测试和 1 项网页模拟测试。真实 DeepSeek 拆题接口已跑通；少量接口联调不能代表开放问法的准确率。详见报告中的实际联调记录和限制。

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

第一条当前应显示 `rounds: 2`、内部规则的局部结论，以及官方链接缺原文的缺口，不生成完整对比。第二条故意把工具预算压到 1，应看到 `stop_reason: tool_limit`、`answer_status: partial_verified`、非空 `answer` 和空 `fallback`；它只陈述已查到的峰值，明确说 MAE 仍缺失。第三条测试改写问法的语义候选，不代表已证实问题答案。资料核对日期过滤仅保留在命令行／后端，不占用网页提问表单；它与预测回放时间是不同概念。加 `--llm` 才会调用 DeepSeek；`--thinking` 可与它同时使用。新机器可从 [.env.example](.env.example) 复制本地配置模板，`.env` 被 Git 忽略。

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
| `项目内部 Caution 和英国官方预警有什么区别？` | 内部规则＋官方来源补查 | 只回答内部规则；官方条目缺原文时明确未确认，不作完整对比 |
| `你好，预测峰值和 MAE 的联系是什么？` | 峰值归档＋评估快照 | 解释一次预测与批次平均误差的区别；不推算这次峰值误差 |
| `你好` | 普通对话 | 介绍能查什么，不调用项目数据 |

混合问题 `当时预测峰值、历史水位、MAE 和项目内部 Caution 是什么` 会分别取证。高水位问题 `2米以上高水位 MAE 是多少` 返回不同筛选范围的 0.204788 m、22,658 个匹配点，不能与总体数值混用。

界面统一把 4.20 m 这一档称为 **Caution**。服务端原始 README 使用 **Watch**，论文使用 **Caution**；这里仅做显示别名，不修改源文件、引用 ID 或原始摘要。“证据从哪来”会标出这个对应关系。`官方 Flood Alert 是什么` 才会检索英国官方公开资料的静态定义；本应用不读取当前官方警报。

## 设计边界

- 数据工具仅通过 SQLite `mode=ro&immutable=1` 与 `PRAGMA query_only=ON` 读取，MAE 从固定 SHA-256 的 JSON 快照获取。预测按生成、入库和有效期做 UTC 时间闸门；声纳按观测和入库双时间闸门。
- 多步调查只能补查既有的归档预测、历史水位、评估快照和策展知识；模型的下一步建议须通过原问题相关性与工具白名单，重复调用会被去重。每轮和最终状态留 trace，审计日志只写请求哈希、来源 ID 和预算计数，不写问题正文或思维链。没有任意 SQL、文件写入或 MQTT 发布工具。
- 文档检索把关键词／近似拼写与本地 TF-IDF＋低秩语义向量候选合并，明确标记命中方式。它不是大型预训练 embedding 模型；语义分数只是候选排序，不能直接支持答案。进入 Agent 回答前有逐项支持契约、原文件／摘录 SHA-256、页码／段落、版本和数字／单位检查。来源类型、精确版本和可选核对日期在候选评分前过滤；核对日期不等于发表日或回放时刻。缺失、篡改或冲突项不能生成结论，但其他已核验项可保留。论文知识记录区分离线事件评估与部署期无越线事件。DeepSeek 可选地规划和整理已核验结论的表达，不能自由新增项目事实；小语料人工契约**不等于通用语义真实性证明**。普通聊天不附项目证据，也不能声称项目事实。
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

2026-10-03 逐项证据与补查实际验证见 [最新工作报告](reports/P2_step3_4_evidence_2026-10-03.md)；原话混合检索见 [第 2 步报告](reports/P2_step2_raw_hybrid_2026-10-03.md)，问题图见 [第 1 步报告](reports/P2_step1_question_graph_2026-10-03.md)。P1-2/P1-3 和 D8 的历史成绩没有在本次重跑，不能当作当前证据关口成绩。固定测试不能代表开放环境的泛化精度或完整安全覆盖率。历史报告保持原样。

特别注意：论文有**离线历史事件**检出评估，但**部署期没有实测越过阈值的事件**，因此线上事件检出能力仍未验证。点级 MAE 的分母是匹配预测目标点，不是独立洪水事件。2026-08-10 的评估是事后快照，不能当作 2026-08-03 回放时刻已知事实。

更多信息：[问题识别与 DeepSeek 调用](docs/11_input_routing.md)、[论文证据目录](docs/10_thesis_ground_truth.md)、[House Mill 背景来源](docs/12_housemill_context.md)、[简版 PRD](docs/07_PRD.md)、[架构与信任边界](docs/08_architecture.md)、[安全测试与限制](docs/09_security.md)。
