# LangGraph 迁移第 4–9 步工作报告

日期：2026-10-28（Asia/Hong_Kong）。结论：**多步调查入口已由 LangGraph 1.2.18 控制分支与循环，新旧行为对照及完整离线回归通过。** 本次没有实现检查点、恢复、并行工具、自动重试或 LangChain 模型适配。

## 1. 修改前快照、环境与依赖

修改或安装依赖前建立：

`C:\UCL\CASA0016\FloodPred-Agent\backup\11-pre-langgraph-orchestration-20261028-182911`

| 项目 | 实际结果 |
|---|---|
| Git 基线 | `0527a66ff61864c112e261e10e894ad8720f1b88` 加已有工作区内容 |
| 原有工作区 | `.env.example` 修改，及前三步的四个未提交交付文件，全部保留 |
| 项目快照 | 105 个文件，802,404 字节；另有清单及安装前包清单 |
| 文件清单 | `MANIFEST.json`：相对路径、字节数、SHA-256、香港时间、提交和原工作区状态 |
| 历史备份保护 | 额外记录 800 个已有备份文件的字节数及 SHA-256，未重复复制 |
| 冻结验证 | 105 份副本与源文件逐件一致，随后再次按清单核对 |
| 环境 | 原 `.venv-d4`，Python 3.12.14 |
| 修改前完整回归 | 183 项通过，测试计时 7.977 秒 |

复制范围沿用前三步：源码、测试、文档、知识、演示数据、历史报告、脚本、依赖声明、README 和配置模板。排除真实 `.env`、虚拟环境、Git、旧备份、活动日志、临时目录和缓存。不执行旧清理脚本，不修改旧备份索引。

先执行 pip dry-run，确认 Pydantic 2.13.5、NumPy 2.5.3、Streamlit 1.64.0 无需变化，再安装 LangGraph 1.2.18。沙箱中的首次解析因 WinError 10013 无法联网；经允许使用有网络权限的命令完成解析和安装。

安装新增 **24 个包，原有包 0 个升级或移除**。`pip check` 返回 `No broken requirements found.`。没有安装完整 `langchain` 包；`langchain-core`、`langsmith`、`langgraph-checkpoint` 等是必需传递依赖，不表示应用启用了外部追踪或检查点。

`requirements.txt` 增加固定 LangGraph 版本及约束文件引用；`requirements_langgraph.lock.txt` 固定本次解析的 24 项新增版本，面向此次 Windows／Python 3.12 环境，不作为跨平台测试保证。安装前包清单位于快照 `PIP_FREEZE_BEFORE.txt`；安装报告与安装后包清单位于忽略的 `logs/langgraph_install_20261028.json` 和 `logs/langgraph_packages_after_20261028.txt`。

## 2. 第 4–8 步实现

### 可复用职责与兼容入口

新增 `app/investigation_core.py`，提取限制、唯一预算实现、候选合并、缺口请求、指纹及补查逻辑，并新增初始化运行依赖、首次结果归整、最终整理和正常审计四个职责函数。

生产依赖方向固定为：

```text
investigation → investigation_graph → investigation_core
                                      → 原路由／检索／证据门／只读工具
```

core 不依赖兼容入口或图。`run_investigation()` 保持原签名、默认值、客户端／时钟注入、`QueryResult` 返回及异常行为。`InvestigationLimits`、`BudgetExceeded`、`_Budget`、`_gap_request` 从旧入口重新导出，现有调用方继续可用。

现有 8 个被复用的类／函数定义与冻结源做 AST 对照，业务内容相同；仅 `_fill_gaps` 的预算参数注解改为结构化 Protocol。首次结果归整新增副本，以免节点修改上一个状态对象。抽取期间专项测试发现正常审计缺少局部问题变量，补齐后保留原审计断言并通过所有验证。

### 七个节点与条件边

新增 `app/investigation_graph.py`：

```text
START → initialize → first_pass → decide
decide → followup → recheck → decide
decide → finalize → audit → END
followup → finalize（未实际执行或预算异常）
```

