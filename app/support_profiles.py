"""Reviewed support contracts for the current small curated corpus.

These are not queries or model judgments. Each contract describes which
attribute a source can support, mandatory source anchors, and a bounded claim.
Unknown attributes remain unsupported even when vector similarity is high.
"""

from dataclasses import dataclass


# Fingerprints of the exact reviewed excerpts (LF, no trailing newline), NOT
# copied dynamically from an editable catalog. A new/changed paragraph needs
# a new manual support review before it can inherit these bounded claims.
REVIEWED_EXCERPTS = {
    "internal_watch": "94dac2af8f24ff9ffa64066837d8fa9fff0bb471b8303c3e98ee43cb4f75213f",
    "internal_warning": "94dac2af8f24ff9ffa64066837d8fa9fff0bb471b8303c3e98ee43cb4f75213f",
    "highwater_evaluation_limit": "9cb750d5a736b43d505d69d6836dcad27f9e289a9810bf51f6b4442791afff59",
    "thesis_forecast_design": "fb4c8daffacf2df27fd74e21ee3f8f33c5292c37b96fe5d1cf32203cb901a9b1",
    "thesis_risk_terms": "6572bab36614bcdbaa35d432ef9ed68d783666f221d33e35a80a9879ab531a31",
    "thesis_offline_events": "1f906f6094f4628273c9ad459ce26bd693c5d82cd9f28aae2aa21c3a6e88e5eb",
    "thesis_live_limit": "102265ce617a4bc79c508e530e2e14421a5a356ebd4cc669efa1ae212512c3bd",
    "thesis_rapid_rise": "73ede1be8256c6a88ad9a66d8ba4c32e80f6fe46495dce00eeb64c770f87fde2",
    "thesis_model_design": "10b66d4323cd7877c6d6bfa7f5dca55e471bbfec176e601a03ecf02886fe2989",
    "housemill_heritage": "f87d3be27a87e3b551c0bff3b620e4b9d8166825c55eafed922f452ef14849f2",
    "housemill_flood_context": "64f283b54a128d95c33cd53b17c05693c9850f90bfd61df0b8a51be50f0174ab",
    "housemill_old_sensor": "7f876804d96707566fcd8945350793d079b6a6b0bb26428f6f8cc641a6a3d165",
    "housemill_study_findings": "bf3c5f99cf9413e51ab50e2fbe1b8a510e23ef7738fb5b5c2c01a54ed91214f0",
    "housemill_volunteer_need": "a66a79d68ae81f8f3247b7ae98f8258f89f2f1b094a8bc9a651e21b504a7e37e",
    "housemill_project_connection": "89038e8971d4ea0997cc52d13803d812fe1fd0a21d20bf89ca110bf673249dad",
}


@dataclass(frozen=True)
class SupportProfile:
    concept: str
    attributes: tuple[str, ...]
    anchors: tuple[str, ...]
    text: str


