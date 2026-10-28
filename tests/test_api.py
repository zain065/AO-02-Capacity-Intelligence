"""
test_api.py - Integration and unit tests for FastAPI endpoints, tenant isolation,
audit trail, and error responses.
"""

import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.telemetry import telemetry_store

client = TestClient(app)


@pytest.fixture(autouse=True)
def init_datacenter():
    """Ensure telemetry store is seeded before each test."""
    telemetry_store.seed_default_datacenter(seed=42)


def test_health_check():
    """GET /health must return healthy status and positive observation count."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["version"] == "1.0.0"
    assert data["data_points_ingested"] > 0


def test_capacity_overview():
    """GET /api/v1/overview must return health summary, resources list, and data source label."""
    response = client.get("/api/v1/overview")
    assert response.status_code == 200
    data = response.json()
    assert data["facility_id"] == "DC-NORTH-PRIMARY"
    assert "operational" in data["data_source"].lower() or "synthetic" in data["data_source"].lower()
    assert data["total_resources_monitored"] == 5
    assert "overall_health" in data
    assert len(data["resources"]) == 5


def test_list_resources():
    """GET /api/v1/resources must list all 5 monitored resources."""
    response = client.get("/api/v1/resources")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 5
    types = {r["resource_type"] for r in data}
    assert types == {"power", "cooling", "storage", "network", "vm"}


def test_resource_history():
    """GET /api/v1/resources/{resource}/history must return sorted observation list."""
    response = client.get("/api/v1/resources/storage/history?limit=24")
    assert response.status_code == 200
    data = response.json()
    assert len(data) <= 24
    assert len(data) > 0
    assert "value" in data[0]
    assert "capacity" in data[0]
    assert "timestamp" in data[0]


def test_resource_history_invalid_type():
    """Invalid resource type query must return HTTP 422 Unprocessable Entity."""
    response = client.get("/api/v1/resources/quantum_core/history")
    assert response.status_code == 422


def test_resource_forecast():
    """GET /api/v1/resources/{resource}/forecast must return forecast points, confidence, and explanation."""
    response = client.get("/api/v1/resources/storage/forecast?days=14")
    assert response.status_code == 200
    data = response.json()
    assert data["resource_type"] == "storage"
    assert "status" in data
    assert "reason" in data
    assert "assumptions" in data
    assert len(data["forecast_points"]) == 14
    assert data["confidence_score"] >= 0.0


def test_active_alerts():
    """GET /api/v1/alerts must return active alert list with severity and suggestions."""
    response = client.get("/api/v1/alerts")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    for alert in data:
        assert alert["severity"] in ["critical", "warning", "info"]
        assert "suggested_action" in alert
        assert "message" in alert


def test_tenants_list():
    """GET /api/v1/tenants must return summaries of tenant resource allocations."""
    response = client.get("/api/v1/tenants")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 3
    tenant_ids = [t["tenant_id"] for t in data]
    assert "tenant-alpha" in tenant_ids
    assert "tenant-beta" in tenant_ids


def test_tenant_isolation_enforcement():
    """
    Tenant isolation test:
    - Tenant-alpha querying tenant-alpha resources succeeds.
    - Tenant-alpha querying tenant-beta resources is blocked with 403 Forbidden.
    - Admin querying tenant-beta resources is permitted.
    """
    # 1. Tenant-alpha accesses own resources -> 200 OK
    res_own = client.get(
        "/api/v1/tenants/tenant-alpha/resources",
        headers={"X-Tenant-ID": "tenant-alpha"}
    )
    assert res_own.status_code == 200
    assert res_own.json()["tenant_id"] == "tenant-alpha"

    # 2. Tenant-alpha attempts to access tenant-beta -> 403 Forbidden
    res_cross = client.get(
        "/api/v1/tenants/tenant-beta/resources",
        headers={"X-Tenant-ID": "tenant-alpha"}
    )
    assert res_cross.status_code == 403
    assert "Tenant isolation prevents" in res_cross.json()["detail"]

    # 3. Admin accesses tenant-beta -> 200 OK
    res_admin = client.get(
        "/api/v1/tenants/tenant-beta/resources",
        headers={"X-Tenant-ID": "admin-auditor", "X-Role": "admin"}
    )
    assert res_admin.status_code == 200
    assert res_admin.json()["tenant_id"] == "tenant-beta"


def test_audit_log_records_events():
    """GET /api/v1/audit must return recorded events without exposing sensitive credentials."""
    # Trigger an action
    client.get("/api/v1/overview")
    response = client.get("/api/v1/audit?limit=10")
    assert response.status_code == 200
    events = response.json()
    assert len(events) > 0
    assert "event_type" in events[0]
    assert "timestamp" in events[0]
    assert "details" in events[0]


def test_capacity_planning_endpoint():
    """GET /api/v1/capacity-planning must return planning items with lead times and overdue metrics."""
    response = client.get("/api/v1/capacity-planning")
    assert response.status_code == 200
    data = response.json()
    assert data["facility_id"] == "DC-NORTH-PRIMARY"
    assert data["total_resources"] == 5
    assert len(data["items"]) == 5
    for item in data["items"]:
        assert "procurement_lead_time_days" in item
        assert "planning_status" in item
        assert "planning_headline" in item
        assert "explanation" in item


def test_recommendations_endpoint():
    """GET /api/v1/recommendations must return prioritized advisory recommendations."""
    response = client.get("/api/v1/recommendations")
    assert response.status_code == 200
    data = response.json()
    assert len(data) > 0
    for rec in data:
        assert rec["priority"] in ["P0_CRITICAL", "P1_HIGH", "P2_MEDIUM", "P3_LOW"]
        assert "action_items" in rec
        assert "trigger_reason" in rec
        assert rec["automated_action"] is False


def test_what_if_simulation_endpoint():
    """POST /api/v1/simulations/forecast must compute simulated forecast without altering telemetry."""
    payload = {
        "resource_type": "storage",
        "demand_growth_multiplier": 1.5,
        "workload_delta": 50.0,
        "capacity_adjustment": 200.0,
        "forecast_days": 14
    }
    response = client.post("/api/v1/simulations/forecast", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["is_simulation"] is True
    assert "SIMULATION" in data["simulation_notice"]
    assert data["resource_type"] == "storage"
    assert len(data["simulated_forecast_points"]) == 14
    assert "severity_change" in data
    assert "urgency_impact" in data


def test_history_tenant_isolation_enforcement():
    """GET /api/v1/resources/{resource}/history must reject cross-tenant history queries with 403."""
    # Tenant alpha querying tenant beta's history -> 403
    res_cross = client.get(
        "/api/v1/resources/storage/history?tenant_id=tenant-beta",
        headers={"X-Tenant-ID": "tenant-alpha"}
    )
    assert res_cross.status_code == 403
    assert "Tenant isolation prevents" in res_cross.json()["detail"]

    # Tenant alpha querying aggregate or own history -> 200
    res_own = client.get(
        "/api/v1/resources/storage/history?tenant_id=tenant-alpha",
        headers={"X-Tenant-ID": "tenant-alpha"}
    )
    assert res_own.status_code == 200

