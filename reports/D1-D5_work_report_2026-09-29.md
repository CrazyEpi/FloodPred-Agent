# FloodOps Agent D1–D5 工作报告

日期：2026-09-29  
工作目录：`C:\UCL\DissertationAgent`  
结论：**D5 最小 RAG 代码与本地验证已完成；真实 DeepSeek 调用待用户填入 API key 后做 smoke test。** 这是明确的剩余验证项，不能把假模型测试说成线上模型已跑通。

## 1. D1–D5 工作脉络

| 日程 | 已交付能力 | 关键产物 |
| --- | --- | --- |
| D1 | MVP 边界、服务/数据资产盘点与原资产备份 | `docs/01_scope.md`、`docs/02_asset_inventory.md`、`backup/source_snapshot_2026-09-29/` |
| D2 | 只读读取历史预测 run；按 `as_of_utc` 选当时已生成、已入库且有效的预测 | `app/archive.py`、`app/cli.py` |
| D3 | 无 LLM 的历史回放卡，含预测峰值、目标时间、run_id、内部等级和声纳双时间闸门 | `app/replay.py`、`app/demo.py` |
| D4 | 分层可信来源目录、关键词＋元数据检索、原文件/章节定位、缺失和版本冲突拒答 | `knowledge/catalog.json`、`app/knowledge.py`、`app/knowledge_cli.py` |
| D5 | DeepSeek 受限生成、两类知识问答、原文事实门禁、引用校验和缺资料拒答 | `app/rag.py`、`app/rag_cli.py`、`tests/test_rag.py`、`docs/05_rag_contract.md` |

D2/D3 的结构化预测查询并未强行改成 RAG。D5 只负责**文档型**问题：内部 Watch 规则、模型局限。两类数据源职责分明，避免让语言模型凭记忆给出历史预测数值。

## 2. 先备份、再开发；单一环境清理

D5 动手前，新建 `backup/d1_d4_snapshot_2026-09-29/`，复制既有 `app/`、`docs/`、`reports/`、`tests/`、`knowledge/`、README 和 `.gitignore`。共 **31 个文件、123,439 字节**，与原件逐项 SHA-256 校验，**0 个不一致**。此快照不重复复制较大的原资产备份或虚拟环境；既有所有备份目录未作为开发写入目标。D1–D4 的代码、目录文件和工作报告都未改动。原 `D1-D2_work_report_2026-09-29.md` 保留；D1–D4 报告不再是必须保留项，但本轮没有删除它。

遵照用户“只维护一个 Python 环境”的新要求，确认清理目标为工作目录内的 `.venv`（861 文件，11,988,266 字节）和 `.venv-d3`（1,122 文件，21,945,499 字节），绝对路径均位于 `C:\UCL\DissertationAgent` 且名称精确匹配；`Get-Process` 未发现运行中的项目 Python 进程。**仅删除这两个旧虚拟环境**，它们没有被纳入代码/数据快照，可以从 `requirements.txt` 重建。保留 `.venv-d4` 作为唯一项目环境（Python 3.12.14，Pydantic 2.13.5）；它能运行 D1–D5 全部功能。旧 D1–D3 历史报告中的 `.venv` 命令描述属于当时环境记录，不再是当前运行命令；新命令一律使用 `.venv-d4`。未删除模型、数据库、原始文件或报告。

## 3. D5 的实现与证据控制

`app/rag.py` 先按确定性规则将问题路由至 `internal_watch` 或 `highwater_evaluation_limit`。随后调用 D4 `search()`，要求唯一目标概念、正确来源类型、active 版本、完整原文件与哈希/行号检查。内部 Watch 必须在原文中看到 `4.20m <= level < 4.43m`；模型局限必须看到 Watch 阈值实测目标点为 0、以及报告明确的“不能用于证明”结论。**任何一步失败，DeepSeek 都不会收到请求。**

证据通过后才调用 DeepSeek `POST /chat/completions`。默认模型 `deepseek-flash`，关闭思考模式，要求 JSON：短解释、逐字支持引文、引用 ID。返回后逐项核对：引用 ID 精确匹配、引文是检索原文片段、解释数字均在证据内，且内部规则/模型局限各自的关键结论不缺失；拒绝若干无依据的官方状态或行动断言。最终显示**目录中已核验的事实**与**模型生成的短解释**，并附原文件行号。这个检查能拦住一些典型幻觉，但不是通用语义事实核查，详见 `docs/05_rag_contract.md`。

