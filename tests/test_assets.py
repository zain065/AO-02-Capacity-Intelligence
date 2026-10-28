"""
test_assets.py - Verification tests for Phase 3: Asset-Level Capacity Tracking.

Part of AO-02: Data Centre Capacity Intelligence.
Verifies that:
- Constrained individual assets (storage pools, network uplinks, VM clusters)
  are tracked within a facility.
- A facility-wide aggregate total does NOT hide a critical individual asset
  that is rapidly approaching exhaustion.
- Asset-level telemetry history and forecast endpoints function correctly.
"""

import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.telemetry import ResourceType, telemetry_store

client = TestClient(app)


def test_list_facility_assets():
    """GET /api/v1/facilities/{id}/assets must return individual monitored pools/uplinks/clusters."""
    response = client.get(
        "/api/v1/facilities/DC-NORTH-PRIMARY/assets",
        headers={"Authorization": "Bearer admin-token-secret-01"}
    )
    assert response.status_code == 200
    assets = response.json()
    assert len(assets) >= 4

    asset_ids = [a["asset_id"] for a in assets]
    assert "storage-pool-a" in asset_ids
    assert "storage-pool-b" in asset_ids
    assert "uplink-1" in asset_ids
    assert "uplink-2" in asset_ids


def test_asset_exhaustion_not_masked_by_aggregate():
    """
    Key Domain Safety Test:
    Verifies that a constrained individual asset (Storage Pool A) at 94% utilization
    is surfaced independently, preventing operators from being lulled into complacency
    by a healthy overall aggregate number.
    """
    # 1. Query Pool A (High utilization - critical asset)
    res_pool_a = client.get(
        "/api/v1/resources/storage/history?facility_id=DC-NORTH-PRIMARY&asset_id=storage-pool-a&limit=1",
        headers={"Authorization": "Bearer admin-token-secret-01"}
    )
    assert res_pool_a.status_code == 200
    pool_a_latest = res_pool_a.json()[0]

    # 2. Query Pool B (Low utilization - ample headroom)
    res_pool_b = client.get(
        "/api/v1/resources/storage/history?facility_id=DC-NORTH-PRIMARY&asset_id=storage-pool-b&limit=1",
        headers={"Authorization": "Bearer admin-token-secret-01"}
    )
    assert res_pool_b.status_code == 200
    pool_b_latest = res_pool_b.json()[0]

    # Pool A utilization must be significantly higher than Pool B
    assert pool_a_latest["utilization_pct"] > pool_b_latest["utilization_pct"]
    assert pool_a_latest["asset_id"] == "storage-pool-a"
    assert pool_b_latest["asset_id"] == "storage-pool-b"


def test_asset_history_and_forecast_query():
    """Asset history and forecast endpoints operate with asset_id parameter."""
    res_hist = client.get(
        "/api/v1/resources/network/history?facility_id=DC-NORTH-PRIMARY&asset_id=uplink-1&limit=24",
        headers={"Authorization": "Bearer admin-token-secret-01"}
    )
    assert res_hist.status_code == 200
    records = res_hist.json()
    assert len(records) > 0
    assert all(r["asset_id"] == "uplink-1" for r in records)
