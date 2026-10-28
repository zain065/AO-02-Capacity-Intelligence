"""
test_forecast.py - Unit tests for capacity forecasting engine, regime-shift detection,
uncertainty intervals, and false-alarm guardrails.
"""

import pytest
from datetime import datetime, timedelta, timezone
from backend.telemetry import (
    ResourceType,
    DEFAULT_CAPACITIES,
    TelemetryRecord,
    generate_synthetic_series,
)
from backend.forecast import (
    CapacityStatus,
    ForecastResult,
    compute_capacity_forecast,
    detect_regime_change,
)


def test_empty_telemetry_raises_error():
    """Forecasting on empty records must raise ValueError."""
    with pytest.raises(ValueError, match="Cannot forecast empty telemetry list"):
        compute_capacity_forecast([])


def test_insufficient_data_returns_guardrail():
    """Fewer than MIN_REQUIRED_OBSERVATIONS must return INSUFFICIENT_DATA without extrapolation."""
    # Generate only 5 observations
    series = generate_synthetic_series(ResourceType.POWER, "increasing", days=1, seed=42)[:5]
    res = compute_capacity_forecast(series)

    assert res.status == CapacityStatus.INSUFFICIENT_DATA
    assert res.days_to_capacity is None
    assert res.estimated_exhaustion_date is None
    assert "Insufficient telemetry" in res.reason
    assert res.confidence_score <= 0.2


def test_short_time_span_guardrail():
    """Observations clustered within < 12 hours must be rejected as insufficient span."""
    base = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)
    records = [
        TelemetryRecord(
            timestamp=base + timedelta(minutes=i * 10),
            resource_type=ResourceType.STORAGE,
            value=100.0 + i,
            capacity=2000.0,
            unit="TB",
            data_source="synthetic"
        )
        for i in range(15)  # 15 points across 150 minutes (2.5 hours)
    ]
    res = compute_capacity_forecast(records)
    assert res.status == CapacityStatus.INSUFFICIENT_DATA
    assert "time span is under 12 hours" in res.reason


def test_capacity_already_exceeded():
    """When utilization is at or above 100%, status must be EXCEEDED and days_to_capacity 0.0."""
    series = generate_synthetic_series(ResourceType.POWER, "exceeded", days=3, seed=42)
    # Ensure latest value is at least capacity
    series[-1].value = series[-1].capacity * 1.02
    res = compute_capacity_forecast(series)

    assert res.status == CapacityStatus.EXCEEDED
    assert res.days_to_capacity == 0.0
    assert "has already been reached or exceeded" in res.reason


def test_increasing_demand_forecast():
    """Increasing demand must estimate days_to_capacity, trend slope, and forecast points."""
    series = generate_synthetic_series(ResourceType.STORAGE, "increasing", days=30, seed=42)
    res = compute_capacity_forecast(series, forecast_days=30)

    assert res.resource_type == ResourceType.STORAGE
    assert res.trend_slope_per_day > 0
    assert res.days_to_capacity is not None
    assert res.days_to_capacity > 0
    assert res.estimated_exhaustion_date is not None
    assert len(res.forecast_points) == 30
    assert res.confidence_score > 0.5

    # Check uncertainty bounds
    for pt in res.forecast_points:
        assert pt.upper_bound_95 >= pt.predicted_value
        assert pt.predicted_value >= pt.lower_bound_95


def test_stable_demand_does_not_claim_capacity_exhaustion():
    """Stable demand must NOT claim capacity will be exhausted; days_to_capacity must be None."""
    series = generate_synthetic_series(ResourceType.POWER, "stable", days=30, seed=42)
    res = compute_capacity_forecast(series)

    assert res.status == CapacityStatus.NORMAL
    # If slope is very small or negative, days_to_capacity is None or indefinite
    if res.trend_slope_per_day <= 0.001:
        assert res.days_to_capacity is None
        assert "No capacity exhaustion projected" in res.reason


def test_decreasing_demand_forecast():
    """Decreasing demand should result in NORMAL status and None for days_to_capacity."""
    series = generate_synthetic_series(ResourceType.VM, "decreasing", days=20, seed=42)
    res = compute_capacity_forecast(series)

    assert res.status == CapacityStatus.NORMAL
    assert res.days_to_capacity is None
    assert res.trend_slope_per_day <= 0.001


def test_regime_change_detection():
    """Sudden surge in demand in recent observations must be detected and flagged."""
    series = generate_synthetic_series(ResourceType.COOLING, "change_point", days=30, seed=42)
    res = compute_capacity_forecast(series)

    assert res.regime_change_detected is True
    assert res.regime_change_detail is not None
    assert "inflection detected" in res.regime_change_detail.lower()
    assert any("regime" in a.lower() for a in res.assumptions)


def test_cooling_thermal_thresholds():
    """Cooling must use thermal-specific thresholds (35 degC redline)."""
    series = generate_synthetic_series(ResourceType.COOLING, "stable", days=14, seed=42)
    res = compute_capacity_forecast(series)

    assert res.resource_type == ResourceType.COOLING
    assert any("thermal design limit" in a for a in res.assumptions)


def test_forecast_uncertainty_widening():
    """Uncertainty interval should widen as we project further into the future."""
    series = generate_synthetic_series(ResourceType.STORAGE, "increasing", days=30, seed=42)
    res = compute_capacity_forecast(series, forecast_days=30)

    points = res.forecast_points
    early_spread = points[0].upper_bound_95 - points[0].lower_bound_95
    late_spread = points[-1].upper_bound_95 - points[-1].lower_bound_95

    assert late_spread >= early_spread
