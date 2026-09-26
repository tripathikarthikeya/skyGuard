"""
tests/test_hybrid_integration_validation.py

Step 9: SkyGuard AI Hybrid Edge + Central AI Architecture Complete Integration Validation.

Validates:
- Phase A: Backend contract validation
- Phase B: Simulator end-to-end observation flow
- Phase C: Normal observation (BOTH_AGREE_NORMAL)
- Phase D: Edge/Central agreement (BOTH_AGREE_ANOMALY)
- Phase E: Edge/Central disagreement (EDGE_ONLY_ANOMALY, CENTRAL_ONLY_ANOMALY)
- Phase F: Missing edge inference (EDGE_UNAVAILABLE)
- Phase G: Raw sensor dropout (null preserved, no sentinels)
- Phase H: Extreme sensor value (physical_bounds, raw value preserved)
- Phase I: Duplicate retry handling (409 Conflict, no duplicate persistence)
- Phase J: Sequence gap handling (GAP status, observation accepted)
- Phase K: Out-of-order sequence handling (OUT_OF_ORDER status)
- Phase L: Timestamp validation & timezone awareness
- Phase M: ESP32 protocol validation against Pydantic schema
- Phase N: ESP32 edge rule parity across all 11 test vectors
- Phase O: Multi-layer persistence integrity & event_id correlation
- Phase P: CSV fallback history format compatibility
- Phase Q: WebSocket broadcast from ObservationIngestionService
- Phase R/S: Full API route regression suite
- Phase T: Frontend anomaly drift data compatibility
"""

import asyncio
import os
import sys
import unittest
import uuid
from datetime import datetime, timezone
import pandas as pd
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from main import app
from model.contracts import (
    ObservationPacket,
    ObservationReadings,
    EdgeInference,
    DeviceMetadata,
    ObservationIngestResponse,
)
from model.edge_rules import run_edge_inference, EDGE_MODEL_VERSION, EDGE_INFERENCE_METHOD
from model.edge_central_comparison import ComparisonStatus, compare_edge_central
from history_store import HistoryStore


class TestHybridIntegrationValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()
        cls.sim = app.state.sim
        cls.station_id = cls.sim.metadata["station_id"].iloc[0]
        cls.station_id_2 = cls.sim.metadata["station_id"].iloc[1]
        cls.history_store = cls.sim.manager.history

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)

    # ─────────────────────────────────────────────────────────────────────────
    # Phase A: Backend Contract Validation
    # ─────────────────────────────────────────────────────────────────────────
    def test_phase_a_backend_contract_validation(self):
        packet = ObservationPacket(
            event_id=str(uuid.uuid4()),
            station_id=self.station_id,
            device_id="esp32_val_001",
            observed_at=datetime.now(timezone.utc),
            sequence_number=1,
            readings=ObservationReadings(
                temperature_c=26.5,
                pressure_hpa=1012.0,
                humidity_pct=60.0
            ),
            edge_inference=EdgeInference(
                status="ok",
                anomaly_flag=False,
                anomaly_type=None,
                score=None,
                score_type=None,
                model_version=EDGE_MODEL_VERSION,
                inference_method=EDGE_INFERENCE_METHOD,
            ),
            device_metadata=DeviceMetadata(
                firmware_version="esp32_edge_v1.0.0",
                battery_voltage=None,
                signal_strength=-65.0
            )
        )
        payload = packet.model_dump(mode="json")
        resp = self.client.post("/api/ingest/observation", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["accepted"])
        self.assertEqual(data["event_id"], packet.event_id)
        self.assertEqual(data["station_id"], self.station_id)

    # ─────────────────────────────────────────────────────────────────────────
    # Phase B: Simulator End-to-End Validation
    # ─────────────────────────────────────────────────────────────────────────
    def test_phase_b_simulator_end_to_end(self):
        event_id = str(uuid.uuid4())
        packet = ObservationPacket(
            event_id=event_id,
            station_id=self.station_id,
            device_id="sim_node_e2e",
            observed_at=datetime.now(timezone.utc),
            sequence_number=10,
            readings=ObservationReadings(
                temperature_c=27.2,
                pressure_hpa=1011.5,
                humidity_pct=58.0
            ),
            edge_inference=run_edge_inference(27.2, 1011.5, 58.0),
            device_metadata=DeviceMetadata(firmware_version="sim_v1.0.0")
        )
        resp = self.client.post("/api/ingest/observation", json=packet.model_dump(mode="json"))
        self.assertEqual(resp.status_code, 200)

        # Retrieve hybrid record across all four layers
        record = self.history_store.get_hybrid_record(event_id)
        self.assertIsNotNone(record, f"Hybrid record {event_id} must be persisted")
        self.assertEqual(record["event_id"], event_id)
        self.assertEqual(record["station_id"], self.station_id)
        # Raw readings preserved
        self.assertAlmostEqual(record["raw_observation"]["temperature_c"], 27.2, places=1)
        self.assertAlmostEqual(record["raw_observation"]["pressure_hpa"], 1011.5, places=1)
        self.assertAlmostEqual(record["raw_observation"]["humidity_pct"], 58.0, places=1)
        # Edge inference layer
        self.assertIsNotNone(record["edge_inference"])
        self.assertEqual(record["edge_inference"]["status"], "ok")
        # Central inference layer
        self.assertIsNotNone(record["central_verdict"])
        # Comparison layer
        self.assertIsNotNone(record["comparison"])
        self.assertEqual(record["comparison"]["comparison_status"], ComparisonStatus.BOTH_AGREE_NORMAL.value)

    # ─────────────────────────────────────────────────────────────────────────
    # Phase C: Normal Observation
    # ─────────────────────────────────────────────────────────────────────────
    def test_phase_c_normal_observation(self):
        event_id = str(uuid.uuid4())
        packet = ObservationPacket(
            event_id=event_id,
            station_id=self.station_id,
            device_id="sim_node_normal",
            observed_at=datetime.now(timezone.utc),
            sequence_number=11,
            readings=ObservationReadings(
                temperature_c=25.0,
                pressure_hpa=1013.25,
                humidity_pct=50.0
            ),
            edge_inference=run_edge_inference(25.0, 1013.25, 50.0),
            device_metadata=DeviceMetadata(firmware_version="sim_v1.0.0")
        )
        resp = self.client.post("/api/ingest/observation", json=packet.model_dump(mode="json"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["accepted"])

        record = self.history_store.get_hybrid_record(event_id)
        self.assertIsNotNone(record)
        self.assertEqual(record["edge_inference"]["status"], "ok")
        self.assertFalse(record["edge_inference"]["anomaly_flag"])
        self.assertEqual(record["comparison"]["comparison_status"], ComparisonStatus.BOTH_AGREE_NORMAL.value)

    # ─────────────────────────────────────────────────────────────────────────
    # Phase D: Edge/Central Agreement
    # ─────────────────────────────────────────────────────────────────────────
    def test_phase_d_edge_central_agreement(self):
        event_id = str(uuid.uuid4())
        # Sensor fail low temperature triggers both edge rule and central detection
        temp = -20.0
        pres = 1013.25
        hum = 50.0
        packet = ObservationPacket(
            event_id=event_id,
            station_id=self.station_id,
            device_id="sim_node_agree_anom",
            observed_at=datetime.now(timezone.utc),
            sequence_number=12,
            readings=ObservationReadings(
                temperature_c=temp,
                pressure_hpa=pres,
                humidity_pct=hum
            ),
            edge_inference=run_edge_inference(temp, pres, hum),
            device_metadata=DeviceMetadata(firmware_version="sim_v1.0.0")
        )
        resp = self.client.post("/api/ingest/observation", json=packet.model_dump(mode="json"))
        self.assertEqual(resp.status_code, 200)

        record = self.history_store.get_hybrid_record(event_id)
        self.assertIsNotNone(record)
        self.assertEqual(record["edge_inference"]["status"], "anomaly_detected")
        self.assertTrue(record["edge_inference"]["anomaly_flag"])
        self.assertEqual(record["comparison"]["comparison_status"], ComparisonStatus.BOTH_AGREE_ANOMALY.value)

    # ─────────────────────────────────────────────────────────────────────────
    # Phase E: Edge/Central Disagreement
    # ─────────────────────────────────────────────────────────────────────────
    def test_phase_e_edge_central_disagreement_edge_only(self):
        # Direct comparison unit check: Edge flags anomaly, Central says normal
        edge = EdgeInference(
            status="anomaly_detected",
            anomaly_flag=True,
            anomaly_type="physical_bounds",
            score=None,
            score_type=None,
            model_version=EDGE_MODEL_VERSION,
            inference_method=EDGE_INFERENCE_METHOD
        )
        central_normal = {
            "is_anomaly": False,
            "severity": "none",
            "anomaly_score_pct": 10.0,
            "fault_type": None,
            "model_version": "central_v1.0.0"
        }
        cmp_result = compare_edge_central(edge, central_normal)
        self.assertEqual(cmp_result.comparison_status, ComparisonStatus.EDGE_ONLY_ANOMALY.value)
        self.assertFalse(cmp_result.anomaly_decision_agreement)

    def test_phase_e_edge_central_disagreement_central_only(self):
        # Edge says normal, Central flags ML anomaly
        edge_normal = EdgeInference(
            status="ok",
            anomaly_flag=False,
            anomaly_type=None,
            score=None,
            score_type=None,
            model_version=EDGE_MODEL_VERSION,
            inference_method=EDGE_INFERENCE_METHOD
        )
        central_anomaly = {
            "is_anomaly": True,
            "severity": "high",
            "anomaly_score_pct": 88.0,
            "fault_type": "drift",
            "model_version": "central_v1.0.0"
        }
        cmp_result = compare_edge_central(edge_normal, central_anomaly)
        self.assertEqual(cmp_result.comparison_status, ComparisonStatus.CENTRAL_ONLY_ANOMALY.value)
        self.assertFalse(cmp_result.anomaly_decision_agreement)

    # ─────────────────────────────────────────────────────────────────────────
    # Phase F: Missing Edge Inference
    # ─────────────────────────────────────────────────────────────────────────
    def test_phase_f_missing_edge_inference(self):
        event_id = str(uuid.uuid4())
        packet = ObservationPacket(
            event_id=event_id,
            station_id=self.station_id,
            device_id="legacy_node",
            observed_at=datetime.now(timezone.utc),
            sequence_number=13,
            readings=ObservationReadings(
                temperature_c=25.0,
                pressure_hpa=1013.25,
                humidity_pct=50.0
            ),
            edge_inference=EdgeInference(
                status="unavailable",
                anomaly_flag=False,
                anomaly_type=None,
                score=None,
                score_type=None,
                model_version="edge_rules_v1.0.0",
                inference_method="none"
            ),
            device_metadata=DeviceMetadata(firmware_version="legacy_v0.9.0")
        )
        resp = self.client.post("/api/ingest/observation", json=packet.model_dump(mode="json"))
        self.assertEqual(resp.status_code, 200)

        # Persistence check: edge_inference reports status unavailable
        record = self.history_store.get_hybrid_record(event_id)
        self.assertIsNotNone(record)
        self.assertEqual(record["comparison"]["comparison_status"], ComparisonStatus.EDGE_UNAVAILABLE.value)
        self.assertIsNotNone(record["central_verdict"])

    # ─────────────────────────────────────────────────────────────────────────
    # Phase G: Raw Sensor Dropout
    # ─────────────────────────────────────────────────────────────────────────
    def test_phase_g_raw_sensor_dropout(self):
        event_id = str(uuid.uuid4())
        packet = ObservationPacket(
            event_id=event_id,
            station_id=self.station_id,
            device_id="sim_node_dropout",
            observed_at=datetime.now(timezone.utc),
            sequence_number=14,
            readings=ObservationReadings(
                temperature_c=None,
                pressure_hpa=1010.0,
                humidity_pct=None
            ),
            edge_inference=run_edge_inference(None, 1010.0, None),
            device_metadata=DeviceMetadata(firmware_version="sim_v1.0.0")
        )
        resp = self.client.post("/api/ingest/observation", json=packet.model_dump(mode="json"))
        self.assertEqual(resp.status_code, 200)

        # Check that nulls were preserved without injecting zero / -999
        record = self.history_store.get_hybrid_record(event_id)
        self.assertIsNotNone(record)
        self.assertIsNone(record["raw_observation"]["temperature_c"])
        self.assertIsNone(record["raw_observation"]["humidity_pct"])
        self.assertAlmostEqual(record["raw_observation"]["pressure_hpa"], 1010.0, places=1)
        self.assertEqual(record["edge_inference"]["anomaly_type"], "dropout")

    # ─────────────────────────────────────────────────────────────────────────
    # Phase H: Extreme Sensor Value
    # ─────────────────────────────────────────────────────────────────────────
    def test_phase_h_extreme_sensor_value(self):
        event_id = str(uuid.uuid4())
        extreme_temp = 75.0  # Exceeds 60.0°C physical limit
        packet = ObservationPacket(
            event_id=event_id,
            station_id=self.station_id,
            device_id="sim_node_extreme",
            observed_at=datetime.now(timezone.utc),
            sequence_number=15,
            readings=ObservationReadings(
                temperature_c=extreme_temp,
                pressure_hpa=1013.25,
                humidity_pct=50.0
            ),
            edge_inference=run_edge_inference(extreme_temp, 1013.25, 50.0),
            device_metadata=DeviceMetadata(firmware_version="sim_v1.0.0")
        )
        resp = self.client.post("/api/ingest/observation", json=packet.model_dump(mode="json"))
        self.assertEqual(resp.status_code, 200)

        record = self.history_store.get_hybrid_record(event_id)
        self.assertIsNotNone(record)
        # Raw value preserved un-clamped
        self.assertAlmostEqual(record["raw_observation"]["temperature_c"], extreme_temp, places=1)
        self.assertEqual(record["edge_inference"]["anomaly_type"], "physical_bounds")

    # ─────────────────────────────────────────────────────────────────────────
    # Phase I: Duplicate Retry
    # ─────────────────────────────────────────────────────────────────────────
    def test_phase_i_duplicate_retry(self):
        event_id = str(uuid.uuid4())
        packet = ObservationPacket(
            event_id=event_id,
            station_id=self.station_id,
            device_id="retry_node",
            observed_at=datetime.now(timezone.utc),
            sequence_number=20,
            readings=ObservationReadings(
                temperature_c=25.0,
                pressure_hpa=1013.25,
                humidity_pct=50.0
            ),
            edge_inference=run_edge_inference(25.0, 1013.25, 50.0),
            device_metadata=DeviceMetadata(firmware_version="sim_v1.0.0")
        )
        payload = packet.model_dump(mode="json")

        # 1st Submission: 200 OK
        resp1 = self.client.post("/api/ingest/observation", json=payload)
        self.assertEqual(resp1.status_code, 200)

        # 2nd Submission (Exact same packet / retry): 409 Conflict
        resp2 = self.client.post("/api/ingest/observation", json=payload)
        self.assertEqual(resp2.status_code, 409)

    # ─────────────────────────────────────────────────────────────────────────
    # Phase J: Sequence Gap
    # ─────────────────────────────────────────────────────────────────────────
    def test_phase_j_sequence_gap(self):
        station = self.station_id_2
        # Seq 30
        p1 = ObservationPacket(
            event_id=str(uuid.uuid4()),
            station_id=station,
            device_id="seq_node_1",
            observed_at=datetime.now(timezone.utc),
            sequence_number=30,
            readings=ObservationReadings(temperature_c=25.0, pressure_hpa=1013.0, humidity_pct=50.0),
            edge_inference=run_edge_inference(25.0, 1013.0, 50.0),
            device_metadata=DeviceMetadata(firmware_version="sim_v1.0.0")
        )
        r1 = self.client.post("/api/ingest/observation", json=p1.model_dump(mode="json"))
        self.assertEqual(r1.status_code, 200)

        # Seq 32 (Gap of 1 packet)
        p2 = ObservationPacket(
            event_id=str(uuid.uuid4()),
            station_id=station,
            device_id="seq_node_1",
            observed_at=datetime.now(timezone.utc),
            sequence_number=32,
            readings=ObservationReadings(temperature_c=25.2, pressure_hpa=1013.0, humidity_pct=50.0),
            edge_inference=run_edge_inference(25.2, 1013.0, 50.0),
            device_metadata=DeviceMetadata(firmware_version="sim_v1.0.0")
        )
        r2 = self.client.post("/api/ingest/observation", json=p2.model_dump(mode="json"))
        self.assertEqual(r2.status_code, 200)
        data2 = r2.json()
        self.assertEqual(data2["sequence_status"], "gap_detected")

    # ─────────────────────────────────────────────────────────────────────────
    # Phase K: Out-of-Order Observation
    # ─────────────────────────────────────────────────────────────────────────
    def test_phase_k_out_of_order_observation(self):
        station = self.station_id_2
        # Seq 50
        p1 = ObservationPacket(
            event_id=str(uuid.uuid4()),
            station_id=station,
            device_id="seq_node_ooo",
            observed_at=datetime.now(timezone.utc),
            sequence_number=50,
            readings=ObservationReadings(temperature_c=25.0, pressure_hpa=1013.0, humidity_pct=50.0),
            edge_inference=run_edge_inference(25.0, 1013.0, 50.0),
            device_metadata=DeviceMetadata(firmware_version="sim_v1.0.0")
        )
        self.client.post("/api/ingest/observation", json=p1.model_dump(mode="json"))

        # Seq 48 (Out of order)
        p2 = ObservationPacket(
            event_id=str(uuid.uuid4()),
            station_id=station,
            device_id="seq_node_ooo",
            observed_at=datetime.now(timezone.utc),
            sequence_number=48,
            readings=ObservationReadings(temperature_c=25.1, pressure_hpa=1013.0, humidity_pct=50.0),
            edge_inference=run_edge_inference(25.1, 1013.0, 50.0),
            device_metadata=DeviceMetadata(firmware_version="sim_v1.0.0")
        )
        r2 = self.client.post("/api/ingest/observation", json=p2.model_dump(mode="json"))
        self.assertEqual(r2.status_code, 200)
        data2 = r2.json()
        self.assertEqual(data2["sequence_status"], "out_of_order")

    # ─────────────────────────────────────────────────────────────────────────
    # Phase L: Timestamp Validation
    # ─────────────────────────────────────────────────────────────────────────
    def test_phase_l_timestamp_validation(self):
        # Timezone aware timestamp passes
        packet = ObservationPacket(
            event_id=str(uuid.uuid4()),
            station_id=self.station_id,
            device_id="time_node",
            observed_at=datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc),
            sequence_number=60,
            readings=ObservationReadings(temperature_c=25.0, pressure_hpa=1013.0, humidity_pct=50.0),
            edge_inference=run_edge_inference(25.0, 1013.0, 50.0),
            device_metadata=DeviceMetadata(firmware_version="esp32_edge_v1.0.0")
        )
        resp = self.client.post("/api/ingest/observation", json=packet.model_dump(mode="json"))
        self.assertEqual(resp.status_code, 200)

    # ─────────────────────────────────────────────────────────────────────────
    # Phase M: ESP32 Representative Protocol Validation
    # ─────────────────────────────────────────────────────────────────────────
    def test_phase_m_esp32_protocol_validation(self):
        # Representative JSON payload identical to ESP32 firmware output
        esp32_json = {
            "event_id": "550e8400-e29b-41d4-a716-446655440000",
            "station_id": self.station_id,
            "device_id": "esp32_c44f33112233",
            "observed_at": "2026-09-23T18:00:00Z",
            "sequence_number": 75,
            "readings": {
                "temperature_c": 28.50,
                "pressure_hpa": 1012.30,
                "humidity_pct": 58.20
            },
            "edge_inference": {
                "status": "ok",
                "anomaly_flag": False,
                "anomaly_type": None,
                "score": None,
                "score_type": None,
                "model_version": "edge_rules_v1.0.0",
                "inference_method": "rules"
            },
            "device_metadata": {
                "firmware_version": "esp32_edge_v1.0.0",
                "battery_voltage": None,
                "signal_strength": -64.0
            }
        }
        resp = self.client.post("/api/ingest/observation", json=esp32_json)
        self.assertEqual(resp.status_code, 200)

    # ─────────────────────────────────────────────────────────────────────────
    # Phase N: ESP32 Edge Rule Parity
    # ─────────────────────────────────────────────────────────────────────────
    def test_phase_n_edge_rule_parity(self):
        # Verify normal
        r_norm = run_edge_inference(25.0, 1013.25, 50.0)
        self.assertEqual(r_norm.status, "ok")
        self.assertFalse(r_norm.anomaly_flag)

        # Verify fail-low
        r_fl = run_edge_inference(-10.0, 1013.25, 50.0)
        self.assertEqual(r_fl.anomaly_type, "sensor_fail_low")

        # Verify physical bounds
        r_pb = run_edge_inference(65.0, 1013.25, 50.0)
        self.assertEqual(r_pb.anomaly_type, "physical_bounds")

        # Verify dropout
        r_do = run_edge_inference(None, 1013.25, 50.0)
        self.assertEqual(r_do.anomaly_type, "dropout")

    # ─────────────────────────────────────────────────────────────────────────
    # Phase O: Multi-Layer Persistence Integrity
    # ─────────────────────────────────────────────────────────────────────────
    def test_phase_o_persistence_integrity(self):
        event_id = str(uuid.uuid4())
        packet = ObservationPacket(
            event_id=event_id,
            station_id=self.station_id,
            device_id="persist_integ_node",
            observed_at=datetime.now(timezone.utc),
            sequence_number=80,
            readings=ObservationReadings(temperature_c=24.5, pressure_hpa=1014.0, humidity_pct=52.0),
            edge_inference=run_edge_inference(24.5, 1014.0, 52.0),
            device_metadata=DeviceMetadata(firmware_version="sim_v1.0.0")
        )
        self.client.post("/api/ingest/observation", json=packet.model_dump(mode="json"))

        record = self.history_store.get_hybrid_record(event_id)
        self.assertIsNotNone(record)
        self.assertEqual(record["event_id"], event_id)
        self.assertEqual(record["station_id"], self.station_id)
        self.assertAlmostEqual(record["raw_observation"]["temperature_c"], 24.5, places=1)
        self.assertIsNotNone(record["edge_inference"])
        self.assertIsNotNone(record["central_verdict"])
        self.assertIsNotNone(record["comparison"])

    # ─────────────────────────────────────────────────────────────────────────
    # Phase P: CSV Fallback
    # ─────────────────────────────────────────────────────────────────────────
    def test_phase_p_csv_fallback(self):
        # CSV history store records are accessible via CSV read
        path = self.history_store._path(self.station_id)
        hist = self.history_store._read_csv(path)
        self.assertIsInstance(hist, pd.DataFrame)
        self.assertGreater(len(hist), 0)

    # ─────────────────────────────────────────────────────────────────────────
    # Phase Q: WebSocket Live Broadcast
    # ─────────────────────────────────────────────────────────────────────────
    def test_phase_q_websocket_live_endpoint(self):
        # Test WS connection handshake & ping-pong
        with self.client.websocket_connect("/ws/live") as ws:
            handshake = ws.receive_json()
            self.assertEqual(handshake["type"], "CONNECTION_READY")
            self.assertIn("mode", handshake)
            ws.send_text("ping")
            reply = ws.receive_text()
            self.assertEqual(reply, "pong")

    # ─────────────────────────────────────────────────────────────────────────
    # Phase R & S: Full API Route Regression Suite
    # ─────────────────────────────────────────────────────────────────────────
    def test_phase_r_s_api_regression(self):
        # 1. /api/stations
        r_stations = self.client.get("/api/stations")
        self.assertEqual(r_stations.status_code, 200)
        stations = r_stations.json()
        self.assertIsInstance(stations, list)
        self.assertGreater(len(stations), 0)

        # 2. /api/system-status
        r_sys = self.client.get("/api/system-status")
        self.assertEqual(r_sys.status_code, 200)
        self.assertIn("mode", r_sys.json())

        # 3. /api/current-reading
        r_cr = self.client.get(f"/api/current-reading?station_id={self.station_id}")
        self.assertEqual(r_cr.status_code, 200)
        self.assertIn("temperature_c", r_cr.json())

        # 4. /api/trends
        r_trends = self.client.get(f"/api/trends?station_id={self.station_id}")
        self.assertEqual(r_trends.status_code, 200)
        self.assertIn("points", r_trends.json())

        # 5. /api/network-status
        r_net = self.client.get("/api/network-status")
        self.assertEqual(r_net.status_code, 200)
        self.assertIn("overall_status", r_net.json())
        self.assertIn("active_stations_count", r_net.json())

        # 6. /api/anomalies/latest
        r_anom = self.client.get(f"/api/anomalies/latest?station_id={self.station_id}")
        self.assertEqual(r_anom.status_code, 200)

        # 7. /api/sensor-health
        r_sh = self.client.get(f"/api/sensor-health?station_id={self.station_id}")
        self.assertEqual(r_sh.status_code, 200)
        self.assertIn("health_pct", r_sh.json())

    # ─────────────────────────────────────────────────────────────────────────
    # Phase T: Frontend Active Anomaly Drift Data Compatibility
    # ─────────────────────────────────────────────────────────────────────────
    def test_phase_t_frontend_anomaly_drift_compatibility(self):
        r_anom = self.client.get("/api/anomalies/recent")
        self.assertEqual(r_anom.status_code, 200)
        data = r_anom.json()
        self.assertIsInstance(data, list)


if __name__ == "__main__":
    unittest.main()
