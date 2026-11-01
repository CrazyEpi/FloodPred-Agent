# 本机检查点、恢复演示与测试方法

更新：2026-10-28（Asia/Hong_Kong）。已实现 LangGraph SQLite 检查点、跨进程恢复、持久预算、节点结果记录及唯一最终审计。普通 `run_investigation()` 继续临时执行；持久化是显式开启的功能。

## 1. 运行和自动验收

在 PowerShell 中进入项目，沿用原环境：

```powershell
Set-Location C:\UCL\CASA0016\FloodPred-Agent
.\.venv-d4\Scripts\python.exe -m pip install -r requirements.txt
Remove-Item Env:FLOODPRED_DATA_MODE -ErrorAction SilentlyContinue
.\.venv-d4\Scripts\python.exe -X utf8 -m unittest tests.test_checkpoints -v
.\.venv-d4\Scripts\python.exe -X utf8 -m unittest discover -s tests -q
.\.venv-d4\Scripts\python.exe -m pip check
```

专项测试使用临时 SQLite、模拟客户端和假时钟，不调用真实模型。包括实际跨进程恢复、序列化、停机超时、额度不重置、来源失效、未知调用结果不重试、节点结果复用、唯一审计、恢复码隔离和网页不使用持久化的隔离检查。当前实际计数和运行结果见 [工作报告](../reports/checkpoints_and_demo_2026-10-28.md)。

完整回归使用本项目现有完整来源目录；所以上面先清除演示模式变量。下方手动演示再明确启用精简的 demo 数据，不需要原云服务器。

依赖固定为 LangGraph 1.2.18、`langgraph-checkpoint-sqlite==3.1.2`，保留原 Pydantic、NumPy 和 Streamlit 版本。

## 2. 一条命令演示真正的跨进程恢复

```powershell
$env:FLOODPRED_DATA_MODE = 'demo'
.\.venv-d4\Scripts\python.exe -X utf8 -m app.checkpoint_cli demo
```

这个演示先在首次取证结束后暂停，再启动另一个 Python 进程恢复，不是重新执行一次相同问题。

预期输出：

- `separate_process=true`、`same_execution=true`、`same_replay_time=true`、`budget_not_reset=true`。
- `before.status=paused`，下一节点为 `decide`；`after.status=completed`。
- 对内部 Caution 与官方预警的比较题，已有内部规则，但官方原文不足；补查后得到 `partial_verified` 和 `no_support_gain`。
- 典型工具次数从 1 变为 3，模型次数为 0；累计次数保留，不从 0 开始。

问题仍然是历史解释，演示不会发布预警或查询当前现场水情。

## 3. 手动暂停和恢复

复制以下命令可在新任务默认 120 秒内直接完成：

```powershell
$checkpointRun = .\.venv-d4\Scripts\python.exe -X utf8 -m app.checkpoint_cli start "项目内部 Caution 和英国官方预警有什么区别？" --pause-after first_pass | ConvertFrom-Json
$checkpointRun | Select-Object execution_id,status,next_nodes,tool_calls,llm_calls,as_of_utc,deadline_utc
.\.venv-d4\Scripts\python.exe -X utf8 -m app.checkpoint_cli resume $checkpointRun.resume_token
```

也可以关闭启动进程，在新 PowerShell 中设置相同工作目录与数据模式，再使用输出中的完整恢复码运行 `resume`。恢复不接受新的问题、回放时间、文档截止时间或预算上限；这些条件来自原调查。

`--pause-after followup` 可暂停在补查完成、重新校验之前。模型默认关闭；`--llm` 才使用原 DeepSeek 配置。手动暂停不会停止计时，超过截止时间后只整理已保存的核验部分。

CLI 成功创建、暂停、恢复或读取完成结果退出 0；参数／恢复码／拒绝错误退出 2；执行中断且已保留恢复码退出 3。具体回答是否完整以 `answer_status` 和 `stop_reason` 判断。旧 `app.investigate_cli` 的退出规则保持原样。

### 验证停机时间计入预算

```powershell
$checkpointRun = .\.venv-d4\Scripts\python.exe -X utf8 -m app.checkpoint_cli start "当时预测峰值是多少？项目内部 Caution 是什么？" --pause-after first_pass --max-seconds 1 | ConvertFrom-Json
Start-Sleep -Seconds 2
.\.venv-d4\Scripts\python.exe -X utf8 -m app.checkpoint_cli resume $checkpointRun.resume_token
```

预期 `stop_reason=time_limit`，不再新增工具／模型调用。保留中断前已保存且仍有效的证据，若不足则说明缺口；并非总能形成完整回答。

### 验证完成结果不会重复执行

对一个已完成调查，再运行同一条 `resume`。工具／模型累计次数和保存结果保持不变；状态库中该 execution 仍只有一条最终审计记录。完成结果属于历史快照读取，不是对今天来源有效性的重新证明；需要重新取证时应新建调查。

## 4. Streamlit 测试

```powershell
.\.venv-d4\Scripts\python.exe -m streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8501
```

当前网页没有保存进度、恢复码、恢复输入框或恢复按钮；这些功能也不会在调试模式出现。后端和 CLI 恢复继续可用，已有调查无需删除，使用上方 CLI 命令读取。

已有 8501 服务运行时直接刷新页面，不另启服务。手动验收：

