# FloodOps Agent D1–D3 工作报告

日期：2026-09-29  
目标：`C:\UCL\DissertationAgent`  
结论：**D1–D3 已完成一个可运行的、无 LLM 的历史回放纵向切片。** 云服务器和外部 API 均不需要。原 D1–D2 工作报告 `reports/D1-D2_work_report_2026-09-29.md` 保留且未改动。

## 1. 交付范围与验收

| 日程 | 完成内容 | 交付位置 |
| --- | --- | --- |
| D1 | 定义目标、边界、三类问法；核对原服务、模型和两份 SQLite；建立源资产备份 | `docs/01_scope.md`、`docs/02_asset_inventory.md`、`backup/source_snapshot_2026-09-29/` |
| D2 | 只读读取归档 run/96 点预测，按历史时刻选择当时已生成、已入库且仍有效的 run | `app/archive.py`、`app/cli.py` |
| D3 | Pydantic 回放卡数据结构、固定问句路由、归档预测和声纳纵向连接、中文命令行回放卡 | `app/replay.py`、`app/demo.py`、`README_D3.md` |
| D3 时间闸门 | 防止事后才获得的预测/观测流入“当时”答案 | `app/replay.py`、`tests/test_replay.py` |
| D1–D3 验证 | 7 个聚焦单元测试和真实归档 CLI smoke test | `tests/test_archive.py`、`tests/test_replay.py` |

这不是洪水预测模型的新训练或实时推理；D3 读取的是当时已保存的预测归档。没有引入 LLM、RAG、FastAPI、LangGraph 或前端，因此可以在当前离线条件下直接演示“提问 → 找到当时可用证据 → 生成风险卡”的核心数据链。

## 2. 开工前备份和隔离情况

开始 D3 开发前，先在**新目录** `backup/d1_d2_snapshot_2026-09-29/` 复制 D1–D2 的 `app/`、`docs/`、`reports/`、`tests/`、`README.md`、`.gitignore`。快照共 **13 个文件、41,891 字节**；复制时对每个文件与原件计算 SHA-256，**0 个不一致**。快照中包括原 `D1-D2_work_report_2026-09-29.md`。D3 完成后再次抽核全部 D1–D2 正式源文件及原报告，**仍与快照相同**。

该新快照是 D1–D2 代码与文档的冻结点；D1 时建立的 `backup/source_snapshot_2026-09-29/` 则仍是服务代码、权重、历史 CSV 和归档数据库的独立数据/源资产备份。为避免无意义的 203 MB 重复复制，新快照未再次复制原资产备份或 `.venv`。**两个备份目录均没有作为 D3 的写入目标，原始 `C:\UCL\Dissertation` 和 `C:\UCL\CASA0016` 也未修改。** D1 原报告记载源资产备份 57 个文件、203,323,176 字节且逐件 hash 一致；其备份范围和曾清理的 SQLite 辅助文件详情仍以该报告为准。

D3 新建 `.venv-d3/`，与 D1–D2 的 `.venv/` 分离。Python 是 **3.12.14**，`sys.prefix` 指向 `C:\UCL\DissertationAgent\.venv-d3`。只在 D3 环境安装 Pydantic **2.13.5** 及其四个依赖：`pydantic_core 2.46.5`、`annotated-types 0.8.0`、`typing-inspection 0.4.4`、`typing_extensions 4.16.0`。第一次安装受沙箱网络限制失败，经授权后安装成功；没有使用或修改原环境。数据访问只使用 Python 标准库 `sqlite3`，数据库采用 `mode=ro&immutable=1`，不生成 WAL/SHM，也不写原库。

## 3. D3 数据结构与数据流

`ReplayCard` 是 Pydantic `BaseModel`：明确定义 `mode=historical_replay`、`as_of_utc`、64 位十六进制 `run_id`、生成/入库时间、预测峰值及其目标时间、内部等级、可见的最新声纳观测和 `official_warning_status=not_checked`。`Observation` 把观测时间和入库时间分开建模。两者均为 frozen model，生成后不能原位改写，减少展示层意外覆盖证据的风险。

命令行仅识别 `当时预测峰值是多少`（可带中文/英文问号，忽略空白）。路由是确定性的，不会把其他问句交给 LLM 猜答。`answer_question()` 调用 D2 的 `get_forecast_as_of()` 选择归档 run，按该 run 的预测点取峰值并取对应 `target_utc`，再从声纳库挑出当时已可见的最近观测。`render_card()` 负责中文展示。未知问题明确报错；没有可用 run 时也明确报错，不用事后数据补洞。

