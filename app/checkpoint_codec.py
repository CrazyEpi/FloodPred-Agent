"""Versioned, closed-type JSON checkpoint encoding. No pickle or dynamic import."""

from __future__ import annotations

import base64
from dataclasses import fields, is_dataclass
from datetime import datetime
import json
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel

from .evidence_gate import Citation, QuestionEvidence, VerifiedClaim
from .investigation_state import BudgetSnapshot, InvestigationRequest
from .knowledge import Hit
from .question_graph import Coreference, QuestionGraph, QuestionItem, SourceSpan
from .read_tools import EvaluationMetric
from .replay import Observation, ReplayCard
from .router import KnowledgeItem, QueryResult, RoutePlan
from .web_policy import NetworkHint


_TYPES = {cls.__name__: cls for cls in (
    Citation, QuestionEvidence, VerifiedClaim, BudgetSnapshot, InvestigationRequest,
    Hit, Coreference, QuestionGraph, QuestionItem, SourceSpan, EvaluationMetric,
    Observation, ReplayCard, KnowledgeItem, QueryResult, RoutePlan, NetworkHint,
)}


def encode(value):
    if value is None or type(value) in (str, bool, int, float):
        return value
    if type(value) in _TYPES.values():
        data = value.model_dump(mode="python") if isinstance(value, BaseModel) else {
            field.name: getattr(value, field.name) for field in fields(value)
        }
        if isinstance(value, QueryResult):
            data["reasoning_traces"] = []  # Ephemeral model diagnostics stay out of storage.
        return {"type": type(value).__name__, "value": encode(data)}
    if isinstance(value, dict):
        if any(type(key) is not str for key in value):
            raise ValueError("Checkpoint dictionaries require string keys")
        return {"type": "dict", "value": [[key, encode(item)] for key, item in value.items()]}
    if type(value) in (list, tuple, frozenset, set):
        return {"type": type(value).__name__, "value": [encode(item) for item in value]}
    if isinstance(value, Path):
        return {"type": "path", "value": str(value)}
    if isinstance(value, UUID):
        return {"type": "uuid", "value": str(value)}
    if isinstance(value, datetime):
        return {"type": "datetime", "value": value.isoformat()}
    if isinstance(value, bytes):
        return {"type": "bytes", "value": base64.b64encode(value).decode("ascii")}
    raise ValueError(f"Unsupported checkpoint type: {type(value).__name__}")


def decode(value):
    if value is None or type(value) in (str, bool, int, float):
        return value
    if not isinstance(value, dict) or set(value) != {"type", "value"}:
        raise ValueError("Invalid checkpoint value")
    kind, data = value["type"], value["value"]
    if kind in _TYPES:
        cls = _TYPES[kind]
        data = decode(data)
        expected = set(cls.model_fields) if issubclass(cls, BaseModel) else {f.name for f in fields(cls)}
        if not isinstance(data, dict) or set(data) != expected:
            raise ValueError(f"Incompatible checkpoint fields: {kind}")
        return cls.model_validate(data) if issubclass(cls, BaseModel) else cls(**data)
    if kind == "dict":
        if not isinstance(data, list) or any(not isinstance(pair, list) or len(pair) != 2 or type(pair[0]) is not str for pair in data):
            raise ValueError("Invalid checkpoint dictionary")
        if len({pair[0] for pair in data}) != len(data):
            raise ValueError("Duplicate checkpoint dictionary key")
        return {key: decode(item) for key, item in data}
    if kind in ("list", "tuple", "frozenset", "set"):
        if not isinstance(data, list):
            raise ValueError("Invalid checkpoint sequence")
        return {"list": list, "tuple": tuple, "frozenset": frozenset, "set": set}[kind](decode(item) for item in data)
    if kind == "path":
        return Path(data)
    if kind == "uuid":
        return UUID(data)
    if kind == "datetime":
        return datetime.fromisoformat(data)
    if kind == "bytes":
        return base64.b64decode(data, validate=True)
    raise ValueError("Unknown checkpoint type tag")


class CheckpointCodec:
    tag = "floodpred-json-v1"

    def dumps_typed(self, obj):
        return self.tag, json.dumps(encode(obj), ensure_ascii=False, allow_nan=False).encode("utf-8")

    def loads_typed(self, data):
        kind, payload = data
        if kind != self.tag:
            raise ValueError("Unsupported checkpoint schema version")
        return decode(json.loads(payload))

    def dumps(self, obj) -> str:
        return self.dumps_typed(obj)[1].decode("utf-8")

    def loads(self, payload: str):
        return self.loads_typed((self.tag, payload.encode("utf-8")))
