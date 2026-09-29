"""Focused D3 checks using only disposable, tiny SQLite fixtures."""

from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from app.archive import DataIntegrityError, ForecastNotFound
from app.replay import UnsupportedQuestion, answer_question, render_card


RUN_ID = "d" * 64


class ReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.forecast_db = Path(self.temp.name) / "prediction.sqlite3"
        self.sonar_db = Path(self.temp.name) / "sonar.sqlite3"
        with closing(sqlite3.connect(self.forecast_db)) as connection:
            connection.executescript(
                """CREATE TABLE forecast_runs (
                     run_id TEXT PRIMARY KEY, forecast_generated_utc TEXT,
                     history_last_utc TEXT, stored_at_utc TEXT,
                     valid_until_utc TEXT, site TEXT, source TEXT,
                     risk_level INTEGER, risk_label TEXT, max_predicted_m REAL,
                     eta_minutes INTEGER, next_flood_utc TEXT,
                     data_quality_json TEXT);
                   CREATE TABLE forecast_points (
                     run_id TEXT, target_utc TEXT, lead_minutes INTEGER,
                     predicted_water_m REAL, flood_probability REAL);"""
            )
            connection.execute(
                "INSERT INTO forecast_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    RUN_ID, "2026-08-03T01:00:00Z", "2026-08-03T00:55:00Z",
                    "2026-08-03T01:00:02Z", "2026-08-03T01:30:00Z",
                    "house_mill", "fixture", 0, "No risk", 3.5, None, None,
                    json.dumps({"sonar_latest_utc": "2026-08-03T00:54:00Z"}),
                ),
            )
            connection.executemany(
                "INSERT INTO forecast_points VALUES (?,?,?,?,?)",
                [
                    (RUN_ID, "2026-08-03T01:15:00Z", 15, 3.2, None),
                    (RUN_ID, "2026-08-03T01:30:00Z", 30, 3.5, None),
                ],
            )
            connection.commit()
        with closing(sqlite3.connect(self.sonar_db)) as connection:
            connection.execute(
                "CREATE TABLE sonar_readings (date_utc TEXT, stored_at_utc TEXT, internal_water_m REAL)"
            )
            connection.executemany(
                "INSERT INTO sonar_readings VALUES (?,?,?)",
                [
                    ("2026-08-03T00:50:00Z", "2026-08-03T00:50:01Z", 1.1),
                    # The observation time is old, but it was backfilled after cutoff.
                    ("2026-08-03T00:59:00Z", "2026-08-03T01:06:00Z", 9.9),
                    ("2026-08-03T01:10:00Z", "2026-08-03T01:10:01Z", 8.8),
                ],
            )
            connection.commit()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_card_contains_peak_time_id_risk_and_replay_marker(self) -> None:
        card = answer_question(
            "当时预测峰值是多少", "2026-08-03T01:05:00Z",
            self.forecast_db, self.sonar_db,
        )
        shown = render_card(card)
        for expected in ("3.5000 m", "2026-08-03T01:30:00Z", RUN_ID, "No risk", "历史回放"):
            self.assertIn(expected, shown)
        self.assertEqual(card.mode, "historical_replay")

    def test_as_of_blocks_future_or_late_stored_observations(self) -> None:
        card = answer_question(
            "当时预测峰值是多少？", "2026-08-03T01:05:00Z",
            self.forecast_db, self.sonar_db,
        )
        self.assertEqual(card.latest_observation.water_m, 1.1)
        self.assertNotIn("9.900", render_card(card))
        self.assertNotIn("8.800", render_card(card))
        with self.assertRaises(ForecastNotFound):
            answer_question(
                "当时预测峰值是多少", "2026-08-03T01:00:01Z",
                self.forecast_db, self.sonar_db,
            )

    def test_rejects_future_quality_metadata_and_unsupported_question(self) -> None:
        with closing(sqlite3.connect(self.forecast_db)) as connection:
            connection.execute(
                "UPDATE forecast_runs SET data_quality_json = ? WHERE run_id = ?",
                (json.dumps({"sonar_latest_utc": "2026-08-03T01:07:00Z"}), RUN_ID),
            )
            connection.commit()
        with self.assertRaises(DataIntegrityError):
            answer_question(
                "当时预测峰值是多少", "2026-08-03T01:05:00Z",
                self.forecast_db, self.sonar_db,
            )
        with self.assertRaises(UnsupportedQuestion):
            answer_question("明天会不会下雨", "2026-08-03T01:05:00Z", self.forecast_db, self.sonar_db)


if __name__ == "__main__":
    unittest.main()
