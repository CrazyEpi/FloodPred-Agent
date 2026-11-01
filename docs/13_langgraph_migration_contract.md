# LangGraph 迁移准备：行为契约、外层编排与状态接口

日期：2026-10-28（Asia/Hong_Kong）。适用基线：`0527a66ff61864c112e261e10e894ad8720f1b88` 加修改前工作区；当时已有 `.env.example` 修改，已随快照保存。

本文保留前三步建立的修改前行为契约，并更新迁移实现位置与覆盖。当前多步入口使用 LangGraph 1.2.18；第 10–11 步新增可选本机检查点与节点边界恢复，见第七节。第一节及第五节是前三步历史冻结点，第六节记录第 4–9 步验收，不将历史边界误作当前持久模式能力。

## 1. 修改前冻结点与验证口径

- 快照：`C:\UCL\CASA0016\FloodPred-Agent\backup\10-pre-langgraph-contract-20261028-180328`。
- `MANIFEST.json` 记录 Git 提交、原有工作区状态及 101 个文件的相对路径、字节数和 SHA-256，共 761,261 字节；复制时核对源文件和副本，随后按清单复核。
- 快照包含源码、测试、文档、知识、演示数据、历史报告、脚本、依赖声明、README、`.gitignore` 和已修改的 `.env.example`；排除真实 `.env`、虚拟环境、Git、旧备份、活动日志、缓存和临时目录。不重跑历史备份清理脚本。
- 沿用 `.venv-d4`，修改前执行 `python -X utf8 -m unittest discover -s tests -q`，175 项通过。完整命令和本次结果见工作报告；历史通过记录不能替代本次验证。

以下映射使用 `tests.模块.测试类.测试方法` 的完整名称。“已有测试”仅指该方法实际断言的场景；未覆盖部分明确列出，不宣称测试已证明所有输入的正确性。

## 2. 行为契约及既有测试映射

### C01：数值事实由现有只读工具提供

**实现位置：**`app/read_tools.py` 的 `archived_forecast_tool`、`historical_water_tool`、`evaluation_metrics_tool`；`app/router.py::run_query`；`app/archive.py::_connect_read_only` 和 `app/replay.py::latest_observation_as_of`。

**输入与输出：**预测与历史水位问题使用经现有参数模型校验的回放时间；MAE 问题使用现有评估范围。返回现有工具结果，保留批次、来源、单位、版本和样本范围。SQLite 使用只读、immutable 连接及 `query_only`；评估读取固定哈希的 JSON。模型不得产生新的数值事实、任意 SQL 或写入能力。评估结果保持事后评估标识，不因与预测一起回答而变成当时已知的指标。

**已有测试：**

- `tests.test_router.RouterTests.test_three_query_classes_and_mixed_route`
- `tests.test_router.RouterTests.test_mae_is_exact_snapshot_with_version_scope_and_unit`
- `tests.test_security.SecurityTests.test_archive_file_remains_identical_after_query`
- `tests.test_archive.ArchiveTests.test_exact_run_returns_archived_values`

**当前覆盖：**数值及混合路线已做冻结旧流程对照，完整结果、候选和调用数一起比较，见第六节；不能据此证明全部问法都被正确识别。

### C02：保留混合检索与原文约束

**实现位置：**`app/hybrid_retrieval.py::make_request`、`hybrid_search`、`reciprocal_rank_fusion`；`app/knowledge.py::_semantic_rank`。

**输入与输出：**保留用户原话及真实片段，使用受约束改写和低权重补充查询；过滤来源、版本和核对日期后，通过 BM25 与本地 TF-IDF/LSA 通道检索，用现有加权 RRF 按排名融合。语义实现已有小样本与大词表降级分支，迁移不改算法、阈值或排序。检索输出是候选；分数不直接授权回答，链接或摘要不能自动成为可引用原文。

**已有测试：**

- `tests.test_hybrid_retrieval.HybridRetrievalTests.test_original_and_fragment_are_retained_verbatim`
- `tests.test_hybrid_retrieval.HybridRetrievalTests.test_invented_fragment_and_rewrite_subject_are_rejected`
- `tests.test_hybrid_retrieval.HybridRetrievalTests.test_explicit_source_version_and_date_filter_before_ranking`
- `tests.test_hybrid_retrieval.HybridRetrievalTests.test_rrf_uses_ranks_not_raw_score_magnitude`
- `tests.test_hybrid_retrieval.HybridRetrievalTests.test_document_batches_exclude_pure_numeric_and_negated_nodes`

