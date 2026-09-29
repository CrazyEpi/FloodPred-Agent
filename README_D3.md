# D3 无 LLM 历史回放 demo

在 PowerShell 中运行（不需要云服务器、API key、MQTT 或 LLM）：

```powershell
cd C:\UCL\DissertationAgent
$env:PYTHONIOENCODING='utf-8'
.\.venv-d3\Scripts\python.exe -m app.demo --as-of 2026-08-03T01:48:00Z
```

看到 `请输入问题：` 后输入 `当时预测峰值是多少` 并回车。也可以一次性运行：

```powershell
.\.venv-d3\Scripts\python.exe -m app.demo --as-of 2026-08-03T01:48:00Z --question '当时预测峰值是多少'
```

应显示预测峰值 `3.7832 m`、峰值目标时间 `2026-08-03T16:15:00Z`、64 位 `run_id`、内部等级 `No risk`，以及显著的“历史回放”标识。峰值来自当时已存档的未来预测点，**不是真实发生的峰值**；内部等级也不是官方预警。

最小测试：

```powershell
.\.venv-d3\Scripts\python.exe -m unittest discover -s tests -v
```

数据只从 `backup/source_snapshot_2026-09-29` 中以 SQLite `mode=ro&immutable=1` 打开。`as_of_utc` 的时间闸门对预测生成/入库、所用历史数据、声纳观测/入库分别检查；晚入库的旧观测也不可见。原 D1–D2 文件和报告没有修改，D3 开工前的代码文档快照在 `backup/d1_d2_snapshot_2026-09-29`。详见 `reports/D1-D3_work_report_2026-09-29.md`。
