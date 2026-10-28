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

程序优先使用服务进程的 `DEEPSEEK_API_KEY`，未设置时读取当前项目根目录 `.env`。只读核对已确认项目 `.env` 中密钥有效，DeepSeek `/models` 返回 HTTP 200 且包含当前 `deepseek-v4-pro`；这不代表已验证完整聊天生成或解决所有超时。此次未改客户端、模型、Thinking 默认值、预算或响应处理，也未额外发起真实模型生成。

## 多步调查超时：位置与调整顺序

当前有两层限制，不由 `.env` 配置：

| 位置 | 当前值 | 含义 |
|---|---|---|
| `app/investigation_core.py::InvestigationLimits.max_seconds` | 60 秒；允许 1–180 秒 | 整个多步调查的协作式总预算 |
| `app/investigation_core.py::_Budget.reserve_llm` | `min(20.0, max(1.0, self.remaining()))` | 每次模型调用前覆盖客户端超时，最多 20 秒 |
| `app/rag.py::DeepSeekClient._call` | Thinking 90 秒／非 Thinking 40 秒，再与调用方限制取较小值 | 原 HTTP 客户端超时；多步路径通常被上一行的 20 秒压低 |

所以只增加 60 秒总预算，或只把客户端 90 秒调高，都不能解除多步路径的单次 20 秒限制。单轮问答没有这层 `_Budget` 覆盖，可能表现为单轮成功、多步反而超时。

先在调试页区分：`errors` 中的 `LLMRequestError`／`TimeoutError` 是请求失败线索；`investigation_stop_reason=time_limit` 表示总预算耗尽；`llm_limit` 是调用次数上限；`finish_reason=length` 则是输出未完整结束，不是等待超时。没有逐次真实调用耗时数据时，不能只凭超时认定 DeepSeek 服务本身缓慢。

建议顺序（建议没有自动修改代码）：

1. 保持多步调查，取消“开启 Thinking”，用同一问题、同一回放时间比较。调试开关只影响展示，关闭调试不等于关闭 Thinking。当前代码已使用 `reasoning_effort=low`，继续降低该字段不是现成的主要优化点。
2. 仍被单次请求截断时，可先把 `_Budget.reserve_llm` 的 `20.0` 试为 `40.0`，总预算使用 `InvestigationLimits(max_seconds=120)`。若决定保留 Thinking，可试单次 45–60 秒、总预算 120–180 秒；实际单次还要受剩余总预算约束。提高等待阈值只增加容忍时间，不会提高模型生成速度。
3. 网页当前没有超时滑块。局部调整总预算应只在 `use_investigation` 分支调用 `run_investigation(..., limits=InvestigationLimits(max_seconds=120))`，不要把 `limits` 参数传给 `run_query()`。也可修改限制类默认值影响所有普通调用方；已有持久调查的限制来自保存状态，不会追溯更改。
4. 后续真正提速可以分别评估：只对复杂规划启用 Thinking；程序能判断的缺口不再请模型排序；已核验的简单数值使用本地表达；缩短重复证据和不必要的输出；对当前问题图做回归后比较 `deepseek-flash`。这些属于后续实现和实测范围，当前没有变更模型或调用逻辑。
5. 当前 `stream=False`，流式响应能改善等待反馈，但需要完整接收 JSON 并通过原校验后才能给出正式答案；不能保证总推理时间下降。不要为超时直接加自动重试，避免重复等待和额外计费。

HTTP `urlopen(timeout=...)` 主要限制阻塞网络操作，既有总预算也在程序检查点协作检查，二者都不能解释为进程级精确硬截止。若要严格取消，需要另行设计。

官方接口说明：[Thinking 开关](https://api-docs.deepseek.com/guides/thinking_mode/)、[当前模型](https://api-docs.deepseek.com/quick_start/pricing/)。本次仅核对代码和官方文档，没有额外调用真实聊天 API。

网页已移除固定的内部／官方及非实时免责声明；数值、日期、来源原文、证据不足、来源冲突和实际错误继续展示。调试页保留原始回答和原始引用映射，便于复核；后台回答与来源资料未改写。
