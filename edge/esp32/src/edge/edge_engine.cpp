#include "edge_engine.h"
#include <math.h>

#ifndef NULL
#define NULL 0
#endif

static const char* STATUS_OK = "ok";
static const char* STATUS_ANOMALY = "anomaly_detected";

static const char* FAULT_DROPOUT = "dropout";
static const char* FAULT_FAIL_LOW = "sensor_fail_low";
static const char* FAULT_BOUNDS = "physical_bounds";

static const char* MODEL_VERSION_STR = "edge_rules_v1.0.0";
static const char* INFERENCE_METHOD_STR = "rules";

EdgeInferenceResult run_edge_inference_c(
    float temp_c,
    float pressure_hpa,
    float humidity_pct
) {
    EdgeInferenceResult result;
    result.model_version = MODEL_VERSION_STR;
    result.inference_method = INFERENCE_METHOD_STR;

    // ── Precedence 1: Dropout Check ───────────────────────────────────────
    // Missing, NaN, or unreadable sensor channel
    if (isnan(temp_c) || isnan(pressure_hpa) || isnan(humidity_pct)) {
        result.status = STATUS_ANOMALY;
        result.anomaly_flag = true;
        result.anomaly_type = FAULT_DROPOUT;
        return result;
    }

    // ── Precedence 2: Sensor Fail-Low Check ───────────────────────────────
    // Hardware transducer ground/rail collapse to hardware noise floor
    if (temp_c <= EDGE_TEMP_FAIL_LOW ||
        pressure_hpa <= EDGE_PRESSURE_FAIL_LOW ||
        humidity_pct <= EDGE_HUMIDITY_FAIL_LOW) {
        result.status = STATUS_ANOMALY;
        result.anomaly_flag = true;
        result.anomaly_type = FAULT_FAIL_LOW;
        return result;
    }

    // ── Precedence 3: Physical Bounds Check ───────────────────────────────
    // Values outside planetary surface meteorological limits
    if (temp_c < EDGE_TEMP_PHYSICAL_MIN || temp_c > EDGE_TEMP_PHYSICAL_MAX ||
        pressure_hpa < EDGE_PRESSURE_PHYSICAL_MIN || pressure_hpa > EDGE_PRESSURE_PHYSICAL_MAX ||
        humidity_pct < EDGE_HUMIDITY_PHYSICAL_MIN || humidity_pct > EDGE_HUMIDITY_PHYSICAL_MAX) {
        result.status = STATUS_ANOMALY;
        result.anomaly_flag = true;
        result.anomaly_type = FAULT_BOUNDS;
        return result;
    }

    // ── Precedence 4: Normal Reading ─────────────────────────────────────
    result.status = STATUS_OK;
    result.anomaly_flag = false;
    result.anomaly_type = NULL;
    return result;
}
