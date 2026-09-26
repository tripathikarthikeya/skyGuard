# SkyGuard AI — Canonical Observation Data Contract

> **Document Status**: Authoritative Specification (Step 2 Migration)  
> **Schema Definition Module**: [`model/contracts.py`](file:///d:/sky/LULLABY-SKYGUARD-main/model/contracts.py)  
> **Test Suite**: [`tests/test_observation_contract.py`](file:///d:/sky/LULLABY-SKYGUARD-main/tests/test_observation_contract.py)  

---

## 1. Purpose

The **Canonical Observation Data Contract** defines the single, standardized data interchange packet for environmental telemetry and edge inference metadata across the SkyGuard AI system. It provides an unambiguous schema definition implemented via Pydantic v2 in `model/contracts.py`.

---

## 2. Architecture Role

In the Hybrid Edge + Central AI architecture, this contract acts as the universal boundary interface across all tiers:

```
┌─────────────────────────────────────────────────────────┐
│ LEVEL 1: ESP32 Edge Device (or Virtual Edge Simulator) │
└────────────────────────────┬────────────────────────────┘
                             │ Canonical ObservationPacket (JSON)
                             ▼
┌─────────────────────────────────────────────────────────┐
│ Backend Ingestion API (POST /api/ingest/observation)   │
└────────────────────────────┬────────────────────────────┘
                             │ Validated ObservationPacket
                             ▼
┌─────────────────────────────────────────────────────────┐
│ LEVEL 2: Central AI & Verification Engine               │
│  - Independent Validation & 22-Feature Engineering      │
│  - Central Anomaly Scoring (Isolation Forest + Rules)   │
│  - Edge vs Central Verdict Comparison Matrix            │
└────────────────────────────┬────────────────────────────┘
                             │
              ┌──────────────┴──────────────┐
              ▼                             ▼
┌───────────────────────────┐ ┌───────────────────────────┐
│ TimescaleDB / CSV Storage │ │ React Frontend Dashboard  │
└───────────────────────────┘ └───────────────────────────┘
```

---

## 3. Complete Canonical JSON Example

```json
{
  "event_id": "evt-20260923-0001",
  "station_id": "AWS-CHN-024",
  "device_id": "esp32-node-chennai-01",
  "observed_at": "2026-09-23T13:00:00Z",
  "sequence_number": 1001,
  "readings": {
    "temperature_c": 25.4,
    "pressure_hpa": 1008.2,
    "humidity_pct": 72.5
  },
  "edge_inference": {
    "status": "ok",
    "anomaly_flag": false,
    "anomaly_type": null,
    "score": null,
    "score_type": null,
    "model_version": "edge_v1.0.0",
    "inference_method": "rules"
  },
  "device_metadata": {
    "firmware_version": "1.0.0",
    "battery_voltage": 3.95,
    "signal_strength": -65.0
  }
}
```

---

## 4. Top-Level Fields

| Field Name | Type | Required? | Constraints | Description |
|---|---|---|---|---|
| `event_id` | `string` | **Yes** | Non-empty, non-whitespace | Unique identifier for the observation event. Used for duplicate detection and tracing. |
| `station_id` | `string` | **Yes** | Non-empty, non-whitespace | Monitored meteorological station identifier (e.g. `"AWS-CHN-024"`). |
| `device_id` | `string` | **Yes** | Non-empty, non-whitespace | Physical hardware device identifier (e.g. `"esp32-node-chennai-01"`). |
| `observed_at` | `string (ISO-8601)` | **Yes** | Timezone-aware datetime | Exact timestamp when the sensor reading was captured by the hardware. |
| `sequence_number` | `integer` | **Yes** | `ge=0` (non-negative) | Monotonically increasing packet sequence counter emitted by the edge device. |
| `readings` | `object` | **Yes** | Sub-model `ObservationReadings` | Original raw sensor measurements. |
| `edge_inference` | `object` | **Yes** | Sub-model `EdgeInference` | Level 1 edge validation flags and metadata. |
| `device_metadata` | `object` | **Yes** | Sub-model `DeviceMetadata` | Edge device operational telemetry. |

---

## 5. `readings` Fields

The `readings` sub-object encapsulates the original measurements captured by the transducers:

| Field Name | Type | Required in Dict? | Nullable? | Description |
|---|---|---|---|---|
| `temperature_c` | `float` | **Yes** | **Yes** (`null` on channel dropout) | Ambient dry-bulb temperature in degrees Celsius (°C). |
| `pressure_hpa` | `float` | **Yes** | **Yes** (`null` on channel dropout) | Atmospheric surface pressure in hectopascals (hPa). |
| `humidity_pct` | `float` | **Yes** | **Yes** (`null` on channel dropout) | Relative humidity percentage (% RH). |

---

## 6. `edge_inference` Fields

The `edge_inference` sub-object captures Level 1 validation and embedded model results:

| Field Name | Type | Required? | Nullable? | Description |
|---|---|---|---|---|
| `status` | `string` | **Yes** | No | Execution status string (e.g. `"ok"`, `"anomaly_detected"`, `"sensor_degraded"`, `"error"`). |
| `anomaly_flag` | `boolean` | **Yes** | No | `true` if edge rules or embedded ML flagged an anomaly; `false` otherwise. |
| `anomaly_type` | `string` | No | **Yes** | Edge fault classification category (e.g. `"physical_bounds"`, `"dropout"`, `"sensor_fail_low"`) or `null`. |
| `score` | `float` | No | **Yes** | Quantitative severity or heuristic score ($0.0 - 100.0$) produced at the edge. |
| `score_type` | `string` | No | **Yes** | Score descriptor (e.g. `"rule_score"`, `"raw_score"`, `"normalized_score"`). |
| `model_version` | `string` | **Yes** | No | Edge rule engine or ML model version string (e.g. `"edge_v1.0.0"`). |
| `inference_method` | `string` | **Yes** | No | Mechanism used on edge (e.g. `"rules"`, `"embedded_ml"`, `"rules+embedded_ml"`). |

---

## 7. `device_metadata` Fields

The `device_metadata` sub-object captures telemetry relating to device health:

| Field Name | Type | Required? | Nullable? | Description |
|---|---|---|---|---|
| `firmware_version` | `string` | **Yes** | No | Firmware version deployed on the microcontroller (e.g. `"1.0.0"`). |
| `battery_voltage` | `float` | No | **Yes** | Supply voltage in Volts (V), or `null` for mains-powered / unmetered nodes. |
| `signal_strength` | `float` | No | **Yes** | Wireless telemetry link metric (unspecified unit, e.g. RSSI in dBm or link percentage). |

---

## 8. Required vs. Optional Fields Matrix

```
ObservationPacket
├── event_id (REQUIRED, string)
├── station_id (REQUIRED, string)
├── device_id (REQUIRED, string)
├── observed_at (REQUIRED, timezone-aware datetime)
├── sequence_number (REQUIRED, non-negative integer)
├── readings (REQUIRED, object)
│   ├── temperature_c (REQUIRED KEY, float or null)
│   ├── pressure_hpa (REQUIRED KEY, float or null)
│   └── humidity_pct (REQUIRED KEY, float or null)
├── edge_inference (REQUIRED, object)
│   ├── status (REQUIRED, string)
│   ├── anomaly_flag (REQUIRED, boolean)
│   ├── anomaly_type (OPTIONAL, string or null)
│   ├── score (OPTIONAL, float or null)
│   ├── score_type (OPTIONAL, string or null)
│   ├── model_version (REQUIRED, string)
│   └── inference_method (REQUIRED, string)
└── device_metadata (REQUIRED, object)
    ├── firmware_version (REQUIRED, string)
    ├── battery_voltage (OPTIONAL, float or null)
    └── signal_strength (OPTIONAL, float or null)
```

---

## 9. Data Types & Strictness

- **Pydantic v2 Base**: All models derive from `pydantic.BaseModel`.
- **Extra Fields Forbidden**: `model_config = ConfigDict(extra="forbid")` is enforced on `ObservationPacket`, `ObservationReadings`, `EdgeInference`, and `DeviceMetadata`. Unknown top-level or nested keys result in immediate validation rejection.
- **Empty String Rejection**: Identity and version strings (`event_id`, `station_id`, `device_id`, `model_version`, `inference_method`, `firmware_version`) cannot be empty or whitespace-only.

---

## 10. Timestamp Semantics

- **Source of Truth**: `observed_at` represents the hardware sampling time on the edge device.
- **Timezone Awareness**: Timestamps **must** include timezone information (e.g. `"2026-09-23T13:00:00Z"` or `"2026-09-23T18:30:00+05:30"`). Naive datetimes (e.g. `"2026-09-23T13:00:00"`) are explicitly rejected by schema validators.
- **No Ingestion Time Substitution**: The backend must never overwrite `observed_at` with server receipt time. Server receipt time will be tracked in separate server-side audit columns during later steps.

---

## 11. Sequence Number Semantics

- `sequence_number` is an integer $\ge 0$ generated sequentially by the device firmware.
- Enables downstream detection of:
  - Dropped / lost transmission packets.
  - Reordered packets arriving over unreliable networks.
  - Replay attacks or duplicate packets.

---

## 12. Raw Data Preservation Rule

The observation contract establishes the absolute rule that **original raw sensor values must never be destructively altered, clamped, or replaced at the ingestion boundary**.

- If an atmospheric sensor reads an extreme or physically impossible value ($75^\circ\text{C}$ or $140\%$ humidity), that value must be ingested as received so that Central AI, SHAP explainability, and sensor circuit breakers can evaluate it.
- Value imputation, corrections, and suggested values are computed separately by Level 2 algorithms and stored alongside the original reading, never in place of it.

---

## 13. Structural Validation vs. Anomaly Detection

| Dimension | Structural Validation (`model/contracts.py`) | Anomaly Detection (`model/detect.py`) |
|---|---|---|
| **Question Answered** | "Is this packet correctly formed, typed, and well-structured?" | "Is this reading scientifically unusual, drifted, or faulty?" |
| **Execution Layer** | Schema Ingestion Layer (Pydantic) | Central AI & Edge Rule Engine |
| **Handling of $T = 70.0^\circ\text{C}$** | **VALID** (Numeric float conforms to schema) | **ANOMALOUS** (Flags physical bounds violation) |
| **Handling of $T = \text{"seventy"}$** | **INVALID** (Rejection with 422 Unprocessable Entity) | *Never reaches detection layer* |

---

## 14. Edge Score Semantics

- The `score` field in `edge_inference` is an optional quantitative heuristic or rule confidence metric.
- It is **explicitly not a calibrated statistical probability**.
- The `score_type` field explicitly describes the meaning of the score (e.g., `"rule_score"`, `"raw_score"`, `"normalized_score"`).

---

## 15. Versioning Considerations

- `edge_inference.model_version`: Tracks the version of the edge detection rules/model deployed on the device (e.g. `"edge_v1.0.0"`).
- `device_metadata.firmware_version`: Tracks the overall microcontroller firmware build (e.g. `"1.0.4"`).
- Enables the Central AI to dynamically calibrate how it interprets edge flags based on the capabilities of specific firmware revisions.

---

## 16. Example Valid Packet

```json
{
  "event_id": "evt-20260923-1002",
  "station_id": "AWS-MUM-007",
  "device_id": "esp32-mumbai-02",
  "observed_at": "2026-09-23T14:30:00+05:30",
  "sequence_number": 2045,
  "readings": {
    "temperature_c": 31.2,
    "pressure_hpa": 1012.4,
    "humidity_pct": 84.0
  },
  "edge_inference": {
    "status": "ok",
    "anomaly_flag": false,
    "anomaly_type": null,
    "score": null,
    "score_type": null,
    "model_version": "edge_v1.0.0",
    "inference_method": "rules"
  },
  "device_metadata": {
    "firmware_version": "1.0.0",
    "battery_voltage": 3.88,
    "signal_strength": -72.0
  }
}
```

---

## 17. Example Structurally Invalid Packet (Rejected by Contract)

```json
{
  "event_id": "",
  "station_id": "AWS-CHN-024",
  "device_id": "esp32-node-01",
  "observed_at": "2026-09-23 13:00:00",
  "sequence_number": -5,
  "readings": {
    "temperature_c": "invalid_string",
    "pressure_hpa": 1008.2
  },
  "edge_inference": {
    "anomaly_flag": false
  },
  "device_metadata": {
    "firmware_version": "1.0.0"
  },
  "unknown_injected_field": true
}
```

**Rejection Reasons**:
1. `event_id` is an empty string.
2. `observed_at` is a naive timestamp without timezone offset.
3. `sequence_number` is negative (`-5 < 0`).
4. `readings.temperature_c` is a string instead of a float/null.
5. `readings.humidity_pct` key is missing from `readings`.
6. `edge_inference.status`, `model_version`, and `inference_method` are missing.
7. `unknown_injected_field` violates `extra="forbid"`.

---

## 18. Example Structurally Valid but Meteorologically Anomalous Packet

```json
{
  "event_id": "evt-20260923-1003",
  "station_id": "AWS-DEL-011",
  "device_id": "esp32-delhi-01",
  "observed_at": "2026-09-23T09:15:00Z",
  "sequence_number": 884,
  "readings": {
    "temperature_c": -40.0,
    "pressure_hpa": 120.0,
    "humidity_pct": 0.0
  },
  "edge_inference": {
    "status": "anomaly_detected",
    "anomaly_flag": true,
    "anomaly_type": "sensor_fail_low",
    "score": 95.0,
    "score_type": "rule_score",
    "model_version": "edge_v1.0.0",
    "inference_method": "rules"
  },
  "device_metadata": {
    "firmware_version": "1.0.0",
    "battery_voltage": 3.42,
    "signal_strength": -88.0
  }
}
```

**Behavior**:
- Passes structural validation 100%.
- Level 1 edge correctly identifies hardware-rail collapse (`sensor_fail_low`) with score `95.0`.
- Central AI will independently ingest the original $-40^\circ\text{C}$ / $120\,\text{hPa}$ / $0\%$ readings and confirm the verdict without dropping data.
