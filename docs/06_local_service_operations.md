# FloodPred 本地网页服务操作说明

## 服务在哪里

- 项目目录：`C:\UCL\DissertationAgent`
- 网页程序：`C:\UCL\DissertationAgent\streamlit_app.py`
- Python 环境：`C:\UCL\DissertationAgent\.venv-d4`
- 本机访问地址：<http://127.0.0.1:8501/>

这是运行在**你自己的 Windows 电脑上**的 Streamlit 服务，没有部署在云服务器。`127.0.0.1` 只供本机访问；关闭运行它的 Python 进程或重启电脑后，网页就不能访问。勾选“用 DeepSeek 为文档证据生成短解释”后，服务端才会向 DeepSeek API 发出请求，可能产生费用；不勾选时不需要访问 DeepSeek。

它不会随浏览器或电脑自动启动。若访问地址打不开，先按下文的“如何重新打开”启动服务；截至本说明更新时，本机服务未在运行。

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
cd C:\UCL\DissertationAgent
.\.venv-d4\Scripts\python.exe -m streamlit run streamlit_app.py --server.headless true --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
```

看到 `URL: http://127.0.0.1:8501` 后，在浏览器打开 <http://127.0.0.1:8501/>。保持这个 PowerShell 窗口运行；需要停机时回到该窗口按 `Ctrl+C`。不必恢复旧云服务器，也不必另建 Python 环境。请在普通的、允许访问互联网的本机 PowerShell 中启动；此前 Codex 的受限执行环境会阻断 DeepSeek 网络请求。

首次验证可使用默认问题“当时预测峰值是多少？项目内部 Watch 是什么”，点击“提问并取证”。若要验证 DeepSeek 解释，再勾选相应复选框后提交。页面显示的是历史回放，不是实时预测或官方洪水警报。

## 常见问题

| 现象 | 检查和处理 |
| --- | --- |
| 浏览器提示无法连接 | 确认 PowerShell 启动窗口仍在运行；重新执行上面的启动命令。 |
| 提示 8501 端口被占用 | 先确认是否已有一个服务在 <http://127.0.0.1:8501/> 运行；不要盲目启动第二份。 |
| 仅 DeepSeek 解释报 `URLError` 或 `WinError 10013` | 其他已核验结果仍可看；在有外网访问权限的本机 PowerShell 中重启服务，并检查网络或代理设置。 |
| DeepSeek 提示未配置 key | 在项目根目录的 `.env` 中配置 `DEEPSEEK_API_KEY`，或在启动服务的同一个 PowerShell 会话里设置同名环境变量；不要把 key 发到聊天或写入操作截图。 |
| 想确认服务是否还活着 | 在 PowerShell 中运行 `Invoke-WebRequest http://127.0.0.1:8501/_stcore/health -UseBasicParsing`；状态码 `200` 表示网页服务在线，但不代表 DeepSeek 一定可用。 |

本说明只涉及本地网页服务的启停，不会修改归档数据、备份目录或预测模型。
