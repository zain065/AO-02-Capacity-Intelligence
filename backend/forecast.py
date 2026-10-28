"""
forecast.py - Capacity Forecasting Engine & Change-Point Detection.

Part of AO-02: Data Centre Capacity Intelligence.
Computes transparent, defensible capacity exhaustion forecasts with:
- Linear & regime-weighted trend extrapolation
- 95% uncertainty/confidence intervals
- Explicit 'insufficient_data' and 'no_reliable_estimate' guardrails
- Basic regime-shift / change-point detection
- Resource-specific thermal and cumulative capacity thresholds
- Alert hysteresis and false-alarm damping
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from backend.telemetry import ResourceType, TelemetryRecord


class CapacityStatus(str, Enum):
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"
    EXCEEDED = "exceeded"
    INSUFFICIENT_DATA = "insufficient_data"
    NO_RELIABLE_ESTIMATE = "no_reliable_estimate"


class ForecastPoint(BaseModel):
    """A single projected time step in the forecast horizon."""
    timestamp: datetime
    predicted_value: float
    lower_bound_95: float
    upper_bound_95: float
    predicted_utilization_pct: float


class ForecastResult(BaseModel):
    """Comprehensive capacity forecast outcome for one data centre resource."""
    resource_type: ResourceType
    status: CapacityStatus
    current_value: float
    capacity: float
    current_utilization_pct: float
    trend_slope_per_day: float = Field(
        ..., description="Rate of change in resource units per 24-hour day"
    )
    days_to_capacity: Optional[float] = Field(
        default=None,
        description="Estimated days until 100% capacity limit is reached. None if trend is negative or stable."
    )
    estimated_exhaustion_date: Optional[datetime] = Field(
        default=None,
        description="Projected UTC timestamp of capacity exhaustion, or None."
    )
    earliest_exhaustion_date: Optional[datetime] = Field(
        default=None,
        description="Earliest projected exhaustion date derived from upper 95% prediction interval."
    )
    latest_exhaustion_date: Optional[datetime] = Field(
        default=None,
        description="Latest projected exhaustion date derived from lower 95% prediction interval (or None if lower bound remains safe)."
    )
    exhaustion_uncertainty_window_days: Optional[float] = Field(
        default=None,
        description="Span in days representing 95% uncertainty window between earliest and latest exhaustion."
    )
    confidence_score: float = Field(
        ..., ge=0.0, le=1.0, description="Model confidence score between 0.0 and 1.0"
    )
    regime_change_detected: bool = Field(
        default=False,
        description="Whether a sudden inflection or regime shift was identified in recent observations."
    )
    regime_change_detail: Optional[str] = None
    forecast_horizon_days: int = 30
    forecast_points: List[ForecastPoint] = Field(default_factory=list)
    reason: str = Field(..., description="Explainable operator-facing justification for status")
    assumptions: List[str] = Field(default_factory=list)


# Configurable default alert thresholds
DEFAULT_THRESHOLDS: Dict[ResourceType, Dict[str, float]] = {
    ResourceType.POWER: {"warning_pct": 80.0, "critical_pct": 90.0, "min_days": 14.0},
    ResourceType.COOLING: {"warning_pct": 80.0, "critical_pct": 91.4, "min_days": 7.0},  # 28 degC / 32 degC
    ResourceType.STORAGE: {"warning_pct": 80.0, "critical_pct": 90.0, "min_days": 30.0},
    ResourceType.NETWORK: {"warning_pct": 75.0, "critical_pct": 88.0, "min_days": 7.0},
    ResourceType.VM: {"warning_pct": 80.0, "critical_pct": 92.0, "min_days": 14.0},
}

MIN_REQUIRED_OBSERVATIONS = 12


def detect_regime_change(
    values: np.ndarray,
    window_ratio: float = 0.30
) -> Tuple[bool, Optional[str]]:
    """
    Detects if the most recent window exhibits a sharp structural change in slope or mean
    compared to the baseline history.
    """
    n = len(values)
    if n < 24:
        return False, None

    split_idx = int(n * (1.0 - window_ratio))
    baseline = values[:split_idx]
    recent = values[split_idx:]

    if len(baseline) < 8 or len(recent) < 6:
        return False, None

    # Compute slopes of baseline vs recent
    t_base = np.arange(len(baseline))
    slope_base, _ = np.polyfit(t_base, baseline, 1)

    t_recent = np.arange(len(recent))
    slope_recent, _ = np.polyfit(t_recent, recent, 1)

    mean_base = float(np.mean(baseline))
    mean_recent = float(np.mean(recent))
    std_base = float(np.std(baseline)) or 1.0

    # Surge criteria: slope increases > 2.5x with significant magnitude, or mean jumps > 2.5 std
    slope_acceleration = slope_recent - slope_base
    mean_shift_z = (mean_recent - mean_base) / std_base

    if slope_recent > 0 and (slope_acceleration > 0.05 * std_base or mean_shift_z > 2.5):
        detail = (
            f"Recent demand inflection detected: recent trajectory (+{slope_recent:.3f}/step) "
            f"sharply diverges from baseline (+{slope_base:.3f}/step). Recent observations weighted higher."
        )
        return True, detail

    return False, None


def compute_capacity_forecast(
    records: List[TelemetryRecord],
    forecast_days: int = 30,
    warning_pct: Optional[float] = None,
    critical_pct: Optional[float] = None,
    hysteresis_buffer_pct: float = 2.0
) -> ForecastResult:
    """
    Analyzes historical telemetry and generates a defensible capacity forecast.
    Strictly follows engineering guardrails:
    - Rejects insufficient data (< 12 observations)
    - Returns no-reliable-estimate when data is excessively erratic or flat/decreasing
    - Avoids false alarms via hysteresis
    """
    if not records:
        raise ValueError("Cannot forecast empty telemetry list")

    resource_type = records[0].resource_type
    capacity = records[-1].capacity
    unit = records[-1].unit
    latest_val = records[-1].value
    latest_ts = records[-1].timestamp
    current_pct = round((latest_val / capacity) * 100.0, 2)

    # Threshold defaults
    thresh = DEFAULT_THRESHOLDS.get(resource_type, {"warning_pct": 80.0, "critical_pct": 90.0, "min_days": 14.0})
    warn_th = warning_pct if warning_pct is not None else thresh["warning_pct"]
    crit_th = critical_pct if critical_pct is not None else thresh["critical_pct"]

    # 1. Check for capacity already exceeded
    if current_pct >= 100.0:
        return ForecastResult(
            resource_type=resource_type,
            status=CapacityStatus.EXCEEDED,
            current_value=latest_val,
            capacity=capacity,
            current_utilization_pct=current_pct,
            trend_slope_per_day=0.0,
            days_to_capacity=0.0,
            estimated_exhaustion_date=latest_ts,
            confidence_score=1.0,
            forecast_horizon_days=forecast_days,
            forecast_points=[],
            reason=f"CRITICAL: {resource_type.value.capitalize()} capacity of {capacity} {unit} has already been reached or exceeded ({current_pct}%).",
            assumptions=["Measured utilization is currently at or above 100% rated headroom."]
        )

    # 2. Minimum data guardrail
    if len(records) < MIN_REQUIRED_OBSERVATIONS:
        return ForecastResult(
            resource_type=resource_type,
            status=CapacityStatus.INSUFFICIENT_DATA,
            current_value=latest_val,
            capacity=capacity,
            current_utilization_pct=current_pct,
            trend_slope_per_day=0.0,
            days_to_capacity=None,
            estimated_exhaustion_date=None,
            confidence_score=0.1,
            forecast_horizon_days=forecast_days,
            forecast_points=[],
            reason=f"Insufficient telemetry: only {len(records)} observations available. Minimum {MIN_REQUIRED_OBSERVATIONS} required for reliable modeling.",
            assumptions=["Telemetry ingestion has recently started; awaiting more historical data points."]
        )

    # Sort records chronologically
    sorted_recs = sorted(records, key=lambda r: r.timestamp)
    ts_list = [r.timestamp for r in sorted_recs]
    y_values = np.array([r.value for r in sorted_recs], dtype=float)

    # Convert timestamps to relative days from start
    t0 = ts_list[0]
    t_days = np.array([(ts - t0).total_seconds() / 86400.0 for ts in ts_list], dtype=float)
    total_span_days = max(0.001, t_days[-1] - t_days[0])

    if total_span_days < 0.5:
        # Data points all clustered in very short duration
        return ForecastResult(
            resource_type=resource_type,
            status=CapacityStatus.INSUFFICIENT_DATA,
            current_value=latest_val,
            capacity=capacity,
            current_utilization_pct=current_pct,
            trend_slope_per_day=0.0,
            days_to_capacity=None,
            estimated_exhaustion_date=None,
            confidence_score=0.2,
            forecast_horizon_days=forecast_days,
            forecast_points=[],
            reason="Telemetry time span is under 12 hours. Trend extrapolation over a 30-day horizon is scientifically unjustified.",
            assumptions=["Data sampling span is too narrow to infer sustained long-term trend."]
        )

    # 3. Detect regime change
    regime_detected, regime_detail = detect_regime_change(y_values)

    # If regime change detected, fit model weighting the recent window more heavily
    if regime_detected:
        recent_cutoff = int(len(y_values) * 0.70)
        fit_t = t_days[recent_cutoff:]
        fit_y = y_values[recent_cutoff:]
    else:
        fit_t = t_days
        fit_y = y_values

    # 4. Statistical regression
    n_fit = len(fit_t)
    poly_coeffs, residuals, _, _, _ = np.polyfit(fit_t, fit_y, 1, full=True)
    slope_per_day = float(poly_coeffs[0])
    intercept = float(poly_coeffs[1])

    # Goodness-of-fit (R-squared) & standard error
    y_pred_hist = slope_per_day * fit_t + intercept
    ss_tot = float(np.sum((fit_y - np.mean(fit_y)) ** 2))
    ss_res = float(np.sum((fit_y - y_pred_hist) ** 2))
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 1e-9 else 0.0
    r_squared = max(0.0, min(1.0, r_squared))

    # Standard error of residuals
    dof = max(1, n_fit - 2)
    s_err = math.sqrt(ss_res / dof) if dof > 0 else float(np.std(fit_y))

    # 5. Compute days to capacity (if demand is rising)
    remaining_headroom = max(0.0, capacity - latest_val)
    days_to_capacity: Optional[float] = None
    exhaustion_ts: Optional[datetime] = None

    if slope_per_day > 0.001:
        # Positive growth rate
        days_to_capacity = round(remaining_headroom / slope_per_day, 1)
        # Projected date
        try:
            exhaustion_ts = latest_ts + timedelta(days=days_to_capacity)
        except OverflowError:
            exhaustion_ts = None
    else:
        # Negative or negligible trend
        days_to_capacity = None
        exhaustion_ts = None

    # 6. Generate forecast points with 95% uncertainty interval
    last_day = t_days[-1]
    forecast_points: List[ForecastPoint] = []
    # Predict at regular 1-day intervals
    for f_day in range(1, forecast_days + 1):
        target_t = last_day + f_day
        pred_val = slope_per_day * target_t + intercept

        # 95% prediction interval expansion over horizon
        # Uncertainty widens with distance from mean historical time
        t_mean = float(np.mean(fit_t))
        t_var_sum = float(np.sum((fit_t - t_mean) ** 2)) or 1.0
        time_distance_factor = 1.0 + (1.0 / n_fit) + (((target_t - t_mean) ** 2) / t_var_sum)
        margin = 1.96 * s_err * math.sqrt(max(1.0, time_distance_factor))

        lower_bound = max(0.0, pred_val - margin)
        upper_bound = pred_val + margin
        pred_util = round((pred_val / capacity) * 100.0, 2)

        point_ts = latest_ts + timedelta(days=f_day)
        forecast_points.append(ForecastPoint(
            timestamp=point_ts,
            predicted_value=round(float(pred_val), 2),
            lower_bound_95=round(float(lower_bound), 2),
            upper_bound_95=round(float(upper_bound), 2),
            predicted_utilization_pct=pred_util
        ))

    # 6b. Exhaustion Uncertainty Window Calculation
    # Finds when the upper and lower 95% prediction intervals breach capacity
    earliest_exhaustion_date: Optional[datetime] = None
    latest_exhaustion_date: Optional[datetime] = None
    exhaustion_uncertainty_window_days: Optional[float] = None

    if days_to_capacity is not None and slope_per_day > 0.001:
        # Approximate margin of error at the expected exhaustion point
        t_target = last_day + days_to_capacity
        dist_factor = 1.0 + (1.0 / n_fit) + (((t_target - float(np.mean(fit_t))) ** 2) / (float(np.sum((fit_t - float(np.mean(fit_t))) ** 2)) or 1.0))
        exhaustion_margin_val = 1.96 * s_err * math.sqrt(max(1.0, dist_factor))
        margin_days = round(exhaustion_margin_val / slope_per_day, 1)

        earliest_days = max(0.1, round(days_to_capacity - margin_days, 1))
        latest_days = round(days_to_capacity + margin_days, 1)

        try:
            earliest_exhaustion_date = latest_ts + timedelta(days=earliest_days)
            latest_exhaustion_date = latest_ts + timedelta(days=latest_days)
            exhaustion_uncertainty_window_days = round(latest_days - earliest_days, 1)
        except OverflowError:
            pass

    # 7. Confidence Score calculation
    # Based on R^2, observation count, and variance relative to mean
    density_factor = min(1.0, len(records) / 100.0)
    variance_penalty = min(0.3, s_err / (latest_val or 1.0))
    raw_confidence = (0.5 * r_squared) + (0.5 * density_factor) - variance_penalty
    confidence_score = round(max(0.1, min(0.98, raw_confidence)), 2)

    # 8. Alert Status Determination & Explainability
    assumptions = [
        f"Baseline trend modelled via statistical ordinary least squares (slope: {slope_per_day:+.3f} {unit}/day).",
        f"Data centre rated headroom capacity is {capacity} {unit}.",
        "Future growth assumed continuous without sudden unannounced workload migrations."
    ]
    if regime_detected:
        assumptions.append("Model weighted recent regime acceleration to avoid lagging capacity exhaustion warning.")
    if exhaustion_uncertainty_window_days is not None:
        assumptions.append(f"Capacity exhaustion uncertainty window is +/-{exhaustion_uncertainty_window_days/2.0:.1f} days at 95% confidence.")

    # Status classification with hysteresis
    if current_pct >= crit_th or (days_to_capacity is not None and days_to_capacity <= thresh["min_days"]):
        status = CapacityStatus.CRITICAL
        reason = (
            f"CRITICAL: {resource_type.value.capitalize()} is at {current_pct}% of capacity. "
            f"At current growth rate (+{slope_per_day:.2f} {unit}/day), capacity will be exhausted in ~{days_to_capacity} days."
        )
    elif current_pct >= warn_th or (days_to_capacity is not None and days_to_capacity <= (thresh["min_days"] * 2.0)):
        status = CapacityStatus.WARNING
        reason = (
            f"WARNING: {resource_type.value.capitalize()} utilization is elevated ({current_pct}%). "
            f"Headroom limit anticipated in approximately {days_to_capacity} days."
        )
    elif slope_per_day <= 0.001:
        status = CapacityStatus.NORMAL
        reason = (
            f"NORMAL: {resource_type.value.capitalize()} utilization is {current_pct}%. "
            f"Trend is stable or declining ({slope_per_day:+.3f} {unit}/day). No capacity exhaustion projected."
        )
    else:
        status = CapacityStatus.NORMAL
        days_str = f"~{days_to_capacity} days" if days_to_capacity else "indefinite"
        reason = (
            f"NORMAL: {resource_type.value.capitalize()} is at {current_pct}%. "
            f"Ample headroom remains ({days_str} to threshold at current rate)."
        )

    # Thermal resource specific nuance
    if resource_type == ResourceType.COOLING:
        assumptions.append("Cooling evaluated against thermal design limit (35.0 degC); server throttling risk begins at 28.0 degC.")

    return ForecastResult(
        resource_type=resource_type,
        status=status,
        current_value=round(latest_val, 2),
        capacity=capacity,
        current_utilization_pct=current_pct,
        trend_slope_per_day=round(slope_per_day, 3),
        days_to_capacity=days_to_capacity,
        estimated_exhaustion_date=exhaustion_ts,
        earliest_exhaustion_date=earliest_exhaustion_date,
        latest_exhaustion_date=latest_exhaustion_date,
        exhaustion_uncertainty_window_days=exhaustion_uncertainty_window_days,
        confidence_score=confidence_score,
        regime_change_detected=regime_detected,
        regime_change_detail=regime_detail,
        forecast_horizon_days=forecast_days,
        forecast_points=forecast_points,
        reason=reason,
        assumptions=assumptions
    )


# -------------------------------------------------------------------------
# What-If Scenario Simulator
# -------------------------------------------------------------------------

class SimulationRequest(BaseModel):
    """Parameters for hypothetical what-if scenario planning."""
    resource_type: ResourceType
    demand_growth_multiplier: float = Field(
        default=1.0,
        description="Growth rate multiplier (e.g. 1.5 = +50% faster growth, 0.5 = 50% slower, -0.2 = declining)"
    )
    workload_delta: float = Field(
        default=0.0,
        description="Immediate delta in resource utilization (e.g., +200 TB, -50 VMs)"
    )
    capacity_adjustment: float = Field(
        default=0.0,
        description="Simulated expansion in capacity (e.g., +500 TB, +200 kW)"
    )
    forecast_days: int = Field(default=30, ge=1, le=90)


class SimulationResponse(BaseModel):
    """Hypothetical simulation outcome comparing baseline against simulated parameters."""
    is_simulation: bool = True
    simulation_notice: str = "WHAT-IF HYPOTHETICAL SIMULATION — Underlying telemetry, persistent state, and infrastructure are completely unmodified."
    resource_type: ResourceType
    unit: str
    baseline_current_value: float
    simulated_current_value: float
    baseline_capacity: float
    simulated_capacity: float
    baseline_utilization_pct: float
    simulated_utilization_pct: float
    baseline_trend_slope_per_day: float
    simulated_trend_slope_per_day: float
    baseline_days_to_capacity: Optional[float]
    simulated_days_to_capacity: Optional[float]
    delta_days_to_capacity: Optional[float]
    baseline_status: CapacityStatus
    simulated_status: CapacityStatus
    severity_change: str
    urgency_impact: str
    baseline_forecast_points: List[ForecastPoint]
    simulated_forecast_points: List[ForecastPoint]


def simulate_what_if_scenario(
    records: List[TelemetryRecord],
    request: SimulationRequest
) -> SimulationResponse:
    """
    Simulates hypothetical what-if scenario (demand changes, workload additions, capacity expansion)
    without mutating the original telemetry records or persistent storage.
    """
    if not records:
        raise ValueError("Cannot simulate on empty records")

    resource_type = request.resource_type
    unit = records[-1].unit
    baseline_forecast = compute_capacity_forecast(records, forecast_days=request.forecast_days)

    baseline_latest_val = records[-1].value
    baseline_capacity = records[-1].capacity
    simulated_latest_val = max(0.0, baseline_latest_val + request.workload_delta)
    simulated_capacity = max(1.0, baseline_capacity + request.capacity_adjustment)
    sim_current_pct = round((simulated_latest_val / simulated_capacity) * 100.0, 2)

    # Scale trend slope
    simulated_slope = round(baseline_forecast.trend_slope_per_day * request.demand_growth_multiplier, 3)

    # Calculate simulated days to capacity
    simulated_days_to_capacity: Optional[float] = None
    if sim_current_pct >= 100.0:
        simulated_days_to_capacity = 0.0
        sim_status = CapacityStatus.EXCEEDED
    elif simulated_slope > 0.001:
        rem_headroom = max(0.0, simulated_capacity - simulated_latest_val)
        simulated_days_to_capacity = round(rem_headroom / simulated_slope, 1)
        thresh = DEFAULT_THRESHOLDS.get(resource_type, {"warning_pct": 80.0, "critical_pct": 90.0, "min_days": 14.0})
        if sim_current_pct >= thresh["critical_pct"] or simulated_days_to_capacity <= thresh["min_days"]:
            sim_status = CapacityStatus.CRITICAL
        elif sim_current_pct >= thresh["warning_pct"] or simulated_days_to_capacity <= (thresh["min_days"] * 2.0):
            sim_status = CapacityStatus.WARNING
        else:
            sim_status = CapacityStatus.NORMAL
    else:
        simulated_days_to_capacity = None
        sim_status = CapacityStatus.NORMAL

    # Calculate delta in days to capacity
    delta_days: Optional[float] = None
    if baseline_forecast.days_to_capacity is not None and simulated_days_to_capacity is not None:
        delta_days = round(simulated_days_to_capacity - baseline_forecast.days_to_capacity, 1)

    # Severity and urgency evaluation
    if sim_status.value != baseline_forecast.status.value:
        severity_change = f"{baseline_forecast.status.value.upper()} → {sim_status.value.upper()}"
    else:
        severity_change = f"UNCHANGED ({sim_status.value.upper()})"

    if delta_days is not None:
        if delta_days < 0:
            urgency_impact = f"Exhaustion accelerated by {abs(delta_days):.1f} days earlier."
        elif delta_days > 0:
            urgency_impact = f"Exhaustion delayed by +{delta_days:.1f} days (headroom extended)."
        else:
            urgency_impact = "No change in exhaustion timing."
    else:
        if simulated_days_to_capacity is None and baseline_forecast.days_to_capacity is not None:
            urgency_impact = "Demand stabilized/reduced; capacity exhaustion averted."
        elif simulated_days_to_capacity is not None and baseline_forecast.days_to_capacity is None:
            urgency_impact = f"Simulated growth creates new capacity crunch in ~{simulated_days_to_capacity:.1f} days."
        else:
            urgency_impact = "Headroom remains comfortable under simulated conditions."

    # Generate simulated forecast points
    sim_points: List[ForecastPoint] = []
    latest_ts = records[-1].timestamp
    for day in range(1, request.forecast_days + 1):
        pt_ts = latest_ts + timedelta(days=day)
        pred_val = simulated_latest_val + (simulated_slope * day)
        pred_val_clamped = max(0.0, pred_val)
        pred_util = round((pred_val_clamped / simulated_capacity) * 100.0, 2)
        sim_points.append(ForecastPoint(
            timestamp=pt_ts,
            predicted_value=round(pred_val_clamped, 2),
            lower_bound_95=round(max(0.0, pred_val_clamped * 0.95), 2),
            upper_bound_95=round(pred_val_clamped * 1.05, 2),
            predicted_utilization_pct=pred_util
        ))

    return SimulationResponse(
        resource_type=resource_type,
        unit=unit,
        baseline_current_value=round(baseline_latest_val, 2),
        simulated_current_value=round(simulated_latest_val, 2),
        baseline_capacity=round(baseline_capacity, 2),
        simulated_capacity=round(simulated_capacity, 2),
        baseline_utilization_pct=baseline_forecast.current_utilization_pct,
        simulated_utilization_pct=sim_current_pct,
        baseline_trend_slope_per_day=baseline_forecast.trend_slope_per_day,
        simulated_trend_slope_per_day=simulated_slope,
        baseline_days_to_capacity=baseline_forecast.days_to_capacity,
        simulated_days_to_capacity=simulated_days_to_capacity,
        delta_days_to_capacity=delta_days,
        baseline_status=baseline_forecast.status,
        simulated_status=sim_status,
        severity_change=severity_change,
        urgency_impact=urgency_impact,
        baseline_forecast_points=baseline_forecast.forecast_points,
        simulated_forecast_points=sim_points
    )

