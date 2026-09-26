# Step 10 — Frontend Data Integration Audit and Fixes

## 1. Backend Endpoint → Frontend Consumer Mapping

| Backend Endpoint | Method | Authoritative Schema / Fields | Frontend Service / Consumer | Validator / Type | Components Rendered |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `/api/stations` | GET | `list[{station_id, name, lat, lon, status}]` | `stationService.getStations()` | `validateStations()` -> `Station[]` | `StationSelector`, `StationMap`, `StationOverviewCard` |
| `/api/system-status` | GET | `{mode, replay_step_seconds, live_poll_interval_seconds}` | `systemStatusService.getSystemStatus()` | `validateSystemStatus()` -> `SystemStatus` | `Header`, `SystemControlPanel` |
| `/api/current-reading` | GET | `{station_id, timestamp, temperature_c: {value, normal_min, normal_max}, pressure_hpa: {...}, humidity_pct: {...}, anomaly_score_pct, is_anomaly, fault_type, severity, model_confidence_pct, rule_confidence_pct, sensor_health_pct, sensor_health_status, sensor_parameters, suggested_values, source}` | `currentReadingService.getCurrentReading(stationId)` | `validateCurrentReading()` -> `CurrentSensorReading` | `SensorMetricCard`, `StatusOverviewCards`, `SelectedStationCard` |
| `/api/trends` | GET | `{station_id, hours, points: list[{timestamp, temperature_c, pressure_hpa, humidity_pct, is_anomaly, fault_type, severity, anomaly_score_pct, suggested_temperature_c, suggested_pressure_hpa, suggested_humidity_pct, health_status, source}]}` | `trendService.getTrends(stationId, hours)` | `validateTrends()` -> `TrendData` | `TrendChart`, `AnalyticsTrendChart`, `ReportTelemetrySection` |
| `/api/network-status` | GET | `{overall_status, active_stations_count, total_stations_count, active_anomalies_count, avg_sensor_health_pct, last_updated, mode}` | `stationService.getNetworkStatus()` | `validateNetworkStatus()` -> `NetworkStatus` | `HeaderNetworkStatusBadge`, `StationNetworkSummary` |
| `/api/anomalies/latest` | GET | `null` OR `{anomaly_id, station_id, timestamp, anomaly_score_pct, severity, type, root_cause, description, affected_parameters, observed_values, suggested_values, regime, network_corroboration, decision_basis, model_status}` | `anomalyService.getLatestAnomaly(stationId)` | `validateLatestAnomaly()` -> `LatestAnomaly \| null` | `LatestAnomalyCard`, `LatestAnomalyBanner`, `AlertSummaryCards` |
| `/api/anomalies/recent` | GET | `list[{anomaly_id, station_id, timestamp, anomaly_score_pct, severity, type, root_cause, description, affected_parameters, observed_values, suggested_values, regime, network_corroboration, decision_basis, model_status}]` | `anomalyService.getRecentAnomalies(limit, stationId)` | `validateRecentAnomalies()` -> `RecentAnomalyItem[]` | `RecentAnomaliesCard`, `RecentAnomaliesList`, `AnomalySelector` |
| `/api/sensor-health` | GET | `{station_id, health_pct, status, parameters: {temperature_c, pressure_hpa, humidity_pct}}` | `sensorHealthService.getSensorHealth(stationId)` | `validateSensorHealth()` -> `SensorHealthData` (maps `health_pct` -> `sensor_health_pct`, `status` -> `sensor_health_status`) | `SensorChannelOverview`, `SensorHealthCard` |
| `/api/explain/{id}` | GET | `{anomaly_id, text, model_confidence_pct, rules_fired, regime, corroboration_pct, suggested_action, generated_at}` | `explanationService.getExplanation(anomalyId)` | `validateExplanation()` -> `AnomalyExplanation` | `AnomalyDetailModal`, `AlertDetailSection` |
| `/api/inject-anomaly` | POST | `{station_id, type}` -> `{status, message, anomaly: {...}}` | `anomalyService.injectAnomaly(payload)` | Direct response | Testing / Demonstration UI |
| `/ws/live` | WS | Push events: `CONNECTION_READY`, `TELEMETRY_UPDATE`, `ANOMALY_DETECTED`, `HISTORY_PURGED` | `useLiveWebSocket.ts` | Dispatches to react-query cache and component handlers | Live Telemetry Cards, Notifications |

---

## 2. WebSocket -> Frontend Mapping

