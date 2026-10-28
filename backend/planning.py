"""
planning.py - Capacity Planning, Procurement Lead Time Engine, and Actionable Recommendations.

Part of AO-02: Data Centre Capacity Intelligence.
Provides:
- Configurable procurement & expansion lead times per resource type
- Planning status evaluation (ACTION_OVERDUE, ORDER_IMMINENT, ON_TRACK, SUFFICIENT_HEADROOM)
- Resource-specific advisory recommendations (P0_CRITICAL to P3_LOW)
- Strictly read-only; no automated infrastructure modification.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from backend.forecast import CapacityStatus, ForecastResult
from backend.telemetry import ResourceType, RESOURCE_UNITS, TelemetryRecord


class PlanningStatus(str, Enum):
    ACTION_OVERDUE = "action_overdue"          # days_to_capacity <= lead_time
    ORDER_IMMINENT = "order_imminent"          # 0 < (days_to_capacity - lead_time) <= 14 days
    ON_TRACK = "on_track"                      # Ample margin beyond lead time
    SUFFICIENT_HEADROOM = "sufficient_headroom"# Demand is flat or declining
    CAPACITY_EXCEEDED = "capacity_exceeded"    # Currently at or above 100%
    INSUFFICIENT_DATA = "insufficient_data"    # Not enough points for reliable estimate


class RecommendationPriority(str, Enum):
    P0_CRITICAL = "P0_CRITICAL"
    P1_HIGH = "P1_HIGH"
    P2_MEDIUM = "P2_MEDIUM"
    P3_LOW = "P3_LOW"


# Standard data centre expansion and procurement lead times in calendar days
DEFAULT_LEAD_TIMES_DAYS: Dict[ResourceType, float] = {
    ResourceType.POWER: 60.0,    # PDU/feeder expansion, electrical contractor scheduling
    ResourceType.COOLING: 45.0,  # CRAC unit procurement, chiller balancing, ducting
    ResourceType.STORAGE: 30.0,  # SAN expansion shelf, high-density NVMe drive arrays
    ResourceType.NETWORK: 21.0,  # ToR switch, 100G optical transceivers, cabling
    ResourceType.VM: 14.0,       # Server blade delivery, hypervisor cluster onboarding
}


class ResourcePlanningItem(BaseModel):
    """Capacity planning analysis for a single resource incorporating procurement lead time."""
    resource_type: ResourceType
    unit: str
    current_value: float
    capacity: float
    utilization_pct: float
    forecast_status: CapacityStatus
    days_to_capacity: Optional[float]
    procurement_lead_time_days: float
    days_until_order_deadline: Optional[float] = Field(
        default=None,
        description="Days remaining before purchase order must be placed (days_to_capacity - lead_time)."
    )
    overdue_by_days: Optional[float] = Field(
        default=None,
        description="If order deadline has passed, how many days overdue."
    )
    planning_status: PlanningStatus
    planning_headline: str
    explanation: str


class CapacityPlanningReport(BaseModel):
    """Data centre overall capacity planning and procurement readiness report."""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    facility_id: str = "DC-NORTH-PRIMARY"
    total_resources: int
    overdue_count: int
    imminent_count: int
    items: List[ResourcePlanningItem]


class ActionableRecommendation(BaseModel):
    """Resource-specific, advisory operational recommendation."""
    recommendation_id: str
    priority: RecommendationPriority
    resource_type: ResourceType
    title: str
    summary: str
    trigger_reason: str
    action_items: List[str]
    estimated_impact: str
    automated_action: bool = False  # Strictly read-only / advisory


def evaluate_resource_planning(
    forecast: ForecastResult,
    lead_time_days: Optional[float] = None
) -> ResourcePlanningItem:
    """
    Evaluates whether capacity expansion is overdue, imminent, or on track
    based on the estimated time to capacity versus required procurement lead time.
    """
    res_type = forecast.resource_type
    std_lead = lead_time_days if lead_time_days is not None else DEFAULT_LEAD_TIMES_DAYS.get(res_type, 30.0)
    unit = RESOURCE_UNITS[res_type]
    days_to_cap = forecast.days_to_capacity

    if forecast.status == CapacityStatus.EXCEEDED:
        return ResourcePlanningItem(
            resource_type=res_type,
            unit=unit,
            current_value=forecast.current_value,
            capacity=forecast.capacity,
            utilization_pct=forecast.current_utilization_pct,
            forecast_status=forecast.status,
            days_to_capacity=0.0,
            procurement_lead_time_days=std_lead,
            days_until_order_deadline=0.0,
            overdue_by_days=std_lead,
            planning_status=PlanningStatus.CAPACITY_EXCEEDED,
            planning_headline="CAPACITY EXCEEDED — EMERGENCY MEASURES REQUIRED",
            explanation=f"{res_type.value.capitalize()} has already reached or breached rated capacity of {forecast.capacity} {unit}. Immediate workload shedding or redirection required."
        )

    if forecast.status == CapacityStatus.INSUFFICIENT_DATA or days_to_cap is None:
        if forecast.status == CapacityStatus.INSUFFICIENT_DATA:
            status = PlanningStatus.INSUFFICIENT_DATA
            headline = "INSUFFICIENT TELEMETRY FOR PLANNING"
            explanation = "Observations are too sparse or sampling period too short to calculate a reliable procurement deadline."
        else:
            status = PlanningStatus.SUFFICIENT_HEADROOM
            headline = "AMPLE HEADROOM — NO EXPANSION REQUIRED"
            explanation = f"Demand for {res_type.value} is stable or declining ({forecast.trend_slope_per_day:+.2f} {unit}/day). Headroom is comfortable."

        return ResourcePlanningItem(
            resource_type=res_type,
            unit=unit,
            current_value=forecast.current_value,
            capacity=forecast.capacity,
            utilization_pct=forecast.current_utilization_pct,
            forecast_status=forecast.status,
            days_to_capacity=None,
            procurement_lead_time_days=std_lead,
            days_until_order_deadline=None,
            overdue_by_days=None,
            planning_status=status,
            planning_headline=headline,
            explanation=explanation
        )

    # Calculate deadline margin
    margin = days_to_cap - std_lead

    if margin <= 0:
        overdue_days = round(abs(margin), 1)
        return ResourcePlanningItem(
            resource_type=res_type,
            unit=unit,
            current_value=forecast.current_value,
            capacity=forecast.capacity,
            utilization_pct=forecast.current_utilization_pct,
            forecast_status=forecast.status,
            days_to_capacity=days_to_cap,
            procurement_lead_time_days=std_lead,
            days_until_order_deadline=0.0,
            overdue_by_days=overdue_days,
            planning_status=PlanningStatus.ACTION_OVERDUE,
            planning_headline=f"ACTION OVERDUE BY ~{overdue_days} DAYS",
            explanation=(
                f"Procurement lead time is {std_lead:.0f} days, but capacity limit will be reached in {days_to_cap:.1f} days. "
                f"Purchase order should have been placed {overdue_days} days ago. Expedited delivery or interim workload mitigation required."
            )
        )
    elif margin <= 14.0:
        return ResourcePlanningItem(
            resource_type=res_type,
            unit=unit,
            current_value=forecast.current_value,
            capacity=forecast.capacity,
            utilization_pct=forecast.current_utilization_pct,
            forecast_status=forecast.status,
            days_to_capacity=days_to_cap,
            procurement_lead_time_days=std_lead,
            days_until_order_deadline=round(margin, 1),
            overdue_by_days=None,
            planning_status=PlanningStatus.ORDER_IMMINENT,
            planning_headline=f"ORDER WINDOW CLOSING IN ~{margin:.0f} DAYS",
            explanation=(
                f"Procurement window opens now. To receive expansion hardware ({std_lead:.0f}-day lead time) "
                f"before exhaustion in {days_to_cap:.1f} days, purchase orders must be approved within {margin:.1f} days."
            )
        )
    else:
        return ResourcePlanningItem(
            resource_type=res_type,
            unit=unit,
            current_value=forecast.current_value,
            capacity=forecast.capacity,
            utilization_pct=forecast.current_utilization_pct,
            forecast_status=forecast.status,
            days_to_capacity=days_to_cap,
            procurement_lead_time_days=std_lead,
            days_until_order_deadline=round(margin, 1),
            overdue_by_days=None,
            planning_status=PlanningStatus.ON_TRACK,
            planning_headline="EXPANSION ON TRACK",
            explanation=(
                f"Comfortable runway: {days_to_cap:.1f} days to capacity exceeds {std_lead:.0f}-day procurement lead time. "
                f"Purchase requisition deadline is in {margin:.1f} days."
            )
        )


def generate_actionable_recommendations(
    forecasts: Dict[ResourceType, ForecastResult],
    planning_items: Dict[ResourceType, ResourcePlanningItem]
) -> List[ActionableRecommendation]:
    """
    Generates tailored, actionable, and prioritized operational recommendations
    based on current utilization, forecast trajectory, lead times, and regime detection.
    Strictly advisory (automated_action is always False).
    """
    recommendations: List[ActionableRecommendation] = []

    for res, fc in forecasts.items():
        plan = planning_items.get(res)
        unit = RESOURCE_UNITS[res]

        # -------------------------------------------------------------
        # STORAGE RECOMMENDATIONS
        # -------------------------------------------------------------
        if res == ResourceType.STORAGE:
            if plan and plan.planning_status == PlanningStatus.ACTION_OVERDUE:
                recommendations.append(ActionableRecommendation(
                    recommendation_id="rec-storage-overdue",
                    priority=RecommendationPriority.P0_CRITICAL,
                    resource_type=res,
                    title="Initiate Emergency SAN Expansion Purchase Order",
                    summary=f"Storage exhaustion is projected in ~{fc.days_to_capacity:.0f} days against a 30-day hardware lead time (overdue by {plan.overdue_by_days:.0f} days).",
                    trigger_reason=f"Current utilization at {fc.current_utilization_pct:.1f}% (+{fc.trend_slope_per_day:.1f} TB/day). Order deadline is past.",
                    action_items=[
                        "Issue urgent purchase order for additional high-density SAN expansion shelf.",
                        "Request vendor expedited freight / air shipment to shorten delivery window.",
                        "Audit non-essential development/staging snapshots and purge cold temp tables immediately.",
                        "Enable aggressive deduplication and compression on secondary volume groups."
                    ],
                    estimated_impact="Recovers 5-10% immediate headroom; avoids database read-only freeze.",
                    automated_action=False
                ))
            elif fc.current_utilization_pct >= 80.0:
                recommendations.append(ActionableRecommendation(
                    recommendation_id="rec-storage-headroom",
                    priority=RecommendationPriority.P2_MEDIUM,
                    resource_type=res,
                    title="Storage Quota & Archival Policy Review",
                    summary="Storage utilization is elevated; implement tiering to object storage.",
                    trigger_reason=f"Utilization is at {fc.current_utilization_pct:.1f}%.",
                    action_items=[
                        "Identify datasets untouched for > 90 days and schedule migration to S3/cold tier.",
                        "Review tenant quotas for analytics pipelines."
                    ],
                    estimated_impact="Slows daily growth rate by up to 25%.",
                    automated_action=False
                ))

        # -------------------------------------------------------------
        # COOLING / THERMAL RECOMMENDATIONS
        # -------------------------------------------------------------
        elif res == ResourceType.COOLING:
            if fc.regime_change_detected or fc.current_value >= 32.0 or (plan and plan.planning_status == PlanningStatus.ACTION_OVERDUE):
                recommendations.append(ActionableRecommendation(
                    recommendation_id="rec-cooling-thermal-urgent",
                    priority=RecommendationPriority.P0_CRITICAL,
                    resource_type=res,
                    title="CRAC Unit Containment & Thermal Balancing Intervention",
                    summary=f"Thermal readings are approaching or at critical thresholds ({fc.current_value:.1f} °C on a 35 °C ceiling).",
                    trigger_reason=fc.regime_change_detail or f"Thermal measurement at {fc.current_value:.1f} °C.",
                    action_items=[
                        "Inspect hot/cold aisle containment curtains for breaches or open blanking panels.",
                        "Verify chiller valve bypass status and CRAC unit fan speed inverter feedback.",
                        "Temporarily migrate compute-intensive batch jobs away from high-density racks in Pod A.",
                        "Schedule HVAC mechanical contractor inspection within 24 hours."
                    ],
                    estimated_impact="Reduces rack temperature by 2-4 °C; mitigates CPU thermal throttling.",
                    automated_action=False
                ))
            elif fc.current_value >= 28.0:
                recommendations.append(ActionableRecommendation(
                    recommendation_id="rec-cooling-preventive",
                    priority=RecommendationPriority.P2_MEDIUM,
                    resource_type=res,
                    title="Aisle Airflow & Temperature Tuning",
                    summary="Thermal readings in warning zone; optimize air delivery.",
                    trigger_reason=f"Ambient temperature at {fc.current_value:.1f} °C.",
                    action_items=[
                        "Adjust perforated floor tile placement near high-density server blades.",
                        "Ensure all unoccupied rack slots have blanking panels installed."
                    ],
                    estimated_impact="Improves cooling efficiency by ~10%.",
                    automated_action=False
                ))

        # -------------------------------------------------------------
        # VM INVENTORY RECOMMENDATIONS
        # -------------------------------------------------------------
        elif res == ResourceType.VM:
            if plan and plan.planning_status in [PlanningStatus.ACTION_OVERDUE, PlanningStatus.ORDER_IMMINENT]:
                recommendations.append(ActionableRecommendation(
                    recommendation_id="rec-vm-cluster-expansion",
                    priority=RecommendationPriority.P1_HIGH,
                    resource_type=res,
                    title="Procure Hypervisor Compute Blades & Review Zombie VMs",
                    summary=f"VM capacity exhaustion anticipated in {fc.days_to_capacity:.0f} days (+{fc.trend_slope_per_day:.1f} VMs/day).",
                    trigger_reason=f"Current allocation at {fc.current_utilization_pct:.1f}% ({fc.current_value:.0f} / {fc.capacity:.0f} VMs).",
                    action_items=[
                        "Submit procurement request for 4x dual-socket server blades (14-day lead time).",
                        "Run automated idle-VM audit script to identify VMs with < 2% CPU usage over past 14 days.",
                        "Notify project owners for decommission approval of completed batch workloads."
                    ],
                    estimated_impact="Recovers 50-80 VM slots; secures expansion ahead of deadline.",
                    automated_action=False
                ))

        # -------------------------------------------------------------
        # POWER RECOMMENDATIONS
        # -------------------------------------------------------------
        elif res == ResourceType.POWER:
            if fc.current_utilization_pct >= 80.0:
                recommendations.append(ActionableRecommendation(
                    recommendation_id="rec-power-phase-balance",
                    priority=RecommendationPriority.P1_HIGH,
                    resource_type=res,
                    title="PDU Phase Balancing & High-Density Power Audit",
                    summary=f"Power consumption at {fc.current_utilization_pct:.1f}% of transformer zone rating.",
                    trigger_reason=f"Power load is {fc.current_value:.1f} kW / {fc.capacity:.1f} kW.",
                    action_items=[
                        "Perform 3-phase amperage balancing across Floor PDUs A and B.",
                        "Review rack power caps on dense GPU nodes.",
                        "Confirm UPS battery health and emergency generator auto-transfer switches."
                    ],
                    estimated_impact="Eliminates single-phase overload risk.",
                    automated_action=False
                ))
            else:
                recommendations.append(ActionableRecommendation(
                    recommendation_id="rec-power-routine",
                    priority=RecommendationPriority.P3_LOW,
                    resource_type=res,
                    title="Routine Power Headroom Verification",
                    summary="Power load operating within safe parameters.",
                    trigger_reason=f"Safe utilization ({fc.current_utilization_pct:.1f}%).",
                    action_items=[
                        "Maintain bi-weekly PDU inspection schedule.",
                        "No capacity expansion required at this time."
                    ],
                    estimated_impact="Confirms steady-state operation.",
                    automated_action=False
                ))

        # -------------------------------------------------------------
        # NETWORK RECOMMENDATIONS
        # -------------------------------------------------------------
        elif res == ResourceType.NETWORK:
            if fc.current_utilization_pct >= 75.0:
                recommendations.append(ActionableRecommendation(
                    recommendation_id="rec-network-uplink-expand",
                    priority=RecommendationPriority.P1_HIGH,
                    resource_type=res,
                    title="Core Switch 100G LAG Uplink Expansion",
                    summary=f"Network uplink load is {fc.current_utilization_pct:.1f}% ({fc.current_value:.1f} Gbps).",
                    trigger_reason="Approaching network congestion threshold.",
                    action_items=[
                        "Order 2x 100G QSFP28 optical transceivers for Link Aggregation Group (LAG) bundling.",
                        "Review peak-hour backup schedules and apply QoS rate-limiting."
                    ],
                    estimated_impact="Doubles core transit bandwidth to 200 Gbps.",
                    automated_action=False
                ))
            else:
                recommendations.append(ActionableRecommendation(
                    recommendation_id="rec-network-ok",
                    priority=RecommendationPriority.P3_LOW,
                    resource_type=res,
                    title="Network Bandwidth Headroom Verified",
                    summary="Network traffic normal with ample transit buffer.",
                    trigger_reason=f"Uplink at {fc.current_utilization_pct:.1f}%.",
                    action_items=[
                        "Continue standard packet drop and buffer monitoring.",
                        "No immediate network hardware order needed."
                    ],
                    estimated_impact="Maintains sub-millisecond core latency.",
                    automated_action=False
                ))

    # Sort recommendations by priority: P0_CRITICAL first, then P1_HIGH, P2_MEDIUM, P3_LOW
    priority_order = {
        RecommendationPriority.P0_CRITICAL: 0,
        RecommendationPriority.P1_HIGH: 1,
        RecommendationPriority.P2_MEDIUM: 2,
        RecommendationPriority.P3_LOW: 3
    }
    recommendations.sort(key=lambda r: priority_order[r.priority])
    return recommendations
