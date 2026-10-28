"""
test_planning.py - Unit tests for capacity planning, procurement lead times,
and actionable advisory recommendations.
"""

import pytest
from datetime import datetime, timezone
from backend.telemetry import ResourceType, generate_synthetic_series
from backend.forecast import CapacityStatus, compute_capacity_forecast, ForecastResult
from backend.planning import (
    PlanningStatus,
    RecommendationPriority,
    DEFAULT_LEAD_TIMES_DAYS,
    evaluate_resource_planning,
    generate_actionable_recommendations,
)


def test_planning_status_overdue():
    """When days_to_capacity <= procurement_lead_time, status must be ACTION_OVERDUE."""
    # Storage with increasing demand reaches capacity in ~8 days, but lead time is 30 days
    series = generate_synthetic_series(ResourceType.STORAGE, "increasing", days=30, seed=42)
    fc = compute_capacity_forecast(series, forecast_days=30)
    assert fc.days_to_capacity is not None

    plan = evaluate_resource_planning(fc, lead_time_days=30.0)
    assert plan.planning_status == PlanningStatus.ACTION_OVERDUE
    assert plan.overdue_by_days is not None
    assert plan.overdue_by_days > 0
    assert "ACTION OVERDUE" in plan.planning_headline


def test_planning_status_order_imminent():
    """When order deadline is between 0 and 14 days, status must be ORDER_IMMINENT."""
    series = generate_synthetic_series(ResourceType.STORAGE, "increasing", days=30, seed=42)
    fc = compute_capacity_forecast(series, forecast_days=30)
    # Set lead time just slightly under days_to_capacity
    custom_lead = max(1.0, fc.days_to_capacity - 5.0)
    plan = evaluate_resource_planning(fc, lead_time_days=custom_lead)

    assert plan.planning_status == PlanningStatus.ORDER_IMMINENT
    assert plan.days_until_order_deadline is not None
    assert 0.0 < plan.days_until_order_deadline <= 14.0
    assert "ORDER WINDOW CLOSING" in plan.planning_headline


def test_planning_status_on_track():
    """When days_to_capacity far exceeds lead time, status must be ON_TRACK."""
    fc = ForecastResult(
        resource_type=ResourceType.STORAGE,
        status=CapacityStatus.NORMAL,
        current_value=1200.0,
        capacity=2000.0,
        current_utilization_pct=60.0,
        trend_slope_per_day=5.0,
        days_to_capacity=160.0,
        confidence_score=0.9,
        reason="Normal headroom with slow growth",
    )
    plan = evaluate_resource_planning(fc, lead_time_days=30.0)

    assert plan.planning_status == PlanningStatus.ON_TRACK
    assert plan.days_until_order_deadline == 130.0
    assert plan.days_until_order_deadline > 14.0


def test_planning_status_sufficient_headroom():
    """Stable or declining demand with None days_to_capacity must yield SUFFICIENT_HEADROOM."""
    series = generate_synthetic_series(ResourceType.POWER, "stable", days=30, seed=42)
    fc = compute_capacity_forecast(series, forecast_days=30)
    plan = evaluate_resource_planning(fc)

    assert plan.planning_status == PlanningStatus.SUFFICIENT_HEADROOM
    assert plan.days_to_capacity is None
    assert plan.days_until_order_deadline is None
    assert "NO EXPANSION REQUIRED" in plan.planning_headline


def test_planning_status_exceeded():
    """Already exceeded resource must yield CAPACITY_EXCEEDED."""
    series = generate_synthetic_series(ResourceType.POWER, "exceeded", days=10, seed=42)
    series[-1].value = series[-1].capacity * 1.05
    fc = compute_capacity_forecast(series, forecast_days=30)
    plan = evaluate_resource_planning(fc)

    assert plan.planning_status == PlanningStatus.CAPACITY_EXCEEDED
    assert plan.overdue_by_days is not None


def test_actionable_recommendations_generation():
    """Advisory recommendations must be generated, prioritized, and strictly read-only."""
    forecasts = {}
    plans = {}
    for res in ResourceType:
        series = generate_synthetic_series(res, "increasing" if res == ResourceType.STORAGE else "stable", days=30, seed=10)
        fc = compute_capacity_forecast(series, forecast_days=30)
        forecasts[res] = fc
        plans[res] = evaluate_resource_planning(fc)

    recs = generate_actionable_recommendations(forecasts, plans)
    assert len(recs) > 0
    # Strictly non-automated / advisory
    assert all(r.automated_action is False for r in recs)

    # Must contain action items and explanations
    for r in recs:
        assert len(r.action_items) > 0
        assert len(r.trigger_reason) > 0
        assert r.priority in [RecommendationPriority.P0_CRITICAL, RecommendationPriority.P1_HIGH, RecommendationPriority.P2_MEDIUM, RecommendationPriority.P3_LOW]
