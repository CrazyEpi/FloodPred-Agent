"""Focused D2 checks against a tiny disposable SQLite fixture."""

from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from app.archive import ForecastNotFound, get_archived_forecast, get_forecast_as_of


RUN_A = "a" * 64
RUN_B = "b" * 64


class ArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.db = Path(self.temp.name) / "fixture.sqlite3"
        with closing(sqlite3.connect(self.db)) as connection:
            connection.executescript(
                """CREATE TABLE forecast_runs (
                     run_id TEXT PRIMARY KEY,
                     forecast_generated_utc TEXT,
                     history_last_utc TEXT,
                     stored_at_utc TEXT,
                     valid_until_utc TEXT,
                     site TEXT,
                     source TEXT,
                     risk_level INTEGER,
                     risk_label TEXT,
                     max_predicted_m REAL,
                     eta_minutes INTEGER,
                     next_flood_utc TEXT,
                     data_quality_json TEXT
                   );
                   CREATE TABLE forecast_points (
                     run_id TEXT,
                     target_utc TEXT,
                     lead_minutes INTEGER,
                     predicted_water_m REAL,
                     flood_probability REAL
                   );"""
            )
            for run_id, generated, stored, expires, peak in (
                (RUN_A, "2026-08-03T01:00:00Z", "2026-08-03T01:00:01Z", "2026-08-03T01:30:00Z", 3.0),
                (RUN_B, "2026-08-03T01:20:00Z", "2026-08-03T01:20:05Z", "2026-08-03T01:50:00Z", 3.5),
            ):
                connection.execute(
                    "INSERT INTO forecast_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        run_id, generated, "2026-08-03T01:00:00Z", stored,
                        expires, "house_mill", "test", 0, "No risk", peak,
                        None, None, json.dumps({"status": "data_available"}),
                    ),
                )
                connection.execute(
                    "INSERT INTO forecast_points VALUES (?,?,?,?,?)",
                    (run_id, "2026-08-03T01:45:00Z", 15, peak, None),
                )
            connection.commit()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_exact_run_returns_archived_values(self) -> None:
        result = get_archived_forecast(RUN_A, self.db)
        self.assertEqual(result["run_id"], RUN_A)
        self.assertEqual(result["archived_peak_m"], 3.0)
        self.assertEqual(result["point_count"], 1)
        self.assertEqual(result["official_warning_status"], "not_checked")

    def test_as_of_does_not_use_run_before_it_was_stored(self) -> None:
        result = get_forecast_as_of("2026-08-03T01:20:03Z", self.db)
        self.assertEqual(result["run_id"], RUN_A)
        result = get_forecast_as_of("2026-08-03T01:20:06Z", self.db)
        self.assertEqual(result["run_id"], RUN_B)

    def test_as_of_rejects_expired_or_missing_forecast(self) -> None:
        with self.assertRaises(ForecastNotFound):
            get_forecast_as_of("2026-08-03T01:51:00Z", self.db)
        with self.assertRaises(ForecastNotFound):
            get_archived_forecast("c" * 64, self.db)

    def test_rejects_naive_time_and_invalid_run_id(self) -> None:
        with self.assertRaises(ValueError):
            get_forecast_as_of("2026-08-03T01:20:00", self.db)
        with self.assertRaises(ValueError):
            get_archived_forecast("not-a-run-id", self.db)


if __name__ == "__main__":
    unittest.main()
