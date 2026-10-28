# AO-02: Data Centre Capacity Intelligence

[![FastAPI](https://img.shields.io/badge/FastAPI-1.0.0-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.14-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Tests: 67 Passed](https://img.shields.io/badge/pytest-67%20passed-brightgreen?logo=pytest&logoColor=white)](https://docs.pytest.org)
[![Coverage: 97.7%](https://img.shields.io/badge/coverage-97.7%25-brightgreen)](https://docs.pytest.org)

An intelligent, explainable predictive capacity forecasting and thermal monitoring platform for modern data centre infrastructure. 

Developed as a hackathon project to give data centre operators early, actionable warning before critical infrastructure limits are breached—preventing service outages, hardware throttling, and emergency procurement panics.

---

## 1. Problem Statement & Motivation

Modern hyperscale and enterprise data centres operate within hard physical, thermal, and virtual constraints:
- **Electrical Power**: Substation and transformer zone limits (kW).
- **Cooling & Thermal**: CRAC/chiller capacity and server rack redline thermal limits (°C).
- **Storage**: SAN/NAS array capacity exhaustion (TB).
- **Network Bandwidth**: Top-of-Rack (ToR) and core uplink saturation (Gbps).
- **Compute / Virtual Machines**: Hypervisor host density and VM slot allocation limits.

If demand grows faster than anticipated or hardware lead times are overlooked, data centres face service downtime, thermal throttling, or exorbitant emergency procurement costs. **AO-02 Capacity Intelligence** ingests continuous multi-facility infrastructure telemetry over 12-month historical observation baselines, detects demand trends and regime shifts, models 95% uncertainty intervals, and translates technical projections into proactive hardware procurement schedules.

---

## 2. Technology Stack (Free & Locally Runnable)

- **Backend**: Python 3.10–3.14, FastAPI (read-only ASGI REST API with CORS & static mounting)
- **Data Modeling & Validation**: Pydantic v2
- **Persistence & Telemetry**: SQLite with WAL mode (`backend/database.py`), zero cloud dependencies
- **Numerical & Statistical Analysis**: NumPy & Pandas
- **Forecasting Engine**: Ordinary Least Squares (OLS) trend extrapolation, 95% dynamic prediction intervals ($\pm t \times \text{SE}$), regime-shift surge detection
- **Capacity Planning Engine**: Lead-time procurement calendar (`ACTION_OVERDUE`, `ORDER_IMMINENT`, `ON_TRACK`) and automated P0–P3 actionable advisory recommendations
- **Scenario Simulator**: Non-destructive what-if simulation with demand multipliers, delta shifts, and planned capacity expansions
- **Frontend**: Plain HTML5, Vanilla CSS3 (responsive dark-mode glassmorphic theme), Vanilla JavaScript (No React, Angular, or node_modules needed)
- **Visualization**: Chart.js (CDN canvas time-series trajectory with 95% confidence bands)
- **Automated Verification**: pytest (**67 automated unit and integration tests** passing)

---

## 3. Project Structure

```
AO-02-Capacity-Intelligence/
├── backend/
│   ├── main.py            # FastAPI REST server & static UI mount
│   ├── telemetry.py       # Telemetry store, schema validation, multi-facility seeding
│   ├── forecast.py        # OLS forecasting engine, regime shifts, uncertainty bounds, what-if simulator
│   ├── planning.py        # Capacity procurement planning, lead times, actionable recommendations
│   ├── database.py        # SQLite persistence layer (facilities, telemetry, audit logs)
│   ├── ingestion.py       # CSV telemetry ingestion & validation engine
│   ├── auth.py            # RBAC authentication and tenant/facility isolation
│   └── requirements.txt   # Pinned Python dependencies
├── frontend/
│   ├── index.html         # Responsive operator dashboard
│   ├── style.css          # Design system, dark mode, glassmorphic layout
│   ├── enterprise-theme.css # High-contrast enterprise styling
│   └── script.js          # REST API integration, Chart.js visualization, UI interactivity
├── evaluation/
│   ├── evaluate_models.py # Quantitative benchmark measuring MAE, false alarms, and missed alerts
│   ├── backtest.py        # Empirical out-of-sample backtesting against naive baseline
│   └── evaluation_report.json # Reproducible evaluation report
├── tests/
│   ├── test_api.py            # REST API endpoints & tenant isolation
│   ├── test_auth.py           # Role-based access control tests
│   ├── test_backtest.py       # Out-of-sample backtesting validation
│   ├── test_database.py       # SQLite persistence and query tests
│   ├── test_evaluation.py     # Benchmark suite verification
│   ├── test_forecast.py       # Trend extrapolation, uncertainty & guardrails
│   ├── test_ingestion.py      # CSV validation & ingestion tests
│   ├── test_lead_times.py     # Procurement lead-time status logic
│   ├── test_multi_facility.py # Multi-facility & asset hierarchy tests
│   ├── test_simulation.py     # What-If scenario simulation tests
│   └── test_telemetry.py      # Telemetry models, validation, and units
├── data/
│   ├── capacity_intelligence.db # Pre-populated 12-month operational database
│   ├── download_sources.py      # Source trace download & conversion utility
│   └── README.md                # Data provenance documentation
├── Screenshots/
│   ├── homepage.png       # Overview dashboard, facility selector & headroom table
│   ├── feature_1.png      # Predictive capacity trajectory & 95% uncertainty window
│   ├── feature_2.png      # Lead times procurement schedule & actionable advisories
│   └── feature_3.png      # What-If scenario simulator & empirical backtest table
├── README.md              # Complete setup and reproduction guide
└── .gitignore             # Git ignore rules for venv, cache, and metadata
```

---

## 4. Empirical Evaluation & Verification

Our platform includes empirical out-of-sample backtesting across **3,600 historical observations**:

| Resource Category | Historical Window | Coverage Rate | AO-02 MAE | Naive Baseline MAE | Improvement |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Storage (SAN/NAS)** | 12 Months | **97.7%** | **4.21 TB** | 5.30 TB | **+20.5%** |
| **Electrical Power** | 12 Months | **97.7%** | **41.05 kW** | 78.40 kW | **+47.6%** |
| **Cooling & Thermal** | 12 Months | **97.7%** | **0.82 °C** | 1.15 °C | **+28.7%** |
| **Network Uplink** | 12 Months | **97.7%** | **5.40 Gbps** | 7.90 Gbps | **+31.6%** |
| **Compute / VMs** | 12 Months | **97.7%** | **18.20 VMs** | 25.10 VMs | **+27.5%** |

### Safety Guardrails:
1. **Stable/Declining Demand Guardrail**: If slope $\le 0$, the engine strictly suppresses false alarms, returning `status: "stable"` or `"declining"` with `days_to_capacity: null`.
2. **Insufficient Data Guardrail**: If fewer than 12 points are available, the model rejects speculation and flags `status: "insufficient_data"`.
3. **95% Dynamic Uncertainty Bounds**: Computes statistical standard error and $t$-distribution intervals, surfacing honest $\pm \text{days}$ confidence windows.

---

## 5. Quickstart: Run Locally in 3 Steps

### Prerequisites
- Windows 10/11, macOS, or Linux
- Python 3.10+ (tested on Python 3.14)
- Modern web browser (Chrome, Edge, Firefox, Safari)

### Step 1: Clone and Navigate
```powershell
git clone https://github.com/zain065/AO-02-Capacity-Intelligence.git
cd AO-02-Capacity-Intelligence
```

### Step 2: Set Up Virtual Environment & Dependencies
```powershell
python -m venv .venv

# On Windows (PowerShell):
.\.venv\Scripts\Activate.ps1

# On Linux / macOS:
source .venv/bin/activate

pip install -r backend/requirements.txt
```

### Step 3: Launch the Platform
```powershell
python backend/main.py
```
Open your browser and navigate to:
- **Interactive Operator Dashboard**: [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- **Swagger REST API Documentation**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **ReDoc API Documentation**: [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)

---

## 6. Running Automated Tests

Run the full automated test suite (67 tests across all subsystems):
```powershell
pytest tests/ -v
```

Expected output:
```text
======================== 67 passed in ~85s ========================
```

To run individual subsystem test suites:
```powershell
pytest tests/test_api.py -v         # 14 API tests
pytest tests/test_forecast.py -v    # 7 Forecasting & guardrail tests
pytest tests/test_lead_times.py -v  # 6 Procurement planning tests
pytest tests/test_simulation.py -v  # 5 What-If scenario tests
```

---

## 7. API Endpoints Overview

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Service health status and ingested telemetry point count |
| `GET` | `/api/v1/overview` | Capacity summary, critical resources, and 12-month metrics |
| `GET` | `/api/v1/facilities` | List monitored data centre facilities and metadata |
| `GET` | `/api/v1/facilities/{id}/assets` | List physical assets and zones for a facility |
| `GET` | `/api/v1/resources` | List all 5 monitored resources with utilization & headroom |
| `GET` | `/api/v1/resources/{type}/history` | Historical telemetry time-series |
| `GET` | `/api/v1/resources/{type}/forecast`| OLS forecast, days-to-capacity & 95% uncertainty |
| `GET` | `/api/v1/capacity-planning` | Procurement lead times (`ACTION_OVERDUE`, `ORDER_IMMINENT`) |
| `GET` | `/api/v1/recommendations` | Prioritized P0–P3 actionable engineering advisories |
| `POST`| `/api/v1/simulations/forecast` | Non-destructive what-if scenario forecasting |
| `GET` | `/api/v1/evaluation/summary` | Out-of-sample backtesting metrics vs naive baseline |
| `POST`| `/api/v1/telemetry/import-csv` | Ingest external CSV telemetry files with validation |
| `GET` | `/api/v1/tenants` | Multi-tenant attribution and resource shares |
| `GET` | `/api/v1/audit` | Read-only audit log of system events |
| `GET` | `/` | Web operator dashboard |

---

## 8. License

This project is licensed under the MIT License.

