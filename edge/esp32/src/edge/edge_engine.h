#pragma once

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

// =============================================================================
// SkyGuard AI Level 1 Edge Inference Engine (C/C++ Implementation)
// Strictly synchronized with model/edge_rules.py and docs/EDGE_INFERENCE.md
// =============================================================================

// Threshold Constants (Synchronized with config.py & model/edge_rules.py)
#define EDGE_TEMP_PHYSICAL_MIN      -50.0f
#define EDGE_TEMP_PHYSICAL_MAX       60.0f
#define EDGE_PRESSURE_PHYSICAL_MIN  870.0f
#define EDGE_PRESSURE_PHYSICAL_MAX 1085.0f
#define EDGE_HUMIDITY_PHYSICAL_MIN    0.0f
#define EDGE_HUMIDITY_PHYSICAL_MAX  100.0f

#define EDGE_TEMP_FAIL_LOW           -8.0f
#define EDGE_PRESSURE_FAIL_LOW      150.0f
#define EDGE_HUMIDITY_FAIL_LOW        3.0f

typedef struct {
    const char* status;          // "ok", "anomaly_detected"
    bool anomaly_flag;           // true if rule triggered, false otherwise
    const char* anomaly_type;    // "dropout", "sensor_fail_low", "physical_bounds", or NULL
    const char* model_version;   // "edge_rules_v1.0.0"
    const char* inference_method;// "rules"
} EdgeInferenceResult;

/**
 * Runs Level 1 deterministic edge inference on raw floating point readings.
 * 
 * Precedence Order:
 *   1. dropout (missing/NaN values)
 *   2. sensor_fail_low (hardware electrical rail floor values)
 *   3. physical_bounds (unphysical meteorological extremes)
 *   4. ok (normal observation)
 * 
 * @param temp_c        Temperature in °C (or NAN if unavailable)
 * @param pressure_hpa  Pressure in hPa (or NAN if unavailable)
 * @param humidity_pct  Relative humidity in % (or NAN if unavailable)
 * @return EdgeInferenceResult populated matching canonical EdgeInference schema
 */
EdgeInferenceResult run_edge_inference_c(
    float temp_c,
    float pressure_hpa,
    float humidity_pct
);

#ifdef __cplusplus
}
#endif
