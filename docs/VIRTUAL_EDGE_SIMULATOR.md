# Virtual Edge Simulator Specification & Integration Guide

## 1. Simulator Purpose

The **Virtual Edge Simulator** (`model/simulator.py`) is the software representation of a distributed fleet of Level 1 Edge devices (such as future ESP32 or RP2040 microcontrollers) within SkyGuard AI.

Its core design tenet is **architectural symmetry**:
> The simulator intentionally uses the **exact same canonical observation data contract** (`ObservationPacket`) and the **exact same backend ingestion API** (`POST /api/ingest/observation`) that physical ESP32 hardware will use in production.

The simulator is **NOT** an independent second backend pipeline. It does not bypass ingestion, does not directly mutate central state buffers, and does not directly invoke persistence or fake WebSocket events.

---

## 2. Architecture & Ingestion Flow

```
+─────────────────────────────────────────────────────────────+
|               Virtual Edge Simulator Node                   |
|                                                             |
|  1. Virtual Sensor Readings (Open-Meteo / Labeled CSV)      |
|  2. Level 1 Edge Inference (model.edge_rules)                |
|  3. Canonical ObservationPacket Construction               |
+─────────────────────────────────────────────────────────────+
                              │
                              │ HTTP POST /api/ingest/observation
                              ▼
+─────────────────────────────────────────────────────────────+
|               Backend Ingestion Service                     |
|                                                             |
|  4. Station Validation & Deduplication (event_id)           |
|  5. Sequence Continuity Evaluation                          |
|  6. Raw Sensor Reading Extraction Verbatim                  |
|  7. Level 2 Central AI Detection (StateManager)             |
|  8. Level 1 Edge vs Level 2 Central Comparison              |
|  9. Full Hybrid Lifecycle Persistence (HistoryStore)        |
| 10. Real-Time WebSocket Broadcasts (/ws/live)              |
+─────────────────────────────────────────────────────────────+
```

---

## 3. Canonical ObservationPacket Generation

Every simulated reading is converted into a canonical `ObservationPacket` conforming to `model/contracts.py`:

```python
ObservationPacket(
    event_id="e7189390-2b6b-4c4a-90cd-18dc13d1676c",  # Unique UUID per observation
    station_id="AWS-CHN-024",                          # Registered meteorological station
    device_id="sim-node-AWS-CHN-024",                  # Stable virtual edge device identifier
    observed_at="2026-09-23T17:34:31.327012+00:00",    # Timezone-aware UTC timestamp
    sequence_number=10,                                # Monotonically increasing sequence
    readings=ObservationReadings(
        temperature_c=31.4,                            # Raw °C (unclamped)
        pressure_hpa=1011.2,                           # Raw hPa (unclamped)
        humidity_pct=74.5,                             # Raw % (unclamped)
    ),
    edge_inference=EdgeInference(
        status="ok",                                   # 'ok' | 'anomaly_detected'
        anomaly_flag=False,                            # Edge boolean flag
        anomaly_type=None,                             # 'physical_bounds' | 'dropout' | 'sensor_fail_low'
        score=None,
        score_type=None,
        model_version="edge_rules_v1.0.0",             # From model.edge_rules
        inference_method="rules",                      # 'rules'
    ),
    device_metadata=DeviceMetadata(
        firmware_version="sim_edge_v1.0.0",            # Virtual edge firmware tag
        battery_voltage=None,                          # Unmetered / wall-powered
        signal_strength=None,                          # Unmetered
    ),
)
```

---

## 4. Edge Inference Generation

The simulator runs Level 1 Edge inference prior to creating the packet by invoking the standardized rule engine:

```python
edge_inference = run_edge_inference(
    temp_c=raw_reading.get("temperature_c"),
    pressure_hpa=raw_reading.get("pressure_hpa"),
    humidity_pct=raw_reading.get("humidity_pct"),
)
```

- Deterministic checks evaluated:
  - `dropout`: Missing, None, or NaN values on any sensor channel.
  - `sensor_fail_low`: Hardware electrical rail collapses (e.g. $T \le -8^\circ\text{C}$, $P \le 150\text{ hPa}$, $RH \le 3\%$).
  - `physical_bounds`: Meteorological physical extremes (e.g. $T \notin [-50, 60]^\circ\text{C}$, $P \notin [870, 1085]\text{ hPa}$, $RH \notin [0, 100]\%$).

---

## 5. Station & Device Mapping

- **Station IDs**: Sourced directly from `data/stations_metadata.csv` (e.g., `AWS-CHN-001` through `AWS-CHN-101`).
- **Device IDs**: Generated using a deterministic, stable convention:
  $$\text{device\_id} = \texttt{"sim-node-" } + \text{station\_id}$$
- Each simulated station retains its own stable device identity throughout the process lifecycle.

---

## 6. Sequence Number Handling

- Sequence numbers are tracked on a per-device `(station_id, device_id)` basis.
- The counter begins at `0` and increments monotonically with every new observation packet ($1, 2, 3, \ldots$).
- **Retry Invariant**: A retry transmission of the **same observation packet** reuses the **exact same `sequence_number` and `event_id`**.

