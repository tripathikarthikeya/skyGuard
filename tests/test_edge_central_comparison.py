"""
tests/test_edge_central_comparison.py

Unit and integration tests for the Edge vs. Central AI Comparison Layer.

Verifies:
1. edge anomaly + central anomaly -> BOTH_AGREE_ANOMALY
2. edge normal + central normal -> BOTH_AGREE_NORMAL
3. edge anomaly + central normal -> EDGE_ONLY_ANOMALY
4. edge normal + central anomaly -> CENTRAL_ONLY_ANOMALY
5. missing edge inference -> EDGE_UNAVAILABLE
6. missing central result -> CENTRAL_UNAVAILABLE
7. different anomaly types but both anomaly=true -> anomaly decision agrees, type agreement is False
8. matching anomaly types and both anomaly=true -> anomaly decision agrees, type agreement is True
9. edge score present but central score absent -> preserves edge score, central score is None
10. incompatible score types/scales preserved independently without invented score difference
11. null readings are not modified by comparison
12. extreme raw values remain unchanged
13. comparison is deterministic
14. comparison does not mutate input objects/dictionaries
15. both edge and central unavailable -> INSUFFICIENT_EVIDENCE
16. edge status 'unavailable' -> EDGE_UNAVAILABLE
17. integration via POST /api/ingest/observation stores comparison in simulator latest state
"""

import copy
import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from model.contracts import EdgeInference, ObservationPacket
from model.edge_central_comparison import ComparisonStatus, compare_edge_central
from main import app


