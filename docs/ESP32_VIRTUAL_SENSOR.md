# ESP32 VIRTUAL SENSOR MODE

## 1. Purpose
This document details the architecture and implementation of the ESP32 Virtual Sensor Mode, designed for evaluating the SkyGuard AI edge integration without requiring physical temperature, pressure, and humidity sensors. The architecture enables streaming anomaly-injected CSV datasets directly to the ESP32 over USB Serial.

## 2. Architecture
The architecture comprises a host PC streaming historical CSV rows via USB Serial to the ESP32. The ESP32 parses the telemetry, processes the reading using the exact existing deterministic rules (Level 1 Inference), builds the canonical `ObservationPacket`, and submits it to the Central AI over Wi-Fi.

## 3. CSV Format
The datasets reside in `data/` (e.g., `AWS-BHO-030_labeled.csv`).
Schema: `timestamp,temperature_c,pressure_hpa,humidity_pct,station_id,station_name,cluster_id,role,is_anomaly,fault_type`
The host streamer extracts only `timestamp`, `temperature_c`, `pressure_hpa`, `humidity_pct`, and `station_id`. Ground truth labels (`is_anomaly`, `fault_type`) are explicitly omitted.

## 4. Serial Protocol
Data is streamed row-by-row as newline-delimited JSON objects over Serial.
Example payload:
```json
{"timestamp": "2025-01-01 00:00:00", "station_id": "AWS-BHO-030", "temperature_c": 14.2, "pressure_hpa": 960.7, "humidity_pct": 94.0}
```

## 5. PC Streamer
The script `scripts/esp32_csv_streamer.py` handles parsing the CSV, stripping unnecessary fields, pacing the payload delivery according to timestamps (with a configurable `--speed` multiplier), and writing the JSON over the COM port.

## 6. ESP32 Virtual Mode
Activated by setting `#define ENABLE_VIRTUAL_SENSOR_MODE 1` in `edge/esp32/include/config.h`. When enabled, the ESP32 listens on `Serial` instead of sampling I2C/GPIO buses. It parses the incoming JSON using `ArduinoJson`.

## 7. Packet Construction
Virtual mode utilizes the exact same `build_observation_packet_json` method as physical mode. The constructed canonical packet is structurally identical, preserving backend interoperability.

## 8. Event ID
Each received row generates a new UUIDv4 `event_id` in the firmware, exactly mimicking the physical sensor flow. Retries preserve the original `event_id`.

## 9. Sequence Number
The `sequence_number` is strictly monotonically increasing. It advances only after the ESP32 receives an `INGEST_SUCCESS` or `INGEST_DUPLICATE_ACCEPTED` acknowledgment from the backend API.

## 10. Timestamp Behavior
When available in the CSV, the historical ISO-8601 observation timestamp is forwarded intact. If missing, the ESP32 falls back to its NTP-synced wall-clock time. The timestamp represents *observation time*, not network ingestion time.

## 11. Ground-Truth Isolation
Ground-truth columns (`is_anomaly`, `fault_type`) are deliberately excluded from the Serial payload by the PC Streamer. The ESP32's `EdgeInferenceResult` is populated solely via local evaluation of the raw telemetry, guaranteeing no data leakage into the edge rules.

## 12. Wi-Fi / Backend Flow
Packets are transmitted via HTTP POST to the existing `/api/ingest/observation` endpoint. Retry logic, timeouts, and network failure backoffs remain fully functional.

## 13. Testing
**End-to-End Flow:** 
1. `esp32_csv_streamer.py` reads a CSV row.
2. ESP32 processes and flags physical anomalies.
3. Payload is sent to `main.py` ingestion API.
4. Central Isolation Forest computes the final score.

(Note: Tests requiring actual hardware execution are subject to physical ESP32 availability).

## 14. Hardware Requirements
- ESP32 Development Board.
- USB Cable connecting ESP32 to the Host PC.
- No physical BMP280 or DHT22 sensors are required when `ENABLE_VIRTUAL_SENSOR_MODE` is 1.

## 15. Known Limitations
- The timestamp spacing on the ESP32 is synthetic; extreme replay speeds may cause queue backups.
- Central AI validation requiring deep temporal windows may diverge from real-time evaluation if the historical replay lacks sufficient warmup data.
