"""
test_telemetry.py - Unit tests for telemetry validation, ingestion, and deterministic generation.
"""

import pytest
from datetime import datetime, timezone
import math
from backend.telemetry import (
    ResourceType,
    RESOURCE_UNITS,
    DEFAULT_CAPACITIES,
    TelemetryRecord,
    ValidationError,
    validate_raw_record,
    generate_synthetic_series,
    generate_tenant_breakdown,
    TelemetryStore,
)


def test_valid_telemetry_record():
    """Verify standard valid telemetry record creation and utilization percentage."""
    now = datetime.now(timezone.utc)
    rec = TelemetryRecord(
        timestamp=now,
        resource_type=ResourceType.POWER,
        value=350.0,
        capacity=500.0,
        unit="kW",
        data_source="synthetic",
        tenant_id="tenant-alpha"
    )
    assert rec.resource_type == ResourceType.POWER
    assert rec.value == 350.0
    assert rec.capacity == 500.0
    assert rec.utilization_pct == 70.0
    assert rec.data_source == "synthetic"
    assert rec.tenant_id == "tenant-alpha"


def test_reject_nan_or_inf_value():
    """Verify NaN and Inf measurements are rejected."""
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="Value must be a finite numerical measurement"):
        TelemetryRecord(
            timestamp=now,
            resource_type=ResourceType.STORAGE,
            value=float("nan"),
            capacity=1000.0,
            unit="TB"
        )

    with pytest.raises(ValueError, match="Value must be a finite numerical measurement"):
        TelemetryRecord(
            timestamp=now,
            resource_type=ResourceType.STORAGE,
            value=float("inf"),
            capacity=1000.0,
            unit="TB"
        )


def test_reject_invalid_capacity():
    """Verify zero or negative capacity is rejected."""
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="Capacity must be a positive number greater than 0"):
        TelemetryRecord(
            timestamp=now,
            resource_type=ResourceType.NETWORK,
            value=10.0,
            capacity=0.0,
            unit="Gbps"
        )

    with pytest.raises(ValueError, match="Capacity must be a positive number greater than 0"):
        TelemetryRecord(
            timestamp=now,
            resource_type=ResourceType.NETWORK,
            value=10.0,
            capacity=-50.0,
            unit="Gbps"
        )


def test_validate_raw_record_success():
    """Test validating raw dictionary ingestion."""
    raw = {
        "timestamp": "2026-10-28T12:00:00Z",
        "resource_type": "storage",
        "value": 1400.5,
        "capacity": 2000.0,
        "unit": "TB",
        "data_source": "synthetic"
    }
    rec = validate_raw_record(raw)
    assert rec.resource_type == ResourceType.STORAGE
    assert rec.value == 1400.5
    assert rec.capacity == 2000.0
    assert math.isclose(rec.utilization_pct, 70.025, abs_tol=0.01)


def test_validate_raw_record_inconsistent_unit():
    """Inconsistent measurement units must raise ValidationError."""
    raw = {
        "timestamp": "2026-10-28T12:00:00Z",
        "resource_type": "power",
        "value": 150.0,
        "capacity": 500.0,
        "unit": "MW"  # expected kW
    }
    with pytest.raises(ValidationError, match="Inconsistent unit for power: received 'MW', expected 'kW'"):
        validate_raw_record(raw)


def test_validate_raw_record_negative_utilization():
    """Negative values for physical resources like storage or power must be rejected."""
    raw = {
        "timestamp": "2026-10-28T12:00:00Z",
        "resource_type": "storage",
        "value": -10.0,
        "capacity": 500.0,
        "unit": "TB"
    }
    with pytest.raises(ValidationError, match="Utilization for storage cannot be negative"):
        validate_raw_record(raw)


def test_validate_raw_record_missing_field():
    """Missing required keys must raise ValidationError."""
    raw = {
        "timestamp": "2026-10-28T12:00:00Z",
        "resource_type": "vm",
        # missing 'value'
        "capacity": 1000.0,
        "unit": "vms"
    }
    with pytest.raises(ValidationError, match="Missing required field: 'value'"):
        validate_raw_record(raw)


def test_deterministic_synthetic_generation():
    """Identical seeds must produce identical deterministic synthetic telemetry."""
    series_a = generate_synthetic_series(ResourceType.POWER, "increasing", days=7, seed=42)
    series_b = generate_synthetic_series(ResourceType.POWER, "increasing", days=7, seed=42)
    series_diff_seed = generate_synthetic_series(ResourceType.POWER, "increasing", days=7, seed=99)

    assert len(series_a) == len(series_b)
    assert len(series_a) == 7 * 24

    for rec_a, rec_b in zip(series_a, series_b):
        assert rec_a.timestamp == rec_b.timestamp
        assert rec_a.value == rec_b.value
        assert rec_a.data_source == "synthetic"

    # Different seed should produce slightly different values
    assert series_a[10].value != series_diff_seed[10].value


def test_all_five_resources_supported():
    """All 5 required resources must have valid defaults and series generators."""
    for res_type in ResourceType:
        assert res_type in RESOURCE_UNITS
        assert res_type in DEFAULT_CAPACITIES
        series = generate_synthetic_series(res_type, "stable", days=2, seed=123)
        assert len(series) == 48
        assert series[0].unit == RESOURCE_UNITS[res_type]
        assert series[0].capacity == DEFAULT_CAPACITIES[res_type]


def test_scenarios_generation():
    """Test synthetic generator across all documented scenarios."""
    scenarios = ["increasing", "stable", "decreasing", "change_point", "exceeded", "sparse"]
    for sc in scenarios:
        series = generate_synthetic_series(ResourceType.STORAGE, scenario=sc, days=5, seed=7)
        if sc == "sparse":
            # Sparse series intentionally skips measurements
            assert len(series) < 5 * 24
        else:
            assert len(series) == 5 * 24

        if sc == "exceeded":
            # Exceeded scenario should have values near or beyond 100%
            max_val = max(r.value for r in series)
            assert max_val >= DEFAULT_CAPACITIES[ResourceType.STORAGE] * 0.95


def test_tenant_breakdown():
    """Verify tenant breakdown retains data integrity and sums up near aggregate total."""
    base_series = generate_synthetic_series(ResourceType.VM, "stable", days=1, seed=5)
    sample_rec = base_series[0]
    breakdown = generate_tenant_breakdown(sample_rec, seed=50)

    assert len(breakdown) == 4
    tenant_sum = sum(t.value for t in breakdown)
    # Tenant sum should equal the aggregate total
    assert math.isclose(tenant_sum, sample_rec.value, rel_tol=1e-3)
    assert all(t.data_source == "synthetic" for t in breakdown)
    assert all(t.tenant_id is not None for t in breakdown)


def test_telemetry_store():
    """Test TelemetryStore querying, DataFrame conversion, and latest observation."""
    store = TelemetryStore()
    assert store.count() == 0

    store.seed_default_datacenter(seed=42)
    assert store.count() > 0

    power_records = store.get_by_resource(ResourceType.POWER)
    assert len(power_records) > 0

    latest_power = store.get_latest(ResourceType.POWER)
    assert latest_power is not None
    assert latest_power.resource_type == ResourceType.POWER

    df = store.to_dataframe(resource_type=ResourceType.STORAGE)
    assert not df.empty
    assert "utilization_pct" in df.columns
    assert "timestamp" in df.columns
    assert df["resource_type"].iloc[0] == "storage"