**当前覆盖及缺口：**完整检索结果、顺序及 trace 已纳入新旧对照；语义降级规模边界未新增算法测试，本次未改算法或阈值。

### C03：两个时间范围独立且补查不放宽

**实现位置：**`app/archive.py::get_forecast_as_of`；`app/replay.py`；`app/investigation_core.py::_fill_gaps`；`app/hybrid_retrieval.py::hybrid_search`。

**输入与输出：**预测可见性受生成、入库和有效期约束，观测同时受观测时间与入库时间约束。预测的未来目标时间允许晚于回放时间。数值补查沿用 `result.as_of_utc`，文档补查沿用 `result.knowledge_checked_on_or_before`，不得放宽时间以重试失败的数据查询。文档核对日期是独立来源筛选条件，不证明该文档在历史回放时刻可见。

**已有测试：**

- `tests.test_archive.ArchiveTests.test_as_of_does_not_use_run_before_it_was_stored`
- `tests.test_archive.ArchiveTests.test_as_of_rejects_expired_or_missing_forecast`
- `tests.test_replay.ReplayTests.test_as_of_blocks_future_or_late_stored_observations`
- `tests.test_investigation.InvestigationTests.test_document_cutoff_is_separate_from_replay_time`
- `tests.test_hybrid_retrieval.HybridRetrievalTests.test_document_checked_date_is_not_replay_as_of`
- `tests.test_evidence_gate.EvidenceGateTests.test_document_stamp_does_not_prove_historical_availability`

**当前覆盖：**新增专项 spy 检查数值补查的回放时间、文档截止时间及关闭内部审计的参数；历史可见性另做新旧对照。冻结请求仍不替代数据访问层校验。

### C04：逐结论证据检查与最终复核

**实现位置：**`app/evidence_gate.py::assess_evidence`、`verify_final_claims`、`finalize_answer`、`render_checked_answer`。

**输入与输出：**候选资料必须通过既有来源定位、版本、哈希、引文、数字、单位及支持契约检查。关系题的两端成立不能自动证明直接关系。模型只能选择已核验结论及允许的表达，不接受自由增加项目结论；旧客户端的自由生成文本不会进入最终结论。模型整理前后复核来源，失败时移除受影响结论及其依赖，只保留仍有效的部分。

**已有测试：**

- `tests.test_evidence_gate.EvidenceGateTests.test_direct_claim_has_its_own_exact_quote_and_locator`
- `tests.test_evidence_gate.EvidenceGateTests.test_two_endpoints_without_bridge_do_not_prove_inheritance`
- `tests.test_evidence_gate.EvidenceGateTests.test_tool_metric_without_version_sample_or_unit_is_not_admitted`
- `tests.test_evidence_gate.EvidenceGateTests.test_same_number_wrong_unit_fails`
- `tests.test_evidence_gate.EvidenceGateTests.test_hash_valid_injected_document_is_still_rejected`
- `tests.test_evidence_gate.EvidenceGateTests.test_real_model_selector_rejects_prose_unknown_citation_and_duplicate`
- `tests.test_evidence_gate.EvidenceGateTests.test_source_changed_during_model_selection_is_rechecked`

**当前覆盖及缺口：**新图实际节点路径、错误单位、关系缺少桥接及生成中来源失效已测试。各类机械检查不构成通用语义正确性证明，也不是所有来源变更的穷举。

### C05：保留轮次、预算及实际计数口径

**实现位置：**`app/investigation_core.py::InvestigationLimits`、`_Budget`、`_fill_gaps`，以及 `app/investigation_graph.py` 的节点和兼容入口。

| 配置 | 默认 | 当前构造器接受的范围 |
|---|---:|---|
| `max_rounds` | 3 | 2–3 |
| `max_tool_calls` | 8 | 1–12 |
| `max_llm_calls` | 4 | 2–6 |
| `max_seconds` | 60.0 | 1–180 |

