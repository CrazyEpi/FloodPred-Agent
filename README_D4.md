# D4 可信知识检索 demo

打开 PowerShell：

```powershell
cd C:\UCL\DissertationAgent
$env:PYTHONIOENCODING='utf-8'
.\.venv-d4\Scripts\python.exe -m app.knowledge_cli
```

在提示处输入 `项目内部 Watch 是什么`。可用一行命令直接运行：

```powershell
.\.venv-d4\Scripts\python.exe -m app.knowledge_cli --question '项目内部 Watch 是什么'
```

预期答案：项目内部 `Watch` 是 level 1，预测峰值在 `4.20 m <= level < 4.43 m`；返回 `cloud_flood_server/README.md:13-20 § 风险等级` 的原文片段、来源类型和版本，并明确写出**不是 Environment Agency 的官方 Flood Alert**。它也不是 House Mill 专属行动 SOP。

其他可试的问题：

```powershell
.\.venv-d4\Scripts\python.exe -m app.knowledge_cli --question '官方 Flood Alert 是什么'
.\.venv-d4\Scripts\python.exe -m app.knowledge_cli --question '高水位评估能证明 Watch 召回率吗'
.\.venv-d4\Scripts\python.exe -m app.knowledge_cli --question 'House Mill 专属 SOP 是什么'
```

最后一问应明确“未找到匹配的已核对来源”，不会编造 SOP。检索支持 `--source-type internal_project|evaluation_report|official_public_guidance` 和 `--version` 元数据筛选。运行少量测试：

```powershell
.\.venv-d4\Scripts\python.exe -m unittest discover -s tests -v
```

D4 是离线、无 LLM 的文档检索原型；英国官方网页的摘要是 2026-09-29 核对时的静态来源说明，**不代表当前某地警报状态**。详见 `docs/04_knowledge_sources.md` 和 `reports/D1-D4_work_report_2026-09-29.md`。
