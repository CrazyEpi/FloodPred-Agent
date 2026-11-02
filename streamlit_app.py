"""FloodPred: a local, evidence-forward historical replay UI."""

from __future__ import annotations

import streamlit as st
import os

from app.archive import DEFAULT_DB
from app.investigation import run_investigation
from app.presentation import ResultPresentation, SOURCE_LABELS
from app.rag import DeepSeekClient
from app.replay import DEFAULT_SONAR_DB
from app.router import QueryResult, UnsupportedRoute, run_query, volunteer_fact
from app.conversation import ConversationStore, ConversationError, DEFAULT_TIME, submit_turn
from app.checkpoint_codec import CheckpointCodec
from app.chat_memory import ChatMemory, MemoryAccessError


st.set_page_config(page_title="FloodPred｜历史回放", page_icon="🌊", layout="wide")
st.markdown("""<style>
.block-container {max-width: 1120px; padding-top: 2.5rem; padding-bottom: 4rem;}
div[data-testid="stMetric"] {padding: 1rem 1.1rem; border: 1px solid rgba(127,127,127,.18); border-radius: 12px;}
div[data-testid="stMetricValue"] {font-size: 1.7rem;}
</style>""", unsafe_allow_html=True)


def show_result(result: QueryResult, debug_mode: bool = False) -> None:
    display = ResultPresentation(result)
    # An already-running Streamlit session may retain a pre-update result class.
    web_evidence = getattr(result, "web_evidence", [])
    web_runs = getattr(result, "web_runs", [])
    if result.errors:
        for error in result.errors:
            st.error(display.text(error))
    for warning in result.warnings:
        if warning.startswith(("DeepSeek 理解问题时遇到困难", "DeepSeek 主题识别未通过")) and any(
            entry.get("step") == "intent_plan" and entry.get("error_type") in ("LLMRequestError", "LLMNotConfigured") for entry in result.trace):
            st.warning("DeepSeek 未能完成问题理解，本次采用程序规则取证。")
        if not warning.startswith(("缺口排序未完成", "表达整理未完成", "DeepSeek 理解问题时遇到困难", "DeepSeek 主题识别未通过")) and (shown := display.text(warning).strip()):
            st.warning(shown)
    tabs = st.tabs(["回答", "证据", "运行细节"] if debug_mode else ["回答", "证据"])
    result_tab, evidence_tab = tabs[:2]

    with result_tab:
        if result.investigation_rounds:
            st.caption(f"本次调查取证 {result.investigation_rounds} 轮、调用只读工具 {result.investigation_tool_calls} 次。")
        if result.fallback_text:
            st.warning(display.text(result.fallback_text))
        if result.answer_text:
            if result.response_mode == "project":
                st.write(display.answer())
                if result.answer_status == "partial_verified":
                    st.caption("这是根据已核验残余资料整理的局部结论，不是整个问题的完整答案；缺失部分没有推测或补写。")
                else:
                    st.caption("只整理逐项核对通过的结论；每句话后面的编号对应‘证据’里的原文。")
            else:
                st.write(display.answer())
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
        if result.knowledge and not result.answer_text:
            st.subheader("从资料里查到的解释")
        for item in (result.knowledge if not result.answer_text else []):
            with st.container(border=True):
                st.write(display.text(volunteer_fact(item.fact)))
                if item.explanation:
                    st.write(f"换句话说：{display.text(volunteer_fact(item.explanation))}")
                source_label = {"internal_project": "项目服务端文档", "evaluation_report": "项目评估报告", "thesis": "毕业论文", "official_public_guidance": "英国官方公开资料", "research_paper": "House Mill 历史研究论文", "prior_project": "早期传感器项目仓库", "heritage_public": "Historic England 建筑档案"}.get(item.source_type, item.source_type)
                st.caption(source_label)
                if "Watch" in item.fact:
                    st.caption("Caution 是本界面的统一显示名；源文件中的名称见“证据”。")
        if result.answer_status == "candidate_only":
            st.info("找到了一些相关资料，可在‘证据’中查看。还没有确认它们能回答整个问题，所以这次只展示候选段落。")
        if not any((result.answer_text, result.forecast, result.water, result.evaluation, result.knowledge, result.document_candidates, web_evidence)):
            st.info("没有可展示的结果；请查看上方提示。")

    with evidence_tab:
        st.subheader("这次回答依据什么？")
        if result.question_evidence:
            st.write("**问题有没有查清楚？**")
            st.table([{"问题": display.question_labels.get(row.node_id, row.question),
                       "证据状态": row.to_dict()["status_label"],
                       "还缺什么": display.text(display.reasons(row)) or "本项已通过核对"}
                      for index, row in enumerate(result.question_evidence, 1) if not row.excluded])
            for index, row in enumerate(result.question_evidence, 1):
                if row.conflicts:
                    st.warning(f"问题 {index} 的资料有冲突，没有替你选择某个版本；相关版本和原文见下方来源。")
                    if debug_mode:
                        st.json(row.conflicts)
        for index, claim in enumerate(display.display_claims, 1):
            with st.expander(f"结论 {index} · {display.text(claim.text)}"):
                st.write(display.reference(claim.citations))
                if claim.derivation:
                    st.caption("有限推导依据：" + display.text(claim.derivation))
                if debug_mode:
                    st.write("内部结论 ID：" + "、".join(item.id for item in display.claim_groups[index - 1]))
        for citation in display.sources:
            number = display.numbers[citation.id]
            with st.expander(f"来源 {number} · {display.title(citation)}"):
                st.write(f"资料版本：`{citation.version}`")
                snapshot = next((item for item in web_evidence if citation.id.startswith(item.id + ":")), None)
                if snapshot:
                    st.markdown(f"[查看原始网页]({snapshot.page.url})")
                    st.caption(f"抓取时间：{snapshot.page.fetched_at_utc}")
                    if snapshot.page.published_at:
                        st.caption(f"页面标注发表时间：{snapshot.page.published_at}")
                for source in display.source_citations(number):
                    if source.unit:
                        st.caption("单位：" + source.unit)
                    if debug_mode:
                        st.write(f"引用：{source.id}")
                        st.code(source.locator, language=None)
                        st.caption(f"{source.source_type} · 时间范围：{source.time_scope}")
                        if source.sha256:
                            st.caption("SHA-256：" + source.sha256)
                    quote = display.text(source.quote) if source.source_type in ("historical_forecast", "historical_observation") else source.quote
                    st.code(quote, language=None)
        if result.forecast:
            card = result.forecast
            with st.container(border=True):
                st.write("**归档预测（只读）**")
                if debug_mode:
                    st.code(str(DEFAULT_DB), language=None)
                    st.write(f"run_id：`{card.run_id}`")
                st.write(f"生成：`{card.forecast_generated_utc}` · 入库：`{card.forecast_stored_at_utc}`")
        if result.water:
            with st.container(border=True):
                st.write("**历史声纳观测（只读）**")
                if debug_mode:
                    st.code(str(DEFAULT_SONAR_DB), language=None)
                st.write(f"观测：`{result.water.observed_utc}` · 入库：`{result.water.stored_at_utc}`")
        if result.evaluation:
            metric = result.evaluation
            with st.container(border=True):
                st.write("**结构化评估快照（事后）**")
                st.write(f"版本：`{metric.generated_utc}`")
                if debug_mode:
                    st.code(metric.source_locator, language=None)
                    st.write(f"SHA-256：`{metric.source_sha256}`")
                st.write(metric.method)
        for item in result.knowledge:
            if item.citation_id in display.numbers:
                continue
            with st.container(border=True):
                st.write(f"**{item.title}**")
                if debug_mode:
                    st.code(item.locator, language=None)
                    st.write(f"引用 ID：`{item.citation_id}` · {item.source_type}")
                if item.version:
                    st.write(f"资料版本：`{item.version}`")
                if debug_mode and item.source_sha256:
                    st.write(f"本地源文件 SHA-256：`{item.source_sha256}`")
                if debug_mode and item.retrieval_methods:
                    st.caption(f"检索方式：{', '.join(item.retrieval_methods)}。相似度只是候选排序，不是事实支持证明。")
                st.write(f"已核验的来源说明：{display.text(item.fact)}")
                if "Watch" in item.fact:
                    st.caption("显示名称 Caution 对应此来源中的 Watch；源文件和引用 ID 保留原词，不能把别名当成原文。")
        if result.document_candidates:
            st.subheader("检索到的相关资料")
            st.caption("这些是检索候选。文件校验和相似度不能证明段落足以支持答案；未用于本次结论的资料也列在这里，方便核对。核对日期不代表资料在历史回放时已经可用。")
            selected_ids = {item.citation_id for item in result.knowledge}
            for hit in result.document_candidates:
                record = hit.record
                paragraph_ids = {record["id"], *(item["id"] for item in hit.equivalent_records)}
                if paragraph_ids & display.numbers.keys():
                    continue
                label = "本次已选来源条目" if paragraph_ids & selected_ids else "仅候选，未用于结论"
                with st.expander(f"{record['title']} · {record['section']} · {label}"):
                    st.caption(f"{SOURCE_LABELS.get(record['source_type'], '项目资料')} · 版本：{record['version']}")
                    if debug_mode:
                        st.caption(f"ID：{record['id']}")
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
                    for item_id in paragraph_ids:
                        if item_id in result.source_rejections:
                            st.warning(display.text(result.source_rejections[item_id]))
        if not any((result.forecast, result.water, result.evaluation, result.knowledge, result.document_candidates, web_evidence)):
            st.info("本次是普通对话或澄清问题，没有调用项目数据和资料。" if result.answer_text else "本次没有通过验证的证据。")

    if debug_mode:
        with tabs[2]:
            st.caption("这里保留取证路径，方便复核；不会展示 API key 或请求头。")
            st.write("原始回答（保留内部引用）：")
            st.code(result.answer_text or "（无）", language=None)
            st.json({
                "source_mapping": [{"number": display.numbers[citation.id],
                                    "claim_id": claim.id, "citation_id": citation.id,
                                    "locator": citation.locator, "version": citation.version,
                                    "source_type": citation.source_type, "quote": citation.quote,
                                    "sha256": citation.sha256, "time_scope": citation.time_scope,
                                    "unit": citation.unit}
                                   for claim in result.verified_claims for citation in claim.citations],
                "request_id": result.request_id,
                "audit_status": result.audit_status,
                "question": result.question,
                "as_of_utc": result.as_of_utc,
                "route": result.plan.steps,
                "response_mode": result.response_mode,
                "knowledge_concepts": result.plan.knowledge_concepts,
                "evaluation_scope": result.plan.evaluation_scope,
                "question_graph": result.question_graph.to_dict() if result.question_graph else None,
                "web_decisions": result.web_decisions,
                "web_answer_integration": "bounded_public_evidence",
                "web_runs": web_runs,
                "web_sources": [{"id": item.id, "topic": item.topic, "url": item.page.url,
                    "fetched_at_utc": item.page.fetched_at_utc, "sha256": item.page.raw_sha256,
                    "paragraphs": item.page.paragraphs} for item in web_evidence],
                "question_evidence": [row.to_dict() for row in result.question_evidence],
                "source_rejections": result.source_rejections,
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
st.session_state.pop("last_resume_token", None)

store = ConversationStore()
if "chat_access" not in st.session_state:
    capability = store.create()
    st.session_state["chat_access"] = {capability: "新对话"}
    st.session_state["chat_selected"] = capability
if st.session_state.get("chat_selected") not in st.session_state["chat_access"]:
    st.session_state["chat_selected"] = next(iter(st.session_state["chat_access"]))
capability = st.session_state["chat_selected"]
conversation = store.load(capability)
memory = ChatMemory(store, capability)

def select_conversation():
    selected = st.session_state["chat_picker"]
    st.session_state["chat_selected"] = selected
    st.session_state.pop("last_result", None)
    st.session_state.pop("replay_time", None)
    st.session_state.pop("chat_transient_error", None)

def activate_conversation(selected):
    st.session_state["chat_selected"] = selected
    st.session_state["chat_picker"] = selected
    for key in ("last_result", "replay_time", "chat_transient_error", "confirm_clear_chat", "preference_plain"):
        st.session_state.pop(key, None)

def new_conversation():
    selected = store.create()
    if st.session_state.get("preference_code"):
        try:
            ChatMemory(store, selected).attach_profile(st.session_state["preference_code"], confirmed=True)
        except MemoryAccessError:
            st.session_state.pop("preference_code", None)
    st.session_state["chat_access"][selected] = "新对话"
    activate_conversation(selected)

def restore_conversation():
    try:
        selected = st.session_state["chat_restore_code"].strip()
        recovered = store.load(selected)
        st.session_state["chat_access"][selected] = recovered["title"]
        activate_conversation(selected)
        st.session_state.pop("chat_restore_error", None)
    except ConversationError as exc:
        st.session_state["chat_restore_error"] = str(exc)

def clear_conversation():
    store.clear(st.session_state["chat_selected"])
    activate_conversation(st.session_state["chat_selected"])

def preference_action(action):
    if not st.session_state.get("confirm_preference_memory"):
        st.session_state["preference_feedback"] = "请先确认偏好记忆操作。"
        return
    selected = st.session_state["chat_selected"]
    manager = ChatMemory(store, selected)
    try:
        if action == 'save':
            code = manager.save_preferences(bool(st.session_state["preference_plain"]), confirmed=True,
                                            profile_code=st.session_state.get("preference_code"))
            st.session_state["preference_code"] = code
            message = "已保存表达偏好；本机后续新对话会沿用。请保管偏好恢复码。"
        elif action == 'attach':
            code = st.session_state["preference_restore_code"].strip()
            manager.attach_profile(code, confirmed=True)
            st.session_state["preference_code"] = code
            message = "已将这份偏好用于当前会话和本机后续新对话。"
        elif action == 'detach':
            manager.forget()
            message = "当前对话已停用长期偏好；不会删除其他对话所用的偏好。"
        else:
            code = st.session_state.get("preference_code")
            if not code:
                raise MemoryAccessError("删除共享偏好需要偏好恢复码，请先导入。")
            manager.forget(profile_code=code)
            st.session_state.pop("preference_code", None)
            message = "已删除这份长期偏好，所有关联会话会停止读取它。"
        st.session_state["preference_feedback"] = message
        st.session_state.pop("preference_plain", None)
    except MemoryAccessError as exc:
        st.session_state["preference_feedback"] = str(exc)

with st.sidebar:
    st.subheader("对话")
    chat_labels = {key: value + ' · ' + key[:6] for key, value in st.session_state["chat_access"].items()}
    st.selectbox("当前对话", options=list(st.session_state["chat_access"]),
                 format_func=lambda item, labels=chat_labels: labels.get(item, "对话"),
                 index=None if "chat_picker" in st.session_state else list(st.session_state["chat_access"]).index(capability),
                 key="chat_picker", on_change=select_conversation)
    st.button("新建对话", key="new_conversation", on_click=new_conversation)
    with st.expander("保存／恢复对话"):
        st.caption("刷新或重启后可用恢复码找回；恢复码相当于访问凭据，请自行保管，不要分享。")
        if st.checkbox("显示本会话恢复码", key="show_chat_code"):
            st.code(capability, language=None)
        resume_code = st.text_input("对话恢复码", type="password", key="chat_restore_code")
        st.button("恢复对话", key="restore_conversation", on_click=restore_conversation)
        if st.session_state.get("chat_restore_error"):
            st.error(st.session_state["chat_restore_error"])
    with st.expander("清理本对话"):
        confirmed = st.checkbox("确认清空聊天记录与上下文", key="confirm_clear_chat")
        st.button("清空记录", key="clear_conversation", disabled=not confirmed, on_click=clear_conversation)
    context_budget = st.number_input("上下文预算（保守 token 上界）", min_value=2048, max_value=200000,
        value=max(2048, min(200000, int(os.environ.get("FLOODPRED_CHAT_CONTEXT_BUDGET", "65536")))), step=1024,
        help="默认 65536；当前只送结构化上下文，不会为了填满额度发送全部聊天历史。不是模型实际计费 token 数。")
    with st.expander("跨会话表达偏好（可选）"):
        pref = memory.preferences()
        st.caption("只保存固定的表达偏好，不自动记住项目结论、个人资料或网页指令。偏好恢复码与对话恢复码不同。")
        st.caption("当前：" + ("已关联偏好，版本 " + str(pref['revision']) if pref else "未使用跨会话偏好"))
        st.checkbox("少用术语、用通俗中文", value=bool(pref and pref['plain_language']), key="preference_plain")
        st.checkbox("确认偏好操作；保存或导入后同意本机新对话沿用", key="confirm_preference_memory")
        st.button("保存偏好", key="save_preference", on_click=preference_action, args=('save',))
        st.text_input("偏好恢复码", type="password", key="preference_restore_code")
        st.button("导入偏好", key="attach_preference", on_click=preference_action, args=('attach',))
        if st.button("查看偏好恢复码", key="show_preference_code"):
            if st.session_state.get("preference_code"):
                st.code(st.session_state["preference_code"], language=None)
            else:
                st.info("当前浏览器没有偏好恢复码，请先保存或导入。")
        st.button("本对话停用长期偏好", key="detach_preference", on_click=preference_action, args=('detach',))
        st.button("删除这份长期偏好（所有关联对话）", key="delete_preference", on_click=preference_action, args=('delete',))
        if st.session_state.get("preference_feedback"):
            st.info(st.session_state["preference_feedback"])

context = conversation["state"]
from app.conversation import TOPICS
labels = "、".join(TOPICS.get(key, key) for key in context["topics"]) or "尚未选定"
st.caption(f"当前话题：{labels} · 历史资料查询，非实时水情／非官方警报")
if context.get("run_id"):
    with st.expander("当前预测绑定"):
        st.caption("已绑定一条归档预测。追问会重新查归档，不从历史回答复制数值；修改时间后旧绑定失效。原始标识可在调试模式查看。")

# Chat input is a root-level Streamlit element: it remains pinned at the bottom.
from app.dialogue import submit_dialogue
from app.checkpoint_codec import CheckpointCodec

debug_mode = st.sidebar.toggle("调试模式", value=False, key="debug_mode")
allow_web = st.sidebar.toggle("允许联网补查", value=True, key="allow_web")
as_of_utc = None
if debug_mode:
    as_of_utc = st.sidebar.text_input("调试固定业务时刻（UTC）", value=context["as_of_utc"], key="replay_time")
    st.sidebar.warning("调试时间覆盖已开启：项目点时查询统一使用此时刻，原话时间仅供核对。生活聊天和消息时间不受影响。")
else:
    st.sidebar.caption("正常模式：使用用户指定的真实日期。House Mill 未注明时区时按 Europe/London 解释，不默认使用演示日期。")
use_llm = st.sidebar.checkbox("用 DeepSeek 理解和回答", value=bool(DeepSeekClient().api_key), key="use_llm")
use_thinking = st.sidebar.checkbox("复杂问题允许深度思考", value=True, key="use_thinking")
use_investigation = st.sidebar.checkbox("多步调查（最多 3 轮）", value=True, key="use_investigation")
with st.sidebar.expander("示例与使用说明"):
    examples={
        '聊天与记忆':['你是谁','我喜欢摄影','我刚才说喜欢什么？'],
        '历史数据':['2025年12月31日晚上11点45分 UTC 的 House Mill 内部水位是多少？','31 December 2025 at 11:45pm UTC water level'],
        '预测与资料':['2026-08-03T01:48:00Z 当时预测峰值是多少？','项目内部 Warning 是什么？'],
        '比较分析':['2025年12月31日23点45分 UTC 的内部水位，跟前一天比涨了吗？','2025年12月30日23点45分 UTC 至 2025年12月31日23点45分 UTC 的内部水位均值是多少？'],
        '边界与混合问题':['那天早上八点河里多高？','2023年1月2日早上8点的水位是多少？项目内部 Warning 是什么？']}
    group=st.selectbox("问题分类",list(examples),key='example_group')
    for example in examples[group]: st.code(example,language=None)
    st.caption("复制到底部输入框，再按发送。查看示例不会调用模型或更改时间。调试覆盖开启时请勿用比较示例。")
with st.sidebar.expander("数据源与覆盖范围"):
    try:
        import json
        from app.history_data import CATALOG
        sources=json.loads(CATALOG.read_text(encoding='utf-8'))['sources']
        st.caption(f"已登记 {len(sources)} 份历史文件；时间范围不代表中间无缺失。")
        for kind,label in [('processed_history','处理后历史'),('integrated_training','整合训练'),('raw_sensor','原始传感器')]:
            selected=[s for s in sources if s['kind']==kind]
            if selected: st.write(f"{label}：{min(s['first'] for s in selected)} 至 {max(s['last'] for s in selected)}")
        st.caption("原始距离、处理后水位、预测归档不是同一种数据。2026 年预测／观测归档由独立只读工具查询。")
    except (OSError,ValueError,KeyError): st.warning("历史来源目录不可用，请先生成并核对目录。")
visible = st.session_state.get("visible_turns", 30)
if len(conversation["turns"]) > visible:
    if st.button("加载更早记录"):
        st.session_state["visible_turns"] = visible + 30
        st.rerun()
for turn in conversation["turns"][-visible:]:
    with st.chat_message("user"):
        st.write(turn["raw_question"])
    with st.chat_message("assistant"):
        st.write(turn["answer"])
        if turn["result_json"]:
            st.caption("历史回放，不是实时水情或官方警报。")
            with st.expander("项目证据与风险卡"):
                try:
                    show_result(CheckpointCodec().loads(turn["result_json"]), debug_mode)
                except (ValueError, TypeError, KeyError):
                    st.warning("旧结果无法完整展示，请重新取证。")
        if debug_mode:
            with st.expander("需求与上下文解析"):
                st.json(__import__("json").loads(turn["context_json"]))
            with st.expander("时间 来源 分析与依据"):
                diagnostic=__import__('json').loads(turn['context_json'])
                st.write('原文时间 → 解析时间 → 调试覆盖／实际执行时间 → 查询来源 → 结果分析 → 回答依据')
                st.json({'time_policy':diagnostic.get('time_policy','legacy'),
                    'parsed_time':diagnostic.get('parsed_time'),
                    'demands':[{'id':n.get('id'),'text':n.get('text'),'query':n.get('query'),
                        'status':n.get('status'),'evidence':n.get('project_evidence',[])} for n in diagnostic.get('needs',[])]})
question = st.chat_input("聊点什么，或问我项目里的问题", key="chat_prompt")
if question:
    try:
        with st.status("正在处理", expanded=True) as status_box:
            outcome = submit_dialogue(store, capability, question, as_of_utc=as_of_utc,
                use_llm=use_llm, thinking_enabled=use_thinking, allow_web=allow_web,
                use_investigation=use_investigation, context_budget=int(context_budget),
                progress=lambda text: status_box.update(label=text),
                time_mode='debug' if debug_mode else 'normal')
            status_box.update(label="完成", state="complete")
        st.session_state["chat_access"][capability] = store.load(capability)["title"]
        st.session_state.pop("replay_time", None)
        st.rerun()
    except (UnsupportedRoute, ConversationError, ValueError) as exc:
        st.error(str(exc))
