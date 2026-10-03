"""FloodPred: a local, evidence-forward historical replay UI."""

from __future__ import annotations

import streamlit as st

from app.archive import DEFAULT_DB
from app.investigation import run_investigation
from app.rag import DeepSeekClient
from app.replay import DEFAULT_SONAR_DB
from app.router import QueryResult, UnsupportedRoute, run_query, volunteer_fact


st.set_page_config(page_title="FloodPred｜历史回放", page_icon="🌊", layout="wide")
st.markdown("""<style>
.block-container {max-width: 1120px; padding-top: 2.5rem; padding-bottom: 4rem;}
div[data-testid="stMetric"] {padding: 1rem 1.1rem; border: 1px solid rgba(127,127,127,.18); border-radius: 12px;}
div[data-testid="stMetricValue"] {font-size: 1.7rem;}
</style>""", unsafe_allow_html=True)


def show_result(result: QueryResult) -> None:
    if result.errors:
        for error in result.errors:
            st.error(error)
    for warning in result.warnings:
        st.warning(warning)
    result_tab, evidence_tab, debug_tab = st.tabs(["回答", "证据", "运行细节"])

    with result_tab:
        if result.investigation_rounds:
            st.caption(f"本次调查取证 {result.investigation_rounds} 轮、调用只读工具 {result.investigation_tool_calls} 次；每一步可在‘运行细节’查看。")
        if result.fallback_text:
            st.warning(result.fallback_text)
        if result.answer_text:
            if result.response_mode == "project":
                st.write(result.answer_text)
                if result.answer_status == "partial_verified":
                    st.caption("这是根据已核验残余资料整理的局部结论，不是整个问题的完整答案；缺失部分没有推测或补写。")
                else:
                    st.caption("这段话由 DeepSeek 根据下方已核对的资料整理；重要数据请以原始卡片和可信来源为准")
            else:
                st.write(result.answer_text)
                if result.response_mode == "general":
                    st.caption("这是普通对话，没有调用项目资料或实时信息")
                elif result.response_mode == "clarify":
                    st.caption("我还不确定你指的是哪项项目资料")
        if result.forecast:
            card = result.forecast
            st.subheader("当时的预测")
            a, b = st.columns(2)
            a.metric("当时预测的最高水位", f"{card.predicted_peak_m:.4f} m")
            b.metric("项目内部等级", {"No risk": "无风险", "Watch": "留意（Caution）", "Warning": "警告（Warning）", "Severe": "严重（Severe）"}.get(card.internal_risk_label, card.internal_risk_label))
            st.write(f"这条预测预计在 **{card.predicted_peak_utc}** 达到峰值。回看时间：`{card.as_of_utc}`。")
            st.caption("峰值是当时作出的未来预测；内部等级不等于英国官方预警")
        if result.water:
            item = result.water
            st.subheader("当时能看到的实测水位")
            st.metric("声纳实测水位", f"{item.water_m:.3f} m")
            st.write(f"观测时间：`{item.observed_utc}` · 入库时间：`{item.stored_at_utc}`")
            st.caption("只展示回放时刻之前既已观测、又已入库的水位")
        elif "historical_water" in result.plan.steps and not result.errors:
            st.info("该回放时刻没有可见的历史声纳观测。")
        if result.evaluation:
            metric = result.evaluation
            st.subheader("模型后来评得怎样")
            a, b = st.columns(2)
            a.metric("平均误差（MAE）", f"{metric.mae_m:.6f} {metric.unit}")
            b.metric("匹配预测目标点", f"{metric.matched_prediction_points:,}")
            st.write(f"样本范围：{volunteer_fact(metric.sample_scope)}")
            st.write(f"评估版本（生成 UTC）：`{metric.generated_utc}`")
            if metric.target_start_utc:
                st.write(f"目标时间范围：`{metric.target_start_utc}` 至 `{metric.target_end_utc}`")
            st.caption("MAE 可以简单理解为：把一批预测和实测逐个比较，平均差了多少米。这是事后评估，不代表这一次峰值差了这么多，也不是洪水检出率。")
        if result.knowledge:
            st.subheader("从资料里查到的解释")
        for item in result.knowledge:
            with st.container(border=True):
                st.write(volunteer_fact(item.fact))
                if item.explanation:
                    st.write(f"换句话说：{volunteer_fact(item.explanation)}")
                source_label = {"internal_project": "项目服务端文档", "evaluation_report": "项目评估报告", "thesis": "毕业论文", "official_public_guidance": "英国官方公开资料", "research_paper": "House Mill 历史研究论文", "prior_project": "早期传感器项目仓库", "heritage_public": "Historic England 建筑档案"}.get(item.source_type, item.source_type)
                st.caption(source_label)
                if "Watch" in item.fact:
                    st.caption("Caution 是本界面的统一显示名；源文件中的名称见“证据”。")
        if result.answer_status == "candidate_only":
            st.info("找到了一些相关资料，可在‘证据’中查看。还没有确认它们能回答整个问题，所以这次只展示候选段落。")
        if not any((result.answer_text, result.forecast, result.water, result.evaluation, result.knowledge, result.document_candidates)):
            st.info("没有可展示的结果；请查看上方错误和调试信息。")

    with evidence_tab:
        st.subheader("这次回答依据什么？")
        if result.forecast:
            card = result.forecast
            with st.container(border=True):
                st.write("**归档预测（只读）**")
                st.code(str(DEFAULT_DB), language=None)
                st.write(f"run_id：`{card.run_id}`")
                st.write(f"生成：`{card.forecast_generated_utc}` · 入库：`{card.forecast_stored_at_utc}`")
        if result.water:
            with st.container(border=True):
                st.write("**历史声纳观测（只读）**")
                st.code(str(DEFAULT_SONAR_DB), language=None)
                st.write(f"观测：`{result.water.observed_utc}` · 入库：`{result.water.stored_at_utc}`")
        if result.evaluation:
            metric = result.evaluation
            with st.container(border=True):
                st.write("**结构化评估快照（事后）**")
                st.code(metric.source_locator, language=None)
                st.write(f"版本：`{metric.generated_utc}` · SHA-256：`{metric.source_sha256}`")
                st.write(metric.method)
        for item in result.knowledge:
            with st.container(border=True):
                st.write(f"**{item.title}** · `{item.source_type}`")
                st.code(item.locator, language=None)
                st.write(f"引用 ID：`{item.citation_id}`")
                if item.version:
                    st.write(f"资料版本：`{item.version}`")
                if item.source_sha256:
                    st.write(f"本地源文件 SHA-256：`{item.source_sha256}`")
                if item.retrieval_methods:
                    st.caption(f"检索方式：{', '.join(item.retrieval_methods)}。相似度只是候选排序，不是事实支持证明。")
                st.write(f"来源目录的原始摘要：{item.fact}")
                if "Watch" in item.fact:
                    st.caption("显示名称 Caution 对应此来源中的 Watch；源文件和引用 ID 保留原词，不能把别名当成原文。")
        if result.document_candidates:
            st.subheader("检索到的相关资料")
            st.caption("这些是检索候选。文件校验和相似度不能证明段落足以支持答案；未用于本次结论的资料也列在这里，方便核对。核对日期不代表资料在历史回放时已经可用。")
            selected_ids = {item.citation_id for item in result.knowledge}
            for hit in result.document_candidates:
                record = hit.record
                paragraph_ids = {record["id"], *(item["id"] for item in hit.equivalent_records)}
                label = "本次已选来源条目" if paragraph_ids & selected_ids else "仅候选，未用于结论"
                with st.expander(f"{record['title']} · {record['section']} · {label}"):
                    st.caption(f"{record['source_type']} · 版本：{record['version']} · ID：{record['id']}")
                    if hit.equivalent_records:
                        st.caption("同段落的其他目录 ID：" + "、".join(item["id"] for item in hit.equivalent_records))
                    st.code(hit.locator, language=None)
                    if record.get("sha256"):
                        st.caption(f"本地源文件 SHA-256：{record['sha256']}")
                    if record.get("origin_sha256"):
                        st.caption(f"原始 PDF SHA-256：{record['origin_sha256']}")
                    if hit.excerpt:
                        st.code(hit.excerpt, language=None)
                    else:
                        st.write("此条只有官方链接和目录摘要，没有本地网页原文快照。")
        if not any((result.forecast, result.water, result.evaluation, result.knowledge, result.document_candidates)):
            st.info("本次是普通对话或澄清问题，没有调用项目数据和资料。" if result.answer_text else "本次没有通过验证的证据。")

    with debug_tab:
        st.caption("这里保留取证路径，方便复核；不会展示 API key 或请求头。")
        st.json({
            "request_id": result.request_id,
            "audit_status": result.audit_status,
            "question": result.question,
            "as_of_utc": result.as_of_utc,
            "route": result.plan.steps,
            "response_mode": result.response_mode,
            "knowledge_concepts": result.plan.knowledge_concepts,
            "evaluation_scope": result.plan.evaluation_scope,
            "question_graph": result.question_graph.to_dict() if result.question_graph else None,
            "retrieval_runs": result.retrieval_runs,
            "tool_trace": result.trace,
            "investigation_rounds": result.investigation_rounds,
            "investigation_tool_calls": result.investigation_tool_calls,
            "investigation_llm_calls": result.investigation_llm_calls,
            "investigation_stop_reason": result.investigation_stop_reason,
            "safe_fallback": result.fallback_text,
            "answer_status": result.answer_status,
            "warnings": result.warnings,
            "errors": result.errors,
        })
        if result.reasoning_traces:
            st.warning("以下是 DeepSeek 返回的原始思维链，仅用于调试；它可能有误，不是项目事实或引用依据，也不会写入审计日志。")
            for entry in result.reasoning_traces:
                with st.expander(f"DeepSeek 思维链 · {entry['stage']}"):
                    st.code(entry["content"], language=None)
        else:
            st.caption("本次 DeepSeek 未返回思维链，或没有调用 DeepSeek。")