密钥放在项目根目录的 `.env` 或 `DEEPSEEK_API_KEY` 环境变量。新建 `.env.example` 只有 `PASTE_YOUR_DEEPSEEK_API_KEY_HERE` 占位符；实际 `.env` 已在现有 `.gitignore` 中忽略。程序不会把 key 写进输出、报告或备份；HTTP 失败也不打印返回正文。

## 4. 两类问题的预期答案

1. `项目内部 Watch 是什么`：基于 `cloud_flood_server/README.md` 的“风险等级”第 13–20 行，说明这是内部 level 1，预测最高水位 `4.20 m <= level < 4.43 m`。**不是** Environment Agency 官方 Flood Alert，也不是 House Mill 行动 SOP。
2. `模型局限是什么`：基于 `HIGH_WATER_EVALUATION_REPORT.md` 的“风险阈值背景”第 71–78 行，指出达到内部 Watch 4.20 m 阈值的不同实测目标点为 **0**，因此这批评估无法证明 Watch/Warning/Severe 事件召回率。可评价已观测水位区间内的数值误差，但不能推断未出现的洪水事件检出能力。

检索命中只意味着“可能相关”，不等于生成答案受到支持。D5 的概念门禁、原文模式、逐字引文和引用 ID 检查是为弥补此差异。模型不从自身记忆补充缺失的规则/评估结论。

## 5. 用户如何配置并测试

在 PowerShell 中：

```powershell
cd C:\UCL\DissertationAgent
Copy-Item .\.env.example .\.env
notepad .\.env
```

将 `.env` 里的 `PASTE_YOUR_DEEPSEEK_API_KEY_HERE` 换成你的真实 key，保存；**不要把 key 发给我或贴进报告**。然后执行：

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv-d4\Scripts\python.exe -m app.rag_cli --question '项目内部 Watch 是什么'
.\.venv-d4\Scripts\python.exe -m app.rag_cli --question '模型局限是什么'
```

成功时应看到“已核验事实”“简短解释（deepseek-flash）”“来源类型”“引用定位”四部分。没有 key 时会明确提示先配置，不会悄悄输出无 LLM 的伪答案。调用 DeepSeek 可能产生 API 费用；首次配置后这两条命令就是最小线上 smoke test。不要对真实备份文件做删除实验。

无需 key 即可重现本次本地验证：

```powershell
.\.venv-d4\Scripts\python.exe -m unittest discover -s tests -v
.\.venv-d4\Scripts\python.exe -m app.knowledge_cli --question '项目内部 Watch 是什么'
.\.venv-d4\Scripts\python.exe -m app.demo --as-of 2026-08-03T01:48:00Z --question '当时预测峰值是多少'
```

本次 **16/16 个聚焦测试通过**：D1–D4 原有 11 个，D5 新增 5 个。D5 测试用临时目录模拟目录记录被删、原文文件缺失，均在模型调用前拒答且假模型调用次数为 0；还覆盖错误引用 ID、虚构引文、证据外数字、两类正常问答，以及无网络的 DeepSeek 请求结构检查。真实 D3 回放与 D4 关键词检索仍可在唯一环境中运行。

## 6. 仍需用户完成的外部条件与后续风险

本轮未发现可由当前 Codex 进程读取的 DeepSeek key；用户表示之后会自行填入。因此**没有真实 DeepSeek 网络响应记录**，不能声称 D5 线上生成已验证。待填 key 后按第 5 节执行两问；如返回接口错误，需要核对 key 权限、余额和当时的模型名称。接入方式按 2026-09-29 核对的 [DeepSeek 官方首次调用指南](https://api-docs.deepseek.com/guides/harness)、[Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/) 和 [JSON Output](https://api-docs.deepseek.com/guides/json_mode/) 编写。

此外，引用存在不等于复杂断言必然正确。当前只对两个窄领域意图设置强门禁；不要外推为开放式洪水行动助手。未获得 House Mill 场地管理者批准的专属 SOP，不提供此类指令；官方 Flood Alert 当前状态仍须查官方实时服务。