---

## 7. Timestamp Handling

- All observation timestamps (`observed_at`) are guaranteed to be **timezone-aware UTC `datetime` objects**.
- In **Live Mode**, the timestamp represents the observation time provided by Open-Meteo or current UTC wall-clock time.
- In **Replay Mode**, the historical UTC timestamp embedded within labeled CSV files is preserved to ensure detector rolling windows and time-series history follow measurement time.

---

## 8. Anomaly Injection Behavior

- Triggered via `POST /api/inject-anomaly` (or CLI replay mode).
- The simulator loads labeled historical dataset files (`*_labeled.csv`) containing pre-injected sensor faults (spikes, drift, frozen values, dropouts, physical bound violations).
- During simulation ticks:
  1. Raw reading with injected fault is fetched.
  2. Level 1 edge inference evaluates the reading locally.
  3. `ObservationPacket` is constructed and posted to `POST /api/ingest/observation`.
  4. Central AI independently evaluates the same observation.
  5. The comparison engine analyzes Level 1 vs Level 2 diagnostic consensus/divergence.
  6. Results are persisted to `HistoryStore`.

---

## 9. HTTP Ingestion Path & Transport

- Ingest URL: Configurable via constructor or `INGEST_URL` environment variable (default: `http://127.0.0.1:8000/api/ingest/observation`).
- In FastAPI server mode, the shared client dispatches directly to the application router via ASGI/HTTP.
- Serialization: Uses canonical `packet.model_dump(mode="json")`.

---

## 10. Retry Behavior & Deduplication

- The simulator supports bounded retry logic with backoff.
- When retrying:
  - `event_id`, `sequence_number`, `observed_at`, `readings`, and `edge_inference` remain identical.
- If the backend receives an `event_id` that has already been ingested, it returns `HTTP 409 Conflict`.
- The simulator catches the `409 Conflict` and safely ignores duplicate acknowledgment without crashing.

---

## 11. Failure Handling

| HTTP Code | Condition | Simulator Action |
|:---|:---|:---|
| `200 OK` | Observation successfully ingested | Record timestamp, advance local cache |
| `404 Not Found` | Unknown or unregistered station | Log error, skip station tick, do not crash |
| `409 Conflict` | Duplicate event submission | Log deduplication confirmation, continue |
| `422 Unprocessable` | Schema validation error | Log validation failure details, continue |
| `5xx / Connection` | Backend unavailable / server error | Retry up to `max_retries`, log failure, continue |

---

## 12. Persistence Path

Simulator observations reach persistence **exclusively** through the backend ingestion service:
1. `ObservationPacket` received at `POST /api/ingest/observation`.
2. Central AI generates `central_verdict`.
3. Comparator produces `EdgeCentralComparison`.
4. Ingestion service calls `HistoryStore.persist_hybrid_lifecycle(packet, verdict, comparison, source)`.

The simulator never writes directly to TimescaleDB or CSV history files.

---

## 13. WebSocket Path

Live streaming events (`TELEMETRY_TICK`, `ANOMALY_EVENT`, `OBSERVATION_INGESTED`) are broadcasted **exclusively** by the backend `ObservationIngestionService` when an `ObservationPacket` is successfully processed.

The simulator does not emit independent fake telemetry events.

---

## 14. How to Run the Simulator

### Running inside FastAPI Server
When starting the SkyGuard AI backend, the simulator starts automatically in the lifespan task:
```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

### Running Standalone / Scripted
```python
import asyncio
from model.simulator import create_simulator_state

async def main():
    sim = create_simulator_state(ingest_url="http://127.0.0.1:8000/api/ingest/observation")
    await sim.tick()

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 15. How to Verify an Observation End-to-End

```python
import asyncio
from datetime import datetime, timezone
from model.simulator import create_simulator_state

async def verify_roundtrip():
    sim = create_simulator_state()
    
    # 1. Build canonical packet
    packet = sim.build_observation_packet(
        station_id="AWS-CHN-024",
        raw_reading={"temperature_c": 29.5, "pressure_hpa": 1010.0, "humidity_pct": 65.0},
        observed_at=datetime.now(timezone.utc),
    )
    
    # 2. Submit via HTTP ingestion
    resp = await sim.submit_observation(packet)
    assert resp.accepted is True
    
    # 3. Verify persistence roundtrip
    record = sim.manager.history.get_hybrid_record(packet.event_id)
    assert record is not None
    assert record["event_id"] == packet.event_id
    assert record["raw_observation"]["temperature_c"] == 29.5
    assert "edge_inference" in record
    assert "central_verdict" in record
    assert "comparison" in record

asyncio.run(verify_roundtrip())
```

---

## 16. Current Limitations

1. **Physical Sensor Drivers**: The virtual simulator generates data via Open-Meteo weather API and CSV replay datasets; real I2C/SPI hardware sensor drivers (BMP280, DHT22) reside in ESP32 firmware (Step 8).
2. **Network Transport**: In standalone testing within the same process, ASGI transport is used; external physical nodes communicate via Wi-Fi/HTTP POST.
