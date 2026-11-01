# FloodPred 本地网页服务操作说明

## 服务在哪里

- 项目目录：`C:\UCL\CASA0016\FloodPred-Agent`
- 网页程序：`C:\UCL\CASA0016\FloodPred-Agent\streamlit_app.py`
- Python 环境：`C:\UCL\CASA0016\FloodPred-Agent\.venv-d4`
- 本机访问地址：<http://127.0.0.1:8501/>

这是运行在**你自己的 Windows 电脑上**的 Streamlit 服务，没有部署在云服务器。`127.0.0.1` 只供本机访问；关闭运行它的 Python 进程或重启电脑后，网页就不能访问。勾选“用 DeepSeek 理解和回答”后，服务端才会向 DeepSeek API 发出请求，可能产生费用；不勾选时不需要访问 DeepSeek。

它不会随浏览器或电脑自动启动。若访问地址打不开，先按下文的“如何重新打开”启动服务。2026-10-28 已核对 8501 为当前项目环境；本次界面修改没有停止或重启服务。运行状态会变化，请按健康接口检查，已有服务时直接刷新页面。

## 如何正常关闭

找到启动服务的 PowerShell 窗口，按 `Ctrl+C`。等命令提示符重新出现后，服务就已退出。**只关闭浏览器标签页不会停止服务。**

如果找不到启动窗口，在 PowerShell 中运行：

```powershell
netstat -ano -p tcp | findstr "127.0.0.1:8501"
```

只读取 `LISTENING` 那一行末尾的 PID（不要取 `TIME_WAIT` 或浏览器连接的 PID）。先核对进程，再停止这个**具体 PID**：

```powershell
Get-Process -Id <PID> | Select-Object Id,ProcessName,Path
Stop-Process -Id <PID>
```

将 `<PID>` 换成刚查到的数字，不要照抄尖括号。如果查询结果不是预期的 Python 服务，先不要停止它。PID 每次启动可能改变，不能长期照搬旧数字。如果 `Stop-Process` 提示权限不足，可在有相应权限的 PowerShell 中重试。

## 如何重新打开

打开一个新的 PowerShell 窗口，逐行运行：

```powershell
cd C:\UCL\CASA0016\FloodPred-Agent
.\.venv-d4\Scripts\python.exe -m streamlit run streamlit_app.py --server.headless true --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
```

看到 `URL: http://127.0.0.1:8501` 后，在浏览器打开 <http://127.0.0.1:8501/>。保持这个 PowerShell 窗口运行；需要停机时回到该窗口按 `Ctrl+C`。不必恢复旧云服务器，也不必另建 Python 环境。请在普通的、允许访问互联网的本机 PowerShell 中启动；此前 Codex 的受限执行环境会阻断 DeepSeek 网络请求。

首次验证可使用默认问题“当时预测峰值是多少？项目内部 Warning 是什么”，点击“查看结果”。若要验证 DeepSeek 解释，再勾选相应复选框后提交。页面显示的是历史回放，不是实时预测或官方洪水警报。

## 常见问题

| 现象 | 检查和处理 |
| --- | --- |
| 浏览器提示无法连接 | 确认 PowerShell 启动窗口仍在运行；重新执行上面的启动命令。 |
| 提示 8501 端口被占用 | 先确认是否已有一个服务在 <http://127.0.0.1:8501/> 运行；不要盲目启动第二份。 |
| 仅 DeepSeek 解释报 `URLError` 或 `WinError 10013` | 其他已核验结果仍可看；在有外网访问权限的本机 PowerShell 中重启服务，并检查网络或代理设置。 |
| DeepSeek 提示未配置 key | 在项目根目录的 `.env` 中配置 `DEEPSEEK_API_KEY`，或在启动服务的同一个 PowerShell 会话里设置同名环境变量；不要把 key 发到聊天或写入操作截图。 |
| 想确认服务是否还活着 | 在 PowerShell 中运行 `Invoke-WebRequest http://127.0.0.1:8501/_stcore/health -UseBasicParsing`；状态码 `200` 表示网页服务在线，但不代表 DeepSeek 一定可用。 |

本说明只涉及本地网页服务的启停，不会修改归档数据、备份目录或预测模型。

## 界面与 API 配置核对

侧边栏“调试模式”默认关闭；开启后增加“运行细节”，展示原始引用、来源定位、运行 JSON 和已有模型诊断。开关只重新展示最近结果，不重新取证；网页已移除恢复码功能，恢复请用 [CLI 指南](14_checkpoints_and_demo.md)。

程序优先使用服务进程的 `DEEPSEEK_API_KEY`，未设置时读取当前项目根目录 `.env`。只读核对已确认项目 `.env` 中密钥有效，DeepSeek `/models` 返回 HTTP 200 且包含当前 `deepseek-v4-pro`；这不代表已验证完整聊天生成或解决所有超时。此前只读核对期间没有调整调用逻辑；本次已按批准方案调整编排预算，见下节。模型、Thinking 默认值及底层请求／响应解析保持，没有额外发起真实模型生成。

## 当前超时与联网配置

这些值由代码／`InvestigationLimits` 管理，不从 `.env` 读取：

| 限制 | 新调查默认值 | 配置位置 |
|---|---|---|
| 整个调查 | 120 秒，允许 1–180 秒 | `InvestigationLimits.max_seconds` |
| 每次模型请求 | 40 秒，允许 1–90 秒，同时受剩余预算约束 | `InvestigationLimits.llm_request_timeout_seconds` 与 `_Budget.reserve_llm` |
| HTTP 客户端 | Thinking 90 秒／非 Thinking 40 秒，再与调用方限制取较小值 | `DeepSeekClient._call`，底层响应解析未改 |
| 网络子预算 | 每个调查最多 4 请求／20 秒，同时计入共享工具数和总时间 | `web_evidence.py::_SharedNetworkBudget` |

旧持久任务仍沿用保存的总时限；缺少新单次限制字段时按旧 20 秒恢复，不重新发放额度。总预算和 HTTP 阻塞超时都不是进程级精确硬截止。

网页“允许联网补查”默认开启，仅在多步调查的公开资料缺口生效。关闭后下一次提交不联网；切换开关不重新调查最近结果。单轮继续本地查询。项目数值不使用外部网页替代。

慢请求可先关闭 Thinking，用同问题与回放时间对照；调试开关不影响推理。原代码已使用 `reasoning_effort=low`。没有自动重试；内部排序失败只作调试记录，模型网络失败仍以可读提示说明，保留已取得的有效证据。`time_limit` 是总时限，`llm_limit` 是调用次数上限，`TimeoutError` 是请求失败线索，`finish_reason=length` 是输出未完整结束，应分别定位。

Brave 密钥可选；没有时仍可直连登记的 GOV.UK、House Mill 和项目来源。搜索发出的是公开主题模板，不包含完整混合问题、归档批次或本地路径。运行环境禁止联网时，允许开关不会绕过系统权限；结果会保留已有部分。当前服务本次没有重启，直接刷新页面即可；不要启动第二份服务。

实测联网样例、离线失败演示和完整测试命令见 [公开网页取证与演示](15_web_evidence_and_demo.md)。本次没有额外调用真实 DeepSeek 生成，也没有改 `.env`、模型、Thinking 默认值或底层响应处理。
