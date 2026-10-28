"""
test_evaluation.py - Unit test verifying that the quantitative evaluation suite executes
and outputs valid benchmark metrics.
"""

from evaluation.evaluate_models import evaluate_synthetic_benchmarks


def test_evaluation_benchmark_execution():
    """Verify evaluation procedure runs and produces complete metric records."""
    report = evaluate_synthetic_benchmarks(seed=123)
    assert report["total_test_scenarios"] >= 50
    metrics = report["metrics"]
    assert "time_to_capacity_mae_days" in metrics
    assert "false_positive_rate_stable_demand_pct" in metrics
    assert "false_positive_rate_declining_demand_pct" in metrics
    assert "missed_alert_rate_overloaded_pct" in metrics
    assert metrics["false_positive_rate_declining_demand_pct"] == 0.0
    assert metrics["missed_alert_rate_overloaded_pct"] == 0.0
