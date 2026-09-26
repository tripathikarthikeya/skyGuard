"""
tests/test_edge_rules.py

Comprehensive test suite for the standardized Level 1 Edge Inference Engine (model/edge_rules.py).

Verifies:
1. Normal readings evaluation (anomaly_flag=False, status='ok')
2. Dropout rule detection (None, NaN)
3. Sensor fail-low rule detection (rail floor values)
4. Physical bounds rule detection (exceeding extremes)
5. Status semantics ('ok' vs 'anomaly_detected')
6. Model version and inference method constants
7. Null score semantics for deterministic rules
8. Deterministic precedence when multiple rules could apply
9. Full compatibility with EdgeInference and ObservationPacket schemas
10. Backward compatibility for legacy check_reading_edge() and EdgeVerdict
"""

import math
import unittest
from datetime import datetime, timezone

from model.contracts import EdgeInference, ObservationPacket, ObservationReadings, DeviceMetadata
from model.edge_rules import (
    run_edge_inference,
    check_reading_edge,
    evaluate_edge_rules,
    EdgeVerdict,
    EDGE_MODEL_VERSION,
    EDGE_INFERENCE_METHOD,
    TEMP_PHYSICAL_MIN,
    TEMP_PHYSICAL_MAX,
    PRESSURE_PHYSICAL_MIN,
    PRESSURE_PHYSICAL_MAX,
    HUMIDITY_PHYSICAL_MIN,
    HUMIDITY_PHYSICAL_MAX,
    TEMP_FAIL_LOW,
    PRESSURE_FAIL_LOW,
    HUMIDITY_FAIL_LOW,
)


