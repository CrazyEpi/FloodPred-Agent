# D5 最小 RAG 问答

D5 使用 DeepSeek，只支持两类经核验的知识问题：项目内部 Watch 规则、模型局限。查询先确认目录记录与原文文件完整，之后才调用模型；缺少资料时直接拒答，不以模型记忆补全。

## 第一次配置（你之后填入 key）

在 PowerShell 中：

```powershell
cd C:\UCL\DissertationAgent
Copy-Item .\.env.example .\.env
notepad .\.env
```

把 `.env` 中 `DEEPSEEK_API_KEY=PASTE_YOUR_DEEPSEEK_API_KEY_HERE` 的占位符替换为你的真实 DeepSeek key，保存。不要把 key 发到聊天、写进 README、报告或代码。`.env` 已被 `.gitignore` 忽略。也可不建 `.env`，而在当前 PowerShell 会话设置 `$env:DEEPSEEK_API_KEY='你的密钥'`。

## 运行

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv-d4\Scripts\python.exe -m app.rag_cli --question '项目内部 Watch 是什么'
.\.venv-d4\Scripts\python.exe -m app.rag_cli --question '模型局限是什么'
```

如果省略 `--question`，会显示交互输入提示。真实请求会调用 DeepSeek，可能产生 API 费用。当前默认模型是 DeepSeek 官方文档列出的 `deepseek-flash`；如服务端型号变化，需核对文档并在代码中调整 `DEFAULT_MODEL`。

成功输出分为：已核验事实、模型生成的简短解释、来源类型和可定位的引用。内部 Watch 的事实应为 `4.20 m <= level < 4.43 m`，并标明**不是官方 Flood Alert**；模型局限应说明评估数据中达到 Watch 阈值的真实目标点为 0，不能证明事件召回率。缺 key 会明确报错；若无对应目录记录/原文、版本冲突、引文不实或模型添加证据外数字，也会拒答。

不要为了验证“删资料拒答”去删除原备份。测试使用一次性临时目录模拟删除：

```powershell
.\.venv-d4\Scripts\python.exe -m unittest discover -s tests -v
```

该命令无需 key 或网络。此前 D1–D2、D3、D4 的功能都可继续使用同一个 `.venv-d4`。本轮没有真实 key，所以 DeepSeek 线上生成尚未现场验证；配置后按上面两条命令做最小 smoke test。详见 `reports/D1-D5_work_report_2026-09-29.md`。