| 节点 | 实际职责 |
|---|---|
| initialize | 按原条件预留首次规划额度 |
| first_pass | 单次 `run_query(generate_answer=False, audit_log=None)`，归整轮次、规划失败、模型可用状态和已尝试查询 |
| decide | 缺口、预算标记、轮次及终止原因判断，不读取时钟 |
| followup | 一轮既有补查，保留原调用前 guard 和时间参数 |
| recheck | 补查后证据检查、trace 及增益指纹比较 |
| finalize | 原最终复核、已核验表达、停止原因覆盖和调用统计 |
| audit | 一条正常调查记录，所有预算操作结束后才读取观察快照 |

首次取证不重复拆题或证据检查；闲聊和澄清不增加工具或模型调用。使用状态替换语义，没有列表追加 reducer。节点修改结果前复制 `QueryResult`，补查局部 set 完成或异常后写回 frozenset。

运行依赖通过 context 注入；类型适配层显式解析 `QueryResult` 延迟引用生成等价 schema。独立状态模块继续只有标准库运行时导入。图只缓存拓扑，预算、客户端和状态逐请求创建。

### 预算、终止与边界

默认仍为最多 3 轮取证、8 次工具、4 次模型、60 秒协作式预算。实际失败不退还预留次数，保留闲聊规划／回复的现有计数口径。节点和条件边不额外读时钟，预算快照仅在 audit 末尾采集，因而不改变原假时钟 guard 顺序。

保留无增益、不可执行补查、来源冲突、末轮耗尽、模型故障禁用及最终预算覆盖顺序。末轮新冲突不被错误地改成下一轮的 `source_conflict`；最终完整证据仍按旧规则覆盖为 `evidence_complete`。图限制固定 64 步，与业务轮次分别管理；意外递归错误直接暴露，不伪装成普通业务停止。

危险请求仍在工具与模型规划调用前拒绝；拒绝分支写一次原审计后抛出原异常，不进入正常审计节点。正常与拒绝审计写入失败的既有差异保留。图不启用重试、节点缓存、并行或检查点；模型仍使用原直接 DeepSeek 客户端。

LangSmith 外部 tracing 在执行图时显式关闭；测试覆盖宿主将 tracing 设置为 true 的情况。没有为迁移调用真实模型或网页工具。Streamlit 与 CLI 沿用原入口；原单轮工具选项不变，没有新增框架选择开关或生产旧循环回退。

## 3. 第 9 步对照与实际验证

旧实现仅保留为 `tests/support/legacy_investigation.py`。其原源文件 SHA-256 为：

`849d68ff9690eef89850e5f85ebcf94d16f6f74099130578f98151794890643e`

只适配相对导入路径并记录来源哈希，原 9 个类／函数定义与快照 AST 全部一致。测试不依赖本机忽略的备份，因此可随仓库运行；生产模块没有导入测试支持代码。

新增 `tests/test_investigation_graph.py`，共 **28 个测试方法**，包含多个参数化子场景。新旧执行器分别使用客户端、时钟和审计路径；反例不修改真实来源。完整结果对照只排除随机请求 ID；审计对照另排除写入 UTC，不删除业务字段以掩盖差异。

| 场景 | 结果 |
|---|---|
| 预测、水位、总体／高水位评估及混合题 | 路由、数值、数据批次、来源、候选、结论、引用、trace、状态及调用次数一致 |
| 完整文档、链接缺原文、关系缺桥接、定向补查 | 新旧结果一致；补查后可核验的桥接结论得到保留 |
| 哈希损坏、版本冲突、错误单位、整理期间来源失效 | 新旧相同拒绝／移除无效证据，保留可核验部分 |
| 无可执行补查、完整三轮、末轮冲突及完整覆盖 | 保持原停止顺序；合成控制场景用于指定分支，不当作真实证据验证 |
| 工具失败、模型额度耗尽、带副作用假时钟 | 已预留调用不返还，超限不新增模型调用，停止原因一致 |
| 无效规划、网络／配置失败、闲聊／澄清 | 本地降级、模型禁用及原计数一致 |
| 时间闸门与补查参数 | 历史可见性一致；数值补查逐调用保留两个时间条件 |
| 审计与拒绝 | 内容及失败差异一致；拒绝先于工具／规划，正常仅一条记录 |
| 图结构与隔离 | 真实 stream 节点顺序通过；连续请求无结果、预算或模型诊断串用 |
| 故障与 tracing | 节点故障不重试、不回退旧实现；递归异常暴露；宿主 tracing 开启仍不联网 |