st.title("🌊 FloodPred")

with st.form("ask_form"):
    question = st.text_input(
        "想了解什么？",
        value="当时预测峰值是多少？项目内部 Warning 是什么？",
        help="可以像聊天一样问。例如：‘你好，预测最高水位和平均误差有什么关系？’",
    )
    as_of_utc = st.text_input("历史回放时刻（UTC）", value="2026-08-03T01:48:00Z")
    llm_column, thinking_column = st.columns(2)
    with llm_column:
        use_llm = st.checkbox("用 DeepSeek 理解和回答", value=bool(DeepSeekClient().api_key))
    with thinking_column:
        use_thinking = st.checkbox("开启 Thinking（显示思维链）", value=True, help="仅在使用 DeepSeek 时生效；关闭后仍可提问，但不会请求或显示思维链。")
    use_investigation = st.checkbox("多步调查（最多 3 轮）", value=True, help="先取证、检查是否缺少关键来源，再在只读白名单内补查；最多 8 次工具调用和 60 秒。取消可使用原来的单轮问答。")
    submitted = st.form_submit_button("查看结果", type="primary")

if submitted:
    try:
        query_runner = run_investigation if use_investigation else run_query
        output = query_runner(question, as_of_utc=as_of_utc,
                              use_llm=use_llm, thinking_enabled=use_thinking)
    except UnsupportedRoute as exc:
        st.error(str(exc))
    else:
        show_result(output)

