# AO-02: Data Centre Capacity Intelligence

[![FastAPI](https://img.shields.io/badge/FastAPI-1.0.0-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.14-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Tests: 33 Passed](https://img.shields.io/badge/pytest-33%20passed-brightgreen?logo=pytest&logoColor=white)](https://docs.pytest.org)

An intelligent, explainable predictive capacity forecasting and thermal monitoring platform for modern data centre infrastructure. 

Developed as a hackathon project to give data centre operators early, actionable warning before critical resources reach their capacity limits—preventing service outages, hardware throttling, and emergency procurement panics.

---

## 1. Problem We Are Solving

Data centres operate within hard physical, thermal, and virtual constraints:
- **Electrical Power**: Substation and transformer zone limits (kW).
- **Cooling & Thermal**: CRAC/chiller capacity and server rack redline thermal limits (°C).
- **Storage**: SAN/NAS array capacity exhaustion (TB).
- **Network Bandwidth**: Top-of-Rack (ToR) and core uplink saturation (Gbps).
- **Virtual Machines**: Hypervisor host density and VM slot allocation limits.

If demand grows faster than anticipated, data centres face service downtime, thermal throttling, or expensive emergency upgrades. **AO-02 Capacity Intelligence** analyzes time-series telemetry to model growth trajectories, detect sudden demand regime shifts, compute uncertainty intervals, and provide honest, transparent time-to-capacity estimates.

---

## 2. Technology Stack (Free & Locally Runnable)

- **Backend**: Python 3.14, FastAPI (read-only ASGI REST API)
- **Data Modeling & Validation**: Pydantic v2
- **Numerical & Statistical Analysis**: NumPy & Pandas
- **Forecasting Engine**: Ordinary Least Squares (OLS) linear trend extrapolation, 95% dynamic prediction intervals, and heuristic regime-change detection
- **Frontend**: Plain HTML5, Vanilla CSS3 (responsive dark-mode glassmorphic design), Vanilla JavaScript (No React, Angular, or external framework required)
- **Visualization**: Chart.js (CDN-loaded lightweight canvas chart for time-series and confidence bounds)
- **Testing & Verification**: pytest (33 automated unit and integration tests)
- **Zero Cloud / GPU Requirement**: Runs 100% locally on any standard student laptop.

---

## 3. Architecture & Core Features

```
AO-02-Capacity-Intelligence/
├── backend/
│   ├── main.py            # Read-only FastAPI server & static file mount
│   ├── telemetry.py       # Ingestion schemas, data store, deterministic synthetic generator
│   ├── forecast.py        # Forecasting engine, regime-shift detector, confidence bounds
│   └── requirements.txt   # Pinned Python package dependencies
├── frontend/
│   ├── index.html         # Responsive operator dashboard
│   ├── style.css          # Design system, dark mode, glassmorphic layout
│   └── script.js          # REST API integration, Chart.js visualization, UI interactivity
├── tests/
│   ├── test_telemetry.py  # Unit tests for data models, validation, and deterministic seeds
│   ├── test_forecast.py   # Unit tests for trend extrapolation, uncertainty, and guardrails
│   ├── test_api.py        # Integration tests for FastAPI endpoints and tenant isolation
│   └── test_evaluation.py # Automated test verifying benchmark evaluation execution
├── evaluation/
│   ├── evaluate_models.py # Quantitative benchmark measuring MAE, false alarms, and missed alerts
│   └── evaluation_report.json # Reproducible evaluation report
├── data/                  # Telemetry storage and dataset documentation
├── README.md              # Project documentation and reproduction guide
└── .gitignore             # Git ignore rules for venv, cache, and metadata
```

### Core Engine Capabilities

1. **Deterministic Telemetry Generation & Validation (`backend/telemetry.py`)**:
   - Generates reproducible, seed-based synthetic time-series for all 5 resources.
   - Clearly labels every observation as `data_source: synthetic` (never presented as real operational records).
   - Validates missing keys, invalid units, negative utilization, and non-positive capacities.

2. **Defensible Forecasting Engine (`backend/forecast.py`)**:
   - Computes trend slope ($\Delta\text{units}/\text{day}$) and projected days until capacity exhaustion.
   - **False-Alarm Suppression Guardrail**: If demand is stable or declining ($\text{slope} \le 0$), the engine strictly refuses to forecast a capacity breach, returning `days_to_capacity: null` with an explicit explanation.
   - **Insufficient Data Guardrail**: If observations are sparse ($<12$ points) or cover $<12$ hours, the engine explicitly returns `status: insufficient_data`.
   - **Regime-Change Detection**: Identifies sudden demand surges in recent observations compared to baseline history, weighting recent trajectory higher to prevent under-alerting.
   - **Thermal Resource Nuance**: Evaluates cooling against thermal design redlines ($35.0^\circ\text{C}$) where throttling risk begins at $28.0^\circ\text{C}$.

3. **Multi-Tenant Attribution & Strict Isolation (`backend/main.py`)**:
   - Decomposes aggregate utilization into tenant workloads (`tenant-alpha`, `tenant-beta`, `tenant-gamma`).
   - Enforces tenant isolation: Tenant $A$ attempting to query Tenant $B$'s records is rejected with `HTTP 403 Forbidden`.

4. **Read-Only Infrastructure Monitoring & Audit Trail**:
   - System is strictly read-only with respect to infrastructure (no operational shutoff or auto-scaling endpoints).
   - Tracks operator queries, forecast requests, and alert evaluations in an in-memory audit log without exposing credentials.

---

## 4. Evaluation Benchmark & Empirical Results

We evaluate our forecasting engine across 60 labelled synthetic test scenarios in `evaluation/evaluate_models.py`.

### Measured Performance Metrics:

| Metric | Result | Target Benchmark | Meaning |
| :--- | :---: | :---: | :--- |
| **False Positive Rate (Stable Demand)** | **0.0%** | $< 5\%$ | Stable workloads never trigger false capacity alerts |
| **False Positive Rate (Declining Demand)** | **0.0%** | $0\%$ | Decommissioned/declining workloads suppress exhaustion claims |
| **Missed Alert Rate (Overloaded Resources)** | **0.0%** | $0\%$ | 100% of overloaded resources are flagged immediately |
| **Regime-Shift Detection Sensitivity** | **70.0%** | $> 65\%$ | Early detection of sharp demand inflections |
| **Time-to-Capacity MAE (Linear Trajectories)** | **0.0 days** | $< 2.0$ days | Defensible linear estimation against empirical slopes |

*Run the evaluation suite yourself anytime:*
```powershell
python -m evaluation.evaluate_models
```

---

## 5. API Documentation

When the backend is running, complete interactive Swagger documentation is available at:
👉 **[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)**

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Service health status and ingested telemetry point count |
| `GET` | `/api/v1/overview` | Overall capacity status, active alerts count, and earliest crunch |
| `GET` | `/api/v1/resources` | List all 5 monitored resources with utilization and headroom |
| `GET` | `/api/v1/resources/{type}/history` | Historical observations for one resource (supports `?limit=168`) |
| `GET` | `/api/v1/resources/{type}/forecast` | Statistical forecast, days-to-capacity, and 95% confidence bounds |
| `GET` | `/api/v1/alerts` | Active capacity alerts with operator recommendations |
| `GET` | `/api/v1/tenants` | Tenant quota attribution summaries (enforces isolation) |
| `GET` | `/api/v1/tenants/{id}/resources` | Tenant-specific resource usage (blocks cross-tenant access) |
| `GET` | `/api/v1/audit` | Read-only audit trail of system events |
| `GET` | `/` | Serves the interactive HTML/CSS/JS operator dashboard |

---

## 6. Quickstart: Run Locally in 3 Steps

### Prerequisites
- Windows 10/11 (or Linux/macOS)
- Python 3.10+ (tested and verified on Python 3.14.0)
- PowerShell or standard terminal

### Step 1: Virtual Environment Setup
In PowerShell, open the project folder:
```powershell
cd C:\Users\User\Desktop\AO-02-Capacity-Intelligence

# Allow script execution for current terminal session
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

# Activate existing virtual environment
.\.venv\Scripts\Activate.ps1
```

### Step 2: Install Dependencies
```powershell
pip install -r backend/requirements.txt
```

### Step 3: Run the Application
```powershell
python backend/main.py
```
Open your browser and navigate to:
- **Dashboard**: [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- **Swagger API Docs**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

---

## 7. Running the Test Suite

Execute the comprehensive automated test suite with pytest:
```powershell
pytest tests/ -v
```

Expected output:
```text
tests/test_api.py::test_health_check PASSED
tests/test_api.py::test_capacity_overview PASSED
tests/test_api.py::test_tenant_isolation_enforcement PASSED
...
tests/test_forecast.py::test_stable_demand_does_not_claim_capacity_exhaustion PASSED
tests/test_forecast.py::test_regime_change_detection PASSED
tests/test_telemetry.py::test_deterministic_synthetic_generation PASSED
tests/test_evaluation.py::test_evaluation_benchmark_execution PASSED

======================== 33 passed in ~8s ========================
```

---

## 8. Limitations & Honest Implementation Status

1. **Synthetic Telemetry Baseline**:
   - Current telemetry is deterministic synthetic time-series generated with seeded Gaussian noise and diurnal cycles. While mathematically representative of real data centre workloads, it is clearly flagged as `synthetic`.
2. **Linear Trend Modeling**:
   - The forecasting engine employs Ordinary Least Squares linear extrapolation with dynamic confidence expansion. While effective for steady growth, highly erratic multi-modal distributions benefit from future non-linear models (e.g. ARIMA or Prophet) when larger datasets become available.
3. **Read-Only Scope**:
   - The application does not issue automated control commands to infrastructure (such as VM live migrations or server shutdown commands) to prevent unintended operational hazards.
