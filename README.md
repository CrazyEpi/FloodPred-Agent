# FloodOps Agent - D1-D2 foundation

This directory is a **historical replay prototype**, not a live flood-warning
service. D1-D2 only inventory the existing work, make a local backup, and read
archived forecasts. No live API, MQTT, LLM, or model inference is called.

## Layout

- `backup/source_snapshot_2026-09-29/`: frozen copy of the relevant original
  cloud server and forecast-evaluation work. Treat as read-only.
- `.venv/`: isolated Python 3.12 environment. No third-party packages are
  required for D1-D2.
- `app/archive.py`: read-only archive access and historical-time selection.
- `app/cli.py`: small command-line interface.
- `tests/`: focused standard-library tests.
- `docs/`: scope and asset inventory.
- `reports/`: work report.

## Run on this Windows machine

From `C:\UCL\DissertationAgent`:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m app.cli --as-of 2026-08-03T01:48:00Z
```

Or query the exact archived run:

```powershell
.\.venv\Scripts\python.exe -m app.cli --run-id ce9a940aa8807b10c85bc20754911bafdc8eca122053e6edc7273edd23efbb38
```

Add `--include-points` to show all 96 fifteen-minute points. The database
defaults to the backup copy and is opened with SQLite `mode=ro&immutable=1`, so
queries do not write journal files in the backup. A custom database can be
passed with `--db` for tests or future snapshots.

## Important distinctions

`internal_risk_label` is the project's own rule classification; it is **not**
an official Environment Agency warning. `--as-of` only selects runs already
generated **and stored** at that time and not yet expired. The archived
forecast contains predictions for future target times; it does not contain
future actual observations.

The `.venv` is local to this computer and may need to be recreated elsewhere
with Python 3.12. Do not copy source `.env` secrets into this project.

