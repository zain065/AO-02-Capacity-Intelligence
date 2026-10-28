"""
database.py - Lightweight SQLite Persistence Layer for AO-02 Capacity Intelligence.

Provides zero-dependency, local ACID persistence for:
- Facilities
- Telemetry records (with facility_id and asset_id support)
- Audit trail events
- Alert logs
- Capacity configurations

Ensures telemetry and imported files survive application restarts.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Default local database file path
DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "capacity_intelligence.db"
)


class DatabaseManager:
    """Manages SQLite connection lifecycle and schema operations."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # Enable WAL mode for high concurrency in local prototypes
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def init_db(self) -> None:
        """Creates required relational tables and indexes if they do not exist."""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # 1. Facilities
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS facilities (
                    facility_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    location TEXT,
                    tier TEXT DEFAULT 'Tier-III',
                    created_at TEXT NOT NULL
                );
            """)

            # 2. Assets (racks, clusters, storage pools, uplinks within a facility)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS assets (
                    asset_id TEXT NOT NULL,
                    facility_id TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    capacity REAL NOT NULL,
                    unit TEXT NOT NULL,
                    PRIMARY KEY (facility_id, asset_id),
                    FOREIGN KEY (facility_id) REFERENCES facilities (facility_id) ON DELETE CASCADE
                );
            """)

            # 3. Telemetry Records
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS telemetry_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    facility_id TEXT NOT NULL DEFAULT 'DC-NORTH-PRIMARY',
                    asset_id TEXT,
                    resource_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    value REAL NOT NULL,
                    capacity REAL NOT NULL,
                    unit TEXT NOT NULL,
                    data_source TEXT NOT NULL DEFAULT 'synthetic',
                    tenant_id TEXT DEFAULT 'aggregate',
                    workload_type TEXT,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL
                );
            """)

            # Indexes for ultra-fast time series querying
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_telemetry_res_time 
                ON telemetry_records (facility_id, resource_type, timestamp);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_telemetry_tenant 
                ON telemetry_records (facility_id, tenant_id, resource_type);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_telemetry_asset 
                ON telemetry_records (facility_id, asset_id, timestamp);
            """)
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_telemetry_dedup
                ON telemetry_records (facility_id, COALESCE(asset_id, ''), resource_type, COALESCE(tenant_id, ''), timestamp);
            """)

            # 4. Audit Trail Events
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    resource_type TEXT,
                    facility_id TEXT DEFAULT 'DC-NORTH-PRIMARY',
                    actor TEXT NOT NULL DEFAULT 'operator',
                    details TEXT NOT NULL
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_time 
                ON audit_events (timestamp DESC);
            """)

            # 5. Alert History
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS alert_history (
                    alert_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    facility_id TEXT NOT NULL DEFAULT 'DC-NORTH-PRIMARY',
                    resource_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    suggested_action TEXT NOT NULL
                );
            """)

            conn.commit()

    # -------------------------------------------------------------------------
    # Facilities Management
    # -------------------------------------------------------------------------

    def upsert_facility(self, facility_id: str, name: str, location: str = "Primary Campus", tier: str = "Tier-III") -> None:
        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO facilities (facility_id, name, location, tier, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(facility_id) DO UPDATE SET
                    name=excluded.name,
                    location=excluded.location,
                    tier=excluded.tier;
            """, (facility_id, name, location, tier, datetime.now(timezone.utc).isoformat()))
            conn.commit()

    def get_facilities(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM facilities ORDER BY facility_id ASC;").fetchall()
            return [dict(r) for r in rows]

    def has_facility(self, facility_id: str) -> bool:
        with self.get_connection() as conn:
            row = conn.execute("SELECT 1 FROM facilities WHERE facility_id = ? LIMIT 1;", (facility_id,)).fetchone()
            return row is not None

    # -------------------------------------------------------------------------
    # Assets Management
    # -------------------------------------------------------------------------

    def upsert_asset(self, facility_id: str, asset_id: str, resource_type: str, name: str, capacity: float, unit: str) -> None:
        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO assets (facility_id, asset_id, resource_type, name, capacity, unit)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(facility_id, asset_id) DO UPDATE SET
                    resource_type=excluded.resource_type,
                    name=excluded.name,
                    capacity=excluded.capacity,
                    unit=excluded.unit;
            """, (facility_id, asset_id, resource_type, name, capacity, unit))
            conn.commit()

    def get_assets(self, facility_id: str, resource_type: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            if resource_type:
                rows = conn.execute("""
                    SELECT * FROM assets WHERE facility_id = ? AND resource_type = ? ORDER BY asset_id ASC;
                """, (facility_id, resource_type)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM assets WHERE facility_id = ? ORDER BY asset_id ASC;
                """, (facility_id,)).fetchall()
            return [dict(r) for r in rows]

    # -------------------------------------------------------------------------
    # Telemetry Operations
    # -------------------------------------------------------------------------

    def insert_telemetry_batch(self, records: List[Dict[str, Any]]) -> int:
        """
        Inserts a batch of telemetry records. Ignores duplicates based on unique constraint.
        Returns the number of rows actually inserted.
        """
        if not records:
            return 0

        now_iso = datetime.now(timezone.utc).isoformat()
        rows_to_insert = []
        for r in records:
            ts = r["timestamp"]
            if isinstance(ts, datetime):
                ts = ts.isoformat()

            meta = r.get("metadata") or {}
            facility_id = r.get("facility_id") or meta.get("facility_id") or "DC-NORTH-PRIMARY"
            asset_id = r.get("asset_id") or meta.get("asset_id")

            rows_to_insert.append((
                facility_id,
                asset_id,
                str(r["resource_type"]).lower(),
                ts,
                float(r["value"]),
                float(r["capacity"]),
                str(r["unit"]),
                str(r.get("data_source", "synthetic")),
                str(r.get("tenant_id") or "aggregate"),
                r.get("workload_type"),
                json.dumps(meta) if meta else None,
                now_iso
            ))

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany("""
                INSERT OR IGNORE INTO telemetry_records (
                    facility_id, asset_id, resource_type, timestamp, value,
                    capacity, unit, data_source, tenant_id, workload_type, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, rows_to_insert)
            conn.commit()
            return cursor.rowcount

    def count_telemetry(self, facility_id: Optional[str] = None) -> int:
        with self.get_connection() as conn:
            if facility_id:
                row = conn.execute("SELECT COUNT(*) FROM telemetry_records WHERE facility_id = ?;", (facility_id,)).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM telemetry_records;").fetchone()
            return row[0] if row else 0

    def query_telemetry(
        self,
        resource_type: Optional[str] = None,
        facility_id: Optional[str] = "DC-NORTH-PRIMARY",
        asset_id: Optional[str] = None,
        tenant_id: Optional[str] = "aggregate",
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Queries telemetry records ordered chronologically with flexible filtering."""
        query = """
            SELECT id, facility_id, asset_id, resource_type, timestamp, value,
                   capacity, unit, data_source, tenant_id, workload_type, metadata_json
            FROM telemetry_records
            WHERE 1=1
        """
        params: List[Any] = []

        if resource_type is not None:
            query += " AND resource_type = ?"
            params.append(resource_type.lower())

        if facility_id is not None:
            query += " AND facility_id = ?"
            params.append(facility_id)

        if asset_id is not None:
            query += " AND asset_id = ?"
            params.append(asset_id)

        if tenant_id is not None:
            query += " AND tenant_id = ?"
            params.append(tenant_id)

        query += " ORDER BY timestamp ASC"

        if limit is not None:
            # If limited, we want the most recent N records in ascending order
            subquery = f"""
                SELECT * FROM ({query} DESC LIMIT ?) ORDER BY timestamp ASC
            """
            params.append(limit)
            query = subquery

        with self.get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            results = []
            for r in rows:
                item = dict(r)
                if item.get("metadata_json"):
                    try:
                        item["metadata"] = json.loads(item["metadata_json"])
                    except Exception:
                        item["metadata"] = {}
                else:
                    item["metadata"] = {}
                results.append(item)
            return results

    def clear_telemetry(self, facility_id: Optional[str] = None) -> None:
        with self.get_connection() as conn:
            if facility_id:
                conn.execute("DELETE FROM telemetry_records WHERE facility_id = ?;", (facility_id,))
            else:
                conn.execute("DELETE FROM telemetry_records;")
            conn.commit()

    # -------------------------------------------------------------------------
    # Audit Trail Operations
    # -------------------------------------------------------------------------

    def insert_audit_event(
        self,
        event_id: str,
        event_type: str,
        details: str,
        resource_type: Optional[str] = None,
        facility_id: str = "DC-NORTH-PRIMARY",
        actor: str = "operator",
        timestamp: Optional[str] = None
    ) -> None:
        ts = timestamp or datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO audit_events (event_id, timestamp, event_type, resource_type, facility_id, actor, details)
                VALUES (?, ?, ?, ?, ?, ?, ?);
            """, (event_id, ts, event_type, resource_type, facility_id, actor, details))
            conn.commit()

    def query_audit_events(self, limit: int = 50, facility_id: Optional[str] = None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM audit_events"
        params: List[Any] = []
        if facility_id:
            query += " WHERE facility_id = ?"
            params.append(facility_id)
        query += " ORDER BY timestamp DESC LIMIT ?;"
        params.append(limit)

        with self.get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]


# Global singleton instance
db_manager = DatabaseManager()
