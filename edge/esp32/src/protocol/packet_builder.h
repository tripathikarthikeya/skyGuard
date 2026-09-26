#pragma once

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include "../edge/edge_engine.h"
#include "../sensors/sensor_manager.h"

#ifdef __cplusplus
extern "C" {
#endif

// Generates a valid UUIDv4 string into out_uuid (buffer must be >= 37 bytes)
void generate_uuid_v4(char* out_uuid);

// Serializes a canonical ObservationPacket JSON string into out_json buffer
// Returns true on success, false if buffer is too small
bool build_observation_packet_json(
    const char* event_id,
    const char* station_id,
    const char* device_id,
    const char* observed_at_iso8601,
    uint32_t sequence_number,
    const SensorReadings* readings,
    const EdgeInferenceResult* edge_inference,
    const char* firmware_version,
    float signal_strength_rssi,
    char* out_json,
    size_t max_out_len
);

#ifdef __cplusplus
}
#endif
