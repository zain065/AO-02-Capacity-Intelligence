"""
telemetry.py - Telemetry Ingestion, Validation, and Deterministic Synthetic Data Generation.

Part of AO-02: Data Centre Capacity Intelligence.
Handles ingestion, validation, tenant attribution metadata, and reproducible
synthetic time-series generation for 5 core data centre resources:
- Power (kW)
- Cooling / Thermal (degC)
- Storage (TB)
- Network (Gbps)
- VM Inventory (vms)
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, field_validator

from backend.database import db_manager


class ResourceType(str, Enum):
    POWER = "power"
    COOLING = "cooling"
    STORAGE = "storage"
    NETWORK = "network"
    VM = "vm"


# Canonical units mapped to resource types
RESOURCE_UNITS: Dict[ResourceType, str] = {
    ResourceType.POWER: "kW",
    ResourceType.COOLING: "degC",
    ResourceType.STORAGE: "TB",
    ResourceType.NETWORK: "Gbps",
    ResourceType.VM: "vms",
}

# Standard capacity baselines for synthetic data centre racks/clusters
DEFAULT_CAPACITIES: Dict[ResourceType, float] = {
    ResourceType.POWER: 500.0,      # 500 kW capacity per power zone
    ResourceType.COOLING: 35.0,     # 35 degC redline thermal threshold
    ResourceType.STORAGE: 2000.0,   # 2000 TB (2 PB) SAN array
    ResourceType.NETWORK: 100.0,    # 100 Gbps core switch uplink
    ResourceType.VM: 1200.0,        # 1200 maximum VM host capacity
}


class TelemetryRecord(BaseModel):
    """
    Schema for a single resource measurement.
    Explicitly labels whether the observation is 'synthetic' or 'imported'.
    """
    timestamp: datetime = Field(..., description="UTC timestamp of the observation")
    resource_type: ResourceType = Field(..., description="Monitored resource category")
    value: float = Field(..., description="Current measured value/utilization")
    capacity: float = Field(..., description="Total rated capacity or threshold limit")
    unit: str = Field(..., description="Measurement unit (e.g., kW, degC, TB, Gbps, vms)")
    data_source: str = Field(
        default="synthetic",
        description="Source of data: 'synthetic' or 'imported'. Synthetic is NEVER presented as real."
    )
    tenant_id: Optional[str] = Field(
        default=None,
        description="Tenant or workload identifier for attribution (if available)"
    )
    workload_type: Optional[str] = Field(
        default=None,
        description="Workload type (e.g. database, analytics, web-tier, batch)"
    )
    facility_id: str = Field(
        default="DC-NORTH-PRIMARY",
        description="Facility identifier (e.g. DC-NORTH-PRIMARY, DC-SOUTH-SECONDARY)"
    )
    asset_id: Optional[str] = Field(
        default=None,
        description="Specific constrained asset or pool (e.g., storage-pool-a, uplink-1, vm-cluster-compute)"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Facility metadata (facility_id, room, rack_id, etc.)"
    )

    @field_validator("value")
    @classmethod
    def validate_value_not_nan(cls, v: float) -> float:
        if math.isnan(v) or math.isinf(v):
            raise ValueError("Value must be a finite numerical measurement (not NaN or Inf)")
        return round(float(v), 3)

    @field_validator("capacity")
    @classmethod
    def validate_capacity_positive(cls, v: float) -> float:
        if math.isnan(v) or math.isinf(v) or v <= 0:
            raise ValueError("Capacity must be a positive number greater than 0")
        return round(float(v), 3)

    @field_validator("unit")
    @classmethod
    def validate_unit_consistency(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("Measurement unit cannot be empty")
        return clean

    @property
    def utilization_pct(self) -> float:
        """Percentage of capacity utilized (can exceed 100% in overload/thermal incident)."""
        if self.capacity <= 0:
            return 0.0
        return round((self.value / self.capacity) * 100.0, 2)


class ValidationError(ValueError):
    """Raised when incoming telemetry violates domain integrity rules."""
    pass


def validate_raw_record(data: Dict[str, Any]) -> TelemetryRecord:
    """
    Validates a raw dictionary observation against integrity rules:
    - Missing required fields
    - Timestamp validity and ISO formatting
    - Inconsistent unit for resource type
    - Negative utilization where prohibited
    - Invalid capacities
    """
    required_fields = ["timestamp", "resource_type", "value", "capacity", "unit"]
    for field in required_fields:
        if field not in data or data[field] is None:
            raise ValidationError(f"Missing required field: '{field}'")

    # Resource type validation
    raw_type = str(data["resource_type"]).lower()
    try:
        res_type = ResourceType(raw_type)
    except ValueError:
        valid_types = [r.value for r in ResourceType]
        raise ValidationError(f"Invalid resource_type '{raw_type}'. Expected one of: {valid_types}")

    # Unit validation
    expected_unit = RESOURCE_UNITS[res_type]
    actual_unit = str(data["unit"]).strip()
    if actual_unit.lower() != expected_unit.lower():
        raise ValidationError(
            f"Inconsistent unit for {res_type.value}: received '{actual_unit}', expected '{expected_unit}'"
        )

    # Utilization bounds
    val = float(data["value"])
    # Cooling can be low but not below -20 degC in data centre context; other resources cannot be negative
    if res_type != ResourceType.COOLING and val < 0:
        raise ValidationError(f"Utilization for {res_type.value} cannot be negative: received {val}")
    elif res_type == ResourceType.COOLING and val < -20.0:
        raise ValidationError(f"Thermal reading physically invalid: received {val} degC")

    # Timestamp conversion
    raw_ts = data["timestamp"]
    if isinstance(raw_ts, str):
        try:
            ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        except ValueError as err:
            raise ValidationError(f"Invalid ISO timestamp format '{raw_ts}': {err}")
    elif isinstance(raw_ts, datetime):
        ts = raw_ts
    else:
        raise ValidationError(f"Timestamp must be ISO string or datetime object, got {type(raw_ts)}")

    # Ensure timezone awareness (default UTC)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    # Capacity check
    cap = float(data["capacity"])
    if cap <= 0:
        raise ValidationError(f"Capacity limit must be strictly positive: received {cap}")

    return TelemetryRecord(
        timestamp=ts,
        resource_type=res_type,
        value=val,
        capacity=cap,
        unit=expected_unit,
        data_source=str(data.get("data_source", "synthetic")),
        tenant_id=data.get("tenant_id"),
        workload_type=data.get("workload_type"),
        metadata=data.get("metadata", {})
    )


# -------------------------------------------------------------------------
# Deterministic Synthetic Telemetry Generator
# -------------------------------------------------------------------------

SAMPLE_TENANTS = [
    {"tenant_id": "tenant-alpha", "workload": "analytics_pipeline", "weight": 0.40},
    {"tenant_id": "tenant-beta", "workload": "core_transact_db", "weight": 0.35},
    {"tenant_id": "tenant-gamma", "workload": "web_api_cluster", "weight": 0.20},
    {"tenant_id": "system_overhead", "workload": "infra_management", "weight": 0.05},
]


def generate_synthetic_series(
    resource_type: ResourceType,
    scenario: str = "increasing",
    days: int = 30,
    interval_hours: int = 1,
    seed: int = 42,
    base_time: Optional[datetime] = None,
    with_tenants: bool = True,
    facility_id: str = "DC-NORTH-PRIMARY",
    asset_id: Optional[str] = None
) -> List[TelemetryRecord]:
    """
    Generates a deterministic time-series for a given resource and scenario.

    Supported Scenarios:
    - 'increasing': Gradual demand growth towards capacity limit (capacity crunch warning/critical)
    - 'stable': Normal diurnal fluctuations with no long-term trend (safe headroom)
    - 'decreasing': Demand decline after workload deprovisioning
    - 'change_point': Stable initial period followed by a sudden structural inflection / regime shift
    - 'exceeded': Telemetry where capacity threshold is already reached or breached
    - 'sparse': Telemetry with irregular timestamps or missing periods to test insufficiency
    """
    rng = np.random.default_rng(seed)

    if base_time is None:
        base_time = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)

    total_steps = int((days * 24) / interval_hours)
    capacity = DEFAULT_CAPACITIES[resource_type]
    unit = RESOURCE_UNITS[resource_type]

    records: List[TelemetryRecord] = []

    # Baseline fractions of capacity depending on resource
    base_fraction = {
        ResourceType.POWER: 0.60,      # 60% power load
        ResourceType.COOLING: 0.65,    # 22.75 degC on 35 degC limit
        ResourceType.STORAGE: 0.55,    # 55% disk full
        ResourceType.NETWORK: 0.45,    # 45% bandwidth load
        ResourceType.VM: 0.50,         # 50% host VM slots used
    }[resource_type]

    for step in range(total_steps):
        # Sparse scenario skips ~40% of observations pseudo-randomly
        if scenario == "sparse" and rng.random() < 0.40:
            continue

        current_time = base_time + timedelta(hours=step * interval_hours)
        progress = step / max(1, total_steps - 1)  # 0.0 to 1.0

        # Daily diurnal cycle (24-hour periodicity)
        diurnal_cycle = 0.06 * math.sin(2 * math.pi * (step * interval_hours % 24) / 24.0)
        # Noise component
        noise = rng.normal(0.0, 0.015)

        if scenario == "increasing":
            # Growth from baseline towards ~95-102% of capacity
            growth = progress * 0.38
            ratio = base_fraction + growth + diurnal_cycle + noise

        elif scenario == "stable":
            # Oscillates around baseline with zero net trend
            ratio = base_fraction + diurnal_cycle + noise

        elif scenario == "decreasing":
            # Declining trend from baseline down towards ~25%
            drop = progress * 0.30
            ratio = max(0.15, base_fraction - drop + diurnal_cycle + noise)

        elif scenario == "change_point":
            # First half stable, second half undergoes sudden steep acceleration
            if progress < 0.50:
                ratio = base_fraction + diurnal_cycle + noise
            else:
                acceleration = (progress - 0.50) * 0.70  # Rapid surge
                ratio = base_fraction + acceleration + diurnal_cycle + noise

        elif scenario == "exceeded":
            # Starts high and exceeds rated capacity threshold early
            ratio = 0.92 + (progress * 0.15) + (noise * 0.5)

        else:
            # Default fallback to stable
            ratio = base_fraction + diurnal_cycle + noise

        # Calculate final physical value
        value = max(0.0, ratio * capacity)

        # Build primary aggregate observation
        record = TelemetryRecord(
            timestamp=current_time,
            resource_type=resource_type,
            value=round(value, 3),
            capacity=capacity,
            unit=unit,
            data_source="synthetic",
            tenant_id="aggregate",
            workload_type="cluster_total",
            facility_id=facility_id,
            asset_id=asset_id,
            metadata={
                "scenario": scenario,
                "step": step,
                "facility": facility_id,
                "asset_id": asset_id,
                "rack_zone": "pod-a",
            }
        )
        records.append(record)

    return records


def generate_tenant_breakdown(
    total_record: TelemetryRecord,
    seed: Optional[int] = None
) -> List[TelemetryRecord]:
    """
    Decomposes an aggregate telemetry observation into tenant-specific records
    using realistic workload allocations.
    """
    rng = np.random.default_rng(seed if seed is not None else 100)
    tenant_records: List[TelemetryRecord] = []

    # Add small noise to baseline tenant weights
    raw_weights = [t["weight"] + rng.normal(0, 0.01) for t in SAMPLE_TENANTS]
    total_w = sum(raw_weights)
    norm_weights = [w / total_w for w in raw_weights]

    for tenant, w in zip(SAMPLE_TENANTS, norm_weights):
        tenant_val = round(total_record.value * w, 3)
        tenant_cap = round(total_record.capacity * tenant["weight"], 3)  # allocated quota
        t_rec = TelemetryRecord(
            timestamp=total_record.timestamp,
            resource_type=total_record.resource_type,
            value=tenant_val,
            capacity=tenant_cap,
            unit=total_record.unit,
            data_source=total_record.data_source,
            tenant_id=tenant["tenant_id"],
            workload_type=tenant["workload"],
            facility_id=total_record.facility_id,
            asset_id=total_record.asset_id,
            metadata={
                **total_record.metadata,
                "quota_pct": round(tenant["weight"] * 100, 1),
            }
        )
        tenant_records.append(t_rec)

    return tenant_records


# -------------------------------------------------------------------------
# Telemetry Store / Repository
# -------------------------------------------------------------------------

class TelemetryStore:
    """
    Unified telemetry store backed by SQLite persistence with an in-memory
    active cache for high-speed linear regressions and what-if simulations.
    Survives application restarts without data loss.
    """
    def __init__(self, use_db: bool = True):
        self._records: List[TelemetryRecord] = []
        self._use_db = use_db

    def clear(self, facility_id: Optional[str] = None) -> None:
        if facility_id:
            self._records = [r for r in self._records if r.facility_id != facility_id]
        else:
            self._records.clear()

        if self._use_db:
            db_manager.clear_telemetry(facility_id=facility_id)

    def add_record(self, record: TelemetryRecord) -> None:
        self._records.append(record)
        if self._use_db:
            db_manager.insert_telemetry_batch([{
                "facility_id": record.facility_id,
                "asset_id": record.asset_id,
                "resource_type": record.resource_type.value,
                "timestamp": record.timestamp.isoformat(),
                "value": record.value,
                "capacity": record.capacity,
                "unit": record.unit,
                "data_source": record.data_source,
                "tenant_id": record.tenant_id or "aggregate",
                "workload_type": record.workload_type,
                "metadata": record.metadata
            }])

    def add_records(self, records: List[TelemetryRecord]) -> None:
        self._records.extend(records)
        if self._use_db:
            batch = [{
                "facility_id": r.facility_id,
                "asset_id": r.asset_id,
                "resource_type": r.resource_type.value,
                "timestamp": r.timestamp.isoformat(),
                "value": r.value,
                "capacity": r.capacity,
                "unit": r.unit,
                "data_source": r.data_source,
                "tenant_id": r.tenant_id or "aggregate",
                "workload_type": r.workload_type,
                "metadata": r.metadata
            } for r in records]
            db_manager.insert_telemetry_batch(batch)

    def count(self, facility_id: Optional[str] = None) -> int:
        if facility_id:
            return sum(1 for r in self._records if r.facility_id == facility_id)
        return len(self._records)

    def get_by_resource(
        self,
        resource_type: ResourceType,
        facility_id: Optional[str] = "DC-NORTH-PRIMARY",
        asset_id: Optional[str] = None,
        tenant_id: Optional[str] = "aggregate"
    ) -> List[TelemetryRecord]:
        """Filters observations by resource, facility, asset, and tenant identifier."""
        return [
            r for r in self._records
            if r.resource_type == resource_type
            and (facility_id is None or r.facility_id == facility_id)
            and (asset_id is None or r.asset_id == asset_id)
            and (tenant_id is None or r.tenant_id == tenant_id)
        ]

    def get_assets_by_facility(
        self,
        facility_id: str = "DC-NORTH-PRIMARY",
        resource_type: Optional[ResourceType] = None
    ) -> List[Dict[str, Any]]:
        """Returns distinct asset identifiers and summaries within a facility."""
        asset_map: Dict[str, Dict[str, Any]] = {}
        for r in self._records:
            if r.facility_id == facility_id and r.asset_id:
                if resource_type is None or r.resource_type == resource_type:
                    if r.asset_id not in asset_map:
                        asset_map[r.asset_id] = {
                            "asset_id": r.asset_id,
                            "facility_id": facility_id,
                            "resource_type": r.resource_type.value,
                            "capacity": r.capacity,
                            "unit": r.unit,
                            "latest_value": r.value,
                            "utilization_pct": r.utilization_pct
                        }
                    else:
                        asset_map[r.asset_id]["latest_value"] = r.value
                        asset_map[r.asset_id]["utilization_pct"] = r.utilization_pct

        return sorted(list(asset_map.values()), key=lambda a: a["asset_id"])

    def get_facilities(self) -> List[Dict[str, Any]]:
        """Returns distinct facilities currently known to the store and database."""
        if self._use_db:
            db_facs = db_manager.get_facilities()
            if db_facs:
                return db_facs

        # Fallback to in-memory facilities
        fac_ids = sorted(list({r.facility_id for r in self._records if r.facility_id}))
        if not fac_ids:
            fac_ids = ["DC-NORTH-PRIMARY"]
        return [{"facility_id": f, "name": f"{f} Data Centre"} for f in fac_ids]

    def to_dataframe(
        self,
        resource_type: Optional[ResourceType] = None,
        facility_id: Optional[str] = "DC-NORTH-PRIMARY",
        tenant_id: Optional[str] = "aggregate"
    ) -> pd.DataFrame:
        """Converts telemetry records into a sorted Pandas DataFrame."""
        filtered = self._records
        if resource_type is not None:
            filtered = [r for r in filtered if r.resource_type == resource_type]
        if facility_id is not None:
            filtered = [r for r in filtered if r.facility_id == facility_id]
        if tenant_id is not None:
            filtered = [r for r in filtered if r.tenant_id == tenant_id]

        if not filtered:
            return pd.DataFrame(columns=[
                "timestamp", "resource_type", "value", "capacity",
                "unit", "utilization_pct", "data_source", "tenant_id", "workload_type",
                "facility_id", "asset_id"
            ])

        data = [{
            "timestamp": r.timestamp,
            "resource_type": r.resource_type.value,
            "value": r.value,
            "capacity": r.capacity,
            "unit": r.unit,
            "utilization_pct": r.utilization_pct,
            "data_source": r.data_source,
            "tenant_id": r.tenant_id,
            "workload_type": r.workload_type,
            "facility_id": r.facility_id,
            "asset_id": r.asset_id,
        } for r in filtered]

        df = pd.DataFrame(data)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values(by="timestamp").reset_index(drop=True)
        return df

    def get_latest(
        self,
        resource_type: ResourceType,
        facility_id: str = "DC-NORTH-PRIMARY",
        asset_id: Optional[str] = None
    ) -> Optional[TelemetryRecord]:
        """Returns the most recent aggregate observation for a resource in a facility."""
        res_records = self.get_by_resource(resource_type, facility_id=facility_id, asset_id=asset_id, tenant_id="aggregate")
        if not res_records:
            return None
        return max(res_records, key=lambda r: r.timestamp)

    def seed_default_datacenter(self, seed: int = 42, force: bool = False) -> None:
        """
        Populates default synthetic observations covering multiple facilities and constrained assets.
        If SQLite already contains persisted data, rehydrates from the database unless force=True.
        """
        # 1. Check if SQLite already has persisted records (Survives application restarts!)
        if self._use_db and db_manager.count_telemetry() > 0 and not force:
            self._records.clear()
            rows = db_manager.query_telemetry(resource_type=None, facility_id=None, tenant_id=None, limit=None)
            for row in rows:
                meta = row.get("metadata") or {}
                try:
                    ts = datetime.fromisoformat(row["timestamp"])
                except Exception:
                    ts = datetime.now(timezone.utc)

                rec = TelemetryRecord(
                    timestamp=ts,
                    resource_type=ResourceType(row["resource_type"]),
                    value=row["value"],
                    capacity=row["capacity"],
                    unit=row["unit"],
                    data_source=row.get("data_source", "synthetic"),
                    tenant_id=row.get("tenant_id"),
                    workload_type=row.get("workload_type"),
                    facility_id=row.get("facility_id", "DC-NORTH-PRIMARY"),
                    asset_id=row.get("asset_id"),
                    metadata=meta
                )
                self._records.append(rec)
            return

        # 2. Fresh initialization: Register facilities
        if self._use_db:
            db_manager.upsert_facility("DC-NORTH-PRIMARY", "North Campus Data Centre (Primary)", "Ashburn, VA", "Tier-IV")
            db_manager.upsert_facility("DC-SOUTH-SECONDARY", "South Campus Disaster Recovery", "Dallas, TX", "Tier-III")

            # Register constrained assets for Phase 3
            db_manager.upsert_asset("DC-NORTH-PRIMARY", "storage-pool-a", "storage", "NVMe Tier-1 Primary Pool", 1200.0, "TB")
            db_manager.upsert_asset("DC-NORTH-PRIMARY", "storage-pool-b", "storage", "HDD Tier-2 Capacity Pool", 800.0, "TB")
            db_manager.upsert_asset("DC-NORTH-PRIMARY", "uplink-1", "network", "Transit Provider Alpha", 60.0, "Gbps")
            db_manager.upsert_asset("DC-NORTH-PRIMARY", "uplink-2", "network", "Transit Provider Beta", 40.0, "Gbps")
            db_manager.upsert_asset("DC-NORTH-PRIMARY", "vm-cluster-compute", "vm", "Compute Optimized Cluster", 800.0, "vms")
            db_manager.upsert_asset("DC-NORTH-PRIMARY", "vm-cluster-general", "vm", "General Purpose Cluster", 400.0, "vms")

        self.clear()

        # Seed Facility 1: DC-NORTH-PRIMARY (Demonstration Scenarios)
        fac1 = "DC-NORTH-PRIMARY"
        self.add_records(generate_synthetic_series(ResourceType.STORAGE, "increasing", days=30, seed=seed, facility_id=fac1))
        self.add_records(generate_synthetic_series(ResourceType.POWER, "stable", days=30, seed=seed + 1, facility_id=fac1))
        self.add_records(generate_synthetic_series(ResourceType.COOLING, "change_point", days=30, seed=seed + 2, facility_id=fac1))
        self.add_records(generate_synthetic_series(ResourceType.NETWORK, "stable", days=30, seed=seed + 3, facility_id=fac1))
        self.add_records(generate_synthetic_series(ResourceType.VM, "increasing", days=30, seed=seed + 4, facility_id=fac1))

        # Seed Phase 3 Constrained Assets in Facility 1
        # Storage Pool A (approaching 94% full - critical asset)
        self.add_records(generate_synthetic_series(
            ResourceType.STORAGE, "increasing", days=30, seed=seed + 10,
            facility_id=fac1, asset_id="storage-pool-a"
        ))
        # Storage Pool B (steady at 45% full - ample headroom)
        self.add_records(generate_synthetic_series(
            ResourceType.STORAGE, "stable", days=30, seed=seed + 11,
            facility_id=fac1, asset_id="storage-pool-b"
        ))
        # Network Uplink 1 & 2
        self.add_records(generate_synthetic_series(
            ResourceType.NETWORK, "stable", days=30, seed=seed + 12,
            facility_id=fac1, asset_id="uplink-1"
        ))
        self.add_records(generate_synthetic_series(
            ResourceType.NETWORK, "stable", days=30, seed=seed + 13,
            facility_id=fac1, asset_id="uplink-2"
        ))

        # Seed Facility 2: DC-SOUTH-SECONDARY (Stable Baseline Facility)
        fac2 = "DC-SOUTH-SECONDARY"
        for res in ResourceType:
            self.add_records(generate_synthetic_series(res, "stable", days=30, seed=seed + 20, facility_id=fac2))

        # Seed tenant breakdown records for tenant attribution and historical queries
        tenant_recs: List[TelemetryRecord] = []
        for agg_rec in list(self._records):
            if agg_rec.asset_id is None:  # Aggregate tenant breakdown
                tenant_recs.extend(generate_tenant_breakdown(agg_rec, seed=seed))
        self.add_records(tenant_recs)


# Global singleton instance for in-app state
telemetry_store = TelemetryStore()

