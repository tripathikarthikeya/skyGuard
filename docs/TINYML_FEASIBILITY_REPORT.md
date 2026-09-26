# TINYML FEASIBILITY REPORT

**IMPLEMENTATION STATUS:**
"VALIDATION ONLY — NO PRODUCTION IMPLEMENTATION PERFORMED"

## 1. Executive Summary
This report investigates the feasibility of two extensions to the SkyGuard AI project:
1. Simulating physical sensors by streaming anomaly-injected CSV datasets directly into an ESP32 edge device.
2. Augmenting or replacing the current edge deterministic rules with a TinyML-based anomaly detection model that can run natively on the ESP32.

Our audit of the codebase confirms that both proposals are technically feasible with specific architectural constraints. The ESP32 can act as a virtual sensor node by receiving data over Serial from a host PC, and a distilled or compact ensemble model can complement the existing deterministic rules without replacing the central Isolation Forest pipeline.

## 2. Current Architecture
SkyGuard employs a hybrid Level 1 / Level 2 edge-cloud architecture:
- **Level 1 (Edge):** ESP32 currently implements deterministic rules (e.g., physical bounds, fail-low detection, dropout) on scalar temperature, pressure, and humidity readings. It constructs an `ObservationPacket`.
- **Level 2 (Central):** Anomaly ingestion pipeline that performs complex multivariate, temporal, and thermodynamic feature engineering over historical buffers, evaluated by an Isolation Forest model to produce an anomaly score and fault classification.

## 3. Repository Findings
- **Anomaly Datasets:** Anomaly-injected datasets reside in the `data/` directory (e.g., `AWS-BHO-030_labeled.csv`).
- **CSV Schema:** 
  `timestamp,temperature_c,pressure_hpa,humidity_pct,station_id,station_name,cluster_id,role,is_anomaly,fault_type`
- **Data Completeness:** The CSV contains exactly the three required measurements (temperature, pressure, humidity). Ground-truth labels (`is_anomaly`, `fault_type`) are present in `*_labeled.csv` files but are stripped before training.
- **Contracts:** The `ObservationPacket` schema in `model/contracts.py` supports any raw readings and includes a generic `EdgeInference` payload, which can natively accommodate a new TinyML inference method without schema changes.

## 4. CSV → ESP32 Feasibility
It is entirely feasible to stream the anomaly-injected CSV files into the ESP32 to act as virtual sensor readings. 

**Transport Evaluation:**
- **A. SD Card on ESP32:** High hardware complexity. Requires physically provisioning SD cards with CSVs. No real-time control.
- **B. Serial Stream from PC (Recommended):** The host PC reads the CSV row-by-row and sends JSON or raw bytes over Serial to the ESP32. The ESP32 parses the line as if it read a sensor, runs inference, and submits the payload over Wi-Fi. Low latency, highly reproducible, requires no extra hardware.
- **C. HTTP / Wi-Fi Fetch:** ESP32 polls a PC web server. Prone to networking overhead and jitter compared to Serial.
- **D. PC completely bypasses ESP32:** Invalidates the purpose of testing actual ESP32 edge inference.

**Streaming Mechanics:**
- The PC must **strip** `is_anomaly` and `fault_type` before sending to the ESP32 to prevent data leakage. The ESP32 must only see `temperature_c`, `pressure_hpa`, and `humidity_pct`.
- **Timestamps:** The PC can send historical timestamps to the ESP32. The ESP32 should package them exactly as provided. The Central AI handles historical replays gracefully since it already relies on `timestamp` for ordering rather than wall-clock ingestion time.
- **Sequence Numbers / Event ID:** The ESP32 can continue incrementing its own sequence number and generating event IDs.

## 5. Current Central AI Analysis
An audit of `model/features.py` and `model/detect.py` reveals the following:

- **ML Algorithm:** Isolation Forest (`sklearn.ensemble.IsolationForest`).
- **Features Engineered:**
  - Raw values.
  - Temporal features: Rate of change (ROC) and deviations from a 48-hour rolling baseline.
  - Cross-parameter features: Thermodynamic consistency checks.
  - Cyclical time: Hour of day, day of year encodings.
- **Dependencies:** Heavily relies on `pandas` (for rolling windows, shifts) and `scikit-learn`.

| Component | Current implementation | ESP32 feasible? | Why |
|-----------|------------------------|------------------|-----|
| Raw Values | Direct reading | YES | Trivial to access at edge. |
| Temporal / Baselines | Pandas 48h rolling mean/std | NO / DIFFICULT | Requires storing hundreds of readings in RAM, computationally heavy for microcontrollers. |
| Cross-parameter | Clausius-Clapeyron consistency | PARTIAL | Can be computed if math libraries fit on flash, but no historical smoothing possible. |
| Time encoding | Datetime extraction | YES | Simple trigonometry. |
| Isolation Forest | `sklearn` ensemble (100 trees) | NO | Too large for flash/RAM, lacks native C++ translation without heavy pruning. |

The current central model is far too complex and memory-intensive to deploy on an ESP32 natively. The rolling historical windows alone violate memory constraints.

