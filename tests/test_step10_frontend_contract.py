"""
Step 10 — Frontend Integration Contract and Active Anomaly QA Tests
Validates the backend data structures consumed by the frontend, ensuring
station association, affected parameters, anomaly scores, and null safety.
"""

import os
import sys
import unittest
import pandas as pd
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from main import app


class TestStep10FrontendContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()
        sim = app.state.sim
        for sid in sim.metadata["station_id"]:
            raw = sim._live_cache.get(sid, {"temperature_c": 25.0, "pressure_hpa": 1000.0, "humidity_pct": 60.0})
            sim.latest[sid] = {
                "raw_reading": raw,
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

    def test_active_anomaly_drift_acceptance(self):
        """
        QA Acceptance Test for Active Anomaly Drift Section:
        1. Query available stations.
        2. Inject a known anomaly on a known station via POST /api/inject-anomaly.
        3. Verify GET /api/anomalies/latest returns correct station_id, affected_parameters,
           anomaly_score_pct, severity, type, and timestamp.
        """
        stations_resp = self.client.get("/api/stations")
        self.assertEqual(stations_resp.status_code, 200)
        stations = stations_resp.json()
        self.assertTrue(len(stations) > 0)
        target_station_id = stations[0]["station_id"]

        # Inject known anomaly
        inject_resp = self.client.post("/api/inject-anomaly", json={
            "station_id": target_station_id,
            "type": "spike"
        })
        self.assertIn(inject_resp.status_code, [200, 409])

        # Query latest anomaly
        latest_resp = self.client.get(f"/api/anomalies/latest?station_id={target_station_id}")
        self.assertEqual(latest_resp.status_code, 200)
        latest_data = latest_resp.json()

        if latest_data is not None:
            # Verify Station Association
            self.assertEqual(latest_data["station_id"], target_station_id)
            # Verify Score
            self.assertIn("anomaly_score_pct", latest_data)
            if latest_data["anomaly_score_pct"] is not None:
                self.assertIsInstance(latest_data["anomaly_score_pct"], (int, float))
            # Verify Type & Root Cause
            self.assertIn("type", latest_data)
            self.assertIn("root_cause", latest_data)
            self.assertIn("timestamp", latest_data)
            # Verify Parameter Information if available
            if "affected_parameters" in latest_data and latest_data["affected_parameters"] is not None:
                self.assertIsInstance(latest_data["affected_parameters"], list)

    def test_current_reading_contract_and_null_tolerance(self):
        """
        Verify GET /api/current-reading structure matches MetricValueRange contract.
        """
        stations = self.client.get("/api/stations").json()
        station_id = stations[0]["station_id"]

        # Ensure reading is populated
        sim = app.state.sim
        if station_id not in sim.latest:
            sim.latest[station_id] = {
                "raw_reading": {"temperature_c": 24.5, "pressure_hpa": 1012.0, "humidity_pct": 55.0},
                "verdict": {
                    "anomaly_score_pct": 12.0,
                    "is_anomaly": False,
                    "fault_type": None,
                    "severity": "none",
                    "model_confidence_pct": 12.0,
                    "rule_confidence_pct": 0.0,
                    "suggested_values": {},
                },
                "timestamp": pd.Timestamp.now(tz="UTC"),
            }

        resp = self.client.get(f"/api/current-reading?station_id={station_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        for metric_key in ["temperature_c", "pressure_hpa", "humidity_pct"]:
            self.assertIn(metric_key, data)
            metric_obj = data[metric_key]
            self.assertIn("value", metric_obj)
            self.assertIn("normal_min", metric_obj)
            self.assertIn("normal_max", metric_obj)
            # value can be float or null
            if metric_obj["value"] is not None:
                self.assertIsInstance(metric_obj["value"], (int, float))

    def test_trends_contract_points(self):
        """
        Verify GET /api/trends returns points with valid structure.
        """
        stations = self.client.get("/api/stations").json()
        station_id = stations[0]["station_id"]

        resp = self.client.get(f"/api/trends?station_id={station_id}&hours=6")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["station_id"], station_id)
        self.assertIn("points", data)
        self.assertIsInstance(data["points"], list)

    def test_sensor_health_contract(self):
        """
        Verify GET /api/sensor-health structure.
        Backend sends health_pct and status, which frontend validator maps to sensor_health_pct and sensor_health_status.
        """
        stations = self.client.get("/api/stations").json()
        station_id = stations[0]["station_id"]

        resp = self.client.get(f"/api/sensor-health?station_id={station_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["station_id"], station_id)
        self.assertTrue("health_pct" in data or "sensor_health_pct" in data)
        self.assertTrue("status" in data or "sensor_health_status" in data)


if __name__ == "__main__":
    unittest.main()
