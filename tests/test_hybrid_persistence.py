"""
tests/test_hybrid_persistence.py

SkyGuard AI — Comprehensive Hybrid Observation Lifecycle Persistence Tests.

Verifies:
1. Raw observation persistence (event_id, station_id, device_id, observed_at, sequence_number, readings, device_metadata).
2. Raw data immutability and extreme raw values preservation (never clamped, zero-filled, or overwritten).
3. Null readings handling (null temperature, pressure, humidity preserved as None).
4. Edge inference persistence (independent level 1 storage with status, anomaly flag, score, model version).
5. Central AI inference persistence (independent level 2 storage with is_anomaly, fault_type, confidence, etc.).
6. Diagnostic comparison persistence (all 7 comparison statuses, agreement booleans, independent scores).
7. Independent reconstruction of all 4 layers (raw, edge, central, comparison).
8. Idempotency and duplicate prevention.
9. Full end-to-end round trip: ObservationPacket -> Ingestion -> Central AI -> Comparison -> Persistence -> Read back.
"""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from history_store import HistoryStore, HYBRID_COLUMNS
from model.contracts import (
    ObservationPacket,
    ObservationReadings,
    EdgeInference,
    DeviceMetadata,
    EdgeCentralComparison,
)
from model.edge_central_comparison import compare_edge_central
from model.ingestion import ObservationIngestionService
from model.state import StateManager


