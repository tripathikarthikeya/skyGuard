# SkyGuard AI — Hybrid Edge + Central AI Architecture Audit & Implementation Plan

> **Document Status**: Step 1 Complete Architectural Audit & Technical Roadmap  
> **Target Architecture**: Two-Level Hybrid Edge (Level 1: ESP32 / Microcontroller) + Central AI (Level 2: Backend & Fusion Engine)  
> **Audit Date**: 2026-09-23  

---

## 1. Executive Summary & Audit Scope

A comprehensive, read-only architectural audit was conducted across the entire **SkyGuard AI** repository to establish the baseline and define an exact engineering roadmap for transitioning from the existing backend-centric simulator/Open-Meteo polling model to an **Authoritative Hybrid Edge + Central AI Architecture**.

### Key Findings
1. **Total Files Deeply Inspected**: 27 core files spanning the backend ML pipeline, simulation, state management, persistence tier, API endpoints, test suite, and the complete React/Vite TypeScript frontend layer.
2. **Current System Topology**: The current system operates as a single-tier server architecture where all sensor data originates either from external Open-Meteo REST calls (`data_fetch.py` / `simulator.py`) or pre-injected CSV replay files. A purely conceptual/isolated Python file (`model/edge_rules.py`) exists with hardcoded rule thresholds, but it is not currently wired into the active ingestion stream (`state.py` or `main.py`).
3. **Firmware & Embedded Code**: **Zero (0)** ESP32, Arduino, PlatformIO, C/C++, MicroPython, or MQTT firmware files currently exist in the repository.
4. **Persistence Architecture**: Dual-store architecture in `history_store.py` featuring a **TimescaleDB hypertable (`sensor_readings`)** primary layer backed by local thread-safe **CSV mirrors** per station in `data/history/`.
5. **Central AI Capabilities**: Highly sophisticated, mature Level 2 engine with 22 engineered features, an Isolation Forest model, an ExtraTrees fault helper, 7 domain physics rules (CUSUM/EWMA drift, physical bounds, sensor fail-low, deterministic frozen-value, multivariate vapor-pressure consistency, spike reversion), SHAP explainability, 9-state environmental regime classification, and calibrated cluster network corroboration.
6. **Core Architectural Principle**: The Central AI must **never blindly trust** edge anomaly verdicts; it must independently featurize and score every observation while preserving raw sensor readings, comparing edge vs. central inference, and calculating edge agreement metrics.

---

## 2. Current Architecture vs. Target Architecture

