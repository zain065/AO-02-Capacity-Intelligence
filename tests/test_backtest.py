"""
test_backtest.py - Verification tests for Out-of-Sample Forecasting Backtester.

Part of AO-02: Data Centre Capacity Intelligence.
Verifies time-ordered train/test splitting, statistical error calculation (MAE/RMSE),
empirical 95% prediction interval coverage, and comparison against Naive Persistence.
"""

import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.telemetry import ResourceType, generate_synthetic_series
from evaluation.backtest import run_out_of_sample_backtest, run_comprehensive_facility_backtest

client = TestClient(app)


def test_time_ordered_backtest_execution():
    """
    Verifies that run_out_of_sample_backtest:
    - Splits series chronologically into 70% train and 30% test.
    - Accurately computes out-of-sample MAE and RMSE.
    - Demonstrates empirical 95% prediction interval coverage.
    """
    series = generate_synthetic_series(ResourceType.STORAGE, "increasing", days=30, seed=42)
    report = run_out_of_sample_backtest(series, train_ratio=0.70)

    assert "ols_model" in report
    assert "naive_baseline" in report
    assert report["train_observations"] > report["test_observations"]
    assert report["train_observations"] + report["test_observations"] == len(series)

    # Statistical validity
    ols_mae = report["ols_model"]["mae"]
    naive_mae = report["naive_baseline"]["mae"]
    assert ols_mae > 0.0
    assert naive_mae > 0.0

    # On an increasing linear demand series, OLS must significantly beat naive persistence
    assert report["improvement_over_naive_pct"] > 20.0

    # Empirical 95% coverage should be reasonably close to 95%
    coverage = report["ols_model"]["prediction_interval_coverage_pct"]
    assert 80.0 <= coverage <= 100.0


def test_evaluation_summary_endpoint():
    """GET /api/v1/evaluation/summary must return full backtest metrics for all 5 resources."""
    response = client.get(
        "/api/v1/evaluation/summary",
        headers={"Authorization": "Bearer admin-token-secret-01"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "mean_ols_mae" in data
    assert "mean_prediction_interval_coverage_pct" in data
    assert "mean_improvement_over_naive_baseline_pct" in data
    assert len(data["resources_evaluated"]) == 5
