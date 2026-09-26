# SkyGuard AI — ESP32 Level-1 Edge Node Firmware

## 1. ESP32 Hardware Architecture

The SkyGuard AI Level-1 Edge Node runs on the **Espressif ESP32-WROOM-32** dual-core microcontroller (240 MHz Tensilica Xtensa LX6, 520 KB SRAM, 4 MB Flash, 802.11 b/g/n 2.4 GHz Wi-Fi).

The ESP32 is exclusively responsible for **Level-1 Edge Processing**:
- Real-time transducer signal acquisition from physical sensors
- Deterministic edge validation and rule evaluation (dropout, rail collapse, unphysical bounds)
- Canonical `ObservationPacket` JSON construction
- Robust, bounded HTTP POST transmission to the central backend at `/api/ingest/observation`

```
  ┌────────────────────────┐      ┌────────────────────────┐
  │   BMP280 (I2C Bus)     │      │   DHT22 (1-Wire Bus)   │
  │ Temp (°C) + Pres (hPa) │      │   Rel. Humidity (%)    │
  └───────────┬────────────┘      └───────────┬────────────┘
              │ SDA (GPIO21) / SCL (GPIO22)   │ DATA (GPIO4)
              ▼                               ▼
    ┌───────────────────────────────────────────────────────┐
    │              ESP32 Microcontroller Core               │
    │  - Sensor Acquisition & NaN Sanitization              │
    │  - Level-1 Deterministic Edge Inference Engine        │
    │  - Canonical ObservationPacket JSON Serialization     │
    │  - SNTP UTC ISO-8601 Timestamp Generator             │
    │  - Wi-Fi Reconnect & Bounded Retry Poster             │
    └───────────────────────────┬───────────────────────────┘
                                │ HTTP POST (JSON)
                                ▼
    ┌───────────────────────────────────────────────────────┐
    │     Central Backend: POST /api/ingest/observation     │
    │     (ObservationIngestionService -> Central AI)       │
    └───────────────────────────────────────────────────────┘
```

> [!IMPORTANT]
> The ESP32 node does NOT run central machine learning models (Isolation Forest, SHAP, CUSUM/EWMA, spatial cross-station corroboration). Those responsibilities reside strictly in the central Python backend.

---

## 2. Sensor Selection

1. **Bosch BMP280 Barometric Pressure & Temperature Sensor**
   - **Interface**: I2C (Address `0x76` default, alternate `0x77`)
   - **Temperature range**: -40°C to +85°C (±1.0°C accuracy)
   - **Pressure range**: 300 hPa to 1100 hPa (±1.0 hPa accuracy)
   - **Metrics captured**: `temperature_c`, `pressure_hpa`
2. **Aosong DHT22 / AM2302 Capacitive Digital Humidity Sensor**
   - **Interface**: Single-bus digital interface
   - **Humidity range**: 0% to 100% RH (±2–5% RH accuracy)
   - **Metrics captured**: `humidity_pct`

---

## 3. GPIO Configuration

All hardware pin assignments are centralized in `edge/esp32/include/config.h`:

| ESP32 Pin | Sensor Pin | Signal / Function | Pull-up Requirement |
| :--- | :--- | :--- | :--- |
| **GPIO 21** | BMP280 SDA | I2C Data Line | 4.7 kΩ pull-up to 3.3V (often on breakout module) |
| **GPIO 22** | BMP280 SCL | I2C Clock Line | 4.7 kΩ pull-up to 3.3V (often on breakout module) |
| **GPIO 4** | DHT22 DATA | 1-Wire Bidirectional Data | 4.7 kΩ–10 kΩ pull-up to 3.3V |
| **3V3** | VCC (Both) | 3.3V DC Power Supply | Decoupling 100 nF capacitor recommended |
| **GND** | GND (Both) | Common Ground | Common Ground Rail |

---

## 4. Sensor Initialization

Initialization sequence implemented in `SensorManager::begin()` (`edge/esp32/src/sensors/sensor_manager.cpp`):
1. Initializes `Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN)` at 100 kHz.
2. Probes BMP280 at `0x76` (and automatically falls back to `0x77` if not found). Sets sampling parameters (ultra-high resolution, 16x oversampling, IIR filter coefficient 16).
3. Initializes `DHT` sensor instance on `DHT_PIN` (GPIO 4) with `DHT22` type.
4. Records boolean health flags (`_bmpInitialized`, `_dhtInitialized`). If a sensor fails initialization, firmware does not panic or hang; it continues operating and records `NAN` for the corresponding channels.

---

## 5. Reading Cycle

The firmware executes on a configurable timer loop (`SAMPLING_INTERVAL_MS = 10000` ms):
1. **Acquisition**: `SensorManager::readSensors()` queries BMP280 and DHT22.
2. **Sanitization**: Any read error or checksum failure returns `NAN` (Never fabricated `0.0` or sentinel values).
3. **Edge Inference**: `run_edge_inference_c()` evaluates deterministic Level-1 rules.
4. **Packet Building**: `build_observation_packet_json()` constructs the canonical JSON string.
5. **Transmission**: `HttpPoster::postObservation()` delivers the payload via HTTP POST.

