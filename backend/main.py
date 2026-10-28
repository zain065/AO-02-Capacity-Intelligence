"""
main.py - FastAPI Application for AO-02: Data Centre Capacity Intelligence.

Provides read-only REST API endpoints and telemetry ingestion for:
- Health check (/health)
- Multi-facility management (/api/v1/facilities, /api/v1/facilities/{facility_id}/assets)
- Capacity Overview (/api/v1/overview)
- Resource Status & History (/api/v1/resources, /api/v1/resources/{resource_type}/history)
- Predictive Forecasting (/api/v1/resources/{resource_type}/forecast)
- Alert Engine (/api/v1/alerts)
- Capacity Planning & Lead Time Deficits (/api/v1/capacity-planning)
- Actionable Advisory Recommendations (/api/v1/recommendations)
- Non-Destructive What-If Simulation (/api/v1/simulations/forecast)
- CSV Telemetry Upload & Ingestion (/api/v1/telemetry/import-csv)
- Out-of-Sample Backtesting Evaluation (/api/v1/evaluation/summary)
- Tenant Attribution with Scoped Authorization (/api/v1/tenants, /api/v1/tenants/{tenant_id}/resources)
- Persistent Audit Log (/api/v1/audit)
- Serves static dashboard frontend (/ and static assets)
"""

from __future__ import annotations

import os
import sys

