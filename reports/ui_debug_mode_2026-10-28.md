# 网页精简与调试模式工作报告

日期：2026-10-28（Asia/Hong_Kong）。本次仅调整展示、相关测试与文档；LangGraph 编排、证据判断、时间闸门、DeepSeek 客户端及后端持久化逻辑保持。

## 备份与基线

- 首次修改前创建 `backup/13-pre-ui-debug-20261028-210053`，未覆盖历史目录。
- 快照包含 118 个项目文件，共 948,920 字节；`MANIFEST.json` 记录香港时间、原 Git 提交、工作区状态、文件相对路径和字节数，包含先前修改的 `.env.example` 及前三阶段交付。
- 排除真实 `.env`、虚拟环境、Git、旧备份、活动日志、缓存及运行状态。源资产旧备份保持原位，没有运行备份清理脚本，也没有重复扫描项目或历史备份哈希。
- 修改前使用 `.venv-d4` 跑完整离线回归：234 项通过。记录在 `logs/ui_debug_baseline_20261028.log`。这些是本次实测结果，区别于历史报告。

## 只读 API 核对与服务边界

本任务确认的先前只读核对结果：程序优先读取服务进程 `DEEPSEEK_API_KEY`，无覆盖时读取项目根目录 `.env`；核对时进程无覆盖，当前有效密钥来自该文件。DeepSeek `GET /models` 在允许联网的环境返回 HTTP 200，列表含当前模型 `deepseek-v4-pro`。受限执行环境此前出现 WinError 10013，不能据此判定密钥无效。

这只证明模型列表接口和密钥可用，不代表完整聊天生成或超时问题已验证。本次不再追加真实模型生成测试；没有改密钥、模型、Thinking 默认值、超时预算、提示词、客户端或响应处理。8501 已核对为当前项目环境，本次没有停止或重启服务。

## 实际交付

1. 网页删除保存进度复选框、恢复码输出、恢复输入／按钮及其持久化导入和调用；清理遗留 `last_resume_token` 会话键。后端检查点、CLI、状态库和已保存调查保留。
2. 侧边栏新增默认关闭的“调试模式”。普通模式仅“回答／证据”；开启后增加“运行细节”，开放内部编号、来源定位、哈希、检索细节、运行 JSON、原始回答、引用映射、问题图、trace 和已有模型诊断。没有新增密钥、请求头或 `.env` 展示。
3. 新增纯展示模块 `app/presentation.py`：按实际已核验引用首次出现顺序编号；相同文件与版本的段落共用来源号，同一结论的重复来源号去重。仅转换已知引用标记、结论／问题标签、预测批次等元数据，不全局删除十六进制串，不增加事实或引用。
4. 工具证据以标题、时间、单位和版本展示；技术定位仅在调试模式出现。原文保留，数字、回放日期、局部回答及缺口说明保留。原 `QueryResult`、源文件、审计、CLI／API 输出及检查点格式不变。
5. 最近结果存于当前 Streamlit 会话；切换调试只重新展示，不调用调查入口、模型或工具。新提交在执行前清理旧结果，支持异常后不残留旧指标。会话显示缓存不等同于持久恢复。
6. Thinking 仍独立控制原请求，默认开启；更新标签与帮助，说明返回的思维链仅在调试模式可见。
7. README、架构、迁移契约和恢复指南同步当前界面；服务操作说明中的旧 `DissertationAgent` 路径改为当前目录。

## 实际验证

| 验证 | 结果 |
|---|---|
| 修改前完整离线回归 | 234 项通过 |
| 更新后的界面与检查点专项 | 32 项通过（原 9 项界面、23 项检查点） |
| 新增展示专项 | 5 项通过 |
| 最终完整离线回归 | 239 项通过 |
| `.venv-d4` 的 `pip check` | `No broken requirements found.` |
| `git diff --check` | 通过；既有 LF／CRLF 提示不属于失败 |
| 按本次清单核对变更范围 | 118 个快照文件存在且字节数符合清单；当前 8 个文件属于批准修改，其余 110 个与快照逐字节一致，未做重复哈希扫描 |

真实本地预测验证仍得到 `3.7832 m`、峰值时间 `2026-08-03T16:15:00Z` 及原单位和限制说明；普通界面不出现原 run_id、完整批次、`q1-tool`、`forecast:`、哈希或本地绝对定位。调试打开后恢复技术信息，关闭后显示与打开前一致，模拟调查入口只调用一次；原证据对象保持相等。

覆盖来源编号稳定及跨结论去重、正常十六进制内容保留、局部回答、澄清、候选来源隔离、Thinking 传参、两种模式均无恢复控件，以及不支持／意外失败后清空旧结果。两项旧网页恢复测试替换为网页移除控件和不调用持久化入口的隔离测试；其他后端恢复测试、时间泄漏、证据校验及新旧编排对照保留。完整回归包含临时 SQLite 的跨进程恢复演示，不使用已有状态库。

测试日志：`logs/ui_debug_targeted_20261028.log`、`logs/ui_presentation_20261028.log`、`logs/ui_debug_regression_20261028.log`。AppTest 的 ScriptRunContext 提示属于模拟运行环境；“模拟失败”栈是故意注入的异常清理用例，测试本身通过。

## 测试方法

在项目目录使用原环境：

```powershell
Set-Location C:\UCL\CASA0016\FloodPred-Agent
Remove-Item Env:FLOODPRED_DATA_MODE -ErrorAction SilentlyContinue
.\.venv-d4\Scripts\python.exe -X utf8 -m unittest tests.test_presentation tests.test_streamlit_app tests.test_checkpoints -v
.\.venv-d4\Scripts\python.exe -X utf8 -m unittest discover -s tests -q
.\.venv-d4\Scripts\python.exe -m pip check
```

网页直接刷新已运行的 8501 页面，取消 DeepSeek 后提交默认问题，检查数值／来源；切换侧边栏调试模式，观察第三页与技术字段出现及隐藏。两种模式均无恢复功能。详细手动步骤及 CLI 恢复命令见 [恢复与展示指南](../docs/14_checkpoints_and_demo.md)。测试没有真实模型生成费用；本次未操作已运行服务。

## 文件与保留范围

新增 `app/presentation.py`、`tests/test_presentation.py` 和本报告。相对快照修改 `streamlit_app.py`、`tests/test_streamlit_app.py`、`tests/test_checkpoints.py`、`README.md`、`docs/06_local_service_operations.md`、`docs/08_architecture.md`、`docs/13_langgraph_migration_contract.md`、`docs/14_checkpoints_and_demo.md`。

没有修改业务编排、模型调用、依赖声明、`.env` 或 `.env.example`；没有修改知识、演示数据、既有报告或历史备份，没有删除状态库或保存调查，没有提交／推送 Git。当前结果只保存在本次网页会话，刷新／断开后可用性受 Streamlit 会话生命周期约束；持久恢复请继续使用 CLI。