---

## 6. Edge Validation

Local validation is performed in C/C++ without dynamic memory allocations or heavy math libraries.
Readings are validated for:
- Completeness (non-NaN)
- Hardware rail viability (transducer grounding / electrical disconnection)
- Physical plausibility (meteorological surface limits)

---

## 7. Edge Anomaly Rules & Precedence

Implemented in `edge/esp32/src/edge/edge_engine.cpp` with exact numeric parity to `model/edge_rules.py`:

```
   ┌─────────────┐
   │ Raw Reading │
   └──────┬──────┘
          │
          ▼
   [ Dropout Check ] ──(Any NaN/null)────────► status: "anomaly_detected", type: "dropout"
          │
          │ (All channels valid float)
          ▼
   [ Fail-Low Check ] ──(Temp<=-8°C / Pres<=150hPa / Hum<=3%)──► status: "anomaly_detected", type: "sensor_fail_low"
          │
          │ (Above rail floors)
          ▼
   [ Physical Bounds ] ──(Temp<-50|>60 / Pres<870|>1085 / Hum<0|>100)──► status: "anomaly_detected", type: "physical_bounds"
          │
          │ (Within all physical limits)
          ▼
   status: "ok", anomaly_flag: false, anomaly_type: null
```

### Threshold Constants

| Rule | Parameter | Threshold | Fault Type Triggered |
| :--- | :--- | :--- | :--- |
| **Dropout** | Any channel | `isnan()` / `NULL` | `dropout` |
| **Fail-Low** | `temperature_c` | $\le -8.0$ °C | `sensor_fail_low` |
| **Fail-Low** | `pressure_hpa` | $\le 150.0$ hPa | `sensor_fail_low` |
| **Fail-Low** | `humidity_pct` | $\le 3.0$ % | `sensor_fail_low` |
| **Physical Bounds** | `temperature_c` | $< -50.0$ °C or $> 60.0$ °C | `physical_bounds` |
| **Physical Bounds** | `pressure_hpa` | $< 870.0$ hPa or $> 1085.0$ hPa | `physical_bounds` |
| **Physical Bounds** | `humidity_pct` | $< 0.0$ % or $> 100.0$ % | `physical_bounds` |

---

## 8. ObservationPacket Construction

The ESP32 serializes strictly conforming JSON matching `model/contracts.py`:

```json
{
  "event_id": "550e8400-e29b-41d4-a716-446655440000",
  "station_id": "DELHI_CENTRAL",
  "device_id": "esp32_c44f33112233",
  "observed_at": "2026-09-23T17:45:00Z",
  "sequence_number": 1,
  "readings": {
    "temperature_c": 28.45,
    "pressure_hpa": 1012.30,
    "humidity_pct": 58.20
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
    "firmware_version": "esp32_edge_v1.0.0",
    "battery_voltage": null,
    "signal_strength": -64.0
  }
}
```

> [!TIP]
> When a reading is unavailable (`NAN`), it is serialized directly as `null`, never `-999` or `0.0`.

---

## 9. Event ID Generation

- Each observation packet generates a compliant **UUIDv4** string (`xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx` where `y \in {8, 9, a, b}`) using `esp_random()` hardware true random number generator (TRNG).
- **Retry Invariant**: If HTTP transmission fails, the **exact same `event_id`** is preserved across all retry attempts.

---

## 10. Device ID Generation

- Derived deterministically from the ESP32 hardware base MAC address: `esp32_XXXXXXXXXXXX` (e.g. `esp32_c44f33112233`).
- Stable across reboots and identical for the lifetime of the physical silicon.

---

## 11. Sequence Management

- A 32-bit monotonically increasing counter starting at `1` on boot.
- Increments only when a *new* observation cycle begins.
- Preserved unchanged during retry transmissions.
- Documented note: In v1.0.0, sequence resets to 1 upon hardware reboot. The backend sequence tracker handles reboot resets safely.

---

## 12. NTP & Time Handling

- Firmware initializes SNTP client with `pool.ntp.org` and `time.google.com`.
- Timezone is explicitly UTC (`0` offset, `0` DST).
- Timestamps are formatted as ISO-8601 UTC strings: `YYYY-MM-DDTHH:MM:SSZ`.
- Fallback: If NTP synchronization fails before transmission, timestamp defaults to `"1970-01-01T00:00:00Z"`; the central ingestion API validates and normalizes received timestamps.

---

## 13. Wi-Fi Configuration

- Configured in `edge/esp32/include/config.h` via `WIFI_SSID` and `WIFI_PASSWORD`.
- Auto-reconnect routine runs non-blocking before every transmission cycle.
- Source template uses placeholders; credentials are never committed.

---

## 14. HTTP Ingestion

- Endpoint: `POST <BACKEND_URL>/api/ingest/observation`
- Configurable via `BACKEND_HOST` and `BACKEND_PORT` in `config.h`.
- Payload size: Typically 420–480 bytes, bounded within a fixed 1024-byte stack buffer.

---

## 15. HTTP Response Handling & Retry Policy