with st.expander("可以试试这些问题"):
    st.markdown(
        """- `你好，预测峰值是多少？项目内部 warning 是什么？`：一次提问同时查预测和规则。
- `你好，预测峰值和 MAE 的联系是什么？`：先分别取证，再用通俗话解释两者区别。
- `你好`：介绍这个页面能帮你做什么。
- `当时水位是多少`：历史声纳工具。
- `本批次 MAE 是多少`：总体评估 JSON；必须显示版本、样本和米。
- `2米以上高水位 MAE 是多少`：高水位评估 JSON，不与总体值混淆。
- `论文的离线事件召回率是多少？线上验证了洪水检出吗？`：分别查看离线与部署期证据。
- `项目内部 Caution 是什么？`：查看内部等级；原始术语可在“证据”中核对。
- `官方 Flood Alert 是什么？`：查看英国官方公开资料的静态定义，不查询当前警报。
- `House Mill 是什么？为什么会受潮汐影响？`：查看建筑档案和历史研究背景。
- `Duncan Wilson 的旧传感器怎么布置？论文中的 136 次与 42 次有什么区别？`：分别查看旧项目仓库与论文；不代表设备今天在线。
- `旧 House Mill 监测和 FloodPred 是什么关系？`：查看毕业论文中的承接说明。
- `项目内部 Caution 和英国官方预警有什么区别？`：多步调查会在内部规则之后补查官方静态定义，不会查询实时警报。
- `周末适合读什么书？`：普通聊天，不会冒充项目事实。"""
    )
