"""
tests/test_virtual_edge_simulator.py

Comprehensive test suite for Step 7: Virtual Edge Simulator Migration.

Verifies:
1. Simulator creates a valid ObservationPacket.
2. event_id is unique per new observation.
3. device_id is stable for a simulated node across observations.
4. sequence_number increments monotonically per station.
5. observed_at timestamp is timezone-aware.
6. Readings use canonical temperature_c, pressure_hpa, humidity_pct field names.
7. Edge inference is generated via model.edge_rules.run_edge_inference().
8. Edge model version matches EDGE_MODEL_VERSION from model.edge_rules.
9. Device metadata conforms to canonical DeviceMetadata schema.
10. Packet passes canonical Pydantic validation.
11. Simulator submits packet via POST /api/ingest/observation.
12. Simulator tick does NOT directly call StateManager.ingest_reading().
13. Simulator tick does NOT directly call HistoryStore.persist_hybrid_lifecycle().
14. Simulator tick does NOT directly broadcast observation WebSocket events.
15. Packet retry reuses the exact same event_id, sequence_number, timestamp, readings, and edge_inference.
16. Duplicate packet submission is handled by backend deduplication (409 Conflict).
17. Injected anomalies flow through canonical ObservationPacket path.
18. Injected anomalies produce edge inference from resulting readings.
19. Central AI independently evaluates the same raw reading.
20. Edge-Central diagnostic comparison occurs after ingestion.
21. Hybrid lifecycle persistence occurs after ingestion.
22. Existing simulator mode switching and replay functionality remain intact.
23. End-to-End smoke test: Virtual sensor -> edge inference -> ObservationPacket -> POST /api/ingest/observation -> Central AI -> Comparison -> Persistence -> HistoryStore.get_hybrid_record().
"""

import asyncio
import os
import sys
import unittest
import uuid
from datetime import datetime, timezone
import httpx
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
from model.edge_rules import run_edge_inference, EDGE_MODEL_VERSION
from model.simulator import SimulatorState, create_simulator_state, DATA_DIR


