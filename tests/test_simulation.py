"""
test_simulation.py - Unit tests for interactive What-If scenario forecasting.
Verifies calculation correctness and absolute preservation of underlying telemetry state.
"""

import pytest
from backend.telemetry import ResourceType, generate_synthetic_series
from backend.forecast import (
    CapacityStatus,
    SimulationRequest,
    simulate_what_if_scenario,
    compute_capacity_forecast,
)


def test_what_if_accelerated_demand():
    """Higher demand multiplier must accelerate capacity exhaustion."""
    series = generate_synthetic_series(ResourceType.STORAGE, "increasing", days=30, seed=42)
    original_last_val = series[-1].value
    original_count = len(series)

    # Simulate 2.0x faster growth
    req = SimulationRequest(
        resource_type=ResourceType.STORAGE,
        demand_growth_multiplier=2.0,
        workload_delta=0.0,
        capacity_adjustment=0.0,
        forecast_days=30
    )
    sim = simulate_what_if_scenario(series, req)

    assert sim.is_simulation is True
    assert "WHAT-IF" in sim.simulation_notice
    assert sim.simulated_trend_slope_per_day > sim.baseline_trend_slope_per_day
    assert sim.simulated_days_to_capacity is not None
    assert sim.baseline_days_to_capacity is not None
    assert sim.simulated_days_to_capacity < sim.baseline_days_to_capacity
    assert sim.delta_days_to_capacity is not None
    assert sim.delta_days_to_capacity < 0

    # CRITICAL: Verify telemetry records were NOT mutated!
    assert len(series) == original_count
    assert series[-1].value == original_last_val


def test_what_if_workload_addition():
    """Adding immediate workload must increase current utilization and shorten headroom."""
    series = generate_synthetic_series(ResourceType.VM, "increasing", days=30, seed=42)
    req = SimulationRequest(
        resource_type=ResourceType.VM,
        demand_growth_multiplier=1.0,
        workload_delta=100.0,  # add 100 VMs
        capacity_adjustment=0.0,
        forecast_days=30
    )
    sim = simulate_what_if_scenario(series, req)

    assert sim.simulated_current_value == pytest.approx(sim.baseline_current_value + 100.0, rel=1e-3)
    assert sim.simulated_utilization_pct > sim.baseline_utilization_pct


def test_what_if_capacity_expansion():
    """Expanding capacity must increase headroom and extend days to capacity."""
    series = generate_synthetic_series(ResourceType.STORAGE, "increasing", days=30, seed=42)
    req = SimulationRequest(
        resource_type=ResourceType.STORAGE,
        demand_growth_multiplier=1.0,
        workload_delta=0.0,
        capacity_adjustment=1000.0,  # Expand from 2000 TB to 3000 TB
        forecast_days=30
    )
    sim = simulate_what_if_scenario(series, req)

    assert sim.simulated_capacity == sim.baseline_capacity + 1000.0
    assert sim.simulated_days_to_capacity is not None
    assert sim.simulated_days_to_capacity > sim.baseline_days_to_capacity
    assert sim.delta_days_to_capacity is not None
    assert sim.delta_days_to_capacity > 0


def test_what_if_stabilized_demand():
    """Setting negative/zero growth multiplier must eliminate capacity crunch."""
    series = generate_synthetic_series(ResourceType.STORAGE, "increasing", days=30, seed=42)
    req = SimulationRequest(
        resource_type=ResourceType.STORAGE,
        demand_growth_multiplier=-0.5,
        workload_delta=0.0,
        capacity_adjustment=0.0,
        forecast_days=30
    )
    sim = simulate_what_if_scenario(series, req)

    assert sim.simulated_days_to_capacity is None
    assert sim.simulated_status == CapacityStatus.NORMAL
    assert "averted" in sim.urgency_impact.lower()
