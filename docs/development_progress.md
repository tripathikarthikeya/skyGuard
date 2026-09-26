# SkyGuard AI — Development Progress

## Completed Migration Steps

### Step 1 — Architectural Review & Contract Design
- Evaluated existing standalone anomaly detection architecture.
- Designed hybrid edge + central AI specifications and contracts.

### Step 2 — Canonical Observation Contract
- Defined standard `ObservationPacket` and `SensorReading` schema.
- Built serialization and validation models.

### Step 3 — Edge Inference Implementation
- Created Level-1 deterministic and statistical rule evaluators for edge nodes.
- Implemented edge score calculation and feature validation.

### Step 4 — Observation Ingestion API
- Implemented `POST /api/ingest/observation` endpoint in FastAPI.
- Added packet validation, deduplication, and sequence tracking.

### Step 5 — Central Inference & Edge Comparison Engine
- Built Central AI model inference integration and ensemble scorer.
- Implemented `EdgeCentralComparison` arbitration and consensus policy.

### Step 6 — Hybrid Persistence
- Created dual-write and hybrid observation history persistence.
- Stored raw readings, edge inference, central inference, and comparison records separately.

### Step 7 — Virtual Edge Simulator Migration
- Migrated synthetic sensor simulator to emit canonical `ObservationPacket` structures via `POST /api/ingest/observation`.
- Retained simulation controls and replay features.

### Step 8 — ESP32 Level-1 Edge Node Firmware
- Implemented C++/Arduino firmware for ESP32 with BME280/DHT22 sensors.
- Embedded local Level-1 rules, JSON packet construction, and HTTP POST ingestion.

### Step 9 — Hybrid Integration Validation
- Validated end-to-end telemetry flow from edge nodes to central AI and persistence.
- Verified arbitration logic, edge dropout handling, and duplicate packet protection across 156 test cases.

### Step 10 — Frontend Data Integration Audit and Fixes
- Conducted full frontend data-flow audit across API services, validators, hooks, and React components.
- Fixed domain types and validators to support nullable sensor readings (`temperature_c = null`, `pressure_hpa = null`, `humidity_pct = null`, `anomaly_score_pct = null`).
- Updated `LatestAnomalyCard.tsx` and `LatestAnomalyBanner.tsx` to bind authoritative `affected_parameters`, explicit `station_id`, and safely formatted score fallback (`—`).
- Hardened all charts (`TrendChart.tsx`, `AnalyticsTrendChart.tsx`) and tables against `null` coordinates and metric values.
- Verified zero regressions on backend test suite (156 tests passing) and validated clean frontend production build (`npm run build`).
- Implemented Step 10 integration and contract QA test in `tests/test_step10_frontend_contract.py`.

### Step 11 — ESP32 Virtual Sensor Mode
- Implemented virtual sensor architecture to parse telemetry over USB Serial.
- Created `scripts/esp32_csv_streamer.py` for streaming historical CSV rows while actively isolating ground-truth labels.
- Modified ESP32 firmware (`edge/esp32/src/main.cpp`) to receive JSON payload, execute local edge rules, generate event IDs, and build identical ObservationPackets without using physical I2C sensors.
