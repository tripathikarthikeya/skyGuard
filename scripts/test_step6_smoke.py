"""
scripts/test_step6_smoke.py

FastAPI Lifespan and End-to-End API Smoke Test for Step 6.
"""

from datetime import datetime, timezone
import json
import sys
from pathlib import Path
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
from main import app

def run_smoke_test():
    print("=" * 70)
    print("[RUNNING] SKYGUARD AI STEP 6 FASTAPI SMOKE TEST")
    print("=" * 70)

    with TestClient(app) as client:
        # 1. /api/stations
        resp = client.get("/api/stations")
        assert resp.status_code == 200, f"/api/stations failed: {resp.status_code}"
        stations = resp.json()
        print(f"[OK] /api/stations: {len(stations)} stations returned")

        # 2. /api/system-status
        resp = client.get("/api/system-status")
        assert resp.status_code == 200, f"/api/system-status failed: {resp.status_code}"
        print(f"[OK] /api/system-status: {resp.json()}")

        # 3. POST /api/ingest/observation (Live Ingestion Pipeline with Hybrid Persistence)
        packet_payload = {
            "event_id": f"smoke-evt-{int(datetime.now().timestamp())}",
            "station_id": "AWS-CHN-024",
            "device_id": "esp32-node-01",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "sequence_number": 999,
            "readings": {
                "temperature_c": 31.4,
                "pressure_hpa": 1006.2,
                "humidity_pct": 68.0,
            },
            "edge_inference": {
                "status": "ok",
                "anomaly_flag": False,
                "anomaly_type": None,
                "score": 0.0,
                "score_type": "rule_score",
                "model_version": "edge_v1.0.0",
                "inference_method": "rules",
            },
            "device_metadata": {
                "firmware_version": "v2.1.0",
                "battery_voltage": 3.9,
                "signal_strength": -70.0,
            },
        }

        resp = client.post("/api/ingest/observation", json=packet_payload)
        assert resp.status_code == 200, f"Ingestion failed: {resp.status_code} {resp.text}"
        ingest_data = resp.json()
        assert ingest_data["accepted"] is True
        print(f"[OK] POST /api/ingest/observation: accepted (event_id={ingest_data['event_id']}, status={ingest_data['ingestion_status']})")

        # 4. /api/current-reading
        resp = client.get("/api/current-reading?station_id=AWS-CHN-024")
        assert resp.status_code == 200, f"/api/current-reading failed: {resp.status_code}"
        print(f"[OK] /api/current-reading")

        # 5. /api/trends
        resp = client.get("/api/trends?station_id=AWS-CHN-024&hours=24")
        assert resp.status_code == 200, f"/api/trends failed: {resp.status_code}"
        print(f"[OK] /api/trends: {len(resp.json())} points")

        # 6. /api/network-status
        resp = client.get("/api/network-status")
        assert resp.status_code == 200, f"/api/network-status failed: {resp.status_code}"
        print(f"[OK] /api/network-status: {resp.json()['overall_status']}")

        # 7. /api/anomalies/latest
        resp = client.get("/api/anomalies/latest?station_id=AWS-CHN-024")
        assert resp.status_code in (200, 404), f"/api/anomalies/latest failed: {resp.status_code}"
        print(f"[OK] /api/anomalies/latest (status={resp.status_code})")

        # 8. /api/sensor-health
        resp = client.get("/api/sensor-health?station_id=AWS-CHN-024")
        assert resp.status_code == 200, f"/api/sensor-health failed: {resp.status_code}"
        print(f"[OK] /api/sensor-health")

        # 9. Verify round-trip persistence through HistoryStore
        sim = app.state.sim
        rec = sim.manager.history.get_hybrid_record(packet_payload["event_id"])
        assert rec is not None, "Record was not persisted in HistoryStore!"
        assert rec["raw_observation"]["temperature_c"] == 31.4
        assert rec["edge_inference"]["status"] == "ok"
        assert rec["central_verdict"] is not None
        assert rec["comparison"] is not None
        print(f"[OK] Hybrid HistoryStore Readback: all 4 layers reconstructed independently")

        # 10. WebSocket Live Endpoint
        with client.websocket_connect("/ws/live") as websocket:
            data = websocket.receive_json()
            assert data["type"] == "CONNECTION_READY"
            websocket.send_text("ping")
            pong = websocket.receive_text()
            assert pong == "pong"
            print("[OK] WebSocket /ws/live: Handshake & Ping/Pong")

    print("=" * 70)
    print("[SUCCESS] ALL STEP 6 FASTAPI SMOKE TESTS PASSED!")
    print("=" * 70)

if __name__ == "__main__":
    run_smoke_test()
