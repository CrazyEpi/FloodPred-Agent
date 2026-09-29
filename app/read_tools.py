"""D6: three explicit, read-only tools with validated input contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from .archive import DEFAULT_DB, DataIntegrityError, get_forecast_as_of, parse_utc
from .replay import DEFAULT_SONAR_DB, Observation, ReplayCard, _check_available_time, latest_observation_as_of


EVALUATION_ROOT = DEFAULT_DB.parent
OVERALL_SUMMARY = EVALUATION_ROOT / "output" / "summary.json"
HIGH_WATER_SUMMARY = EVALUATION_ROOT / "output_above_2m" / "high_water_summary.json"
EXPECTED_SHA256 = {
    "overall": "45c11dfeeb39b3a44d86589fcecd2c54f65d8741846636a89182a587a666cff1",
    "high_water_2m": "f5bc8dd212397928fb74543bf309fc1e20d18bd9ade8163dde0aa54afad08082",
}


class TimeArgs(BaseModel):
    model_config = ConfigDict(frozen=True)
    as_of_utc: str

    @field_validator("as_of_utc")
    @classmethod
    def validate_as_of(cls, value: str) -> str:
        return parse_utc(value).isoformat().replace("+00:00", "Z")


class ForecastArgs(TimeArgs):
    site: Literal["house_mill"] = "house_mill"


class WaterArgs(TimeArgs):
    pass


class EvaluationArgs(BaseModel):
    model_config = ConfigDict(frozen=True)
    scope: Literal["overall", "high_water_2m"] = "overall"


class EvaluationMetric(BaseModel):
    model_config = ConfigDict(frozen=True)
    scope: Literal["overall", "high_water_2m"]
    mae_m: float
    unit: Literal["m"] = "m"
    generated_utc: str
    matched_prediction_points: int
    sample_scope: str
    method: str
    target_start_utc: str | None = None
    target_end_utc: str | None = None
    source_locator: str
    source_sha256: str
    hindsight_only: Literal[True] = True


def archived_forecast_tool(args: ForecastArgs, db_path: Path = DEFAULT_DB) -> ReplayCard:
    """Select only an archived run available at the replay cutoff."""
    forecast = get_forecast_as_of(args.as_of_utc, db_path=db_path, site=args.site)
    cutoff = parse_utc(args.as_of_utc)
    for field in ("forecast_generated_utc", "stored_at_utc", "history_last_utc"):
        _check_available_time(forecast[field], cutoff, field)
    quality = forecast["data_quality"] or {}
    if not isinstance(quality, dict):
        raise DataIntegrityError("data_quality is not an object")
    for field in ("history_latest_utc", "sonar_latest_utc"):
        _check_available_time(quality.get(field), cutoff, f"data_quality.{field}")
    peak = max(forecast["points"], key=lambda point: point["predicted_water_m"])
    return ReplayCard(
        mode="historical_replay",
        as_of_utc=args.as_of_utc,
        run_id=forecast["run_id"],
        forecast_generated_utc=forecast["forecast_generated_utc"],
        forecast_stored_at_utc=forecast["stored_at_utc"],
        predicted_peak_m=peak["predicted_water_m"],
        predicted_peak_utc=peak["target_utc"],
        internal_risk_level=forecast["internal_risk_level"],
        internal_risk_label=forecast["internal_risk_label"] or "Unknown",
        latest_observation=None,
    )


def historical_water_tool(args: WaterArgs, db_path: Path = DEFAULT_SONAR_DB) -> Observation | None:
    """Return latest reading both observed and archived by as_of_utc."""
    return latest_observation_as_of(args.as_of_utc, db_path)


def evaluation_metrics_tool(
    args: EvaluationArgs,
    overall_path: Path = OVERALL_SUMMARY,
    high_water_path: Path = HIGH_WATER_SUMMARY,
) -> EvaluationMetric:
    """Read exact MAE from a versioned JSON snapshot, never from similar prose."""
    path = overall_path if args.scope == "overall" else high_water_path
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_SHA256[args.scope]:
        raise DataIntegrityError(f"Evaluation snapshot changed: {path}")
    data = json.loads(raw)
    metrics = data["point_metrics"]
    if args.scope == "overall":
        scope = "本评估快照中已成熟且匹配的预测目标点；不是相互独立的洪水事件，也未限定为达到 Watch 的事件。"
        locator = f"{path.resolve()}#point_metrics.mae_m"
    else:
        if data["filter"] != "actual_water_m >= threshold_m" or data["threshold_m"] != 2.0:
            raise DataIntegrityError("High-water evaluation filter does not match 2.0 m scope")
        scope = "实测水位 ≥2.00 m 的已匹配预测目标点；同一目标时刻可能被不同提前量重复评分，且不代表 Watch 事件。"
        locator = f"{path.resolve()}#point_metrics.mae_m"
    if metrics["matched_points"] != metrics["matured_points"] or metrics["matched_points"] <= 0:
        raise DataIntegrityError("Evaluation counts are inconsistent")
    return EvaluationMetric(
        scope=args.scope,
        mae_m=metrics["mae_m"],
        generated_utc=data["generated_utc"],
        matched_prediction_points=metrics["matched_points"],
        sample_scope=scope,
        method=f"声纳实测按15分钟窗口 {data['actual_resample_method']} 重采样；MAE为点级平均绝对误差。",
        target_start_utc=data.get("target_start_utc"),
        target_end_utc=data.get("target_end_utc"),
        source_locator=locator,
        source_sha256=digest,
    )