- **Endpoint**: `/ws/live`
- **Connection Handshake**: Backend emits `{"type": "CONNECTION_READY", "mode": "live"|"replay", "timestamp": "..."}` upon connect.
- **Data Flow**:
  - `useLiveWebSocket` hook initializes `WebSocket` connection with auto-reconnection and exponential backoff.
  - On receiving `TELEMETRY_UPDATE` event, updates the active station current reading cache in TanStack Query.
  - On receiving `ANOMALY_DETECTED` event, invalidates `/api/anomalies/latest` and `/api/anomalies/recent` queries.
  - On receiving `HISTORY_PURGED` event, resets chart buffers.
- **Heartbeat**: Frontend sends text `"ping"` and receives `"pong"`.

---

## 3. Active Anomaly Drift Data Flow

- **Components**: `LatestAnomalyCard.tsx` (Dashboard) & `LatestAnomalyBanner.tsx` (Alerts)
- **Data Source**: Authoritative source is `GET /api/anomalies/latest?station_id={station_id}` supplemented by WebSocket `ANOMALY_DETECTED` cache invalidation.
- **Parameter Cause Resolution**:
  - Bound directly to `anomaly.affected_parameters` (array of strings, e.g. `["temperature_c"]`, `["pressure_hpa"]`, or `["multivariate"]`).
  - Formatted cleanly into badges without inventing parameters.
- **Station Identification**:
  - Bound to `anomaly.station_id`. Displayed explicitly with a station badge to prevent confusion when multiple stations exist.
- **Top-Right Score Resolution**:
  - Displays `anomaly.anomaly_score_pct` as `Math.round(score)%`.
  - If null/missing: Safely renders `—` without displaying `0`, `NaN`, `undefined`, or fake values.
  - Preserves backend distinction between raw edge confidence vs central ensemble confidence.

---

## 4. Station Selection Data Flow

- **State Management**: `StationContext.tsx` holds `selectedStationId` (defaults to first station returned by `/api/stations`).
- **Synchronization**:
  - When user selects a station in `StationSelector.tsx` or `StationMap.tsx`, `setSelectedStationId(id)` updates context.
  - All dependent query hooks (`useCurrentReading(selectedStationId)`, `useTrends(selectedStationId, hours)`, `useLatestAnomaly(selectedStationId)`, `useRecentAnomalies(limit, selectedStationId)`, `useSensorHealth(selectedStationId)`) reactively refetch for that specific station ID.
  - If an anomaly event occurs on Station B while Station A is selected, station-filtered queries do not show Station B's anomaly under Station A's view.

---

## 5. Edge / Central Data Mapping

- Backend now records distinct edge inferences (`ObservationPacket.edge_inference`), central inferences (`CentralInferenceVerdict`), and comparison records (`EdgeCentralComparison`).
- Frontend presents the authoritative API payload returned by `ObservationIngestionService` and `/api/anomalies/*`:
  - `decision_basis`: Explains whether anomaly verdict was `PHYSICS_ONLY`, `MODEL_AND_RULE_SUPPORTED`, `RULE_ONLY_STATISTICAL`, `MODEL_CONFIRMED`, `MODEL_UNAVAILABLE`, or `INSUFFICIENT_EVIDENCE`.
  - `model_status`: Reflects `AVAILABLE`, `UNAVAILABLE_WARMUP`, or `UNAVAILABLE_MISSING_FEATURES`.
  - Frontend acts purely as a presentation layer without synthesizing consensus or executing custom AND/OR arbitration.

---

## 6. Null / Missing Data Handling

The backend preserves sensor dropouts as `null` values (`temperature_c.value = null`, `pressure_hpa.value = null`, `humidity_pct.value = null`).
- **Types & Validators**: `MetricValueRange`, `CurrentSensorReading`, `TrendPoint`, `LatestAnomaly`, and `TelemetryHistoryRecord` have been updated so `value?: number | null`. Validators permit `null` values.
- **UI Components**:
  - `SensorMetricCard.tsx`: Safely formats `null` value as `—` and skips range bar calculation when value is null.
  - `TrendChart.tsx` & `AnalyticsTrendChart.tsx`: Filters out `null` coordinate values when plotting SVG polyline paths, avoiding `NaN` coordinate errors.
  - `TelemetryHistoryTable.tsx`: Formats null sensor readings as `—`.
  - `SensorChannelOverview.tsx`: Displays `—` when sensor reading is null.
  - `SelectedStationCard.tsx`: Displays `—` in telemetry chips when metrics are null.
  - `analytics.ts`: Accepts `(number | null | undefined)[]` and computes statistics on non-null values only.

