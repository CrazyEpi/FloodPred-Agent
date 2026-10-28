# FloodPred Agent

FloodPred Agent is an **evidence-driven investigation assistant** for House Mill volunteers. It brings forecasting analytics, hybrid RAG, and bounded multi-step reasoning into a single conversational interface, with traceable sources behind each project conclusion.

Built on [FloodPredict](https://github.com/CrazyEpi/CASA0022-Dissertation-FloodPredict), it turns archived predictions and research material into accessible explanations backed by time-aware data access and claim-level evidence checks. The replay demo runs locally without the original cloud server.

> **Research prototype:** This is a historical replay tool, not a live forecast service, official flood alert, or guide for on-site safety decisions.

## Highlights

- **Multi-source intelligence:** combines archived forecasts, observations, evaluation metrics, and research documents.
- **Auditable answers:** preserves original question spans, source locations, versions, and claim-level citations.
- **Controlled autonomy:** investigates evidence gaps within explicit tool, model-call, and time budgets.

## How It Works

```text
User question → Validated question graph → Read-only routing
                                           |          |
                                  Structured data   Hybrid retrieval
                                           |          |
                                           Evidence gate
                                            |         |
                                       Supported    Missing evidence
                                            |         |
                                     Claim assembly ← Targeted follow-up
                                            |
                                  Final validation → Answer + sources
```

1. **Question planning.** Rules and optional DeepSeek assistance split complex questions into smaller requests while preserving their context.
2. **Dual-path retrieval.** Read-only tools retrieve numbers, while hybrid keyword and semantic search finds relevant documents.
3. **Time-aware replay.** Historical queries exclude observations unavailable at the replay time and clearly label later evaluations.
4. **Evidence checks.** Each conclusion is checked against source passages, versions, hashes, and units before entering the answer.
5. **Targeted follow-ups.** The agent investigates missing evidence within three rounds and fixed tool, model-call, and time budgets.
6. **Traceable answers.** DeepSeek arranges verified conclusions into answers with citations, clearly marking gaps when evidence is incomplete.

## Quick Start

From the repository root in Windows PowerShell, using Python 3.12+:

```powershell
py -3.12 -m venv .venv-d4
.\.venv-d4\Scripts\python.exe -m pip install -r requirements.txt
```

Reuse `.venv-d4` if it already exists, then start the bundled replay:

```powershell
$env:FLOODPRED_DATA_MODE = 'demo'
.\.venv-d4\Scripts\python.exe -m streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8501
```

Open http://127.0.0.1:8501. Use the replay time `2026-08-03T01:48:00Z` for the bundled example.

The UI and example queries are currently in Chinese. Core replay and document checks work without an API key.

For optional DeepSeek planning, copy [.env.example](.env.example) to `.env` on a new checkout, or edit your existing configuration:

```dotenv
DEEPSEEK_API_KEY=your_deepseek_api_key
```

Enable DeepSeek in the UI; Thinking has a separate switch. Never commit `.env`.

## Example Questions

The Chinese inputs below match the current demo:

| Question | What it checks |
| --- | --- |
| 当时预测峰值是多少？ | Forecast peak, target time, and run ID |
| 本批次 MAE 是多少？ | Evaluation value, version, sample range, and unit |
| 项目内部 Caution 是什么？ | Internal risk rules and their source |
| 你好，预测峰值和 MAE 的联系是什么？ | The difference between one forecast and average evaluation error |

Command-line replay also works without DeepSeek:

```powershell
.\.venv-d4\Scripts\python.exe -X utf8 -m app.investigate_cli "当时预测峰值是多少？"
```

Add `--llm` to use DeepSeek.

The UI uses **Caution** for the level called **Watch** in the original server documents. This is a display alias; source wording is preserved.

## Experimental Web Tools

The main chat does not yet search the web or use fetched pages as answer evidence. The standalone tools require explicit permission, restrict allowed hosts, validate public IP addresses and TLS, and block redirects and oversized responses.

Try the simulated workflow without a search account or network access:

```powershell
.\.venv-d4\Scripts\python.exe -X utf8 -m app.web_cli demo --allow-web
```

Real search currently uses the optional Brave adapter and requires `BRAVE_SEARCH_API_KEY` in `.env`. This is separate from the DeepSeek key. Without it, search returns `search_not_configured`. Search summaries and fetched pages remain unverified candidates.

## Tests

Run the offline regression tests:

```powershell
.\.venv-d4\Scripts\python.exe -X utf8 -m unittest discover -s tests -q
```

The offline suite covers routing, source integrity, time leakage, partial answers, budgets, web restrictions, and LangGraph migration parity. The migration tests retain a frozen pre-migration executor under `tests/support/` and compare evidence, outcomes, stopping reasons, and call counts. See the [migration report](reports/langgraph_migration_steps4_9_2026-10-28.md) for the current recorded results; these are not an open-ended accuracy or security guarantee.

Run the graph-specific checks separately:

```powershell
.\.venv-d4\Scripts\python.exe -X utf8 -m unittest tests.test_investigation_graph -v
```

See the [web-tool report](reports/web_step1_2_2026-10-05.md) and [evidence-check report](reports/P2_step3_4_evidence_2026-10-03.md) for detailed results.

## Repository Structure

```text
FloodPred-Agent/
|-- app/                 Planning, retrieval, evidence checks, and read-only tools
|-- knowledge/           Curated project and research excerpts
|-- demo_data/           Small replay dataset and evaluation snapshots
|-- docs/                Design, source, and security notes
|-- tests/               Offline regression tests
|-- reports/             Evaluation results and development reports
|-- streamlit_app.py     Demo interface
|-- requirements.txt     Python dependencies
`-- .env.example         Optional API configuration
```

The stack uses Python, Streamlit, Pydantic, NumPy, SQLite, LangGraph **1.2.18**, and the optional DeepSeek API. `run_investigation()` uses a sequential LangGraph workflow for first evidence, targeted follow-ups, evidence checks, finalization, and audit. Existing tools, hybrid retrieval, and the direct DeepSeek client remain in place. The full LangChain package and an external vector database are not required; LangGraph's necessary dependencies are installed by `requirements.txt`, with resolved additions pinned in `requirements_langgraph.lock.txt`.

The ordinary graph caches topology only; evidence, clients, and budgets are per request. Optional SQLite checkpoints now support cross-process resume with saved budgets and time scope. Node-result journaling reuses completed work; an interrupted node with an unknown call outcome stops conservatively instead of issuing the call again. The default 60-second deadline includes downtime. Parallel tools and automatic node retries remain disabled. External LangSmith tracing is disabled for investigation execution, including when enabled in the host environment.

## Web display and debug mode

The sidebar “调试模式” toggle is off by default. Normal mode shows only “回答” and “证据”: answers use friendly source numbers, with source titles, original excerpts and versions. Internal claim/citation IDs, forecast run IDs, hashes, full paths and retrieval details appear only in debug mode. Debug adds “运行细节”, including the original answer, citation mapping, trace, question graph and available model diagnostics. Thinking still independently controls the existing DeepSeek request; its default is unchanged.

Repeated product disclaimers are omitted from the displayed answer; source originals, evidence gaps and actual failures remain available. Raw answers are preserved in debug mode. For timeout settings and acceleration options, see [service operations](docs/06_local_service_operations.md).

The latest result stays in the current Streamlit session. Switching debug mode rerenders it without another investigation or model call. Submitting a failed new question clears the previous result. This session display is not a persistent checkpoint.

Offline display verification:

```powershell
.\.venv-d4\Scripts\python.exe -X utf8 -m unittest tests.test_presentation tests.test_streamlit_app -v
```

For manual verification, disable DeepSeek and submit the default historical question. Check the peak, date, units and friendly sources; turn debug on to see raw IDs, then off to hide them. Neither mode has recovery-code controls. See [manual acceptance steps](docs/14_checkpoints_and_demo.md#4-streamlit-测试).

## Local checkpoints and resume

The web UI uses ordinary investigations and has no save/resume controls. Existing saved investigations remain available through the CLI. Run the two-process offline demo:

```powershell
$env:FLOODPRED_DATA_MODE = 'demo'
.\.venv-d4\Scripts\python.exe -X utf8 -m app.checkpoint_cli demo
.\.venv-d4\Scripts\python.exe -X utf8 -m unittest tests.test_checkpoints -v
```

The default state database is `state/investigations.sqlite3`, separate from evidence databases and ignored by Git. It stores question/evidence snapshots, not model clients, API keys, or raw reasoning. Resume codes authorize access to one local investigation. Resume works at saved node boundaries, not arbitrary lines of code. See [checkpoint operations and testing](docs/14_checkpoints_and_demo.md) and the [checkpoint report](reports/checkpoints_and_demo_2026-10-28.md) for exact behavior, manual tests, and validated resume wording for a CV.

## Scope

- A local research MVP with curated evidence rules, not a general-purpose fact checker or production service.
- Historical replay only; the deployment evaluation contained no observed threshold-crossing flood event, so live event detection remains unverified.
- Automatic web investigation and external evidence validation are still pending.

The bundled dataset is a small demonstration subset. Check [data provenance](demo_data/PROVENANCE.md) and usage rights before redistribution. Keep API keys, local backups, and request logs out of version control.

Further reading: [architecture](docs/08_architecture.md), [migration contract](docs/13_langgraph_migration_contract.md), [checkpoints](docs/14_checkpoints_and_demo.md), [security](docs/09_security.md), and [House Mill sources](docs/12_housemill_context.md).
