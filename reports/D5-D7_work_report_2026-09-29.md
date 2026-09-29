# FloodOps Agent D5–D7 工作报告

日期：2026-09-29  
工作目录：`C:\UCL\DissertationAgent`  
结论：**D6 的显式路由和三个只读工具已完成；D7 Streamlit 本地界面已运行并通过交互测试。P0 演示链路已具备。** 它仍是历史回放，不是实时洪水服务或官方警报系统。

## 1. 先备份、环境与不改动原则

在 D6–D7 开发前，将 D1–D5 的 `app/`、`docs/`、`reports/`、`tests/`、`knowledge/`、README、`requirements.txt`、`.gitignore` 复制到**新目录** `backup/d1_d5_snapshot_2026-09-29/`，共 **42 个文件、180,513 字节**；逐件 SHA-256 与原件一致，**0 个不一致**。没有复制含真实 DeepSeek key 的 `.env`，也没有复制较大的既有原资产备份或虚拟环境。备份目录未写入 D6–D7 内容；原服务、预测数据库及 D1–D5 正式功能代码未修改。

继续维护唯一的 `.venv-d4`，没有创建第二套 Python 环境。新装 Streamlit **1.64.0** 及依赖，另建 `requirements_d6_d7.txt` 记录可重建安装项。用户此前将 D5 默认模型调为 `deepseek-v4-pro`，旧测试却硬编码 `deepseek-flash`，所以开工前原有 16 项测试有 1 项失败。经用户明确同意，仅将旧测试的模型断言改为与当前 `DEFAULT_MODEL` 一致；**没有改动模型配置或 D5 业务代码**。修正后所有测试通过。此前的 D5 DeepSeek 两类真实调用已有独立验证记录；D6–D7 的默认界面演示不再额外消耗 API。

## 2. D6：工具参数与只读边界

新增 `app/read_tools.py`，用 Pydantic 明确参数契约：时间参数必须带时区并归一化为 UTC；站点只接受现有 `house_mill`；评估范围只接受 `overall` 或 `high_water_2m`。三个工具分别是：

1. `archived_forecast_tool`：从只读预测归档选择回放时刻已生成、已入库且有效的 run，继续核对所用历史数据和质量字段时间；返回峰值、目标时间、run_id、内部等级。它**不**顺便查询声纳库。
2. `historical_water_tool`：从声纳归档返回该时刻既已观测、又已入库的最新水位。它不把事后补录数据当作当时可见事实。
3. `evaluation_metrics_tool`：从结构化评估 JSON 精确读取 `point_metrics.mae_m`，先核对快照 SHA-256，不从相似文段或 LLM 记忆猜数。输出包含评估生成 UTC、匹配预测目标点数量、筛选范围、单位 `m`、方法和来源定位；并标记为**事后评估**。

两份评估快照不能混用：总体 MAE **0.212531 m**，评估版本 `2026-08-10T02:02:33.544996Z`，**76,091 个已成熟且匹配的预测目标点**；高水位（实测 ≥2.00 m）MAE **0.204788 m**，版本 `2026-08-10T02:18:51.779010Z`，**22,658 个匹配预测目标点**，目标时刻范围从 `2026-07-24T07:15:00+00:00` 至 `2026-08-09T23:45:00+00:00`。点级配对不是独立洪水事件，也不证明 Watch 召回率。

## 3. D6：显式路由

新增 `app/router.py` 与 `app/ask_cli.py`。`plan_route()` 根据已支持的明确词组生成计划：预测/水位/MAE 数值分别走相应工具；Watch、模型局限及官方 Flood Alert 定义走 D4 的可信文档检索；混合问题并行取各自来源后合并展示。D5 DeepSeek 文档解释可选，默认不调用；即使启用，数值工具的值也不交给 LLM 猜测。无匹配意图直接报不支持，缺少或无时区的回放时间报参数错误。对 MAE 的输出必须同时展示数值、**单位、评估版本和样本范围**。

没有引入 LangGraph：当前路由是同步、确定性的普通 Python，未出现需要暂停、等待人工批准再恢复的工作流状态。若以后确实引入长时任务、审批节点或恢复执行，再评估 LangGraph。

## 4. D7：陌生人可用的本地界面

新增根目录 `streamlit_app.py`。页面由四层组成：

- 顶部固定警示：**历史回放、非实时、非官方洪水警报**，内部 Watch/Warning 不等于官方类别。
- 提问表单：问题、UTC 回放时刻、可选的 DeepSeek 文档短解释开关（默认关闭，提示可能有费用）。默认填入可立即演示的混合问题。
- “结果”标签：预测风险卡（峰值、目标时间、内部等级、run_id）、当时可见水位、MAE 的评估版本/样本/单位、文档定义与局限；每种信息单独标识。
- “证据与来源”及“调试信息”标签：归档文件和 run、观测/入库时间、评估 JSON 的哈希与版本、文档精确定位；调试区显示路由步骤与错误，但**不显示密钥或 Authorization 头**。

界面对无匹配问题、缺失时间、工具错误显示显式错误状态，绝不把空结果伪装成“无风险”。没有 House Mill 经授权的现场 SOP，因此不输出行动指令。默认仅绑定 `127.0.0.1`；命令禁用 Streamlit 使用统计。第一次启动未禁用统计时，受限执行环境在尝试写用户目录时出现权限错误；以 `--browser.gatherUsageStats false` 重启后服务正常，健康检查返回 **HTTP 200 / ok**。

## 5. 用户如何运行和测试

在 PowerShell 启动：

```powershell
cd C:\UCL\DissertationAgent
.\.venv-d4\Scripts\python.exe -m streamlit run streamlit_app.py --server.headless true --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
```

浏览器打开 `http://127.0.0.1:8501`，默认问题和时间无需修改，点击“提问并取证”。应见预测峰值 **3.7832 m**、目标时间、`run_id`、内部 `No risk`，以及 Watch 定义和 README `风险等级`章节引用。切到“证据与来源”确认生成/入库时间，切到“调试信息”确认 `archived_forecast → knowledge`。再将问题改为 `本批次 MAE 是多少`、`2米以上高水位 MAE 是多少`、`当时水位是多少` 和 `明天适合去野餐吗`，分别检查精确指标、声纳和错误状态。结束后按 `Ctrl+C`。

无需打开浏览器的 D6 验证：

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv-d4\Scripts\python.exe -m app.ask_cli --question '本批次 MAE 是多少'
.\.venv-d4\Scripts\python.exe -m app.ask_cli --question '当时预测峰值是多少？项目内部 Watch 是什么' --as-of 2026-08-03T01:48:00Z
.\.venv-d4\Scripts\python.exe -m unittest discover -s tests -q
```

本次 **22/22 个聚焦测试通过**：既有功能、旧模型配置测试修正、D6 新增 4 个路由/指标测试及 D7 新增 2 个 Streamlit 模拟交互测试。手动运行了混合问答、总体 MAE、高水位 MAE 命令；本地 Streamlit 服务启动并经 `/_stcore/health` 返回 200。没有训练模型、批量调用 DeepSeek 或做压力测试。

## 6. 限制与后续

当前 UI 是本机演示，不是生产部署。外部 API、MQTT 和云服务器无需恢复即可演示；如果打开 DeepSeek 解释开关，仍依赖用户已配置的 key 与网络，可能产生费用。正式场景还需要经授权的 SOP、官方实时状态接入、更完整的意图覆盖、证据发布时间控制及更严格的语义评估。D6 的评估 MAE 是 2026-08-10 的**事后**快照，不能倒灌为 2026-08-03 回放时刻已知的事实。
