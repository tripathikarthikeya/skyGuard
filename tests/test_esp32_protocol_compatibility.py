"""
tests/test_esp32_protocol_compatibility.py

Protocol Compatibility Validation Test Suite for ESP32 Firmware.

Verifies that JSON payloads constructed according to the ESP32 packet_builder.cpp format
are 100% compliant with the canonical ObservationPacket data contract defined in model/contracts.py
and are successfully ingested by POST /api/ingest/observation.
"""

import json
import os
import sys
import unittest
import uuid
from datetime import datetime, timezone
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from main import app
from model.contracts import ObservationPacket, ObservationIngestResponse


def construct_simulated_esp32_json(
    event_id: str,
    station_id: str,
    device_id: str,
    observed_at_iso: str,
    sequence_number: int,
    temp_c: float | None,
    pressure_hpa: float | None,
    humidity_pct: float | None,
    status: str,
    anomaly_flag: bool,
    anomaly_type: str | None,
    firmware_version: str = "esp32_edge_v1.0.0",
    rssi: float | None = -62.5,
) -> str:
    """
    Constructs a JSON string using the exact string template used by
    edge/esp32/src/protocol/packet_builder.cpp.
    """
    temp_str = f"{temp_c:.2f}" if temp_c is not None else "null"
    pres_str = f"{pressure_hpa:.2f}" if pressure_hpa is not None else "null"
    hum_str = f"{humidity_pct:.2f}" if humidity_pct is not None else "null"
    anom_type_str = f'"{anomaly_type}"' if anomaly_type is not None else "null"
    rssi_str = f"{rssi:.1f}" if rssi is not None else "null"

    return f"""{{
"event_id":"{event_id}",
"station_id":"{station_id}",
"device_id":"{device_id}",
"observed_at":"{observed_at_iso}",
"sequence_number":{sequence_number},
"readings":{{
"temperature_c":{temp_str},
"pressure_hpa":{pres_str},
"humidity_pct":{hum_str}
}},
"edge_inference":{{
"status":"{status}",
"anomaly_flag":{"true" if anomaly_flag else "false"},
"anomaly_type":{anom_type_str},
"score":null,
"score_type":null,
"model_version":"edge_rules_v1.0.0",
"inference_method":"rules"
}},
"device_metadata":{{
"firmware_version":"{firmware_version}",
"battery_voltage":null,
"signal_strength":{rssi_str}
}}
}}"""


class TestESP32ProtocolCompatibility(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)

    def test_normal_esp32_payload_validates_against_pydantic_contract(self):
        raw_json = construct_simulated_esp32_json(
            event_id=str(uuid.uuid4()),
            station_id="AWS-CHN-024",
            device_id="esp32-node-246F2801A2B4",
            observed_at_iso="2026-09-23T18:00:00Z",
            sequence_number=101,
            temp_c=28.75,
            pressure_hpa=1012.40,
            humidity_pct=65.20,
            status="ok",
            anomaly_flag=False,
            anomaly_type=None,
        )

        data = json.loads(raw_json)
        packet = ObservationPacket.model_validate(data)

        self.assertEqual(packet.station_id, "AWS-CHN-024")
        self.assertEqual(packet.device_id, "esp32-node-246F2801A2B4")
        self.assertEqual(packet.readings.temperature_c, 28.75)
        self.assertEqual(packet.readings.pressure_hpa, 1012.40)
        self.assertEqual(packet.readings.humidity_pct, 65.20)
        self.assertFalse(packet.edge_inference.anomaly_flag)
        self.assertIsNone(packet.edge_inference.anomaly_type)
        self.assertEqual(packet.device_metadata.firmware_version, "esp32_edge_v1.0.0")

    def test_dropout_payload_with_null_values_validates_successfully(self):
        raw_json = construct_simulated_esp32_json(
            event_id=str(uuid.uuid4()),
            station_id="AWS-CHN-024",
            device_id="esp32-node-246F2801A2B4",
            observed_at_iso="2026-09-23T18:01:00Z",
            sequence_number=102,
            temp_c=None,
            pressure_hpa=1010.0,
            humidity_pct=None,
            status="anomaly_detected",
            anomaly_flag=True,
            anomaly_type="dropout",
        )

        data = json.loads(raw_json)
        packet = ObservationPacket.model_validate(data)

        self.assertIsNone(packet.readings.temperature_c)
        self.assertIsNone(packet.readings.humidity_pct)
        self.assertEqual(packet.readings.pressure_hpa, 1010.0)
        self.assertTrue(packet.edge_inference.anomaly_flag)
        self.assertEqual(packet.edge_inference.anomaly_type, "dropout")

    def test_fail_low_payload_validates_successfully(self):
        raw_json = construct_simulated_esp32_json(
            event_id=str(uuid.uuid4()),
            station_id="AWS-CHN-024",
            device_id="esp32-node-246F2801A2B4",
            observed_at_iso="2026-09-23T18:02:00Z",
            sequence_number=103,
            temp_c=-40.0,
            pressure_hpa=0.0,
            humidity_pct=0.0,
            status="anomaly_detected",
            anomaly_flag=True,
            anomaly_type="sensor_fail_low",
        )

        data = json.loads(raw_json)
        packet = ObservationPacket.model_validate(data)
        self.assertEqual(packet.edge_inference.anomaly_type, "sensor_fail_low")

    def test_physical_bounds_payload_validates_successfully(self):
        raw_json = construct_simulated_esp32_json(
            event_id=str(uuid.uuid4()),
            station_id="AWS-CHN-024",
            device_id="esp32-node-246F2801A2B4",
            observed_at_iso="2026-09-23T18:03:00Z",
            sequence_number=104,
            temp_c=75.0,
            pressure_hpa=1012.0,
            humidity_pct=50.0,
            status="anomaly_detected",
            anomaly_flag=True,
            anomaly_type="physical_bounds",
        )

        data = json.loads(raw_json)
        packet = ObservationPacket.model_validate(data)
        self.assertEqual(packet.edge_inference.anomaly_type, "physical_bounds")

    def test_esp32_json_payload_ingested_by_backend_endpoint(self):
        event_id = str(uuid.uuid4())
        raw_json = construct_simulated_esp32_json(
            event_id=event_id,
            station_id="AWS-CHN-024",
            device_id="esp32-node-246F2801A2B4",
            observed_at_iso=datetime.now(timezone.utc).isoformat(),
            sequence_number=105,
            temp_c=30.5,
            pressure_hpa=1011.0,
            humidity_pct=60.0,
            status="ok",
            anomaly_flag=False,
            anomaly_type=None,
        )

        response = self.client.post(
            "/api/ingest/observation",
            headers={"Content-Type": "application/json"},
            content=raw_json,
        )

        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertTrue(res_data["accepted"])
        self.assertEqual(res_data["event_id"], event_id)
        self.assertEqual(res_data["station_id"], "AWS-CHN-024")


if __name__ == "__main__":
    unittest.main()
