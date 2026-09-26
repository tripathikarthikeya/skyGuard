"""
model/ingestion.py

SkyGuard AI — Edge Observation Ingestion Service.

Handles ingestion-layer validation, duplicate event checking, sequence number tracking,
raw reading preservation, and hand-off to the Central AI state management pipeline.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import time
from typing import Optional

from fastapi import HTTPException, status
import pandas as pd

from model.contracts import ObservationPacket, ObservationIngestResponse, EdgeCentralComparison
from model.edge_central_comparison import compare_edge_central
from model.state import StateManager


class ObservationIngestionService:
    """
    Ingestion service for Canonical Observation Packets.
    
    Coordinates station identity verification, duplicate event prevention,
    sequence continuity tracking, and pipeline hand-off to StateManager.
    """

    def __init__(
        self,
        state_manager: StateManager,
        sim: Optional[object] = None,
        max_seen_events: int = 20000,
    ):
        self.state_manager = state_manager
        self.sim = sim
        self.seen_events: set[str] = set()
        self.last_sequences: dict[tuple[str, str], int] = {}
        self.max_seen_events = max_seen_events

    def ingest_observation(self, packet: ObservationPacket) -> ObservationIngestResponse:
        """
        Processes an incoming ObservationPacket.
        
        Args:
            packet: Validated Pydantic ObservationPacket.
            
        Returns:
            ObservationIngestResponse indicating acceptance and processing status.
            
        Raises:
            HTTPException(404): If station_id is not registered.
            HTTPException(409): If event_id has already been ingested.
        """
        # 1. Station Identity Validation
        if packet.station_id not in self.state_manager.buffers:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown station_id '{packet.station_id}'. Station is not registered in the system.",
            )

        # 2. Duplicate Event Detection
        if packet.event_id in self.seen_events:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Duplicate event_id '{packet.event_id}' has already been ingested.",
            )

        # 3. Sequence Number Evaluation
        seq_key = (packet.station_id, packet.device_id)
        if seq_key not in self.last_sequences:
            seq_status = "initial"
        else:
            last_seq = self.last_sequences[seq_key]
            if packet.sequence_number == last_seq + 1:
                seq_status = "ok"
            elif packet.sequence_number > last_seq + 1:
                seq_status = "gap_detected"
            elif packet.sequence_number == last_seq:
                seq_status = "repeated_sequence"
            else:
                seq_status = "out_of_order"

        # Update sequence cache and event_id registry
        self.last_sequences[seq_key] = packet.sequence_number
        if len(self.seen_events) >= self.max_seen_events:
            self.seen_events.clear()
        self.seen_events.add(packet.event_id)

        # 4. Extract Raw Readings Verbatim (No Clamping / No Imputation at Ingestion)
        raw_reading = {
            "temperature_c": packet.readings.temperature_c,
            "pressure_hpa": packet.readings.pressure_hpa,
            "humidity_pct": packet.readings.humidity_pct,
        }

        # 5. Pipeline Hand-off to Central StateManager
        timestamp_dt = packet.observed_at
        verdict = self.state_manager.ingest_reading(
            station_id=packet.station_id,
            raw_reading=raw_reading,
            timestamp=timestamp_dt,
        )

        # 6. Edge <-> Central AI Diagnostic Comparison
        comparison = compare_edge_central(
            edge_inference=packet.edge_inference,
            central_verdict=verdict,
        )

        # 6b. Persist Full Hybrid Lifecycle (Raw Obs + Edge Inf + Central AI + Comparison)
        if hasattr(self.state_manager, "history") and self.state_manager.history is not None:
            try:
                self.state_manager.history.persist_hybrid_lifecycle(
                    packet=packet,
                    verdict=verdict,
                    comparison=comparison,
                    source=self.state_manager.mode,
                )
            except Exception as e:
                print(f"[ObservationIngestionService] Hybrid persistence error: {e!r}")

        # 7. Update Simulator Dashboard State (if attached)
        if self.sim is not None:
            sim = self.sim
            sim.latest[packet.station_id] = {
                "raw_reading": raw_reading,
                "verdict": verdict,
                "timestamp": timestamp_dt,
                "edge_inference": packet.edge_inference.model_dump(),
                "device_metadata": packet.device_metadata.model_dump(),
                "comparison": comparison.model_dump(),
            }
            sim.trend_history[packet.station_id].append({
                "timestamp": timestamp_dt,
                **raw_reading,
                "is_anomaly": verdict.get("is_anomaly", False),
                "fault_type": verdict.get("fault_type"),
                "severity": verdict.get("severity"),
                "anomaly_score_pct": verdict.get("anomaly_score_pct"),
                "suggested_temperature_c": verdict.get("suggested_values", {}).get("temperature_c"),
                "suggested_pressure_hpa": verdict.get("suggested_values", {}).get("pressure_hpa"),
                "suggested_humidity_pct": verdict.get("suggested_values", {}).get("humidity_pct"),
                "health_status": verdict.get("health_status"),
                "source": self.state_manager.mode,
            })

            if verdict.get("is_anomaly", False):
                sim._anomaly_counter += 1
                anom_entry = {
                    "anomaly_id": f"anom_{sim._anomaly_counter:05d}",
                    "station_id": packet.station_id,
                    "timestamp": timestamp_dt,
                    "anomaly_score_pct": verdict.get("anomaly_score_pct"),
                    "severity": verdict.get("severity"),
                    "type": verdict.get("fault_type"),
                    "root_cause": verdict.get("fault_type", "Unknown Anomaly"),
                    "suggested_values": verdict.get("suggested_values", {}),
                    "observed_values": raw_reading,
                    "affected_parameters": verdict.get("likely_faulty_sensors", []),
                    "shap_features": verdict.get("shap_features", []),
                    "regime": verdict.get("regime"),
                    "network_corroboration": verdict.get("network_corroboration"),
                    "model_confidence_pct": verdict.get("model_confidence_pct"),
                    "rule_confidence_pct": verdict.get("rule_confidence_pct"),
                    "rules_fired": verdict.get("rules_fired", []),
                    "edge_inference": packet.edge_inference.model_dump(),
                    "comparison": comparison.model_dump(),
                }
                sim.recent_anomalies.appendleft(anom_entry)

            # Asynchronously broadcast observation events to live WebSocket subscribers
            try:
                loop = asyncio.get_running_loop()
                ingest_time_ms = int(time.time() * 1000)

                # 1. Telemetry tick for dashboard cards & trend charts
                loop.create_task(sim._broadcast_event({
                    "type": "TELEMETRY_TICK",
                    "station_id": packet.station_id,
                    "timestamp": timestamp_dt.isoformat() if hasattr(timestamp_dt, "isoformat") else str(timestamp_dt),
                    "reading": raw_reading,
                    "verdict": {
                        "is_anomaly": bool(verdict.get("is_anomaly", False)),
                        "anomaly_score_pct": verdict.get("anomaly_score_pct"),
                        "model_confidence_pct": verdict.get("model_confidence_pct"),
                        "rule_confidence_pct": verdict.get("rule_confidence_pct"),
                        "fault_type": verdict.get("fault_type"),
                        "severity": verdict.get("severity"),
                        "health_status": verdict.get("health_status"),
                        "suggested_values": verdict.get("suggested_values"),
                        "decision_basis": verdict.get("decision_basis"),
                        "likely_faulty_sensors": verdict.get("likely_faulty_sensors", []),
                        "model_status": verdict.get("model_status"),
                    },
                    "mode": self.state_manager.mode,
                    "ingest_time_ms": ingest_time_ms,
                    "edge_inference": packet.edge_inference.model_dump(),
                    "comparison": comparison.model_dump(),
                }))

                # 2. Anomaly incident event if anomalous
                if verdict.get("is_anomaly", False):
                    loop.create_task(sim._broadcast_event({
                        "type": "ANOMALY_EVENT",
                        "station_id": packet.station_id,
                        "anomaly": anom_entry,
                        "ingest_time_ms": ingest_time_ms,
                    }))

                # 3. Canonical observation ingested event
                loop.create_task(sim._broadcast_event({
                    "type": "OBSERVATION_INGESTED",
                    "event_id": packet.event_id,
                    "station_id": packet.station_id,
                    "device_id": packet.device_id,
                    "observed_at": packet.observed_at.isoformat(),
                    "raw_reading": raw_reading,
                    "edge_inference": packet.edge_inference.model_dump(),
                    "verdict": verdict,
                    "comparison": comparison.model_dump(),
                }))
            except Exception:
                pass

        # 8. Construct and Return Ingestion Response
        return ObservationIngestResponse(
            accepted=True,
            event_id=packet.event_id,
            station_id=packet.station_id,
            device_id=packet.device_id,
            observed_at=packet.observed_at,
            sequence_number=packet.sequence_number,
            sequence_status=seq_status,
            ingestion_status="processed",
            message="Observation successfully validated and processed by central pipeline.",
        )
