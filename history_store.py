"""
SkyGuard AI — history_store.py: persistent, long-horizon sensor history.

Supports dual-store architecture:
1. Primary: TimescaleDB (Tiger Cloud) hypertable `sensor_readings` with native time-series
   indexing, sub-millisecond range queries, and automatic 30-day retention policies.
2. Mirror & Fallback: Local append-only per-station CSV files under DATA_DIR ensuring
   100% offline resilience, instant local audits, and zero downtime if the cloud database
   is temporarily unreachable.
"""

import csv
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(__file__).parent / "data" / "history"
MAX_HISTORY_DAYS = 90
TRIM_CHECK_INTERVAL = 200

RAW_PARAMS = ["temperature_c", "pressure_hpa", "humidity_pct"]

HISTORY_COLUMNS = (
    ["timestamp", "station_id"]
    + RAW_PARAMS
    + ["is_anomaly", "fault_type", "severity", "anomaly_score_pct", "decision_basis"]
    + [f"suggested_{p}" for p in RAW_PARAMS]
    + ["health_status", "source", "model_confidence_pct", "rule_confidence_pct"]
)

HYBRID_COLUMNS = [
    # Layer 1: Raw Observation
    "event_id",
    "station_id",
    "device_id",
    "observed_at",
    "sequence_number",
    "temperature_c",
    "pressure_hpa",
    "humidity_pct",
    "firmware_version",
    "battery_voltage",
    "signal_strength",
    # Layer 2: Edge Inference
    "edge_status",
    "edge_anomaly_flag",
    "edge_anomaly_type",
    "edge_score",
    "edge_score_type",
    "edge_model_version",
    "edge_inference_method",
    # Layer 3: Central AI Inference
    "central_is_anomaly",
    "central_fault_type",
    "central_severity",
    "central_anomaly_score_pct",
    "central_model_confidence_pct",
    "central_rule_confidence_pct",
    "central_model_status",
    "central_decision_basis",
    "central_health_status",
    "central_suggested_temperature_c",
    "central_suggested_pressure_hpa",
    "central_suggested_humidity_pct",
    "source",
    # Layer 4: Edge <-> Central Comparison
    "comparison_status",
    "anomaly_decision_agreement",
    "type_agreement",
    "edge_score_val",
    "central_score_val",
    "details",
    # Audit Metadata
    "created_at",
]


