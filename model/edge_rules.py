"""
model/edge_rules.py

SkyGuard AI — Edge-Deployable Rule Subset (Level 1 Inference Engine).

This module implements the lightweight deterministic validation rules that:
  1. Require NO machine learning (no sklearn, no pandas, no numpy, no scipy)
  2. Require NO network connectivity or cross-station state
  3. Operate on scalar float readings (temperature, pressure, humidity)
  4. Are 100% portable to resource-constrained microcontrollers (ESP32, RP2040)

Level 1 Edge rules evaluate three unambiguous physical and transducer fault conditions:
  - dropout: Missing/None/NaN readings on any sensor channel
  - sensor_fail_low: Hardware rail collapse / dead transducer floor values
  - physical_bounds: Impossible meteorological values exceeding physical extremes

Standardized Output:
  `run_edge_inference(...)` returns a canonical `EdgeInference` object conforming
  to `model/contracts.py` with deterministic status, anomaly flags, and rule metadata.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

# Safe local path resolution to import contracts without heavy packages
sys.path.append(str(Path(__file__).parent.parent))
from model.contracts import EdgeInference

# ─────────────────────────────────────────────────────────────────────
# Configuration & Threshold Constants
# Kept in numeric synchronization with config.py without importing heavy deps.
# ─────────────────────────────────────────────────────────────────────

EDGE_MODEL_VERSION = "edge_rules_v1.0.0"
EDGE_INFERENCE_METHOD = "rules"

# Physical plausibility bounds (same as config.py PHYSICAL_BOUNDS)
TEMP_PHYSICAL_MIN = -50.0      # °C — below any surface station record
TEMP_PHYSICAL_MAX = 60.0       # °C — above any recorded surface temp
PRESSURE_PHYSICAL_MIN = 870.0  # hPa — below strongest hurricane center
PRESSURE_PHYSICAL_MAX = 1085.0 # hPa — above any recorded surface pressure
HUMIDITY_PHYSICAL_MIN = 0.0    # %
HUMIDITY_PHYSICAL_MAX = 100.0  # %

# Sensor fail-low hardware rails (same as config.py FAIL_LOW_FLOOR)
TEMP_FAIL_LOW = -8.0      # °C — genuinely below realistic Indian station range
PRESSURE_FAIL_LOW = 150.0 # hPa — rail-floor value for a broken pressure transducer
HUMIDITY_FAIL_LOW = 3.0   # %   — near-zero, unreachable under normal conditions


class EdgeVerdict:
    """
    Legacy lightweight result container for a single edge-rule evaluation.
    Preserved for backward compatibility with existing standalone callers.
    """
    __slots__ = ("flag", "fault_type", "affected", "reason")

    def __init__(self, flag: bool, fault_type: str, affected: list[str], reason: str):
        self.flag = flag              # True = anomaly flagged by edge rules
        self.fault_type = fault_type  # "physical_bounds", "sensor_fail_low", "dropout", or ""
        self.affected = affected      # list of affected parameter names
        self.reason = reason          # human-readable reason string

    def to_edge_inference(self, model_version: str = EDGE_MODEL_VERSION) -> EdgeInference:
        """Convert EdgeVerdict into the canonical Pydantic EdgeInference model."""
        if self.flag:
            return EdgeInference(
                status="anomaly_detected",
                anomaly_flag=True,
                anomaly_type=self.fault_type if self.fault_type else None,
                score=None,
                score_type=None,
                model_version=model_version,
                inference_method=EDGE_INFERENCE_METHOD,
            )
        return EdgeInference(
            status="ok",
            anomaly_flag=False,
            anomaly_type=None,
            score=None,
            score_type=None,
            model_version=model_version,
            inference_method=EDGE_INFERENCE_METHOD,
        )

    def __repr__(self) -> str:
        return (
            f"EdgeVerdict(flag={self.flag}, fault_type={self.fault_type!r}, "
            f"affected={self.affected!r}, reason={self.reason!r})"
        )


def evaluate_edge_rules(
    temp_c: float | None,
    pressure_hpa: float | None,
    humidity_pct: float | None,
) -> tuple[bool, str | None, list[str], str]:
    """
    Pure deterministic rule evaluation core (ESP32 C/C++ equivalent logic).
    
    Returns:
        (anomaly_flag, anomaly_type, affected_parameters, reason_string)
    """
    affected: list[str] = []
    reasons: list[str] = []

    # ── Rule 1: Dropout check ──────────────────────────────────────────
    # Any None or NaN (x != x) is a sensor communication failure or total dropout.
    if temp_c is None or temp_c != temp_c:
        affected.append("temperature_c")
        reasons.append("temperature missing/NaN")
    if pressure_hpa is None or pressure_hpa != pressure_hpa:
        affected.append("pressure_hpa")
        reasons.append("pressure missing/NaN")
    if humidity_pct is None or humidity_pct != humidity_pct:
        affected.append("humidity_pct")
        reasons.append("humidity missing/NaN")

    if affected:
        return True, "dropout", affected, "; ".join(reasons)

    # All values are valid non-None floats beyond this point.

    # ── Rule 2: Sensor fail-low check ──────────────────────────────────
    # Transducer electrical rail failure to ground / ADC floor.
    fail_low_affected: list[str] = []
    fail_low_reasons: list[str] = []

    if temp_c <= TEMP_FAIL_LOW:
        fail_low_affected.append("temperature_c")
        fail_low_reasons.append(f"temp {temp_c:.1f}°C <= fail-low floor {TEMP_FAIL_LOW}°C")
    if pressure_hpa <= PRESSURE_FAIL_LOW:
        fail_low_affected.append("pressure_hpa")
        fail_low_reasons.append(f"pressure {pressure_hpa:.1f} hPa <= fail-low floor {PRESSURE_FAIL_LOW} hPa")
    if humidity_pct <= HUMIDITY_FAIL_LOW:
        fail_low_affected.append("humidity_pct")
        fail_low_reasons.append(f"humidity {humidity_pct:.1f}% <= fail-low floor {HUMIDITY_FAIL_LOW}%")

    if fail_low_affected:
        return True, "sensor_fail_low", fail_low_affected, "; ".join(fail_low_reasons)

    # ── Rule 3: Physical bounds check ──────────────────────────────────
    # Impossible atmospheric readings exceeding surface extremes.
    pb_affected: list[str] = []
    pb_reasons: list[str] = []

    if not (TEMP_PHYSICAL_MIN <= temp_c <= TEMP_PHYSICAL_MAX):
        pb_affected.append("temperature_c")
        pb_reasons.append(f"temp {temp_c:.1f}°C outside [{TEMP_PHYSICAL_MIN}, {TEMP_PHYSICAL_MAX}]°C")
    if not (PRESSURE_PHYSICAL_MIN <= pressure_hpa <= PRESSURE_PHYSICAL_MAX):
        pb_affected.append("pressure_hpa")
        pb_reasons.append(f"pressure {pressure_hpa:.1f} hPa outside [{PRESSURE_PHYSICAL_MIN}, {PRESSURE_PHYSICAL_MAX}] hPa")
    if not (HUMIDITY_PHYSICAL_MIN <= humidity_pct <= HUMIDITY_PHYSICAL_MAX):
        pb_affected.append("humidity_pct")
        pb_reasons.append(f"humidity {humidity_pct:.1f}% outside [{HUMIDITY_PHYSICAL_MIN}, {HUMIDITY_PHYSICAL_MAX}]%")

    if pb_affected:
        return True, "physical_bounds", pb_affected, "; ".join(pb_reasons)

    # ── All clear ──────────────────────────────────────────────────────
    return False, None, [], "Reading within physical bounds — forward to server for full analysis."


def run_edge_inference(
    temp_c: float | None,
    pressure_hpa: float | None,
    humidity_pct: float | None,
    model_version: str = EDGE_MODEL_VERSION,
) -> EdgeInference:
    """
    Standardized Edge Inference Interface.
    
    Executes lightweight Level 1 deterministic rule evaluation and returns
    a canonical EdgeInference model matching model/contracts.py.
    """
    flag, fault_type, _affected, _reason = evaluate_edge_rules(
        temp_c=temp_c,
        pressure_hpa=pressure_hpa,
        humidity_pct=humidity_pct,
    )

    if flag:
        return EdgeInference(
            status="anomaly_detected",
            anomaly_flag=True,
            anomaly_type=fault_type,
            score=None,
            score_type=None,
            model_version=model_version,
            inference_method=EDGE_INFERENCE_METHOD,
        )

    return EdgeInference(
        status="ok",
        anomaly_flag=False,
        anomaly_type=None,
        score=None,
        score_type=None,
        model_version=model_version,
        inference_method=EDGE_INFERENCE_METHOD,
    )


def check_reading_edge(
    temp_c: float | None,
    pressure_hpa: float | None,
    humidity_pct: float | None,
) -> EdgeVerdict:
    """
    Legacy edge-side anomaly check interface.
    Returns an EdgeVerdict instance for backward compatibility.
    """
    flag, fault_type, affected, reason = evaluate_edge_rules(
        temp_c=temp_c,
        pressure_hpa=pressure_hpa,
        humidity_pct=humidity_pct,
    )
    return EdgeVerdict(
        flag=flag,
        fault_type=fault_type or "",
        affected=affected,
        reason=reason,
    )


# ─────────────────────────────────────────────────────────────────────
# Self-Test Execution
# ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    tests = [
        # (temp, pressure, humidity, expected_flag, expected_fault_type, label)
        (25.0, 1013.0, 65.0,   False, None,              "Normal reading"),
        (None, 1013.0, 65.0,   True,  "dropout",        "Dropout (temp None)"),
        (25.0, None,   65.0,   True,  "dropout",        "Dropout (pressure None)"),
        (-40.0, 0.0,   0.0,   True,  "sensor_fail_low", "Fail-low (all rail)"),
        (-40.0, 1013.0, 65.0, True,  "sensor_fail_low", "Fail-low (temp rail)"),
        (99.0, 1013.0, 65.0,  True,  "physical_bounds", "Physical bounds (temp > 60)"),
        (25.0, 500.0,  65.0,  True,  "physical_bounds", "Physical bounds (pressure < 870)"),
        (25.0, 1013.0, 105.0, True,  "physical_bounds", "Physical bounds (humidity > 100)"),
    ]

    all_pass = True
    print("Running model/edge_rules.py self-test...")
    for temp, pressure, humidity, exp_flag, exp_fault, label in tests:
        inference = run_edge_inference(temp, pressure, humidity)
        verdict = check_reading_edge(temp, pressure, humidity)
        
        ok_inf = (inference.anomaly_flag == exp_flag) and (inference.anomaly_type == exp_fault)
        ok_erd = (verdict.flag == exp_flag) and ((verdict.fault_type or None) == exp_fault)
        ok = ok_inf and ok_erd
        
        status = "PASS" if ok else f"FAIL (inf={inference.anomaly_type!r}, erd={verdict.fault_type!r})"
        print(f"  {status:6} | {label}")
        all_pass = all_pass and ok

    print()
    print("edge_rules self-test: ALL PASS" if all_pass else "edge_rules self-test: SOME FAILURES")