class TestEdgeRules(unittest.TestCase):
    # 1. Normal reading
    def test_normal_reading(self):
        result = run_edge_inference(temp_c=25.0, pressure_hpa=1013.25, humidity_pct=55.0)
        self.assertIsInstance(result, EdgeInference)
        self.assertFalse(result.anomaly_flag)
        self.assertEqual(result.status, "ok")
        self.assertIsNone(result.anomaly_type)
        self.assertIsNone(result.score)
        self.assertIsNone(result.score_type)
        self.assertEqual(result.model_version, EDGE_MODEL_VERSION)
        self.assertEqual(result.inference_method, EDGE_INFERENCE_METHOD)

    # 2. Dropout detection (None and NaN)
    def test_dropout_missing_channels(self):
        # Temperature is None
        res_t = run_edge_inference(temp_c=None, pressure_hpa=1013.25, humidity_pct=55.0)
        self.assertTrue(res_t.anomaly_flag)
        self.assertEqual(res_t.anomaly_type, "dropout")
        self.assertEqual(res_t.status, "anomaly_detected")

        # Pressure is None
        res_p = run_edge_inference(temp_c=25.0, pressure_hpa=None, humidity_pct=55.0)
        self.assertTrue(res_p.anomaly_flag)
        self.assertEqual(res_p.anomaly_type, "dropout")

        # Humidity is None
        res_h = run_edge_inference(temp_c=25.0, pressure_hpa=1013.25, humidity_pct=None)
        self.assertTrue(res_h.anomaly_flag)
        self.assertEqual(res_h.anomaly_type, "dropout")

        # NaN float
        res_nan = run_edge_inference(temp_c=float("nan"), pressure_hpa=1013.25, humidity_pct=55.0)
        self.assertTrue(res_nan.anomaly_flag)
        self.assertEqual(res_nan.anomaly_type, "dropout")

    # 3. Sensor fail-low detection
    def test_sensor_fail_low(self):
        # Temperature at or below fail-low floor (-8.0 C)
        res_t = run_edge_inference(temp_c=TEMP_FAIL_LOW, pressure_hpa=1013.25, humidity_pct=55.0)
        self.assertTrue(res_t.anomaly_flag)
        self.assertEqual(res_t.anomaly_type, "sensor_fail_low")
        self.assertEqual(res_t.status, "anomaly_detected")

        # Pressure at or below fail-low floor (150.0 hPa)
        res_p = run_edge_inference(temp_c=25.0, pressure_hpa=PRESSURE_FAIL_LOW, humidity_pct=55.0)
        self.assertTrue(res_p.anomaly_flag)
        self.assertEqual(res_p.anomaly_type, "sensor_fail_low")

        # Humidity at or below fail-low floor (3.0 %)
        res_h = run_edge_inference(temp_c=25.0, pressure_hpa=1013.25, humidity_pct=HUMIDITY_FAIL_LOW)
        self.assertTrue(res_h.anomaly_flag)
        self.assertEqual(res_h.anomaly_type, "sensor_fail_low")

    # 4. Physical bounds detection
    def test_physical_bounds(self):
        # Temperature above max (60.0 C)
        res_thi = run_edge_inference(temp_c=TEMP_PHYSICAL_MAX + 1.0, pressure_hpa=1013.25, humidity_pct=55.0)
        self.assertTrue(res_thi.anomaly_flag)
        self.assertEqual(res_thi.anomaly_type, "physical_bounds")
        self.assertEqual(res_thi.status, "anomaly_detected")

        # Pressure below min (870.0 hPa) but above fail-low (150.0 hPa)
        res_plo = run_edge_inference(temp_c=25.0, pressure_hpa=500.0, humidity_pct=55.0)
        self.assertTrue(res_plo.anomaly_flag)
        self.assertEqual(res_plo.anomaly_type, "physical_bounds")

        # Pressure above max (1085.0 hPa)
        res_phi = run_edge_inference(temp_c=25.0, pressure_hpa=PRESSURE_PHYSICAL_MAX + 10.0, humidity_pct=55.0)
        self.assertTrue(res_phi.anomaly_flag)
        self.assertEqual(res_phi.anomaly_type, "physical_bounds")

        # Humidity above max (100.0 %)
        res_hhi = run_edge_inference(temp_c=25.0, pressure_hpa=1013.25, humidity_pct=105.0)
        self.assertTrue(res_hhi.anomaly_flag)
        self.assertEqual(res_hhi.anomaly_type, "physical_bounds")

    # 5. Status semantics
    def test_status_semantics(self):
        ok_res = run_edge_inference(25.0, 1013.0, 50.0)
        self.assertEqual(ok_res.status, "ok")

        anom_res = run_edge_inference(None, 1013.0, 50.0)
        self.assertEqual(anom_res.status, "anomaly_detected")

    # 6. Model version and inference method
    def test_metadata_fields(self):
        res = run_edge_inference(25.0, 1013.0, 50.0)
        self.assertEqual(res.model_version, "edge_rules_v1.0.0")
        self.assertEqual(res.inference_method, "rules")

        # Custom version pass-through
        res_custom = run_edge_inference(25.0, 1013.0, 50.0, model_version="edge_v2.0.0-rc1")
        self.assertEqual(res_custom.model_version, "edge_v2.0.0-rc1")

    # 7. Null score semantics for deterministic rules
    def test_null_score_semantics(self):
        normal_inf = run_edge_inference(25.0, 1013.0, 50.0)
        self.assertIsNone(normal_inf.score)
        self.assertIsNone(normal_inf.score_type)

        anom_inf = run_edge_inference(99.0, 1013.0, 50.0)
        self.assertIsNone(anom_inf.score)
        self.assertIsNone(anom_inf.score_type)

    # 8. Deterministic precedence ordering
    def test_deterministic_precedence(self):
        # Case A: Reading with both None (dropout) and out-of-bounds float
        # Dropout evaluates first
        res_a = run_edge_inference(temp_c=None, pressure_hpa=1200.0, humidity_pct=50.0)
        self.assertEqual(res_a.anomaly_type, "dropout")

        # Case B: Reading with both sensor_fail_low (e.g. temp=-40C <= -8C) and physical bounds
        # Fail-low evaluates before general physical bounds
        res_b = run_edge_inference(temp_c=-40.0, pressure_hpa=1013.0, humidity_pct=50.0)
        self.assertEqual(res_b.anomaly_type, "sensor_fail_low")

    # 9. Schema compatibility with ObservationPacket
    def test_packet_integration_compatibility(self):
        raw_t, raw_p, raw_h = 32.5, 1009.1, 68.0
        edge_inf = run_edge_inference(raw_t, raw_p, raw_h)

        packet = ObservationPacket(
            event_id="evt-edge-test-01",
            station_id="AWS-CHN-024",
            device_id="esp32-node-01",
            observed_at=datetime.now(timezone.utc),
            sequence_number=42,
            readings=ObservationReadings(
                temperature_c=raw_t,
                pressure_hpa=raw_p,
                humidity_pct=raw_h,
            ),
            edge_inference=edge_inf,
            device_metadata=DeviceMetadata(
                firmware_version="1.0.0",
                battery_voltage=3.85,
                signal_strength=-62.0,
            ),
        )
        self.assertEqual(packet.edge_inference.status, "ok")
        self.assertFalse(packet.edge_inference.anomaly_flag)
        self.assertIsNone(packet.edge_inference.anomaly_type)

        # JSON round-trip of the packet containing this edge inference
        json_data = packet.model_dump_json()
        restored = ObservationPacket.model_validate_json(json_data)
        self.assertEqual(restored.edge_inference.status, "ok")
        self.assertEqual(restored.edge_inference.model_version, EDGE_MODEL_VERSION)

    # 10. Backward compatibility: check_reading_edge() & EdgeVerdict
    def test_legacy_check_reading_edge_compatibility(self):
        verdict_ok = check_reading_edge(25.0, 1013.0, 50.0)
        self.assertIsInstance(verdict_ok, EdgeVerdict)
        self.assertFalse(verdict_ok.flag)
        self.assertEqual(verdict_ok.fault_type, "")

        # Test conversion from EdgeVerdict to EdgeInference
        inf_from_verdict = verdict_ok.to_edge_inference()
        self.assertEqual(inf_from_verdict.status, "ok")
        self.assertFalse(inf_from_verdict.anomaly_flag)

        # Legacy fail-low check
        verdict_fl = check_reading_edge(-10.0, 1013.0, 50.0)
        self.assertTrue(verdict_fl.flag)
        self.assertEqual(verdict_fl.fault_type, "sensor_fail_low")
        inf_fl = verdict_fl.to_edge_inference()
        self.assertEqual(inf_fl.status, "anomaly_detected")
        self.assertEqual(inf_fl.anomaly_type, "sensor_fail_low")


if __name__ == "__main__":
    unittest.main()