| HTTP Status Code | Meaning | Firmware Handling |
| :--- | :--- | :--- |
| **200 OK** | Observation Accepted | Success: Advance sequence number, proceed to next cycle. |
| **409 Conflict** | Duplicate `event_id` | Handled as Success (packet already processed by backend during retry). |
| **400 / 422** | Schema / Validation Failure | Fatal Packet Error: Log error and discard packet. Do NOT retry bad payload. |
| **404 Not Found** | Unknown `station_id` | Configuration Error: Log error and discard packet. Do NOT retry. |
| **5xx Server Error** | Backend Failure | Transient Failure: Retry up to `HTTP_MAX_RETRIES` (3 attempts) with exponential backoff (`2s`, `4s`, `8s`). |
| **Timeout / Connect Error** | Network Down | Retry up to `HTTP_MAX_RETRIES` (3 attempts). Retain same packet metadata. |

---

## 16. Memory Safety

- Zero heap allocations (`malloc`/`new`) inside the main loop or packet builder.
- Static / stack-based fixed-length buffers:
  - JSON output buffer: 1024 bytes
  - UUID buffer: 37 bytes
  - ISO-8601 timestamp buffer: 32 bytes
- No heavy C++ standard library dependencies (no `<iostream>`, `<vector>`, or heavy string streams).

---

## 17. Security Considerations

- Local development uses HTTP.
- Production HTTPS is supported via `WiFiClientSecure` with root CA certificates.
- Credentials (`WIFI_SSID`, `WIFI_PASSWORD`) are kept in separate untracked config headers.

---

## 18. Firmware Compilation & Flashing Guide

### Prerequisites
- [PlatformIO Core (CLI)](https://docs.platformio.org/en/latest/core/index.html) or [VS Code PlatformIO Extension](https://platformio.org/install/ide?install=vscode)

### Build & Flash Steps

1. Open a terminal in `edge/esp32/`:
   ```bash
   cd edge/esp32
   ```
2. Edit `include/config.h` to set your local Wi-Fi SSID, password, and backend IP address.
3. Connect your ESP32 board via USB.
4. Compile the firmware:
   ```bash
   pio run
   ```
5. Flash to the connected board:
   ```bash
   pio run --target upload
   ```
6. Open the serial monitor:
   ```bash
   pio device monitor -b 115200
   ```

---

## 19. Serial Monitor Verification Procedure

Expected boot and observation trace:

```text
=====================================================
[SkyGuard ESP32] Initializing Level-1 Edge Node
=====================================================
[SkyGuard ESP32] Firmware Version : esp32_edge_v1.0.0
[SkyGuard ESP32] Device ID        : esp32_c44f33112233
[SkyGuard ESP32] Station ID       : DELHI_CENTRAL
[SkyGuard ESP32] Ingestion URL    : http://192.168.1.100:8000/api/ingest/observation
[WiFi] Connecting to MyNetwork ... Connected! IP: 192.168.1.150 RSSI: -62 dBm
[NTP] Initializing SNTP synchronization... Time sync successful!
[Sensors] Initializing sensors on I2C (SDA=21, SCL=22) and DHT22 (Pin=4)...
[Sensors] BMP280 detected successfully at 0x76!
[Sensors] DHT22 initialized on GPIO 4.

[LOOP] --- Starting Observation Cycle #1 ---
[Sensors] Readings -> Temp: 28.45 °C, Pres: 1012.30 hPa, Hum: 58.20 %
[Edge] Level-1 Inference -> Status: ok, Anomaly: 0, Fault: (none)
[Protocol] Generated Event ID: 550e8400-e29b-41d4-a716-446655440000
[HTTP] POST http://192.168.1.100:8000/api/ingest/observation (Attempt 1/3)
[HTTP] Observation accepted! (HTTP 200)
```

---

## 20. Verification Status & Test Coverage

### Host-Side Automated Tests Executed
1. **Protocol Compatibility Suite** (`tests/test_esp32_protocol_compatibility.py`):
   - Validates firmware JSON payload against Pydantic `ObservationPacket` contract.
   - Tests nullable field handling, UUIDv4 compliance, ISO-8601 timestamps, and full HTTP ingestion pipeline integration.
2. **Edge Rules Compatibility Suite** (`tests/test_esp32_edge_rules_compatibility.py`):
   - Validates all 11 test vectors and strict precedence rules (`dropout` $\to$ `sensor_fail_low` $\to$ `physical_bounds` $\to$ `ok`).
3. **Native C++ Unit Test Runner** (`tests/cpp/test_edge_engine.cpp`):
   - Compiled with MinGW `g++` and executed natively on host.
   - 51 / 51 assertions passed (100%).
4. **Backend Regression Test Suite**:
   - All 119 existing backend tests passed without regression.

### Physical Hardware & Compilation Status
- **Firmware Source Implementation**: Completed.
- **Native C++ Core Execution**: Completed & 100% verified.
- **Firmware Compilation (PlatformIO/ESP-IDF)**: Toolchain unavailable on host machine; compilation pending toolchain installation.
- **Physical Hardware Validation**: Not performed (No physical ESP32 connected to test runner environment).
