"""
test_database.py - Verification tests for SQLite Persistence Layer.

Part of AO-02: Data Centre Capacity Intelligence.
Verifies that telemetry, facilities, assets, and audit logs persist reliably
and survive simulated server reboots.
"""

import os
import tempfile
import pytest
from datetime import datetime, timezone

from backend.database import DatabaseManager


@pytest.fixture
def temp_db():
    import uuid
    path = os.path.join(tempfile.gettempdir(), f"test_cap_intel_{uuid.uuid4().hex}.db")
    db = DatabaseManager(db_path=path)
    yield db
    try:
        if os.path.exists(path):
            os.remove(path)
        wal = f"{path}-wal"
        if os.path.exists(wal):
            os.remove(wal)
        shm = f"{path}-shm"
        if os.path.exists(shm):
            os.remove(shm)
    except PermissionError:
        pass


def test_database_initialization(temp_db):
    """Verifies that all required relational tables and indexes are created."""
    with temp_db.get_connection() as conn:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
        table_names = [r[0] for r in tables]
        assert "facilities" in table_names
        assert "assets" in table_names
        assert "telemetry_records" in table_names
        assert "audit_events" in table_names
        assert "alert_history" in table_names


def test_facility_upsert_and_query(temp_db):
    """Verifies facility registration and retrieval."""
    temp_db.upsert_facility("DC-WEST-1", "West Campus Primary", "Seattle, WA", "Tier-IV")
    temp_db.upsert_facility("DC-EAST-1", "East Campus Secondary", "New York, NY", "Tier-III")

    facs = temp_db.get_facilities()
    assert len(facs) == 2
    fac_ids = [f["facility_id"] for f in facs]
    assert "DC-WEST-1" in fac_ids
    assert "DC-EAST-1" in fac_ids


def test_asset_upsert_and_query(temp_db):
    """Verifies constrained asset registration and lookup within a facility."""
    temp_db.upsert_facility("DC-NORTH-PRIMARY", "North Campus", "Ashburn, VA")
    temp_db.upsert_asset("DC-NORTH-PRIMARY", "pool-alpha", "storage", "High-Speed NVMe Pool", 1000.0, "TB")
    temp_db.upsert_asset("DC-NORTH-PRIMARY", "pool-beta", "storage", "Standard Capacity Pool", 2000.0, "TB")

    assets = temp_db.get_assets("DC-NORTH-PRIMARY", resource_type="storage")
    assert len(assets) == 2
    assert assets[0]["asset_id"] == "pool-alpha"
    assert assets[0]["capacity"] == 1000.0


def test_telemetry_batch_insert_and_persistence_survival(temp_db):
    """
    Simulates application restart:
    1. Insert telemetry records into database.
    2. Close first connection and create a completely new DatabaseManager instance.
    3. Query new instance to prove records survived in the SQLite file.
    """
    records = [
        {
            "facility_id": "DC-NORTH-PRIMARY",
            "asset_id": "pool-alpha",
            "resource_type": "storage",
            "timestamp": "2026-10-28T12:00:00Z",
            "value": 850.0,
            "capacity": 1000.0,
            "unit": "TB",
            "data_source": "imported",
            "tenant_id": "aggregate",
            "workload_type": "storage_san",
            "metadata": {"batch": 1}
        },
        {
            "facility_id": "DC-NORTH-PRIMARY",
            "asset_id": "pool-alpha",
            "resource_type": "storage",
            "timestamp": "2026-10-28T13:00:00Z",
            "value": 870.0,
            "capacity": 1000.0,
            "unit": "TB",
            "data_source": "imported",
            "tenant_id": "aggregate",
            "workload_type": "storage_san",
            "metadata": {"batch": 1}
        }
    ]

    inserted = temp_db.insert_telemetry_batch(records)
    assert inserted == 2
    assert temp_db.count_telemetry("DC-NORTH-PRIMARY") == 2

    # Simulate restart: open same db path with new manager
    new_db_manager = DatabaseManager(db_path=temp_db.db_path)
    persisted_recs = new_db_manager.query_telemetry(
        resource_type="storage",
        facility_id="DC-NORTH-PRIMARY",
        asset_id="pool-alpha"
    )
    assert len(persisted_recs) == 2
    assert persisted_recs[0]["value"] == 850.0
    assert persisted_recs[1]["value"] == 870.0
    assert persisted_recs[0]["data_source"] == "imported"


def test_telemetry_deduplication(temp_db):
    """Verifies that duplicate records with identical facility, asset, resource, and timestamp are rejected."""
    rec = {
        "facility_id": "DC-NORTH-PRIMARY",
        "asset_id": "uplink-1",
        "resource_type": "network",
        "timestamp": "2026-10-28T14:00:00Z",
        "value": 45.0,
        "capacity": 100.0,
        "unit": "Gbps",
        "data_source": "synthetic",
        "tenant_id": "aggregate"
    }

    # First insert succeeds
    count1 = temp_db.insert_telemetry_batch([rec])
    assert count1 == 1

    # Second insert with exact duplicate is skipped
    count2 = temp_db.insert_telemetry_batch([rec])
    assert count2 == 0

    assert temp_db.count_telemetry("DC-NORTH-PRIMARY") == 1
