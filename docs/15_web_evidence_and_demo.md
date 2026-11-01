# 公开网页取证、比较回答与演示

更新：2026-10-29（Asia/Hong_Kong）。多步调查已接入受限网页取证，覆盖官方预警、House Mill 公开信息及项目研究来源。单轮、Python API 和 CLI 默认离线；网页侧边栏“允许联网补查”默认开启，仅针对下一次提交的实际缺口。

## 网页使用

刷新已运行的 8501 页面，不需要重启服务。保留“多步调查”，可先关闭 DeepSeek，用以下问题检查确定性取证：

> 项目内部 Caution 和英国官方预警有什么区别？

正常联网成功时：

1. 证据表只有三个有区别的项目：Caution 定义、官方定义、两者比较。
2. 内部阈值为 4.20 m（含）至 4.43 m（不含）；官方部分引用 [Environment Agency 的英格兰说明](https://www.gov.uk/guidance/flood-alerts-and-warnings-what-they-are-and-what-to-do)，分别解释 Flood Alert、Flood Warning 和页面有记载的 Severe Flood Warning。
3. 对照明确发布主体、适用范围和判定表述不同，标为有限推导；不推出数值／等级的一一对应。
4. 同一官方网页只读取一次、只分配一个来源号。原文和抓取时间在来源卡查看；内部定位、完整映射及诊断只在调试模式出现。

关闭“允许联网补查”后重新提交同题，应得到简洁的局部回答：保留内部定义并指出官方原文缺口，不显示重复 Caution 结论。开关切换本身不调用模型或工具，需再次点击提交才执行新调查。单轮入口始终本地取证。

## CLI 演示

在项目目录使用既有环境：

```powershell
Set-Location C:\UCL\CASA0016\FloodPred-Agent
# 完整比较：只读联网，无需 DeepSeek 或 Brave 密钥
.\.venv-d4\Scripts\python.exe -X utf8 -m app.investigate_cli "项目内部 Caution 和英国官方预警有什么区别？" --allow-web
# 离线局部回答：不会访问外部网页
.\.venv-d4\Scripts\python.exe -X utf8 -m app.investigate_cli "项目内部 Caution 和英国官方预警有什么区别？"
# House Mill 公开活动；仅接受日期明确且相关的原文
.\.venv-d4\Scripts\python.exe -X utf8 -m app.investigate_cli "House Mill 最近有什么活动？" --allow-web
# 公开研究：登记来源及可选搜索，保留原文摘录，不凭模型记忆补写
.\.venv-d4\Scripts\python.exe -X utf8 -m app.investigate_cli "PatchTST 最新公开研究是什么？" --allow-web
```

这些命令没有 `--llm`，不会调用 DeepSeek。真实网站不可达、没有相关 HTML、当前登记来源覆盖不到问题或未配置搜索时，House Mill／研究例子可能只返回候选或缺口；这属于实际结果，不能把没有资料描述为已经验证答案。当前 Brave 未配置时直连仍可工作；需要扩展搜索时使用已有 `BRAVE_SEARCH_API_KEY`，不应提交或显示密钥。

原 `app.web_cli demo --allow-web` 是独立工具预览；其搜索候选／页面预览不是正式答案。带状态保存的联网演示见 [恢复指南](14_checkpoints_and_demo.md#7-联网快照与旧状态兼容)。

## 无网络的完整与失败样例

以下测试采用固定 HTML、假模型和临时数据库；不调用真实搜索或模型，可稳定展示全部三条证据路径：

```powershell
.\.venv-d4\Scripts\python.exe -X utf8 -m unittest tests.test_web_integration.WebIntegrationTests.test_complete_comparison_with_no_search_key_and_no_model_ranking -v
.\.venv-d4\Scripts\python.exe -X utf8 -m unittest tests.test_web_integration.WebIntegrationTests.test_online_failures_preserve_partial_answer_and_are_not_retried -v
.\.venv-d4\Scripts\python.exe -X utf8 -m unittest tests.test_web_integration.WebIntegrationTests.test_housemill_current_events_require_an_explicit_relevant_date -v
.\.venv-d4\Scripts\python.exe -X utf8 -m unittest tests.test_web_integration.WebIntegrationTests.test_research_metadata_and_model_selection_remain_extractive -v
```

完整验证：

```powershell
Remove-Item Env:FLOODPRED_DATA_MODE -ErrorAction SilentlyContinue
.\.venv-d4\Scripts\python.exe -X utf8 -m unittest tests.test_web_integration -v
.\.venv-d4\Scripts\python.exe -X utf8 -m unittest discover -s tests -q
.\.venv-d4\Scripts\python.exe -m pip check
```

## 证据与时间规则

- 沿用既有精确域名表、HTTPS、公网 DNS 与 TLS 检查；不接登录、不执行脚本、不任意跟随重定向。第一版只处理 HTML，PDF 或无可读正文的页面仍说明缺口。
- 登记来源优先，其次页面实际提供的一层相关链接，必要时使用现有 Brave。搜索仅发公开主题模板，摘要不授权事实；相同 URL 不重复抓取。
- 官方定义使用受约束的中文表达；House Mill 和研究信息使用原文摘录，模型只选已有段落索引。来源、数字和引用检查不等于完整的语义真实性证明。
- 近期信息需要页面明确给出的相关日期；发表日期、活动日期及抓取日期不是同一概念。当前页面不能证明过去时刻或早于抓取日期的文档截止要求。
- 定义比较可据双方定义作限定推导；因果、硬件相同、技术继承仍要求直接关系证据。项目峰值、水位、MAE 只来自原工具。
- 每次调查最多 3 轮、8 次工具、4 次模型；新总时限 120 秒，模型单次最多 40 秒。网络最多 4 次请求／20 秒，同时扣共享预算；失败保留计数，不自动重试。预算耗尽时只整理已取得且有效的证据。
- 网页快照可随 CLI 调查保存；恢复沿用这次已保存的来源和累计预算，不冒充对网站当前状态的新核验。旧任务保持原时间限制，缺少联网字段按关闭恢复。

本次真实与离线结果、备份和限制见 [工作报告](../reports/web_answer_integration_2026-10-28.md)。
