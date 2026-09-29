# D5 最小 RAG 的证据契约

## 检索命中不等于答案得到支持

关键词命中只说明“可能找到了相关片段”。这不能证明回答正确。例如包含 `Watch` 的 README 可能只是内部等级说明；包含 `MAE` 的评估报告也不能证明洪水检出率。D5 因而把问答分为可观察的关口：

1. 问题路由：只承认 `internal_watch` 和 `highwater_evaluation_limit` 两个概念。其他问题拒答。
2. 证据检索：D4 的关键词＋来源类型检索；只接受目标概念的唯一 active 记录。若无记录或版本冲突，不调用 LLM。
3. 原文核验：本地文件必须存在、SHA-256 与目录一致、行号可定位；内部 Watch 原文必须出现完整的 `4.20m <= level < 4.43m`，模型局限原文必须出现“达到 Watch 的实测目标点为 0”和“不能用于证明”的结论。
4. 受限生成：只把核验过的原文及记录 ID 发送给 DeepSeek，请它生成短解释、逐字引文和引用 ID。摘要事实仍由检索记录给出，模型没有资料检索或自行补全权限。
5. 输出检查：引用 ID 必须唯一且匹配；`supporting_quote` 必须是原文的逐字片段；解释中的数字只能来自原文；两种意图各有关键结论检查；禁用一些未经证实的官方状态和现场行动词。任一关口失败时，不返回模型答案。

这是一套**窄领域、可测试的机械约束**，不构成通用的语义蕴含证明。即使引用形式正确，复杂的无数字断言也可能需要人工复核。输出因此清楚区分“已核验事实”和“模型简短解释”，并保留来源定位。它不提供 House Mill 专属行动 SOP，也不能表示官方当前警报。

## API 与密钥

DeepSeek 的官方文档在 2026-09-29 核对时给出 `POST https://api.deepseek.com/chat/completions`、`Authorization: Bearer`、`deepseek-flash`；JSON Output 需要 `response_format={"type":"json_object"}`，并在提示中明确要求 JSON。D5 只使用标准库发送请求，不新增第三方 SDK。密钥只从进程环境变量 `DEEPSEEK_API_KEY` 或项目根目录的被忽略 `.env` 读取；仓库中只放 `.env.example` 占位符。请求错误仅报告状态或异常类型，不打印密钥和响应正文。

参考：[DeepSeek 首次 API 调用](https://api-docs.deepseek.com/guides/harness)、[Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)、[JSON Output](https://api-docs.deepseek.com/guides/json_mode/)。

## 数据删除试验

单元测试在临时目录复制目录元数据，再分别移除内部 Watch 记录和让模型局限原文文件缺失。两种情况下均应在 LLM 调用前拒答，假模型调用次数为 0。测试不触碰真正的备份。另有错误引用 ID、虚构引文、证据外数字和 DeepSeek HTTP 请求封装的无网络测试。