**输入与输出：**最多三轮是首次取证加最多两轮补查，不是三次补查。无项目工具步骤时轮次为 0；有首次计划时为 1；补查实际执行后才更新轮次。一轮可包含多次工具调用，不能用图节点数替代工具次数或轮次。

工具 guard 在执行前预留额度，预留成功后发生读取失败不会退还；达到次数上限再尝试会标记超限，但不会增加计数。`use_llm=True` 时现有入口在首次查询前预留规划额度；闲聊回复通过 trace 再计一次，缺口审阅与最终整理分别预留。审阅会为最终整理保留模型额度。此处描述现有口径，不把它扩展成逐 HTTP 请求的完整用量账本，也不顺带修正既有计数行为。

**60 秒是协作式预算检查，不是进程级硬截止。** 检查发生于现有 guard、补查和调用边界，部分本地操作及异常整理可能超出时限。当前 DeepSeek 预留会把请求超时限制为 `min(20, max(1, remaining))` 秒；该实现也不是整个进程的强制取消机制。

**已有测试：**

- `tests.test_investigation.InvestigationTests.test_complete_evidence_does_not_add_an_unrequested_source`
- `tests.test_investigation.InvestigationTests.test_tool_limit_uses_verified_residue_for_partial_conclusion`
- `tests.test_investigation.InvestigationTests.test_time_limit_stops_before_followup`
- `tests.test_investigation.InvestigationTests.test_greeting_does_not_enter_tool_loop_and_counts_both_model_calls`

**当前覆盖：**新增上下界／错误消息、新旧三轮耗尽、最终模型额度耗尽、假时钟停止、工具失败保留额度和无节点重试测试；不存在生产自动重试。协作式时间预算仍不是硬截止保证。

### C06：结束条件、优先级与安全降级

**实现位置：**`app/investigation_graph.py::decide`、`followup`、`recheck`；`app/investigation_core.py::_check_fingerprint`、`finalize_result`；`app/evidence_gate.py::missing_items`、`finalize_answer`。

| 条件 | 当前停止原因及输出约定 |
|---|---|
| 无项目步骤 | `needs_clarification` 或 `chat_mode`；不进入工具循环 |
| 无证据缺口 | `evidence_complete`；不为凑轮数额外取证 |
| 剩余缺口全部冲突 | `source_conflict`；保留版本冲突，不让模型裁决 |
| 未实际执行可用补查 | `partial_evidence` |
| 补查后证据指纹不变 | `no_support_gain`；停止重复查询 |
| 正常耗尽取证轮次 | `round_limit` |
| 预算已标记超限 | `tool_limit`、`llm_limit` 或 `time_limit`；停止后续受限调用 |

预算超限在循环结束后及回答整理后覆盖停止原因；只有无缺口、有项目步骤且没有预算超限时，才能最终标记 `evidence_complete`。模型网络／配置失败时禁用后续模型调用，保留规则取证；无效模型计划可以回退本地规则。表中的 `partial_evidence` 是停止原因，不能与回答状态 `partial_verified` 混用。

**已有测试：**

- `tests.test_evidence_gate.EvidenceGateTests.test_two_versions_are_shown_without_model_arbitration`
- `tests.test_evidence_gate.EvidenceGateTests.test_no_gain_stops_early_instead_of_repeating_queries`
- `tests.test_investigation.InvestigationTests.test_invalid_initial_model_plan_falls_back_and_can_still_synthesize`
- `tests.test_investigation.InvestigationTests.test_model_cannot_synthesize_when_partial_result_exceeds_tool_limit`

**当前覆盖及缺口：**新增不可执行补查、轮次耗尽、末轮冲突与完整证据覆盖、模型故障禁用等对照；合成控制场景只证明指定分支顺序，不穷举全部条件组合。

### C07：保留外部接口与拒绝异常

**实现位置：**`app/investigation.py::run_investigation`；`app/router.py::QueryResult`、`UnsupportedRoute`、`_reject_unsafe_intent`；`streamlit_app.py` 及现有 CLI。

**输入与输出：**保留 `run_investigation` 的问题、两个时间参数、模型开关与客户端注入、数据路径、审计路径、`limits` 和 `clock` 参数及默认值。普通完成结果返回现有 `QueryResult`；危险／不支持请求继续抛出 `UnsupportedRoute`，不伪装成普通结果。拒绝在数据工具与模型规划调用前发生；它并不保证客户端对象从未构造或预算计数从未预留。首次查询继续生成最终使用的请求 ID。

