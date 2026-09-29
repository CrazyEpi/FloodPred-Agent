"""D7: local, evidence-forward FloodOps replay demonstration UI."""

from __future__ import annotations

import streamlit as st

from app.archive import DEFAULT_DB
from app.replay import DEFAULT_SONAR_DB
from app.router import QueryResult, UnsupportedRoute, run_query


st.set_page_config(page_title="FloodOps 历史回放", page_icon="🌊", layout="wide")


def show_result(result: QueryResult) -> None:
    if result.errors:
        for error in result.errors:
            st.error(error)
    result_tab, evidence_tab, debug_tab = st.tabs(["结果", "证据与来源", "调试信息"])

    with result_tab:
        if result.forecast:
            card = result.forecast
            st.subheader("历史预测风险卡")
            a, b, c = st.columns(3)
            a.metric("当时预测峰值", f"{card.predicted_peak_m:.4f} m")
            b.metric("内部风险等级", card.internal_risk_label)
            c.metric("回放时刻（UTC）", card.as_of_utc)
            st.write(f"预测峰值目标时间：`{card.predicted_peak_utc}`")
            st.code(f"run_id: {card.run_id}", language=None)
            st.caption("这是一条当时已归档的未来预测；内部等级不是 Environment Agency 官方警报。")
        if result.water:
            item = result.water
            st.subheader("当时可见的历史水位")
            st.metric("声纳实测水位", f"{item.water_m:.3f} m")
            st.write(f"观测时间：`{item.observed_utc}` · 入库时间：`{item.stored_at_utc}`")
            st.caption("只展示回放时刻之前既已观测、又已入库的水位；不是事后补录值。")
        elif "historical_water" in result.plan.steps and not result.errors:
            st.info("该回放时刻没有可见的历史声纳观测。")
        if result.evaluation:
            metric = result.evaluation
            st.subheader("模型评估指标 · 事后评估")
            a, b = st.columns(2)
            a.metric("MAE（平均绝对误差）", f"{metric.mae_m:.6f} {metric.unit}")
            b.metric("匹配预测目标点", f"{metric.matched_prediction_points:,}")
            st.write(f"样本范围：{metric.sample_scope}")
            st.write(f"评估版本（生成 UTC）：`{metric.generated_utc}`")
            if metric.target_start_utc:
                st.write(f"目标时间范围：`{metric.target_start_utc}` 至 `{metric.target_end_utc}`")
            st.caption("这是事后批次评估，不是回放时刻已知的 MAE，也不是洪水检出率。")
        for item in result.knowledge:
            st.subheader("定义与模型局限")
            with st.container(border=True):
                st.write(item.fact)
                if item.explanation:
                    st.write(f"DeepSeek 简短解释：{item.explanation}")
                st.caption(f"[{item.citation_id}] · {item.source_type}")
        if not any((result.forecast, result.water, result.evaluation, result.knowledge)):
            st.info("没有可展示的结果；请查看上方错误和调试信息。")

    with evidence_tab:
        st.subheader("这次回答用了哪些证据？")
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
        if not any((result.forecast, result.water, result.evaluation, result.knowledge)):
            st.info("本次没有通过验证的证据。")

    with debug_tab:
        st.caption("仅展示路由和工具状态；不展示 API key 或原始请求头。")
        st.json({
            "question": result.question,
            "as_of_utc": result.as_of_utc,
            "route": result.plan.steps,
            "knowledge_concepts": result.plan.knowledge_concepts,
            "evaluation_scope": result.plan.evaluation_scope,
            "tool_trace": result.trace,
            "errors": result.errors,
        })


st.title("🌊 FloodOps 历史回放")
st.write("输入问题，查看当时可用的预测或水位、事后评估指标，以及可定位的文档证据。")

with st.form("ask_form"):
    question = st.text_input(
        "你的问题",
        value="当时预测峰值是多少？项目内部 Watch 是什么",
        help="示例：当时水位是多少；本批次 MAE 是多少；2米以上高水位 MAE 是多少；模型局限是什么。",
    )
    as_of_utc = st.text_input("历史回放时刻（UTC）", value="2026-08-03T01:48:00Z")
    st.caption("仅预测和历史水位需要此时间；评估指标来自事后快照，会单独标记。")
    use_llm = st.checkbox("用 DeepSeek 为文档证据生成短解释", value=False)
    submitted = st.form_submit_button("提问并取证", type="primary")

if submitted:
    try:
        output = run_query(question, as_of_utc=as_of_utc, use_llm=use_llm)
    except UnsupportedRoute as exc:
        st.error(str(exc))
    else:
        show_result(output)

with st.expander("可尝试的问题与边界"):
    st.markdown(
        """- `当时预测峰值是多少？项目内部 Watch 是什么`：预测工具＋文档检索。
- `当时水位是多少`：历史声纳工具。
- `本批次 MAE 是多少`：总体评估 JSON；必须显示版本、样本和米。
- `2米以上高水位 MAE 是多少`：高水位评估 JSON，不与总体值混淆。
- `模型局限是什么`：引用高水位评估报告，不声称已验证洪水事件召回率。

没有经过 House Mill 场地管理者批准的专属 SOP；本应用不提供现场行动指令。"""
    )