# Ensure repository root is on sys.path for direct python execution
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import (
    FastAPI,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.auth import (
    AuthIdentity,
    authenticate_identity,
    enforce_facility_access,
    enforce_tenant_access,
    VALID_TOKENS,
)
from backend.database import db_manager
from backend.forecast import (
    CapacityStatus,
    ForecastResult,
    compute_capacity_forecast,
    SimulationRequest,
    SimulationResponse,
    simulate_what_if_scenario,
)
from backend.ingestion import process_csv_telemetry, ImportSummary
from backend.planning import (
    PlanningStatus,
    ResourcePlanningItem,
    CapacityPlanningReport,
    ActionableRecommendation,
    evaluate_resource_planning,
    generate_actionable_recommendations,
    DEFAULT_LEAD_TIMES_DAYS,
)
from backend.telemetry import (
    ResourceType,
    RESOURCE_UNITS,
    DEFAULT_CAPACITIES,
    TelemetryRecord,
    generate_synthetic_series,
    generate_tenant_breakdown,
    telemetry_store,
)
from evaluation.backtest import run_comprehensive_facility_backtest

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application Lifespan Context Manager.
    Rehydrates from SQLite persistence layer or seeds default demonstration telemetry.
    """
    telemetry_store.seed_default_datacenter(seed=42, force=False)
    audit_store.log(
        event_type="SYSTEM_STARTUP",
        details="Data Centre Capacity Intelligence started. Persistence active via local SQLite repository.",
        facility_id="DC-NORTH-PRIMARY"
    )
    yield


# Initialize FastAPI application
app = FastAPI(
    title="AO-02: Data Centre Capacity Intelligence API",
    description="Read-only capacity forecasting, thermal analysis, and proactive alert intelligence for data centre infrastructure.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# Enable CORS for local web development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -------------------------------------------------------------------------
# Audit Trail Engine
# -------------------------------------------------------------------------

class AuditEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: str
    resource_type: Optional[str] = None
    facility_id: str = "DC-NORTH-PRIMARY"
    actor: str = "operator"
    details: str


class AuditStore:
    def __init__(self, max_events: int = 500):
        self._events: List[AuditEvent] = []
        self._max_events = max_events

    def log(
        self,
        event_type: str,
        details: str,
        resource_type: Optional[str] = None,
        facility_id: str = "DC-NORTH-PRIMARY",
        actor: str = "operator"
    ) -> None:
        event = AuditEvent(
            event_type=event_type,
            resource_type=resource_type,
            facility_id=facility_id,
            actor=actor,
            details=details
        )
        self._events.append(event)
        if len(self._events) > self._max_events:
            self._events.pop(0)

        # Persist to SQLite repository
        try:
            db_manager.insert_audit_event(
                event_id=event.event_id,
                event_type=event_type,
                details=details,
                resource_type=resource_type,
                facility_id=facility_id,
                actor=actor,
                timestamp=event.timestamp.isoformat()
            )
        except Exception:
            pass

    def get_recent(self, limit: int = 50, facility_id: Optional[str] = None) -> List[AuditEvent]:
        # Query SQLite first for persistent historical logs
        try:
            db_events = db_manager.query_audit_events(limit=limit, facility_id=facility_id)
            if db_events:
                return [
                    AuditEvent(
                        event_id=r["event_id"],
                        timestamp=datetime.fromisoformat(r["timestamp"]),
                        event_type=r["event_type"],
                        resource_type=r.get("resource_type"),
                        facility_id=r.get("facility_id", "DC-NORTH-PRIMARY"),
                        actor=r.get("actor", "operator"),
                        details=r["details"]
                    )
                    for r in db_events
                ]
        except Exception:
            pass
        return list(reversed(self._events[-limit:]))


audit_store = AuditStore()


# -------------------------------------------------------------------------
# API Response Schemas
# -------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str = "healthy"
    version: str = "1.0.0"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    data_points_ingested: int
    database_persisted: bool = True
    facilities_registered: int = 2


class ResourceSummary(BaseModel):
    resource_type: ResourceType
    unit: str
    current_value: float
    capacity: float
    utilization_pct: float
    status: CapacityStatus
    days_to_capacity: Optional[float]
    trend_slope_per_day: float
    data_source: str
    latest_timestamp: datetime
    asset_id: Optional[str] = None


class OverviewResponse(BaseModel):
    facility_id: str = "DC-NORTH-PRIMARY"
    data_source: str = "hybrid (sqlite persistent telemetry & synthetic baseline)"
    data_notice: str = "All telemetry is deterministically validated and persisted. Imported records are tagged explicitly."
    total_resources_monitored: int
    overall_health: CapacityStatus
    critical_alerts_count: int
    warning_alerts_count: int
    earliest_capacity_crunch: Optional[str]
    days_to_earliest_crunch: Optional[float]
    resources: List[ResourceSummary]


class AlertItem(BaseModel):
    alert_id: str
    severity: str  # critical, warning, info
    resource_type: ResourceType
    current_pct: float
    days_to_capacity: Optional[float]
    title: str
    message: str
    suggested_action: str
    timestamp: datetime
    facility_id: str = "DC-NORTH-PRIMARY"
    asset_id: Optional[str] = None


class TenantResourceUsage(BaseModel):
    tenant_id: str
    workload_type: str
    resource_type: ResourceType
    value: float
    quota: float
    quota_utilization_pct: float
    unit: str
    timestamp: datetime


class TenantSummary(BaseModel):
    tenant_id: str
    workload_type: str
    allocated_resources: List[TenantResourceUsage]


# -------------------------------------------------------------------------
# Core Endpoints
# -------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, tags=["System"])
def get_health():
    """Health check endpoint indicating service availability and ingestion count."""
    return HealthResponse(
        status="healthy",
        version="1.0.0",
        data_points_ingested=telemetry_store.count(),
        database_persisted=True,
        facilities_registered=len(telemetry_store.get_facilities())
    )


@app.get("/api/v1/facilities", tags=["Facilities"])
def list_facilities(identity: AuthIdentity = Depends(authenticate_identity)):
    """Lists all registered data centre facilities accessible to the authenticated caller."""
    all_facs = telemetry_store.get_facilities()
    if "*" in identity.allowed_facilities:
        return all_facs
    return [f for f in all_facs if f["facility_id"] in identity.allowed_facilities]


@app.get("/api/v1/facilities/{facility_id}/assets", tags=["Facilities"])
def list_facility_assets(
    facility_id: str,
    resource_type: Optional[ResourceType] = None,
    identity: AuthIdentity = Depends(authenticate_identity)
):
    """
    Lists constrained assets (e.g. Storage Pool A/B, Uplink 1/2, VM Clusters)
    monitored within a specific facility.
    """
    enforce_facility_access(identity, facility_id)
    return telemetry_store.get_assets_by_facility(facility_id, resource_type=resource_type)


@app.get("/api/v1/overview", response_model=OverviewResponse, tags=["Capacity Intelligence"])
def get_capacity_overview(
    facility_id: str = Query(default="DC-NORTH-PRIMARY", description="Target facility identifier"),
    identity: AuthIdentity = Depends(authenticate_identity)
):
    """
    High-level capacity status across all monitored infrastructure resources for a facility.
    Returns current utilization, alerts count, and earliest predicted capacity limit.
    """
    enforce_facility_access(identity, facility_id)

    summaries: List[ResourceSummary] = []
    critical_count = 0
    warning_count = 0
    earliest_crunch_resource: Optional[str] = None
    min_days: Optional[float] = None

    for res in ResourceType:
        records = telemetry_store.get_by_resource(res, facility_id=facility_id, tenant_id="aggregate")
        latest = telemetry_store.get_latest(res, facility_id=facility_id)
        if not records or not latest:
            continue

        forecast = compute_capacity_forecast(records, forecast_days=30)
        if forecast.status == CapacityStatus.CRITICAL or forecast.status == CapacityStatus.EXCEEDED:
            critical_count += 1
        elif forecast.status == CapacityStatus.WARNING:
            warning_count += 1

        if forecast.days_to_capacity is not None and forecast.days_to_capacity > 0:
            if min_days is None or forecast.days_to_capacity < min_days:
                min_days = forecast.days_to_capacity
                earliest_crunch_resource = res.value

        summaries.append(ResourceSummary(
            resource_type=res,
            unit=latest.unit,
            current_value=latest.value,
            capacity=latest.capacity,
            utilization_pct=latest.utilization_pct,
            status=forecast.status,
            days_to_capacity=forecast.days_to_capacity,
            trend_slope_per_day=forecast.trend_slope_per_day,
            data_source=latest.data_source,
            latest_timestamp=latest.timestamp,
            asset_id=latest.asset_id
        ))

    overall_health = (
        CapacityStatus.CRITICAL if critical_count > 0
        else CapacityStatus.WARNING if warning_count > 0
        else CapacityStatus.NORMAL
    )

    audit_store.log(
        event_type="OVERVIEW_QUERY",
        details=f"Overview queried for facility '{facility_id}': overall_health={overall_health.value}, critical={critical_count}, warning={warning_count}",
        facility_id=facility_id
    )

    return OverviewResponse(
        facility_id=facility_id,
        total_resources_monitored=len(summaries),
        overall_health=overall_health,
        critical_alerts_count=critical_count,
        warning_alerts_count=warning_count,
        earliest_capacity_crunch=earliest_crunch_resource,
        days_to_earliest_crunch=min_days,
        resources=summaries
    )


@app.get("/api/v1/resources", response_model=List[ResourceSummary], tags=["Resources"])
def list_resources(
    facility_id: str = Query(default="DC-NORTH-PRIMARY", description="Target facility identifier"),
    identity: AuthIdentity = Depends(authenticate_identity)
):
    """Lists each supported resource with latest utilization and short-term forecast metrics."""
    enforce_facility_access(identity, facility_id)

    results: List[ResourceSummary] = []
    for res in ResourceType:
        records = telemetry_store.get_by_resource(res, facility_id=facility_id, tenant_id="aggregate")
        latest = telemetry_store.get_latest(res, facility_id=facility_id)
        if not records or not latest:
            continue

        forecast = compute_capacity_forecast(records, forecast_days=30)
        results.append(ResourceSummary(
            resource_type=res,
            unit=latest.unit,
            current_value=latest.value,
            capacity=latest.capacity,
            utilization_pct=latest.utilization_pct,
            status=forecast.status,
            days_to_capacity=forecast.days_to_capacity,
            trend_slope_per_day=forecast.trend_slope_per_day,
            data_source=latest.data_source,
            latest_timestamp=latest.timestamp,
            asset_id=latest.asset_id
        ))
    return results


@app.get("/api/v1/resources/{resource_type}/history", tags=["Resources"])
def get_resource_history(
    resource_type: ResourceType,
    facility_id: str = Query(default="DC-NORTH-PRIMARY", description="Facility identifier"),
    asset_id: Optional[str] = Query(default=None, description="Constrained asset identifier (e.g. storage-pool-a)"),
    limit: int = Query(default=168, ge=1, le=2000, description="Observations to return (default: 168h / 7 days)"),
    tenant_id: Optional[str] = Query(default="aggregate", description="Filter by tenant ID or 'aggregate'"),
    identity: AuthIdentity = Depends(authenticate_identity)
):
    """Returns chronological historical observations for a specific resource for charting."""
    enforce_facility_access(identity, facility_id)
    if tenant_id:
        enforce_tenant_access(identity, tenant_id)

    records = telemetry_store.get_by_resource(
        resource_type,
        facility_id=facility_id,
        asset_id=asset_id,
        tenant_id=tenant_id
    )
    if not records:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No telemetry records found for resource '{resource_type.value}' in facility '{facility_id}'"
        )

    sorted_recs = sorted(records, key=lambda r: r.timestamp)[-limit:]
    return [
        {
            "timestamp": r.timestamp.isoformat(),
            "value": r.value,
            "capacity": r.capacity,
            "utilization_pct": r.utilization_pct,
            "unit": r.unit,
            "tenant_id": r.tenant_id,
            "data_source": r.data_source,
            "facility_id": r.facility_id,
            "asset_id": r.asset_id
        }
        for r in sorted_recs
    ]


@app.get("/api/v1/resources/{resource_type}/forecast", response_model=ForecastResult, tags=["Forecasting"])
def get_resource_forecast(
    resource_type: ResourceType,
    facility_id: str = Query(default="DC-NORTH-PRIMARY", description="Facility identifier"),
    asset_id: Optional[str] = Query(default=None, description="Constrained asset identifier"),
    days: int = Query(default=30, ge=1, le=90, description="Forecast projection horizon in days"),
    identity: AuthIdentity = Depends(authenticate_identity)
):
    """
    Computes statistical trend forecast, 95% uncertainty intervals,
    exhaustion uncertainty window, and days until capacity limit.
    """
    enforce_facility_access(identity, facility_id)

    records = telemetry_store.get_by_resource(
        resource_type,
        facility_id=facility_id,
        asset_id=asset_id,
        tenant_id="aggregate"
    )
    if not records:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No telemetry found for resource '{resource_type.value}' in facility '{facility_id}'"
        )

    forecast = compute_capacity_forecast(records, forecast_days=days)
    audit_store.log(
        event_type="FORECAST_CALCULATION",
        resource_type=resource_type.value,
        facility_id=facility_id,
        details=f"Forecast generated: status={forecast.status.value}, days_to_capacity={forecast.days_to_capacity}"
    )
    return forecast


@app.get("/api/v1/alerts", response_model=List[AlertItem], tags=["Alerts"])
def get_active_alerts(
    facility_id: str = Query(default="DC-NORTH-PRIMARY", description="Target facility identifier"),
    identity: AuthIdentity = Depends(authenticate_identity)
):
    """
    Returns active or impending capacity alerts across all resources in a facility.
    Includes operator explanations and suggested read-only actions.
    """
    enforce_facility_access(identity, facility_id)

    alerts: List[AlertItem] = []

    for res in ResourceType:
        records = telemetry_store.get_by_resource(res, facility_id=facility_id, tenant_id="aggregate")
        latest = telemetry_store.get_latest(res, facility_id=facility_id)
        if not records or not latest:
            continue

        forecast = compute_capacity_forecast(records, forecast_days=30)
        if forecast.status in [CapacityStatus.CRITICAL, CapacityStatus.EXCEEDED]:
            alerts.append(AlertItem(
                alert_id=f"alert-{facility_id}-{res.value}-critical",
                severity="critical",
                resource_type=res,
                current_pct=latest.utilization_pct,
                days_to_capacity=forecast.days_to_capacity,
                title=f"Critical {res.value.capitalize()} Headroom Deficit ({facility_id})",
                message=forecast.reason,
                suggested_action=f"Initiate urgent expansion order or workload rebalancing for {res.value}.",
                timestamp=latest.timestamp,
                facility_id=facility_id
            ))
        elif forecast.status == CapacityStatus.WARNING:
            alerts.append(AlertItem(
                alert_id=f"alert-{facility_id}-{res.value}-warning",
                severity="warning",
                resource_type=res,
                current_pct=latest.utilization_pct,
                days_to_capacity=forecast.days_to_capacity,
                title=f"Impending {res.value.capitalize()} Saturation ({facility_id})",
                message=forecast.reason,
                suggested_action=f"Review capacity allocation and procurement schedule for {res.value}.",
                timestamp=latest.timestamp,
                facility_id=facility_id
            ))

    return alerts


@app.get("/api/v1/tenants", response_model=List[TenantSummary], tags=["Tenants"])
def list_tenants(identity: AuthIdentity = Depends(authenticate_identity)):
    """
    Lists tenant resource usage and quota utilization.
    Adheres strictly to tenant isolation (viewers only see their own tenant).
    """
    all_tenants = ["tenant-alpha", "tenant-beta", "tenant-gamma"]
    allowed_tenants = all_tenants if identity.is_admin or identity.role == "operator" else identity.allowed_tenants

    results: List[TenantSummary] = []
    workload_map = {
        "tenant-alpha": "database-analytics",
        "tenant-beta": "web-microservices",
        "tenant-gamma": "ml-batch-training"
    }

    for t_id in allowed_tenants:
        allocations: List[TenantResourceUsage] = []
        for res in ResourceType:
            recs = telemetry_store.get_by_resource(res, facility_id="DC-NORTH-PRIMARY", tenant_id=t_id)
            if recs:
                latest = max(recs, key=lambda r: r.timestamp)
                allocations.append(TenantResourceUsage(
                    tenant_id=t_id,
                    workload_type=workload_map.get(t_id, "general"),
                    resource_type=res,
                    value=latest.value,
                    quota=latest.capacity,
                    quota_utilization_pct=latest.utilization_pct,
                    unit=latest.unit,
                    timestamp=latest.timestamp
                ))

        results.append(TenantSummary(
            tenant_id=t_id,
            workload_type=workload_map.get(t_id, "general"),
            allocated_resources=allocations
        ))

    return results


@app.get("/api/v1/tenants/{tenant_id}/resources", response_model=TenantSummary, tags=["Tenants"])
def get_tenant_resources(
    tenant_id: str,
    identity: AuthIdentity = Depends(authenticate_identity)
):
    """
    Returns specific tenant resource allocation and quota usage.
    Rejects unauthorized access with 403 Forbidden.
    """
    enforce_tenant_access(identity, tenant_id)

    workload_map = {
        "tenant-alpha": "database-analytics",
        "tenant-beta": "web-microservices",
        "tenant-gamma": "ml-batch-training"
    }

    allocations: List[TenantResourceUsage] = []
    for res in ResourceType:
        recs = telemetry_store.get_by_resource(res, facility_id="DC-NORTH-PRIMARY", tenant_id=tenant_id)
        if recs:
            latest = max(recs, key=lambda r: r.timestamp)
            allocations.append(TenantResourceUsage(
                tenant_id=tenant_id,
                workload_type=workload_map.get(tenant_id, "general"),
                resource_type=res,
                value=latest.value,
                quota=latest.capacity,
                quota_utilization_pct=latest.utilization_pct,
                unit=latest.unit,
                timestamp=latest.timestamp
            ))

    return TenantSummary(
        tenant_id=tenant_id,
        workload_type=workload_map.get(tenant_id, "general"),
        allocated_resources=allocations
    )


@app.get("/api/v1/audit", response_model=List[AuditEvent], tags=["Audit Trail"])
def get_audit_trail(
    limit: int = Query(default=50, ge=1, le=200),
    facility_id: Optional[str] = Query(default=None),
    identity: AuthIdentity = Depends(authenticate_identity)
):
    """Returns chronologically ordered audit events recorded by the capacity intelligence engine."""
    if facility_id:
        enforce_facility_access(identity, facility_id)
    return audit_store.get_recent(limit=limit, facility_id=facility_id)


@app.get("/api/v1/capacity-planning", response_model=CapacityPlanningReport, tags=["Capacity Planning"])
def get_capacity_planning(
    facility_id: str = Query(default="DC-NORTH-PRIMARY", description="Target facility identifier"),
    lead_time_override: Optional[float] = Query(default=None, ge=1.0, le=365.0, description="Optional override for procurement lead time in days"),
    identity: AuthIdentity = Depends(authenticate_identity)
):
    """
    Evaluates each resource against hardware expansion and procurement lead times.
    Determines whether procurement action is overdue, imminent, or on track.
    """
    enforce_facility_access(identity, facility_id)

    items: List[ResourcePlanningItem] = []
    overdue_count = 0
    imminent_count = 0

    for res in ResourceType:
        records = telemetry_store.get_by_resource(res, facility_id=facility_id, tenant_id="aggregate")
        if not records:
            continue
        fc = compute_capacity_forecast(records, forecast_days=30)
        custom_lead = lead_time_override if lead_time_override is not None else DEFAULT_LEAD_TIMES_DAYS.get(res, 30.0)
        item = evaluate_resource_planning(fc, lead_time_days=custom_lead)
        if item.planning_status == PlanningStatus.ACTION_OVERDUE or item.planning_status == PlanningStatus.CAPACITY_EXCEEDED:
            overdue_count += 1
        elif item.planning_status == PlanningStatus.ORDER_IMMINENT:
            imminent_count += 1
        items.append(item)

    audit_store.log(
        event_type="CAPACITY_PLANNING_EVALUATION",
        facility_id=facility_id,
        details=f"Evaluated capacity planning across {len(items)} resources in '{facility_id}': {overdue_count} overdue, {imminent_count} imminent."
    )

    return CapacityPlanningReport(
        facility_id=facility_id,
        total_resources=len(items),
        overdue_count=overdue_count,
        imminent_count=imminent_count,
        items=items
    )


@app.get("/api/v1/recommendations", response_model=List[ActionableRecommendation], tags=["Recommendations"])
def get_recommendations(
    facility_id: str = Query(default="DC-NORTH-PRIMARY", description="Target facility identifier"),
    identity: AuthIdentity = Depends(authenticate_identity)
):
    """
    Generates resource-specific, prioritized advisory recommendations based on
    utilization, forecast trajectory, procurement lead times, and regime detection.
    Strictly read-only and advisory (no automatic infrastructure control).
    """
    enforce_facility_access(identity, facility_id)

    forecasts: Dict[ResourceType, ForecastResult] = {}
    plans: Dict[ResourceType, ResourcePlanningItem] = {}

    for res in ResourceType:
        records = telemetry_store.get_by_resource(res, facility_id=facility_id, tenant_id="aggregate")
        if not records:
            continue
        fc = compute_capacity_forecast(records, forecast_days=30)
        forecasts[res] = fc
        plans[res] = evaluate_resource_planning(fc)

    recs = generate_actionable_recommendations(forecasts, plans)
    audit_store.log(
        event_type="RECOMMENDATIONS_GENERATED",
        facility_id=facility_id,
        details=f"Generated {len(recs)} prioritized advisory recommendations for '{facility_id}'."
    )
    return recs


@app.post("/api/v1/simulations/forecast", response_model=SimulationResponse, tags=["What-If Simulation"])
def simulate_forecast(
    request: SimulationRequest,
    facility_id: str = Query(default="DC-NORTH-PRIMARY", description="Facility identifier"),
    identity: AuthIdentity = Depends(authenticate_identity)
):
    """
    Simulates hypothetical what-if scenario (demand changes, workload additions, capacity expansion).
    Recalculates time-to-capacity without modifying the original telemetry, persistent state, or infrastructure.
    """
    enforce_facility_access(identity, facility_id)

    records = telemetry_store.get_by_resource(request.resource_type, facility_id=facility_id, tenant_id="aggregate")
    if not records:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No telemetry found for resource '{request.resource_type.value}' in facility '{facility_id}'"
        )

    sim_result = simulate_what_if_scenario(records, request)
    audit_store.log(
        event_type="SIMULATION_EXECUTED",
        resource_type=request.resource_type.value,
        facility_id=facility_id,
        details=(
            f"What-if simulation executed: growth_multiplier={request.demand_growth_multiplier}x, "
            f"workload_delta={request.workload_delta:+.1f}, capacity_adjustment={request.capacity_adjustment:+.1f}. "
            f"Severity change: {sim_result.severity_change}."
        )
    )
    return sim_result


@app.post("/api/v1/telemetry/import-csv", response_model=ImportSummary, tags=["Telemetry Ingestion"])
async def import_telemetry_csv(
    file: UploadFile = File(...),
    facility_id: Optional[str] = Form(None),
    identity: AuthIdentity = Depends(authenticate_identity)
):
    """
    Imports historical telemetry from a CSV file into SQLite persistent storage.
    Validates required columns, formats, units, and non-negative values.
    Requires 'admin' or 'operator' role.
    """
    if identity.role not in ["admin", "operator"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access forbidden: Role '{identity.role}' is not authorized to import telemetry."
        )

    target_fac = facility_id or "DC-NORTH-PRIMARY"
    enforce_facility_access(identity, target_fac)

    content = await file.read()
    try:
        summary = process_csv_telemetry(content, file.filename or "uploaded.csv", target_facility_id=target_fac)
        # Re-seed memory cache from database so new data reflects immediately
        telemetry_store.seed_default_datacenter(force=False)
        return summary
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.get("/api/v1/evaluation/summary", tags=["Evaluation"])
def get_evaluation_summary(identity: AuthIdentity = Depends(authenticate_identity)):
    """
    Returns time-ordered out-of-sample backtesting metrics (MAE, RMSE,
    95% prediction interval empirical coverage, and comparison against Naive baseline).
    """
    return run_comprehensive_facility_backtest()


# -------------------------------------------------------------------------
# Static Dashboard Mounting
# -------------------------------------------------------------------------

frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
if os.path.isdir(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    print("Starting AO-02 Capacity Intelligence Server at http://127.0.0.1:8000 ...")
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=False)
