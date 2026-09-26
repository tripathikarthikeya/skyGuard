import argparse
import csv
import json
import time
import serial
import sys
from datetime import datetime

def parse_args():
    parser = argparse.ArgumentParser(description="Stream anomaly-injected CSV to ESP32 over USB Serial")
    parser.add_argument("--csv", required=True, help="Path to the input CSV file")
    parser.add_argument("--port", required=True, help="Serial COM port (e.g. COM5 or /dev/ttyUSB0)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument("--speed", type=float, default=1.0, help="Replay speed multiplier (default: 1.0)")
    return parser.parse_args()

def safe_float(val):
    if not val or val.strip().lower() in ["", "nan", "null"]:
        return None
    try:
        return float(val)
    except ValueError:
        return None

def main():
    args = parse_args()

    try:
        ser = serial.Serial(args.port, args.baud, timeout=1)
        print(f"Connected to {args.port} at {args.baud} baud.")
        # Allow ESP32 to reset
        time.sleep(2)
    except serial.SerialException as e:
        print(f"Error opening serial port: {e}")
        sys.exit(1)

    print(f"Opening CSV file: {args.csv}")
    try:
        with open(args.csv, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            last_timestamp = None
            
            for row in reader:
                # Parse timestamp to calculate realistic delay if needed, 
                # though simple scaled delay is also an option.
                current_time_str = row.get("timestamp")
                current_time = None
                if current_time_str:
                    try:
                        current_time = datetime.strptime(current_time_str, "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        pass
                
                # Determine delay based on timestamp and speed
                if last_timestamp and current_time:
                    delta_seconds = (current_time - last_timestamp).total_seconds()
                    if delta_seconds > 0:
                        sleep_time = delta_seconds / args.speed
                        # Limit sleep time so it doesn't hang forever on large gaps
                        sleep_time = min(sleep_time, 5.0 / args.speed)
                        time.sleep(sleep_time)
                else:
                    # Fallback if timestamps are missing or invalid
                    time.sleep(1.0 / args.speed)
                
                last_timestamp = current_time

                # Extract only necessary fields, explicitly isolating ground-truth
                payload = {
                    "timestamp": current_time_str,
                    "station_id": row.get("station_id"),
                    "temperature_c": safe_float(row.get("temperature_c")),
                    "pressure_hpa": safe_float(row.get("pressure_hpa")),
                    "humidity_pct": safe_float(row.get("humidity_pct"))
                }
                
                # Strip nulls to keep JSON small
                payload = {k: v for k, v in payload.items() if v is not None}
                
                json_str = json.dumps(payload)
                
                # Send over serial with newline
                ser.write((json_str + "\n").encode('utf-8'))
                print(f"Sent: {json_str}")
                
                # Read any response/debug output from ESP32
                while ser.in_waiting:
                    try:
                        esp_out = ser.readline().decode('utf-8').strip()
                        if esp_out:
                            print(f"[ESP32] {esp_out}")
                    except UnicodeDecodeError:
                        pass
                
    except FileNotFoundError:
        print(f"CSV file not found: {args.csv}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nStreaming stopped by user.")
    finally:
        ser.close()
        print("Serial port closed.")

if __name__ == "__main__":
    main()
