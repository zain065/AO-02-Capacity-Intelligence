"""
ingestion.py - Robust CSV Telemetry Parser, Validator, and Batch Importer.

Part of AO-02: Data Centre Capacity Intelligence.

Enforces strict domain validation:
- Required column verification (timestamp, resource_type, value, capacity, unit)
- ISO-8601 and Unix epoch timestamp parsing
- Unit consistency and canonical mapping
- Rejection of NaN, Inf, and negative utilization
- Deduplication against existing database observations
- Line-numbered error reporting for operational auditability
- Explicit labeling as 'imported' telemetry (never conflated with synthetic demo data)
"""

from __future__ import annotations

import csv
import io
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from backend.telemetry import (
    ResourceType,
    RESOURCE_UNITS,
    DEFAULT_CAPACITIES,
    TelemetryRecord,
    validate_raw_record,
    ValidationError,
)
from backend.database import db_manager

# Maximum CSV upload size: 10 MB (prevents denial of service on local servers)
MAX_CSV_BYTES = 10 * 1024 * 1024


class ImportSummary(BaseModel):
    """Structured report returned after CSV telemetry upload."""
    filename: str
    total_rows_parsed: int
    valid_rows_imported: int
    duplicate_rows_skipped: int
    error_count: int
    errors: List[str] = Field(default_factory=list, description="Line-numbered validation error explanations")
    facility_id: str
    resources_affected: List[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def parse_timestamp_flexible(raw_val: str) -> datetime:
    """Parses ISO-8601, common SQL date strings, or Unix timestamps."""
    clean = raw_val.strip()
    if not clean:
        raise ValueError("Timestamp string is empty")

    # Try numeric Unix epoch
    try:
        epoch = float(clean)
        # Handle milliseconds vs seconds
        if epoch > 1e11:
            epoch /= 1000.0
        return datetime.fromtimestamp(epoch, tz=timezone.utc)
    except ValueError:
        pass

    # Try standard ISO-8601 with Z or offset
    normalized = clean.replace("Z", "+00:00")
    for fmt in [
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ]:
        try:
            dt = datetime.strptime(normalized, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue

    # Fallback to datetime.fromisoformat
    try:
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        raise ValueError(f"Unrecognized date/time format: '{raw_val}'")


def normalize_unit(raw_unit: str, res_type: ResourceType) -> str:
    """Normalizes variations like 'degC'/'°C' or 'vms'/'VMs' to canonical units."""
    u = raw_unit.strip()
    if res_type == ResourceType.COOLING:
        if u in ["°C", "C", "degC", "celsius"]:
            return "degC"
    elif res_type == ResourceType.POWER:
        if u.lower() in ["kw", "kilowatt", "kilowatts"]:
            return "kW"
    elif res_type == ResourceType.STORAGE:
        if u.upper() in ["TB", "TERABYTE", "TERABYTES"]:
            return "TB"
    elif res_type == ResourceType.NETWORK:
        if u.lower() in ["gbps", "gigabit", "gigabits"]:
            return "Gbps"
    elif res_type == ResourceType.VM:
        if u.lower() in ["vm", "vms", "slots", "hosts"]:
            return "vms"
    return u


def process_csv_telemetry(
    file_content: bytes,
    filename: str,
    target_facility_id: Optional[str] = None
) -> ImportSummary:
    """
    Validates, normalizes, and ingests CSV telemetry rows into SQLite database.
    """
    if len(file_content) > MAX_CSV_BYTES:
        raise ValueError(f"File size exceeds maximum permitted limit of {MAX_CSV_BYTES // (1024*1024)} MB.")

    text_content = file_content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text_content))

    if not reader.fieldnames:
        raise ValueError("CSV file is empty or missing header row.")

    # Validate header fields (case-insensitive mapping)
    header_map: Dict[str, str] = {col.strip().lower(): col for col in reader.fieldnames if col}
    required_cols = ["timestamp", "resource_type", "value", "capacity", "unit"]
    for req in required_cols:
        if req not in header_map:
            raise ValueError(f"Missing mandatory CSV column: '{req}'. Required columns: {required_cols}")

    total_rows = 0
    valid_records: List[Dict[str, Any]] = []
    errors: List[str] = []
    duplicate_count = 0
    seen_in_batch: Set[Tuple[str, str, str, str, str]] = set()
    resources_affected: Set[str] = set()

    default_facility = target_facility_id or "DC-NORTH-PRIMARY"

    # Ensure target facility exists in SQLite
    if not db_manager.has_facility(default_facility):
        db_manager.upsert_facility(default_facility, f"{default_facility} Data Centre", "Custom Imported Facility")

    for row_idx, raw_row in enumerate(reader, start=2):  # line 2 is first data row
        total_rows += 1

        try:
            # 1. Parse Resource Type
            raw_res = raw_row[header_map["resource_type"]].strip().lower()
            try:
                res_type = ResourceType(raw_res)
            except ValueError:
                errors.append(f"Row {row_idx}: Unsupported resource_type '{raw_res}'. Must be power, cooling, storage, network, or vm.")
                continue

            # 2. Parse Timestamp
            raw_ts = raw_row[header_map["timestamp"]].strip()
            try:
                ts = parse_timestamp_flexible(raw_ts)
            except Exception as e:
                errors.append(f"Row {row_idx}: Invalid timestamp '{raw_ts}': {str(e)}")
                continue

            # 3. Parse Numerical Value & Capacity
            raw_val_str = raw_row[header_map["value"]].strip()
            raw_cap_str = raw_row[header_map["capacity"]].strip()
            try:
                val = float(raw_val_str)
                cap = float(raw_cap_str)
            except ValueError:
                errors.append(f"Row {row_idx}: Non-numeric value ('{raw_val_str}') or capacity ('{raw_cap_str}').")
                continue

            if math.isnan(val) or math.isinf(val):
                errors.append(f"Row {row_idx}: Value must be finite (received NaN or Inf).")
                continue
            if math.isnan(cap) or math.isinf(cap) or cap <= 0:
                errors.append(f"Row {row_idx}: Capacity must be a positive finite number > 0 (received {cap}).")
                continue

            # Thermal values can be positive celsius; others cannot be negative
            if res_type != ResourceType.COOLING and val < 0:
                errors.append(f"Row {row_idx}: Negative utilization ({val} {res_type.value}) is invalid.")
                continue

            # 4. Parse & Normalize Unit
            raw_unit = raw_row[header_map["unit"]].strip()
            norm_unit = normalize_unit(raw_unit, res_type)
            canonical_unit = RESOURCE_UNITS[res_type]
            if norm_unit != canonical_unit:
                errors.append(f"Row {row_idx}: Inconsistent unit '{raw_unit}' for {res_type.value}. Expected '{canonical_unit}'.")
                continue

            # 5. Facility and Asset metadata
            row_fac = default_facility
            if "facility_id" in header_map and raw_row[header_map["facility_id"]].strip():
                row_fac = raw_row[header_map["facility_id"]].strip()

            row_asset = None
            if "asset_id" in header_map and raw_row[header_map["asset_id"]].strip():
                row_asset = raw_row[header_map["asset_id"]].strip()

            row_tenant = "aggregate"
            if "tenant_id" in header_map and raw_row[header_map["tenant_id"]].strip():
                row_tenant = raw_row[header_map["tenant_id"]].strip()

            row_workload = None
            if "workload_type" in header_map and raw_row[header_map["workload_type"]].strip():
                row_workload = raw_row[header_map["workload_type"]].strip()

            # 6. Check duplicate within current upload batch
            dedup_key = (row_fac, row_asset or "", res_type.value, row_tenant, ts.isoformat())
            if dedup_key in seen_in_batch:
                duplicate_count += 1
                continue
            seen_in_batch.add(dedup_key)

            # Record is valid
            valid_records.append({
                "facility_id": row_fac,
                "asset_id": row_asset,
                "resource_type": res_type.value,
                "timestamp": ts.isoformat(),
                "value": val,
                "capacity": cap,
                "unit": canonical_unit,
                "data_source": "imported",
                "tenant_id": row_tenant,
                "workload_type": row_workload,
                "metadata": {
                    "source_file": filename,
                    "imported_at": datetime.now(timezone.utc).isoformat()
                }
            })
            resources_affected.add(res_type.value)

        except Exception as ex:
            errors.append(f"Row {row_idx}: Unexpected parsing failure: {str(ex)}")

    # 7. Insert valid batch into SQLite persistence layer
    inserted_count = 0
    if valid_records:
        inserted_count = db_manager.insert_telemetry_batch(valid_records)
        duplicate_count += (len(valid_records) - inserted_count)

        # Log audit trail event
        import uuid
        db_manager.insert_audit_event(
            event_id=f"evt-import-{uuid.uuid4().hex[:12]}",
            event_type="CSV_TELEMETRY_IMPORT",
            details=f"Imported {inserted_count} telemetry records from '{filename}' into facility '{default_facility}'.",
            resource_type=",".join(sorted(resources_affected)),
            facility_id=default_facility,
            actor="operator"
        )

    return ImportSummary(
        filename=filename,
        total_rows_parsed=total_rows,
        valid_rows_imported=inserted_count,
        duplicate_rows_skipped=duplicate_count,
        error_count=len(errors),
        errors=errors[:50],  # Return up to 50 detailed errors for display
        facility_id=default_facility,
        resources_affected=sorted(list(resources_affected))
    )
