"""
model/contracts.py

SkyGuard AI — Canonical Authoritative Observation Data Contract.

This module defines the canonical Pydantic v2 data models for the observation packet
ingested from Level 1 (Edge / ESP32) to Level 2 (Central AI / Backend).

Architectural Rule:
The contract establishes a strict structural schema while strictly decoupling
structural validation from anomaly detection. Raw sensor readings are preserved
without destructive filtering, clamping, or modification.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ObservationReadings(BaseModel):
    """
    Original raw sensor readings recorded by the edge station hardware.
    
    Fields are typed as numeric floats. Null values are permitted for channels
    experiencing sensor dropout or communication loss, but meteorological thresholds
    (e.g., physical bounds) are deliberately NOT enforced at this schema layer.
    """
    model_config = ConfigDict(extra="forbid")

    temperature_c: Optional[float] = Field(
        ...,
        description="Original observed temperature in degrees Celsius (°C).",
    )
    pressure_hpa: Optional[float] = Field(
        ...,
        description="Original observed barometric pressure in hectopascals (hPa).",
    )
    humidity_pct: Optional[float] = Field(
        ...,
        description="Original observed relative humidity in percentage (%).",
    )


class EdgeInference(BaseModel):
    """
    Level 1 Edge inference result produced on the ESP32 / microcontroller.
    
    Contains lightweight local validation flags, edge anomaly types, heuristic scores,
    and model version metadata.
    """
    model_config = ConfigDict(extra="forbid")

    status: str = Field(
        ...,
        min_length=1,
        description="Edge execution status (e.g., 'ok', 'anomaly_detected', 'sensor_degraded', 'error').",
    )
    anomaly_flag: bool = Field(
        ...,
        description="True if edge-side rule or lightweight model flagged an anomaly, False otherwise.",
    )
    anomaly_type: Optional[str] = Field(
        default=None,
        description="Edge anomaly category (e.g. 'physical_bounds', 'dropout', 'sensor_fail_low') or null.",
    )
    score: Optional[float] = Field(
        default=None,
        description="Quantitative edge severity or heuristic score (not a calibrated probability).",
    )
    score_type: Optional[str] = Field(
        default=None,
        description="Descriptor for edge score meaning (e.g., 'rule_score', 'raw_score', 'normalized_score').",
    )
    model_version: str = Field(
        ...,
        min_length=1,
        description="Edge model or rule engine version identifier (e.g., 'edge_v1.0.0').",
    )
    inference_method: str = Field(
        ...,
        min_length=1,
        description="Edge inference mechanism (e.g., 'rules', 'embedded_ml', 'rules+embedded_ml').",
    )

    @field_validator("status", "model_version", "inference_method", mode="after")
    @classmethod
    def validate_non_empty_strings(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Field cannot be empty or whitespace only.")
        return v


class DeviceMetadata(BaseModel):
    """
    Hardware and telemetry metadata for the transmitting edge device.
    """
    model_config = ConfigDict(extra="forbid")

    firmware_version: str = Field(
        ...,
        min_length=1,
        description="Firmware version running on the edge device.",
    )
    battery_voltage: Optional[float] = Field(
        default=None,
        description="Battery voltage in Volts (V), or null if wall-powered / unmetered.",
    )
    signal_strength: Optional[float] = Field(
        default=None,
        description="Wireless signal strength in unspecified units (e.g., RSSI dBm or link quality).",
    )

    @field_validator("firmware_version", mode="after")
    @classmethod
    def validate_non_empty_firmware(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("firmware_version cannot be empty or whitespace only.")
        return v


class ObservationPacket(BaseModel):
    """
    Authoritative Canonical Observation Packet.
    
    The single standardized schema shared between ESP32 hardware, Virtual Edge
    simulators, Backend Ingestion APIs, Central AI pipelines, and Storage.
    """
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(
        ...,
        min_length=1,
        description="Unique observation event identifier suitable for duplicate detection.",
    )
    station_id: str = Field(
        ...,
        min_length=1,
        description="Meteorological station identifier (e.g., 'AWS-CHN-024').",
    )
    device_id: str = Field(
        ...,
        min_length=1,
        description="Hardware device identifier (e.g., 'esp32-node-01').",
    )
    observed_at: datetime = Field(
        ...,
        description="Timezone-aware ISO-8601 datetime of when the observation was recorded.",
    )
    sequence_number: int = Field(
        ...,
        ge=0,
        description="Monotonically increasing sequence number from the device (>= 0).",
    )
    readings: ObservationReadings = Field(
        ...,
        description="Original raw sensor readings.",
    )
    edge_inference: EdgeInference = Field(
        ...,
        description="Level 1 edge inference metadata and verdict.",
    )
    device_metadata: DeviceMetadata = Field(
        ...,
        description="Edge hardware and telemetry metadata.",
    )

    @field_validator("event_id", "station_id", "device_id", mode="after")
    @classmethod
    def validate_non_empty_identity_fields(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Field cannot be empty or whitespace only.")
        return v

    @field_validator("observed_at", mode="after")
    @classmethod
    def validate_timezone_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("observed_at must be a timezone-aware datetime (e.g., UTC or explicit offset).")
        return v


class ObservationIngestResponse(BaseModel):
    """
    Standard response model for POST /api/ingest/observation.
    """
    model_config = ConfigDict(extra="forbid")

    accepted: bool = Field(
        default=True,
        description="Whether the observation packet was accepted by the ingestion layer.",
    )
    event_id: str = Field(
        ...,
        description="Unique event identifier of the ingested observation.",
    )
    station_id: str = Field(
        ...,
        description="Target meteorological station identifier.",
    )
    device_id: str = Field(
        ...,
        description="Originating edge device identifier.",
    )
    observed_at: datetime = Field(
        ...,
        description="Original timezone-aware observation timestamp.",
    )
    sequence_number: int = Field(
        ...,
        ge=0,
        description="Device-emitted sequence number.",
    )
    sequence_status: str = Field(
        ...,
        description="Sequence evaluation status ('ok', 'gap_detected', 'repeated_sequence', 'out_of_order', 'initial').",
    )
    ingestion_status: str = Field(
        default="processed",
        description="Pipeline processing status ('processed', 'accepted').",
    )
    message: Optional[str] = Field(
        default=None,
        description="Human-readable diagnostic status message.",
    )


class EdgeCentralComparison(BaseModel):
    """
    Diagnostic comparison result evaluating Level 1 Edge inference against Level 2 Central AI.
    
    Provides an explicit audit trail of consensus, divergence, fault type alignment,
    and independently preserved confidence metrics without imposing a final anomaly decision policy.
    """
    model_config = ConfigDict(extra="forbid")

    comparison_status: str = Field(
        ...,
        description="High-level consensus classification (e.g., 'BOTH_AGREE_ANOMALY', 'BOTH_AGREE_NORMAL', 'EDGE_ONLY_ANOMALY', 'CENTRAL_ONLY_ANOMALY', 'EDGE_UNAVAILABLE', 'CENTRAL_UNAVAILABLE', 'INSUFFICIENT_EVIDENCE').",
    )
    edge_anomaly_flag: Optional[bool] = Field(
        default=None,
        description="Boolean anomaly decision reported by the edge device, or null if unavailable.",
    )
    central_anomaly_flag: Optional[bool] = Field(
        default=None,
        description="Boolean anomaly decision reported by central AI detection, or null if unavailable.",
    )
    anomaly_decision_agreement: Optional[bool] = Field(
        default=None,
        description="True if edge and central boolean decisions agree, False if they disagree, null if unavailable.",
    )
    edge_anomaly_type: Optional[str] = Field(
        default=None,
        description="Anomaly type reported by edge inference, or null.",
    )
    central_anomaly_type: Optional[str] = Field(
        default=None,
        description="Anomaly / fault type diagnosed by central detection, or null.",
    )
    type_agreement: Optional[bool] = Field(
        default=None,
        description="True if both flagged an anomaly and diagnosed the same fault type, False if types differ, null if not both anomalous.",
    )
    edge_score: Optional[float] = Field(
        default=None,
        description="Raw or heuristic anomaly score reported by edge, or null.",
    )
    edge_score_type: Optional[str] = Field(
        default=None,
        description="Descriptor for edge score meaning, or null.",
    )
    central_score: Optional[float] = Field(
        default=None,
        description="Quantitative anomaly score reported by central AI (0.0 to 100.0), or null.",
    )
    central_score_type: Optional[str] = Field(
        default="percentage",
        description="Descriptor for central score metric (e.g. 'percentage').",
    )
    details: Optional[str] = Field(
        default=None,
        description="Human-readable audit explanation of the comparison result.",
    )