class TestEdgeCentralComparison(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)

    _seq_counter = 1000

    def _make_edge_inference(
        self,
        status: str = "ok",
        anomaly_flag: bool = False,
        anomaly_type: str = None,
        score: float = None,
        score_type: str = None,
        model_version: str = "edge_rules_v1.0.0",
        inference_method: str = "rules",
    ) -> EdgeInference:
        return EdgeInference(
            status=status,
            anomaly_flag=anomaly_flag,
            anomaly_type=anomaly_type,
            score=score,
            score_type=score_type,
            model_version=model_version,
            inference_method=inference_method,
        )

    def _make_central_verdict(
        self,
        is_anomaly: bool = False,
        fault_type: str = None,
        anomaly_score_pct: float = 5.0,
        severity: str = "none",
    ) -> dict:
        return {
            "is_anomaly": is_anomaly,
            "fault_type": fault_type,
            "anomaly_score_pct": anomaly_score_pct,
            "severity": severity,
            "model_confidence_pct": anomaly_score_pct,
            "rule_confidence_pct": 0.0,
            "rules_fired": [],
            "suggested_values": {},
        }

    # 1. edge anomaly + central anomaly -> BOTH_AGREE_ANOMALY
    def test_both_agree_anomaly(self):
        edge = self._make_edge_inference(status="anomaly_detected", anomaly_flag=True, anomaly_type="physical_bounds")
        central = self._make_central_verdict(is_anomaly=True, fault_type="physical_bounds", anomaly_score_pct=95.0)

        res = compare_edge_central(edge, central)
        self.assertEqual(res.comparison_status, ComparisonStatus.BOTH_AGREE_ANOMALY.value)
        self.assertTrue(res.edge_anomaly_flag)
        self.assertTrue(res.central_anomaly_flag)
        self.assertTrue(res.anomaly_decision_agreement)
        self.assertTrue(res.type_agreement)

    # 2. edge normal + central normal -> BOTH_AGREE_NORMAL
    def test_both_agree_normal(self):
        edge = self._make_edge_inference(status="ok", anomaly_flag=False)
        central = self._make_central_verdict(is_anomaly=False, fault_type=None, anomaly_score_pct=8.0)

        res = compare_edge_central(edge, central)
        self.assertEqual(res.comparison_status, ComparisonStatus.BOTH_AGREE_NORMAL.value)
        self.assertFalse(res.edge_anomaly_flag)
        self.assertFalse(res.central_anomaly_flag)
        self.assertTrue(res.anomaly_decision_agreement)
        self.assertIsNone(res.type_agreement)

    # 3. edge anomaly + central normal -> EDGE_ONLY_ANOMALY
    def test_edge_only_anomaly(self):
        edge = self._make_edge_inference(status="anomaly_detected", anomaly_flag=True, anomaly_type="dropout")
        central = self._make_central_verdict(is_anomaly=False, fault_type=None, anomaly_score_pct=12.0)

        res = compare_edge_central(edge, central)
        self.assertEqual(res.comparison_status, ComparisonStatus.EDGE_ONLY_ANOMALY.value)
        self.assertTrue(res.edge_anomaly_flag)
        self.assertFalse(res.central_anomaly_flag)
        self.assertFalse(res.anomaly_decision_agreement)
        self.assertIsNone(res.type_agreement)

    # 4. edge normal + central anomaly -> CENTRAL_ONLY_ANOMALY
    def test_central_only_anomaly(self):
        edge = self._make_edge_inference(status="ok", anomaly_flag=False)
        central = self._make_central_verdict(is_anomaly=True, fault_type="drift", anomaly_score_pct=85.0)

        res = compare_edge_central(edge, central)
        self.assertEqual(res.comparison_status, ComparisonStatus.CENTRAL_ONLY_ANOMALY.value)
        self.assertFalse(res.edge_anomaly_flag)
        self.assertTrue(res.central_anomaly_flag)
        self.assertFalse(res.anomaly_decision_agreement)
        self.assertIsNone(res.type_agreement)

    # 5. missing edge inference -> EDGE_UNAVAILABLE
    def test_missing_edge_inference(self):
        central = self._make_central_verdict(is_anomaly=False, anomaly_score_pct=10.0)

        res = compare_edge_central(None, central)
        self.assertEqual(res.comparison_status, ComparisonStatus.EDGE_UNAVAILABLE.value)
        self.assertIsNone(res.edge_anomaly_flag)
        self.assertFalse(res.central_anomaly_flag)
        self.assertIsNone(res.anomaly_decision_agreement)

    # 6. missing central result -> CENTRAL_UNAVAILABLE
    def test_missing_central_result(self):
        edge = self._make_edge_inference(status="ok", anomaly_flag=False)

        res = compare_edge_central(edge, None)
        self.assertEqual(res.comparison_status, ComparisonStatus.CENTRAL_UNAVAILABLE.value)
        self.assertFalse(res.edge_anomaly_flag)
        self.assertIsNone(res.central_anomaly_flag)
        self.assertIsNone(res.anomaly_decision_agreement)

    # 7. different anomaly types but both anomaly=true -> anomaly decision agrees, type agreement is False
    def test_different_anomaly_types_both_true(self):
        edge = self._make_edge_inference(status="anomaly_detected", anomaly_flag=True, anomaly_type="physical_bounds")
        central = self._make_central_verdict(is_anomaly=True, fault_type="drift", anomaly_score_pct=88.0)

        res = compare_edge_central(edge, central)
        self.assertEqual(res.comparison_status, ComparisonStatus.BOTH_AGREE_ANOMALY.value)
        self.assertTrue(res.anomaly_decision_agreement)
        self.assertFalse(res.type_agreement)
        self.assertEqual(res.edge_anomaly_type, "physical_bounds")
        self.assertEqual(res.central_anomaly_type, "drift")

    # 8. matching anomaly types and both anomaly=true -> type_agreement is True
    def test_matching_anomaly_types_both_true(self):
        edge = self._make_edge_inference(status="anomaly_detected", anomaly_flag=True, anomaly_type="dropout")
        central = self._make_central_verdict(is_anomaly=True, fault_type="dropout", anomaly_score_pct=99.0)

        res = compare_edge_central(edge, central)
        self.assertEqual(res.comparison_status, ComparisonStatus.BOTH_AGREE_ANOMALY.value)
        self.assertTrue(res.anomaly_decision_agreement)
        self.assertTrue(res.type_agreement)

    # 9. edge score present but central score absent -> preserves edge score, central score is None
    def test_edge_score_present_central_score_absent(self):
        edge = self._make_edge_inference(
            status="ok",
            anomaly_flag=False,
            score=0.15,
            score_type="raw_distance",
        )
        central = {"is_anomaly": False, "fault_type": None, "anomaly_score_pct": None}

        res = compare_edge_central(edge, central)
        self.assertEqual(res.edge_score, 0.15)
        self.assertEqual(res.edge_score_type, "raw_distance")
        self.assertIsNone(res.central_score)

    # 10. incompatible score types/scales preserved independently without invented score difference
    def test_incompatible_scores_preserved_independently(self):
        edge = self._make_edge_inference(
            status="anomaly_detected",
            anomaly_flag=True,
            score=3.45,
            score_type="z_score",
        )
        central = self._make_central_verdict(is_anomaly=True, anomaly_score_pct=92.5)

        res = compare_edge_central(edge, central)
        self.assertEqual(res.edge_score, 3.45)
        self.assertEqual(res.edge_score_type, "z_score")
        self.assertEqual(res.central_score, 92.5)
        self.assertEqual(res.central_score_type, "percentage")
        # Ensure no invented score difference field exists on model
        self.assertFalse(hasattr(res, "score_difference"))

    # 11. null readings are not modified by comparison
    def test_null_readings_unmodified(self):
        readings = {"temperature_c": None, "pressure_hpa": 1013.25, "humidity_pct": None}
        readings_copy = copy.deepcopy(readings)

        edge = self._make_edge_inference(status="anomaly_detected", anomaly_flag=True, anomaly_type="dropout")
        central = self._make_central_verdict(is_anomaly=True, fault_type="dropout")

        compare_edge_central(edge, central)
        self.assertEqual(readings, readings_copy)

    # 12. extreme raw values remain unchanged
    def test_extreme_raw_values_unmodified(self):
        readings = {"temperature_c": 75.0, "pressure_hpa": 1250.0, "humidity_pct": 140.0}
        readings_copy = copy.deepcopy(readings)

        edge = self._make_edge_inference(status="anomaly_detected", anomaly_flag=True, anomaly_type="physical_bounds")
        central = self._make_central_verdict(is_anomaly=True, fault_type="physical_bounds")

        compare_edge_central(edge, central)
        self.assertEqual(readings, readings_copy)

    # 13. comparison is deterministic
    def test_comparison_deterministic(self):
        edge = self._make_edge_inference(status="ok", anomaly_flag=False, score=0.05, score_type="raw_score")
        central = self._make_central_verdict(is_anomaly=False, anomaly_score_pct=10.0)

        res1 = compare_edge_central(edge, central)
        res2 = compare_edge_central(edge, central)

        self.assertEqual(res1.model_dump(), res2.model_dump())

    # 14. comparison does not mutate input objects/dictionaries
    def test_comparison_does_not_mutate_inputs(self):
        edge_dict = {
            "status": "anomaly_detected",
            "anomaly_flag": True,
            "anomaly_type": "physical_bounds",
            "score": 0.9,
            "score_type": "heuristic",
            "model_version": "edge_v1",
            "inference_method": "rules",
        }
        edge_copy = copy.deepcopy(edge_dict)

        central_dict = self._make_central_verdict(is_anomaly=True, fault_type="physical_bounds", anomaly_score_pct=90.0)
        central_copy = copy.deepcopy(central_dict)

        compare_edge_central(edge_dict, central_dict)
        self.assertEqual(edge_dict, edge_copy)
        self.assertEqual(central_dict, central_copy)

    # 15. both edge and central unavailable -> INSUFFICIENT_EVIDENCE
    def test_both_unavailable_insufficient_evidence(self):
        res = compare_edge_central(None, None)
        self.assertEqual(res.comparison_status, ComparisonStatus.INSUFFICIENT_EVIDENCE.value)
        self.assertIsNone(res.edge_anomaly_flag)
        self.assertIsNone(res.central_anomaly_flag)
        self.assertIsNone(res.anomaly_decision_agreement)

    # 16. edge status 'unavailable' -> EDGE_UNAVAILABLE
    def test_edge_status_unavailable(self):
        edge = self._make_edge_inference(status="unavailable", anomaly_flag=False)
        central = self._make_central_verdict(is_anomaly=False)

        res = compare_edge_central(edge, central)
        self.assertEqual(res.comparison_status, ComparisonStatus.EDGE_UNAVAILABLE.value)
        self.assertIsNone(res.edge_anomaly_flag)

    # 17. integration via POST /api/ingest/observation stores comparison in simulator latest state
    def test_ingestion_api_attaches_comparison_to_simulator(self):
        TestEdgeCentralComparison._seq_counter += 1
        cnt = TestEdgeCentralComparison._seq_counter
        base_time = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)
        cur_time = base_time + timedelta(minutes=cnt * 10)

        payload = {
            "event_id": f"evt-comp-test-{cnt}",
            "station_id": "AWS-CHN-024",
            "device_id": "esp32-node-chennai-01",
            "observed_at": cur_time.isoformat(),
            "sequence_number": cnt,
            "readings": {
                "temperature_c": 28.0,
                "pressure_hpa": 1012.0,
                "humidity_pct": 60.0,
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
                "signal_strength": -65.0,
            },
        }

        resp = self.client.post("/api/ingest/observation", json=payload)
        self.assertEqual(resp.status_code, 200)

        sim = app.state.sim
        latest = sim.latest["AWS-CHN-024"]
        self.assertIn("comparison", latest)
        comp = latest["comparison"]
        self.assertIn("comparison_status", comp)
        self.assertIn("anomaly_decision_agreement", comp)


if __name__ == "__main__":
    unittest.main()
