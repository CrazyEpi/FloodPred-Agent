"""FloodPred: a local, evidence-forward historical replay UI."""

from __future__ import annotations

import streamlit as st

from app.archive import DEFAULT_DB
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
    result_tab, evidence_tab, debug_tab = st.tabs(["回答", "证据", "运行细节"])

    with result_tab:
        if result.answer_text:
            if result.response_mode == "project":
                st.write(result.answer_text)
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
                source_label = {"internal_project": "项目服务端文档", "evaluation_report": "项目评估报告", "thesis": "毕业论文", "official_public_guidance": "英国官方公开资料"}.get(item.source_type, item.source_type)
                st.caption(source_label)
                if "Watch" in item.fact:
                    st.caption("Caution 是本界面的统一显示名；源文件中的名称见“证据”。")
        if not any((result.answer_text, result.forecast, result.water, result.evaluation, result.knowledge)):
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
                st.write(f"来源目录的原始摘要：{item.fact}")
                if "Watch" in item.fact:
                    st.caption("显示名称 Caution 对应此来源中的 Watch；源文件和引用 ID 保留原词，不能把别名当成原文。")
        if not any((result.forecast, result.water, result.evaluation, result.knowledge)):
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
            "tool_trace": result.trace,
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
    submitted = st.form_submit_button("查看结果", type="primary")

if submitted:
    try:
        output = run_query(question, as_of_utc=as_of_utc, use_llm=use_llm, thinking_enabled=use_thinking)
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
- `周末适合读什么书？`：普通聊天，不会冒充项目事实。"""
    )