class HistoryStore:
    """
    Dual-store architecture: TimescaleDB hypertable primary with local CSV mirror.
    Thread-safe per station with connection pooling for database operations.
    Supports durable persistence of the 4-layer hybrid observation lifecycle:
      1. Raw Sensor Observation (immutable, un-clamped)
      2. Edge Inference Result (Level 1)
      3. Central AI Inference Result (Level 2)
      4. Edge <-> Central Comparison (Diagnostic audit consensus)
    """

    def __init__(self, base_dir: Path = DATA_DIR, max_days: int = MAX_HISTORY_DAYS):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.max_days = max_days
        self._locks: dict[str, threading.Lock] = {}
        self._append_counts: dict[str, int] = {}

        # TimescaleDB pool initialization
        self.use_db = False
        self._db_pool = None
        self._init_timescale_pool()

    def _init_timescale_pool(self):
        db_url = os.environ.get("DATABASE_URL") or os.environ.get("TIMESCALE_SERVICE_URL")
        if not db_url:
            print("[HistoryStore] No DATABASE_URL/TIMESCALE_SERVICE_URL configured. Using local CSV store.")
            return

        try:
            import psycopg2
            from psycopg2.pool import ThreadedConnectionPool

            self._db_pool = ThreadedConnectionPool(minconn=1, maxconn=10, dsn=db_url)
            # Test connection and initialize schema
            conn = self._db_pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1;")
                self._init_schema(conn)
                conn.commit()
                self.use_db = True
                print("[HistoryStore] Connected to TimescaleDB (Tiger Cloud) successfully.")
            finally:
                self._db_pool.putconn(conn)
        except Exception as e:
            print(f"[HistoryStore] TimescaleDB connection failed: {e!r}. Operating in CSV fallback mode.")
            self.use_db = False
            self._db_pool = None

    def _init_schema(self, conn):
        """Idempotent, additive schema initialization for hybrid lifecycle tables."""
        ddl = """
        CREATE TABLE IF NOT EXISTS sensor_observations (
            event_id VARCHAR(128) PRIMARY KEY,
            station_id VARCHAR(64) NOT NULL,
            device_id VARCHAR(64) NOT NULL,
            observed_at TIMESTAMPTZ NOT NULL,
            sequence_number BIGINT NOT NULL,
            temperature_c DOUBLE PRECISION,
            pressure_hpa DOUBLE PRECISION,
            humidity_pct DOUBLE PRECISION,
            firmware_version VARCHAR(64),
            battery_voltage DOUBLE PRECISION,
            signal_strength DOUBLE PRECISION,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_sensor_obs_station_time ON sensor_observations (station_id, observed_at DESC);

        CREATE TABLE IF NOT EXISTS edge_inference_results (
            event_id VARCHAR(128) PRIMARY KEY,
            station_id VARCHAR(64) NOT NULL,
            device_id VARCHAR(64) NOT NULL,
            observed_at TIMESTAMPTZ NOT NULL,
            status VARCHAR(64) NOT NULL,
            anomaly_flag BOOLEAN NOT NULL,
            anomaly_type VARCHAR(64),
            score DOUBLE PRECISION,
            score_type VARCHAR(64),
            model_version VARCHAR(64) NOT NULL,
            inference_method VARCHAR(64) NOT NULL,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_edge_inf_station_time ON edge_inference_results (station_id, observed_at DESC);

        CREATE TABLE IF NOT EXISTS central_inference_results (
            event_id VARCHAR(128) PRIMARY KEY,
            station_id VARCHAR(64) NOT NULL,
            observed_at TIMESTAMPTZ NOT NULL,
            is_anomaly BOOLEAN NOT NULL,
            fault_type VARCHAR(64),
            severity VARCHAR(32),
            anomaly_score_pct DOUBLE PRECISION,
            model_confidence_pct DOUBLE PRECISION,
            rule_confidence_pct DOUBLE PRECISION,
            model_status VARCHAR(64),
            decision_basis VARCHAR(64),
            health_status VARCHAR(32),
            suggested_temperature_c DOUBLE PRECISION,
            suggested_pressure_hpa DOUBLE PRECISION,
            suggested_humidity_pct DOUBLE PRECISION,
            source VARCHAR(32),
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_central_inf_station_time ON central_inference_results (station_id, observed_at DESC);

        CREATE TABLE IF NOT EXISTS edge_central_comparisons (
            event_id VARCHAR(128) PRIMARY KEY,
            station_id VARCHAR(64) NOT NULL,
            device_id VARCHAR(64) NOT NULL,
            observed_at TIMESTAMPTZ NOT NULL,
            comparison_status VARCHAR(64) NOT NULL,
            edge_anomaly_flag BOOLEAN,
            central_anomaly_flag BOOLEAN,
            anomaly_decision_agreement BOOLEAN,
            edge_anomaly_type VARCHAR(64),
            central_anomaly_type VARCHAR(64),
            type_agreement BOOLEAN,
            edge_score DOUBLE PRECISION,
            edge_score_type VARCHAR(64),
            central_score DOUBLE PRECISION,
            central_score_type VARCHAR(64),
            details TEXT,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_edge_central_comp_station_time ON edge_central_comparisons (station_id, observed_at DESC);
        """
        with conn.cursor() as cur:
            cur.execute(ddl)

    @contextmanager
    def _get_db_conn(self):
        """Context manager to acquire and return pooled connections safely."""
        if not self.use_db or not self._db_pool:
            yield None
            return
        conn = None
        try:
            conn = self._db_pool.getconn()
            yield conn
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            print(f"[HistoryStore DB Error] {e!r}")
        finally:
            if conn and self._db_pool:
                try:
                    self._db_pool.putconn(conn)
                except Exception:
                    pass

    def _path(self, station_id: str) -> Path:
        return self.base_dir / f"{station_id}_history.csv"

    def _hybrid_path(self, station_id: str) -> Path:
        return self.base_dir / f"{station_id}_hybrid.csv"

    def _lock_for(self, station_id: str) -> threading.Lock:
        return self._locks.setdefault(station_id, threading.Lock())

    @staticmethod
    def _read_csv(path: Path) -> pd.DataFrame:
        """Read history with one canonical, timezone-aware timestamp type."""
        try:
            df = pd.read_csv(path, on_bad_lines="skip")
        except Exception:
            return pd.DataFrame(columns=HISTORY_COLUMNS)
        if df.empty or "timestamp" not in df.columns:
            return df
        for col in HISTORY_COLUMNS:
            if col not in df.columns:
                df[col] = None
        for str_col in ["fault_type", "severity", "health_status", "source", "decision_basis"]:
            if str_col in df.columns:
                df[str_col] = df[str_col].astype(object)
        parsed = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        if parsed.isna().any():
            df = df.loc[parsed.notna()].copy()
            parsed = parsed.loc[parsed.notna()]
        df["timestamp"] = parsed
        return df

    def append(self, station_id: str, timestamp, raw_reading: dict, verdict: dict, source: str):
        """
        Writes ONE row per ingested reading. Dual-writes to TimescaleDB and CSV mirror.
        """
        suggested = verdict.get("suggested_values", {}) or {}
        ts_obj = pd.Timestamp(timestamp)
        if ts_obj.tzinfo is None:
            ts_obj = ts_obj.tz_localize("UTC")
        ts_iso = ts_obj.isoformat()

        row = {
            "timestamp": ts_iso,
            "station_id": station_id,
            **{p: raw_reading.get(p) for p in RAW_PARAMS},
            "is_anomaly": bool(verdict.get("is_anomaly", False)),
            "fault_type": verdict.get("fault_type"),
            "severity": verdict.get("severity"),
            "anomaly_score_pct": verdict.get("anomaly_score_pct"),
            "decision_basis": verdict.get("decision_basis"),
            **{f"suggested_{p}": suggested.get(p) for p in RAW_PARAMS},
            "health_status": verdict.get("health_status"),
            "source": source,
            "model_confidence_pct": verdict.get("model_confidence_pct"),
            "rule_confidence_pct": verdict.get("rule_confidence_pct"),
        }

        # 1. TimescaleDB Write
        if self.use_db:
            try:
                with self._get_db_conn() as conn:
                    if conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                INSERT INTO sensor_readings (
                                    time, station_id, temperature_c, pressure_hpa, humidity_pct,
                                    is_anomaly, fault_type, severity, anomaly_score_pct,
                                    suggested_temperature_c, suggested_pressure_hpa, suggested_humidity_pct,
                                    health_status, source, decision_basis,
                                    model_confidence_pct, rule_confidence_pct
                                ) VALUES (
                                    %s, %s, %s, %s, %s,
                                    %s, %s, %s, %s,
                                    %s, %s, %s,
                                    %s, %s, %s,
                                    %s, %s
                                )
                                ON CONFLICT (station_id, time) DO UPDATE SET
                                    temperature_c = EXCLUDED.temperature_c,
                                    pressure_hpa = EXCLUDED.pressure_hpa,
                                    humidity_pct = EXCLUDED.humidity_pct,
                                    is_anomaly = EXCLUDED.is_anomaly,
                                    fault_type = EXCLUDED.fault_type,
                                    severity = EXCLUDED.severity,
                                    anomaly_score_pct = EXCLUDED.anomaly_score_pct,
                                    suggested_temperature_c = EXCLUDED.suggested_temperature_c,
                                    suggested_pressure_hpa = EXCLUDED.suggested_pressure_hpa,
                                    suggested_humidity_pct = EXCLUDED.suggested_humidity_pct,
                                    health_status = EXCLUDED.health_status,
                                    source = EXCLUDED.source,
                                    decision_basis = EXCLUDED.decision_basis,
                                    model_confidence_pct = EXCLUDED.model_confidence_pct,
                                    rule_confidence_pct = EXCLUDED.rule_confidence_pct;
                                """,
                                (
                                    ts_obj.to_pydatetime(),
                                    station_id,
                                    row["temperature_c"],
                                    row["pressure_hpa"],
                                    row["humidity_pct"],
                                    row["is_anomaly"],
                                    row["fault_type"],
                                    row["severity"],
                                    row["anomaly_score_pct"],
                                    row["suggested_temperature_c"],
                                    row["suggested_pressure_hpa"],
                                    row["suggested_humidity_pct"],
                                    row["health_status"],
                                    row["source"],
                                    row["decision_basis"],
                                    row["model_confidence_pct"],
                                    row["rule_confidence_pct"],
                                ),
                            )
                        conn.commit()
            except Exception as e:
                print(f"[HistoryStore] TimescaleDB insert error: {e!r}")

        # 2. Local CSV Mirror Write
        path = self._path(station_id)
        write_header = not path.exists()
        with self._lock_for(station_id):
            if path.exists():
                existing = pd.read_csv(path, usecols=["timestamp", "source"])
                duplicate = (
                    (existing["timestamp"].astype(str) == row["timestamp"])
                    & (existing["source"].astype(str) == source)
                )
                if duplicate.any():
                    return
            with open(path, "a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=HISTORY_COLUMNS)
                if write_header:
                    writer.writeheader()
                writer.writerow(row)

        count = self._append_counts.get(station_id, 0) + 1
        self._append_counts[station_id] = count
        if count % TRIM_CHECK_INTERVAL == 0:
            self.trim(station_id)

    def persist_hybrid_lifecycle(
        self,
        packet,
        verdict: Optional[dict] = None,
        comparison=None,
        source: str = "live",
    ) -> dict:
        """
        Persists the 4 distinct hybrid lifecycle layers independently and immutably:
          1. Original Raw Sensor Observation
          2. Level 1 Edge Inference
          3. Level 2 Central AI Verdict
          4. Edge <-> Central Diagnostic Comparison
        """
        ts_obj = pd.Timestamp(packet.observed_at)
        if ts_obj.tzinfo is None:
            ts_obj = ts_obj.tz_localize("UTC")
        obs_iso = ts_obj.isoformat()

        edge = getattr(packet, "edge_inference", None)
        suggested = (verdict.get("suggested_values", {}) or {}) if verdict else {}

        # 1. Dual-write to TimescaleDB / PostgreSQL if connected
        if self.use_db:
            try:
                with self._get_db_conn() as conn:
                    if conn:
                        with conn.cursor() as cur:
                            # 1a. Raw observation
                            cur.execute(
                                """
                                INSERT INTO sensor_observations (
                                    event_id, station_id, device_id, observed_at, sequence_number,
                                    temperature_c, pressure_hpa, humidity_pct,
                                    firmware_version, battery_voltage, signal_strength
                                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                ON CONFLICT (event_id) DO NOTHING;
                                """,
                                (
                                    packet.event_id,
                                    packet.station_id,
                                    packet.device_id,
                                    ts_obj.to_pydatetime(),
                                    packet.sequence_number,
                                    packet.readings.temperature_c,
                                    packet.readings.pressure_hpa,
                                    packet.readings.humidity_pct,
                                    packet.device_metadata.firmware_version,
                                    packet.device_metadata.battery_voltage,
                                    packet.device_metadata.signal_strength,
                                ),
                            )
                            # 1b. Edge inference
                            if edge is not None:
                                cur.execute(
                                    """
                                    INSERT INTO edge_inference_results (
                                        event_id, station_id, device_id, observed_at,
                                        status, anomaly_flag, anomaly_type, score, score_type,
                                        model_version, inference_method
                                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                    ON CONFLICT (event_id) DO NOTHING;
                                    """,
                                    (
                                        packet.event_id,
                                        packet.station_id,
                                        packet.device_id,
                                        ts_obj.to_pydatetime(),
                                        edge.status,
                                        edge.anomaly_flag,
                                        edge.anomaly_type,
                                        edge.score,
                                        edge.score_type,
                                        edge.model_version,
                                        edge.inference_method,
                                    ),
                                )
                            # 1c. Central inference
                            if verdict is not None:
                                cur.execute(
                                    """
                                    INSERT INTO central_inference_results (
                                        event_id, station_id, observed_at,
                                        is_anomaly, fault_type, severity, anomaly_score_pct,
                                        model_confidence_pct, rule_confidence_pct, model_status,
                                        decision_basis, health_status,
                                        suggested_temperature_c, suggested_pressure_hpa, suggested_humidity_pct,
                                        source
                                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                    ON CONFLICT (event_id) DO NOTHING;
                                    """,
                                    (
                                        packet.event_id,
                                        packet.station_id,
                                        ts_obj.to_pydatetime(),
                                        bool(verdict.get("is_anomaly", False)),
                                        verdict.get("fault_type"),
                                        verdict.get("severity"),
                                        verdict.get("anomaly_score_pct"),
                                        verdict.get("model_confidence_pct"),
                                        verdict.get("rule_confidence_pct"),
                                        verdict.get("model_status"),
                                        verdict.get("decision_basis"),
                                        verdict.get("health_status"),
                                        suggested.get("temperature_c"),
                                        suggested.get("pressure_hpa"),
                                        suggested.get("humidity_pct"),
                                        source,
                                    ),
                                )
                            # 1d. Diagnostic comparison
                            if comparison is not None:
                                cur.execute(
                                    """
                                    INSERT INTO edge_central_comparisons (
                                        event_id, station_id, device_id, observed_at,
                                        comparison_status, edge_anomaly_flag, central_anomaly_flag,
                                        anomaly_decision_agreement, edge_anomaly_type, central_anomaly_type,
                                        type_agreement, edge_score, edge_score_type,
                                        central_score, central_score_type, details
                                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                    ON CONFLICT (event_id) DO NOTHING;
                                    """,
                                    (
                                        packet.event_id,
                                        packet.station_id,
                                        packet.device_id,
                                        ts_obj.to_pydatetime(),
                                        comparison.comparison_status,
                                        comparison.edge_anomaly_flag,
                                        comparison.central_anomaly_flag,
                                        comparison.anomaly_decision_agreement,
                                        comparison.edge_anomaly_type,
                                        comparison.central_anomaly_type,
                                        comparison.type_agreement,
                                        comparison.edge_score,
                                        comparison.edge_score_type,
                                        comparison.central_score,
                                        comparison.central_score_type,
                                        comparison.details,
                                    ),
                                )
                        conn.commit()
            except Exception as e:
                print(f"[HistoryStore] DB persist_hybrid_lifecycle error: {e!r}")

        # 2. Local CSV mirror write
        hybrid_row = {
            "event_id": packet.event_id,
            "station_id": packet.station_id,
            "device_id": packet.device_id,
            "observed_at": obs_iso,
            "sequence_number": packet.sequence_number,
            "temperature_c": packet.readings.temperature_c,
            "pressure_hpa": packet.readings.pressure_hpa,
            "humidity_pct": packet.readings.humidity_pct,
            "firmware_version": packet.device_metadata.firmware_version,
            "battery_voltage": packet.device_metadata.battery_voltage,
            "signal_strength": packet.device_metadata.signal_strength,
            "edge_status": edge.status if edge else None,
            "edge_anomaly_flag": edge.anomaly_flag if edge else None,
            "edge_anomaly_type": edge.anomaly_type if edge else None,
            "edge_score": edge.score if edge else None,
            "edge_score_type": edge.score_type if edge else None,
            "edge_model_version": edge.model_version if edge else None,
            "edge_inference_method": edge.inference_method if edge else None,
            "central_is_anomaly": verdict.get("is_anomaly") if verdict else None,
            "central_fault_type": verdict.get("fault_type") if verdict else None,
            "central_severity": verdict.get("severity") if verdict else None,
            "central_anomaly_score_pct": verdict.get("anomaly_score_pct") if verdict else None,
            "central_model_confidence_pct": verdict.get("model_confidence_pct") if verdict else None,
            "central_rule_confidence_pct": verdict.get("rule_confidence_pct") if verdict else None,
            "central_model_status": verdict.get("model_status") if verdict else None,
            "central_decision_basis": verdict.get("decision_basis") if verdict else None,
            "central_health_status": verdict.get("health_status") if verdict else None,
            "central_suggested_temperature_c": suggested.get("temperature_c") if verdict else None,
            "central_suggested_pressure_hpa": suggested.get("pressure_hpa") if verdict else None,
            "central_suggested_humidity_pct": suggested.get("humidity_pct") if verdict else None,
            "source": source,
            "comparison_status": comparison.comparison_status if comparison else None,
            "anomaly_decision_agreement": comparison.anomaly_decision_agreement if comparison else None,
            "type_agreement": comparison.type_agreement if comparison else None,
            "edge_score_val": comparison.edge_score if comparison else None,
            "central_score_val": comparison.central_score if comparison else None,
            "details": comparison.details if comparison else None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        path = self._hybrid_path(packet.station_id)
        write_header = not path.exists()
        with self._lock_for(packet.station_id):
            if path.exists():
                try:
                    existing = pd.read_csv(path, usecols=["event_id"])
                    if (existing["event_id"].astype(str) == str(packet.event_id)).any():
                        return hybrid_row
                except Exception:
                    pass
            with open(path, "a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=HYBRID_COLUMNS)
                if write_header:
                    writer.writeheader()
                writer.writerow(hybrid_row)

        return hybrid_row

    def get_hybrid_record(self, event_id: str) -> Optional[dict]:
        """
        Reconstructs the full 4-layer hybrid observation lifecycle for one event_id.
        Returns:
          {
            "event_id": ...,
            "station_id": ...,
            "device_id": ...,
            "observed_at": datetime,
            "sequence_number": int,
            "raw_observation": {...},
            "edge_inference": {...} or None,
            "central_verdict": {...} or None,
            "comparison": {...} or None,
          }
        """
        # 1. DB Lookup
        if self.use_db:
            try:
                with self._get_db_conn() as conn:
                    if conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                SELECT
                                    o.event_id, o.station_id, o.device_id, o.observed_at, o.sequence_number,
                                    o.temperature_c, o.pressure_hpa, o.humidity_pct,
                                    o.firmware_version, o.battery_voltage, o.signal_strength,
                                    e.status AS edge_status, e.anomaly_flag AS edge_anomaly_flag,
                                    e.anomaly_type AS edge_anomaly_type, e.score AS edge_score,
                                    e.score_type AS edge_score_type, e.model_version AS edge_model_version,
                                    e.inference_method AS edge_inference_method,
                                    c.is_anomaly AS central_is_anomaly, c.fault_type AS central_fault_type,
                                    c.severity AS central_severity, c.anomaly_score_pct AS central_anomaly_score_pct,
                                    c.model_confidence_pct AS central_model_confidence_pct,
                                    c.rule_confidence_pct AS central_rule_confidence_pct,
                                    c.model_status AS central_model_status, c.decision_basis AS central_decision_basis,
                                    c.health_status AS central_health_status,
                                    c.suggested_temperature_c AS central_suggested_temperature_c,
                                    c.suggested_pressure_hpa AS central_suggested_pressure_hpa,
                                    c.suggested_humidity_pct AS central_suggested_humidity_pct,
                                    c.source AS source,
                                    comp.comparison_status AS comparison_status,
                                    comp.anomaly_decision_agreement AS anomaly_decision_agreement,
                                    comp.type_agreement AS type_agreement,
                                    comp.edge_score AS comp_edge_score,
                                    comp.edge_score_type AS comp_edge_score_type,
                                    comp.central_score AS comp_central_score,
                                    comp.central_score_type AS comp_central_score_type,
                                    comp.details AS details
                                FROM sensor_observations o
                                LEFT JOIN edge_inference_results e ON o.event_id = e.event_id
                                LEFT JOIN central_inference_results c ON o.event_id = c.event_id
                                LEFT JOIN edge_central_comparisons comp ON o.event_id = comp.event_id
                                WHERE o.event_id = %s;
                                """,
                                (event_id,),
                            )
                            row = cur.fetchone()
                            if row:
                                col_names = [desc[0] for desc in cur.description]
                                d = dict(zip(col_names, row))
                                return self._format_hybrid_dict(d)
            except Exception as e:
                print(f"[HistoryStore] DB get_hybrid_record failed: {e!r}")

        # 2. CSV Lookup
        for path in self.base_dir.glob("*_hybrid.csv"):
            try:
                df = pd.read_csv(path)
                match = df[df["event_id"].astype(str) == str(event_id)]
                if not match.empty:
                    d = match.iloc[0].to_dict()
                    return self._format_hybrid_dict(d)
            except Exception:
                continue

        return None

    def get_hybrid_history(
        self,
        station_id: str,
        hours: float = 24,
        source: Optional[str] = None,
    ) -> list[dict]:
        """
        Returns retained hybrid observations for one station as reconstructed lifecycle dicts.
        """
        # 1. DB Lookup
        if self.use_db:
            try:
                with self._get_db_conn() as conn:
                    if conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                "SELECT MAX(observed_at) FROM sensor_observations WHERE station_id = %s;",
                                (station_id,),
                            )
                            anchor = cur.fetchone()[0]
                            if anchor is not None:
                                cutoff = anchor - pd.Timedelta(hours=hours)
                                sql = """
                                SELECT
                                    o.event_id, o.station_id, o.device_id, o.observed_at, o.sequence_number,
                                    o.temperature_c, o.pressure_hpa, o.humidity_pct,
                                    o.firmware_version, o.battery_voltage, o.signal_strength,
                                    e.status AS edge_status, e.anomaly_flag AS edge_anomaly_flag,
                                    e.anomaly_type AS edge_anomaly_type, e.score AS edge_score,
                                    e.score_type AS edge_score_type, e.model_version AS edge_model_version,
                                    e.inference_method AS edge_inference_method,
                                    c.is_anomaly AS central_is_anomaly, c.fault_type AS central_fault_type,
                                    c.severity AS central_severity, c.anomaly_score_pct AS central_anomaly_score_pct,
                                    c.model_confidence_pct AS central_model_confidence_pct,
                                    c.rule_confidence_pct AS central_rule_confidence_pct,
                                    c.model_status AS central_model_status, c.decision_basis AS central_decision_basis,
                                    c.health_status AS central_health_status,
                                    c.suggested_temperature_c AS central_suggested_temperature_c,
                                    c.suggested_pressure_hpa AS central_suggested_pressure_hpa,
                                    c.suggested_humidity_pct AS central_suggested_humidity_pct,
                                    c.source AS source,
                                    comp.comparison_status AS comparison_status,
                                    comp.anomaly_decision_agreement AS anomaly_decision_agreement,
                                    comp.type_agreement AS type_agreement,
                                    comp.edge_score AS comp_edge_score,
                                    comp.edge_score_type AS comp_edge_score_type,
                                    comp.central_score AS comp_central_score,
                                    comp.central_score_type AS comp_central_score_type,
                                    comp.details AS details
                                FROM sensor_observations o
                                LEFT JOIN edge_inference_results e ON o.event_id = e.event_id
                                LEFT JOIN central_inference_results c ON o.event_id = c.event_id
                                LEFT JOIN edge_central_comparisons comp ON o.event_id = comp.event_id
                                WHERE o.station_id = %s AND o.observed_at >= %s
                                """
                                params = [station_id, cutoff]
                                if source:
                                    sql += " AND c.source = %s"
                                    params.append(source)
                                sql += " ORDER BY o.observed_at ASC;"
                                cur.execute(sql, tuple(params))
                                rows = cur.fetchall()
                                if rows:
                                    col_names = [desc[0] for desc in cur.description]
                                    return [
                                        self._format_hybrid_dict(dict(zip(col_names, r)))
                                        for r in rows
                                    ]
            except Exception as e:
                print(f"[HistoryStore] DB get_hybrid_history failed: {e!r}")

        # 2. CSV Lookup
        path = self._hybrid_path(station_id)
        if not path.exists():
            return []
        try:
            df = pd.read_csv(path)
            if df.empty or "observed_at" not in df.columns:
                return []
            df["observed_at_dt"] = pd.to_datetime(df["observed_at"], utc=True, errors="coerce")
            df = df.dropna(subset=["observed_at_dt"])
            if df.empty:
                return []
            if source is not None and "source" in df.columns:
                df = df[df["source"] == source]
            anchor = df["observed_at_dt"].max()
            cutoff = anchor - pd.Timedelta(hours=hours)
            filtered = df[df["observed_at_dt"] >= cutoff].sort_values("observed_at_dt")
            return [
                self._format_hybrid_dict(row.to_dict())
                for _, row in filtered.iterrows()
            ]
        except Exception:
            return []

    def _format_hybrid_dict(self, d: dict) -> dict:
        """Helper to structure raw dict / row into the canonical 4-layer representation."""
        def _clean_val(v):
            if v is None or pd.isna(v):
                return None
            return v

        def _clean_bool(v):
            if v is None or pd.isna(v):
                return None
            if isinstance(v, bool):
                return v
            if isinstance(v, (int, float)):
                return bool(v)
            if str(v).lower() in ("true", "1"):
                return True
            if str(v).lower() in ("false", "0"):
                return False
            return None

        def _clean_float(v):
            if v is None or pd.isna(v):
                return None
            try:
                return float(v)
            except (ValueError, TypeError):
                return None

        def _clean_int(v):
            if v is None or pd.isna(v):
                return None
            try:
                return int(v)
            except (ValueError, TypeError):
                return None

        event_id = str(d.get("event_id"))
        station_id = str(d.get("station_id"))
        device_id = str(d.get("device_id"))
        obs_at = pd.to_datetime(d.get("observed_at"), utc=True).to_pydatetime() if d.get("observed_at") else None
        seq_num = _clean_int(d.get("sequence_number"))

        raw_obs = {
            "temperature_c": _clean_float(d.get("temperature_c")),
            "pressure_hpa": _clean_float(d.get("pressure_hpa")),
            "humidity_pct": _clean_float(d.get("humidity_pct")),
            "firmware_version": _clean_val(d.get("firmware_version")),
            "battery_voltage": _clean_float(d.get("battery_voltage")),
            "signal_strength": _clean_float(d.get("signal_strength")),
        }

        edge_status = _clean_val(d.get("edge_status"))
        edge_inf = None
        if edge_status is not None:
            edge_inf = {
                "status": edge_status,
                "anomaly_flag": _clean_bool(d.get("edge_anomaly_flag")),
                "anomaly_type": _clean_val(d.get("edge_anomaly_type")),
                "score": _clean_float(d.get("edge_score") if d.get("edge_score") is not None else d.get("edge_score_val")),
                "score_type": _clean_val(d.get("edge_score_type")),
                "model_version": _clean_val(d.get("edge_model_version")),
                "inference_method": _clean_val(d.get("edge_inference_method")),
            }

        central_is_anom = _clean_bool(d.get("central_is_anomaly"))
        central_score = _clean_float(d.get("central_anomaly_score_pct"))
        central_verdict = None
        if central_is_anom is not None or central_score is not None:
            central_verdict = {
                "is_anomaly": central_is_anom if central_is_anom is not None else False,
                "fault_type": _clean_val(d.get("central_fault_type")),
                "severity": _clean_val(d.get("central_severity")),
                "anomaly_score_pct": central_score,
                "model_confidence_pct": _clean_float(d.get("central_model_confidence_pct")),
                "rule_confidence_pct": _clean_float(d.get("central_rule_confidence_pct")),
                "model_status": _clean_val(d.get("central_model_status")),
                "decision_basis": _clean_val(d.get("central_decision_basis")),
                "health_status": _clean_val(d.get("central_health_status")),
                "suggested_values": {
                    "temperature_c": _clean_float(d.get("central_suggested_temperature_c")),
                    "pressure_hpa": _clean_float(d.get("central_suggested_pressure_hpa")),
                    "humidity_pct": _clean_float(d.get("central_suggested_humidity_pct")),
                },
                "source": _clean_val(d.get("source")),
            }

        comp_status = _clean_val(d.get("comparison_status"))
        comp = None
        if comp_status is not None:
            comp = {
                "comparison_status": comp_status,
                "edge_anomaly_flag": _clean_bool(d.get("edge_anomaly_flag")),
                "central_anomaly_flag": central_is_anom,
                "anomaly_decision_agreement": _clean_bool(d.get("anomaly_decision_agreement")),
                "edge_anomaly_type": _clean_val(d.get("edge_anomaly_type")),
                "central_anomaly_type": _clean_val(d.get("central_fault_type")),
                "type_agreement": _clean_bool(d.get("type_agreement")),
                "edge_score": _clean_float(d.get("edge_score") if d.get("edge_score") is not None else d.get("edge_score_val")),
                "edge_score_type": _clean_val(d.get("edge_score_type")),
                "central_score": _clean_float(d.get("central_anomaly_score_pct") if d.get("central_anomaly_score_pct") is not None else d.get("central_score_val")),
                "central_score_type": "percentage",
                "details": _clean_val(d.get("details")),
            }

        return {
            "event_id": event_id,
            "station_id": station_id,
            "device_id": device_id,
            "observed_at": obs_at,
            "sequence_number": seq_num,
            "raw_observation": raw_obs,
            "edge_inference": edge_inf,
            "central_verdict": central_verdict,
            "comparison": comp,
        }

    def clear_hybrid_history(self, station_id: Optional[str] = None, source: Optional[str] = None):
        """Purges hybrid lifecycle records from DB and CSV."""
        if self.use_db:
            try:
                with self._get_db_conn() as conn:
                    if conn:
                        with conn.cursor() as cur:
                            if station_id is None and source is None:
                                cur.execute("TRUNCATE TABLE edge_central_comparisons;")
                                cur.execute("TRUNCATE TABLE central_inference_results;")
                                cur.execute("TRUNCATE TABLE edge_inference_results;")
                                cur.execute("TRUNCATE TABLE sensor_observations;")
                            elif station_id is not None and source is not None:
                                cur.execute(
                                    "DELETE FROM central_inference_results WHERE station_id = %s AND source = %s;",
                                    (station_id, source),
                                )
                            elif station_id is not None:
                                cur.execute("DELETE FROM edge_central_comparisons WHERE station_id = %s;", (station_id,))
                                cur.execute("DELETE FROM central_inference_results WHERE station_id = %s;", (station_id,))
                                cur.execute("DELETE FROM edge_inference_results WHERE station_id = %s;", (station_id,))
                                cur.execute("DELETE FROM sensor_observations WHERE station_id = %s;", (station_id,))
                        conn.commit()
            except Exception as e:
                print(f"[HistoryStore] DB clear_hybrid_history error: {e!r}")

        # CSV mirror clear
        pattern = f"{station_id}_hybrid.csv" if station_id else "*_hybrid.csv"
        for path in self.base_dir.glob(pattern):
            sid = path.stem[: -len("_hybrid")] if path.stem.endswith("_hybrid") else path.stem
            with self._lock_for(sid):
                if source is None:
                    path.unlink(missing_ok=True)
                else:
                    df = pd.read_csv(path)
                    if not df.empty and "source" in df.columns:
                        remaining = df[df["source"] != source]
                        if remaining.empty:
                            path.unlink(missing_ok=True)
                        else:
                            remaining.to_csv(path, index=False)

    def log_health_transition(self, station_id: str, timestamp, old_state: str, new_state: str, reason: str):
        ts_obj = pd.Timestamp(timestamp)
        if ts_obj.tzinfo is None:
            ts_obj = ts_obj.tz_localize("UTC")

        # Database write
        if self.use_db:
            try:
                with self._get_db_conn() as conn:
                    if conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                INSERT INTO station_health_events (time, station_id, old_state, new_state, reason)
                                VALUES (%s, %s, %s, %s, %s);
                                """,
                                (ts_obj.to_pydatetime(), station_id, old_state, new_state, reason),
                            )
                        conn.commit()
            except Exception as e:
                print(f"[HistoryStore] Failed to log health transition to DB: {e!r}")

        # CSV mirror write
        path = self.base_dir / f"{station_id}_health_events.csv"
        write_header = not path.exists()
        row = {
            "timestamp": ts_obj.isoformat(),
            "station_id": station_id,
            "old_state": old_state,
            "new_state": new_state,
            "reason": reason,
        }
        with self._lock_for(station_id):
            with open(path, "a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["timestamp", "station_id", "old_state", "new_state", "reason"])
                if write_header:
                    writer.writeheader()
                writer.writerow(row)

    def trim(self, station_id: str):
        """
        Enforces MAX_HISTORY_DAYS independently for each source.
        For TimescaleDB, hypertable retention policy handles auto-drop of old chunks.
        For local CSVs, trims rows older than max_days.
        """
        path = self._path(station_id)
        if not path.exists():
            return
        with self._lock_for(station_id):
            df = self._read_csv(path)
            if df.empty:
                return
            if "source" not in df.columns:
                cutoff = df["timestamp"].max() - pd.Timedelta(days=self.max_days)
                trimmed = df[df["timestamp"] >= cutoff]
            else:
                retained = []
                for _, source_rows in df.groupby("source", dropna=False):
                    cutoff = source_rows["timestamp"].max() - pd.Timedelta(days=self.max_days)
                    retained.append(source_rows[source_rows["timestamp"] >= cutoff])
                trimmed = pd.concat(retained, ignore_index=True) if retained else df.iloc[0:0]
            if len(trimmed) < len(df):
                trimmed.to_csv(path, index=False)

    def mark_spike(self, station_id: str, timestamp, parameter: str, suggested_value: float, source: str):
        """Retroactively annotate the original one-reading spike in both DB and CSV."""
        ts_obj = pd.Timestamp(timestamp)
        if ts_obj.tzinfo is None:
            ts_obj = ts_obj.tz_localize("UTC")

        # Database update
        if self.use_db and parameter in RAW_PARAMS:
            try:
                col_name = f"suggested_{parameter}"
                with self._get_db_conn() as conn:
                    if conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                f"""
                                UPDATE sensor_readings
                                SET is_anomaly = TRUE, fault_type = 'spike', severity = 'medium', {col_name} = %s
                                WHERE station_id = %s AND time = %s AND source = %s;
                                """,
                                (suggested_value, station_id, ts_obj.to_pydatetime(), source),
                            )
                        conn.commit()
            except Exception as e:
                print(f"[HistoryStore] DB mark_spike error: {e!r}")

        # CSV update
        path = self._path(station_id)
        if not path.exists():
            return
        target = pd.to_datetime(timestamp, utc=True)
        with self._lock_for(station_id):
            df = self._read_csv(path)
            mask = (df["timestamp"] == target) & (df["source"] == source)
            if not mask.any():
                return
            df["is_anomaly"] = df["is_anomaly"].astype(object)
            df["fault_type"] = df["fault_type"].astype(object)
            df["severity"] = df["severity"].astype(object)
            df.loc[mask, "is_anomaly"] = True
            df.loc[mask, "fault_type"] = "spike"
            df.loc[mask, "severity"] = "medium"
            df.loc[mask, f"suggested_{parameter}"] = suggested_value
            df.to_csv(path, index=False)

    def get_recent(
        self,
        station_id: str,
        hours: float = 24,
        relative_to: str = "latest",
        source: str | None = None,
    ) -> pd.DataFrame:
        """
        Returns retained rows for one station as a DataFrame matching HISTORY_COLUMNS.
        Queries TimescaleDB hypertable first; seamlessly falls back to CSV mirror if offline.
        """
        if self.use_db:
            try:
                with self._get_db_conn() as conn:
                    if conn:
                        with conn.cursor() as cur:
                            # 1. Determine anchor timestamp
                            if relative_to == "latest":
                                if source:
                                    cur.execute(
                                        "SELECT MAX(time) FROM sensor_readings WHERE station_id = %s AND source = %s;",
                                        (station_id, source),
                                    )
                                else:
                                    cur.execute(
                                        "SELECT MAX(time) FROM sensor_readings WHERE station_id = %s;",
                                        (station_id,),
                                    )
                                anchor = cur.fetchone()[0]
                            else:
                                anchor = datetime.now(timezone.utc)

                            if anchor is not None:
                                cutoff = anchor - pd.Timedelta(hours=hours)
                                if source:
                                    cur.execute(
                                        """
                                        SELECT
                                            time as timestamp,
                                            station_id,
                                            temperature_c,
                                            pressure_hpa,
                                            humidity_pct,
                                            is_anomaly,
                                            fault_type,
                                            severity,
                                            anomaly_score_pct,
                                            decision_basis,
                                            suggested_temperature_c,
                                            suggested_pressure_hpa,
                                            suggested_humidity_pct,
                                            health_status,
                                            source,
                                            model_confidence_pct,
                                            rule_confidence_pct
                                        FROM sensor_readings
                                        WHERE station_id = %s AND source = %s AND time >= %s
                                        ORDER BY time ASC;
                                        """,
                                        (station_id, source, cutoff),
                                    )
                                else:
                                    cur.execute(
                                        """
                                        SELECT
                                            time as timestamp,
                                            station_id,
                                            temperature_c,
                                            pressure_hpa,
                                            humidity_pct,
                                            is_anomaly,
                                            fault_type,
                                            severity,
                                            anomaly_score_pct,
                                            decision_basis,
                                            suggested_temperature_c,
                                            suggested_pressure_hpa,
                                            suggested_humidity_pct,
                                            health_status,
                                            source,
                                            model_confidence_pct,
                                            rule_confidence_pct
                                        FROM sensor_readings
                                        WHERE station_id = %s AND time >= %s
                                        ORDER BY time ASC;
                                        """,
                                        (station_id, cutoff),
                                    )
                                rows = cur.fetchall()
                                if rows:
                                    colnames = [desc[0] for desc in cur.description]
                                    df = pd.DataFrame(rows, columns=colnames)
                                    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
                                    return df
            except Exception as e:
                print(f"[HistoryStore] DB get_recent query failed: {e!r}. Using CSV mirror.")

        # Fallback to CSV
        path = self._path(station_id)
        if not path.exists():
            return pd.DataFrame(columns=HISTORY_COLUMNS)
        df = self._read_csv(path)
        if source is not None and "source" in df.columns:
            df = df[df["source"] == source]
        if df.empty:
            return df
        anchor = df["timestamp"].max() if relative_to == "latest" else pd.Timestamp.now(tz="UTC")
        cutoff = anchor - pd.Timedelta(hours=hours)
        return df[df["timestamp"] >= cutoff].reset_index(drop=True)

    def get_all(self, station_id: str) -> pd.DataFrame:
        """Full retained window for one station."""
        if self.use_db:
            try:
                with self._get_db_conn() as conn:
                    if conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                SELECT
                                    time as timestamp,
                                    station_id,
                                    temperature_c,
                                    pressure_hpa,
                                    humidity_pct,
                                    is_anomaly,
                                    fault_type,
                                    severity,
                                    anomaly_score_pct,
                                    decision_basis,
                                    suggested_temperature_c,
                                    suggested_pressure_hpa,
                                    suggested_humidity_pct,
                                    health_status,
                                    source,
                                    model_confidence_pct,
                                    rule_confidence_pct
                                FROM sensor_readings
                                WHERE station_id = %s
                                ORDER BY time ASC;
                                """,
                                (station_id,),
                            )
                            rows = cur.fetchall()
                            if rows:
                                colnames = [desc[0] for desc in cur.description]
                                df = pd.DataFrame(rows, columns=colnames)
                                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
                                return df
            except Exception as e:
                print(f"[HistoryStore] DB get_all failed: {e!r}. Using CSV fallback.")

        path = self._path(station_id)
        if not path.exists():
            return pd.DataFrame(columns=HISTORY_COLUMNS)
        return self._read_csv(path)

    def clear_source(self, station_id: str, source: str, sync_db: bool = True):
        """Purges every row tagged with source in DB and CSV."""
        if sync_db and self.use_db:
            try:
                with self._get_db_conn() as conn:
                    if conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                "DELETE FROM sensor_readings WHERE station_id = %s AND source = %s;",
                                (station_id, source),
                            )
                        conn.commit()
            except Exception as e:
                print(f"[HistoryStore] DB clear_source error: {e!r}")

        path = self._path(station_id)
        if path.exists():
            with self._lock_for(station_id):
                df = self._read_csv(path)
                if not df.empty and "source" in df.columns:
                    remaining = df[df["source"] != source]
                    if len(remaining) < len(df):
                        if remaining.empty:
                            path.unlink()
                        else:
                            remaining.to_csv(path, index=False)

        self.clear_hybrid_history(station_id=station_id, source=source)

    def clear_all(self, source: str = None):
        """Purges source rows or truncates the store across all stations."""
        if self.use_db:
            try:
                with self._get_db_conn() as conn:
                    if conn:
                        with conn.cursor() as cur:
                            if source is None:
                                cur.execute("TRUNCATE TABLE sensor_readings;")
                            else:
                                cur.execute("DELETE FROM sensor_readings WHERE source = %s;", (source,))
                        conn.commit()
            except Exception as e:
                print(f"[HistoryStore] DB clear_all error: {e!r}")

        for path in self.base_dir.glob("*_history.csv"):
            station_id = path.stem[: -len("_history")] if path.stem.endswith("_history") else path.stem
            if source is None:
                with self._lock_for(station_id):
                    path.unlink(missing_ok=True)
            else:
                self.clear_source(station_id, source, sync_db=False)

        self.clear_hybrid_history(source=source)
