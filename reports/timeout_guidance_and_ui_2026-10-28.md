# 超时配置核对与固定免责声明精简

日期：2026-10-28（Asia/Hong_Kong）。本次回答超时配置与提速问题，并移除网页中的固定免责声明。没有调整 DeepSeek 请求或业务预算。

## 修改前备份

`backup/14-pre-timeout-guidance-ui-20261028-212446` 保存 121 个文件、973,686 字节。清单记录香港时间、Git 提交、已有工作区状态、路径与字节数，保留先前未提交修改及 `.env.example`。排除真实 `.env`、状态库、日志、虚拟环境、旧备份和缓存；没有运行清理脚本或重复扫描历史备份哈希。

## 核对结论与建议

- `app/investigation_core.py:38`：总调查预算默认 60 秒，允许范围 1–180 秒。
- 同文件 `_Budget.reserve_llm()`：每次模型调用前设置 `min(20.0, max(1.0, self.remaining()))`。这是多步路径单次最多 20 秒的直接限制。
- `app/rag.py::_call()`：Thinking 默认 HTTP 超时 90 秒，非 Thinking 40 秒，与上述调用方限制取较小值。仅调大这里的 90 秒或总预算，仍不能解除单次 20 秒限制。
- 当前非流式调用；Thinking 已设置 `reasoning_effort=low`。规划、可选缺口排序和最终表达可形成多次顺序模型请求；多步调查不是一个 API 请求。
- 调试页的请求错误、`time_limit`、`llm_limit` 与不完整输出应分别判断。没有真实调用分段耗时，不断言所有超时都来自模型服务速度。本次没有实际生成测试，没有额外 API 消耗。

优先通过现有 Thinking 复选框对比。若仍有请求超时，可评估单次 40 秒、总计 120 秒；若保留 Thinking，可评估单次 45–60 秒和总计 120–180 秒，继续受剩余预算限制。阈值增加只延长等待容忍时间。更深入提速需单独评估减少模型排序、简单问题本地表达、上下文精简、模型切换与输出长度；流式主要改善等待反馈，不保证生成总耗时缩短。没有新增重试或并行。

官方文档核对：[Thinking 开关](https://api-docs.deepseek.com/guides/thinking_mode/)、[当前模型](https://api-docs.deepseek.com/quick_start/pricing/)。有关速度改善是待实测建议，不是本次性能测量结论。具体代码位置、总预算传参及单轮差异见 [服务操作说明](../docs/06_local_service_operations.md)。

## 实际修改

纯展示模块按固定短句移除“这是内部等级，不是官方警报”“不是实测或实时水情”等重复免责声明；同时删除预测卡固定的官方预警提醒，统一处理正文、结论标签、来源说明和工具生成的说明。没有通过删除所有“不是／不能”句子的方式处理。

保留实际错误、来源冲突、证据缺口和实质指标含义。源文件、文档原文、后端 `QueryResult`、校验、审计和 CLI 输出保持原样；调试页继续提供原始回答与原始引用映射。没有修改提示词、模型、Thinking 默认值、响应解析或超时参数，没有编辑真实 `.env`、停止／重启服务或操作已保存调查。

更新文件：`app/presentation.py`、`streamlit_app.py`、`tests/test_presentation.py`、README、`docs/06_local_service_operations.md`、`docs/08_architecture.md`；新增本报告。按照本次快照清单逐字节核对，只有上述 6 个已有文件变化，其他 115 个文件保持一致；没有额外哈希扫描。包括 DeepSeek 客户端、编排预算、`.env.example`、依赖、知识、数据及历史报告均保持。

## 验证与复测

| 检查 | 实际结果 |
|---|---|
| 展示及界面专项 | 15 项通过 |
| 完整离线回归 | 240 项通过 |
| `pip check` | `No broken requirements found.` |
| `git diff --check` | 通过，只有已有 LF／CRLF 提示 |

新增断言验证固定免责声明省略、数值／来源不变、缺口／超时错误保留及原始对象不变；现有调试开关与一次调用断言继续通过。日志位于 `logs/ui_boilerplate_targeted_20261028.log` 与 `logs/ui_boilerplate_regression_20261028.log`。

```powershell
Set-Location C:\UCL\CASA0016\FloodPred-Agent
.\.venv-d4\Scripts\python.exe -X utf8 -m unittest tests.test_presentation tests.test_streamlit_app -q
.\.venv-d4\Scripts\python.exe -X utf8 -m unittest discover -s tests -q
.\.venv-d4\Scripts\python.exe -m pip check
```

网页刷新后，关闭 DeepSeek 提交默认问题，可见阈值、日期和来源，固定免责声明不再出现；打开调试可复核原始回答。需要对比真实模型速度时，应使用相同问题及时间条件，分别切换 Thinking，记录等待时间、模型次数和停止原因。该人工速度对比会调用 API，本次未执行。不提交或推送 Git。