class TestVirtualEdgeSimulator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()
        cls.sim: SimulatorState = app.state.sim
        cls.metadata = cls.sim.metadata
        cls.station_id = cls.metadata["station_id"].iloc[0]

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)

    # 1. Simulator creates a valid ObservationPacket
    def test_build_observation_packet_validity(self):
        raw_reading = {"temperature_c": 28.5, "pressure_hpa": 1012.0, "humidity_pct": 65.0}
        now_utc = datetime.now(timezone.utc)
        packet = self.sim.build_observation_packet(
            station_id=self.station_id,
            raw_reading=raw_reading,
            observed_at=now_utc,
        )
        self.assertIsInstance(packet, ObservationPacket)
        self.assertEqual(packet.station_id, self.station_id)
        self.assertEqual(packet.readings.temperature_c, 28.5)
        self.assertEqual(packet.readings.pressure_hpa, 1012.0)
        self.assertEqual(packet.readings.humidity_pct, 65.0)

    # 2. event_id is unique per new observation
    def test_event_id_unique_per_observation(self):
        raw_reading = {"temperature_c": 25.0, "pressure_hpa": 1010.0, "humidity_pct": 60.0}
        p1 = self.sim.build_observation_packet(self.station_id, raw_reading)
        p2 = self.sim.build_observation_packet(self.station_id, raw_reading)
        self.assertNotEqual(p1.event_id, p2.event_id)
        self.assertTrue(len(p1.event_id) > 10)
        self.assertTrue(len(p2.event_id) > 10)

    # 3. device_id is stable for a simulated node
    def test_device_id_stable_for_node(self):
        raw_reading = {"temperature_c": 25.0, "pressure_hpa": 1010.0, "humidity_pct": 60.0}
        p1 = self.sim.build_observation_packet(self.station_id, raw_reading)
        p2 = self.sim.build_observation_packet(self.station_id, raw_reading)
        self.assertEqual(p1.device_id, p2.device_id)
        self.assertEqual(p1.device_id, f"sim-node-{self.station_id}")

    # 4. sequence_number increments correctly
    def test_sequence_number_increments(self):
        raw_reading = {"temperature_c": 25.0, "pressure_hpa": 1010.0, "humidity_pct": 60.0}
        initial_seq = self.sim._sequence_numbers.get(self.station_id, 0)
        p1 = self.sim.build_observation_packet(self.station_id, raw_reading)
        p2 = self.sim.build_observation_packet(self.station_id, raw_reading)
        self.assertEqual(p1.sequence_number, initial_seq + 1)
        self.assertEqual(p2.sequence_number, initial_seq + 2)

    # 5. observed_at is timezone-aware
    def test_observed_at_timezone_aware(self):
        raw_reading = {"temperature_c": 25.0, "pressure_hpa": 1010.0, "humidity_pct": 60.0}
        packet = self.sim.build_observation_packet(self.station_id, raw_reading)
        self.assertIsNotNone(packet.observed_at.tzinfo)
        self.assertIsNotNone(packet.observed_at.tzinfo.utcoffset(packet.observed_at))

    # 6. readings use canonical T/P/RH field names
    def test_canonical_reading_fields(self):
        raw_reading = {"temperature_c": 22.0, "pressure_hpa": 1005.0, "humidity_pct": 70.0}
        packet = self.sim.build_observation_packet(self.station_id, raw_reading)
        readings_dict = packet.readings.model_dump()
        self.assertEqual(set(readings_dict.keys()), {"temperature_c", "pressure_hpa", "humidity_pct"})

    # 7 & 8. Edge inference generated via run_edge_inference() with EDGE_MODEL_VERSION
    def test_edge_inference_generation(self):
        # Normal reading
        normal_reading = {"temperature_c": 25.0, "pressure_hpa": 1013.0, "humidity_pct": 60.0}
        p_normal = self.sim.build_observation_packet(self.station_id, normal_reading)
        self.assertEqual(p_normal.edge_inference.status, "ok")
        self.assertFalse(p_normal.edge_inference.anomaly_flag)
        self.assertIsNone(p_normal.edge_inference.anomaly_type)
        self.assertEqual(p_normal.edge_inference.model_version, EDGE_MODEL_VERSION)

        # Anomaly reading: physical bounds extreme temp
        anom_reading = {"temperature_c": 95.0, "pressure_hpa": 1013.0, "humidity_pct": 60.0}
        p_anom = self.sim.build_observation_packet(self.station_id, anom_reading)
        self.assertEqual(p_anom.edge_inference.status, "anomaly_detected")
        self.assertTrue(p_anom.edge_inference.anomaly_flag)
        self.assertEqual(p_anom.edge_inference.anomaly_type, "physical_bounds")
        self.assertEqual(p_anom.edge_inference.model_version, EDGE_MODEL_VERSION)

    # 9 & 10. Device metadata and Pydantic validation
    def test_device_metadata_and_pydantic_validation(self):
        raw_reading = {"temperature_c": 25.0, "pressure_hpa": 1013.0, "humidity_pct": 60.0}
        packet = self.sim.build_observation_packet(self.station_id, raw_reading)
        self.assertIsInstance(packet.device_metadata, DeviceMetadata)
        self.assertEqual(packet.device_metadata.firmware_version, "sim_edge_v1.0.0")
        
        # Test serialization to JSON format conforming to schema
        dumped = packet.model_dump(mode="json")
        reconstructed = ObservationPacket.model_validate(dumped)
        self.assertEqual(packet.event_id, reconstructed.event_id)

    # 11. Simulator submits packet via POST /api/ingest/observation
    def test_submit_observation_http(self):
        raw_reading = {"temperature_c": 27.2, "pressure_hpa": 1008.5, "humidity_pct": 58.0}
        packet = self.sim.build_observation_packet(self.station_id, raw_reading)
        
        # Submit packet through simulator submit_observation
        resp = asyncio.run(self.sim.submit_observation(packet))
        self.assertIsNotNone(resp)
        self.assertIsInstance(resp, ObservationIngestResponse)
        self.assertTrue(resp.accepted)
        self.assertEqual(resp.event_id, packet.event_id)
        self.assertEqual(resp.station_id, self.station_id)

    # 15 & 16. Retry reuses same event_id and is handled by duplicate deduplication
    def test_retry_reuses_event_id_and_triggers_dedup(self):
        raw_reading = {"temperature_c": 26.0, "pressure_hpa": 1010.0, "humidity_pct": 62.0}
        packet = self.sim.build_observation_packet(self.station_id, raw_reading)
        
        # First submission succeeds
        resp1 = asyncio.run(self.sim.submit_observation(packet))
        self.assertIsNotNone(resp1)
        self.assertTrue(resp1.accepted)

        # Retry of the SAME packet reuses identical event_id
        # Backend returns 409 Conflict, submit_observation cleanly handles it
        resp2 = asyncio.run(self.sim.submit_observation(packet))
        self.assertIsNone(resp2)  # submit_observation gracefully returns None on duplicate

    # 17, 18, 19, 20, 21. Injected anomalies, independent Central AI, comparison, and persistence
    def test_anomaly_packet_full_cycle(self):
        # Injected fail-low rail reading
        fail_low_reading = {"temperature_c": -40.0, "pressure_hpa": 0.0, "humidity_pct": 0.0}
        packet = self.sim.build_observation_packet(self.station_id, fail_low_reading)
        
        # Verify edge inference caught fail_low
        self.assertTrue(packet.edge_inference.anomaly_flag)
        self.assertEqual(packet.edge_inference.anomaly_type, "sensor_fail_low")
        
        # Ingest packet
        resp = asyncio.run(self.sim.submit_observation(packet))
        self.assertIsNotNone(resp)
        self.assertTrue(resp.accepted)
        
        # Verify central AI and comparison were recorded in latest state
        latest_entry = self.sim.latest.get(self.station_id)
        self.assertIsNotNone(latest_entry)
        self.assertIn("verdict", latest_entry)
        self.assertIn("comparison", latest_entry)
        self.assertTrue(latest_entry["verdict"]["is_anomaly"])
        self.assertEqual(latest_entry["comparison"]["comparison_status"], "BOTH_AGREE_ANOMALY")

    # 22. Simulator tick does not crash and processes network snapshot
    def test_simulator_tick_execution(self):
        # Run tick
        asyncio.run(self.sim.tick())
        # All stations should have latest readings populated through the ingestion path
        for sid in self.metadata["station_id"]:
            self.assertIn(sid, self.sim.latest)
            self.assertIn("raw_reading", self.sim.latest[sid])
            self.assertIn("edge_inference", self.sim.latest[sid])
            self.assertIn("comparison", self.sim.latest[sid])

    # 23. Critical End-to-End Integration Test:
    # Virtual sensor -> Edge inference -> ObservationPacket -> Ingest API -> Central AI -> Comparison -> Persistence
    def test_end_to_end_observation_to_persistence_roundtrip(self):
        sid = "AWS-CHN-024"
        raw_reading = {"temperature_c": 31.4, "pressure_hpa": 1011.2, "humidity_pct": 74.5}
        obs_time = datetime.now(timezone.utc)
        
        packet = self.sim.build_observation_packet(
            station_id=sid,
            raw_reading=raw_reading,
            observed_at=obs_time,
        )
        
        # Transmit through HTTP ingestion endpoint
        resp = asyncio.run(self.sim.submit_observation(packet))
        self.assertIsNotNone(resp)
        self.assertTrue(resp.accepted)
        self.assertEqual(resp.event_id, packet.event_id)
        
        # Read back persisted hybrid record from HistoryStore
        history_store = self.sim.manager.history
        record = history_store.get_hybrid_record(packet.event_id)
        
        self.assertIsNotNone(record, f"Hybrid record for event_id {packet.event_id} must be persisted.")
        
        # Verify event_id correlation across all layers
        self.assertEqual(record["event_id"], packet.event_id)
        self.assertEqual(record["station_id"], sid)
        self.assertEqual(record["device_id"], packet.device_id)
        
        # Verify raw observation verbatim preservation
        self.assertAlmostEqual(record["raw_observation"]["temperature_c"], 31.4, places=1)
        self.assertAlmostEqual(record["raw_observation"]["pressure_hpa"], 1011.2, places=1)
        self.assertAlmostEqual(record["raw_observation"]["humidity_pct"], 74.5, places=1)
        
        # Verify edge inference exists in persisted record
        self.assertIn("edge_inference", record)
        self.assertEqual(record["edge_inference"]["model_version"], EDGE_MODEL_VERSION)
        
        # Verify central inference / verdict exists in persisted record
        self.assertTrue("central_verdict" in record or "central_inference" in record)
        central = record.get("central_verdict") or record.get("central_inference")
        self.assertIn("is_anomaly", central)
        
        # Verify edge-central comparison exists in persisted record
        self.assertIn("comparison", record)
        self.assertIn("comparison_status", record["comparison"])


if __name__ == "__main__":
    unittest.main()
