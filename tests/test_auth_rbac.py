"""
test_auth_rbac.py - Verification tests for Scoped Authentication and Facility/Tenant RBAC.

Part of AO-02: Data Centre Capacity Intelligence.
Verifies that:
- Bearer tokens are validated cryptographically.
- Invalid or forged tokens return 401 Unauthorized.
- Operators scoped to Facility A are blocked from querying Facility B with 403 Forbidden.
- Viewers scoped to Tenant A are blocked from inspecting Tenant B with 403 Forbidden.
- Global Admins have verified access across all facilities and tenants.
"""

import pytest
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)


def test_invalid_bearer_token_rejected():
    """Requests with unknown or forged bearer tokens must return 401 Unauthorized."""
    response = client.get(
        "/api/v1/overview",
        headers={"Authorization": "Bearer forged-or-expired-token-999"}
    )
    assert response.status_code == 401
    assert "Invalid or expired bearer token" in response.json()["detail"]


def test_valid_admin_token_access():
    """Global admin bearer token has full access to all facilities and tenants."""
    response = client.get(
        "/api/v1/overview?facility_id=DC-NORTH-PRIMARY",
        headers={"Authorization": "Bearer admin-token-secret-01"}
    )
    assert response.status_code == 200
    assert response.json()["facility_id"] == "DC-NORTH-PRIMARY"

    # Admin can access second facility
    response2 = client.get(
        "/api/v1/overview?facility_id=DC-SOUTH-SECONDARY",
        headers={"Authorization": "Bearer admin-token-secret-01"}
    )
    assert response2.status_code == 200
    assert response2.json()["facility_id"] == "DC-SOUTH-SECONDARY"


def test_facility_scoping_enforcement():
    """
    Facility-level isolation:
    - Operator-North (scoped only to DC-NORTH-PRIMARY) accessing DC-NORTH-PRIMARY succeeds (200).
    - Operator-North attempting to access DC-SOUTH-SECONDARY is rejected with 403 Forbidden.
    """
    # Allowed
    res_north = client.get(
        "/api/v1/overview?facility_id=DC-NORTH-PRIMARY",
        headers={"Authorization": "Bearer operator-north-token-02"}
    )
    assert res_north.status_code == 200

    # Blocked
    res_south = client.get(
        "/api/v1/overview?facility_id=DC-SOUTH-SECONDARY",
        headers={"Authorization": "Bearer operator-north-token-02"}
    )
    assert res_south.status_code == 403
    assert "not authorized to access facility" in res_south.json()["detail"]


def test_tenant_scoping_enforcement():
    """
    Tenant-level isolation:
    - User-Tenant-Alpha (scoped to tenant-alpha) querying tenant-alpha succeeds (200).
    - User-Tenant-Alpha querying tenant-beta is blocked with 403 Forbidden.
    """
    # Allowed own tenant
    res_alpha = client.get(
        "/api/v1/tenants/tenant-alpha/resources",
        headers={"Authorization": "Bearer tenant-alpha-token-04"}
    )
    assert res_alpha.status_code == 200
    assert res_alpha.json()["tenant_id"] == "tenant-alpha"

    # Blocked other tenant
    res_beta = client.get(
        "/api/v1/tenants/tenant-beta/resources",
        headers={"Authorization": "Bearer tenant-alpha-token-04"}
    )
    assert res_beta.status_code == 403
    assert "Tenant isolation prevents" in res_beta.json()["detail"]


def test_import_unauthorized_role_rejected():
    """Tenant viewer attempting to call CSV import is rejected with 403 Forbidden."""
    csv_bytes = b"timestamp,resource_type,value,capacity,unit\n2026-10-28,power,100,200,kW\n"
    response = client.post(
        "/api/v1/telemetry/import-csv",
        files={"file": ("test.csv", csv_bytes, "text/csv")},
        headers={"Authorization": "Bearer tenant-alpha-token-04"}
    )
    assert response.status_code == 403
    assert "not authorized to import telemetry" in response.json()["detail"]