---

## 7. Error Handling

- **HTTP Status Codes**:
  - `200 OK`: Successful data fetch.
  - `404 Not Found`: Handled gracefully in `currentReadingService` and `anomalyService` (e.g. no reading yet or no anomaly returns empty/null state without crashing).
  - `409 Conflict`: Handled gracefully during anomaly injection or duplicate observation ingestion.
  - `422 Unprocessable Entity` & `500 Internal Error`: Triggers TanStack Query error state with user-friendly retry alerts.
- **WebSocket Disconnect**: Automatically attempts reconnection using exponential backoff without freezing UI.

---

## 8. Mock Mode vs Live Mode

- `VITE_USE_MOCK_DATA` environment variable allows development testing with mock generators in `FRONTEND/src/services/mockData.ts`.
- When `VITE_USE_MOCK_DATA=false` (production/live default), all API services call real FastAPI endpoints.
- Mock mode is preserved for developer workflow without interfering with live backend integration.

---

## 9. Bugs Found

1. **Strict TypeScript & Validator Rejections on Null Values**: `MetricValueRange`, `CurrentSensorReading`, and `TrendPoint` required non-null `number`, causing validator errors when the backend sent valid `null` readings on sensor dropouts.
2. **Missing Station Association Badge in Anomaly Card**: `LatestAnomalyCard.tsx` did not display `anomaly.station_id`, making it ambiguous which station experienced the anomaly.
3. **Missing Affected Parameters Display**: `LatestAnomalyCard.tsx` and `LatestAnomalyBanner.tsx` did not bind `anomaly.affected_parameters`, omitting the causative parameter.
4. **Unsafe Score Handling**: Top-right anomaly score rendering threw or produced `NaN%` when `anomaly_score_pct` was null or missing.
5. **Trend Chart Coordinate Computation on Nulls**: `TrendChart.tsx` and `AnalyticsTrendChart.tsx` failed or computed invalid SVG paths when a `TrendPoint` contained `null` values.
6. **Telemetry History & Sensor Overview Null Displays**: Components rendered `null` or unformatted text rather than standard `—`.

---

## 10. Fixes Applied

1. Updated `FRONTEND/src/types/index.ts` to allow `number | null` across all reading values and scores.
2. Updated `FRONTEND/src/services/validators.ts` to accept `null` in `validateCurrentReading`, `validateTrends`, `validateLatestAnomaly`, and `validateRecentAnomalies`.
3. Updated `FRONTEND/src/components/dashboard/LatestAnomalyCard.tsx` to display `station_id`, `affected_parameters`, and formatted score with safe fallback `—`.
4. Updated `FRONTEND/src/components/alerts/LatestAnomalyBanner.tsx` to display `station_id`, `affected_parameters`, and formatted evidence score.
5. Updated `TrendChart.tsx`, `AnalyticsTrendChart.tsx`, and `ReportTelemetrySection.tsx` to filter null points before SVG path rendering.
6. Updated `SensorMetricCard.tsx`, `StatusOverviewCards.tsx`, `SensorChannelOverview.tsx`, `SelectedStationCard.tsx`, and `TelemetryHistoryTable.tsx` to render `—` for null readings.
7. Updated `FRONTEND/src/utils/analytics.ts` to sanitize and filter null inputs.

---

## 11. Remaining Backend/Frontend Contract Gaps

- **Edge vs Central Multi-Confidence Display in Header**: Header overview badge aggregates overall network health; individual edge vs central confidence breakdown is currently provided in detailed anomaly/explanation endpoints.
- **Physical Sensor Hardware Latency**: In live deployment, network jitter and ESP32 WiFi reconnects are handled via client-side WebSocket reconnect and query caching.

---

## 12. Testing Performed

- **TypeScript Compilation & Frontend Build**: Executed `npm run build` (`tsc && vite build`) in `FRONTEND/` with **0 errors**.
- **Backend Test Suite**: Ran all 156 unit tests across `tests/` with **156/156 passed**.
- **Integration Contract & Acceptance Test**: Created `tests/test_step10_frontend_contract.py` verifying:
  - Station association on anomalies (`station_id`).
  - Parameter attribution (`affected_parameters`).
  - Score preservation (`anomaly_score_pct`).
  - Current reading metric structures (`MetricValueRange` contract with null tolerance).
  - Trend history query and null safety.
  - Sensor health endpoint mapping.
  - Result: **4/4 passed (100%)**.
