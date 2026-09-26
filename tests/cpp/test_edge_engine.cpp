/**
 * tests/cpp/test_edge_engine.cpp
 * 
 * Native host-side C++ unit test runner for the ESP32 firmware core modules:
 *   - edge_engine (Level 1 deterministic rules)
 *   - packet_builder (UUIDv4 generation and JSON serialization)
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <assert.h>
#include "../../edge/esp32/src/edge/edge_engine.h"
#include "../../edge/esp32/src/protocol/packet_builder.h"

static int g_tests_run = 0;
static int g_tests_passed = 0;

#define TEST_ASSERT(cond, msg) do { \
    g_tests_run++; \
    if (!(cond)) { \
        fprintf(stderr, "[FAIL] Line %d: %s\n", __LINE__, msg); \
    } else { \
        g_tests_passed++; \
    } \
} while(0)

void test_uuidv4_format() {
    char uuid[40];
    generate_uuid_v4(uuid);
    TEST_ASSERT(strlen(uuid) == 36, "UUID length must be 36 chars");
    TEST_ASSERT(uuid[8] == '-', "UUID dash at pos 8");
    TEST_ASSERT(uuid[13] == '-', "UUID dash at pos 13");
    TEST_ASSERT(uuid[18] == '-', "UUID dash at pos 18");
    TEST_ASSERT(uuid[23] == '-', "UUID dash at pos 23");
    TEST_ASSERT(uuid[14] == '4', "UUID version 4 marker");
    TEST_ASSERT(uuid[19] == '8' || uuid[19] == '9' || uuid[19] == 'a' || uuid[19] == 'b', "UUID variant marker");
}

void test_edge_engine_vectors() {
    // 1. Normal
    EdgeInferenceResult r1 = run_edge_inference_c(25.0f, 1013.25f, 50.0f);
    TEST_ASSERT(strcmp(r1.status, "ok") == 0, "Normal status must be 'ok'");
    TEST_ASSERT(r1.anomaly_flag == false, "Normal anomaly_flag must be false");
    TEST_ASSERT(r1.anomaly_type == NULL, "Normal anomaly_type must be NULL");
    TEST_ASSERT(strcmp(r1.model_version, "edge_rules_v1.0.0") == 0, "Model version check");
    TEST_ASSERT(strcmp(r1.inference_method, "rules") == 0, "Inference method check");

    // 2. Dropout (NaN on temp)
    EdgeInferenceResult r2 = run_edge_inference_c(NAN, 1013.25f, 50.0f);
    TEST_ASSERT(strcmp(r2.status, "anomaly_detected") == 0, "Dropout status");
    TEST_ASSERT(r2.anomaly_flag == true, "Dropout flag");
    TEST_ASSERT(strcmp(r2.anomaly_type, "dropout") == 0, "Dropout type");

    // 2b. Dropout (NaN on pressure)
    EdgeInferenceResult r2b = run_edge_inference_c(25.0f, NAN, 50.0f);
    TEST_ASSERT(strcmp(r2b.anomaly_type, "dropout") == 0, "Dropout pressure type");

    // 2c. Dropout (NaN on humidity)
    EdgeInferenceResult r2c = run_edge_inference_c(25.0f, 1013.25f, NAN);
    TEST_ASSERT(strcmp(r2c.anomaly_type, "dropout") == 0, "Dropout humidity type");

    // 3. Fail low temp (<= -8.0°C)
    EdgeInferenceResult r3 = run_edge_inference_c(-10.0f, 1013.25f, 50.0f);
    TEST_ASSERT(r3.anomaly_flag == true, "Fail-low temp flag");
    TEST_ASSERT(strcmp(r3.anomaly_type, "sensor_fail_low") == 0, "Fail-low temp type");

    // 4. Fail low pressure (<= 150.0 hPa)
    EdgeInferenceResult r4 = run_edge_inference_c(25.0f, 100.0f, 50.0f);
    TEST_ASSERT(r4.anomaly_flag == true, "Fail-low pres flag");
    TEST_ASSERT(strcmp(r4.anomaly_type, "sensor_fail_low") == 0, "Fail-low pres type");

    // 5. Fail low humidity (<= 3.0%)
    EdgeInferenceResult r5 = run_edge_inference_c(25.0f, 1013.25f, 2.0f);
    TEST_ASSERT(r5.anomaly_flag == true, "Fail-low hum flag");
    TEST_ASSERT(strcmp(r5.anomaly_type, "sensor_fail_low") == 0, "Fail-low hum type");

    // 6. Physical temp upper bound (> 60.0°C)
    EdgeInferenceResult r6 = run_edge_inference_c(65.0f, 1013.25f, 50.0f);
    TEST_ASSERT(r6.anomaly_flag == true, "Physical bounds temp upper flag");
    TEST_ASSERT(strcmp(r6.anomaly_type, "physical_bounds") == 0, "Physical bounds temp upper type");

    // 7. Physical pressure lower bound (< 870.0 hPa, > 150.0 hPa)
    EdgeInferenceResult r7 = run_edge_inference_c(25.0f, 850.0f, 50.0f);
    TEST_ASSERT(r7.anomaly_flag == true, "Physical bounds pres lower flag");
    TEST_ASSERT(strcmp(r7.anomaly_type, "physical_bounds") == 0, "Physical bounds pres lower type");

    // 8. Physical pressure upper bound (> 1085.0 hPa)
    EdgeInferenceResult r8 = run_edge_inference_c(25.0f, 1100.0f, 50.0f);
    TEST_ASSERT(r8.anomaly_flag == true, "Physical bounds pres upper flag");
    TEST_ASSERT(strcmp(r8.anomaly_type, "physical_bounds") == 0, "Physical bounds pres upper type");

    // 9. Physical humidity upper bound (> 100.0%)
    EdgeInferenceResult r9 = run_edge_inference_c(25.0f, 1013.25f, 105.0f);
    TEST_ASSERT(r9.anomaly_flag == true, "Physical bounds hum upper flag");
    TEST_ASSERT(strcmp(r9.anomaly_type, "physical_bounds") == 0, "Physical bounds hum upper type");

    // 10. Precedence: Dropout overrides fail-low
    EdgeInferenceResult r10 = run_edge_inference_c(NAN, 50.0f, 50.0f);
    TEST_ASSERT(strcmp(r10.anomaly_type, "dropout") == 0, "Precedence: dropout over fail-low");

    // 11. Precedence: Fail-low overrides physical bounds
    EdgeInferenceResult r11 = run_edge_inference_c(-10.0f, 1150.0f, 50.0f);
    TEST_ASSERT(strcmp(r11.anomaly_type, "sensor_fail_low") == 0, "Precedence: fail-low over physical bounds");
}

void test_packet_builder_serialization() {
    SensorReadings readings;
    readings.temperature_c = 28.5f;
    readings.pressure_hpa = 1008.2f;
    readings.humidity_pct = 62.0f;
    readings.bmp_ok = true;
    readings.dht_ok = true;

    EdgeInferenceResult edge = run_edge_inference_c(readings.temperature_c, readings.pressure_hpa, readings.humidity_pct);

    char json_buf[1024];
    bool ok = build_observation_packet_json(
        "550e8400-e29b-41d4-a716-446655440000",
        "DELHI_CENTRAL",
        "esp32_c44f33112233",
        "2026-09-23T12:00:00Z",
        42,
        &readings,
        &edge,
        "esp32_edge_v1.0.0",
        -68.0f,
        json_buf,
        sizeof(json_buf)
    );

    TEST_ASSERT(ok == true, "build_observation_packet_json must return true");
    TEST_ASSERT(strstr(json_buf, "\"event_id\":\"550e8400-e29b-41d4-a716-446655440000\"") != NULL, "JSON event_id present");
    TEST_ASSERT(strstr(json_buf, "\"station_id\":\"DELHI_CENTRAL\"") != NULL, "JSON station_id present");
    TEST_ASSERT(strstr(json_buf, "\"device_id\":\"esp32_c44f33112233\"") != NULL, "JSON device_id present");
    TEST_ASSERT(strstr(json_buf, "\"sequence_number\":42") != NULL, "JSON sequence_number present");
    TEST_ASSERT(strstr(json_buf, "\"temperature_c\":28.50") != NULL, "JSON temperature_c present");
    TEST_ASSERT(strstr(json_buf, "\"pressure_hpa\":1008.20") != NULL, "JSON pressure_hpa present");
    TEST_ASSERT(strstr(json_buf, "\"humidity_pct\":62.00") != NULL, "JSON humidity_pct present");
    TEST_ASSERT(strstr(json_buf, "\"status\":\"ok\"") != NULL, "JSON status ok present");
    TEST_ASSERT(strstr(json_buf, "\"anomaly_type\":null") != NULL, "JSON anomaly_type null present");
    TEST_ASSERT(strstr(json_buf, "\"model_version\":\"edge_rules_v1.0.0\"") != NULL, "JSON model_version present");
    TEST_ASSERT(strstr(json_buf, "\"inference_method\":\"rules\"") != NULL, "JSON inference_method present");
    TEST_ASSERT(strstr(json_buf, "\"firmware_version\":\"esp32_edge_v1.0.0\"") != NULL, "JSON firmware_version present");
    TEST_ASSERT(strstr(json_buf, "\"signal_strength\":-68.0") != NULL, "JSON signal_strength present");

    // Test with nullable reading (e.g. NaN)
    readings.humidity_pct = NAN;
    EdgeInferenceResult edge_drop = run_edge_inference_c(readings.temperature_c, readings.pressure_hpa, readings.humidity_pct);
    ok = build_observation_packet_json(
        "550e8400-e29b-41d4-a716-446655440001",
        "DELHI_CENTRAL",
        "esp32_c44f33112233",
        "2026-09-23T12:00:05Z",
        43,
        &readings,
        &edge_drop,
        "esp32_edge_v1.0.0",
        -70.0f,
        json_buf,
        sizeof(json_buf)
    );
    TEST_ASSERT(ok == true, "build_observation_packet_json with NaN must return true");
    TEST_ASSERT(strstr(json_buf, "\"humidity_pct\":null") != NULL, "JSON humidity_pct null when NaN");
    TEST_ASSERT(strstr(json_buf, "\"status\":\"anomaly_detected\"") != NULL, "JSON status anomaly when NaN");
    TEST_ASSERT(strstr(json_buf, "\"anomaly_type\":\"dropout\"") != NULL, "JSON anomaly_type dropout when NaN");
}

int main() {
    printf("Running ESP32 Native C++ Core Unit Tests...\n");
    test_uuidv4_format();
    test_edge_engine_vectors();
    test_packet_builder_serialization();

    printf("Result: %d / %d assertions passed.\n", g_tests_passed, g_tests_run);
    if (g_tests_passed == g_tests_run) {
        printf("ALL C++ NATIVE TESTS PASSED (100%%).\n");
        return 0;
    } else {
        printf("SOME C++ NATIVE TESTS FAILED.\n");
        return 1;
    }
}
