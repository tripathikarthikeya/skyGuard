"""
Tests for ESP32 edge rules semantic compatibility with the Python reference model.
Validates that Level-1 edge rules match the canonical logic defined in model/edge_rules.py.
"""

import math
import unittest
from model.edge_rules import (
    run_edge_inference,
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


class TestESP32EdgeRulesCompatibility(unittest.TestCase):
    """
    Validates semantic compatibility across the 11 required test vectors:
    1. Normal reading
    2. Dropout (all nulls / single nulls / NaN)
    3. Sensor fail low temperature (<= -8.0°C)
    4. Sensor fail low pressure (<= 150.0 hPa)
    5. Sensor fail low humidity (<= 3.0%)
    6. Physical temperature lower bound (< -50.0°C)
    7. Physical temperature upper bound (> 60.0°C)
    8. Pressure lower bound (< 870.0 hPa)
    9. Pressure upper bound (> 1085.0 hPa)
    10. Humidity lower bound (< 0.0%)
    11. Humidity upper bound (> 100.0%)
    """

    def test_01_normal_reading(self):
        edge = run_edge_inference(25.4, 1013.25, 55.0)
        self.assertEqual(edge.status, "ok")
        self.assertFalse(edge.anomaly_flag)
        self.assertIsNone(edge.anomaly_type)
        self.assertIsNone(edge.score)
        self.assertIsNone(edge.score_type)
        self.assertEqual(edge.model_version, EDGE_MODEL_VERSION)
        self.assertEqual(edge.inference_method, EDGE_INFERENCE_METHOD)

    def test_02_dropout(self):
        # All null readings
        edge_all = run_edge_inference(None, None, None)
        self.assertEqual(edge_all.status, "anomaly_detected")
        self.assertTrue(edge_all.anomaly_flag)
        self.assertEqual(edge_all.anomaly_type, "dropout")
        self.assertIsNone(edge_all.score)
        self.assertEqual(edge_all.model_version, EDGE_MODEL_VERSION)

        # Single null reading (e.g. DHT22 failed, BMP280 working)
        edge_partial = run_edge_inference(22.0, 1010.0, None)
        self.assertEqual(edge_partial.status, "anomaly_detected")
        self.assertTrue(edge_partial.anomaly_flag)
        self.assertEqual(edge_partial.anomaly_type, "dropout")

        # Float NaN reading
        edge_nan = run_edge_inference(float("nan"), 1010.0, 50.0)
        self.assertEqual(edge_nan.status, "anomaly_detected")
        self.assertTrue(edge_nan.anomaly_flag)
        self.assertEqual(edge_nan.anomaly_type, "dropout")

    def test_03_sensor_fail_low_temperature(self):
        # Temperature at or below -8.0°C indicates transducer rail collapse
        edge = run_edge_inference(-10.0, 1013.25, 50.0)
        self.assertEqual(edge.status, "anomaly_detected")
        self.assertTrue(edge.anomaly_flag)
        self.assertEqual(edge.anomaly_type, "sensor_fail_low")

    def test_04_sensor_fail_low_pressure(self):
        # Pressure at or below 150.0 hPa indicates disconnected I2C / electrical rail floor
        edge = run_edge_inference(25.0, 100.0, 50.0)
        self.assertEqual(edge.status, "anomaly_detected")
        self.assertTrue(edge.anomaly_flag)
        self.assertEqual(edge.anomaly_type, "sensor_fail_low")

    def test_05_sensor_fail_low_humidity(self):
        # Humidity at or below 3.0% indicates disconnected 1-wire / CRC fail
        edge = run_edge_inference(25.0, 1013.25, 1.5)
        self.assertEqual(edge.status, "anomaly_detected")
        self.assertTrue(edge.anomaly_flag)
        self.assertEqual(edge.anomaly_type, "sensor_fail_low")

    def test_06_physical_temperature_lower_bound(self):
        # Temperature below -50.0°C (caught by sensor_fail_low first since -55 <= -8.0)
        edge = run_edge_inference(-55.0, 1013.25, 50.0)
        self.assertEqual(edge.status, "anomaly_detected")
        self.assertTrue(edge.anomaly_flag)
        self.assertEqual(edge.anomaly_type, "sensor_fail_low")

    def test_07_physical_temperature_upper_bound(self):
        # Above 60°C (planetary surface extreme)
        edge = run_edge_inference(65.0, 1013.25, 50.0)
        self.assertEqual(edge.status, "anomaly_detected")
        self.assertTrue(edge.anomaly_flag)
        self.assertEqual(edge.anomaly_type, "physical_bounds")

    def test_08_pressure_lower_bound(self):
        # Below 870 hPa but above 150 hPa (triggers physical_bounds)
        edge = run_edge_inference(25.0, 850.0, 50.0)
        self.assertEqual(edge.status, "anomaly_detected")
        self.assertTrue(edge.anomaly_flag)
        self.assertEqual(edge.anomaly_type, "physical_bounds")

    def test_09_pressure_upper_bound(self):
        # Above 1085 hPa
        edge = run_edge_inference(25.0, 1100.0, 50.0)
        self.assertEqual(edge.status, "anomaly_detected")
        self.assertTrue(edge.anomaly_flag)
        self.assertEqual(edge.anomaly_type, "physical_bounds")

    def test_10_humidity_lower_bound(self):
        # Below 0.0% (caught by sensor_fail_low since -1.0 <= 3.0)
        edge = run_edge_inference(25.0, 1013.25, -2.0)
        self.assertEqual(edge.status, "anomaly_detected")
        self.assertTrue(edge.anomaly_flag)
        self.assertEqual(edge.anomaly_type, "sensor_fail_low")

    def test_11_humidity_upper_bound(self):
        # Above 100%
        edge = run_edge_inference(25.0, 1013.25, 105.0)
        self.assertEqual(edge.status, "anomaly_detected")
        self.assertTrue(edge.anomaly_flag)
        self.assertEqual(edge.anomaly_type, "physical_bounds")

    def test_edge_rule_precedence(self):
        """
        Tests the strict precedence order:
        dropout -> sensor_fail_low -> physical_bounds -> normal
        """
        # Dropout over fail_low (temperature None even if pressure is <= 150.0)
        edge = run_edge_inference(None, 100.0, 50.0)
        self.assertEqual(edge.anomaly_type, "dropout")

        # Fail low over physical upper bound (temp -10.0 and pressure 1150.0)
        edge = run_edge_inference(-10.0, 1150.0, 50.0)
        self.assertEqual(edge.anomaly_type, "sensor_fail_low")


if __name__ == "__main__":
    unittest.main()
