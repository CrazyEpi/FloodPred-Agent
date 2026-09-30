"""Build a minimal, reviewable portfolio replay bundle from local backups.

This reads original archives only. The output contains one archived forecast,
one water observation, two evaluation JSON snapshots, and two cited excerpts.
It deliberately excludes sensor identifiers, raw MQTT payloads and secrets.
"""

from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

from .archive import _SOURCE_DB, get_forecast_as_of
from .knowledge import ROOT
from .replay import latest_observation_as_of


AS_OF = "2026-08-03T01:48:00Z"
SOURCE = ROOT / "backup" / "source_snapshot_2026-09-29"
TARGET = ROOT / "demo_data"


def _db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing demo bundle: {path}")
    return sqlite3.connect(path)


def main() -> None:
    if TARGET.exists():
        raise FileExistsError("demo_data already exists; review it instead of silently overwriting")
    forecast = get_forecast_as_of(AS_OF, _SOURCE_DB)
    sonar = latest_observation_as_of(AS_OF, SOURCE / "forecast_evaluation" / "sonar_history.sqlite3")
    if sonar is None:
        raise RuntimeError("No source water observation at selected replay time")
    evaluation = TARGET / "forecast_evaluation"
    evaluation.mkdir(parents=True)
    with closing(_db(evaluation / "prediction_history.sqlite3")) as connection:
        connection.execute("""CREATE TABLE forecast_runs (
            run_id TEXT PRIMARY KEY, forecast_generated_utc TEXT, history_last_utc TEXT,
            stored_at_utc TEXT, valid_until_utc TEXT, site TEXT, source TEXT,
            risk_level INTEGER, risk_label TEXT, max_predicted_m REAL,
            eta_minutes INTEGER, next_flood_utc TEXT, data_quality_json TEXT)""")
        connection.execute("""CREATE TABLE forecast_points (
            run_id TEXT, target_utc TEXT, lead_minutes INTEGER,
            predicted_water_m REAL, flood_probability REAL)""")
        quality = forecast["data_quality"] or {}
        safe_quality = {key: quality[key] for key in ("history_latest_utc", "sonar_latest_utc") if key in quality}
        connection.execute(
            "INSERT INTO forecast_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (forecast["run_id"], forecast["forecast_generated_utc"], forecast["history_last_utc"],
             forecast["stored_at_utc"], forecast["valid_until_utc"], forecast["site"],
             "portfolio_curated_subset", forecast["internal_risk_level"], forecast["internal_risk_label"],
             forecast["archived_peak_m"], forecast["eta_minutes"], forecast["next_flood_utc"],
             json.dumps(safe_quality)),
        )
        connection.executemany(
            "INSERT INTO forecast_points VALUES (?,?,?,?,?)",
            [(forecast["run_id"], point["target_utc"], point["lead_minutes"],
              point["predicted_water_m"], point["flood_probability"]) for point in forecast["points"]],
        )
        connection.commit()
    with closing(_db(evaluation / "sonar_history.sqlite3")) as connection:
        connection.execute("CREATE TABLE sonar_readings (date_utc TEXT, stored_at_utc TEXT, internal_water_m REAL)")
        connection.execute("INSERT INTO sonar_readings VALUES (?,?,?)", (sonar.observed_utc, sonar.stored_at_utc, sonar.water_m))
        connection.commit()
    for relative in (
        "output/summary.json",
        "output_above_2m/high_water_summary.json",
    ):
        destination = evaluation / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / "forecast_evaluation" / relative, destination)

    catalog = json.loads((ROOT / "knowledge" / "catalog.json").read_text(encoding="utf-8"))
    excerpts = {
        "internal_watch": (SOURCE / "cloud_flood_server" / "README.md", "internal_watch.md"),
        "highwater_evaluation_limit": (SOURCE / "forecast_evaluation" / "output_above_2m" / "HIGH_WATER_EVALUATION_REPORT.md", "model_limit.md"),
    }
    for record in catalog["records"]:
        concept = record["concept"]
        if concept in excerpts:
            source, name = excerpts[concept]
            original_lines = source.read_text(encoding="utf-8").splitlines()
            excerpt = "\n".join(original_lines[record["line_start"] - 1:record["line_end"]]) + "\n"
            destination = TARGET / "knowledge" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(excerpt, encoding="utf-8")
            record["source_ref"] = str(destination.relative_to(ROOT)).replace("\\", "/")
            record["line_start"], record["line_end"] = 1, len(excerpt.splitlines())
            record["sha256"] = hashlib.sha256(destination.read_bytes()).hexdigest()
            record["title"] += " (curated excerpt)"
        elif concept == "overall_model_evaluation":
            destination = evaluation / "output" / "summary.json"
            record["source_ref"] = str(destination.relative_to(ROOT)).replace("\\", "/")
        catalog["catalog_version"] = "portfolio-demo-2026-09-30"
    (TARGET / "knowledge" / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (TARGET / "PROVENANCE.md").write_text(
        "# Portfolio demo subset\n\n"
        f"Curated at replay time `{AS_OF}` from the owner's local project snapshot. "
        "Contains one archived forecast with its 96 predicted points, one historical water observation, "
        "two small evaluation summaries, and two cited document excerpts. "
        "Device identifiers, MQTT payloads, credentials and full history are excluded. "
        "This is historical data, not a live forecast or official alert. "
        "Review data rights before publishing this directory publicly.\n",
        encoding="utf-8",
    )
    print(f"Built curated demo bundle in {TARGET}")


if __name__ == "__main__":
    main()