class TestHybridPersistence(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.history = HistoryStore(base_dir=self.base_dir)
        # Ensure tests use local storage fixture
        self.history.use_db = False

    def tearDown(self):
        self.temp_dir.cleanup()

    def _sample_packet(
        self,
        event_id: str = "evt-test-001",
        station_id: str = "AWS-CHN-024",
        device_id: str = "esp32-node-01",
        temp: float | None = 32.5,
        pressure: float | None = 1012.3,
        humidity: float | None = 65.0,
        seq: int = 101,
        edge_anom: bool = False,
        edge_type: str | None = None,
        edge_score: float | None = 0.0,
        edge_score_type: str | None = "rule_score",
    ) -> ObservationPacket:
        return ObservationPacket(
            event_id=event_id,
            station_id=station_id,
            device_id=device_id,
            observed_at=datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc),
            sequence_number=seq,
            readings=ObservationReadings(
                temperature_c=temp,
                pressure_hpa=pressure,
                humidity_pct=humidity,
            ),
            edge_inference=EdgeInference(
                status="anomaly_detected" if edge_anom else "ok",
                anomaly_flag=edge_anom,
                anomaly_type=edge_type,
                score=edge_score,
                score_type=edge_score_type,
                model_version="edge_v1.0.0",
                inference_method="rules",
            ),
            device_metadata=DeviceMetadata(
                firmware_version="v2.1.0",
                battery_voltage=3.85,
                signal_strength=-68.0,
            ),
        )

    def test_raw_observation_persisted_and_reconstructed(self):
        """1. Raw observation fields are persisted and reconstructed accurately."""
        packet = self._sample_packet(event_id="evt-raw-1", temp=28.4, pressure=1008.2, humidity=72.1)
        self.history.persist_hybrid_lifecycle(packet)

        rec = self.history.get_hybrid_record("evt-raw-1")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["event_id"], "evt-raw-1")
        self.assertEqual(rec["station_id"], "AWS-CHN-024")
        self.assertEqual(rec["device_id"], "esp32-node-01")
        self.assertEqual(rec["sequence_number"], 101)
        self.assertEqual(rec["raw_observation"]["temperature_c"], 28.4)
        self.assertEqual(rec["raw_observation"]["pressure_hpa"], 1008.2)
        self.assertEqual(rec["raw_observation"]["humidity_pct"], 72.1)
        self.assertEqual(rec["raw_observation"]["firmware_version"], "v2.1.0")
        self.assertEqual(rec["raw_observation"]["battery_voltage"], 3.85)
        self.assertEqual(rec["raw_observation"]["signal_strength"], -68.0)

    def test_timestamp_preservation(self):
        """5. observed_at is preserved exactly as a timezone-aware timestamp."""
        observed_ts = datetime(2026, 9, 20, 14, 35, 22, tzinfo=timezone.utc)
        packet = ObservationPacket(
            event_id="evt-ts-1",
            station_id="AWS-CHN-024",
            device_id="esp32-node-01",
            observed_at=observed_ts,
            sequence_number=50,
            readings=ObservationReadings(temperature_c=25.0, pressure_hpa=1010.0, humidity_pct=50.0),
            edge_inference=EdgeInference(
                status="ok", anomaly_flag=False, model_version="v1", inference_method="rules"
            ),
            device_metadata=DeviceMetadata(firmware_version="v1"),
        )
        self.history.persist_hybrid_lifecycle(packet)
        rec = self.history.get_hybrid_record("evt-ts-1")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["observed_at"], observed_ts)

    def test_null_readings_preserved(self):
        """7-9. Null temperature, pressure, and humidity are preserved as None."""
        packet = self._sample_packet(event_id="evt-null-1", temp=None, pressure=None, humidity=None)
        self.history.persist_hybrid_lifecycle(packet)

        rec = self.history.get_hybrid_record("evt-null-1")
        self.assertIsNotNone(rec)
        self.assertIsNone(rec["raw_observation"]["temperature_c"])
        self.assertIsNone(rec["raw_observation"]["pressure_hpa"])
        self.assertIsNone(rec["raw_observation"]["humidity_pct"])

    def test_extreme_raw_values_unmodified(self):
        """10. Extreme raw values (e.g. 70°C, 150% RH) remain untouched and not clamped."""
        packet = self._sample_packet(event_id="evt-extreme-1", temp=70.0, pressure=1085.5, humidity=150.0)
        self.history.persist_hybrid_lifecycle(packet)

        rec = self.history.get_hybrid_record("evt-extreme-1")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["raw_observation"]["temperature_c"], 70.0)
        self.assertEqual(rec["raw_observation"]["pressure_hpa"], 1085.5)
        self.assertEqual(rec["raw_observation"]["humidity_pct"], 150.0)

    def test_edge_inference_persisted_independently(self):
        """11. Edge inference metadata is persisted independently."""
        packet = self._sample_packet(
            event_id="evt-edge-1",
            edge_anom=True,
            edge_type="physical_bounds",
            edge_score=95.5,
            edge_score_type="bounds_distance",
        )
        self.history.persist_hybrid_lifecycle(packet)

        rec = self.history.get_hybrid_record("evt-edge-1")
        self.assertIsNotNone(rec)
        edge = rec["edge_inference"]
        self.assertIsNotNone(edge)
        self.assertEqual(edge["status"], "anomaly_detected")
        self.assertTrue(edge["anomaly_flag"])
        self.assertEqual(edge["anomaly_type"], "physical_bounds")
        self.assertEqual(edge["score"], 95.5)
        self.assertEqual(edge["score_type"], "bounds_distance")
        self.assertEqual(edge["model_version"], "edge_v1.0.0")
        self.assertEqual(edge["inference_method"], "rules")

    def test_central_inference_persisted_independently(self):
        """12. Central AI verdict is persisted independently."""
        packet = self._sample_packet(event_id="evt-central-1")
        central_verdict = {
            "is_anomaly": True,
            "fault_type": "drift",
            "severity": "high",
            "anomaly_score_pct": 88.5,
            "model_confidence_pct": 92.0,
            "rule_confidence_pct": 85.0,
            "model_status": "AVAILABLE",
            "decision_basis": "MODEL_AND_RULE_SUPPORTED",
            "health_status": "WARNING",
            "suggested_values": {
                "temperature_c": 29.5,
                "pressure_hpa": 1012.0,
                "humidity_pct": 60.0,
            },
        }
        self.history.persist_hybrid_lifecycle(packet, verdict=central_verdict, source="live")

        rec = self.history.get_hybrid_record("evt-central-1")
        self.assertIsNotNone(rec)
        central = rec["central_verdict"]
        self.assertIsNotNone(central)
        self.assertTrue(central["is_anomaly"])
        self.assertEqual(central["fault_type"], "drift")
        self.assertEqual(central["severity"], "high")
        self.assertEqual(central["anomaly_score_pct"], 88.5)
        self.assertEqual(central["model_confidence_pct"], 92.0)
        self.assertEqual(central["decision_basis"], "MODEL_AND_RULE_SUPPORTED")
        self.assertEqual(central["health_status"], "WARNING")
        self.assertEqual(central["suggested_values"]["temperature_c"], 29.5)
        self.assertEqual(central["source"], "live")

    def test_comparison_status_both_agree_anomaly(self):
        """16. BOTH_AGREE_ANOMALY consensus is preserved and reconstructed."""
        packet = self._sample_packet(event_id="evt-agree-anom", edge_anom=True, edge_type="physical_bounds")
        central_verdict = {
            "is_anomaly": True,
            "fault_type": "physical_bounds",
            "anomaly_score_pct": 99.0,
        }
        comparison = compare_edge_central(packet.edge_inference, central_verdict)
        self.assertEqual(comparison.comparison_status, "BOTH_AGREE_ANOMALY")

        self.history.persist_hybrid_lifecycle(packet, verdict=central_verdict, comparison=comparison)
        rec = self.history.get_hybrid_record("evt-agree-anom")
        comp = rec["comparison"]
        self.assertIsNotNone(comp)
        self.assertEqual(comp["comparison_status"], "BOTH_AGREE_ANOMALY")
        self.assertTrue(comp["edge_anomaly_flag"])
        self.assertTrue(comp["central_anomaly_flag"])
        self.assertTrue(comp["anomaly_decision_agreement"])
        self.assertTrue(comp["type_agreement"])

    def test_comparison_status_both_agree_normal(self):
        """17. BOTH_AGREE_NORMAL consensus is preserved and reconstructed."""
        packet = self._sample_packet(event_id="evt-agree-norm", edge_anom=False)
        central_verdict = {"is_anomaly": False, "anomaly_score_pct": 0.0}
        comparison = compare_edge_central(packet.edge_inference, central_verdict)
        self.assertEqual(comparison.comparison_status, "BOTH_AGREE_NORMAL")

        self.history.persist_hybrid_lifecycle(packet, verdict=central_verdict, comparison=comparison)
        rec = self.history.get_hybrid_record("evt-agree-norm")
        comp = rec["comparison"]
        self.assertIsNotNone(comp)
        self.assertEqual(comp["comparison_status"], "BOTH_AGREE_NORMAL")
        self.assertFalse(comp["edge_anomaly_flag"])
        self.assertFalse(comp["central_anomaly_flag"])
        self.assertTrue(comp["anomaly_decision_agreement"])

    def test_comparison_status_edge_only_anomaly(self):
        """14. EDGE_ONLY_ANOMALY divergence is preserved without mutating central AI."""
        packet = self._sample_packet(event_id="evt-edge-only", edge_anom=True, edge_type="physical_bounds")
        central_verdict = {"is_anomaly": False, "anomaly_score_pct": 5.0}
        comparison = compare_edge_central(packet.edge_inference, central_verdict)
        self.assertEqual(comparison.comparison_status, "EDGE_ONLY_ANOMALY")

        self.history.persist_hybrid_lifecycle(packet, verdict=central_verdict, comparison=comparison)
        rec = self.history.get_hybrid_record("evt-edge-only")
        comp = rec["comparison"]
        self.assertEqual(comp["comparison_status"], "EDGE_ONLY_ANOMALY")
        self.assertTrue(comp["edge_anomaly_flag"])
        self.assertFalse(comp["central_anomaly_flag"])
        self.assertFalse(comp["anomaly_decision_agreement"])

    def test_comparison_status_central_only_anomaly(self):
        """15. CENTRAL_ONLY_ANOMALY divergence is preserved without mutating edge result."""
        packet = self._sample_packet(event_id="evt-central-only", edge_anom=False)
        central_verdict = {"is_anomaly": True, "fault_type": "drift", "anomaly_score_pct": 75.0}
        comparison = compare_edge_central(packet.edge_inference, central_verdict)
        self.assertEqual(comparison.comparison_status, "CENTRAL_ONLY_ANOMALY")

        self.history.persist_hybrid_lifecycle(packet, verdict=central_verdict, comparison=comparison)
        rec = self.history.get_hybrid_record("evt-central-only")
        comp = rec["comparison"]
        self.assertEqual(comp["comparison_status"], "CENTRAL_ONLY_ANOMALY")
        self.assertFalse(comp["edge_anomaly_flag"])
        self.assertTrue(comp["central_anomaly_flag"])
        self.assertFalse(comp["anomaly_decision_agreement"])

    def test_comparison_status_edge_unavailable(self):
        """18. EDGE_UNAVAILABLE state is correctly persisted and reconstructed."""
        packet = self._sample_packet(event_id="evt-edge-unavail")
        packet.edge_inference.status = "unavailable"
        central_verdict = {"is_anomaly": False, "anomaly_score_pct": 0.0}
        comparison = compare_edge_central(packet.edge_inference, central_verdict)
        self.assertEqual(comparison.comparison_status, "EDGE_UNAVAILABLE")

        self.history.persist_hybrid_lifecycle(packet, verdict=central_verdict, comparison=comparison)
        rec = self.history.get_hybrid_record("evt-edge-unavail")
        self.assertEqual(rec["comparison"]["comparison_status"], "EDGE_UNAVAILABLE")
        self.assertIsNone(rec["comparison"]["anomaly_decision_agreement"])

    def test_comparison_status_central_unavailable(self):
        """19. CENTRAL_UNAVAILABLE state is correctly persisted and reconstructed."""
        packet = self._sample_packet(event_id="evt-cent-unavail", edge_anom=False)
        comparison = compare_edge_central(packet.edge_inference, None)
        self.assertEqual(comparison.comparison_status, "CENTRAL_UNAVAILABLE")

        self.history.persist_hybrid_lifecycle(packet, verdict=None, comparison=comparison)
        rec = self.history.get_hybrid_record("evt-cent-unavail")
        self.assertEqual(rec["comparison"]["comparison_status"], "CENTRAL_UNAVAILABLE")
        self.assertIsNone(rec["central_verdict"])

    def test_scores_remain_independent(self):
        """20-21. Edge heuristic score and central score remain independent without normalization."""
        packet = self._sample_packet(
            event_id="evt-scores-1",
            edge_score=2.35,
            edge_score_type="z_distance",
        )
        central_verdict = {
            "is_anomaly": False,
            "anomaly_score_pct": 14.2,
        }
        comparison = compare_edge_central(packet.edge_inference, central_verdict)
        self.history.persist_hybrid_lifecycle(packet, verdict=central_verdict, comparison=comparison)

        rec = self.history.get_hybrid_record("evt-scores-1")
        self.assertEqual(rec["edge_inference"]["score"], 2.35)
        self.assertEqual(rec["edge_inference"]["score_type"], "z_distance")
        self.assertEqual(rec["central_verdict"]["anomaly_score_pct"], 14.2)
        self.assertEqual(rec["comparison"]["edge_score"], 2.35)
        self.assertEqual(rec["comparison"]["central_score"], 14.2)

    def test_idempotent_duplicate_event_persistence(self):
        """22. Duplicate event_id persistence does not create duplicate rows."""
        packet = self._sample_packet(event_id="evt-dup-1")
        self.history.persist_hybrid_lifecycle(packet)
        # Attempt duplicate write
        self.history.persist_hybrid_lifecycle(packet)

        # Verify only 1 record exists in CSV
        df = pd.read_csv(self.history._hybrid_path(packet.station_id))
        self.assertEqual(len(df[df["event_id"] == "evt-dup-1"]), 1)

    def test_get_hybrid_history_range_query(self):
        """Verifies range queries on hybrid history per station."""
        station = "AWS-CHN-024"
        now = datetime.now(timezone.utc)
        for i in range(5):
            pkt = ObservationPacket(
                event_id=f"evt-range-{i}",
                station_id=station,
                device_id="esp32-node-01",
                observed_at=now - pd.Timedelta(hours=i * 2),
                sequence_number=100 + i,
                readings=ObservationReadings(temperature_c=30.0 + i, pressure_hpa=1010.0, humidity_pct=60.0),
                edge_inference=EdgeInference(status="ok", anomaly_flag=False, model_version="v1", inference_method="rules"),
                device_metadata=DeviceMetadata(firmware_version="v1"),
            )
            self.history.persist_hybrid_lifecycle(pkt)

        # Query recent 5 hours
        records = self.history.get_hybrid_history(station, hours=5)
        # Should include events from 0, 2, 4 hours ago (3 records)
        self.assertEqual(len(records), 3)

    def test_end_to_end_ingestion_persistence_round_trip(self):
        """
        29. Complete Round-Trip Test:
        ObservationPacket -> Ingestion Service -> Central AI -> Comparison -> Persistence -> Read back
        Verifying that all four layers (Raw, Edge, Central, Comparison) are reconstructed accurately.
        """
        # Set up real state manager with this test history store and artifact
        from model.simulator import ARTIFACTS_PATH, DATA_DIR
        import joblib
        artifact = joblib.load(ARTIFACTS_PATH)
        metadata = pd.read_csv(DATA_DIR / "stations_metadata.csv")
        state_mgr = StateManager(metadata=metadata, artifact=artifact, history_store=self.history)
        ingestion_service = ObservationIngestionService(state_manager=state_mgr)

        # Formulate canonical packet with out-of-bounds dropout reading
        packet = ObservationPacket(
            event_id="evt-roundtrip-42",
            station_id="AWS-CHN-024",
            device_id="esp32-node-01",
            observed_at=datetime(2026, 9, 23, 15, 30, 0, tzinfo=timezone.utc),
            sequence_number=201,
            readings=ObservationReadings(
                temperature_c=-45.0,  # Extreme negative out-of-bounds reading
                pressure_hpa=1013.25,
                humidity_pct=80.0,
            ),
            edge_inference=EdgeInference(
                status="anomaly_detected",
                anomaly_flag=True,
                anomaly_type="physical_bounds",
                score=100.0,
                score_type="bounds_violation",
                model_version="edge_v1.0.0",
                inference_method="rules",
            ),
            device_metadata=DeviceMetadata(
                firmware_version="v2.1.0",
                battery_voltage=3.92,
                signal_strength=-65.0,
            ),
        )

        # 1. Ingest via service
        resp = ingestion_service.ingest_observation(packet)
        self.assertTrue(resp.accepted)
        self.assertEqual(resp.event_id, "evt-roundtrip-42")

        # 2. Read back from persistent storage
        rec = self.history.get_hybrid_record("evt-roundtrip-42")
        self.assertIsNotNone(rec, "Persisted hybrid record should be retrievable by event_id")

        # 3. Layer 1 Verification (Raw Sensor Observation)
        raw = rec["raw_observation"]
        self.assertEqual(raw["temperature_c"], -45.0, "Raw extreme temperature must NOT be clamped or imputed")
        self.assertEqual(raw["pressure_hpa"], 1013.25)
        self.assertEqual(raw["humidity_pct"], 80.0)
        self.assertEqual(raw["firmware_version"], "v2.1.0")
        self.assertEqual(raw["battery_voltage"], 3.92)

        # 4. Layer 2 Verification (Edge Inference)
        edge = rec["edge_inference"]
        self.assertIsNotNone(edge)
        self.assertEqual(edge["status"], "anomaly_detected")
        self.assertTrue(edge["anomaly_flag"])
        self.assertEqual(edge["anomaly_type"], "physical_bounds")
        self.assertEqual(edge["model_version"], "edge_v1.0.0")

        # 5. Layer 3 Verification (Central AI Inference)
        central = rec["central_verdict"]
        self.assertIsNotNone(central)
        self.assertTrue(central["is_anomaly"], "Central AI should independently flag the physical bounds fault")
        self.assertIsNotNone(central["suggested_values"])
        self.assertEqual(central["source"], "live")

        # 6. Layer 4 Verification (Diagnostic Comparison)
        comp = rec["comparison"]
        self.assertIsNotNone(comp)
        self.assertEqual(comp["comparison_status"], "BOTH_AGREE_ANOMALY")
        self.assertTrue(comp["edge_anomaly_flag"])
        self.assertTrue(comp["central_anomaly_flag"])
        self.assertTrue(comp["anomaly_decision_agreement"])


if __name__ == "__main__":
    unittest.main()