**已有测试：**

- `tests.test_investigation.InvestigationTests.test_dangerous_request_is_rejected_before_model_planning`
- `tests.test_planner.PlannerTests.test_unsafe_request_rejected_before_planning`
- `tests.test_security.SecurityTests.test_dangerous_requests_rejected_before_tools`
- `tests.test_router.RouterTests.test_wrong_or_missing_parameters_fail_without_guessing`

**当前覆盖：**入口签名及默认值、异常、客户端与时钟注入均已对照；兼容入口现在执行 LangGraph，状态模块被入口和图使用，但自身仍无业务 IO。

### C08：保留完整、局部、回退、澄清与闲聊结果

**实现位置：**`app/evidence_gate.py::render_checked_answer`；`app/router.py::run_query`。

**输入与输出：**有完整有效结论时为 `verified`；仅部分成立时为 `partial_verified`，列明缺口；无有效结论时为 `fallback`，不编造缺失事实。歧义问题保留 `needs_clarification`；普通闲聊使用现有 `response_mode` 和回复，不凭空补项目证据。不统一改写所有分支的状态或把拒绝转成新状态。项目回答不把检索候选直接呈现为已核验结论。

**已有测试：**

- `tests.test_investigation.InvestigationTests.test_missing_document_without_budget_limit_still_uses_forecast_residue`
- `tests.test_investigation.InvestigationTests.test_no_residual_evidence_falls_back_without_budget_limit`
- `tests.test_planner.PlannerTests.test_greeting_general_and_ambiguous_have_no_project_evidence`
- `tests.test_streamlit_app.StreamlitAppTests.test_ambiguous_question_shows_graph_and_clarification_without_cards`
- `tests.test_streamlit_app.StreamlitAppTests.test_partial_answer_is_not_labeled_as_deepseek_full_answer`

**当前覆盖及缺口：**完整、局部、回退、闲聊和澄清已对照，既有 Streamlit AppTest 继续执行，CLI 另做离线 smoke；不要求真实模型文本逐字相同，也未穷举所有 UI 操作组合。

### C09：保留审计内容、隐私和失败路径

**实现位置：**`app/investigation_core.py::first_pass`、`audit_result`；`app/investigation_graph.py::audit`；`app/router.py::_write_audit`。

**输入与输出：**正常调查只在外层写一条记录，首次／补查 `run_query` 传 `audit_log=None`。记录请求 ID、UTC、问题哈希、两个时间参数、路由、轮次、工具／模型次数、停止原因、状态、批次、引用、trace 和错误数。原话、证据正文、密钥及临时 reasoning 不加入此审计记录；检索 trace 保留查询哈希而非查询原文。`audit_log=None` 时标记 `disabled`。

正常结果的审计写入 `OSError` 会标记 `failed` 并保留可查看结果。**拒绝分支的审计写入没有相同的异常兜底**，失败时可能先抛出 `OSError`；本阶段如实记录差异，不修改其行为或扩大保证。

**已有测试：**

- `tests.test_investigation.InvestigationTests.test_one_audit_record_without_question_text`
- `tests.test_question_graph.QuestionGraphTests.test_audit_contains_counts_not_raw_graph`
- `tests.test_hybrid_retrieval.HybridRetrievalTests.test_persistent_trace_contains_hashes_not_queries`

**当前覆盖及缺口：**正常／拒绝审计内容、写入失败差异及原话排除已对照，宿主开启 tracing 时显式关闭也有专项验证；未穷举所有元数据敏感组合。检查点不属于当前能力。

## 3. 第一阶段外层编排边界（已实施）

```text
初始化请求、运行上下文及预算
  → run_query(generate_answer=False, audit_log=None, tool_guard=budget.reserve_tool)
  → 首轮结果归整、模型可用状态与 attempted 初始化
  → 判断缺口与终止条件
      → 必要时执行一轮 _fill_gaps()
      → assess_evidence() 与前后支持指纹比较
      → 返回条件判断
  → finalize_answer()（预算／模型状态约束仍有效）
  → 更新预算计数与执行信息
  → 写审计并返回原 QueryResult
```

