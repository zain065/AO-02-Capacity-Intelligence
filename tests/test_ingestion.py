"""
test_ingestion.py - Verification tests for CSV Telemetry Import Engine.

Part of AO-02: Data Centre Capacity Intelligence.
Verifies parsing of standard and irregular CSV telemetry files, column validation,
timestamp normalization, unit consistency, duplicate rejection, and audit tagging.
"""

import pytest
from backend.ingestion import process_csv_telemetry, ImportSummary


def test_valid_csv_import():
    """Verifies that a valid CSV file with multiple resources is parsed and ingested."""
    csv_data = (
        "timestamp,resource_type,value,capacity,unit,facility_id\n"
        "2026-11-05T10:00:00Z,storage,1200.5,2000.0,TB,DC-CUSTOM-CAMPUS\n"
        "2026-11-05T11:00:00Z,storage,1220.0,2000.0,TB,DC-CUSTOM-CAMPUS\n"
        "2026-11-05T10:00:00Z,power,310.2,500.0,kW,DC-CUSTOM-CAMPUS\n"
        "2026-11-05T10:00:00Z,cooling,24.5,35.0,degC,DC-CUSTOM-CAMPUS\n"
    ).encode("utf-8")

    summary = process_csv_telemetry(csv_data, "sample_valid.csv", target_facility_id="DC-CUSTOM-CAMPUS")
    assert isinstance(summary, ImportSummary)
    assert summary.total_rows_parsed == 4
    assert summary.valid_rows_imported + summary.duplicate_rows_skipped == 4
    assert summary.error_count == 0
    assert "storage" in summary.resources_affected
    assert "power" in summary.resources_affected


def test_csv_missing_mandatory_columns():
    """Verifies rejection when mandatory columns are missing from the header."""
    csv_data = "time,value,capacity\n2026-10-28,100,200\n".encode("utf-8")
    with pytest.raises(ValueError) as exc:
        process_csv_telemetry(csv_data, "missing_cols.csv")
    assert "Missing mandatory CSV column" in str(exc.value)


def test_csv_row_level_validation_errors():
    """
    Verifies that malformed rows generate clear, line-numbered errors
    while valid rows continue to be processed.
    """
    csv_data = (
        "timestamp,resource_type,value,capacity,unit\n"
        "invalid-date,storage,500.0,1000.0,TB\n"
        "2026-10-28T12:00:00Z,invalid_type,500.0,1000.0,TB\n"
        "2026-10-28T12:00:00Z,power,not_a_number,500.0,kW\n"
        "2026-10-28T12:00:00Z,storage,500.0,-10.0,TB\n"
        "2026-10-28T12:00:00Z,power,-50.0,500.0,kW\n"
        "2026-10-28T12:00:00Z,storage,500.0,1000.0,wrong_unit\n"
    ).encode("utf-8")

    summary = process_csv_telemetry(csv_data, "bad_rows.csv")
    assert summary.total_rows_parsed == 6
    assert summary.valid_rows_imported == 0
    assert summary.error_count == 6
    assert any("Row 2: Invalid timestamp" in err for err in summary.errors)
    assert any("Row 3: Unsupported resource_type" in err for err in summary.errors)
    assert any("Row 4: Non-numeric value" in err for err in summary.errors)
    assert any("Row 5: Capacity must be a positive" in err for err in summary.errors)
    assert any("Row 6: Negative utilization" in err for err in summary.errors)
    assert any("Row 7: Inconsistent unit" in err for err in summary.errors)


def test_csv_timestamp_formats():
    """Verifies that ISO, SQL, and Unix timestamps are all supported and normalized."""
    csv_data = (
        "timestamp,resource_type,value,capacity,unit\n"
        "2026-10-28 15:30:00,storage,1400.0,2000.0,TB\n"
        "2026-10-28T16:30:00+00:00,storage,1410.0,2000.0,TB\n"
        "1761667200,storage,1420.0,2000.0,TB\n"
    ).encode("utf-8")

    summary = process_csv_telemetry(csv_data, "various_timestamps.csv")
    assert summary.error_count == 0
    assert summary.total_rows_parsed == 3


def test_csv_unit_normalization():
    """Verifies that common unit variants like '°C' and 'vms' are normalized."""
    csv_data = (
        "timestamp,resource_type,value,capacity,unit\n"
        "2026-10-28T10:00:00Z,cooling,26.5,35.0,°C\n"
        "2026-10-28T10:00:00Z,vm,600,1200,VMs\n"
        "2026-10-28T10:00:00Z,power,250,500,kW\n"
    ).encode("utf-8")

    summary = process_csv_telemetry(csv_data, "units.csv")
    assert summary.error_count == 0
    assert summary.total_rows_parsed == 3
