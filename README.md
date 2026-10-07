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

**175 regression tests passed on 5 October 2026**, covering routing, source integrity, time leakage, partial answers, budgets, and web restrictions. This is a recorded test result, not an open-ended accuracy or security guarantee.

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

The stack uses Python, Streamlit, Pydantic, NumPy, SQLite, and the optional DeepSeek API. Orchestration uses ordinary Python; no LangChain, LangGraph, or external vector database is required.

## Scope

- A local research MVP with curated evidence rules, not a general-purpose fact checker or production service.
- Historical replay only; the deployment evaluation contained no observed threshold-crossing flood event, so live event detection remains unverified.
- Automatic web investigation and external evidence validation are still pending.

The bundled dataset is a small demonstration subset. Check [data provenance](demo_data/PROVENANCE.md) and usage rights before redistribution. Keep API keys, local backups, and request logs out of version control.

Further reading: [architecture](docs/08_architecture.md), [security](docs/09_security.md), and [House Mill sources](docs/12_housemill_context.md).