首次 `run_query` 已负责危险意图校验、拆题、路由、数值工具、文档检索及首次 `assess_evidence`。后续图不得重复规划或重复首次校验来装饰节点；`generate_answer=False` 只推迟项目回答收尾，不代表闲聊回复也一律不生成。

当前图已接管外层条件分支和循环。`question_graph.py` 仍描述用户子问题与依赖；它不是执行图。`run_investigation` 保持兼容入口；helper 已提取到 core，依赖方向为入口 → 图 → core，core 不导入入口或图。

第 4–9 步只提取编排 helper 并接入图。当时没有实现检查点和恢复；第 10–11 步新增可选持久模式；当前网页只用普通入口，持久化保留独立 CLI，不改变普通入口的默认行为，不引入并行或自动重试。检索、提示词和证据支持规则继续保持。

## 4. 独立类型接口与状态所有权

实现：`app/investigation_state.py`。只运行标准库导入；业务类型位于 `TYPE_CHECKING` 分支并使用延迟注解。生产入口和图会使用此模块，但其导入仍不加载业务执行器。

| 类型／函数 | 字段、默认值与职责 |
|---|---|
| `InvestigationRequest`（冻结 dataclass） | `question: str` 必填；`as_of_utc`、`knowledge_checked_on_or_before` 默认 `None`；`use_llm=False`；`thinking_enabled=None`。保留输入字节对应的原始字符串，不修剪或转换日期 |
| `BudgetHandle`（Protocol） | 只读访问 `limits`、`tool_calls`、`llm_calls`、`exceeded`；提供既有 `remaining()`、`check_time()`、`reserve_tool(tool)`、`reserve_llm(provider)`；由真实 `_Budget` 结构化满足，无需修改或继承它 |
| `BudgetSnapshot`（冻结 dataclass） | `tool_calls: int`、`llm_calls: int`、`remaining_seconds: float`、`exceeded: str | None`；只保存观察值，无扣减／重置接口 |
| `EvidenceFingerprint` | `tuple[tuple[str, str, tuple[str, ...], str], ...]`，与现有 `_check_fingerprint()` 的行结构一致 |
| `InvestigationState`（TypedDict） | `request`、`result: QueryResult | None`、`attempted: frozenset[str]`、`before_fingerprint: EvidenceFingerprint | None`、`llm_available: bool`、`budget_snapshot: BudgetSnapshot | None` |
| `InvestigationRuntime`（冻结 dataclass） | 显式注入 `provider: ChatClient | None`、`budget: BudgetHandle`、`forecast_db`、`sonar_db`、`catalog`、`root`（Path）及 `audit_log: Path | None`；字段均必填，不隐式构造客户端、路径资源或新预算 |
| `initial_investigation_state(request)` | 返回新字典；`result=None`、`attempted=frozenset()`、`before_fingerprint=None`、`llm_available=request.use_llm`、`budget_snapshot=None`；不生成请求 ID |
| `snapshot_budget(budget)` | 仅读取计数、`remaining()` 与超限原因，返回新快照；不调用 `check_time` 或 reserve，不重置时钟 |

**所有权与更新规则：**

1. `QueryResult` 是事实、候选、证据、回答、请求 ID、轮次及停止原因的唯一结果容器；不在图状态复制这些字段。首次查询前 `result=None`。
2. 请求冻结；`attempted` 和指纹是不可变值，后续更新使用替换。现有 `_fill_gaps` 使用可变 set，未来适配层需从状态建立本轮局部 set，再返回新的 frozenset。
3. 现有 `QueryResult` 仍是可变对象，TypedDict 不会自动复制它或生成历史快照。后续节点需要明确其更新所有权；当前类型不能保证深度不可变或恢复隔离。
4. `_Budget` 持有唯一权威计数、限制与时钟。快照可能已经过时；实际执行前仍必须由同一个 live budget 检查并预留。顺序执行下读取，不承诺并发原子快照。
5. `remaining_seconds` 可为负且 `exceeded` 仍为 `None`：预算只有在既有检查／预留时才标记超限。读取快照不得偷偷触发该状态变化。
6. 运行上下文不进入检查点；它可能间接持有客户端凭据，只能临时使用。持久模式单独记录请求、路径配置和预算元数据；Runtime 另有默认 None 的 `evaluation_paths`，用于固定恢复后的评估来源。冻结上下文仅禁止字段重绑定，不冻结客户端或预算内部状态。
7. 类型注解和冻结容器不执行领域校验；日期、工具权限和证据校验仍在既有实现。若后续框架运行时解析延迟注解，应由适配层显式提供业务类型命名空间，本阶段不为此引入运行时业务导入。

