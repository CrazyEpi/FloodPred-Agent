"""D3: a deterministic, no-LLM historical replay question-answer slice."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .archive import (
    DEFAULT_DB,
    DataIntegrityError,
    get_forecast_as_of,
    parse_utc,
)


DEFAULT_SONAR_DB = (
    DEFAULT_DB.parent / "sonar_history.sqlite3"
)


class UnsupportedQuestion(ValueError):
    """The deliberately narrow D3 question router has no matching intent."""


class Observation(BaseModel):
    model_config = ConfigDict(frozen=True)

    observed_utc: str
    stored_at_utc: str
    water_m: float


class ReplayCard(BaseModel):
    """Typed boundary between archive access and the user-facing answer."""

    model_config = ConfigDict(frozen=True)

    mode: Literal["historical_replay"]
    as_of_utc: str
    run_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    forecast_generated_utc: str
    forecast_stored_at_utc: str
    predicted_peak_m: float
    predicted_peak_utc: str
    internal_risk_level: int | None
    internal_risk_label: str
    latest_observation: Observation | None
    official_warning_status: Literal["not_checked"] = "not_checked"


def _check_available_time(value: str | None, cutoff: datetime, name: str) -> None:
    if value and parse_utc(value) > cutoff:
        raise DataIntegrityError(f"{name} is later than as_of_utc; replay refused")


def latest_observation_as_of(
    as_of_utc: str, db_path: Path = DEFAULT_SONAR_DB
) -> Observation | None:
    """Only show readings both observed AND stored by the requested time."""
    cutoff = parse_utc(as_of_utc)
    path = db_path.resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"Not a database file: {path}")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            "SELECT date_utc, stored_at_utc, internal_water_m FROM sonar_readings"
        )
        visible = (
            row for row in rows
            if parse_utc(row[0]) <= cutoff and parse_utc(row[1]) <= cutoff
        )
        latest = max(visible, key=lambda row: parse_utc(row[0]), default=None)
    if latest is None:
        return None
    return Observation(
        observed_utc=latest[0], stored_at_utc=latest[1], water_m=latest[2]
    )


def answer_question(
    question: str,
    as_of_utc: str,
    forecast_db: Path = DEFAULT_DB,
    sonar_db: Path = DEFAULT_SONAR_DB,
) -> ReplayCard:
    """Answer the D3 peak question from archived evidence, without an LLM."""
    normalized = "".join(question.split()).rstrip("？?")
    if normalized not in {"当时预测峰值是多少", "当时预测峰值多少"}:
        raise UnsupportedQuestion("D3 仅支持提问：当时预测峰值是多少")

    cutoff = parse_utc(as_of_utc)
    forecast = get_forecast_as_of(as_of_utc, forecast_db)
    _check_available_time(forecast["forecast_generated_utc"], cutoff, "forecast_generated_utc")
    _check_available_time(forecast["stored_at_utc"], cutoff, "stored_at_utc")
    _check_available_time(forecast["history_last_utc"], cutoff, "history_last_utc")
    quality = forecast["data_quality"] or {}
    if not isinstance(quality, dict):
        raise DataIntegrityError("data_quality is not an object")
    for field in ("history_latest_utc", "sonar_latest_utc"):
        _check_available_time(quality.get(field), cutoff, f"data_quality.{field}")

    # A future target time is legitimate: it is a prediction, not an observation.
    peak = max(forecast["points"], key=lambda point: point["predicted_water_m"])
    observation = latest_observation_as_of(as_of_utc, sonar_db)
    return ReplayCard(
        mode="historical_replay",
        as_of_utc=cutoff.isoformat().replace("+00:00", "Z"),
        run_id=forecast["run_id"],
        forecast_generated_utc=forecast["forecast_generated_utc"],
        forecast_stored_at_utc=forecast["stored_at_utc"],
        predicted_peak_m=peak["predicted_water_m"],
        predicted_peak_utc=peak["target_utc"],
        internal_risk_level=forecast["internal_risk_level"],
        internal_risk_label=forecast["internal_risk_label"] or "Unknown",
        latest_observation=observation,
    )


def render_card(card: ReplayCard) -> str:
    observed = (
        f"{card.latest_observation.water_m:.3f} m"
        f"（观测 {card.latest_observation.observed_utc}；入库 {card.latest_observation.stored_at_utc}）"
        if card.latest_observation else "当时无已入库观测"
    )
    return "\n".join(
        [
            "洪水预测回放卡｜历史回放（非实时、非官方预警）",
            f"回放时间 as_of_utc：{card.as_of_utc}",
            f"当时预测峰值：{card.predicted_peak_m:.4f} m",
            f"预测峰值时间：{card.predicted_peak_utc}",
            f"run_id：{card.run_id}",
            f"内部等级：{card.internal_risk_label}（level {card.internal_risk_level}）",
            f"预测生成：{card.forecast_generated_utc}；归档入库：{card.forecast_stored_at_utc}",
            f"当时最新可见水位：{observed}",
            "说明：峰值时间可晚于回放时间，因为它是当时已生成的未来预测；"
            "内部等级不等于官方警报。",
        ]
    )
