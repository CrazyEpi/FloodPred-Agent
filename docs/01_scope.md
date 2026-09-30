# D1 scope and acceptance

## Product sentence

FloodPred is a portfolio demonstration that replays what the House Mill
forecast archive contained at a historical decision time, shows evidence and
limitations, and later may answer source-backed knowledge questions.

It is **not** a production forecasting, site-action, or official warning
system. The cloud server is offline; D1-D2 do not restart it.

## In scope for D1-D2

1. Preserve a snapshot of the relevant cloud-server and forecast-evaluation
   assets before new implementation work.
2. Create an isolated Python environment in the new project directory.
3. Inventory source files and SQLite schemas without modifying originals.
4. Retrieve a forecast by exact `run_id` and its 96 archived prediction points.
5. Retrieve the latest forecast *available and still valid* at an `as_of_utc`
   replay time. Both generation and archive storage times must be no later than
   the replay time.
6. Return clear errors for missing runs or gaps; never invent a forecast.

## Explicitly out of scope

- Retraining PatchTST or invoking it on new live data.
- Restoring DigitalOcean, MQTT ingestion, alerts, or device control.
- Reading future actual water levels into a historical decision.
- RAG, LLM, FastAPI, Streamlit, and LangGraph implementation (later days).
- Claiming validated flood-event detection or issuing site-specific action
  instructions.

## Answer modes to keep separate later

- **Historical forecast**: what a particular archived run predicted then.
- **Retrospective evaluation**: actual observations and error, viewed only
  after the target time and never fed back into the original decision.
- **Knowledge explanation**: project rules or externally sourced guidance,
  with provenance and version.

## D2 acceptance check

For `2026-08-03T01:48:00Z`, the read-only archive returns run
`ce9a940aa8807b10c85bc20754911bafdc8eca122053e6edc7273edd23efbb38`,
generated at `01:47:59.737191Z`, stored at `01:47:59.826157Z`, with 96
forecast points and an archived peak of `3.783 m`. This is an internal
`No risk` classification, not an official warning. Earlier times must not
select this run before it was stored.