## 5. 本阶段验证与后续验收

新增 `tests.test_investigation_state.InvestigationStateTests`，以真实 `_Budget`、假时钟、模拟 IO 与临时目录验证：跨请求隔离、原话／两个时间范围保留、输入校验延后、快照不扣费不重置、工具／模型超限保持、运行依赖不创建路径，以及新解释器中的无副作用导入／初始化。

本阶段验收是新增专项测试及完整离线回归通过，并按修改前清单核对原文件与快照均未改变。文档引用的旧测试方法另做静态存在性校验。实际命令、计数、备份复核和新增清单记录在 `reports/langgraph_preparation_steps1_3_2026-10-28.md`。

以上为前三步交付时的验证范围。第 4–9 步已新增真实 LangGraph 节点和冻结旧流程对照；当前结果及剩余缺口见下节，不将合成分支场景或机械校验描述为通用正确性保证。

## 6. 第 4–9 步当前实现与迁移测试

当前执行器为 `app/investigation_graph.py`，独立职责见 core；冻结参考为 `tests/support/legacy_investigation.py`，只在测试使用。图使用解析延迟引用后的等价状态 schema，运行依赖由 context 注入；不改独立状态字段，也不将 runtime 放入可保存状态。

`tests.test_investigation_graph.InvestigationGraphTests` 新增 28 个测试方法，其中新旧对照规范化完整结果，仅排除随机请求 ID；审计对照另外排除请求 ID 和写入 UTC。调用方测试继续使用现有 183 项。以下为关键补测映射，全部方法及实际运行结果在工作报告中核验：

| 契约 | 新增迁移测试方法（以上述测试类为前缀） |
|---|---|
| C01/C02 | `test_all_numeric_routes_and_mixed_evidence_match_reference`、`test_document_complete_missing_original_and_relationship_match` |
| C03 | `test_historical_visibility_and_document_cutoff_match`、`test_followup_calls_keep_both_original_time_parameters` |
| C04 | `test_wrong_unit_cannot_become_a_verified_metric`、`test_source_invalidation_during_selection_is_rechecked_in_both_executors` |
| C05 | `test_limit_configuration_bounds_and_messages_match_reference`、`test_side_effecting_fake_clock_preserves_budget_stop`、`test_llm_exhaustion_during_finalization_keeps_checked_output` |
| C06 | `test_round_exhaustion_and_conflict_priority_match_for_else`、`test_complete_evidence_overrides_last_round_limit`、`test_no_executable_followup_preserves_partial_evidence_reason` |
| C07/C08 | `test_public_signature_types_and_frozen_reference_provenance`、`test_model_selection_greeting_general_and_clarification_match` |
| C09 | `test_normal_and_rejected_audits_match_without_exporting_question`、`test_normal_and_rejected_audit_failures_keep_existing_difference`、`test_host_tracing_setting_cannot_export_graph_questions` |
| 图结构／隔离 | `test_actual_graph_follows_complete_and_followup_edges`、`test_cached_graph_does_not_share_results_budgets_or_model_diagnostics` |

图节点为 initialize、first_pass、decide、followup、recheck、finalize、audit。条件边不读取时钟；预算快照只在 audit 末尾采集。`recursion_limit=64` 不代表 64 次工具调用，意外递归异常直接暴露。没有设置节点重试或缓存；外部 tracing 在执行入口显式关闭。

本轮快照为 `backup/11-pre-langgraph-orchestration-20261028-182911`，包含 105 个项目文件（802,404 字节）和安装前包清单，另记录 800 个历史备份文件的保护哈希。详细依赖变化、新增／修改清单、完整回归及 CLI 结果见 [第 4–9 步报告](../reports/langgraph_migration_steps4_9_2026-10-28.md)。