## 6. TinyML Feasibility
Directly porting the Isolation Forest is not viable. Candidate approaches for the edge:
- **A. Direct tree ensemble port:** Truncated Random Forest (e.g., 5-10 trees depth 3). Flash: ~50KB. RAM: ~5KB. Inference: <1ms. High feasibility.
- **B. Small Dense Neural Network:** 3 layers, ~32 units. INT8 Quantized. Flash: ~10KB. RAM: ~2KB. Inference: <5ms. Excellent quantization support (TFLite Micro). High feasibility.
- **C. Rules + TinyML Hybrid:** Combine existing deterministic bounds with a TinyML network. Highest feasibility and explainability.

**Resource Estimates for Edge NN (INT8):**
- Model Size: ~5-15 KB
- RAM Requirement: ~5-10 KB
- Inference Complexity: O(1) matrix multiplications per reading.

## 7. Candidate Architectures
**Architecture A (Current): Rules → Central AI**
- **Flow:** ESP32 runs deterministic rules -> sends packet -> Central AI.
- **Pros:** Zero new edge complexity.

**Architecture B: TinyML Only → Central AI**
- **Flow:** ESP32 extracts features -> NN inference -> sends packet -> Central AI.
- **Cons:** Loss of explicit, explainable deterministic bounds (e.g., "temperature is exactly at physical limit").

**Architecture C (Recommended): Rules + TinyML → Central AI**
- **Flow:** ESP32 runs deterministic bounds. If clean, ESP32 runs quantized TinyML NN on raw readings -> sends packet with combined `EdgeInference` -> Central AI verifies.
- **Justification:** Deterministic rules perfectly catch hardware failures (rail collapse, dropouts) with 0% false positives. TinyML catches complex nonlinear anomalies. The `ObservationPacket` supports multi-method inference scoring.

## 8. Teacher/Student Distillation Analysis
The existing Central AI can act as a Teacher for a TinyML Student:
- **Target:** The student should predict the `anomaly_score_pct` (regression) or a binary `is_anomaly` (classification) derived from the Isolation Forest and rules fusion.
- **Methodology:** Run the full `all_stations.csv` (clean + injected) through the central `detect.py` pipeline. Record the final fused anomaly score for every row. Train a small NN on `(T, P, RH) -> Score`. Quantize to INT8.
- **Viability:** Highly viable. Synthetic injected anomalies provide ample edge cases. The edge model will learn the biases of the central model, which is acceptable since the goal is preliminary edge-filtering, not authoritative replacement.

## 9. Recommended Architecture
We recommend **Architecture C: Rules + TinyML → Central AI**.
- **Data Flow:** PC streams CSV over Serial → ESP32 parses → ESP32 checks rules. If Rules pass, ESP32 runs TinyML model → ESP32 sends `ObservationPacket` with `EdgeInference(inference_method="rules+embedded_ml")`.
- **Backend:** Ingestion API accepts packet. Central AI recalculates full temporal features and Isolation Forest score. Generates `EdgeCentralComparison`.
- **Latency/Memory:** TinyML inference takes <5ms and <15KB RAM, easily fitting on ESP32 alongside the Wi-Fi stack.

## 10. Sensor-less Demonstration Architecture
**Already Exists:**
- Anomaly Injected CSVs (`data/`)
- ESP32 Firmware base (`edge/esp32/`)
- Ingestion API & Central AI (`model/ingestion.py`, `model/detect.py`)
- ObservationPacket Contract (`model/contracts.py`)

**Needs to be Implemented:**
- **PC Serial Streamer Script:** A Python script to read `data/AWS-BHO-030_labeled.csv`, strip labels, format as JSON, and send to the ESP32 via Serial.
- **ESP32 Serial Listener:** Update `edge/esp32/src/` to read Serial payload instead of physical I2C sensors.
- **TinyML Model Training Script:** A new script to train a TFLite Micro model using central labels as targets.
- **ESP32 TFLite Integration:** Include TensorFlow Lite Micro in `platformio.ini` and add inference C++ code.

## 11. Required Future Implementation Work
1. Build the PC-to-ESP32 Serial streamer.
2. Modify ESP32 to support virtual sensor injection mode.
3. Train an INT8 TinyML model via distillation from Central AI scores.
4. Integrate TFLite Micro on ESP32 firmware.

## 12. Risks and limitations
- **Temporal Blindness:** The edge model only sees instantaneous `(T, P, RH)`. It cannot detect slow CUSUM drifts that the Central AI catches, meaning Edge-Central disagreement on drift faults is expected by design.
- **Memory Leaks:** Integrating TFLite Micro alongside Wi-Fi and HTTPS stacks on ESP32 can lead to heap fragmentation.

## 13. Benchmark methodology
To validate the future TinyML model:
1. **Accuracy:** Confusion matrix of Edge Binary Flag vs Central Binary Flag.
2. **False Positives:** Evaluate rate on known-clean segments of USCRN validation slices.
3. **Latency/Metrics:** Profile exact microsecond execution time and heap utilization before/after TFLite `Invoke()`.
4. **Disagreement Rate:** Use the existing `EdgeCentralComparison` module to track `anomaly_decision_agreement`.

## 14. Final feasibility conclusion
It is highly feasible to implement a CSV-to-ESP32 virtual sensor pipeline over Serial, and highly feasible to augment the edge rules with a distilled TinyML model. The architecture natively supports these enhancements without breaking existing data contracts.
