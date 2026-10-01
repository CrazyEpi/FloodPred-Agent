# House Mill 背景知识的来源与边界

2026-09-30 将六条人工核对的背景记录加入 `knowledge/housemill_catalog.json`。笔记保存在 `knowledge/housemill/`，不是网页或论文的逐字全文。每次检索都校验笔记 SHA-256，引用定位保留原始 URL 或 PDF 页码；这不等于对网页内容的实时校验。

| 主题 | 原始来源 | 时间／边界 |
|---|---|---|
| 建筑地点、1776 年、Grade I | [Historic England list entry 1080970](https://historicengland.org.uk/listing/the-list/list-entry/1080970) | 历史建筑资料，不代表当前水情 |
| 潮汐／河水为何影响木结构 | Wilson & Zhang, *Uncharted Waters*，用户提供的 `CUPUM_HouseMill (4).pdf`，PDF 第 1–2 页 | 论文背景，非实时信息 |
| 声纳、Arduino、树莓派、MQTT、Grafana | [djdunc/housemill README](https://github.com/djdunc/housemill) | 早期项目文档；未验证设备今天是否在线 |
| 136 次接触与 42 次更明显事件 | Wilson & Zhang，PDF 第 3–4 页 | 2023-10-01 至 2024-09-30，不同阈值；不是 FloodPred 准确率 |
| 志愿者需要通俗界面、关心触水时长 | Wilson & Zhang，PDF 第 5 页 | 当时的需求讨论；无已核实的现场 SOP |
| 早期监测与 FloodPred 的关系 | Haoyu Hu 的 FloodPred 毕业论文，PDF 第 3、5 页 | 数据采集思路承接；不证明当前服务在线 |

原研究 PDF 的 SHA-256 是 `ca4f530aacce207bde30aaba511ea2a2373eaf016a57f4912be48a470357e70a`。djdunc README 的不同段落分别提到每轮 10 次和 11 次原始采样，本知识库只写“大约十分钟上报”，不固定原始采样次数。系统不把旧论文的历史监测事件数与本项目的 MAE、事件召回率混用；也不据此生成现场行动建议。

需要更新这些知识时，先重新核对原始来源，再更新笔记、摘要、版本和哈希。仅修改摘要或让模型自行补全，不构成可靠的更新。