PROFILES = {
    "internal_watch": SupportProfile("internal_watch", ("meaning_and_scope", "value"),
        ("4.20", "4.43", "Watch"),
        "项目内部 Caution（界面统一显示名称）表示：未来预测的最高水位在 4.20 m（含）到 4.43 m（不含）之间，接近项目洪水线。这是内部等级，不是官方警报。"),
    "internal_warning": SupportProfile("internal_warning", ("meaning_and_scope", "value"),
        ("4.43", "Warning", "1 小时"),
        "项目内部 Warning 的条件是预测最高水位达到 4.43 m，或连续超过 4.43 m 达到 1 小时。这是内部规则，不代表官方发布了警报。"),
    "thesis_risk_terms": SupportProfile("thesis_risk_terms", ("meaning_and_scope", "value"),
        ("4.20 m", "4.43 m", "4.70 m", "two hours"),
        "论文把 4.20 m 这一档称为 Caution；Warning 条件包括达到 4.43 m 或持续至少 1 小时；Severe 条件包括达到 4.70 m、持续超过 4.43 m 至少 2 小时，或预计 2 小时内越过 4.43 m。这些是项目内部规则。"),
    "thesis_forecast_design": SupportProfile("thesis_forecast_design", ("method", "meaning_and_scope"),
        ("sonar measurements", "rainfall", "tidal", "Seven days", "15-minute", "96 points"),
        "论文中的方法是把声纳水位、降雨和潮汐一起用于预测：看过去 7 天、每 15 分钟一次的观测，预测接下来的 96 个点。这里说的是预测方法，不是当前水情。"),
    "thesis_model_design": SupportProfile("thesis_model_design", ("method", "meaning_and_scope"),
        ("asymmetric Huber", "classification head", "confidence gate", "not the classification score"),
        "论文对模型做了两项改进：训练时更重视洪水条件下的误差，另加一个洪水分类分支作置信度门控。最终预测结果仍直接来自预测水位，不是分类分数。"),
    "thesis_offline_events": SupportProfile("thesis_offline_events", ("meaning_and_scope", "value", "limitations"),
        ("windows overlap", "15.0 out of 17.4", "86.19%", "81.50%"),
        "论文离线历史事件评估的平均召回率是 86.19%，平均精确率是 81.50%。评估窗口有重叠，不是彼此独立的年度实验；这些离线结果不能当成线上检出能力。"),
    "thesis_live_limit": SupportProfile("thesis_live_limit", ("limitations", "meaning_and_scope", "value"),
        ("76,091", "no observed level reached", "cannot demonstrate live flood detection"),
        "部署期有 76,091 个匹配预测点，但实测没有达到 4.20 m Caution 阈值。因此这段数据不能证明线上洪水事件检出能力；没有报警不能据此解释为洪水检出表现好。"),
    "thesis_rapid_rise": SupportProfile("thesis_rapid_rise", ("limitations", "meaning_and_scope", "cause"),
        ("beginning of the rise was delayed", "30–60 minutes"),
        "论文记录的那次快速涨水中，模型预测到上涨和峰值，但上涨开始的预测约晚了 30–60 分钟。这是该案例里的表现，不能说每次涨水都固定延迟这么久。"),
    "housemill_old_sensor": SupportProfile("housemill_old_sensor", ("placement", "method", "meaning_and_scope"),
        ("Equipment was placed within the mill above an opening towards the channel", "Arduino", "Raspberry Pi"),
        "旧项目把声纳设备放在磨坊内部，朝地板开口下方的水道测量；声纳连到 Arduino 和树莓派，还配了红外相机。资料记录的是旧布置，不证明设备今天仍在运行。"),
    "housemill_heritage": SupportProfile("housemill_heritage", ("meaning_and_scope", "placement"),
        ("Grade I", "1776", "Bromley-by-Bow"),
        "House Mill 是伦敦 Bromley-by-Bow 三磨坊地区的一座历史潮汐磨坊，建于 1776 年，列为 Grade I 历史建筑。这是建筑背景，不是当前洪水状态。"),
    "housemill_flood_context": SupportProfile("housemill_flood_context", ("cause", "meaning_and_scope"),
        ("River Lea", "incoming Thames tides", "beams and floorboards"),
        "研究论文说明，河流水位和泰晤士河涨潮可能让水接触磨坊的木梁和地板，短时或非工作时间的事件可能被志愿者漏看。这是历史研究背景，不是今天的情况。"),
    "housemill_study_findings": SupportProfile("housemill_study_findings", ("value", "meaning_and_scope", "comparison", "connection"),
        ("136 water-contact events", "42 were high enough", "different thresholds", "53 minutes"),
        "旧研究的 136 次是水接触地板的事件，其中 42 次水位高到能明显看到：这是不同阈值，不是同一件事统计出两个竞争数字。平均事件时长为 53 分钟，也不是 FloodPred 的预测准确率。"),
    "housemill_volunteer_need": SupportProfile("housemill_volunteer_need", ("meaning_and_scope", "method"),
        ("duration of water touching timber", "more understandable information", "does not establish a House Mill emergency SOP"),
        "旧研究认为，志愿者需要更容易看懂的信息，尤其是水接触木结构会持续多久；旧 Grafana 页面偏技术化。这份资料未提供 House Mill 现场应急 SOP。"),
    "housemill_project_connection": SupportProfile("housemill_project_connection", ("connection", "comparison", "method", "meaning_and_scope"),
        ("sonar data-acquisition system builds on the open-source House Mill monitoring implementation", "forecast pipeline with rainfall and tide inputs", "not proof that the old equipment"),
        "毕业论文明确记载：FloodPred 的声纳采集实现思路承接旧 House Mill 开源监测项目，并扩展为包含降雨和潮汐的预测流程。这说明项目实现的承接，不能证明继承了同一套实体设备，也不能证明设备或云服务现在在线。"),
    "highwater_evaluation_limit": SupportProfile("highwater_evaluation_limit", ("limitations", "meaning_and_scope"),
        ("4.20", "0", "不能"),
        "现有高水位评估资料没有验证真实超阈值洪水事件的检出能力。点级误差不能代替洪水事件检出评估。"),
}
