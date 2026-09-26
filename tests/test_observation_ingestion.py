"""
tests/test_observation_ingestion.py

Unit and integration tests for the Observation Ingestion API (POST /api/ingest/observation).

Verifies:
1. Valid observation accepted
2. Invalid schema rejected (422)
3. Unknown station handled correctly (404)
4. Duplicate event_id rejected (409)
5. First event accepted (sequence_status='initial')
6. Sequence gap accepted and reported (sequence_status='gap_detected')
7. Repeated/duplicate sequence handled (sequence_status='repeated_sequence')
8. Out-of-order observation accepted and reported (sequence_status='out_of_order')
9. Null temperature accepted
10. Null pressure accepted
11. Null humidity accepted
12. Extreme temperature preserved verbatim
13. Extreme pressure preserved verbatim
14. Extreme humidity preserved verbatim
15. Edge anomaly metadata preserved
16. observed_at timestamp preserved exactly
17. device_id preserved
18. Response contains event_id
19. Response contains station_id
20. Response contains ingestion_status
21. Existing APIs remain functional
"""

import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
import pandas as pd
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from main import app


class TestObservationIngestion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()
        # Ensure simulator is ready
        sim = app.state.sim
        for sid in sim.metadata["station_id"]:
            if sid not in sim.latest:
                sim.latest[sid] = {
                    "raw_reading": {"temperature_c": 25.0, "pressure_hpa": 1000.0, "humidity_pct": 60.0},
                    "verdict": {
                        "anomaly_score_pct": 5.0,
                        "is_anomaly": False,
                        "fault_type": None,
                        "severity": "none",
                        "model_confidence_pct": 5.0,
                        "rule_confidence_pct": 0.0,
                        "suggested_values": {},
                    },
                    "timestamp": pd.Timestamp.now(tz="UTC"),
                }

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)

    _seq_counter = 0

    def _build_payload(self, **overrides):
        TestObservationIngestion._seq_counter += 1
        cnt = TestObservationIngestion._seq_counter
        base_time = datetime(2026, 9, 23, 10, 0, 0, tzinfo=timezone.utc)
        cur_time = base_time + timedelta(minutes=cnt * 10)
        base = {
            "event_id": f"evt-{cnt}-{datetime.now(timezone.utc).timestamp()}",
            "station_id": "AWS-CHN-024",
            "device_id": "esp32-node-chennai-01",
            "observed_at": cur_time.isoformat(),
            "sequence_number": 100 + cnt,
            "readings": {
                "temperature_c": 28.5,
                "pressure_hpa": 1012.0,
                "humidity_pct": 65.0,
            },
            "edge_inference": {
                "status": "ok",
                "anomaly_flag": False,
                "anomaly_type": None,
                "score": None,
                "score_type": None,
                "model_version": "edge_rules_v1.0.0",
                "inference_method": "rules",
            },
            "device_metadata": {
                "firmware_version": "1.0.0",
                "battery_voltage": 3.9,
                "signal_strength": -68.0,
            },
        }
        base.update(overrides)
        return base

    # 1. Valid observation accepted
    def test_valid_observation_accepted(self):
        payload = self._build_payload(event_id="evt-valid-001", sequence_number=1)
        resp = self.client.post("/api/ingest/observation", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["accepted"])
        self.assertEqual(data["event_id"], "evt-valid-001")
        self.assertEqual(data["station_id"], "AWS-CHN-024")
        self.assertEqual(data["device_id"], "esp32-node-chennai-01")
        self.assertEqual(data["ingestion_status"], "processed")

    # 2. Invalid schema rejected (422)
    def test_invalid_schema_rejected(self):
        # Missing required readings object
        bad_payload = self._build_payload(event_id="evt-bad-001")
        del bad_payload["readings"]
        resp = self.client.post("/api/ingest/observation", json=bad_payload)
        self.assertEqual(resp.status_code, 422)

        # Naive datetime
        bad_dt_payload = self._build_payload(event_id="evt-bad-002", observed_at="2026-09-23 14:00:00")
        resp_dt = self.client.post("/api/ingest/observation", json=bad_dt_payload)
        self.assertEqual(resp_dt.status_code, 422)

    # 3. Unknown station handled correctly (404)
    def test_unknown_station_handled(self):
        payload = self._build_payload(event_id="evt-unk-001", station_id="AWS-NONEXISTENT-999")
        resp = self.client.post("/api/ingest/observation", json=payload)
        self.assertEqual(resp.status_code, 404)
        self.assertIn("Unknown station_id", resp.json()["detail"])

    # 4. Duplicate event_id rejected (409)
    def test_duplicate_event_id_rejected(self):
        payload = self._build_payload(event_id="evt-dup-unique-123", sequence_number=10)
        # First attempt -> 200
        resp1 = self.client.post("/api/ingest/observation", json=payload)
        self.assertEqual(resp1.status_code, 200)

        # Second attempt with same event_id -> 409
        resp2 = self.client.post("/api/ingest/observation", json=payload)
        self.assertEqual(resp2.status_code, 409)
        self.assertIn("Duplicate event_id", resp2.json()["detail"])

    # 5. First event accepted (sequence_status='initial')
    def test_first_event_initial_sequence(self):
        payload = self._build_payload(
            event_id="evt-init-seq-001",
            station_id="AWS-DEL-011",
            device_id="esp32-fresh-device-01",
            sequence_number=50,
        )
        resp = self.client.post("/api/ingest/observation", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["sequence_status"], "initial")

    # 6. Sequence gap accepted and reported (sequence_status='gap_detected')
    def test_sequence_gap_accepted_and_reported(self):
        device_id = "esp32-gap-test-device"
        # Seed initial
        self.client.post(
            "/api/ingest/observation",
            json=self._build_payload(event_id="evt-gap-1", device_id=device_id, sequence_number=10),
        )
        # Sequence jump from 10 to 15 (gap of 4 packets)
        resp = self.client.post(
            "/api/ingest/observation",
            json=self._build_payload(event_id="evt-gap-2", device_id=device_id, sequence_number=15),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["accepted"])
        self.assertEqual(resp.json()["sequence_status"], "gap_detected")

    # 7. Repeated/duplicate sequence handled (sequence_status='repeated_sequence')
    def test_repeated_sequence_number_handled(self):
        device_id = "esp32-repeat-test-device"
        self.client.post(
            "/api/ingest/observation",
            json=self._build_payload(event_id="evt-rep-1", device_id=device_id, sequence_number=20),
        )
        # Same sequence number 20 with different event_id
        resp = self.client.post(
            "/api/ingest/observation",
            json=self._build_payload(event_id="evt-rep-2", device_id=device_id, sequence_number=20),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["sequence_status"], "repeated_sequence")

    # 8. Out-of-order observation accepted and reported
    def test_out_of_order_observation(self):
        device_id = "esp32-ooo-test-device"
        self.client.post(
            "/api/ingest/observation",
            json=self._build_payload(event_id="evt-ooo-1", device_id=device_id, sequence_number=30),
        )
        # Sequence number 25 arrives after 30
        resp = self.client.post(
            "/api/ingest/observation",
            json=self._build_payload(event_id="evt-ooo-2", device_id=device_id, sequence_number=25),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["sequence_status"], "out_of_order")

    # 9. Null temperature accepted
    def test_null_temperature_accepted(self):
        payload = self._build_payload(
            event_id="evt-null-temp-01",
            readings={"temperature_c": None, "pressure_hpa": 1010.0, "humidity_pct": 70.0},
        )
        resp = self.client.post("/api/ingest/observation", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["accepted"])

    # 10. Null pressure accepted
    def test_null_pressure_accepted(self):
        payload = self._build_payload(
            event_id="evt-null-pres-01",
            readings={"temperature_c": 26.0, "pressure_hpa": None, "humidity_pct": 70.0},
        )
        resp = self.client.post("/api/ingest/observation", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["accepted"])

    # 11. Null humidity accepted
    def test_null_humidity_accepted(self):
        payload = self._build_payload(
            event_id="evt-null-hum-01",
            readings={"temperature_c": 26.0, "pressure_hpa": 1010.0, "humidity_pct": None},
        )
        resp = self.client.post("/api/ingest/observation", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["accepted"])

    # 12. Extreme temperature preserved verbatim
    def test_extreme_temperature_preserved(self):
        payload = self._build_payload(
            event_id="evt-ext-temp-01",
            readings={"temperature_c": 70.0, "pressure_hpa": 1010.0, "humidity_pct": 60.0},
        )
        resp = self.client.post("/api/ingest/observation", json=payload)
        self.assertEqual(resp.status_code, 200)
        sim = app.state.sim
        latest_reading = sim.latest["AWS-CHN-024"]["raw_reading"]
        self.assertEqual(latest_reading["temperature_c"], 70.0)

    # 13. Extreme pressure preserved verbatim
    def test_extreme_pressure_preserved(self):
        payload = self._build_payload(
            event_id="evt-ext-pres-01",
            readings={"temperature_c": 25.0, "pressure_hpa": 1200.0, "humidity_pct": 60.0},
        )
        resp = self.client.post("/api/ingest/observation", json=payload)
        self.assertEqual(resp.status_code, 200)
        sim = app.state.sim
        latest_reading = sim.latest["AWS-CHN-024"]["raw_reading"]
        self.assertEqual(latest_reading["pressure_hpa"], 1200.0)

    # 14. Extreme humidity preserved verbatim
    def test_extreme_humidity_preserved(self):
        payload = self._build_payload(
            event_id="evt-ext-hum-01",
            readings={"temperature_c": 25.0, "pressure_hpa": 1010.0, "humidity_pct": 150.0},
        )
        resp = self.client.post("/api/ingest/observation", json=payload)
        self.assertEqual(resp.status_code, 200)
        sim = app.state.sim
        latest_reading = sim.latest["AWS-CHN-024"]["raw_reading"]
        self.assertEqual(latest_reading["humidity_pct"], 150.0)

    # 15. Edge anomaly metadata preserved
    def test_edge_anomaly_metadata_preserved(self):
        edge_inf = {
            "status": "anomaly_detected",
            "anomaly_flag": True,
            "anomaly_type": "physical_bounds",
            "score": None,
            "score_type": None,
            "model_version": "edge_rules_v1.0.0",
            "inference_method": "rules",
        }
        payload = self._build_payload(event_id="evt-edge-pres-01", edge_inference=edge_inf)
        resp = self.client.post("/api/ingest/observation", json=payload)
        self.assertEqual(resp.status_code, 200)
        sim = app.state.sim
        latest = sim.latest["AWS-CHN-024"]
        self.assertEqual(latest["edge_inference"]["anomaly_flag"], True)
        self.assertEqual(latest["edge_inference"]["anomaly_type"], "physical_bounds")

    # 16. observed_at preserved exactly
    def test_observed_at_preserved_exactly(self):
        ts_str = "2026-09-23T14:35:12+05:30"
        payload = self._build_payload(event_id="evt-ts-pres-01", observed_at=ts_str)
        resp = self.client.post("/api/ingest/observation", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["observed_at"], "2026-09-23T14:35:12+05:30")

    # 17. device_id preserved
    def test_device_id_preserved(self):
        payload = self._build_payload(event_id="evt-dev-pres-01", device_id="custom-esp32-dev-99")
        resp = self.client.post("/api/ingest/observation", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["device_id"], "custom-esp32-dev-99")

    # 18. Response contains event_id
    def test_response_contains_event_id(self):
        payload = self._build_payload(event_id="evt-resp-id-01")
        resp = self.client.post("/api/ingest/observation", json=payload)
        self.assertIn("event_id", resp.json())
        self.assertEqual(resp.json()["event_id"], "evt-resp-id-01")

    # 19. Response contains station_id
    def test_response_contains_station_id(self):
        payload = self._build_payload(event_id="evt-resp-sid-01", station_id="AWS-CHN-024")
        resp = self.client.post("/api/ingest/observation", json=payload)
        self.assertIn("station_id", resp.json())
        self.assertEqual(resp.json()["station_id"], "AWS-CHN-024")

    # 20. Response contains ingestion status
    def test_response_contains_ingestion_status(self):
        payload = self._build_payload(event_id="evt-resp-stat-01")
        resp = self.client.post("/api/ingest/observation", json=payload)
        self.assertIn("ingestion_status", resp.json())
        self.assertEqual(resp.json()["ingestion_status"], "processed")

    # 21. Existing APIs remain functional
    def test_existing_apis_remain_functional(self):
        # 1. /api/stations
        res_stations = self.client.get("/api/stations")
        self.assertEqual(res_stations.status_code, 200)
        self.assertTrue(len(res_stations.json()) > 0)

        # 2. /api/system-status
        res_sys = self.client.get("/api/system-status")
        self.assertEqual(res_sys.status_code, 200)
        self.assertEqual(res_sys.json()["mode"], "live")

        # 3. /api/current-reading
        res_cur = self.client.get("/api/current-reading?station_id=AWS-CHN-024")
        self.assertEqual(res_cur.status_code, 200)
        self.assertIn("temperature_c", res_cur.json())

        # 4. /api/trends
        res_trends = self.client.get("/api/trends?station_id=AWS-CHN-024&hours=6")
        self.assertEqual(res_trends.status_code, 200)

        # 5. /api/network-status
        res_net = self.client.get("/api/network-status")
        self.assertEqual(res_net.status_code, 200)


if __name__ == "__main__":
    unittest.main()
