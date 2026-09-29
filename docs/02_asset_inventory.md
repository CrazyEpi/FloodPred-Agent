# D1 asset inventory and backup boundary

Inventory date: 2026-09-29. Original source directories were read only.

## Snapshot

`backup/source_snapshot_2026-09-29/` contains:

| Asset | Original location | Snapshot location | Observation |
| --- | --- | --- | --- |
| Cloud server | `C:\UCL\Dissertation\cloud_flood_server` | `cloud_flood_server/` | Source code, README, model, runtime, tests and configuration examples. |
| Forecast evaluation | `C:\UCL\CASA0016\CASA0022-Dissertation-FloodPredict\forecast_evaluation` | `forecast_evaluation/` | Evaluation code, output summaries/plots/CSVs and two SQLite databases. |

57 backed-up files (203,323,176 bytes) were SHA-256-compared with their
corresponding originals: **57 matches, 0 mismatches**. The original `.env`
containing API/MQTT secrets, top-level logs and top-level `__pycache__` were
not copied. Some nested Python cache files arrived with the runtime subtree;
they are not used by the new project. This is a task-scoped backup, **not** a
full copy of every dissertation training dataset or all `C:\UCL` work.

Five anchor hashes, SHA-256:

| File | Hash |
| --- | --- |
| `server.py` | `5C5398B645A65742C0D7B6EB7D368FE0E2BF07FAC0D5CFA2682B810E6F9B56C3` |
| `checkpoint_15min.pth` | `1B2E45E859298119A86D50A9002434F79247B1EE19315FCF4375353CD7F03175` |
| `history_reference_15min.csv` | `6FE970290D02605B0ED5FDB767304C096839C7A11EF2F0E863CB2AFDF6107CE2` |
| `prediction_history.sqlite3` | `9712AF08ADCACC0426AA1D974CCDD9C758B142CA6967A479CFB04ECE7044EE13` |
| `sonar_history.sqlite3` | `FA7592DD44563C243569714419B75B841AC6B0F489C6267B99862CC4FAB50256` |

## SQLite schema and sample size

The forecast database has `forecast_runs` keyed by `run_id` and
`forecast_points` keyed by (`run_id`, `target_utc`). It contains **817 runs**
and **78,432 forecast points** (96 per run), with run-generation timestamps
from 2026-07-23 to 2026-08-10 UTC. The runs include generation, storage and
expiry timestamps, internal risk class, archived peak and quality JSON.

The sonar database has `sonar_readings` with observation `date_utc`, archive
`stored_at_utc`, internal water level and device metadata. It contains
**3,805 readings**, observed between 2026-07-14 and 2026-08-10 UTC. A later
retrospective tool must compare *both* observation and storage times with the
replay cutoff to prevent future-information leakage.

The evaluation `output/summary.json` is a 2026-08-10 snapshot: 76,091 matured
and matched target points, point-level MAE 0.212531 m. These overlapping
target points are not independent flood events. The high-water report shows
zero actual target points reaching the internal 4.20 m Watch threshold;
therefore event-detection performance is unverified.

## Important implementation observation

The original `server.py` uses its own HTTP server, not FastAPI. D1-D2 do not
modify or import it. The new archive reader uses only Python's standard
library and SQLite immutable read-only connections. The copied checkpoint
is 1,172,021 bytes; its existence alone does not prove that local inference
works. Testing local inference is deferred.

