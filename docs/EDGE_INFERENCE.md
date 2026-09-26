# SkyGuard AI — Level 1 Edge Inference Specification

> **Document Status**: Authoritative Specification (Step 3 Migration)  
> **Module Implementation**: [`model/edge_rules.py`](file:///d:/sky/LULLABY-SKYGUARD-main/model/edge_rules.py)  
> **Data Contract**: [`model/contracts.py`](file:///d:/sky/LULLABY-SKYGUARD-main/model/contracts.py) (`EdgeInference`)  
> **Test Suite**: [`tests/test_edge_rules.py`](file:///d:/sky/LULLABY-SKYGUARD-main/tests/test_edge_rules.py)  

---

## 1. Purpose

This document specifies the **Level 1 Edge Inference Engine** running on edge microcontrollers (ESP32) or simulated edge nodes within SkyGuard AI. It details the lightweight deterministic rules, execution precedence, threshold constants, status semantics, and output payload format conforming to the canonical `EdgeInference` schema.

---

## 2. Current Edge Architecture

Level 1 Edge Inference sits directly adjacent to the physical hardware sensors (e.g., DHT22, BME280) and executes immediately upon sensor sampling:

```
┌────────────────────────────────────────────────────────────┐
│ PHYSICAL SENSORS / TRANSDUCERS (T, P, RH)                  │
└─────────────────────────────┬──────────────────────────────┘
                              │ Raw Float Readings (or None)
                              ▼
┌────────────────────────────────────────────────────────────┐
│ LEVEL 1: EDGE INFERENCE ENGINE (model/edge_rules.py)       │
│  - Zero ML / No heavy dependencies / pure float logic      │
│  - Evaluates Rule 1 (Dropout)                              │
│  - Evaluates Rule 2 (Sensor Fail-Low)                      │
│  - Evaluates Rule 3 (Physical Bounds)                      │
│  - Produces EdgeInference (status, flag, anomaly_type)     │
└─────────────────────────────┬──────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────┐
│ CANONICAL OBSERVATION PACKET ENCAPSULATION                 │
│  - Bundles original raw readings + EdgeInference metadata  │
│  - Transmitted over HTTPS/MQTT to Level 2 Central AI       │
└────────────────────────────────────────────────────────────┘
```

---

## 3. Existing Rules

The Level 1 Edge Engine implements three deterministic rules:
1. **`dropout`**: Detects communication failure or missing telemetry on any channel.
2. **`sensor_fail_low`**: Detects transducer ground/rail collapse to hardware noise floor.
3. **`physical_bounds`**: Detects impossible atmospheric readings exceeding surface extremes.

---

## 4. Exact Rule Semantics

### Rule 1: Dropout Check
- **Trigger Condition**: Any sensor channel (`temperature_c`, `pressure_hpa`, `humidity_pct`) is `None` or `NaN` ($x \ne x$).
- **Implication**: Physical disconnection, I2C/SPI communication bus error, or hardware failure.
- **Output**: `anomaly_flag = True`, `anomaly_type = "dropout"`, `status = "anomaly_detected"`.

### Rule 2: Sensor Fail-Low Check
- **Trigger Condition**: Any valid numeric reading falls at or below the hardware rail floor:
  - $\text{Temperature} \le -8.0^\circ\text{C}$
  - $\text{Pressure} \le 150.0\,\text{hPa}$
  - $\text{Humidity} \le 3.0\%$
- **Implication**: Transducer bridge short-circuit or electrical rail collapse to zero/ground.
- **Output**: `anomaly_flag = True`, `anomaly_type = "sensor_fail_low"`, `status = "anomaly_detected"`.

### Rule 3: Physical Bounds Check
- **Trigger Condition**: Any valid numeric reading exceeds historical planetary surface boundaries:
  - $\text{Temperature} < -50.0^\circ\text{C}$ OR $> 60.0^\circ\text{C}$
  - $\text{Pressure} < 870.0\,\text{hPa}$ OR $> 1085.0\,\text{hPa}$
  - $\text{Humidity} < 0.0\%$ OR $> 100.0\%$
- **Implication**: Gross transducer malfunction, uncalibrated scaling, or corrupt ADC registers.
- **Output**: `anomaly_flag = True`, `anomaly_type = "physical_bounds"`, `status = "anomaly_detected"`.

---

## 5. Rule Thresholds & Numerical Configuration

The constants defined in `model/edge_rules.py` are kept in numerical synchronization with the central `config.py` constants:

| Rule Category | Parameter | Threshold / Range | Unit | Justification |
|---|---|---|---|---|
| **Fail-Low Floor** | `temperature_c` | $\le -8.0$ | °C | Below realistic surface record for monitored Indian stations |
| **Fail-Low Floor** | `pressure_hpa` | $\le 150.0$ | hPa | Rail floor value for a collapsed pressure transducer |
| **Fail-Low Floor** | `humidity_pct` | $\le 3.0$ | % | Electrical floor value, unreachable under surface ambient conditions |
| **Physical Bounds** | `temperature_c` | $[-50.0, 60.0]$ | °C | Encompasses global surface meteorological records |
| **Physical Bounds** | `pressure_hpa` | $[870.0, 1085.0]$ | hPa | Between strongest hurricane eye and highest continental anticyclone |
| **Physical Bounds** | `humidity_pct` | $[0.0, 100.0]$ | % | Fundamental thermodynamic definition of relative humidity |

---

## 6. `EdgeInference` Output Fields

When calling `run_edge_inference(temp_c, pressure_hpa, humidity_pct)`:

| Field Name | Type | Value on Normal Reading | Value on Anomaly Detected | Description |
|---|---|---|---|---|
| `status` | `string` | `"ok"` | `"anomaly_detected"` | Execution status of the edge rule engine |
| `anomaly_flag` | `boolean` | `false` | `true` | Boolean flag indicating if an edge rule triggered |
| `anomaly_type` | `string` or `null` | `null` | `"dropout"` \| `"sensor_fail_low"` \| `"physical_bounds"` | Categorical identifier for the triggered rule |
| `score` | `float` or `null` | `null` | `null` | Reserved for future quantitative edge models |
| `score_type` | `string` or `null` | `null` | `null` | Reserved for future score descriptor |
| `model_version` | `string` | `"edge_rules_v1.0.0"` | `"edge_rules_v1.0.0"` | Version identifier of the edge rule engine |
| `inference_method` | `string` | `"rules"` | `"rules"` | Methodology descriptor for Level 1 inference |

---

## 7. Status Semantics

The `status` field communicates the operational state of Level 1 edge evaluation:
- **`"ok"`**: Sensor readings were successfully acquired and passed all deterministic edge validation rules without incident.
- **`"anomaly_detected"`**: Sensor readings were successfully acquired and triggered one of the edge deterministic anomaly rules.
- **`"error"`**: Reserved for unexpected edge hardware or evaluation faults.

---

## 8. Anomaly-Type Semantics

When an anomaly is flagged, `anomaly_type` carries a stable machine-readable identifier:
- `"dropout"`
- `"sensor_fail_low"`
- `"physical_bounds"`
- `null` (strictly required when `anomaly_flag` is `false`)

---

## 9. Score & Score-Type Semantics

The current edge rules are **purely deterministic boolean rules**.
- The Level 1 engine **does not produce a fake probability** or arbitrary floating-point number.
- `score` is strictly set to `null` (`None`) and `score_type` is set to `null` (`None`).
- If an embedded machine learning model (e.g. TensorFlow Lite for Microcontrollers) is introduced in a future version, `score` will hold the calibrated confidence and `score_type` will specify its mathematical derivation.

---

## 10. Model Version Semantics

- Constant: `EDGE_MODEL_VERSION = "edge_rules_v1.0.0"`
- Identifies the exact rule definitions and thresholds active on the edge node.
- Passed verbatim into `EdgeInference.model_version`.

---

## 11. Inference Method Semantics

- Constant: `EDGE_INFERENCE_METHOD = "rules"`
- Accurately declares that the Level 1 verdict was derived via deterministic static rules.
- Distinguishes rule-based edge verdicts from future `"embedded_ml"` or `"rules+embedded_ml"` methodologies.

---

## 12. Multiple-Rule Behavior & Precedence

When an observation exhibits multiple simultaneous irregularities (e.g., one channel is `None` while another is $-40^\circ\text{C}$), the edge rules evaluate in a **strict deterministic precedence sequence**:

1. **First: `dropout`**  
   If any channel is missing (`None` or `NaN`), the packet is immediately classified as `"dropout"`.
2. **Second: `sensor_fail_low`**  
   If all channels are present, but one or more fall below hardware rail floors, the packet is classified as `"sensor_fail_low"`.
3. **Third: `physical_bounds`**  
   If all channels are present and above rail floors, but exceed atmospheric boundaries, the packet is classified as `"physical_bounds"`.
4. **Fourth: All Clear**  
   The packet is classified as `"ok"` with `anomaly_flag = false`.

---

## 13. ESP32 Portability Considerations

`model/edge_rules.py` is architected specifically to be portable to C/C++ firmware running on an ESP32 microcontroller:
- **No heavy dependencies**: Requires zero `pandas`, `numpy`, `sklearn`, `scipy`, `shap`, or network libraries.
- **Minimal memory footprint**: Operates on scalar primitive floats ($< 24$ bytes RAM).
- **Sub-microsecond execution**: Consists of simple floating-point comparisons (`<`, `<=`, `>`) and boolean ORs ($\sim 0.5\,\mu\text{s}$ on an ESP32 at 240 MHz).

---

## 14. What the Edge Layer DOES NOT Do

To maintain extreme energy efficiency and operational simplicity, Level 1 Edge Inference **DOES NOT**:
- Maintain multi-hour rolling window history buffers.
- Compute temporal features (rate of change, slope, volatility z-scores).
- Execute the 100-tree Isolation Forest or ExtraTrees models.
- Perform diurnal CUSUM/EWMA drift accumulation.
- Corroborate readings across neighboring cluster stations.
- Compute SHAP feature attributions.
- Track 10-hour/24-hour sensor health circuit breakers.
- Impute suggested replacement values.

---

## 15. Difference Between Edge Inference (Level 1) and Central AI (Level 2)

| Dimension | Level 1: Edge Inference (`model/edge_rules.py`) | Level 2: Central AI (`model/detect.py`) |
|---|---|---|
| **Location** | ESP32 Microcontroller / Virtual Edge | Backend Cloud / Server |
| **Execution Time** | $< 1\,\mu\text{s}$ | $\sim 5 - 15\,\text{ms}$ |
| **Dependencies** | None (pure C++ / pure Python) | `scikit-learn`, `pandas`, `numpy`, `shap` |
| **Memory Required** | $< 1\,\text{KB}$ | $\sim 100 - 500\,\text{MB}$ |
| **Scope of Context** | Single instant reading ($t$) | Trailing $48\,\text{h}$ history, seasonal diurnal baselines, cluster peer network |
| **Fault Coverage** | Hard physical bounds, rail collapses, dropouts | Subtle drift, frozen streaks, thermodynamic conflicts, spikes, statistical anomalies |
| **Authority** | Initial advisory metadata attached to packet | Final system verdict & sensor health management |
| **Trust Model** | Untrusted by backend (subject to verification) | Fully authoritative verdict engine |
