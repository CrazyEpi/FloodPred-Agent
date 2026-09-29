# D6–D7 完整演示

仅使用现有 `.venv-d4`。从 PowerShell 启动本地界面：

```powershell
cd C:\UCL\DissertationAgent
.\.venv-d4\Scripts\python.exe -m streamlit run streamlit_app.py --server.headless true --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
```

在浏览器打开 `http://127.0.0.1:8501`。默认已填好混合问题 `当时预测峰值是多少？项目内部 Watch 是什么` 和回放时间 `2026-08-03T01:48:00Z`。点击“提问并取证”，依次查看“结果”“证据与来源”“调试信息”三个标签页。结束时回到 PowerShell 按 `Ctrl+C`。

建议再试：

- `当时水位是多少`：只读历史声纳工具，按观测和入库双时间闸门。
- `本批次 MAE 是多少`：总体评估，`0.212531 m`、76,091 个匹配预测目标点、评估生成时间和 JSON 定位。
- `2米以上高水位 MAE 是多少`：高水位评估，`0.204788 m`、22,658 个匹配预测目标点；不要与总体 MAE 混用。
- `模型局限是什么`：高水位报告原文；不虚构洪水事件召回率。
- `当时预测峰值、历史水位、MAE 和项目内部 Watch 是什么`：四条路径合并展示，事后 MAE 与历史回放分开标记。
- `明天适合去野餐吗`：显示不支持的问题错误，不猜答案。

“用 DeepSeek 为文档证据生成短解释”默认**关闭**，打开后才可能产生 API 调用与费用；已核验的文档事实不依赖它。数值类问题不由 LLM 猜数。界面始终标记“历史回放、非实时、非官方警报”。

不用浏览器也可在命令行测试 D6：

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv-d4\Scripts\python.exe -m app.ask_cli --question '本批次 MAE 是多少'
.\.venv-d4\Scripts\python.exe -m app.ask_cli --question '当时预测峰值是多少？项目内部 Watch 是什么' --as-of 2026-08-03T01:48:00Z
.\.venv-d4\Scripts\python.exe -m unittest discover -s tests -q
```

首选命令使用本地 `127.0.0.1`，不公开部署。当前单一环境可由新文件 `requirements_d6_d7.txt` 补齐依赖。完整验收和限制见 `reports/D5-D7_work_report_2026-09-29.md`。
