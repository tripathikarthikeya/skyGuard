"""
model/edge_central_comparison.py

SkyGuard AI — Level 1 Edge Inference vs. Level 2 Central AI Comparison Layer.

Provides deterministic, side-effect-free diagnostic evaluation comparing
lightweight edge verdicts against central machine learning and rule fusion verdicts.

Architectural Guarantees:
1. Independent Preservation: Edge and central results are evaluated independently
   without overwriting either source.
2. Non-Interference: Central detection remains the independent source of truth for
   central anomaly detection; edge results do not bypass or dictate central scoring.
3. No Arbitrary Decision Policy: This module does NOT impose an arbitrary AND/OR final
   decision policy. It is strictly an analytical and diagnostic consensus evaluator.
4. Raw Data Preservation: Raw sensor readings, timestamps, and metadata are never altered.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional, Union

from model.contracts import EdgeInference, EdgeCentralComparison


class ComparisonStatus(str, Enum):
    """
    Standardized classification statuses for Edge vs. Central verdict consensus.
    """
    BOTH_AGREE_ANOMALY = "BOTH_AGREE_ANOMALY"
    BOTH_AGREE_NORMAL = "BOTH_AGREE_NORMAL"
    EDGE_ONLY_ANOMALY = "EDGE_ONLY_ANOMALY"
    CENTRAL_ONLY_ANOMALY = "CENTRAL_ONLY_ANOMALY"
    EDGE_UNAVAILABLE = "EDGE_UNAVAILABLE"
    CENTRAL_UNAVAILABLE = "CENTRAL_UNAVAILABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


def compare_edge_central(
    edge_inference: Optional[Union[EdgeInference, dict[str, Any]]],
    central_verdict: Optional[dict[str, Any]],
) -> EdgeCentralComparison:
    """
    Compares Level 1 Edge inference against Level 2 Central AI verdict for an observation.

    Args:
        edge_inference: EdgeInference model or dict received with the observation packet,
                        or None if edge inference is missing/unavailable.
        central_verdict: Central detection verdict dict returned by StateManager / detect.py,
                         or None if central inference failed/was skipped.

    Returns:
        EdgeCentralComparison detailing consensus status, boolean agreement,
        fault type alignment, and independently preserved scores.
    """
    # ── 1. Extract Edge Inference Attributes ─────────────────────────────
    edge_flag: Optional[bool] = None
    edge_type: Optional[str] = None
    edge_score: Optional[float] = None
    edge_score_type: Optional[str] = None
    edge_status_str: Optional[str] = None
    edge_usable: bool = False

    if edge_inference is not None:
        if isinstance(edge_inference, EdgeInference):
            edge_flag = edge_inference.anomaly_flag
            edge_type = edge_inference.anomaly_type
            edge_score = edge_inference.score
            edge_score_type = edge_inference.score_type
            edge_status_str = edge_inference.status
        elif isinstance(edge_inference, dict):
            edge_flag = edge_inference.get("anomaly_flag")
            edge_type = edge_inference.get("anomaly_type")
            edge_score = edge_inference.get("score")
            edge_score_type = edge_inference.get("score_type")
            edge_status_str = edge_inference.get("status")

        # Check if edge status indicates valid, usable inference
        if (
            isinstance(edge_flag, bool)
            and edge_status_str not in ("unavailable", "missing", "unknown", "error", None)
        ):
            edge_usable = True

    # ── 2. Extract Central Verdict Attributes ────────────────────────────
    central_flag: Optional[bool] = None
    central_type: Optional[str] = None
    central_score: Optional[float] = None
    central_score_type: Optional[str] = None
    central_usable: bool = False

    if central_verdict is not None and isinstance(central_verdict, dict):
        raw_central_flag = central_verdict.get("is_anomaly")
        if isinstance(raw_central_flag, bool):
            central_flag = raw_central_flag
            central_usable = True
            central_type = central_verdict.get("fault_type")
            central_score = central_verdict.get("anomaly_score_pct")
            if central_score is not None:
                central_score_type = "percentage"

    # ── 3. Handle Availability & Missing Data Conditions ─────────────────
    if not edge_usable and not central_usable:
        return EdgeCentralComparison(
            comparison_status=ComparisonStatus.INSUFFICIENT_EVIDENCE.value,
            edge_anomaly_flag=None,
            central_anomaly_flag=None,
            anomaly_decision_agreement=None,
            edge_anomaly_type=None,
            central_anomaly_type=None,
            type_agreement=None,
            edge_score=edge_score,
            edge_score_type=edge_score_type,
            central_score=central_score,
            central_score_type=central_score_type,
            details="Neither edge inference nor central detection verdict was available to evaluate.",
        )

    if not edge_usable:
        return EdgeCentralComparison(
            comparison_status=ComparisonStatus.EDGE_UNAVAILABLE.value,
            edge_anomaly_flag=None,
            central_anomaly_flag=central_flag,
            anomaly_decision_agreement=None,
            edge_anomaly_type=None,
            central_anomaly_type=central_type,
            type_agreement=None,
            edge_score=edge_score,
            edge_score_type=edge_score_type,
            central_score=central_score,
            central_score_type=central_score_type,
            details="Edge inference was unavailable or incomplete; central verdict evaluated independently.",
        )

    if not central_usable:
        return EdgeCentralComparison(
            comparison_status=ComparisonStatus.CENTRAL_UNAVAILABLE.value,
            edge_anomaly_flag=edge_flag,
            central_anomaly_flag=None,
            anomaly_decision_agreement=None,
            edge_anomaly_type=edge_type,
            central_anomaly_type=None,
            type_agreement=None,
            edge_score=edge_score,
            edge_score_type=edge_score_type,
            central_score=None,
            central_score_type=None,
            details="Central anomaly detection verdict was unavailable; edge inference evaluated independently.",
        )

    # ── 4. Both Usable: Evaluate Consensus and Divergence ────────────────
    # A. BOTH AGREE ANOMALY
    if edge_flag is True and central_flag is True:
        type_agree: Optional[bool] = None
        if edge_type is not None and central_type is not None:
            type_agree = (edge_type.strip().lower() == central_type.strip().lower())
        elif edge_type is not None or central_type is not None:
            type_agree = False

        if type_agree is False:
            details_str = (
                f"Consensus anomaly detected, but fault diagnosis diverges "
                f"(edge='{edge_type}', central='{central_type}')."
            )
        else:
            details_str = "Both edge device and central AI agreed on anomaly detection."

        return EdgeCentralComparison(
            comparison_status=ComparisonStatus.BOTH_AGREE_ANOMALY.value,
            edge_anomaly_flag=True,
            central_anomaly_flag=True,
            anomaly_decision_agreement=True,
            edge_anomaly_type=edge_type,
            central_anomaly_type=central_type,
            type_agreement=type_agree,
            edge_score=edge_score,
            edge_score_type=edge_score_type,
            central_score=central_score,
            central_score_type=central_score_type,
            details=details_str,
        )

    # B. BOTH AGREE NORMAL
    if edge_flag is False and central_flag is False:
        return EdgeCentralComparison(
            comparison_status=ComparisonStatus.BOTH_AGREE_NORMAL.value,
            edge_anomaly_flag=False,
            central_anomaly_flag=False,
            anomaly_decision_agreement=True,
            edge_anomaly_type=edge_type,
            central_anomaly_type=central_type,
            type_agreement=None,
            edge_score=edge_score,
            edge_score_type=edge_score_type,
            central_score=central_score,
            central_score_type=central_score_type,
            details="Both edge device and central AI agreed that the observation is normal.",
        )

    # C. EDGE ONLY ANOMALY
    if edge_flag is True and central_flag is False:
        return EdgeCentralComparison(
            comparison_status=ComparisonStatus.EDGE_ONLY_ANOMALY.value,
            edge_anomaly_flag=True,
            central_anomaly_flag=False,
            anomaly_decision_agreement=False,
            edge_anomaly_type=edge_type,
            central_anomaly_type=central_type,
            type_agreement=None,
            edge_score=edge_score,
            edge_score_type=edge_score_type,
            central_score=central_score,
            central_score_type=central_score_type,
            details=(
                f"Edge device flagged an anomaly (type='{edge_type}'), but central AI "
                f"scored the reading as normal (score={central_score}%)."
            ),
        )

    # D. CENTRAL ONLY ANOMALY
    if edge_flag is False and central_flag is True:
        return EdgeCentralComparison(
            comparison_status=ComparisonStatus.CENTRAL_ONLY_ANOMALY.value,
            edge_anomaly_flag=False,
            central_anomaly_flag=True,
            anomaly_decision_agreement=False,
            edge_anomaly_type=edge_type,
            central_anomaly_type=central_type,
            type_agreement=None,
            edge_score=edge_score,
            edge_score_type=edge_score_type,
            central_score=central_score,
            central_score_type=central_score_type,
            details=(
                f"Central AI detected an anomaly (type='{central_type}', score={central_score}%), "
                f"while edge device classified the observation as normal."
            ),
        )

    # Fallback for unexpected edge cases
    return EdgeCentralComparison(
        comparison_status=ComparisonStatus.INSUFFICIENT_EVIDENCE.value,
        edge_anomaly_flag=edge_flag,
        central_anomaly_flag=central_flag,
        anomaly_decision_agreement=None,
        edge_anomaly_type=edge_type,
        central_anomaly_type=central_type,
        type_agreement=None,
        edge_score=edge_score,
        edge_score_type=edge_score_type,
        central_score=central_score,
        central_score_type=central_score_type,
        details="Could not definitively classify edge-central consensus.",
    )
