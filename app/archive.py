"""Read archived forecasts without modifying the original project or backup.

This module intentionally does not call the live predictor, MQTT, or an LLM.
All timestamps are timezone-aware UTC. ``as_of`` selection checks both when a
forecast was generated and when it was stored, and rejects expired forecasts.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = (
    PROJECT_ROOT
    / "backup"
    / "source_snapshot_2026-09-29"
    / "forecast_evaluation"
    / "prediction_history.sqlite3"
)
RUN_ID_RE = re.compile(r"^[a-fA-F0-9]{64}$")


class ArchiveError(Exception):
    """Base error for an unusable archive request."""


class ForecastNotFound(ArchiveError):
    """No matching forecast exists in the selected archive."""


class DataIntegrityError(ArchiveError):
    """Archived metadata and forecast points are inconsistent."""


def parse_utc(value: str) -> datetime:
    """Parse an ISO 8601 timestamp; reject naive datetimes."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid ISO 8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include a timezone, such as Z or +00:00")
    return parsed.astimezone(timezone.utc)


def _connect_read_only(db_path: Path) -> sqlite3.Connection:
    db_path = db_path.resolve(strict=True)
    if not db_path.is_file():
        raise ArchiveError(f"Not a database file: {db_path}")
    # immutable prevents SQLite from creating journal or shared-memory files.
    connection = sqlite3.connect(
        db_path.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=5
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _load_forecast(connection: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    row = connection.execute(
        """SELECT run_id, forecast_generated_utc, history_last_utc,
                  stored_at_utc, valid_until_utc, site, source, risk_level,
                  risk_label, max_predicted_m, eta_minutes, next_flood_utc,
                  data_quality_json
           FROM forecast_runs WHERE run_id = ?""",
        (run_id,),
    ).fetchone()
    if row is None:
        raise ForecastNotFound(f"No archived forecast for run_id={run_id}")

    points = [
        dict(point)
        for point in connection.execute(
            """SELECT target_utc, lead_minutes, predicted_water_m,
                      flood_probability
               FROM forecast_points WHERE run_id = ? ORDER BY target_utc""",
            (run_id,),
        )
    ]
    if not points:
        raise DataIntegrityError(f"Forecast {run_id} has no archived points")
    peak_from_points = max(point["predicted_water_m"] for point in points)
    archived_peak = row["max_predicted_m"]
    if archived_peak is not None and abs(archived_peak - peak_from_points) > 0.01:
        raise DataIntegrityError(
            f"Forecast {run_id} peak differs from points by more than 0.01 m"
        )

    quality = json.loads(row["data_quality_json"]) if row["data_quality_json"] else None
    return {
        "mode": "historical_replay",
        "run_id": row["run_id"],
        "forecast_generated_utc": row["forecast_generated_utc"],
        "stored_at_utc": row["stored_at_utc"],
        "history_last_utc": row["history_last_utc"],
        "valid_until_utc": row["valid_until_utc"],
        "site": row["site"],
        "model_source": row["source"],
        "internal_risk_level": row["risk_level"],
        "internal_risk_label": row["risk_label"],
        "archived_peak_m": archived_peak,
        "point_peak_m": peak_from_points,
        "eta_minutes": row["eta_minutes"],
        "next_flood_utc": row["next_flood_utc"],
        "data_quality": quality,
        "point_count": len(points),
        "points": points,
        "official_warning_status": "not_checked",
        "disclaimer": "Internal project classification; not an official flood warning.",
    }


def get_archived_forecast(
    run_id: str, db_path: Path = DEFAULT_DB
) -> dict[str, Any]:
    """Return one exact archived forecast and its 15-minute prediction points."""
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("run_id must be a 64-character hexadecimal string")
    with closing(_connect_read_only(db_path)) as connection:
        return _load_forecast(connection, run_id.lower())


def get_forecast_as_of(
    as_of_utc: str, db_path: Path = DEFAULT_DB, site: str = "house_mill"
) -> dict[str, Any]:
    """Find the newest forecast genuinely available and valid at ``as_of_utc``.

    Python compares parsed timestamps instead of SQL text ordering because
    archived strings mix second and fractional-second precision.
    """
    cutoff = parse_utc(as_of_utc)
    with closing(_connect_read_only(db_path)) as connection:
        candidates = connection.execute(
            """SELECT run_id, forecast_generated_utc, stored_at_utc,
                      valid_until_utc
               FROM forecast_runs WHERE site = ?""",
            (site,),
        ).fetchall()
        valid = []
        for row in candidates:
            generated = parse_utc(row["forecast_generated_utc"])
            stored = parse_utc(row["stored_at_utc"])
            expires = parse_utc(row["valid_until_utc"])
            if generated <= cutoff and stored <= cutoff <= expires:
                valid.append((generated, stored, row["run_id"]))
        if not valid:
            raise ForecastNotFound(
                f"No forecast for {site} was stored and valid at {cutoff.isoformat()}"
            )
        selected_id = max(valid)[2]
        result = _load_forecast(connection, selected_id)
        result["as_of_utc"] = cutoff.isoformat().replace("+00:00", "Z")
        return result