展示选择 **预测点原始精度** `3.7832 m`。归档 run 摘要的 `max_predicted_m` 是 `3.783 m`，二者相差 `0.0002 m`，属于摘要舍入；D2 已检查摘要与点级峰值差异不得超过 `0.01 m`。`2026-08-03T16:15:00Z` 晚于回放时刻是合理的：它是当时形成的**未来预测目标时刻**，不是事后观测。

## 4. `as_of_utc` 时间闸门与信息泄漏边界

1. D2 筛选 run 时要求 `forecast_generated_utc <= as_of_utc`、`stored_at_utc <= as_of_utc <= valid_until_utc`。
2. D3 再核对该 run 所用 `history_last_utc`，以及数据质量元信息中的 `history_latest_utc`、`sonar_latest_utc`，均不得晚于 `as_of_utc`；异常时拒绝整张卡，而不是忽略矛盾字段。
3. 声纳观测必须同时满足 `date_utc <= as_of_utc` **和** `stored_at_utc <= as_of_utc`。例如一条时间戳较早、但后来才补录的观测，会被排除。目标时刻晚于闸门的预测点可以展示，因为其值和时间已经包含在当时入库的预测 run 中。
4. 未查询或引用事后发生的真实水位来回答“预测峰值”；卡上“当时最新可见水位”单独标注观测时间和入库时间，不与预测峰值混为一谈。

这是针对当前两个 SQLite 表和现有数据质量字段的时间闸门，不代表未来接入所有数据源后自动具备全局防泄漏能力。未来加入降雨、潮汐、文档或复盘评估时，每种证据仍需分别检查发生时间、取得/入库时间和用途。

## 5. 用户如何测试

打开 **PowerShell**，输入：

```powershell
cd C:\UCL\DissertationAgent
$env:PYTHONIOENCODING='utf-8'
.\.venv-d3\Scripts\python.exe -m app.demo --as-of 2026-08-03T01:48:00Z
```

出现 `请输入问题：` 时，输入 `当时预测峰值是多少`，回车。也可不进入交互，直接运行：

```powershell
.\.venv-d3\Scripts\python.exe -m app.demo --as-of 2026-08-03T01:48:00Z --question '当时预测峰值是多少'
```

预期关键输出：

```text
洪水预测回放卡｜历史回放（非实时、非官方预警）
回放时间 as_of_utc：2026-08-03T01:48:00Z
当时预测峰值：3.7832 m
预测峰值时间：2026-08-03T16:15:00Z
run_id：ce9a940aa8807b10c85bc20754911bafdc8eca122053e6edc7273edd23efbb38
内部等级：No risk（level 0）
```

卡上还会显示预测生成、归档入库，以及当时已可见的最新声纳水位 **1.140 m**（观测 `01:44:14.857087Z`；入库 `01:44:14.868344Z`）。这段输出已用真实备份库实测。若控制台中文乱码，上面的 `PYTHONIOENCODING` 设置应先执行；`--as-of` 必须包含 `Z` 或其他明确时区。

运行聚焦测试：

```powershell
.\.venv-d3\Scripts\python.exe -m unittest discover -s tests -v
```

本次结果：**7/7 通过**（D1–D2 原有 4 个，D3 新增 3 个）。D3 测试分别覆盖卡上必需字段、未来/晚入库声纳被排除、预测入库前不可见、未来数据质量时间被拒绝，以及不支持问句明确报错。并完成了真实库的一次性命令和交互输入路径各 1 次 smoke test，没有运行大规模回归、模型加载或压力测试。

## 6. 已知限制与下一步

- 当前只支持一个固定问句、一个站点（`house_mill`）和一张回放卡；还不是开放式 Agent，也不具备 RAG。这个约束是 D3“先做无 LLM 纵向切片”的刻意范围。
- 使用的峰值是历史**预测**，不是该目标时间的真实水位。`No risk` 是归档服务内部分类，不等于洪水不存在，也不等于官方警报；`official_warning_status` 仍是 `not_checked`。
- `.venv-d3` 尚未装 PatchTST/torch，也未验证原模型本地实时推理；D3 不以此为验收条件。
- 对声纳库当前 3,805 行逐行解析时间，清晰可靠但未为更大库优化。扩展时应增加统一时间格式/索引和分层检索。
- 如后续要做现场行动建议，需要场地管理者认可的 SOP；当前卡不提供行动指令。暂不需要用户提供服务器、API key 或其他外部支持。

建议 D4 保持这张卡为可靠基线，再加入有来源和版本的知识材料；新数据源必须满足相同“事件时间＋可用时间”隔离，切勿让 RAG 或 LLM 把事后事实倒灌到历史预测。
