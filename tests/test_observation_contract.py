"""
tests/test_observation_contract.py

Unit tests for the Authoritative Canonical Observation Data Contract (model/contracts.py).

Covers:
- Valid packet construction
- Required and missing fields
- Empty/whitespace string rejections
- Timezone awareness validation
- Sequence number constraints
- Sensor readings structure and numeric types
- Edge inference schema and nullability
- Device metadata schema and nullability
- JSON serialization, deserialization, and round-trip equality
- Structural validation without meteorological filtering
"""

import json
import unittest
from datetime import datetime, timezone, timedelta
from pydantic import ValidationError

from model.contracts import (
    ObservationPacket,
    ObservationReadings,
    EdgeInference,
    DeviceMetadata,
)


class TestObservationContract(unittest.TestCase):
    def setUp(self):
        self.valid_payload = {
            "event_id": "evt-20260923-0001",
            "station_id": "AWS-CHN-024",
            "device_id": "esp32-node-chennai-01",
            "observed_at": "2026-09-23T13:00:00+00:00",
            "sequence_number": 1001,
            "readings": {
                "temperature_c": 25.4,
                "pressure_hpa": 1008.2,
                "humidity_pct": 72.5,
            },
            "edge_inference": {
                "status": "ok",
                "anomaly_flag": False,
                "anomaly_type": None,
                "score": None,
                "score_type": None,
                "model_version": "edge_v1.0.0",
                "inference_method": "rules",
            },
            "device_metadata": {
                "firmware_version": "1.0.0",
                "battery_voltage": 3.95,
                "signal_strength": -65.0,
            },
        }

    # A. Fully valid observation
    def test_fully_valid_observation(self):
        packet = ObservationPacket.model_validate(self.valid_payload)
        self.assertEqual(packet.event_id, "evt-20260923-0001")
        self.assertEqual(packet.station_id, "AWS-CHN-024")
        self.assertEqual(packet.device_id, "esp32-node-chennai-01")
        self.assertEqual(packet.sequence_number, 1001)
        self.assertEqual(packet.readings.temperature_c, 25.4)
        self.assertEqual(packet.readings.pressure_hpa, 1008.2)
        self.assertEqual(packet.readings.humidity_pct, 72.5)
        self.assertFalse(packet.edge_inference.anomaly_flag)
        self.assertEqual(packet.device_metadata.firmware_version, "1.0.0")

    # B. Missing event_id
    def test_missing_event_id(self):
        payload = dict(self.valid_payload)
        del payload["event_id"]
        with self.assertRaises(ValidationError) as ctx:
            ObservationPacket.model_validate(payload)
        self.assertIn("event_id", str(ctx.exception))

    # C. Empty event_id
    def test_empty_event_id(self):
        for empty_val in ["", "   "]:
            payload = dict(self.valid_payload, event_id=empty_val)
            with self.assertRaises(ValidationError) as ctx:
                ObservationPacket.model_validate(payload)
            self.assertIn("event_id", str(ctx.exception))

    # D. Missing station_id
    def test_missing_station_id(self):
        payload = dict(self.valid_payload)
        del payload["station_id"]
        with self.assertRaises(ValidationError) as ctx:
            ObservationPacket.model_validate(payload)
        self.assertIn("station_id", str(ctx.exception))

    # E. Missing device_id
    def test_missing_device_id(self):
        payload = dict(self.valid_payload)
        del payload["device_id"]
        with self.assertRaises(ValidationError) as ctx:
            ObservationPacket.model_validate(payload)
        self.assertIn("device_id", str(ctx.exception))

    # F. Missing observed_at
    def test_missing_observed_at(self):
        payload = dict(self.valid_payload)
        del payload["observed_at"]
        with self.assertRaises(ValidationError) as ctx:
            ObservationPacket.model_validate(payload)
        self.assertIn("observed_at", str(ctx.exception))

    # G. Naive timestamp (reject naive datetimes)
    def test_naive_timestamp(self):
        # Naive datetime string without timezone offset
        payload = dict(self.valid_payload, observed_at="2026-09-23T13:00:00")
        with self.assertRaises(ValidationError) as ctx:
            ObservationPacket.model_validate(payload)
        self.assertIn("timezone-aware", str(ctx.exception))

        # Naive datetime object
        naive_dt = datetime(2026, 9, 23, 13, 0, 0)
        payload_obj = dict(self.valid_payload, observed_at=naive_dt)
        with self.assertRaises(ValidationError) as ctx:
            ObservationPacket.model_validate(payload_obj)
        self.assertIn("timezone-aware", str(ctx.exception))

    # H. Valid timezone-aware timestamp
    def test_valid_timezone_aware_timestamp(self):
        # UTC string with Z
        payload_z = dict(self.valid_payload, observed_at="2026-09-23T13:00:00Z")
        packet_z = ObservationPacket.model_validate(payload_z)
        self.assertIsNotNone(packet_z.observed_at.tzinfo)

        # Offset string (+05:30)
        payload_ist = dict(self.valid_payload, observed_at="2026-09-23T18:30:00+05:30")
        packet_ist = ObservationPacket.model_validate(payload_ist)
        self.assertIsNotNone(packet_ist.observed_at.tzinfo)

        # Explicit datetime object with timezone
        aware_dt = datetime(2026, 9, 23, 13, 0, 0, tzinfo=timezone.utc)
        payload_dt = dict(self.valid_payload, observed_at=aware_dt)
        packet_dt = ObservationPacket.model_validate(payload_dt)
        self.assertEqual(packet_dt.observed_at, aware_dt)

    # I. Negative sequence number
    def test_negative_sequence_number(self):
        payload = dict(self.valid_payload, sequence_number=-1)
        with self.assertRaises(ValidationError) as ctx:
            ObservationPacket.model_validate(payload)
        self.assertIn("sequence_number", str(ctx.exception))

    # J. Missing readings
    def test_missing_readings(self):
        payload = dict(self.valid_payload)
        del payload["readings"]
        with self.assertRaises(ValidationError) as ctx:
            ObservationPacket.model_validate(payload)
        self.assertIn("readings", str(ctx.exception))

    # K. Missing temperature
    def test_missing_temperature(self):
        readings = {"pressure_hpa": 1008.2, "humidity_pct": 72.5}
        payload = dict(self.valid_payload, readings=readings)
        with self.assertRaises(ValidationError) as ctx:
            ObservationPacket.model_validate(payload)
        self.assertIn("temperature_c", str(ctx.exception))

    # L. Missing pressure
    def test_missing_pressure(self):
        readings = {"temperature_c": 25.4, "humidity_pct": 72.5}
        payload = dict(self.valid_payload, readings=readings)
        with self.assertRaises(ValidationError) as ctx:
            ObservationPacket.model_validate(payload)
        self.assertIn("pressure_hpa", str(ctx.exception))

    # M. Missing humidity
    def test_missing_humidity(self):
        readings = {"temperature_c": 25.4, "pressure_hpa": 1008.2}
        payload = dict(self.valid_payload, readings=readings)
        with self.assertRaises(ValidationError) as ctx:
            ObservationPacket.model_validate(payload)
        self.assertIn("humidity_pct", str(ctx.exception))

    # N. Non-numeric sensor values
    def test_non_numeric_sensor_values(self):
        for bad_val in ["invalid", "twenty", [25.0]]:
            readings = {
                "temperature_c": bad_val,
                "pressure_hpa": 1008.2,
                "humidity_pct": 72.5,
            }
            payload = dict(self.valid_payload, readings=readings)
            with self.assertRaises(ValidationError) as ctx:
                ObservationPacket.model_validate(payload)
            self.assertIn("temperature_c", str(ctx.exception))

    # O. Missing edge_inference
    def test_missing_edge_inference(self):
        payload = dict(self.valid_payload)
        del payload["edge_inference"]
        with self.assertRaises(ValidationError) as ctx:
            ObservationPacket.model_validate(payload)
        self.assertIn("edge_inference", str(ctx.exception))

    # P. Missing required edge fields
    def test_missing_required_edge_fields(self):
        for field in ["status", "anomaly_flag", "model_version", "inference_method"]:
            edge_inf = dict(self.valid_payload["edge_inference"])
            del edge_inf[field]
            payload = dict(self.valid_payload, edge_inference=edge_inf)
            with self.assertRaises(ValidationError) as ctx:
                ObservationPacket.model_validate(payload)
            self.assertIn(field, str(ctx.exception))

    # Q. Null anomaly_type when anomaly_flag=false
    def test_null_anomaly_type_when_anomaly_flag_false(self):
        edge_inf = {
            "status": "ok",
            "anomaly_flag": False,
            "anomaly_type": None,
            "score": None,
            "score_type": None,
            "model_version": "edge_v1.0.0",
            "inference_method": "rules",
        }
        payload = dict(self.valid_payload, edge_inference=edge_inf)
        packet = ObservationPacket.model_validate(payload)
        self.assertFalse(packet.edge_inference.anomaly_flag)
        self.assertIsNone(packet.edge_inference.anomaly_type)

    # R. Valid anomaly_type when anomaly_flag=true
    def test_valid_anomaly_type_when_anomaly_flag_true(self):
        edge_inf = {
            "status": "anomaly_detected",
            "anomaly_flag": True,
            "anomaly_type": "physical_bounds",
            "score": 100.0,
            "score_type": "rule_score",
            "model_version": "edge_v1.0.0",
            "inference_method": "rules",
        }
        payload = dict(self.valid_payload, edge_inference=edge_inf)
        packet = ObservationPacket.model_validate(payload)
        self.assertTrue(packet.edge_inference.anomaly_flag)
        self.assertEqual(packet.edge_inference.anomaly_type, "physical_bounds")
        self.assertEqual(packet.edge_inference.score, 100.0)

    # S. Nullable edge score
    def test_nullable_edge_score(self):
        # score is None
        edge_inf_none = dict(self.valid_payload["edge_inference"], score=None, score_type=None)
        packet_none = ObservationPacket.model_validate(dict(self.valid_payload, edge_inference=edge_inf_none))
        self.assertIsNone(packet_none.edge_inference.score)

        # score is a float
        edge_inf_score = dict(self.valid_payload["edge_inference"], score=85.5, score_type="normalized_score")
        packet_score = ObservationPacket.model_validate(dict(self.valid_payload, edge_inference=edge_inf_score))
        self.assertEqual(packet_score.edge_inference.score, 85.5)
        self.assertEqual(packet_score.edge_inference.score_type, "normalized_score")

    # T. Device metadata with nullable battery/signal fields
    def test_device_metadata_nullable_fields(self):
        meta_null = {
            "firmware_version": "1.0.0",
            "battery_voltage": None,
            "signal_strength": None,
        }
        payload = dict(self.valid_payload, device_metadata=meta_null)
        packet = ObservationPacket.model_validate(payload)
        self.assertIsNone(packet.device_metadata.battery_voltage)
        self.assertIsNone(packet.device_metadata.signal_strength)

        meta_vals = {
            "firmware_version": "2.1.0-rc3",
            "battery_voltage": 3.72,
            "signal_strength": -78.0,
        }
        payload_vals = dict(self.valid_payload, device_metadata=meta_vals)
        packet_vals = ObservationPacket.model_validate(payload_vals)
        self.assertEqual(packet_vals.device_metadata.battery_voltage, 3.72)
        self.assertEqual(packet_vals.device_metadata.signal_strength, -78.0)

    # U. JSON serialization
    def test_json_serialization(self):
        packet = ObservationPacket.model_validate(self.valid_payload)
        json_str = packet.model_dump_json()
        parsed = json.loads(json_str)
        self.assertEqual(parsed["event_id"], "evt-20260923-0001")
        self.assertEqual(parsed["readings"]["temperature_c"], 25.4)
        self.assertIn("2026-09-23T13:00:00", parsed["observed_at"])

    # V. JSON deserialization
    def test_json_deserialization(self):
        raw_json = json.dumps(self.valid_payload)
        packet = ObservationPacket.model_validate_json(raw_json)
        self.assertEqual(packet.station_id, "AWS-CHN-024")
        self.assertEqual(packet.sequence_number, 1001)
        self.assertEqual(packet.readings.pressure_hpa, 1008.2)

    # W. Round-trip equality
    def test_round_trip_equality(self):
        original = ObservationPacket.model_validate(self.valid_payload)
        serialized = original.model_dump_json()
        restored = ObservationPacket.model_validate_json(serialized)
        self.assertEqual(original, restored)

    # Strictness test: extra top-level and nested fields forbidden
    def test_extra_fields_forbidden(self):
        payload_extra = dict(self.valid_payload, unexpected_field="malicious_payload")
        with self.assertRaises(ValidationError) as ctx:
            ObservationPacket.model_validate(payload_extra)
        self.assertIn("unexpected_field", str(ctx.exception))

    # Raw preservation test: extreme meteorological readings are structurally valid
    def test_raw_preservation_structural_validity(self):
        extreme_payload = dict(self.valid_payload)
        extreme_payload["readings"] = {
            "temperature_c": 70.0,    # Highly anomalous but valid float observation
            "pressure_hpa": 1200.0,   # Highly anomalous but valid float observation
            "humidity_pct": 150.0,    # Impossible physical value, but valid raw float
        }
        # Schema MUST NOT reject this observation; anomaly detection belongs to Level 2
        packet = ObservationPacket.model_validate(extreme_payload)
        self.assertEqual(packet.readings.temperature_c, 70.0)
        self.assertEqual(packet.readings.pressure_hpa, 1200.0)
        self.assertEqual(packet.readings.humidity_pct, 150.0)

    # Dropout preservation test: null sensor reading channels are valid
    def test_sensor_dropout_null_readings_valid(self):
        dropout_payload = dict(self.valid_payload)
        dropout_payload["readings"] = {
            "temperature_c": None,   # Channel dropout / sensor comm failure
            "pressure_hpa": 1013.2,
            "humidity_pct": None,
        }
        packet = ObservationPacket.model_validate(dropout_payload)
        self.assertIsNone(packet.readings.temperature_c)
        self.assertEqual(packet.readings.pressure_hpa, 1013.2)
        self.assertIsNone(packet.readings.humidity_pct)


if __name__ == "__main__":
    unittest.main()