1. 确认侧边栏“调试模式”默认关闭。取消“用 DeepSeek 理解和回答”，保持“多步调查”，点击“查看结果”。
2. 普通模式只有“回答”和“证据”，峰值应为 `3.7832 m`，峰值日期与 UTC 保留，正文用“来源 1”等引用；原文和资料版本仍可核对。没有 `run_id`、`q1-tool`、`forecast:`、长批次值、哈希或完整本地路径。
3. 开启调试模式，出现“运行细节”，可查看原始回答和引用映射、问题图、运行 JSON、trace；证据中显示内部编号、来源定位及哈希。仅有实际返回的模型诊断才会显示，不为展示而调用模型。
4. 关闭调试模式，技术字段再次隐藏，数值、日期和友好引用不变。切换只重新渲染会话结果，不再次调用模型／工具；未提交的表单编辑不发起调查。
5. 提交“本批次 MAE 是多少”核对单位，再提交不支持的问题“明天适合去野餐吗”，应显示错误且不残留上次的指标和回答。澄清题、局部回答仍展示缺口。
6. Thinking 开关保持独立，默认开启；调试模式仅控制返回内容是否展示，不改变请求参数。不要在调试页或截图中粘贴密钥。

自动验证（模拟模型，不消耗实际 API）：

```powershell
.\.venv-d4\Scripts\python.exe -X utf8 -m unittest tests.test_presentation tests.test_streamlit_app tests.test_checkpoints -v
```

恢复码包含随机执行 ID 与随机访问凭证；持有码可以读取这次本机调查。它提供调查间隔离，不是带登录账号的多租户权限系统。

## 5. 保存内容、恢复规则与限制

默认状态库为 `state/investigations.sqlite3`，可用 CLI `--state-db` 指定独立文件。`state/` 已被 Git 忽略，源码备份不复制其中的私有问答状态。状态库包含问题、候选资料、证据和结果；运行中的客户端、API 密钥、连接及原始模型思维链不写入。JSON 序列化采用版本和类型白名单，不使用 pickle 或动态导入。

状态库不能指定为已有预测／声纳数据库；遇到已有其他业务表的数据库会拒绝写入。恢复码与执行 ID 绑定，原请求及数据路径被冻结。同一 execution 使用操作系统文件锁，进程退出时锁自动释放，避免并发恢复。

| 恢复情况 | 行为 |
|---|---|
| 已保存的节点边界 | 从后续节点继续，保留已消耗预算 |
| 节点结果已记录，图检查点未完成写入 | 复用节点结果，不重发已完成节点的调用 |
| 节点中途崩溃，调用结果未知 | 保留已扣额度，停止自动重发，整理前一保存边界的证据；`interrupted_inflight` |
| 绝对截止时间已过 | 不新增调用，返回已保存部分；`time_limit` |
| 数据文件、知识版本或来源变化 | 撤下受影响数据，按既有证据规则重新校验，停止补查；`source_changed` |
| 状态版本不兼容或内容损坏 | 拒绝恢复，提示新建调查 |
| 已完成调查 | 直接读取保存结果，不重新运行或重复审计 |

新任务默认总时限为 120 秒，包含暂停和停机时间；工具／模型次数在调用前持久扣减，恢复不重新发放额度。持久化预算以绝对 UTC 截止时间为依据，仍是协作式检查，不是进程级强制取消。

恢复的来源一致性主要通过版本和文件元数据发现变化，并对已有结论执行必要的精确来源校验；不扫描整个项目或所有历史备份的 SHA-256。证据摘录、评估快照和访问凭证所需的完整性／身份校验保留。

持久运行审计存放在状态库 `floodpred_audits`，每个 execution 一条记录，以幂等 upsert 更新最终摘要；不向旧 JSONL 重复追加。普通临时运行继续使用原 JSONL 审计。

当前恢复粒度是整个图节点，不保证从任意代码行或某个 HTTP 调用内部继续。未知调用不会自动重试，因此可能舍弃该节点尚未提交的部分结果；保留前一完整边界与已扣预算。这也不宣称外部 API 具有 exactly-once 语义。

## 6. 简历表述

可使用与已交付能力一致的描述：

> 基于 LangGraph 编排拆题、只读查询、混合检索、证据校验及定向补查，统一管理状态、调用预算和终止条件；支持 SQLite 检查点持久化与节点边界恢复，保留回放时间和已消耗额度，并验证跨进程恢复及局部回答。

恢复能力不等于实时洪水预测、官方告警服务或已上线的多用户生产系统。

## 7. 联网快照与旧状态兼容

调查与检查点 CLI 均需显式传 `--allow-web` 才联网。网页仍无恢复入口。新任务默认模型单次上限 40 秒、总计 120 秒；旧任务沿用保存的截止时间，缺少单次限制字段时按 20 秒处理；旧请求缺少联网字段时按关闭恢复。

网页原始 HTML、提取段落与来源元数据随 `QueryResult.web_evidence` 保存，原始模型诊断继续排除。恢复只复用并复核这次保存的网页快照，不宣称再次验证当前网站；新调查才重新读取网页。补查节点中途失败时，不重发未知结果的请求，已扣次数保留。

离线演示保持原命令。联网恢复示例（有网络请求，不调用 DeepSeek）：

```powershell
$checkpointRun = .\.venv-d4\Scripts\python.exe -X utf8 -m app.checkpoint_cli start "项目内部 Caution 和英国官方预警有什么区别？" --allow-web --pause-after followup | ConvertFrom-Json
.\.venv-d4\Scripts\python.exe -X utf8 -m app.checkpoint_cli resume $checkpointRun.resume_token
```

当前运行结果见 [网页集成报告](../reports/web_answer_integration_2026-10-28.md)。旧报告计数与默认值作为历史记录保留。