```
CURRENT ARCHITECTURE (Single-Tier / Server-Driven)
┌────────────────────────────────────────────────────────┐
│ Open-Meteo API / Labeled CSV Replay Files               │
└──────────────────────────┬─────────────────────────────┘
                           │ (Periodic HTTP Poll / Replay Loop)
                           ▼
┌────────────────────────────────────────────────────────┐
│ FastAPI Backend (main.py / simulator.py / state.py)    │
│  - Ingests raw scalar dictionary                       │
│  - Scores via detect.py (Isolation Forest + 7 Rules)   │
│  - Manages SensorHealthTracker (circuit breaker)       │
│  - Writes to TimescaleDB + Local CSVs                  │
│  - Serves REST + WebSockets to React Dashboard         │
└────────────────────────────────────────────────────────┘

TARGET ARCHITECTURE (Two-Level Hybrid Edge + Central AI)
┌────────────────────────────────────────────────────────────────────────┐
│ LEVEL 1: EDGE / ESP32 + DHT22/BME280 Sensors                           │
│  - Hardware sample acquisition (T, P, RH)                              │
│  - Local lightweight validation & edge rules (bounds, rails, delta)    │
│  - Produces EdgeVerdict (status, flag, score, method, metadata)        │
│  - Transmits Authoritative Observation Packet (Raw Readings + Edge Inf)│
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ HTTPS POST /api/ingest / MQTT / WS
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ LEVEL 2: CENTRAL AI / BACKEND                                          │
│  - Preserves 100% original raw sensor readings                         │
│  - Independent validation & 22-feature temporal/physics engineering    │
│  - Central anomaly detection (Isolation Forest + CUSUM + Multivar)     │
│  - Edge vs. Central verdict comparison (Agreement / Divergence matrix) │
│  - Sensor health tracking, SHAP explainability, suggested values       │
│  - Persistence into TimescaleDB hypertable + CSV fallback mirror       │
│  - Real-time WebSocket live push + REST serving to Frontend Dashboard  │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 3. End-to-End Data Flow Trace & Component Audit

| Pipeline Stage | Actual File | Function / Class | Current Input | Current Output | Current Location | Reusability | Required Modification | New Component Needed? |
|---|---|---|---|---|---|---|---|---|
| **1. Sensor / Data Source** | `model/simulator.py` & `data_fetch.py` | `_fetch_live_reading()`, `start_replay()` | Open-Meteo REST API or `data/AWS-*.csv` | `{temperature_c, pressure_hpa, humidity_pct}` | Central (Backend) | Reusable as demo fallback | Needs toggle between ESP32 real ingestion and Simulator replay | **NEW**: Real Ingestion Receiver Endpoint & ESP32 Firmware |
| **2. Observation Contract Parsing** | `main.py` & `model/state.py` | `ingest_reading()` | `dict` of 3 floats + timestamp | Verdict dict + persisted row | Central | Needs update | Must accept Authoritative Observation Packet (`event_id`, `station_id`, `device_id`, `edge_inference`, `device_metadata`) | **NEW**: Pydantic schema validation for Observation Contract |
| **3. Edge Rule Validation** | `model/edge_rules.py` | `check_reading_edge()` | `temp_c`, `pressure_hpa`, `humidity_pct` | `EdgeVerdict` | Isolated file (Conceptual Edge) | 100% Logic Reusable | Format output to match contract; port logic to C++ for ESP32 | **NEW**: `firmware/src/edge_rules.h / .cpp` |
| **4. Feature Engineering** | `model/features.py` | `build_features_for_latest()`, `add_temporal_features()` | Station history DataFrame | 22-feature `pd.Series` + rule columns | Central | 100% Reusable | None (stays exclusively central) | No |
| **5. Central ML Inference** | `model/detect.py` | `_model_score_to_pct()` | 22-feature vector + `isolation_forest.pkl` | `(model_score_pct, model_status)` | Central | 100% Reusable | None | No |
| **6. Central Rule Fusion** | `model/detect.py` | `_rule_checks()`, `_fuse_and_score()` | Feature row + history buffer | `(fused_score, is_anomaly, fault_type, rule_conf)` | Central | 100% Reusable | Add comparison step with `edge_inference.anomaly_flag` | No |
| **7. Network Corroboration** | `model/detect.py` | `_corroborate_network()` | Neighbor station buffers + target reading | Network state (`LOCALIZED`/`REGIONAL`/`INSUFFICIENT`) | Central | 100% Reusable | None | No |
| **8. SHAP & Explainability** | `model/explain.py` | `ExplainerCache.explain()` | Feature row | `{"method", "features": [{"name", "impact"}]}` | Central | 100% Reusable | None | No |
| **9. Value Imputation** | `model/detect.py` | `score_reading()` | History DF + neighbor buffers | `suggested_values` | Central | 100% Reusable | None | No |
| **10. Sensor Health / Breakers** | `model/detect.py` | `SensorHealthTracker` | Verdict dict | Per-parameter status (`HEALTHY`/`WARNING`/`OFFLINE`) | Central | 100% Reusable | Optionally track edge communication reliability/dropout | No |
| **11. State & History Management** | `model/state.py` | `StateManager.ingest_reading()` | Station ID, raw reading, timestamp | Enriched verdict | Central | Reusable | Update to store edge inference metadata in history | No |
| **12. Persistence Layer** | `history_store.py` | `HistoryStore.append()` | Reading, verdict, metadata | DB Insert + CSV append | Central | Reusable | Add schema columns for edge metadata and device telemetry | Schema migration for TimescaleDB |
| **13. API & WebSockets** | `main.py` | Route handlers & `ConnectionManager` | HTTP requests / WS connections | JSON responses / WS broadcasts | Central | Reusable | Add `POST /api/ingest/observation`, maintain existing routes | **NEW**: Ingestion route |
| **14. Frontend API Client** | `FRONTEND/src/services/` | `apiClient.ts`, `validators.ts` | Backend REST responses | Validated domain models | Frontend | Reusable | Add support for edge comparison metadata display | No breaking changes |
| **15. Frontend Visualization** | `FRONTEND/src/pages/` | `DashboardPage.tsx`, `TelemetryMonitor.tsx` | Validated TypeScript models | UI Components / Charts | Frontend | Reusable | Add edge badge, agreement indicator, device battery/signal display | No |

---

## 4. Special Attention: Edge Rules (`model/edge_rules.py`)

### Deep Code Inspection Results
1. **Existing Rules**:
   - **`dropout`**: Detects missing (`None` or `NaN` via `x != x`) values for temperature, pressure, or humidity.
   - **`sensor_fail_low`**: Detects transducer rail-to-floor failures (`temp <= -8.0°C`, `pressure <= 150.0 hPa`, `humidity <= 3.0%`).
   - **`physical_bounds`**: Detects impossible atmospheric readings (`temp < -50.0` or `> 60.0°C`, `pressure < 870.0` or `> 1085.0 hPa`, `humidity < 0.0` or `> 100.0%`).
2. **Inputs Required**: Three primitive scalar floats (`temp_c`, `pressure_hpa`, `humidity_pct`).
3. **Dependencies**: **Zero third-party dependencies**. Written in pure Python standard library. No `numpy`, `pandas`, or `sklearn`.
4. **Feasibility on ESP32**: **100% conceptually and practically executable on ESP32**. The operations are simple floating-point comparisons and boolean checks requiring less than $0.5\,\mu\text{s}$ CPU time on an Xtensa LX6/LX7 core at 240 MHz.
5. **Directly Reusable Logic**: Threshold constants, comparison logic, and fault type taxonomies (`dropout`, `sensor_fail_low`, `physical_bounds`).
6. **Required Separation for ESP32**:
   - Create a C/C++ header and implementation (`edge_rules.h` and `edge_rules.cpp`) for embedded firmware.
   - Maintain a synchronized Python version in `model/edge_rules.py` for simulated edge testing and validation.
7. **Current Edge Output**: Returns `EdgeVerdict(flag: bool, fault_type: str, affected: list, reason: str)`.
8. **Missing Information vs. Observation Contract**:
   - `status`: Execution status (e.g., `OK`, `ANOMALY_DETECTED`, `SENSOR_DEGRADED`).
   - `score`: Quantitative rule severity / edge confidence score ($0.0 - 100.0$).
   - `score_type`: Label specifying metric type (e.g., `"RULE_CONFIDENCE"`, `"HEURISTIC"`).
   - `model_version`: Edge firmware / rule version string (e.g., `"edge-v1.0.0"`).
   - `inference_method`: Method designation (`"deterministic_rules"`, `"tflite_micro"`).
   - Packet-level headers: `event_id`, `device_id`, `sequence_number`, `device_metadata` (`battery_voltage`, `signal_strength`, `firmware_version`).

---

## 5. Special Attention: Central AI Pipeline (`model/detect.py`)

The central pipeline is designed for deep temporal, multivariate, and network corroboration. It **must remain exclusively central** due to memory, CPU, and cross-station data requirements.

### Components Remaining Exclusively Central:
1. **Machine Learning Model**: `scikit-learn` `IsolationForest` (100 estimators, 22 continuous features) and `ExtraTreesClassifier` (`fault_helper.py`). Cannot and should not run on ESP32.
2. **22-Feature Engine**:
   - 48-hour rolling baselines (`ROLLING_WINDOW_HOURS = 48h`).
   - 1-hour and 3-hour rates-of-change (`roc_1h`, `roc_3h`).
   - 30-day volatility z-scores (`temp_volatility_z`, `pressure_volatility_z`).
   - Thermodynamic cross-parameter consistency: Dewpoint depression, Vapor Pressure Deficit (VPD), and Clausius-Clapeyron consistency deviation (`vapor_pressure_consistency_dev`).
   - Trailing diurnal encodings (`hour_sin`, `hour_cos`, `doy_sin`, `doy_cos`).
3. **Sequential & Statistical Rule Engine**:
   - **CUSUM & EWMA Drift**: Diurnally-hardened cumulative sum accumulator comparing against station-specific seasonal rate-of-change baselines (`model/seasonal_baseline.py`).
   - **Deterministic Frozen Value**: 3-step floor-match streak analysis.
   - **Multivariate Inconsistency**: Joint temperature-humidity divergence with flat barometric pressure.
   - **Spike Reversion Confirmation**: 3-step causal reversion check ($t-1$ jump confirmed at $t$).
4. **Network Corroboration**: Inter-station cluster residual analysis across 4 peer stations (5 geographic clusters across India: CHN, DEL, MUM, KOL, BHO, VAR, RAN).
5. **Explainability & Attribution**: TreeSHAP feature attributions and fallback magnitude rankings (`model/explain.py`).
6. **Sensor Health & Circuit Breakers**: 10-hour / 24-hour sliding window error counters and 3-streak clean reading recovery management (`SensorHealthTracker`).
7. **Suggested Value Imputation**: 5-tier fallback cascade (Neighbor interpolation $\rightarrow$ Same-hour-yesterday $\rightarrow$ Forward trend extrapolation $\rightarrow$ 48h rolling mean $\rightarrow$ Prior clean reading).

---

## 6. Existing Backend API Compatibility Matrix

The backend currently exposes 14 endpoints (13 REST + 1 WebSocket). All existing endpoints **must remain 100% backward-compatible** with the React frontend while adding the new edge observation ingestion interface.

| Endpoint | Method | Current Purpose | Current Response Shape | Frontend Consumer | Must Remain Compatible? |
|---|---|---|---|---|---|
| `/ws/live` | `WebSocket` | Real-time telemetry & anomaly streaming | `{"type", "station_id", "raw_reading", "verdict", ...}` | `TelemetryMonitor`, `LiveMetricsCard` | **YES** (P0) |
| `/api/system-status` | `GET` | Returns backend mode & polling cadence | `{"mode": "live"\|"replay", "replay_step_seconds", "live_poll_interval_seconds"}` | Header, `SystemStatusBar` | **YES** (P0) |
| `/api/system-mode` | `POST` | Switches between live and replay mode | `{"mode": "live", "message": "..."}` | Dashboard Mode Toggle | **YES** (P0) |
| `/api/stations` | `GET` | Lists all monitored AWS stations | `[{"station_id", "name", "lat", "lon", "status"}]` | Station Selector, Network Map | **YES** (P0) |
| `/api/network-status` | `GET` | Aggregated network health badge data | `{"overall_status", "active_stations_count", "avg_sensor_health_pct", ...}` | Header Status Badge | **YES** (P0) |
| `/api/current-reading` | `GET` | Latest telemetry & anomaly verdict for station | `{"station_id", "temperature_c", "pressure_hpa", "humidity_pct", "anomaly_score_pct", "risk_level", ...}` | Main Sensor Cards, Quick Stats | **YES** (P0) |
| `/api/trends` | `GET` | Historical trendline + anomaly windows | `{"station_id", "hours", "points": [...], "anomaly_windows": [...]}` | Interactive Time-Series Charts | **YES** (P0) |
| `/api/anomalies/latest`| `GET` | Most recent anomaly incident for station | `{"anomaly_id", "root_cause", "severity", "suggested_values", "decision_basis", ...}` | Active Alert Banner, Incident Card | **YES** (P0) |
| `/api/anomalies/recent`| `GET` | List of recent anomalies (global or station) | `[{"anomaly_id", "station_id", "root_cause", "severity", ...}]` | Recent Incidents Table | **YES** (P0) |
| `/api/explain/{anomaly_id}` | `GET` | SHAP feature impact & spatial context | `{"anomaly_id", "features": [...], "spatial_context": {...}}` | Explainability Modal / Drawer | **YES** (P0) |
| `/api/sensor-health` | `GET` | Per-parameter sensor health status | `{"station_id", "health_pct", "status", "parameters": {...}}` | Sensor Diagnostics Panel | **YES** (P0) |
| `/api/repair-sensor` | `POST` | Initiates gradual or forced sensor recovery | `{"success": true, "status": "HEALTHY"\|"WARNING", ...}` | Sensor Maintenance Action | **YES** (P0) |
| `/api/inject-anomaly` | `POST` | Triggers replay simulation across stations | `{"success": true, "anomaly_id": "...", "message": "..."}` | Demo Inject / Replay Button | **YES** (P0) |
| `/api/maintenance-ticket` | `POST` | Generates maintenance ticket for anomaly | `{"ticket_id", "station_id", "issue", "priority", ...}` | Maintenance Workflow | **YES** (P0) |
| `/api/history.csv` | `GET` | CSV download of retained station history | `text/csv` attachment | Operator Export Button | **YES** (P0) |
| `/api/admin/clear-history` | `POST` | Clears historical data from DB/CSVs | `{"success": true, "target": "all"\|"replay"}` | Admin Maintenance Console | **YES** (P0) |

---

## 7. Frontend Architecture & Sensor Source Assumptions

1. **How Frontend Obtains Data**:
   - **Current Readings**: Polling `GET /api/current-reading?station_id=...` (or via `/ws/live`).
   - **Trends**: `GET /api/trends?station_id=...&hours=24`.
   - **Anomalies & SHAP**: `GET /api/anomalies/latest`, `GET /api/anomalies/recent`, and `GET /api/explain/{anomaly_id}`.
   - **Sensor Health**: `GET /api/sensor-health?station_id=...`.
   - **Replay / Demo**: Dispatches `POST /api/inject-anomaly` which commands the backend to enter replay mode.
2. **Sensor Source Assumption**:
   - The frontend currently treats the backend as a black-box weather oracle.
   - It expects `GET /api/current-reading` to return `{value, normal_min, normal_max}` for temperature, pressure, and humidity.
   - It is agnostic to whether the reading originated from Open-Meteo, historical CSV replay, or an ESP32 hardware packet.
3. **Design Compatibility Requirement**:
   - Ingesting real ESP32 packets on the backend will seamlessly populate `state.py` and `latest`, meaning the existing frontend will render live ESP32 telemetry immediately with **zero breaking changes**.
   - Additional edge telemetry (e.g. `edge_inference`, `battery_voltage`, `signal_strength`) can be added additively to response payloads.

---

## 8. Database & Persistence Status

1. **Active Storage Engine**: Dual-store architecture in `history_store.py`:
   - **Primary Store**: **TimescaleDB hypertable (`sensor_readings`)** on PostgreSQL with native time-series partitioning on `time`.
   - **Mirror / Fallback Store**: Thread-safe, append-only **CSV files** in `data/history/{station_id}_history.csv`.
   - **Station Health Log**: Dual-writes state transitions to `station_health_events` table and `{station_id}_health_events.csv`.
2. **Current Schema Columns**:
   `time/timestamp`, `station_id`, `temperature_c`, `pressure_hpa`, `humidity_pct`, `is_anomaly`, `fault_type`, `severity`, `anomaly_score_pct`, `decision_basis`, `suggested_temperature_c`, `suggested_pressure_hpa`, `suggested_humidity_pct`, `health_status`, `source`, `model_confidence_pct`, `rule_confidence_pct`.
3. **Required Extensions**:
   Need migration to store: `event_id`, `device_id`, `sequence_number`, `edge_anomaly_flag`, `edge_anomaly_type`, `edge_score`, `edge_score_type`, `edge_model_version`, `edge_inference_method`, `firmware_version`, `battery_voltage`, `signal_strength`, `central_vs_edge_agreement`.

---

## 9. ESP32 Implementation Status

- **Existing Firmware**: None.
- **Hardware Communication Layer**: None.
- **Drivers**: No C/C++ sensor drivers for DHT22, BME280, or BMP280 exist in the repository.
- **Network Stack**: No embedded WiFi, HTTP client, or MQTT publishing code exists in the repository.
- **Conclusion**: A complete, modular ESP32 firmware package (or PlatformIO/Arduino project) needs to be created in a dedicated directory (e.g., `firmware/`).

---

## 10. Comprehensive Gap Analysis

```
┌───────────────────────────────────────────────┬───────────────────────────────────────────────┐
│ Current System                                │ Target Hybrid Edge + Central Architecture     │
├───────────────────────────────────────────────┼───────────────────────────────────────────────┤
│ Open-Meteo / CSV replay only                 │ Physical ESP32 + Virtual Edge Simulator       │
│ Unstructured 3-float dictionary               │ Authoritative Observation Packet Contract     │
│ Edge rules isolated in Python file only       │ C++ Edge Engine on ESP32 + Synced Python Core │
│ No edge metadata in ingestion                 │ Full edge inference & device health metadata  │
│ Backend performs 100% of detection            │ Two-level detection: Edge Level 1 + Central L2│
│ No edge vs central comparison                 │ Independent central validation + agreement matrix│
│ DB schema stores central verdicts only        │ DB schema persists edge + central verdicts    │
└───────────────────────────────────────────────┴───────────────────────────────────────────────┘
```

---

## 11. Reusable, Modified, and New Components

### A. Reusable Components (100% Kept As-Is)
- `model/features.py`: Complete 22-feature temporal and thermodynamic engine.
- `model/train.py`: Isolation Forest model training pipeline.
- `model/explain.py`: SHAP TreeExplainer and magnitude ranking logic.
- `model/seasonal_baseline.py`: Diurnal rate-of-change baseline cache.
- `model/fault_helper.py`: ExtraTrees classifier for sparse network replays.
- `config.py`: Thresholds, fusion weights, cluster definitions.
- `FRONTEND/`: All React UI components, router, context, and charting pages.

### B. Components Requiring Modification
1. `model/edge_rules.py`: Update `EdgeVerdict` structure to match contract fields (`status`, `score`, `score_type`, `model_version`, `inference_method`).
2. `model/state.py`: Update `ingest_reading()` to parse observation packets, record edge inference, and calculate edge-vs-central agreement.
3. `history_store.py`: Update schema and SQL queries to persist edge inference metadata and device telemetry.
4. `main.py`: Add `POST /api/ingest/observation` endpoint while preserving all existing routes.
5. `model/simulator.py`: Add capability to generate edge-packet formatted streams during replay/demo.

### C. New Components Required
1. `model/contracts.py`: Pydantic models for the Authoritative Observation Packet.
2. `model/comparator.py`: Logic to compare Edge Level 1 vs. Central Level 2 verdicts (Agreement, True Positive, False Alarm at Edge, Edge Missed).
3. `firmware/`: Complete ESP32 firmware implementation (PlatformIO / Arduino compatible C++ source for sensor reading, edge rule evaluation, JSON packet assembly, and HTTP/MQTT transmission).
4. `scripts/virtual_edge_device.py`: Python CLI tool to simulate physical ESP32 devices transmitting real packets over HTTP for testing without physical hardware.

---

## 12. Authoritative Observation Contract Design

The JSON schema to be supported by the new ingestion interface:

```json
{
  "event_id": "evt-20260923-0001",
  "station_id": "AWS-CHN-024",
  "device_id": "esp32-node-chennai-01",
  "observed_at": "2026-09-23T13:00:00Z",
  "sequence_number": 1420,
  "readings": {
    "temperature_c": 32.4,
    "pressure_hpa": 1008.2,
    "humidity_pct": 68.5
  },
  "edge_inference": {
    "status": "OK",
    "anomaly_flag": false,
    "anomaly_type": null,
    "score": 0.0,
    "score_type": "RULE_CONFIDENCE",
    "model_version": "edge-rules-v1.0.0",
    "inference_method": "deterministic_rules"
  },
  "device_metadata": {
    "firmware_version": "1.0.4",
    "battery_voltage": 3.92,
    "signal_strength": -64
  }
}
```

---

## 13. Exact Recommended Implementation Order (Post-Audit)

### Phase 1: Observation Contract & Edge Logic Standardization
1. Create `model/contracts.py` defining Pydantic models for `ObservationPacket`, `Readings`, `EdgeInference`, and `DeviceMetadata`.
2. Update `model/edge_rules.py` to produce standardized `EdgeInference` output matching the contract.
3. Unit test `model/edge_rules.py` against all fault types.

### Phase 2: Backend Ingestion & Edge-Central Comparison
4. Create `model/comparator.py` to compute edge-vs-central comparison metrics (`AGREEMENT`, `EDGE_ONLY_FLAG`, `CENTRAL_ONLY_FLAG`, `MUTUAL_FLAG`).
5. Update `model/state.py` to accept observation packets, run independent central scoring, and attach comparison metadata.
6. Create `POST /api/ingest/observation` in `main.py` to ingest real packets asynchronously.
7. Update `history_store.py` to persist edge metadata in TimescaleDB and CSV mirrors.

### Phase 3: Virtual Edge Device & Simulation Harness
8. Build `scripts/virtual_edge_device.py` to emulate an ESP32 sending real JSON packets over HTTP.
9. Update `model/simulator.py` to emit contract-compliant observation packets during replay mode.

### Phase 4: ESP32 Firmware Development
10. Create `firmware/` with PlatformIO / Arduino C++ code:
    - Sensor drivers (DHT22 / BME280 / BMP280).
    - Ported `edge_rules.cpp` deterministic checks.
    - JSON serialization and HTTPS/HTTP POST client.
    - WiFi auto-reconnect and offline buffering.

### Phase 5: Verification & End-to-End Validation
11. Run end-to-end integration tests (`tests/test_hybrid_pipeline.py`).
12. Verify frontend dashboard displays live ESP32 telemetry with zero regressions.

---

## 14. Files Requiring Future Modification vs. Creation

### Files to Modify:
- `model/edge_rules.py`
- `model/state.py`
- `model/simulator.py`
- `history_store.py`
- `main.py`
- `FRONTEND/src/types/index.ts` (additive fields only)
- `FRONTEND/src/services/validators.ts` (additive fields only)

### Files to Create:
- `docs/HYBRID_ARCHITECTURE_AUDIT.md` *(Created in this step)*
- `model/contracts.py`
- `model/comparator.py`
- `scripts/virtual_edge_device.py`
- `firmware/platformio.ini`
- `firmware/src/main.cpp`
- `firmware/src/edge_rules.h`
- `firmware/src/edge_rules.cpp`
- `firmware/src/config.h`
- `tests/test_hybrid_pipeline.py`

---

## 15. Risk Assessment & Engineering Mitigations

1. **Risk: Backend Blind Trust**:
   - *Mitigation*: The backend featurizes raw readings and runs its full Isolation Forest + CUSUM + Rule pipeline completely independently of `edge_inference.anomaly_flag`.
2. **Risk: Edge Rule vs. Central Rule Threshold Drift**:
   - *Mitigation*: Single source of truth configuration principles; edge C++ constants must be generated or synchronized with `config.py` constants.
3. **Risk: Network Downtime at Edge**:
   - *Mitigation*: ESP32 firmware includes local non-volatile or circular RAM buffering to store observations during WiFi outages and transmit with original timestamps upon reconnection.
4. **Risk: Frontend Compatibility**:
   - *Mitigation*: Ingestion endpoint `/api/ingest/observation` is purely additive; all existing `/api/current-reading`, `/api/trends`, and `/ws/live` contracts remain unaltered.
