# SkyGuard AI — Observation Ingestion API

**Status:** Step 4 Complete  
**Endpoint:** `POST /api/ingest/observation`  
**Purpose:** Canonical backend entry point for HTTP observation packets emitted by edge devices (e.g. ESP32 microcontroller nodes or virtual edge nodes).

---

## 1. Endpoint & HTTP Method

- **Method:** `POST`
- **Path:** `/api/ingest/observation`
- **Content-Type:** `application/json`
- **Authentication / Authorization:** None required in this phase (internal / trusted network).

---

## 2. Request Schema

The endpoint consumes the canonical `ObservationPacket` model defined in [`model/contracts.py`](file:///d:/sky/LULLABY-SKYGUARD-main/model/contracts.py):

| Field | Type | Required | Description |
|---|---|---|---|
| `event_id` | `str` (UUIDv4 format, 1–64 chars) | Yes | Unique identifier for the observation packet |
| `station_id` | `str` (1–64 chars) | Yes | Authority station identifier (e.g. `AWS-CHN-024`) |
| `device_id` | `str` (1–64 chars) | Yes | Originating hardware device / node identifier (e.g. `esp32-node-chennai-01`) |
| `observed_at` | `datetime` (ISO 8601, timezone-aware) | Yes | Exact UTC or timezone-offset sensor observation timestamp |
| `sequence_number` | `int` (non-negative integer) | Yes | Monotonic packet counter per edge device |
| `readings` | `ObservationReadings` | Yes | Sensor measurements object |
| `readings.temperature_c` | `float` or `null` | No | Temperature in Celsius |
| `readings.pressure_hpa` | `float` or `null` | No | Atmospheric pressure in hectopascals |
| `readings.humidity_pct` | `float` or `null` | No | Relative humidity percentage |
| `edge_inference` | `EdgeInference` | Yes | Edge-evaluated verdict and inference metadata |
| `edge_inference.status` | `str` (`"ok"`, `"anomaly_detected"`, `"degraded"`, `"unknown"`) | Yes | High-level edge evaluation status |
| `edge_inference.anomaly_flag` | `bool` | Yes | Boolean flag indicating edge-detected anomaly |
| `edge_inference.anomaly_type` | `str` or `null` | No | Rule or anomaly name (e.g. `"physical_bounds"`, `"dropout"`, `"sensor_fail_low"`) |
| `edge_inference.score` | `float` or `null` | No | Local anomaly score (0.0–1.0 or confidence metric) |
| `edge_inference.score_type` | `str` or `null` | No | Score representation type (e.g. `"probability"`, `"distance"`, `"heuristic"`) |
| `edge_inference.model_version` | `str` | Yes | Edge firmware rule/model identifier (e.g. `"edge_rules_v1.0.0"`) |
| `edge_inference.inference_method` | `str` (`"rules"`, `"tflite_micro"`, `"heuristic"`, `"statistical"`) | Yes | Inference engine/technique utilized at edge |
| `device_metadata` | `DeviceMetadata` or `null` | No | Hardware diagnostics (firmware version, battery voltage, signal strength) |

---

## 3. Complete Request Example

```json
{
  "event_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "station_id": "AWS-CHN-024",
  "device_id": "esp32-node-chennai-01",
  "observed_at": "2026-09-23T14:30:00Z",
  "sequence_number": 1042,
  "readings": {
    "temperature_c": 28.5,
    "pressure_hpa": 1012.2,
    "humidity_pct": 65.4
  },
  "edge_inference": {
    "status": "ok",
    "anomaly_flag": false,
    "anomaly_type": null,
    "score": null,
    "score_type": null,
    "model_version": "edge_rules_v1.0.0",
    "inference_method": "rules"
  },
  "device_metadata": {
    "firmware_version": "1.0.0",
    "battery_voltage": 3.92,
    "signal_strength": -68.0
  }
}
```

---

## 4. Response Schema

The endpoint returns `ObservationIngestResponse` defined in [`model/contracts.py`](file:///d:/sky/LULLABY-SKYGUARD-main/model/contracts.py):

| Field | Type | Description |
|---|---|---|
| `accepted` | `bool` | True if the observation was accepted and processed |
| `event_id` | `str` | Event identifier echoed from request |
| `station_id` | `str` | Station identifier echoed from request |
| `device_id` | `str` | Device identifier echoed from request |
| `observed_at` | `datetime` | Observation timestamp preserved from request |
| `sequence_number` | `int` | Sequence number echoed from request |
| `sequence_status` | `str` | Sequence evaluation: `"ok"`, `"initial"`, `"gap_detected"`, `"repeated_sequence"`, `"out_of_order"` |
| `ingestion_status` | `str` | Processing status: `"processed"`, `"accepted_with_warnings"`, `"queued"` |
| `message` | `str` or `null` | Optional descriptive status message |

---

## 5. Complete Response Example

```json
{
  "accepted": true,
  "event_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "station_id": "AWS-CHN-024",
  "device_id": "esp32-node-chennai-01",
  "observed_at": "2026-09-23T14:30:00Z",
  "sequence_number": 1042,
  "sequence_status": "ok",
  "ingestion_status": "processed",
  "message": "Observation ingested and processed by central pipeline"
}
```

---

## 6. Ingestion Behavior & Validation Rules

### Station Validation
- Validates that `station_id` exists in the system's authoritative station metadata registry (`state_manager.metadata` / `station_id`).
- If `station_id` is unknown, the endpoint returns **`404 Not Found`** with `detail: "Unknown station_id: '<station_id>'."`.
- Does **NOT** silently auto-create unregistered stations.

### Device Identity Handling
- The `device_id` is structurally validated and preserved throughout processing.
- In this migration step, device registration is verified structurally. Authoritative persistent device registries and MAC/certificate authorization will be integrated in future steps.

### Timestamp Handling
- `observed_at` must be a valid ISO 8601 timezone-aware datetime (validated via Pydantic validator).
- The sensor observation timestamp is **strictly preserved** and never overwritten with the server's current wall clock.
- If no server freshness policy is configured, observations are accepted without arbitrary rejection thresholds.

### Duplicate Event Detection
- Tracked via an isolated in-memory registry of known `event_id` keys (`ObservationIngestionService._seen_event_ids`).
- First arrival of `event_id`: **Accepted (200 OK)**.
- Subsequent arrival of the same `event_id`: **Rejected (409 Conflict)** with `detail: "Duplicate event_id: '<event_id>'."`.
- Designed to be swapped for persistent database-level deduplication in Step 6.

### Sequence Continuity Tracking
- Maintained per `(station_id, device_id)` pair in memory.
- Transitions:
  - **`initial`**: First packet received from this station/device pair.
  - **`ok`**: `sequence_number == last_seq + 1`.
  - **`gap_detected`**: `sequence_number > last_seq + 1` (missing intermediate packets; packet is accepted with data-quality warning).
  - **`repeated_sequence`**: `sequence_number == last_seq` with a different `event_id`.
  - **`out_of_order`**: `sequence_number < last_seq` (packet is accepted and preserved).
- Sequence anomalies are tracked as data-quality telemetry and do not cause indiscriminate packet rejection.

### Missing Data & Null Sensor Readings
- Individual readings (`temperature_c`, `pressure_hpa`, `humidity_pct`) may be `null` due to sensor dropout or partial hardware failure.
- Structurally valid packets with null fields are **accepted** and passed directly to central anomaly detection.
- The ingestion layer does **not** impute, zero-fill, or substitute mean values.

### Raw Data Preservation
- All numeric measurements are forwarded to downstream pipelines verbatim without artificial clipping or clamping (e.g. `temperature_c = 70.0` or `humidity_pct = 150.0` remain intact for central physics and anomaly evaluation).

### Edge Result Preservation
- Complete `edge_inference` structure is stored and forwarded alongside sensor telemetry.
- `edge_inference.anomaly_flag` is **not** conflated with central detection verdicts.

---

## 7. Error Responses

| Status Code | Scenario | Response Body Structure |
|---|---|---|
| `404 Not Found` | Unregistered `station_id` | `{"detail": "Unknown station_id: 'AWS-XYZ-999'."}` |
| `409 Conflict` | Duplicate `event_id` | `{"detail": "Duplicate event_id: 'evt-12345'."}` |
| `422 Unprocessable Entity` | Schema or contract validation failure | FastAPI validation error object detailing missing/invalid fields |
| `500 Internal Server Error` | Unhandled backend exception | `{"detail": "Internal error during observation ingestion: <message>"}` |

---

## 8. Processing Flow

```
                  POST /api/ingest/observation
                               │
               [1] Pydantic Schema Validation
                     (ObservationPacket)
                               │
            [2] ObservationIngestionService Check
              ├─ Station Identity in Registry? (404 if missing)
              ├─ Duplicate event_id?           (409 if duplicate)
              └─ Sequence Number Transition    (ok / gap / repeat / ooo)
                               │
            [3] Hand-off to StateManager Pipeline
              ├─ Ingest raw reading & timestamp into station history buffer
              ├─ Execute Central Anomaly Detection (score_reading)
              ├─ Update Simulator latest cache (raw_reading + verdict + edge_inference)
              └─ Broadcast update to active WebSockets
                               │
            [4] ObservationIngestResponse (200 OK)
```

---

## 9. Current Limitations & Deferred Items

The following items are intentionally deferred to subsequent migration steps:

1. **Edge-Central Comparator:** Comparing edge verdicts vs. central AI verdicts is deferred to Step 5.
2. **Persistent Storage for Edge Metadata:** Step 6 will introduce TimescaleDB / database tables for edge inference metadata and persistent deduplication.
3. **ESP32 Firmware:** Firmware implementation for physical ESP32 boards is deferred to Step 7.
4. **Simulator Integration & Virtual ESP32:** Background generation of `ObservationPacket` streams will be added in Step 8.
5. **Frontend UI Changes:** Hybrid telemetry UI tabs and comparator displays will be added in Step 9.