实际执行：

```powershell
.\.venv-d4\Scripts\python.exe -X utf8 -m unittest discover -s tests -q
.\.venv-d4\Scripts\python.exe -X utf8 -m unittest tests.test_investigation_graph -q
.\.venv-d4\Scripts\python.exe -m pip check
```

| 验证 | 实际结果 |
|---|---|
| 修改前基线 | 183 项通过，7.977 秒 |
| 原调查／证据／独立类型专项 | 44 项通过，3.303 秒 |
| 最终迁移专项 | 28 项通过，4.294 秒 |
| 最终完整回归 | **211 项通过，13.917 秒**，含既有 Streamlit AppTest |
| 依赖检查 | 无损坏依赖，原有包版本未变 |
| 保护检查 | 98 个未计划修改的原文件、105 个快照副本、800 个旧备份文件全部一致 |
| 源码结构检查 | 9 个冻结定义、8 个复用定义的业务 AST 一致 |
| 文档测试映射 | 42 个既有方法引用及新增迁移方法名称均存在 |

Streamlit bare-mode 的 `missing ScriptRunContext` 警告与原基线一致，未导致失败。测试时间只代表本机此次执行，不作为性能提升结论。完整输出保留在忽略的 `logs/langgraph_migration_baseline_20261028.log` 与 `logs/langgraph_migration_final_20261028.log`。

离线 CLI 使用 `FLOODPRED_DATA_MODE=demo`，没有 `--llm`：

| 输入 | 退出码及结果 |
|---|---|
| 当时预测峰值是多少？ | 0；`verified`、`evidence_complete`，工具 1、模型 0 |
| 当时预测峰值和 MAE 的联系是什么？`--max-tool-calls 1` | 1（预期）；`partial_verified`、`tool_limit`，工具 1、模型 0 |
| 删除 SQLite 数据库 | 2（预期拒绝） |

前两项选中演示 run `ce9a940aa8807b10c85bc20754911bafdc8eca122053e6edc7273edd23efbb38`；结果分别保留正常与局部回答。输出保留在 `logs/langgraph_cli_*_20261028.log`，CLI 的正常审计按原行为写入日志目录。

## 4. 修改清单与保护结论

修改前 105 个文件中只有以下 **7 个**按计划变化：

- `app/investigation.py`：兼容入口转为调用图，重新导出原类型。
- `app/investigation_state.py`：更新已接入状态的说明和 type-only 限制类型来源，字段不变。
- `tests/test_evidence_gate.py`：仅迁移两个 mock 目标到实际 core，原断言保持。
- `requirements.txt`：固定 LangGraph 并引用新增约束。
- `README.md`、`docs/08_architecture.md`、`docs/13_langgraph_migration_contract.md`：当前安装、架构、能力边界和覆盖状态。

新增 core、graph、测试支持包与冻结旧实现、28 项迁移测试、依赖约束及本报告。前三步的历史工作报告及原独立类型测试保持不变。

其余 98 个原文件均与本次快照哈希一致，包括 `.env.example`、知识、演示数据、历史报告、原路由、检索、证据门、数据工具、模型客户端、Streamlit 和 CLI。105 个快照副本及原有 800 个备份文件未改变。真实 `.env` 未编辑，未提交或推送 Git。

## 5. 保留的限制

- 三轮与协作式 60 秒上限仍是原业务机制，不是操作系统级硬取消。
- 对照测试覆盖列出的场景，不证明所有中文问题、资料冲突或语义断言正确。
- 保留拒绝审计失败时可能先抛出 `OSError` 的原行为，本轮没有扩展修复范围。
- 没有检查点恢复、自动重试、并行工具或生产旧实现备用入口；回退依靠修改前快照。
- 运行上下文可能间接持有客户端凭据，不进入持久化状态；节点结果副本不等于已实现历史恢复。

本次交付达到第 4–9 步验收范围；后续能力另行设计与验证。
