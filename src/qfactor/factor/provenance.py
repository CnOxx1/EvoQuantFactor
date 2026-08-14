from __future__ import annotations

import hashlib
import json
from typing import Any

from qfactor.factor.base import FactorSpec


def definition_payload(spec: FactorSpec | dict[str, Any]) -> dict[str, Any]:
    raw = spec.model_dump() if isinstance(spec, FactorSpec) else dict(spec)
    # Only fields that change economic definition, implementation, or required
    # data are part of the identity. Operational library labels are deliberately
    # excluded so a demotion does not invalidate an already frozen definition.
    return {
        "expression": raw.get("expression"),
        "params": raw.get("params") or {},
        "mechanism": raw.get("mechanism"),
        "family": raw.get("family"),
        "category": raw.get("category"),
        "universe": raw.get("universe"),
        "frequency": raw.get("frequency"),
        "lookback": raw.get("lookback"),
        "horizon": raw.get("horizon"),
        "signal_hold_days": raw.get("signal_hold_days"),
        "required_fields": raw.get("required_fields") or [],
        "neutralize": raw.get("neutralize") or [],
    }


def definition_hash(spec: FactorSpec | dict[str, Any]) -> str:
    payload = definition_payload(spec)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
