# P1-1 工作报告｜多步调查与自我纠错

日期：2026-10-01。工作目录：`C:\UCL\CASA0016\FloodPred-Agent`。旧目录 `C:\UCL\DissertationAgent` 未改动。

## 已完成

修改前先把目标目录当时的代码、知识、演示数据、文档、报告、测试和根目录 README 复制到 `backup/pre_p1_1_2026-10-01/`。旧备份没有修改。继续使用目标目录已有的 `.venv-d4`，未安装新依赖。`.env` 保留在目标目录，仍被 Git 忽略；新增不含密钥的 `.env.example`。

新增 `app/investigation.py`：

1. 第 1 轮沿用原有安全检查、DeepSeek 可选规划及确定性路由，执行已核验的只读工具。
2. 查看目前的来源、错误和已尝试工具。对“区别／关系／为什么”等复合问题，采用少量明确的本地缺口规则；若启用 DeepSeek，它也可提出补查建议，但建议需经过工具 ID 白名单、问题相关性和去重检查。
3. 最多补查两轮；最后只对实际取得、通过原有来源验证的证据进行综合解释。若证据失败或预算耗尽，不生成无依据的完整结论。

限制：最多 3 轮、8 次只读工具调用、4 次 LLM 方法调用，默认 60 秒协作式总截止时间。DeepSeek 单次 HTTP 超时被压到最多 20 秒及剩余预算以内。Python 无法安全中断正在执行的本地函数，因此总截止时间不是操作系统级硬中断。执行结果记录轮数、调用数、停止原因及每轮 trace。

原有 `app/router.py` 的单轮 `run_query` 仍保留；只增加内部强制计划、延后生成和工具调用前计数的入口。强制计划必须由现有四类只读工具及允许的知识概念组成。`app/rag.py` 增加一次证据缺口检查和单次网络超时配置。Streamlit 默认使用多步调查，取消“多步调查（最多 3 轮）”即可回到原单轮流程。新增 `app/investigate_cli.py` 用于命令行复现。

根目录旧的 `README_D3.md`、`README_D4.md`、`README_D5.md`、`README_D6_D7.md` 已删除；可提交工作树只保留 `README.md`。这些旧文档在修改前备份中仍可恢复。备份被 `.gitignore` 排除。发现旧 Git 索引已跟踪 `.venv-d4` 的 6479 个文件，因此用 `git rm --cached` **仅从索引移除**，本机虚拟环境及其 Python 可执行文件未删除，重新核对 `sys.prefix` 仍指向新目录。提交这批索引删除后，`.gitignore` 才能阻止虚拟环境继续同步到 GitHub；尚未执行提交或推送。

## 演示路径

关闭 DeepSeek 时问“项目内部 Caution 和英国官方预警有什么区别？”：第一轮读取内部等级，缺口检查发现需要区分英国官方定义，第二轮补查 GOV.UK 的静态来源，得到 2 轮、2 次只读工具调用、2 条来源。这里没有查询当前官方警报，也没有把内部类别等同官方类别。开启 DeepSeek 时，模型如果首轮就选择了两条来源，会在 1 轮结束；轮数少不代表补查失效。

另一个示例是“当时预测峰值和 MAE 的联系是什么？”：归档预测与事后评估分别取证，不能把批次 MAE 说成这次峰值误差。任何回放工具仍使用原有 `as_of_utc` 时间闸门，事后评估明确标为事后资料。

## 安全与失败处理

- 没有增加任意 SQL、文件写入、MQTT 发布或告警发送工具。原有 SQLite 只读连接、评估 JSON 指纹校验和知识来源校验不变。
- 恶意／无关的模型补查建议不能执行；同一工具或同一知识概念不重复补查。缺失文档、来源冲突或调用预算耗尽时保留局部证据并显示错误。
- DeepSeek 首轮计划格式不合规时，退回本地路由，仍可在证据充分时尝试后续解释；网络或密钥不可用时不反复发起模型请求。
- 审计日志每次调查只追加一条元数据记录：请求哈希、路由、轮数、调用数、来源 ID、`run_id`、停止原因和 trace；不记录问题原文、API key、证据正文或思维链。危险请求在模型调用前拒绝并记录拒绝状态。
- 这仍是**历史回放作品集**，不是线上洪水预警产品。当前版本没有事后逐点误差归因工具，不能回答“某次预测为什么偏高”的因果结论；部署期数据也未验证真实超阈值洪水事件检出能力。

## 验证记录

- `.venv-d4` 的 `sys.prefix` 指向新工作目录。
- 本机完整自动化套件：66/66 通过。
- `FLOODPRED_DATA_MODE=demo` 下，P1-1 与 Streamlit 专项：13/13 通过。
- 真实 DeepSeek 小规模联调：示例问题取得内部规则与 GOV.UK 静态定义两个 citation，并生成限制明确的解释。这次 API 在首轮取得两条来源，因此实际只跑 1 轮；二轮补查由确定性运行与离线测试验证。
- 从新工作目录启动 Streamlit 后，`http://127.0.0.1:8501/_stcore/health` 返回 HTTP 200 `ok`；这只验证服务启动，不等于浏览器端到端或实时数据验证。
- 未在本次重跑原 D8 的 20 条系统评估案例，README 中的 20/20 是此前记录，不应误当作本次测试成绩。

## 你如何测试

在 PowerShell 中：

```powershell
cd C:\UCL\CASA0016\FloodPred-Agent
.\.venv-d4\Scripts\python.exe -m app.investigate_cli "项目内部 Caution 和英国官方预警有什么区别？"
.\.venv-d4\Scripts\python.exe -m unittest tests.test_investigation -q
.\.venv-d4\Scripts\python.exe -m streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false
```

命令行示例无需 API key，应出现 `rounds=2`、`read_only_tool_calls=2` 和两个来源 ID。网页打开 `http://127.0.0.1:8501`；保留“多步调查”，先取消 DeepSeek，再输入同一问题。结果页看两条资料，证据页核对来源，运行细节看两轮 trace。若要体验自然语言解释，可在本机 `.env` 配好 key 后勾选 DeepSeek；模型可能首轮就把两条来源都找齐，这属于正常结果。取消“多步调查”可验证旧单轮路径未被替换。

仅当你需要重新运行完整测试时：

```powershell
.\.venv-d4\Scripts\python.exe -m unittest discover -s tests -q
$env:FLOODPRED_DATA_MODE='demo'
.\.venv-d4\Scripts\python.exe -m unittest tests.test_investigation tests.test_streamlit_app -q
```

同步 GitHub 前检查 `git status --short` 和 `.gitignore`。你会看到大量 `.venv-d4` 的**暂存删除**：这是清理旧索引，不是本机文件被删除；与代码变更一同提交后，远程仓库才会停止跟踪环境。不要 `git add -f` 加入 `.env`、`.venv-d4`、`backup` 或 `logs`。本次没有替你提交或推送。