## 7. C10：可选持久化与恢复（第 10–11 步）

普通入口及 C01–C09 的原约定继续适用。新增 `start_persisted_investigation()`、`resume_investigation()`，供 `app.checkpoint_cli` 使用；网页移除保存／恢复入口，已有状态和后端能力保留；保存库独立于预测、声纳及评估证据。

- 每次调查使用随机执行 ID 与随机访问凭证；恢复不能覆盖原问题、时间条件、数据路径和预算上限。
- 明确保存 QueryResult、自定义类型、已尝试查询和图位置。编码器只接受白名单类型及既有字段版本，排除运行对象和原始模型诊断。
- 次数在实际调用前持久扣减，包含闲聊回复；UTC 截止时间包含暂停和停机。恢复不重置剩余额度。
- 已完成节点结果可复用；节点中途崩溃、调用结果未知时不自动重复，保留已扣次数并整理前一保存边界。
- 未完成调查恢复前复核相关来源；变化时移除失效证据，沿用既有校验，仅回答仍有依据的部分。完成结果再次读取属于历史快照，不做新调查。
- 持久模式使用独立库的唯一最终审计行，避免 JSONL 重复追加。普通模式仍按 C09 行为。
- 同一执行由进程锁串行恢复。检查点是整个节点边界，不提供任意代码行或 HTTP 内部续跑保证。

专项测试为 `tests.test_checkpoints.CheckpointTests`。操作、跨进程演示、超时与 UI 测试方法见 [指南](14_checkpoints_and_demo.md)，实际结果见 [本次报告](../reports/checkpoints_and_demo_2026-10-28.md)。

按用户偏好，本阶段保留源码快照和清单，但省去开发过程中的全项目及历史备份重复哈希扫描，并移除只比较固定摘要常量的冗余断言。C01–C04 的必要来源完整性检查及恢复身份校验保留。

## 8. 网页展示隔离验收（2026-10-28）

`streamlit_app.py` 调用纯展示模块 `app/presentation.py`，默认两页，调试开启后增加运行细节；仅展示层转换友好编号及已知批次元数据。Thinking 默认值与 DeepSeek 响应处理保持不变。最近结果会话缓存与旧恢复码键清理不改变后端状态库。

覆盖：`tests.test_presentation.PresentationTests` 验证真实预测展示、来源稳定去重、正常十六进制内容保留、原对象不变、调试切换一次调查及失败后清空；`tests.test_streamlit_app.StreamlitAppTests` 保留来源、澄清、局部回答与 Thinking 传参断言；`tests.test_checkpoints.CheckpointTests.test_streamlit_removes_recovery_controls_in_both_modes` 与 `test_streamlit_ordinary_submission_does_not_use_persistence` 替换旧网页恢复用例，后台恢复用例保持。

当前测试结果见 [界面报告](../reports/ui_debug_mode_2026-10-28.md)。历史阶段的报告及验收记录保持原样。

## 9. 网页证据与问题去重：当前新增约定

前述历史冻结点的 60／20 秒默认值与原入口签名不再代表当前新增能力。新调查总预算 120 秒、单次模型最多 40 秒；新增 `allow_web=False` 关键字，网页显式以默认开启的侧边栏开关传入，CLI 需 `--allow-web`。原有只读数值、时间闸门和异常约定保留。

问题身份规范化保留原始定位和来源／时间限定；内部与论文 Caution 不合并，官方 Flood Warning 不走项目内部 Warning。定义比较允许有依据的限定推导，直接因果／继承边界不放宽。

`WebSnapshot` 是不可变候选快照，经正文、来源、日期、引文及问题属性检查才生成结论。`QueryResult` 新增默认空的网页快照及执行记录；旧已知字段集合兼容读取，未知字段仍拒绝。旧持久任务缺联网字段按关闭，缺单次时限按 20 秒，原总预算和累计次数保留。

覆盖与真实测试位置：`tests.test_web_integration.WebIntegrationTests`，包括长短实体三节点、三个主题、在线失败、搜索摘要不授权、公开查询隔离、明确日期、原文损坏、共享额度、无效模型选择、旧格式、网络中断不重发、唯一审计和界面隔离。当前实测结果与范围见 [网页集成报告](../reports/web_answer_integration_2026-10-28.md)。
